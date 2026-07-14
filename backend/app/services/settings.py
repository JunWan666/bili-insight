from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.db.models import AppSetting
from app.schemas.settings import AppSettings, StorageSettings, normalize_storage_relative_path

logger = logging.getLogger(__name__)

SettingsUpdateCallback = Callable[[AppSettings], Awaitable[None]]


class SettingsService:
    SINGLETON_ID = 1

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        storage_root: Path | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        configured_root = storage_root or self._common_storage_root(settings)
        self.storage_root = configured_root.expanduser().resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._update_callbacks: list[SettingsUpdateCallback] = []
        self._defaults = AppSettings(
            storage=StorageSettings(
                artifact_directory=self._configured_relative_directory(
                    settings.artifact_dir, "artifacts"
                ),
                temporary_directory=self._configured_relative_directory(settings.temp_dir, "tmp"),
            )
        )
        self._defaults_storage_paths = self._storage_paths(self._defaults, create=False)

    async def get(self) -> AppSettings:
        try:
            async with self.session_factory() as session:
                record = await session.get(AppSetting, self.SINGLETON_ID)
        except SQLAlchemyError as exc:
            raise self._database_error() from exc
        if record is None:
            result = self._defaults.model_copy(deep=True)
        else:
            try:
                result = AppSettings.model_validate(record.payload)
            except ValidationError as exc:
                logger.error(
                    "Persisted application settings failed schema validation",
                    extra={"event": "app_settings_invalid"},
                )
                raise AppError(
                    ErrorCode.DATABASE_ERROR,
                    "已保存的应用设置无法读取",
                    action="恢复数据库备份或清除损坏的设置记录",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                ) from exc
        self._storage_paths(result, create=False)
        return result

    async def update(self, value: AppSettings) -> AppSettings:
        self._storage_paths(value, create=True)
        payload = value.model_dump(mode="json")
        async with self._lock:
            previous = await self.get()
            try:
                await self._persist(payload)
            except IntegrityError:
                await asyncio.sleep(0)
                try:
                    await self._persist(payload)
                except IntegrityError as exc:
                    raise self._database_error() from exc
            try:
                for callback in tuple(self._update_callbacks):
                    await callback(value.model_copy(deep=True))
            except Exception as exc:
                await self._restore_after_callback_failure(previous)
                logger.exception(
                    "Application settings runtime update failed",
                    extra={"event": "app_settings_runtime_update_failed"},
                )
                if isinstance(exc, AppError):
                    raise
                raise AppError(
                    ErrorCode.INTERNAL_ERROR,
                    "应用设置无法应用到运行中的服务",
                    action="设置已回滚，请检查系统诊断后重试",
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                ) from exc
        logger.info("Application settings updated", extra={"event": "app_settings_updated"})
        return value.model_copy(deep=True)

    def register_update_callback(self, callback: SettingsUpdateCallback) -> None:
        if callback not in self._update_callbacks:
            self._update_callbacks.append(callback)

    async def _restore_after_callback_failure(self, previous: AppSettings) -> None:
        try:
            await self._persist(previous.model_dump(mode="json"))
            for callback in tuple(self._update_callbacks):
                try:
                    await callback(previous.model_copy(deep=True))
                except Exception:
                    logger.exception(
                        "Application settings runtime rollback callback failed",
                        extra={"event": "app_settings_runtime_rollback_failed"},
                    )
        except Exception:
            logger.exception(
                "Application settings persistence rollback failed",
                extra={"event": "app_settings_persistence_rollback_failed"},
            )

    async def _persist(self, payload: dict[str, Any]) -> None:
        try:
            async with self.session_factory() as session, session.begin():
                record = await session.get(AppSetting, self.SINGLETON_ID)
                if record is None:
                    session.add(
                        AppSetting(
                            id=self.SINGLETON_ID,
                            payload=payload,
                            created_at=datetime.now(UTC),
                            updated_at=datetime.now(UTC),
                        )
                    )
                else:
                    record.payload = payload
                    record.updated_at = datetime.now(UTC)
        except IntegrityError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error() from exc

    async def storage_directories(self) -> tuple[Path, Path]:
        return self._storage_paths(await self.get(), create=False)

    def resolve_storage_directories(
        self,
        value: AppSettings,
        *,
        create: bool = False,
    ) -> tuple[Path, Path]:
        return self._storage_paths(value, create=create)

    def defaults(self) -> AppSettings:
        return self._defaults.model_copy(deep=True)

    def default_storage_directories(self) -> tuple[Path, Path]:
        return self._storage_paths(self._defaults, create=False)

    def _storage_paths(self, value: AppSettings, *, create: bool) -> tuple[Path, Path]:
        artifact = self._resolve_relative_directory(
            value.storage.artifact_directory,
            create=create,
        )
        temporary = self._resolve_relative_directory(
            value.storage.temporary_directory,
            create=create,
        )
        return artifact, temporary

    def _resolve_relative_directory(self, value: str, *, create: bool) -> Path:
        try:
            normalized = normalize_storage_relative_path(value)
        except ValueError as exc:
            raise self._unsafe_storage_path() from exc
        relative = PurePosixPath(normalized)
        candidate = self.storage_root.joinpath(*relative.parts)
        try:
            self._reject_symlink_components(candidate)
            resolved = candidate.resolve(strict=False)
            if not resolved.is_relative_to(self.storage_root) or resolved == self.storage_root:
                raise self._unsafe_storage_path()
            if create:
                candidate.mkdir(parents=True, exist_ok=True)
                self._reject_symlink_components(candidate)
                resolved = candidate.resolve(strict=True)
                if not resolved.is_relative_to(self.storage_root):
                    raise self._unsafe_storage_path()
            return resolved
        except AppError:
            raise
        except OSError as exc:
            logger.warning(
                "Storage directory validation failed",
                extra={"event": "app_settings_storage_unavailable"},
            )
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "存储目录不可用或权限不足",
                action="选择受控存储根目录内可写的相对目录",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc

    def _reject_symlink_components(self, candidate: Path) -> None:
        relative = candidate.relative_to(self.storage_root)
        current = self.storage_root
        for part in relative.parts:
            current /= part
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise self._unsafe_storage_path()

    def _configured_relative_directory(self, configured: Path, fallback: str) -> str:
        try:
            resolved = configured.expanduser().resolve(strict=False)
            relative = resolved.relative_to(self.storage_root)
            value = normalize_storage_relative_path(relative.as_posix())
            candidate = self.storage_root.joinpath(*PurePosixPath(value).parts)
            self._reject_symlink_components(candidate)
            return value
        except (OSError, ValueError, AppError):
            return fallback

    @staticmethod
    def _common_storage_root(settings: Settings) -> Path:
        data = settings.data_dir.expanduser().resolve(strict=False)
        artifact = settings.artifact_dir.expanduser().resolve(strict=False)
        temporary = settings.temp_dir.expanduser().resolve(strict=False)
        try:
            common_artifacts = Path(os.path.commonpath((artifact, temporary)))
        except ValueError:
            return data
        if common_artifacts == data or common_artifacts.is_relative_to(data):
            return data
        if data.parent == common_artifacts and common_artifacts != Path(common_artifacts.anchor):
            return common_artifacts
        return data

    @staticmethod
    def _unsafe_storage_path() -> AppError:
        return AppError(
            ErrorCode.VALIDATION_ERROR,
            "存储目录必须是受控存储根目录内的安全相对路径",
            action="移除绝对路径、路径穿越或符号链接后重试",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    @staticmethod
    def _database_error() -> AppError:
        logger.exception(
            "Application settings database operation failed",
            extra={"event": "app_settings_database_error"},
        )
        return AppError(
            ErrorCode.DATABASE_ERROR,
            "应用设置暂时无法保存或读取",
            action="稍后重试；若持续失败，请检查数据库健康状态",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
