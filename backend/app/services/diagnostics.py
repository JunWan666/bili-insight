from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.core.process_limits import (
    DEFAULT_CHILD_PROCESS_CONCURRENCY,
    DEFAULT_PROCESS_MAX_THREADS,
    DEFAULT_PROCESS_MEMORY_LIMIT_BYTES,
)
from app.core.runtime import probe_executable_version
from app.db.models import Job, JobStatus
from app.db.session import check_database
from app.schemas.diagnostics import (
    ComponentHealth,
    Diagnostics,
    DiskStatus,
    HealthComponent,
    QueueStatus,
)
from app.schemas.settings import AppSettings
from app.services.jobs import JobService
from app.services.settings import SettingsService

ExecutableProbe = Callable[[str], Awaitable[tuple[bool, str | None]]]
PackageProbe = Callable[[str, str], tuple[bool, str | None]]

_SAFE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~\-]{0,63}")
_EXECUTABLE_VERSION = re.compile(r"\bversion\s+([^\s]+)", re.IGNORECASE)


class DiagnosticsService:
    def __init__(
        self,
        settings: Settings,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession],
        settings_service: SettingsService,
        *,
        job_service: JobService | None = None,
        started_at: datetime | None = None,
        executable_probe: ExecutableProbe | None = None,
        package_probe: PackageProbe | None = None,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.session_factory = session_factory
        self.settings_service = settings_service
        self.job_service = job_service
        self.started_at = _as_utc(started_at or datetime.now(UTC))
        self.executable_probe = executable_probe or _probe_executable
        self.package_probe = package_probe or _probe_package
        self._last_application_settings: AppSettings | None = None

    async def apply_runtime_settings(self, value: AppSettings) -> None:
        self._last_application_settings = value.model_copy(deep=True)

    async def collect(self, *, request_id: str | None = None) -> Diagnostics:
        application_settings = await self._application_settings()
        if not application_settings.privacy.diagnostics_enabled:
            raise AppError(
                ErrorCode.DIAGNOSTICS_DISABLED,
                "详细诊断已在隐私设置中关闭",
                action="如需检查组件、队列或磁盘指标，请在设置页临时启用诊断；基础健康检查仍可用",
                status_code=403,
            )
        database_task = asyncio.create_task(self._database_and_queue())
        ffmpeg_task = asyncio.create_task(self._executable_component("FFmpeg", "ffmpeg"))
        ffprobe_task = asyncio.create_task(self._executable_component("FFprobe", "ffprobe"))
        disk_task = asyncio.create_task(self._disk_component())
        asr_task = asyncio.create_task(
            self._package_component(
                name="ASR",
                module_name="faster_whisper",
                distribution_name="faster-whisper",
                available_message="faster-whisper 依赖可用，模型将在任务执行时按需加载",
                unavailable_message="faster-whisper 未安装，语音转写当前不可用",
            )
        )
        ocr_task = asyncio.create_task(self._ocr_component(application_settings))
        (database, queue), ffmpeg, ffprobe, (storage, disk), asr, ocr = await asyncio.gather(
            database_task,
            ffmpeg_task,
            ffprobe_task,
            disk_task,
            asr_task,
            ocr_task,
        )
        summary = HealthComponent(
            name="Summary",
            status=ComponentHealth.HEALTHY,
            version=_safe_version(self.settings.version),
            message="本地可复现内容摘要能力可用",
        )
        resource_limits = HealthComponent(
            name="ResourceLimits",
            status=ComponentHealth.HEALTHY,
            version=None,
            message=(
                f"外部进程并发 {DEFAULT_CHILD_PROCESS_CONCURRENCY}，单进程线程 "
                f"{DEFAULT_PROCESS_MAX_THREADS}，内存上限 "
                f"{DEFAULT_PROCESS_MEMORY_LIMIT_BYTES // 1024**2} MiB"
            ),
        )
        components = [database, storage]
        if self.job_service is not None:
            components.append(self._worker_component())
        components.extend((ffmpeg, ffprobe, asr, ocr, summary, resource_limits))
        overall = _overall_health(components)
        return Diagnostics(
            application_name=_safe_application_name(self.settings.app_name),
            application_version=_safe_version(self.settings.version) or "unknown",
            environment=self.settings.environment,
            started_at=self.started_at,
            status=overall,
            components=components,
            disk=disk,
            queue=queue,
            request_id=_safe_request_id(request_id),
        )

    async def _application_settings(self) -> AppSettings:
        try:
            value = await self.settings_service.get()
        except Exception:
            if self._last_application_settings is None:
                raise
            return self._last_application_settings.model_copy(deep=True)
        self._last_application_settings = value.model_copy(deep=True)
        return value

    def _worker_component(self) -> HealthComponent:
        try:
            health = self.job_service.health() if self.job_service is not None else None
        except Exception:
            health = None
        if health is None or health.status == "stopped":
            return HealthComponent(
                name="Worker",
                status=ComponentHealth.UNAVAILABLE,
                version=None,
                message="任务 Worker 未运行",
            )
        if health.status == "degraded":
            return HealthComponent(
                name="Worker",
                status=ComponentHealth.DEGRADED,
                version=None,
                message="任务 Worker 数量或生命周期状态异常",
            )
        return HealthComponent(
            name="Worker",
            status=ComponentHealth.HEALTHY,
            version=None,
            message="下载与分析 Worker 正常运行",
        )

    async def _database_and_queue(self) -> tuple[HealthComponent, QueueStatus]:
        empty = QueueStatus(queued=0, running=0, failed_last_24_hours=0)
        try:
            await check_database(self.engine)
            cutoff = datetime.now(UTC) - timedelta(hours=24)
            async with self.session_factory() as session:
                queued = await session.scalar(
                    select(func.count()).select_from(Job).where(Job.status == JobStatus.QUEUED)
                )
                running = await session.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(
                        Job.status.in_(
                            (
                                JobStatus.PREPARING,
                                JobStatus.RUNNING,
                                JobStatus.POST_PROCESSING,
                            )
                        )
                    )
                )
                failed = await session.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(
                        Job.status == JobStatus.FAILED,
                        func.coalesce(Job.finished_at, Job.updated_at, Job.created_at) >= cutoff,
                    )
                )
        except Exception:
            return (
                HealthComponent(
                    name="Database",
                    status=ComponentHealth.UNAVAILABLE,
                    version=None,
                    message="数据库健康检查失败",
                ),
                empty,
            )
        return (
            HealthComponent(
                name="Database",
                status=ComponentHealth.HEALTHY,
                version=None,
                message="数据库连接与任务统计查询正常",
            ),
            QueueStatus(
                queued=int(queued or 0),
                running=int(running or 0),
                failed_last_24_hours=int(failed or 0),
            ),
        )

    async def _disk_component(self) -> tuple[HealthComponent, DiskStatus]:
        empty = DiskStatus(
            total_bytes=0,
            used_bytes=0,
            free_bytes=0,
            artifact_bytes=0,
            temporary_bytes=0,
        )
        try:
            (
                artifact_directory,
                temporary_directory,
            ) = await self.settings_service.storage_directories()
            disk = await asyncio.to_thread(
                _read_disk_status,
                self.settings_service.storage_root,
                artifact_directory,
                temporary_directory,
            )
        except Exception:
            return (
                HealthComponent(
                    name="Storage",
                    status=ComponentHealth.UNAVAILABLE,
                    version=None,
                    message="存储空间或目录统计不可用",
                ),
                empty,
            )
        return (
            HealthComponent(
                name="Storage",
                status=ComponentHealth.HEALTHY,
                version=None,
                message="受控存储根目录与磁盘统计正常",
            ),
            disk,
        )

    async def _executable_component(self, display_name: str, executable: str) -> HealthComponent:
        try:
            available, version = await self.executable_probe(executable)
        except Exception:
            available, version = False, None
        return HealthComponent(
            name=display_name,
            status=(ComponentHealth.HEALTHY if available else ComponentHealth.UNAVAILABLE),
            version=_safe_version(version),
            message=(
                f"{display_name} 可执行程序可用"
                if available
                else f"{display_name} 未安装或无法执行"
            ),
        )

    async def _package_component(
        self,
        *,
        name: str,
        module_name: str,
        distribution_name: str,
        available_message: str,
        unavailable_message: str,
    ) -> HealthComponent:
        try:
            available, version = await asyncio.to_thread(
                self.package_probe,
                module_name,
                distribution_name,
            )
        except Exception:
            available, version = False, None
        return HealthComponent(
            name=name,
            status=(ComponentHealth.HEALTHY if available else ComponentHealth.UNAVAILABLE),
            version=_safe_version(version),
            message=available_message if available else unavailable_message,
        )

    async def _ocr_component(self, value: AppSettings) -> HealthComponent:
        package = await self._package_component(
            name="OCR",
            module_name="paddleocr",
            distribution_name="paddleocr",
            available_message="PaddleOCR 依赖可用",
            unavailable_message="PaddleOCR 未安装，画面文字识别当前不可用",
        )
        if not value.analysis.ocr_enabled:
            return HealthComponent(
                name="OCR",
                status=ComponentHealth.DEGRADED,
                version=package.version,
                message="OCR 已在应用设置中关闭",
            )
        if package.status != ComponentHealth.HEALTHY:
            return package
        try:
            paddle_available, _ = await asyncio.to_thread(
                self.package_probe,
                "paddle",
                "paddlepaddle",
            )
        except Exception:
            paddle_available = False
        if not paddle_available:
            return HealthComponent(
                name="OCR",
                status=ComponentHealth.UNAVAILABLE,
                version=package.version,
                message="PaddleOCR 已安装，但 PaddlePaddle 运行时不可用",
            )
        return package


async def _probe_executable(name: str) -> tuple[bool, str | None]:
    try:
        available, first_line = await probe_executable_version(name)
    except (OSError, ValueError):
        return False, None
    if not available or first_line is None:
        return False, None
    match = _EXECUTABLE_VERSION.search(first_line)
    return True, _safe_version(match.group(1) if match else None)


def _probe_package(module_name: str, distribution_name: str) -> tuple[bool, str | None]:
    try:
        if importlib.util.find_spec(module_name) is None:
            return False, None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False, None
    try:
        version = importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        version = None
    return True, _safe_version(version)


def _read_disk_status(
    root: Path,
    artifact_directory: Path,
    temporary_directory: Path,
) -> DiskStatus:
    for directory in (root, artifact_directory, temporary_directory):
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or (hasattr(directory, "is_junction") and directory.is_junction())
        ):
            raise OSError("required storage directory is unavailable")
    usage = shutil.disk_usage(root)
    return DiskStatus(
        total_bytes=usage.total,
        used_bytes=max(0, usage.total - usage.free),
        free_bytes=usage.free,
        artifact_bytes=_directory_size(artifact_directory),
        temporary_bytes=_directory_size(temporary_directory),
    )


def _directory_size(root: Path) -> int:
    if not root.exists() or root.is_symlink():
        return 0
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as scanner:
                entries = tuple(scanner)
        except FileNotFoundError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or (
                    hasattr(os.path, "isjunction") and os.path.isjunction(entry.path)
                ):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except FileNotFoundError:
                continue
    return total


def _overall_health(components: list[HealthComponent]) -> ComponentHealth:
    by_name = {component.name: component.status for component in components}
    if any(
        by_name.get(name) == ComponentHealth.UNAVAILABLE
        for name in ("Database", "Storage", "Worker")
    ):
        return ComponentHealth.UNAVAILABLE
    if any(component.status != ComponentHealth.HEALTHY for component in components):
        return ComponentHealth.DEGRADED
    return ComponentHealth.HEALTHY


def _safe_version(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized if _SAFE_VERSION.fullmatch(normalized) else None


def _safe_application_name(value: str) -> str:
    normalized = " ".join(value.split())
    if (
        not normalized
        or len(normalized) > 128
        or "/" in normalized
        or "\\" in normalized
        or "://" in normalized
        or any(ord(character) < 32 for character in normalized)
    ):
        return "Bili Insight API"
    return normalized


def _safe_request_id(value: str | None) -> str | None:
    if value is None:
        return None
    if value.isascii() and len(value) <= 64 and value.replace("-", "").isalnum():
        return value
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
