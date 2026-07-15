from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.db.models import AppSession, AppUser, new_id
from app.schemas.app_auth import (
    AppAuthStatusRead,
    AppLoginRequest,
    AppPasswordChangeRequest,
    AppSetupRequest,
)


@dataclass(frozen=True, slots=True)
class CreatedAppSession:
    token: str
    status: AppAuthStatusRead


@dataclass(frozen=True, slots=True)
class AppPrincipal:
    user_id: str
    username: str
    session_id: str
    csrf_token: str
    expires_at: datetime


class AppAuthService:
    """Single-administrator authentication with revocable database sessions."""

    cookie_name = "bili_insight_session"
    _maximum_attempts = 5
    _attempt_window = timedelta(minutes=5)

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self._passwords = PasswordHasher(
            time_cost=3,
            memory_cost=65_536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
        )
        self._mutation_lock = asyncio.Lock()
        self._attempts: dict[str, deque[datetime]] = defaultdict(deque)

    async def initialize(self) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            await session.execute(delete(AppSession).where(AppSession.expires_at <= now))
            await session.commit()

    async def status(self, token: str | None) -> AppAuthStatusRead:
        principal = await self.authenticate(token, required=False)
        if principal is not None:
            return self._status(principal, initialized=True)
        async with self.session_factory() as session:
            initialized = bool(await session.scalar(select(func.count(AppUser.id))))
        return AppAuthStatusRead(initialized=initialized, authenticated=False)

    async def setup(self, request: AppSetupRequest) -> CreatedAppSession:
        password_hash = await asyncio.to_thread(self._passwords.hash, request.password)
        async with self._mutation_lock, self.session_factory() as session:
            if await session.scalar(select(func.count(AppUser.id))):
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    "管理员账号已经初始化",
                    action="返回登录页使用现有管理员账号登录",
                    status_code=status.HTTP_409_CONFLICT,
                )
            user = AppUser(username=request.username, password_hash=password_hash)
            session.add(user)
            await session.flush()
            created = self._new_session(user)
            session.add(created[0])
            await session.commit()
        return CreatedAppSession(
            token=created[1],
            status=self._status(created[2], initialized=True),
        )

    async def login(self, request: AppLoginRequest, *, client_key: str) -> CreatedAppSession:
        self._check_rate_limit(client_key)
        async with self.session_factory() as session:
            user = await session.scalar(select(AppUser).where(AppUser.username == request.username))
            password_hash = user.password_hash if user is not None else self._dummy_hash()
            valid = await asyncio.to_thread(self._verify_password, password_hash, request.password)
            if user is None or not valid:
                self._record_failure(client_key)
                raise AppError(
                    ErrorCode.APP_AUTH_REQUIRED,
                    "用户名或密码不正确",
                    action="检查管理员用户名和密码后重试",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )
            if self._passwords.check_needs_rehash(user.password_hash):
                user.password_hash = await asyncio.to_thread(self._passwords.hash, request.password)
            record, token, principal = self._new_session(user)
            session.add(record)
            await session.commit()
        self._attempts.pop(client_key, None)
        return CreatedAppSession(token=token, status=self._status(principal, initialized=True))

    async def authenticate(
        self,
        token: str | None,
        *,
        required: bool = True,
        csrf_token: str | None = None,
        require_csrf: bool = False,
    ) -> AppPrincipal | None:
        if token:
            token_hash = self._token_hash(token)
            now = datetime.now(UTC)
            async with self.session_factory() as session:
                record = await session.scalar(
                    select(AppSession)
                    .where(AppSession.token_hash == token_hash)
                    .options(selectinload(AppSession.user))
                )
                if record is not None and self._as_utc(record.expires_at) > now:
                    if require_csrf and not (
                        csrf_token and hmac.compare_digest(record.csrf_token, csrf_token)
                    ):
                        raise AppError(
                            ErrorCode.PERMISSION_DENIED,
                            "请求安全校验失败",
                            action="刷新页面后重新提交操作",
                            status_code=status.HTTP_403_FORBIDDEN,
                        )
                    record.last_seen_at = now
                    await session.commit()
                    return AppPrincipal(
                        user_id=record.user.id,
                        username=record.user.username,
                        session_id=record.id,
                        csrf_token=record.csrf_token,
                        expires_at=self._as_utc(record.expires_at),
                    )
                if record is not None:
                    await session.delete(record)
                    await session.commit()
        if not required:
            return None
        raise AppError(
            ErrorCode.APP_AUTH_REQUIRED,
            "请先登录 Bili Insight",
            action="返回登录页完成管理员登录",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    async def logout(self, token: str | None) -> None:
        if not token:
            return
        async with self.session_factory() as session:
            await session.execute(
                delete(AppSession).where(AppSession.token_hash == self._token_hash(token))
            )
            await session.commit()

    async def change_password(
        self,
        principal: AppPrincipal,
        request: AppPasswordChangeRequest,
    ) -> CreatedAppSession:
        async with self._mutation_lock, self.session_factory() as session:
            user = await session.get(AppUser, principal.user_id)
            if user is None or not await asyncio.to_thread(
                self._verify_password, user.password_hash, request.current_password
            ):
                raise AppError(
                    ErrorCode.APP_AUTH_REQUIRED,
                    "当前密码不正确",
                    action="重新输入当前管理员密码",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )
            user.password_hash = await asyncio.to_thread(self._passwords.hash, request.new_password)
            await session.execute(delete(AppSession).where(AppSession.user_id == user.id))
            record, token, replacement = self._new_session(user)
            session.add(record)
            await session.commit()
        return CreatedAppSession(
            token=token,
            status=self._status(replacement, initialized=True),
        )

    def _new_session(self, user: AppUser) -> tuple[AppSession, str, AppPrincipal]:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self.settings.app_session_ttl_seconds)
        token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        record = AppSession(
            id=new_id(),
            user_id=user.id,
            token_hash=self._token_hash(token),
            csrf_token=csrf_token,
            expires_at=expires_at,
            created_at=now,
            last_seen_at=now,
        )
        return (
            record,
            token,
            AppPrincipal(
                user_id=user.id,
                username=user.username,
                session_id=record.id,
                csrf_token=csrf_token,
                expires_at=expires_at,
            ),
        )

    @staticmethod
    def _status(principal: AppPrincipal, *, initialized: bool) -> AppAuthStatusRead:
        return AppAuthStatusRead(
            initialized=initialized,
            authenticated=True,
            username=principal.username,
            csrf_token=principal.csrf_token,
            session_expires_at=principal.expires_at,
        )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _dummy_hash(self) -> str:
        # Stable per-process work factor prevents a username timing oracle.
        value = getattr(self, "_cached_dummy_hash", None)
        if isinstance(value, str):
            return value
        value = self._passwords.hash(secrets.token_urlsafe(32))
        self._cached_dummy_hash = value
        return value

    def _verify_password(self, password_hash: str, password: str) -> bool:
        try:
            return bool(self._passwords.verify(password_hash, password))
        except (VerifyMismatchError, InvalidHashError):
            return False

    def _check_rate_limit(self, client_key: str) -> None:
        now = datetime.now(UTC)
        attempts = self._attempts[client_key]
        threshold = now - self._attempt_window
        while attempts and attempts[0] <= threshold:
            attempts.popleft()
        if len(attempts) >= self._maximum_attempts:
            raise AppError(
                ErrorCode.PERMISSION_DENIED,
                "登录尝试过于频繁",
                action="等待 5 分钟后重试",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )

    def _record_failure(self, client_key: str) -> None:
        self._attempts[client_key].append(datetime.now(UTC))

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
