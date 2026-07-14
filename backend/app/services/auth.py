from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.cookiejar import CookieJar

from fastapi import status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.db.models import AuthPersistence, AuthProfile, AuthStatus
from app.providers.models import AuthValidation, VideoProvider
from app.schemas.auth import AuthStatusResponse
from app.security.cookies import CookieCipher, CookieFileParser, clone_cookie_jar

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AuthSession:
    status: AuthStatus = AuthStatus.LOGGED_OUT
    jar: CookieJar | None = None
    masked_account_name: str | None = None
    membership_type: str = "none"
    cookie_expires_at: datetime | None = None
    last_validated_at: datetime | None = None
    persistence: AuthPersistence | None = None
    stored_but_unavailable: bool = False


class AuthService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        provider: VideoProvider,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.provider = provider
        self.parser = CookieFileParser(
            max_bytes=settings.cookie_upload_max_bytes,
            max_items=settings.cookie_max_items,
        )
        self._state = AuthSession()
        self._lock = asyncio.Lock()
        self._clear_callbacks: list[Callable[[], Awaitable[None]]] = []

    async def initialize(self) -> None:
        async with self._lock, self.session_factory() as session:
            profile = await session.scalar(
                select(AuthProfile).where(AuthProfile.provider == "bilibili")
            )
            if profile is None:
                return
            cipher = self._cipher_or_none()
            if cipher is None:
                self._state = AuthSession(
                    status=AuthStatus.ERROR,
                    masked_account_name=profile.masked_account_name,
                    membership_type=profile.membership_type,
                    cookie_expires_at=profile.cookie_expires_at,
                    last_validated_at=profile.last_validated_at,
                    persistence=AuthPersistence.LOCAL,
                    stored_but_unavailable=True,
                )
                logger.error(
                    "Saved authentication is unavailable because no encryption key is configured",
                    extra={"event": "auth_key_unavailable"},
                )
                return
            try:
                jar = cipher.decrypt(profile.encrypted_cookies)
            except AppError:
                self._state = AuthSession(
                    status=AuthStatus.ERROR,
                    masked_account_name=profile.masked_account_name,
                    membership_type=profile.membership_type,
                    cookie_expires_at=profile.cookie_expires_at,
                    last_validated_at=profile.last_validated_at,
                    persistence=AuthPersistence.LOCAL,
                    stored_but_unavailable=True,
                )
                logger.exception(
                    "Saved authentication could not be decrypted",
                    extra={"event": "auth_decryption_failed"},
                )
                return
            now = datetime.now(UTC)
            cookie_expiration = self._as_utc(profile.cookie_expires_at)
            if cookie_expiration is not None and cookie_expiration <= now:
                self._state = AuthSession(
                    status=AuthStatus.EXPIRED,
                    cookie_expires_at=cookie_expiration,
                    persistence=AuthPersistence.LOCAL,
                )
                await session.execute(delete(AuthProfile).where(AuthProfile.provider == "bilibili"))
                await session.commit()
                return
            self._state = AuthSession(
                status=profile.status,
                jar=jar,
                masked_account_name=profile.masked_account_name,
                membership_type=profile.membership_type,
                cookie_expires_at=cookie_expiration,
                last_validated_at=self._as_utc(profile.last_validated_at),
                persistence=AuthPersistence.LOCAL,
            )

    def register_clear_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._clear_callbacks.append(callback)

    async def upload(self, payload: bytes, persistence: AuthPersistence) -> AuthStatusResponse:
        parsed = self.parser.parse(payload)
        async with self._lock:
            try:
                validation = await self.provider.validate_auth(parsed.jar)
            except AppError:
                self._state = AuthSession(
                    status=AuthStatus.ERROR,
                    jar=parsed.jar,
                    cookie_expires_at=parsed.expires_at,
                    persistence=AuthPersistence.SESSION,
                )
                raise

            if not validation.logged_in:
                await self._delete_persisted()
                self._state = AuthSession(status=AuthStatus.EXPIRED)
                await self._notify_cleared()
                return self.status()

            now = datetime.now(UTC)
            auth_status = AuthStatus.PREMIUM if validation.premium else AuthStatus.AUTHENTICATED
            candidate = AuthSession(
                status=auth_status,
                jar=parsed.jar,
                masked_account_name=self._mask_account_name(validation.account_name),
                membership_type=validation.membership_type,
                cookie_expires_at=parsed.expires_at,
                last_validated_at=now,
                persistence=persistence,
            )
            if persistence == AuthPersistence.LOCAL:
                await self._persist(candidate)
            else:
                await self._delete_persisted()
            self._state = candidate
            await self._notify_cleared()
            return self.status()

    async def validate(self) -> AuthStatusResponse:
        async with self._lock:
            jar = self._state.jar
            if jar is None:
                raise AppError(
                    ErrorCode.AUTH_REQUIRED,
                    "当前没有可校验的登录状态",
                    action="上传 Bilibili Cookie JSON 后重试",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )
            try:
                validation = await self.provider.validate_auth(jar)
            except AppError:
                self._state.status = AuthStatus.ERROR
                raise
            if not validation.logged_in:
                await self._delete_persisted()
                self._state = AuthSession(status=AuthStatus.EXPIRED)
                await self._notify_cleared()
                return self.status()

            self._apply_validation(validation)
            if self._state.persistence == AuthPersistence.LOCAL:
                await self._persist(self._state)
            return self.status()

    async def clear(self) -> AuthStatusResponse:
        async with self._lock:
            await self._delete_persisted()
            self._state = AuthSession()
            await self._notify_cleared()
            return self.status()

    def status(self) -> AuthStatusResponse:
        state = self._state
        logged_in = state.status in {AuthStatus.AUTHENTICATED, AuthStatus.PREMIUM}
        return AuthStatusResponse(
            status=state.status,
            logged_in=logged_in,
            premium=state.status == AuthStatus.PREMIUM,
            membership_type=state.membership_type,
            masked_account_name=state.masked_account_name,
            last_validated_at=state.last_validated_at,
            cookie_expires_at=state.cookie_expires_at,
            persistence=state.persistence,
            has_credentials=state.jar is not None or state.stored_but_unavailable,
        )

    async def cookie_jar(self) -> CookieJar:
        async with self._lock:
            expiration = self._state.cookie_expires_at
            if expiration is not None and expiration <= datetime.now(UTC):
                await self._delete_persisted()
                self._state = AuthSession(status=AuthStatus.EXPIRED)
                await self._notify_cleared()
            if self._state.jar is None or self._state.status not in {
                AuthStatus.AUTHENTICATED,
                AuthStatus.PREMIUM,
            }:
                raise AppError(
                    ErrorCode.AUTH_REQUIRED,
                    "需要有效登录态才能完成本次请求",
                    action="重新校验或上传 Cookie，也可切换为匿名模式",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )
            return clone_cookie_jar(self._state.jar)

    async def _persist(self, state: AuthSession) -> None:
        if state.jar is None:
            raise RuntimeError("Cannot persist an empty authentication session")
        cipher = self._cipher_or_error()
        ciphertext = cipher.encrypt(state.jar)
        async with self.session_factory() as session:
            profile = await session.scalar(
                select(AuthProfile).where(AuthProfile.provider == "bilibili")
            )
            if profile is None:
                profile = AuthProfile(
                    provider="bilibili",
                    encrypted_cookies=ciphertext,
                    status=state.status,
                    masked_account_name=state.masked_account_name,
                    membership_type=state.membership_type,
                    cookie_expires_at=state.cookie_expires_at,
                    last_validated_at=state.last_validated_at,
                )
                session.add(profile)
            else:
                profile.encrypted_cookies = ciphertext
                profile.status = state.status
                profile.masked_account_name = state.masked_account_name
                profile.membership_type = state.membership_type
                profile.cookie_expires_at = state.cookie_expires_at
                profile.last_validated_at = state.last_validated_at
            await session.commit()

    async def _delete_persisted(self) -> None:
        async with self.session_factory() as session:
            await session.execute(delete(AuthProfile).where(AuthProfile.provider == "bilibili"))
            await session.commit()

    async def _notify_cleared(self) -> None:
        for callback in self._clear_callbacks:
            await callback()

    def _cipher_or_none(self) -> CookieCipher | None:
        try:
            key = self.settings.load_cookie_encryption_key()
        except (OSError, ValueError):
            logger.exception(
                "Cookie encryption key could not be loaded",
                extra={"event": "auth_key_load_failed"},
            )
            return None
        return CookieCipher(key) if key else None

    def _cipher_or_error(self) -> CookieCipher:
        cipher = self._cipher_or_none()
        if cipher is None:
            raise AppError(
                ErrorCode.ENCRYPTION_KEY_MISSING,
                "未配置本机加密主密钥，无法记住 Cookie",
                action="配置 APP_COOKIE_ENCRYPTION_KEY，或选择仅本次会话使用",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return cipher

    def _apply_validation(self, validation: AuthValidation) -> None:
        self._state.status = AuthStatus.PREMIUM if validation.premium else AuthStatus.AUTHENTICATED
        self._state.masked_account_name = self._mask_account_name(validation.account_name)
        self._state.membership_type = validation.membership_type
        self._state.last_validated_at = datetime.now(UTC)

    @staticmethod
    def _mask_account_name(name: str | None) -> str | None:
        if not name:
            return None
        if len(name) == 1:
            return "*"
        if len(name) == 2:
            return f"{name[0]}*"
        return f"{name[0]}{'*' * min(len(name) - 2, 4)}{name[-1]}"

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
