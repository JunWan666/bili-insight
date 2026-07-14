from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from typing import Protocol, runtime_checkable

from fastapi import status
from sqlalchemy import func, select

from app.core.exceptions import AppError, ErrorCode
from app.db.models import Job, JobStatus
from app.providers.models import VideoProvider
from app.schemas.settings import AppSettings
from app.services.analyses import AnalysisService, ProviderSubtitleService
from app.services.artifacts import ArtifactService
from app.services.downloads import DownloadExecutor
from app.services.jobs import JobService
from app.services.settings import SettingsService

logger = logging.getLogger(__name__)


@runtime_checkable
class RuntimeConfigurableProvider(Protocol):
    def configure_runtime(
        self,
        *,
        timeout_seconds: float,
        upstream_interval_milliseconds: int,
    ) -> None: ...


class RuntimeSettingsCoordinator:
    """Apply persisted application settings to all live runtime components."""

    def __init__(
        self,
        settings_service: SettingsService,
        artifact_service: ArtifactService,
        download_executor: DownloadExecutor,
        analysis_service: AnalysisService,
        subtitle_service: ProviderSubtitleService,
        provider: VideoProvider,
        job_service: JobService,
    ) -> None:
        self.settings_service = settings_service
        self.artifact_service = artifact_service
        self.download_executor = download_executor
        self.analysis_service = analysis_service
        self.subtitle_service = subtitle_service
        self.provider = provider
        self.job_service = job_service
        self._lock = asyncio.Lock()
        self._current: AppSettings | None = None

    async def apply_startup(self, value: AppSettings) -> None:
        """Apply persisted settings before workers are started."""

        async with self._lock:
            await self._apply_components(value, startup=True)
            self._current = value.model_copy(deep=True)

    async def apply_runtime(self, value: AppSettings) -> None:
        """Stop workers, atomically reconfigure their dependencies, then restart them."""

        async with self._lock:
            try:
                await self.job_service.stop()
                await self._ensure_storage_change_is_safe(value)
                await self._apply_components(value, startup=False)
                await self.job_service.start()
                self._current = value.model_copy(deep=True)
            except Exception:
                try:
                    await self.job_service.start()
                except Exception:
                    logger.exception(
                        "Emergency worker restart after runtime reconfiguration failed",
                        extra={"event": "runtime_settings_worker_restart_failed"},
                    )
                raise

    async def maintenance_policy(self) -> tuple[int | None, int | None]:
        value = await self.settings_service.get()
        return value.storage.cleanup_after_days, value.privacy.history_retention_days

    async def _apply_components(self, value: AppSettings, *, startup: bool) -> None:
        artifact_root, temp_root = self.settings_service.resolve_storage_directories(
            value,
            create=True,
        )
        runtime = replace(
            self.download_executor.runtime,
            retries=value.download.retry_limit,
            artifact_quota_bytes=value.storage.quota_bytes,
            rate_limit_bytes_per_second=value.network.rate_limit_bytes_per_second,
        )
        await self.artifact_service.reconfigure_root(artifact_root, startup=startup)
        await self.download_executor.reconfigure(
            runtime=runtime,
            artifact_root=artifact_root,
            temp_root=temp_root,
            default_filename_template=value.download.filename_template,
            timeout_seconds=float(value.network.timeout_seconds),
        )
        await self.analysis_service.reconfigure_storage(artifact_root)
        await self.subtitle_service.reconfigure_temp_root(temp_root)
        if isinstance(self.provider, RuntimeConfigurableProvider):
            self.provider.configure_runtime(
                timeout_seconds=float(value.network.timeout_seconds),
                upstream_interval_milliseconds=value.network.upstream_interval_milliseconds,
            )
        await self.job_service.reconfigure_concurrency(
            download_concurrency=value.download.concurrency,
            analysis_concurrency=self.job_service.analysis_concurrency,
        )

    async def _ensure_storage_change_is_safe(self, value: AppSettings) -> None:
        artifact_root, temp_root = self.settings_service.resolve_storage_directories(
            value,
            create=False,
        )
        artifact_changes = artifact_root != self.artifact_service.root
        temp_changes = temp_root != self.download_executor.temp_root
        if not artifact_changes and not temp_changes:
            return
        active_statuses = (
            JobStatus.QUEUED,
            JobStatus.PREPARING,
            JobStatus.RUNNING,
            JobStatus.POST_PROCESSING,
            JobStatus.PAUSED,
        )
        async with self.job_service.session_factory() as session:
            count = int(
                await session.scalar(
                    select(func.count(Job.id)).where(Job.status.in_(active_statuses))
                )
                or 0
            )
        if count:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "存在排队、运行中或已暂停的任务，暂时不能切换存储目录",
                action="等待任务完成或取消相关任务后重试",
                status_code=status.HTTP_409_CONFLICT,
            )
        retained_temp_files = temp_changes and await asyncio.to_thread(
            self._contains_regular_files,
            self.download_executor.temp_root,
        )
        if retained_temp_files:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "临时目录仍包含可恢复的工作文件，暂时不能切换目录",
                action="恢复或清理相关任务后重试",
                status_code=status.HTTP_409_CONFLICT,
            )

    @staticmethod
    def _contains_regular_files(root: Path) -> bool:
        if not root.exists():
            return False
        return any(path.is_file() for path in root.rglob("*"))
