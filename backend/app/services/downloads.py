from __future__ import annotations

import asyncio
import json
import logging
import math
import mimetypes
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Protocol, cast

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.db.models import (
    AccessContext,
    Artifact,
    Job,
    JobType,
    MediaStream,
    StreamKind,
    Video,
    VideoPart,
)
from app.media.danmaku import (
    MAX_DANMAKU_BYTES,
    DanmakuValidationError,
    validate_danmaku_xml,
)
from app.media.download import (
    DownloadCanceled,
    DownloadCheckpoint,
    DownloadPaused,
    DownloadProgress,
    DownloadResult,
    HTTPMediaDownloader,
    MediaDownloadError,
    ProgressCallback,
)
from app.media.ffmpeg import FFmpegError, FFmpegProcessor, MediaProbe, MediaValidationError
from app.media.security import (
    MediaURLValidator,
    render_filename_template,
    safe_child_path,
    sanitize_filename,
)
from app.providers.models import ProviderPart, ProviderSubtitle, ProviderVideo
from app.schemas.jobs import DownloadRequest, OutputContainer, ProcessingMode
from app.schemas.video import AccessMode
from app.services.artifacts import ArtifactService
from app.services.videos import VideoService

logger = logging.getLogger(__name__)


class DownloadExecutionReporter(Protocol):
    async def update(
        self,
        *,
        phase: str,
        progress: float,
        downloaded_bytes: int | None = None,
        total_bytes: int | None = None,
        automatic_attempt: int | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class DownloadRuntimeConfig:
    retries: int = 3
    min_free_bytes: int = 64 * 1024 * 1024
    unknown_size_reserve_bytes: int = 256 * 1024 * 1024
    retry_base_delay_seconds: float = 0.5
    maximum_stream_bytes: int = 1_099_511_627_776
    artifact_quota_bytes: int | None = None
    rate_limit_bytes_per_second: int | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.retries <= 8:
            raise ValueError("Download retry count is outside the safe range")
        if self.min_free_bytes < 0 or self.unknown_size_reserve_bytes < 0:
            raise ValueError("Disk reserve values cannot be negative")
        if self.maximum_stream_bytes < 64 * 1024 * 1024:
            raise ValueError("Maximum media stream size is outside the safe range")
        if self.artifact_quota_bytes is not None and self.artifact_quota_bytes <= 0:
            raise ValueError("Artifact quota must be positive")
        if self.rate_limit_bytes_per_second is not None and self.rate_limit_bytes_per_second <= 0:
            raise ValueError("Download rate limit must be positive")


@dataclass(frozen=True, slots=True)
class _ProviderContext:
    video: ProviderVideo
    part: ProviderPart
    cover_url: str
    cookies: CookieJar | None


@dataclass(frozen=True, slots=True)
class _SubtitlePlanItem:
    subtitle: ProviderSubtitle
    filename: str


class DownloadExecutor:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        video_service: VideoService,
        artifact_service: ArtifactService,
        *,
        downloader: HTTPMediaDownloader | None = None,
        resource_downloader: HTTPMediaDownloader | None = None,
        processor: FFmpegProcessor | None = None,
        runtime: DownloadRuntimeConfig | None = None,
        default_filename_template: str = "{title} - P{page} - {quality}",
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.video_service = video_service
        self.artifact_service = artifact_service
        self.runtime = runtime or DownloadRuntimeConfig()
        self.default_filename_template = default_filename_template
        validator = MediaURLValidator(settings.media_host_suffixes)
        self.downloader = downloader or HTTPMediaDownloader(
            validator,
            user_agent=settings.user_agent,
            timeout_seconds=max(30.0, settings.upstream_timeout_seconds),
            connect_timeout_seconds=settings.upstream_connect_timeout_seconds,
            maximum_size_bytes=self.runtime.maximum_stream_bytes,
            rate_limit_bytes_per_second=self.runtime.rate_limit_bytes_per_second,
        )
        resource_validator = MediaURLValidator((*settings.media_host_suffixes, "hdslb.com"))
        self.resource_downloader = resource_downloader or HTTPMediaDownloader(
            resource_validator,
            user_agent=settings.user_agent,
            timeout_seconds=max(30.0, settings.upstream_timeout_seconds),
            connect_timeout_seconds=settings.upstream_connect_timeout_seconds,
            maximum_size_bytes=32 * 1024 * 1024,
            rate_limit_bytes_per_second=self.runtime.rate_limit_bytes_per_second,
        )
        self.processor = processor or FFmpegProcessor()
        self.temp_root = settings.temp_dir.expanduser().resolve()
        self.artifact_root = settings.artifact_dir.expanduser().resolve()
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    async def reconfigure(
        self,
        *,
        runtime: DownloadRuntimeConfig,
        artifact_root: Path,
        temp_root: Path,
        default_filename_template: str,
        timeout_seconds: float,
    ) -> None:
        if not math.isfinite(timeout_seconds) or not 1.0 <= timeout_seconds <= 3_600.0:
            raise ValueError("Download timeout is outside the safe range")
        new_artifact_root, new_temp_root = await asyncio.gather(
            asyncio.to_thread(lambda: artifact_root.expanduser().resolve()),
            asyncio.to_thread(lambda: temp_root.expanduser().resolve()),
        )
        if new_artifact_root == new_temp_root:
            raise ValueError("Artifact and temporary directories must be different")
        if self.artifact_service.root != new_artifact_root:
            raise ValueError("Artifact service root must be reconfigured before the downloader")
        await asyncio.to_thread(new_artifact_root.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(new_temp_root.mkdir, parents=True, exist_ok=True)
        render_filename_template(
            default_filename_template,
            {"title": "title", "bvid": "BV", "page": 1, "part": "part", "quality": "HD"},
            extension="mp4",
        )
        self.runtime = runtime
        self.artifact_root = new_artifact_root
        self.temp_root = new_temp_root
        self.default_filename_template = default_filename_template
        connect_timeout_seconds = min(
            timeout_seconds,
            self.settings.upstream_connect_timeout_seconds,
        )
        if isinstance(self.downloader, HTTPMediaDownloader):
            self.downloader.configure_limits(
                maximum_size_bytes=runtime.maximum_stream_bytes,
                rate_limit_bytes_per_second=runtime.rate_limit_bytes_per_second,
            )
            self.downloader.configure_timeout(
                timeout_seconds=timeout_seconds,
                connect_timeout_seconds=connect_timeout_seconds,
            )
        if isinstance(self.resource_downloader, HTTPMediaDownloader):
            self.resource_downloader.configure_limits(
                maximum_size_bytes=32 * 1024 * 1024,
                rate_limit_bytes_per_second=runtime.rate_limit_bytes_per_second,
            )
            self.resource_downloader.configure_timeout(
                timeout_seconds=timeout_seconds,
                connect_timeout_seconds=connect_timeout_seconds,
            )

    async def prepare(self, request: DownloadRequest) -> dict[str, object]:
        async with self.session_factory() as session:
            part = await session.scalar(
                select(VideoPart)
                .where(VideoPart.id == request.part_id, VideoPart.video_id == request.video_id)
                .options(selectinload(VideoPart.video), selectinload(VideoPart.streams))
            )
            if part is None:
                raise AppError(
                    ErrorCode.RESOURCE_NOT_FOUND,
                    "视频分 P 记录不存在",
                    action="返回视频详情页重新选择分 P",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            actual_mode = (
                AccessMode.AUTHENTICATED
                if request.access_mode == AccessMode.AUTHENTICATED
                else AccessMode.ANONYMOUS
            )
            required_context = (
                AccessContext.AUTHENTICATED
                if actual_mode == AccessMode.AUTHENTICATED
                else AccessContext.ANONYMOUS
            )
            video_stream = self._selected_stream(
                part.streams,
                request.video_stream_id,
                kind=StreamKind.VIDEO,
                context=required_context,
            )
            audio_stream = self._select_audio(
                part.streams,
                request.audio_stream_id,
                context=required_context,
            )

            selected_streams = [item for item in (video_stream, audio_stream) if item is not None]
            if not selected_streams:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    "当前分 P 没有符合所选方案的媒体流",
                    action="重新解析并选择可用的视频或音频规格",
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
            self._validate_output_compatibility(
                request,
                video_stream=video_stream,
                audio_stream=audio_stream,
            )
            expected_size = self._expected_total(selected_streams)
            quality = (
                video_stream.quality_label
                if video_stream is not None
                else audio_stream.quality_label
                if audio_stream is not None
                else "audio"
            )
            filename_template = request.filename or self.default_filename_template
            output_filename = render_filename_template(
                filename_template,
                {
                    "title": part.video.title,
                    "bvid": part.video.bvid,
                    "page": part.page_number,
                    "part": part.title,
                    "quality": quality,
                },
                extension=request.container.value,
            )
            prepared: dict[str, object] = {
                "video_id": request.video_id,
                "part_id": request.part_id,
                "video_stream_id": video_stream.id if video_stream else None,
                "audio_stream_id": audio_stream.id if audio_stream else None,
                "container": request.container.value,
                "processing_mode": request.processing_mode.value,
                "access_mode": actual_mode.value,
                "output_filename": output_filename,
                "include_subtitle": request.include_subtitle,
                "include_cover": request.include_cover,
                "include_metadata": request.include_metadata,
                "include_danmaku": request.include_danmaku,
                "cleanup_temporary": request.cleanup_temporary,
                "expected_size": expected_size,
                "video_expected_size": (
                    video_stream.estimated_size if video_stream is not None else None
                ),
                "video_codec": video_stream.codec if video_stream is not None else None,
                "video_width": video_stream.width if video_stream is not None else None,
                "video_height": video_stream.height if video_stream is not None else None,
                "audio_expected_size": (
                    audio_stream.estimated_size if audio_stream is not None else None
                ),
                "audio_codec": audio_stream.codec if audio_stream is not None else None,
                "audio_sample_rate": (
                    audio_stream.sample_rate if audio_stream is not None else None
                ),
                "audio_channels": (
                    audio_stream.audio_channels if audio_stream is not None else None
                ),
                "expected_duration": part.duration,
                "video_title": part.video.title,
                "part_title": part.title,
                "bvid": part.video.bvid,
                "page_number": part.page_number,
                "quality_label": quality,
            }
        self.preflight_disk(expected_size)
        self.processor.check_available()
        return prepared

    async def execute(
        self,
        job: Job,
        *,
        checkpoint: DownloadCheckpoint,
        reporter: DownloadExecutionReporter,
    ) -> list[Artifact]:
        payload = cast(dict[str, object], job.input_json)
        expected_size = self._optional_int(payload.get("expected_size"))
        expected_duration = float(self._required_int(payload, "expected_duration"))
        self.preflight_disk(expected_size)
        self.processor.check_available()
        job_temp = safe_child_path(self.temp_root, job.id)
        job_output = safe_child_path(self.artifact_root, job.id)
        await asyncio.to_thread(job_temp.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(job_output.mkdir, parents=True, exist_ok=True)
        container = str(payload["container"])
        primary_type = self._primary_artifact_type(container)
        existing = await self.artifact_service.existing_all_for_job(job.id)
        existing_primary = next(
            (item for item in existing if item.type in {primary_type, "media"}),
            None,
        )
        if existing_primary is not None:
            artifacts = await self._ensure_companion_artifacts(
                job=job,
                payload=payload,
                media_info=cast(dict[str, object], existing_primary.media_info or {}),
                output_directory=job_output,
                checkpoint=checkpoint,
                reporter=reporter,
                existing=existing,
            )
            if payload.get("cleanup_temporary") is not False:
                await asyncio.to_thread(shutil.rmtree, job_temp, ignore_errors=True)
            await reporter.update(phase="completed", progress=100.0)
            return artifacts

        video_path = (
            safe_child_path(job_temp, "video.media.part")
            if payload.get("video_stream_id") is not None
            else None
        )
        audio_path = (
            safe_child_path(job_temp, "audio.media.part")
            if payload.get("audio_stream_id") is not None
            else None
        )
        output_filename = str(payload["output_filename"])
        staging = safe_child_path(job_output, f".{job.id}.partial.{container}")
        final_path = safe_child_path(job_output, output_filename)
        bytes_by_kind: dict[str, int] = {"video": 0, "audio": 0}
        totals_by_kind: dict[str, int | None] = {"video": None, "audio": None}
        primary_published = False

        try:
            if video_path is not None:
                await self._download_stream(
                    stream_id=str(payload["video_stream_id"]),
                    kind="video",
                    destination=video_path,
                    payload=payload,
                    checkpoint=checkpoint,
                    reporter=reporter,
                    bytes_by_kind=bytes_by_kind,
                    totals_by_kind=totals_by_kind,
                )
            if audio_path is not None:
                await self._download_stream(
                    stream_id=str(payload["audio_stream_id"]),
                    kind="audio",
                    destination=audio_path,
                    payload=payload,
                    checkpoint=checkpoint,
                    reporter=reporter,
                    bytes_by_kind=bytes_by_kind,
                    totals_by_kind=totals_by_kind,
                )

            await checkpoint.checkpoint()
            await reporter.update(phase="post_processing", progress=84.0)

            async def process_progress(value: float) -> None:
                await checkpoint.checkpoint()
                self._check_runtime_storage(staging, enforce_quota=True)
                await reporter.update(
                    phase="post_processing",
                    progress=84.0 + value * 13.0,
                    downloaded_bytes=sum(bytes_by_kind.values()),
                    total_bytes=self._combined_total(totals_by_kind, expected_size),
                )

            probe = await self.processor.process(
                video_path=video_path,
                audio_path=audio_path,
                output_path=staging,
                container=container,
                processing_mode=str(payload["processing_mode"]),
                expected_duration=expected_duration,
                checkpoint=checkpoint,
                progress=process_progress,
            )
            await checkpoint.checkpoint()
            media_record = await self.artifact_service.publish(
                job_id=job.id,
                artifact_type=primary_type,
                staging_path=staging,
                final_path=final_path,
                filename=output_filename,
                mime_type=self._mime_type(container),
                media_info=probe.as_dict(),
            )
            primary_published = True
            artifacts = await self._ensure_companion_artifacts(
                job=job,
                payload=payload,
                media_info=probe.as_dict(),
                output_directory=job_output,
                checkpoint=checkpoint,
                reporter=reporter,
                existing=[media_record],
            )
            if payload.get("cleanup_temporary") is not False:
                await asyncio.to_thread(shutil.rmtree, job_temp, ignore_errors=True)
            await reporter.update(phase="completed", progress=100.0)
            return artifacts
        except DownloadCanceled:
            await asyncio.to_thread(staging.unlink, missing_ok=True)
            if not primary_published:
                await asyncio.to_thread(final_path.unlink, missing_ok=True)
            await asyncio.to_thread(shutil.rmtree, job_temp, ignore_errors=True)
            raise
        except DownloadPaused:
            await asyncio.to_thread(staging.unlink, missing_ok=True)
            raise
        except asyncio.CancelledError:
            await asyncio.to_thread(staging.unlink, missing_ok=True)
            raise
        except (MediaDownloadError, FFmpegError, OSError, ValueError):
            await asyncio.to_thread(staging.unlink, missing_ok=True)
            raise

    async def discard_job_partials(self, job_id: str) -> None:
        job_temp = safe_child_path(self.temp_root, job_id)
        job_output = safe_child_path(self.artifact_root, job_id)
        await asyncio.to_thread(shutil.rmtree, job_temp, ignore_errors=True)
        await asyncio.to_thread(self._remove_unpublished_outputs, job_output)

    def preflight_disk(self, expected_size: int | None) -> None:
        if expected_size is not None and expected_size > self.runtime.maximum_stream_bytes * 2:
            raise self._storage_error("所选媒体超过允许的单任务大小")
        temporary_reserve = (
            expected_size + 16 * 1024 * 1024
            if expected_size is not None
            else self.runtime.unknown_size_reserve_bytes
        )
        artifact_reserve = (
            expected_size * 2 + 16 * 1024 * 1024
            if expected_size is not None
            else self.runtime.unknown_size_reserve_bytes
        )
        artifact_usage = shutil.disk_usage(self.artifact_root)
        if self._same_volume(self.temp_root, self.artifact_root):
            required = temporary_reserve + artifact_reserve + self.runtime.min_free_bytes
            if artifact_usage.free < required:
                raise self._storage_error("可用磁盘空间不足，任务已停止")
        else:
            temporary_usage = shutil.disk_usage(self.temp_root)
            if temporary_usage.free < temporary_reserve + self.runtime.min_free_bytes:
                raise self._storage_error("临时目录所在磁盘空间不足，任务已停止")
            if artifact_usage.free < artifact_reserve + self.runtime.min_free_bytes:
                raise self._storage_error("产物目录所在磁盘空间不足，任务已停止")
        if self.runtime.artifact_quota_bytes is not None:
            used = self._directory_size(self.artifact_root)
            if used + artifact_reserve > self.runtime.artifact_quota_bytes:
                raise self._storage_error("产物存储配额不足，任务已停止")

    def _check_runtime_storage(self, path: Path, *, enforce_quota: bool) -> None:
        if shutil.disk_usage(path.parent).free < self.runtime.min_free_bytes:
            raise self._storage_error("任务执行期间可用磁盘空间不足")
        if enforce_quota and self.runtime.artifact_quota_bytes is not None:
            if self._directory_size(self.artifact_root) > self.runtime.artifact_quota_bytes:
                raise self._storage_error("任务执行期间产物存储配额已耗尽")

    @staticmethod
    def _same_volume(left: Path, right: Path) -> bool:
        try:
            left_device = left.stat().st_dev
            right_device = right.stat().st_dev
        except OSError:
            return left.anchor.casefold() == right.anchor.casefold()
        if left_device or right_device:
            return left_device == right_device
        return left.anchor.casefold() == right.anchor.casefold()

    @staticmethod
    def _directory_size(root: Path) -> int:
        total = 0
        for path in root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

    @staticmethod
    def _storage_error(message: str) -> AppError:
        return AppError(
            ErrorCode.INTERNAL_ERROR,
            message,
            action="清理产物或更改存储目录后重试",
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
        )

    async def _download_stream(
        self,
        *,
        stream_id: str,
        kind: str,
        destination: Path,
        payload: Mapping[str, object],
        checkpoint: DownloadCheckpoint,
        reporter: DownloadExecutionReporter,
        bytes_by_kind: dict[str, int],
        totals_by_kind: dict[str, int | None],
    ) -> None:
        mode = AccessMode(str(payload["access_mode"]))
        expected_track_size = self._optional_int(payload.get(f"{kind}_expected_size"))
        expected_duration = float(self._required_int(payload, "expected_duration"))
        if await self._reuse_completed_track(
            destination=destination,
            stream_id=stream_id,
            kind=kind,
            expected_size=expected_track_size,
            expected_duration=expected_duration,
            payload=payload,
        ):
            size = await asyncio.to_thread(self._required_regular_size, destination)
            bytes_by_kind[kind] = size
            totals_by_kind[kind] = size
            await reporter.update(
                phase=f"reusing_{kind}",
                progress=self._download_progress(bytes_by_kind, totals_by_kind),
                downloaded_bytes=sum(bytes_by_kind.values()),
                total_bytes=self._combined_total(
                    totals_by_kind,
                    self._optional_int(payload.get("expected_size")),
                ),
            )
            return

        last_error: Exception | None = None
        for attempt in range(self.runtime.retries + 1):
            await checkpoint.checkpoint()
            await reporter.update(
                phase=f"refreshing_{kind}",
                progress=self._download_progress(bytes_by_kind, totals_by_kind),
                automatic_attempt=attempt,
            )
            try:
                await self.video_service.get_part_streams(
                    str(payload["video_id"]),
                    str(payload["part_id"]),
                    mode,
                    force_refresh=True,
                )
                resolved = await self.video_service.resolve_stream(stream_id, mode, verify=False)
                current_attempt = attempt
                last_storage_check = 0

                async def on_progress(
                    update: DownloadProgress,
                    automatic_attempt: int = current_attempt,
                ) -> None:
                    nonlocal last_storage_check
                    if update.downloaded_bytes - last_storage_check >= 64 * 1024 * 1024:
                        self._check_runtime_storage(destination, enforce_quota=False)
                        last_storage_check = update.downloaded_bytes
                    bytes_by_kind[kind] = update.downloaded_bytes
                    totals_by_kind[kind] = update.total_bytes
                    await reporter.update(
                        phase=f"downloading_{kind}",
                        progress=self._download_progress(bytes_by_kind, totals_by_kind),
                        downloaded_bytes=sum(bytes_by_kind.values()),
                        total_bytes=self._combined_total(
                            totals_by_kind,
                            self._optional_int(payload.get("expected_size")),
                        ),
                        automatic_attempt=automatic_attempt,
                    )

                result = await self._download_candidates(
                    (resolved.url, *resolved.backup_urls),
                    destination=destination,
                    checkpoint=checkpoint,
                    progress=on_progress,
                )
                bytes_by_kind[kind] = result.size
                totals_by_kind[kind] = result.expected_size or result.size
                probe = await self.processor.validate_input(destination, expected_kind=kind)
                self._validate_track_duration(probe.duration, expected_duration)
                self._validate_track_specification(probe, kind=kind, payload=payload)
                await self._record_stream_verification(stream_id, kind=kind, probe=probe)
                await asyncio.to_thread(
                    self._write_track_marker,
                    destination,
                    stream_id,
                    kind,
                    result.size,
                )
                return
            except (DownloadCanceled, DownloadPaused, asyncio.CancelledError):
                raise
            except MediaValidationError as exc:
                last_error = exc
                self.downloader.discard_partial(destination)
                self._track_marker_path(destination).unlink(missing_ok=True)
            except MediaDownloadError as exc:
                last_error = exc
                if exc.code in {"MEDIA_CHANGED", "RANGE_MISMATCH"}:
                    self.downloader.discard_partial(destination)
                    self._track_marker_path(destination).unlink(missing_ok=True)
                if not exc.retryable:
                    raise
            except AppError as exc:
                last_error = exc
                if exc.code not in {ErrorCode.UPSTREAM_NETWORK, ErrorCode.UPSTREAM_CHANGED}:
                    raise
                if attempt >= self.runtime.retries:
                    raise
            if attempt >= self.runtime.retries:
                break
            await asyncio.sleep(self.runtime.retry_base_delay_seconds * (2**attempt))
        if isinstance(last_error, MediaDownloadError):
            raise MediaDownloadError(
                "MEDIA_RETRY_EXHAUSTED",
                "媒体地址刷新或下载超过最大重试次数",
            ) from last_error
        if isinstance(last_error, FFmpegError):
            raise last_error
        if isinstance(last_error, AppError):
            raise last_error
        raise MediaDownloadError(
            "MEDIA_RETRY_EXHAUSTED",
            "媒体下载超过最大重试次数",
        ) from last_error

    async def _download_candidates(
        self,
        urls: tuple[str, ...],
        *,
        destination: Path,
        checkpoint: DownloadCheckpoint,
        progress: ProgressCallback,
    ) -> DownloadResult:
        candidates = tuple(dict.fromkeys(url for url in urls if url))
        if not candidates:
            raise MediaDownloadError(
                "MEDIA_URL_MISSING",
                "媒体地址刷新后没有可用下载地址",
                retryable=True,
            )
        last_error: MediaDownloadError | None = None
        for url in candidates:
            await checkpoint.checkpoint()
            try:
                probed_size = await self.downloader.probe(url)
                self.preflight_disk(probed_size)
                return await self.downloader.download(
                    url,
                    destination,
                    checkpoint=checkpoint,
                    progress=progress,
                )
            except MediaDownloadError as exc:
                last_error = exc
                if exc.code == "STORAGE_WRITE_FAILED":
                    raise
                if exc.code in {"MEDIA_CHANGED", "RANGE_MISMATCH"}:
                    self.downloader.discard_partial(destination)
        if last_error is not None:
            raise last_error
        raise MediaDownloadError(
            "MEDIA_URL_MISSING",
            "媒体地址刷新后没有可用下载地址",
            retryable=True,
        )

    async def _ensure_companion_artifacts(
        self,
        *,
        job: Job,
        payload: Mapping[str, object],
        media_info: dict[str, object],
        output_directory: Path,
        checkpoint: DownloadCheckpoint,
        reporter: DownloadExecutionReporter,
        existing: list[Artifact],
    ) -> list[Artifact]:
        artifacts = list(existing)
        existing_types = {item.type for item in artifacts}
        existing_subtitle_filenames = {
            item.filename for item in artifacts if item.type == "subtitle"
        }
        outcomes = self._existing_companion_outcomes(payload, artifacts)
        context: _ProviderContext | None = None
        needs_context: set[str] = set()
        if payload.get("include_cover") is True and "cover" not in existing_types:
            needs_context.add("cover")
        if payload.get("include_danmaku") is True and "danmaku" not in existing_types:
            needs_context.add("danmaku")
        if payload.get("include_subtitle") is True and outcomes.get("subtitle") not in {
            "completed",
            "not_available",
        }:
            needs_context.add("subtitle")
        if needs_context:
            try:
                context = await self._provider_context(payload)
            except (DownloadCanceled, DownloadPaused, asyncio.CancelledError):
                raise
            except Exception:
                for artifact_type in needs_context:
                    outcomes[artifact_type] = "failed"
                    self._log_companion_failure(job.id, artifact_type)

        if (
            payload.get("include_cover") is True
            and "cover" not in existing_types
            and context is not None
        ):
            try:
                cover = await self._create_cover_artifact(
                    job=job,
                    payload=payload,
                    context=context,
                    output_directory=output_directory,
                    checkpoint=checkpoint,
                    reporter=reporter,
                )
            except (DownloadCanceled, DownloadPaused, asyncio.CancelledError):
                raise
            except MediaDownloadError as exc:
                outcomes["cover"] = "not_available" if exc.code == "COVER_UNAVAILABLE" else "failed"
                self._log_companion_failure(job.id, "cover")
            except Exception:
                outcomes["cover"] = "failed"
                self._log_companion_failure(job.id, "cover")
            else:
                artifacts.append(cover)
                existing_types.add("cover")
                outcomes["cover"] = "completed"

        if "subtitle" in needs_context and context is not None:
            try:
                subtitle_plan = await self._subtitle_plan(
                    payload=payload,
                    context=context,
                    checkpoint=checkpoint,
                )
                expectations = self._companion_expectations(
                    cast(Mapping[str, object], job.input_json)
                )
                stored_expected = self._subtitle_expectation(
                    cast(Mapping[str, object], job.input_json)
                )
                expected_filenames = list(stored_expected or ())
                expected_filenames.extend(
                    item.filename
                    for item in subtitle_plan
                    if item.filename not in expected_filenames
                )
                if len(expected_filenames) > 100:
                    raise MediaDownloadError(
                        "SUBTITLE_LIMIT_EXCEEDED",
                        "字幕轨道数量超过安全上限",
                    )
                expectations["subtitle_filenames"] = expected_filenames
                await self._persist_companion_state(
                    job,
                    outcomes,
                    expectations=expectations,
                )
                subtitles = await self._create_subtitle_artifacts(
                    job=job,
                    plan=subtitle_plan,
                    output_directory=output_directory,
                    checkpoint=checkpoint,
                    reporter=reporter,
                    existing_filenames=existing_subtitle_filenames,
                )
            except (DownloadCanceled, DownloadPaused, asyncio.CancelledError):
                raise
            except Exception:
                outcomes["subtitle"] = "failed"
                self._log_companion_failure(job.id, "subtitle")
            else:
                artifacts.extend(subtitles)
                existing_subtitle_filenames.update(item.filename for item in subtitles)
                if not expected_filenames:
                    outcomes["subtitle"] = "not_available"
                elif set(expected_filenames).issubset(existing_subtitle_filenames):
                    existing_types.add("subtitle")
                    outcomes["subtitle"] = "completed"
                else:
                    outcomes["subtitle"] = "failed"
                    self._log_companion_failure(job.id, "subtitle")

        if (
            payload.get("include_danmaku") is True
            and "danmaku" not in existing_types
            and context is not None
        ):
            try:
                danmaku = await self._create_danmaku_artifact(
                    job=job,
                    payload=payload,
                    context=context,
                    output_directory=output_directory,
                    checkpoint=checkpoint,
                    reporter=reporter,
                )
            except (DownloadCanceled, DownloadPaused, asyncio.CancelledError):
                raise
            except Exception:
                outcomes["danmaku"] = "failed"
                self._log_companion_failure(job.id, "danmaku")
            else:
                artifacts.append(danmaku)
                existing_types.add("danmaku")
                outcomes["danmaku"] = "completed"

        if payload.get("include_metadata") is True and "metadata" not in existing_types:
            outcomes["metadata"] = "completed"
            try:
                metadata = await self._create_metadata_artifact(
                    job=job,
                    payload=payload,
                    media_info=media_info,
                    output_directory=output_directory,
                    companion_outcomes=outcomes,
                )
            except (DownloadCanceled, DownloadPaused, asyncio.CancelledError):
                raise
            except Exception:
                outcomes["metadata"] = "failed"
                self._log_companion_failure(job.id, "metadata")
            else:
                artifacts.append(metadata)
                existing_types.add("metadata")
        await self._persist_companion_state(job, outcomes)
        return sorted(artifacts, key=lambda item: item.created_at)

    @classmethod
    def _existing_companion_outcomes(
        cls, payload: Mapping[str, object], existing: list[Artifact]
    ) -> dict[str, str]:
        outcomes: dict[str, str] = {}
        raw = payload.get("companion_outcomes")
        if isinstance(raw, Mapping):
            for artifact_type, outcome in raw.items():
                if (
                    isinstance(artifact_type, str)
                    and artifact_type in {"cover", "subtitle", "danmaku", "metadata"}
                    and outcome in {"completed", "not_available", "failed"}
                ):
                    outcomes[artifact_type] = str(outcome)
        existing_types = {item.type for item in existing}
        for artifact_type in existing_types & {"cover", "danmaku", "metadata"}:
            if outcomes.get(artifact_type) != "failed":
                outcomes[artifact_type] = "completed"

        expected_subtitles = cls._subtitle_expectation(payload)
        existing_subtitles = {item.filename for item in existing if item.type == "subtitle"}
        if expected_subtitles is not None:
            if not expected_subtitles:
                outcomes["subtitle"] = "not_available"
            elif set(expected_subtitles).issubset(existing_subtitles):
                outcomes["subtitle"] = "completed"
            else:
                outcomes.pop("subtitle", None)
        elif outcomes.get("subtitle") == "completed":
            outcomes.pop("subtitle")
        return outcomes

    @classmethod
    def _companion_expectations(cls, payload: Mapping[str, object]) -> dict[str, object]:
        expected_subtitles = cls._subtitle_expectation(payload)
        if expected_subtitles is None:
            return {}
        return {"subtitle_filenames": list(expected_subtitles)}

    @staticmethod
    def _subtitle_expectation(payload: Mapping[str, object]) -> tuple[str, ...] | None:
        raw = payload.get("companion_expectations")
        if not isinstance(raw, Mapping):
            return None
        filenames = raw.get("subtitle_filenames")
        if not isinstance(filenames, list) or len(filenames) > 100:
            return None
        validated: list[str] = []
        for filename in filenames:
            if (
                not isinstance(filename, str)
                or not filename
                or len(filename) > 512
                or Path(filename).name != filename
                or any(ord(character) < 32 for character in filename)
            ):
                return None
            validated.append(filename)
        if len(validated) != len(set(validated)):
            return None
        return tuple(validated)

    async def _persist_companion_state(
        self,
        job: Job,
        outcomes: Mapping[str, str],
        *,
        expectations: Mapping[str, object] | None = None,
    ) -> None:
        updated_payload = dict(cast(Mapping[str, object], job.input_json))
        updated_payload["companion_outcomes"] = dict(sorted(outcomes.items()))
        if expectations is not None:
            updated_payload["companion_expectations"] = dict(expectations)
        async with self.session_factory() as session:
            record = await session.get(Job, job.id)
            if record is None:
                raise ValueError("Download job disappeared while saving companion outcomes")
            if record.type != JobType.DOWNLOAD:
                job.input_json = updated_payload
                return
            record.input_json = updated_payload
            await session.commit()
        job.input_json = updated_payload

    @staticmethod
    def _log_companion_failure(job_id: str, artifact_type: str) -> None:
        logger.warning(
            "Optional companion artifact could not be created",
            extra={
                "event": "download_companion_failed",
                "job_id": job_id,
                "artifact_type": artifact_type,
            },
        )

    async def _provider_context(self, payload: Mapping[str, object]) -> _ProviderContext:
        video_id = self._required_string(payload, "video_id")
        part_id = self._required_string(payload, "part_id")
        async with self.session_factory() as session:
            part = await session.scalar(
                select(VideoPart)
                .where(VideoPart.id == part_id, VideoPart.video_id == video_id)
                .options(selectinload(VideoPart.video).selectinload(Video.parts))
            )
            if part is None:
                raise AppError(
                    ErrorCode.RESOURCE_NOT_FOUND,
                    "视频分 P 记录不存在",
                    action="重新解析视频后重试",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            provider_video = self.video_service._provider_video(part.video)
            provider_part = self.video_service._provider_part(part)
            cover_url = part.video.cover_url
        mode = AccessMode(self._required_string(payload, "access_mode"))
        cookies = (
            await self.video_service.auth_service.cookie_jar()
            if mode == AccessMode.AUTHENTICATED
            else None
        )
        return _ProviderContext(
            video=provider_video,
            part=provider_part,
            cover_url=cover_url,
            cookies=cookies,
        )

    async def _create_cover_artifact(
        self,
        *,
        job: Job,
        payload: Mapping[str, object],
        context: _ProviderContext,
        output_directory: Path,
        checkpoint: DownloadCheckpoint,
        reporter: DownloadExecutionReporter,
    ) -> Artifact:
        if not context.cover_url:
            raise MediaDownloadError("COVER_UNAVAILABLE", "视频没有可保存的封面资源")
        staging = safe_child_path(output_directory, f".{job.id}.cover.partial")
        await asyncio.to_thread(self.resource_downloader.discard_partial, staging)
        try:
            await self._download_resource(
                context.cover_url,
                destination=staging,
                phase="downloading_cover",
                progress_start=97.0,
                progress_span=1.0,
                checkpoint=checkpoint,
                reporter=reporter,
            )
            extension, mime_type = await asyncio.to_thread(self._image_type, staging)
            filename = self._companion_filename(
                self._required_string(payload, "output_filename"),
                "cover",
                extension,
            )
            final_path = safe_child_path(output_directory, filename)
            return await self.artifact_service.publish(
                job_id=job.id,
                artifact_type="cover",
                staging_path=staging,
                final_path=final_path,
                filename=filename,
                mime_type=mime_type,
                media_info=None,
            )
        finally:
            await asyncio.to_thread(self.resource_downloader.discard_partial, staging)

    async def _subtitle_plan(
        self,
        *,
        payload: Mapping[str, object],
        context: _ProviderContext,
        checkpoint: DownloadCheckpoint,
    ) -> list[_SubtitlePlanItem]:
        await checkpoint.checkpoint()
        subtitles = await self.video_service.provider.get_subtitles(
            context.video,
            context.part,
            context.cookies,
        )
        await checkpoint.checkpoint()
        unique: dict[tuple[str, str], ProviderSubtitle] = {}
        for subtitle in subtitles:
            unique.setdefault((subtitle.language, subtitle.subtitle_id), subtitle)
        if len(unique) > 100:
            raise MediaDownloadError("SUBTITLE_LIMIT_EXCEEDED", "字幕轨道数量超过安全上限")

        output_filename = self._required_string(payload, "output_filename")
        used_filenames: set[str] = set()
        plan: list[_SubtitlePlanItem] = []
        ordered = sorted(
            unique.values(),
            key=lambda item: (item.language.casefold(), item.subtitle_id),
        )
        for subtitle in ordered:
            language = sanitize_filename(subtitle.language, fallback="und", max_length=32)
            subtitle_id = sanitize_filename(
                subtitle.subtitle_id,
                fallback="track",
                max_length=24,
            )
            label = f"{language}.{subtitle_id}.subtitle"
            filename = self._companion_filename(output_filename, label, "json")
            suffix = 2
            while filename in used_filenames:
                filename = self._companion_filename(
                    output_filename,
                    f"{language}.{subtitle_id}.{suffix}.subtitle",
                    "json",
                )
                suffix += 1
            used_filenames.add(filename)
            plan.append(_SubtitlePlanItem(subtitle=subtitle, filename=filename))
        return plan

    async def _create_subtitle_artifacts(
        self,
        *,
        job: Job,
        plan: list[_SubtitlePlanItem],
        output_directory: Path,
        checkpoint: DownloadCheckpoint,
        reporter: DownloadExecutionReporter,
        existing_filenames: set[str],
    ) -> list[Artifact]:
        created: list[Artifact] = []
        total = max(1, len(plan))
        for index, item in enumerate(plan):
            subtitle = item.subtitle
            filename = item.filename
            if filename in existing_filenames:
                continue
            staging = safe_child_path(
                output_directory,
                f".{job.id}.subtitle.{index}.partial.json",
            )
            await asyncio.to_thread(self.resource_downloader.discard_partial, staging)
            try:
                await self._download_resource(
                    subtitle.url,
                    destination=staging,
                    phase="downloading_subtitle",
                    progress_start=98.0 + index / total,
                    progress_span=1.0 / total,
                    checkpoint=checkpoint,
                    reporter=reporter,
                )
                await asyncio.to_thread(self._validate_subtitle, staging)
                final_path = safe_child_path(output_directory, filename)
                record = await self.artifact_service.publish(
                    job_id=job.id,
                    artifact_type="subtitle",
                    staging_path=staging,
                    final_path=final_path,
                    filename=filename,
                    mime_type="application/json",
                    media_info={
                        "language": subtitle.language,
                        "languageLabel": subtitle.language_label,
                        "source": "bilibili",
                        "subtitleId": subtitle.subtitle_id,
                    },
                )
            finally:
                await asyncio.to_thread(self.resource_downloader.discard_partial, staging)
            created.append(record)
            existing_filenames.add(filename)
        return created

    async def _create_danmaku_artifact(
        self,
        *,
        job: Job,
        payload: Mapping[str, object],
        context: _ProviderContext,
        output_directory: Path,
        checkpoint: DownloadCheckpoint,
        reporter: DownloadExecutionReporter,
    ) -> Artifact:
        staging = safe_child_path(output_directory, f".{job.id}.danmaku.partial.xml")
        await asyncio.to_thread(staging.unlink, missing_ok=True)
        try:
            await checkpoint.checkpoint()
            await reporter.update(phase="downloading_danmaku", progress=99.0)
            document = await self.video_service.provider.get_danmaku(
                context.video,
                context.part,
            )
            await checkpoint.checkpoint()
            self.preflight_disk(len(document))
            await asyncio.to_thread(staging.write_bytes, document)
            self._check_runtime_storage(staging, enforce_quota=True)
            await asyncio.to_thread(self._validate_danmaku, staging)
            filename = self._companion_filename(
                self._required_string(payload, "output_filename"),
                "danmaku",
                "xml",
            )
            final_path = safe_child_path(output_directory, filename)
            artifact = await self.artifact_service.publish(
                job_id=job.id,
                artifact_type="danmaku",
                staging_path=staging,
                final_path=final_path,
                filename=filename,
                mime_type="application/xml",
                media_info={"source": "bilibili", "cid": context.part.cid},
            )
            await reporter.update(phase="downloading_danmaku", progress=99.5)
            return artifact
        finally:
            await asyncio.to_thread(staging.unlink, missing_ok=True)

    async def _download_resource(
        self,
        url: str,
        *,
        destination: Path,
        phase: str,
        progress_start: float,
        progress_span: float,
        checkpoint: DownloadCheckpoint,
        reporter: DownloadExecutionReporter,
    ) -> DownloadResult:
        total = await self.resource_downloader.probe(url)
        self.preflight_disk(total)

        async def update(value: DownloadProgress) -> None:
            self._check_runtime_storage(destination, enforce_quota=True)
            fraction = (
                min(1.0, value.downloaded_bytes / value.total_bytes) if value.total_bytes else 0.0
            )
            await reporter.update(
                phase=phase,
                progress=progress_start + progress_span * fraction,
                downloaded_bytes=value.downloaded_bytes,
                total_bytes=value.total_bytes,
            )

        return await self.resource_downloader.download(
            url,
            destination,
            checkpoint=checkpoint,
            progress=update,
        )

    async def _create_metadata_artifact(
        self,
        *,
        job: Job,
        payload: Mapping[str, object],
        media_info: dict[str, object],
        output_directory: Path,
        companion_outcomes: Mapping[str, str],
    ) -> Artifact:
        filename = self._companion_filename(
            self._required_string(payload, "output_filename"),
            "metadata",
            "json",
        )
        final_path = safe_child_path(output_directory, filename)
        staging = safe_child_path(output_directory, f".{job.id}.metadata.partial.json")
        document = {
            "videoId": payload["video_id"],
            "partId": payload["part_id"],
            "videoTitle": payload["video_title"],
            "partTitle": payload["part_title"],
            "bvid": payload["bvid"],
            "pageNumber": payload["page_number"],
            "qualityLabel": payload["quality_label"],
            "videoStreamId": payload["video_stream_id"],
            "audioStreamId": payload["audio_stream_id"],
            "container": payload["container"],
            "processingMode": payload["processing_mode"],
            "accessMode": payload["access_mode"],
            "includedResources": {
                "subtitle": payload.get("include_subtitle") is True,
                "cover": payload.get("include_cover") is True,
                "metadata": True,
                "danmaku": payload.get("include_danmaku") is True,
            },
            "companionOutcomes": dict(sorted(companion_outcomes.items())),
            "generatedAt": datetime.now(UTC).isoformat(),
            "mediaInfo": media_info,
        }
        await asyncio.to_thread(staging.unlink, missing_ok=True)
        try:
            await asyncio.to_thread(
                staging.write_text,
                json.dumps(document, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            self._check_runtime_storage(staging, enforce_quota=True)
            return await self.artifact_service.publish(
                job_id=job.id,
                artifact_type="metadata",
                staging_path=staging,
                final_path=final_path,
                filename=filename,
                mime_type="application/json",
                media_info=None,
            )
        finally:
            await asyncio.to_thread(staging.unlink, missing_ok=True)

    async def _reuse_completed_track(
        self,
        *,
        destination: Path,
        stream_id: str,
        kind: str,
        expected_size: int | None,
        expected_duration: float,
        payload: Mapping[str, object],
    ) -> bool:
        marker = self._track_marker_path(destination)
        resume = destination.with_name(f"{destination.name}.resume.json")
        if await asyncio.to_thread(resume.exists):
            return False
        marker_matches = await asyncio.to_thread(
            self._track_marker_matches,
            marker,
            destination,
            stream_id,
            kind,
        )
        marker_exists = await asyncio.to_thread(marker.exists)
        if marker_exists and not marker_matches:
            await asyncio.to_thread(marker.unlink, missing_ok=True)
            return False
        size = await asyncio.to_thread(self._regular_file_size, destination)
        if size is None:
            await asyncio.to_thread(marker.unlink, missing_ok=True)
            return False
        if size <= 0:
            return False
        if expected_size is not None and size > max(
            expected_size * 4,
            expected_size + 64 * 1024 * 1024,
        ):
            await asyncio.to_thread(marker.unlink, missing_ok=True)
            return False
        try:
            probe = await self.processor.validate_input(destination, expected_kind=kind)
            self._validate_track_duration(probe.duration, expected_duration)
            self._validate_track_specification(probe, kind=kind, payload=payload)
            await self._record_stream_verification(stream_id, kind=kind, probe=probe)
        except FFmpegError:
            await asyncio.to_thread(marker.unlink, missing_ok=True)
            return False
        if not marker_matches:
            await asyncio.to_thread(
                self._write_track_marker,
                destination,
                stream_id,
                kind,
                size,
            )
        return True

    async def _record_stream_verification(
        self,
        stream_id: str,
        *,
        kind: str,
        probe: MediaProbe,
    ) -> None:
        stream = next((item for item in probe.streams if item.get("type") == kind), None)
        sample_rate = self._positive_probe_int(stream.get("sampleRate")) if stream else None
        channels = self._positive_probe_int(stream.get("channels")) if stream else None
        await self.video_service.record_stream_verification(
            stream_id,
            sample_rate=sample_rate if kind == "audio" else None,
            audio_channels=channels if kind == "audio" else None,
        )

    @staticmethod
    def _positive_probe_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        if isinstance(value, str) and value.isdecimal():
            parsed = int(value)
            return parsed if parsed > 0 else None
        return None

    @staticmethod
    def _track_marker_matches(
        marker: Path,
        destination: Path,
        stream_id: str,
        kind: str,
    ) -> bool:
        if not marker.is_file() or marker.is_symlink() or marker.stat().st_size > 4_096:
            return False
        if not destination.is_file() or destination.is_symlink():
            return False
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        return bool(
            isinstance(value, dict)
            and value.get("version") == 1
            and value.get("stream_id") == stream_id
            and value.get("kind") == kind
            and value.get("size") == destination.stat().st_size
        )

    @staticmethod
    def _write_track_marker(
        destination: Path,
        stream_id: str,
        kind: str,
        size: int,
    ) -> None:
        marker = DownloadExecutor._track_marker_path(destination)
        temporary = marker.with_name(f"{marker.name}.tmp")
        document = {
            "version": 1,
            "stream_id": stream_id,
            "kind": kind,
            "size": size,
        }
        with temporary.open("w", encoding="utf-8") as target:
            json.dump(document, target, ensure_ascii=True, separators=(",", ":"))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, marker)

    @staticmethod
    def _track_marker_path(destination: Path) -> Path:
        return destination.with_name(f"{destination.name}.complete.json")

    @staticmethod
    def _regular_file_size(path: Path) -> int | None:
        if not path.is_file() or path.is_symlink():
            return None
        return path.stat().st_size

    @staticmethod
    def _required_regular_size(path: Path) -> int:
        size = DownloadExecutor._regular_file_size(path)
        if size is None:
            raise OSError("completed media track is no longer a regular file")
        return size

    @staticmethod
    def _validate_track_duration(actual_duration: float, expected_duration: float) -> None:
        if expected_duration <= 0:
            return
        tolerance = max(2.0, min(10.0, expected_duration * 0.01))
        if abs(actual_duration - expected_duration) > tolerance:
            raise MediaValidationError(
                "MEDIA_DURATION_MISMATCH",
                "下载的媒体轨道时长与视频分 P 不一致",
            )

    @classmethod
    def _validate_track_specification(
        cls,
        probe: MediaProbe,
        *,
        kind: str,
        payload: Mapping[str, object],
    ) -> None:
        stream = next((item for item in probe.streams if item.get("type") == kind), None)
        if stream is None:
            raise MediaValidationError("MEDIA_STREAM_MISSING", "下载文件缺少所选媒体轨道")
        expected_codec = payload.get(f"{kind}_codec")
        actual_codec = stream.get("codec")
        if (
            isinstance(expected_codec, str)
            and isinstance(actual_codec, str)
            and not cls._codec_matches(expected_codec, actual_codec)
        ):
            raise MediaValidationError("MEDIA_CODEC_MISMATCH", "下载媒体的编码与所选规格不一致")
        numeric_fields = (
            (("video_width", "width"), ("video_height", "height"))
            if kind == "video"
            else (("audio_sample_rate", "sampleRate"), ("audio_channels", "channels"))
        )
        for payload_key, probe_key in numeric_fields:
            expected = payload.get(payload_key)
            if isinstance(expected, int) and expected > 0 and stream.get(probe_key) != expected:
                raise MediaValidationError(
                    "MEDIA_SPECIFICATION_MISMATCH",
                    "下载媒体的技术参数与所选规格不一致",
                )

    @staticmethod
    def _codec_matches(expected: str, actual: str) -> bool:
        aliases = {
            "h.264/avc": {"h264", "avc", "avc1"},
            "h.265/hevc": {"hevc", "h265", "hev1", "hvc1"},
            "av1": {"av1", "av01"},
            "aac": {"aac", "mp4a"},
            "flac": {"flac"},
            "dolby e-ac-3": {"eac3", "ec-3"},
        }
        expected_value = expected.casefold()
        actual_value = actual.casefold()
        candidates = aliases.get(expected_value, {expected_value})
        return any(actual_value == item or actual_value.startswith(item) for item in candidates)

    @classmethod
    def _validate_output_compatibility(
        cls,
        request: DownloadRequest,
        *,
        video_stream: MediaStream | None,
        audio_stream: MediaStream | None,
    ) -> None:
        """Reject copy combinations that FFmpeg cannot safely mux.

        Request-schema validation covers the shape of an audio-only or video output. This
        second check is intentionally performed after selecting persisted stream records so
        clients cannot bypass codec/container rules with stale or handcrafted requests.
        """

        if request.processing_mode == ProcessingMode.TRANSCODE:
            return

        container = request.container
        video_family = cls._codec_family(video_stream.codec) if video_stream is not None else None
        audio_family = cls._codec_family(audio_stream.codec) if audio_stream is not None else None
        supported_video: dict[OutputContainer, set[str]] = {
            OutputContainer.MP4: {"h264", "hevc", "av1"},
            OutputContainer.MKV: {"h264", "hevc", "av1", "vp8", "vp9", "mpeg4"},
        }
        supported_audio: dict[OutputContainer, set[str]] = {
            OutputContainer.MP4: {"aac"},
            OutputContainer.M4A: {"aac"},
            OutputContainer.MKV: {
                "aac",
                "flac",
                "eac3",
                "ac3",
                "opus",
                "mp3",
                "vorbis",
            },
        }
        allowed_video = supported_video.get(container, set())
        if (
            video_stream is not None
            and video_family is not None
            and video_family not in allowed_video
        ):
            cls._raise_incompatible_copy(
                codec=video_stream.codec,
                container=container,
                kind="视频",
                allow_mkv=video_family in supported_video[OutputContainer.MKV],
            )
        allowed_audio = supported_audio.get(container, set())
        if (
            audio_stream is not None
            and audio_family is not None
            and audio_family not in allowed_audio
        ):
            cls._raise_incompatible_copy(
                codec=audio_stream.codec,
                container=container,
                kind="音频",
                allow_mkv=audio_family in supported_audio[OutputContainer.MKV],
            )

    @staticmethod
    def _codec_family(codec: str) -> str:
        value = codec.casefold().replace("_", "-").strip()
        aliases = (
            ("h264", ("h.264", "h264", "avc1", "avc")),
            ("hevc", ("h.265", "h265", "hevc", "hev1", "hvc1")),
            ("av1", ("av1", "av01")),
            ("aac", ("aac", "mp4a")),
            ("flac", ("flac",)),
            ("eac3", ("e-ac-3", "eac3", "ec-3", "dolby digital plus")),
            ("ac3", ("ac-3", "ac3", "dolby digital")),
            ("opus", ("opus",)),
            ("mp3", ("mp3", "mpeg layer 3")),
            ("vorbis", ("vorbis",)),
            ("vp9", ("vp9", "vp09")),
            ("vp8", ("vp8", "vp08")),
            ("mpeg4", ("mpeg-4 visual", "mpeg4")),
        )
        for family, candidates in aliases:
            if any(candidate in value for candidate in candidates):
                return family
        return value

    @staticmethod
    def _raise_incompatible_copy(
        *,
        codec: str,
        container: OutputContainer,
        kind: str,
        allow_mkv: bool,
    ) -> None:
        alternatives = "改用 MKV 无损封装，或选择“兼容转码”" if allow_mkv else "选择“兼容转码”"
        if container == OutputContainer.M4A:
            alternatives = "选择“兼容转码”，或改为 MP3/FLAC 转码输出"
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"所选 {codec} {kind}不能以无损封装模式安全写入 {container.value.upper()}",
            action=alternatives,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    @staticmethod
    def _image_type(path: Path) -> tuple[str, str]:
        size = path.stat().st_size
        if size <= 0 or size > 25 * 1024 * 1024 or path.is_symlink():
            raise MediaDownloadError("COVER_INVALID", "封面文件大小或文件类型无效")
        with path.open("rb") as source:
            header = source.read(32)
        if header.startswith(b"\xff\xd8\xff"):
            return "jpg", "image/jpeg"
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png", "image/png"
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return "webp", "image/webp"
        if len(header) >= 12 and header[4:8] == b"ftyp" and header[8:12] in {b"avif", b"avis"}:
            return "avif", "image/avif"
        raise MediaDownloadError("COVER_INVALID", "封面文件格式无法识别")

    @staticmethod
    def _validate_subtitle(path: Path) -> None:
        size = path.stat().st_size
        if size <= 0 or size > 16 * 1024 * 1024 or path.is_symlink():
            raise MediaDownloadError("SUBTITLE_INVALID", "字幕文件大小或文件类型无效")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MediaDownloadError("SUBTITLE_INVALID", "字幕文件格式无法识别") from exc
        if not isinstance(document, dict):
            raise MediaDownloadError("SUBTITLE_INVALID", "字幕文件格式无法识别")
        body = document.get("body")
        if not isinstance(body, list) or len(body) > 100_000:
            raise MediaDownloadError("SUBTITLE_INVALID", "字幕时间轴格式无效")
        for item in body:
            if not isinstance(item, dict):
                raise MediaDownloadError("SUBTITLE_INVALID", "字幕时间轴格式无效")
            content = item.get("content")
            start = item.get("from")
            end = item.get("to")
            if (
                not isinstance(content, str)
                or len(content) > 20_000
                or not isinstance(start, int | float)
                or isinstance(start, bool)
                or not isinstance(end, int | float)
                or isinstance(end, bool)
                or not math.isfinite(float(start))
                or not math.isfinite(float(end))
                or start < 0
                or end < start
            ):
                raise MediaDownloadError("SUBTITLE_INVALID", "字幕时间轴格式无效")

    @staticmethod
    def _validate_danmaku(path: Path) -> None:
        try:
            if path.is_symlink() or not path.is_file():
                raise DanmakuValidationError("Danmaku artifact is not a regular file")
            size = path.stat().st_size
            if not 1 <= size <= MAX_DANMAKU_BYTES:
                raise DanmakuValidationError("Danmaku artifact size is outside the safe range")
            with path.open("rb") as source:
                payload = source.read(MAX_DANMAKU_BYTES + 1)
            validate_danmaku_xml(payload)
        except (OSError, DanmakuValidationError) as exc:
            raise MediaDownloadError(
                "DANMAKU_INVALID",
                "弹幕文件格式或大小无效",
            ) from exc

    @staticmethod
    def _companion_filename(output_filename: str, label: str, extension: str) -> str:
        base = Path(sanitize_filename(output_filename)).stem
        safe_label = sanitize_filename(label, fallback="resource", max_length=48)
        return sanitize_filename(f"{base}.{safe_label}.{extension}")

    @staticmethod
    def _primary_artifact_type(container: str) -> str:
        return "audio" if container in {"m4a", "mp3", "flac"} else "video"

    @staticmethod
    def _expected_total(streams: list[MediaStream]) -> int | None:
        if not streams or any(item.estimated_size is None for item in streams):
            return None
        return sum(cast(int, item.estimated_size) for item in streams)

    @staticmethod
    def _required_string(payload: Mapping[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Persisted job field {key} is invalid")
        return value

    @staticmethod
    def _selected_stream(
        streams: list[MediaStream],
        stream_id: str | None,
        *,
        kind: StreamKind,
        context: AccessContext,
    ) -> MediaStream | None:
        if stream_id is None:
            return None
        stream = next((item for item in streams if item.id == stream_id), None)
        if stream is None or stream.kind != kind or stream.access_context != context:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "所选媒体流与视频分 P 或身份策略不一致",
                action="按当前身份重新解析并选择媒体规格",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        return stream

    @classmethod
    def _select_audio(
        cls,
        streams: list[MediaStream],
        requested: str,
        *,
        context: AccessContext,
    ) -> MediaStream | None:
        if requested == "none":
            return None
        candidates = [
            item
            for item in streams
            if item.kind == StreamKind.AUDIO and item.access_context == context
        ]
        if requested == "auto":
            if not candidates:
                return None
            return max(candidates, key=lambda item: item.bitrate or 0)
        return cls._selected_stream(
            streams,
            requested,
            kind=StreamKind.AUDIO,
            context=context,
        )

    @staticmethod
    def _download_progress(
        bytes_by_kind: Mapping[str, int], totals_by_kind: Mapping[str, int | None]
    ) -> float:
        downloaded = sum(bytes_by_kind.values())
        known_totals = [value for value in totals_by_kind.values() if value is not None]
        if downloaded <= 0 or not known_totals:
            return 4.0
        fraction = min(1.0, downloaded / max(sum(known_totals), downloaded))
        return 4.0 + fraction * 78.0

    @staticmethod
    def _combined_total(totals: Mapping[str, int | None], fallback: int | None) -> int | None:
        values = tuple(totals.values())
        if values and all(value is not None for value in values):
            total = sum(cast(int, value) for value in values)
            if total > 0:
                return total
        return fallback

    @staticmethod
    def _mime_type(container: str) -> str:
        explicit = {
            "mp4": "video/mp4",
            "mkv": "video/x-matroska",
            "m4a": "audio/mp4",
            "mp3": "audio/mpeg",
        }
        return explicit.get(
            container,
            mimetypes.types_map.get(f".{container}", "application/octet-stream"),
        )

    @staticmethod
    def _remove_unpublished_outputs(directory: Path) -> None:
        if not directory.is_dir():
            return
        for path in directory.iterdir():
            if (
                path.is_file()
                and path.name.startswith(".")
                and (".partial" in path.name or path.name.endswith(".resume.json"))
            ):
                path.unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            return

    @staticmethod
    def _required_int(payload: Mapping[str, object], key: str) -> int:
        value = payload.get(key)
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"Persisted job field {key} is invalid")
        return value

    @staticmethod
    def _optional_int(value: object) -> int | None:
        return value if isinstance(value, int) and value >= 0 else None
