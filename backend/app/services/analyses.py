from __future__ import annotations

import asyncio
import builtins
import hashlib
import json
import logging
import math
import os
import re
import shutil
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Protocol, cast

import httpx
from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from app.analysis import (
    AnalysisError,
    AnalysisErrorCode,
    AnalysisFailure,
    ContentInputSource,
    FasterWhisperConfig,
    LocalAnalysisEngine,
    MetadataSnapshot,
    PaddleOcrConfig,
    SubtitleDocument,
    SubtitleSegment,
    TranscriptSource,
    VisualEvidence,
    analysis_json_bytes,
    content_report_to_dict,
    export_subtitles,
    from_bilibili_subtitle_json,
    subtitle_document_to_dict,
    to_json_compatible,
)
from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.db.models import (
    Analysis,
    Artifact,
    Job,
    JobStatus,
    JobType,
    StreamKind,
    Video,
    VideoPart,
)
from app.media.download import (
    DownloadCanceled,
    DownloadCheckpoint,
    DownloadPaused,
    DownloadProgress,
    HTTPMediaDownloader,
    MediaDownloadError,
)
from app.media.security import MediaURLValidator, safe_child_path, sanitize_filename
from app.providers.models import ProviderPart, ProviderSubtitle, ProviderVideo, VideoProvider
from app.schemas.analyses import (
    AnalysisCapabilities,
    AnalysisCapabilityRead,
    AnalysisExportFormat,
    AnalysisFeature,
    AnalysisList,
    AnalysisRead,
    AnalysisRequest,
    AnalysisResultStatus,
    OcrResolution,
    TranscriptEditRequest,
)
from app.schemas.jobs import (
    DownloadRequest,
    JobRead,
    OutputContainer,
    ProcessingMode,
)
from app.schemas.settings import AnalysisDevice, AnalysisLanguage, AsrModel
from app.schemas.video import AccessMode, MediaStreamRead, StreamsRead
from app.services.artifacts import ArtifactService
from app.services.auth import AuthService
from app.services.downloads import DownloadExecutionReporter, DownloadExecutor
from app.services.jobs import JobService
from app.services.settings import SettingsService
from app.services.videos import VideoService

logger = logging.getLogger(__name__)

_MEDIA_FEATURES = {
    AnalysisFeature.MEDIA,
    AnalysisFeature.AUDIO,
    AnalysisFeature.ASR,
    AnalysisFeature.OCR,
    AnalysisFeature.SCENES,
}
_VIDEO_FEATURES = {
    AnalysisFeature.MEDIA,
    AnalysisFeature.OCR,
    AnalysisFeature.SCENES,
}
_EXECUTION_ORDER = {
    AnalysisFeature.BASIC: 0,
    AnalysisFeature.MEDIA: 1,
    AnalysisFeature.AUDIO: 2,
    AnalysisFeature.SUBTITLES: 3,
    AnalysisFeature.ASR: 4,
    AnalysisFeature.OCR: 5,
    AnalysisFeature.SCENES: 6,
    AnalysisFeature.SUMMARY: 7,
}
_TEXT_FEATURES = {
    AnalysisFeature.SUBTITLES,
    AnalysisFeature.ASR,
    AnalysisFeature.OCR,
}
_URL_PATTERN = re.compile(r"(?i)\b(?:https?|file)://[^\s<>\"']+")
_WINDOWS_PATH_PATTERN = re.compile(r"(?i)(?<![\w])(?:[a-z]:\\|\\\\)[^\r\n\t<>\"|?*]+")
_POSIX_PATH_PATTERN = re.compile(r"(?<![\w])/(?:[^/\s]+/)+[^\s,;:<>\"']*")
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(?:SESSDATA|bili_jct|DedeUserID|DedeUserID__ckMd5|authorization|cookie)"
    r"\s*[:=]\s*[^\s,;]+"
)
_SENSITIVE_KEYS = {
    "url",
    "urls",
    "path",
    "absolute_path",
    "cookie",
    "cookies",
    "authorization",
    "sessdata",
    "bili_jct",
    "dedeuserid",
}


@dataclass(frozen=True, slots=True)
class AnalysisEngineOptions:
    language: AnalysisLanguage
    asr_model: AsrModel
    device: AnalysisDevice
    ocr_resolution: OcrResolution
    sample_interval_seconds: float | None
    maximum_duration_seconds: int | None


class AnalysisEngineFactory(Protocol):
    def __call__(self, options: AnalysisEngineOptions) -> LocalAnalysisEngine: ...


@dataclass(frozen=True, slots=True)
class AcquiredMedia:
    artifact_id: str
    path: Path
    cleanup_required: bool
    work_job_id: str | None = None


class AnalysisMediaAcquirer(Protocol):
    async def acquire(
        self,
        *,
        parent_job: Job,
        video_id: str,
        part_id: str,
        features: Sequence[AnalysisFeature],
        access_mode: AccessMode,
        ocr_resolution: OcrResolution,
        checkpoint: DownloadCheckpoint,
        reporter: DownloadExecutionReporter,
    ) -> AcquiredMedia: ...

    async def cleanup(self, media: AcquiredMedia) -> None: ...


class PublicSubtitleFetcher(Protocol):
    async def fetch(
        self,
        *,
        video_id: str,
        part_id: str,
        language: AnalysisLanguage,
        access_mode: AccessMode,
        checkpoint: DownloadCheckpoint,
    ) -> SubtitleDocument | None: ...


class _AcquisitionReporter:
    def __init__(self, parent: DownloadExecutionReporter) -> None:
        self.parent = parent

    async def update(
        self,
        *,
        phase: str,
        progress: float,
        downloaded_bytes: int | None = None,
        total_bytes: int | None = None,
        automatic_attempt: int | None = None,
    ) -> None:
        await self.parent.update(
            phase=f"analysis_acquire_{phase}"[:64],
            progress=min(28.0, 2.0 + max(0.0, progress) * 0.26),
            downloaded_bytes=downloaded_bytes,
            total_bytes=total_bytes,
            automatic_attempt=automatic_attempt,
        )


class DownloadAnalysisMediaAcquirer:
    """Acquire bounded analysis media through the hardened download pipeline."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        video_service: VideoService,
        download_executor: DownloadExecutor,
        artifact_service: ArtifactService,
    ) -> None:
        self.session_factory = session_factory
        self.video_service = video_service
        self.download_executor = download_executor
        self.artifact_service = artifact_service

    async def acquire(
        self,
        *,
        parent_job: Job,
        video_id: str,
        part_id: str,
        features: Sequence[AnalysisFeature],
        access_mode: AccessMode,
        ocr_resolution: OcrResolution,
        checkpoint: DownloadCheckpoint,
        reporter: DownloadExecutionReporter,
    ) -> AcquiredMedia:
        recovered = await self._recover_source(
            parent_job.id,
            video_id=video_id,
            part_id=part_id,
            requires_video=any(feature in _VIDEO_FEATURES for feature in features),
        )
        if recovered is not None:
            return recovered
        await self.download_executor.discard_job_partials(parent_job.id)
        streams = await self.video_service.get_part_streams(
            video_id,
            part_id,
            access_mode,
            force_refresh=True,
        )
        request = self._analysis_download_request(
            video_id=video_id,
            part_id=part_id,
            streams=streams,
            features=features,
            access_mode=access_mode,
            ocr_resolution=ocr_resolution,
        )
        prepared = await self.download_executor.prepare(request)
        prepared["analysis_parent_job_id"] = parent_job.id
        download_view = Job(
            id=parent_job.id,
            type=JobType.DOWNLOAD,
            status=JobStatus.RUNNING,
            phase="analysis_media_acquisition",
            progress=0.0,
            input_json=prepared,
            retry_count=parent_job.retry_count,
            cancel_requested=False,
            started_at=parent_job.started_at or datetime.now(UTC),
        )
        try:
            artifacts = await self.download_executor.execute(
                download_view,
                checkpoint=checkpoint,
                reporter=_AcquisitionReporter(reporter),
            )
            primary = next(
                (artifact for artifact in artifacts if artifact.type in {"video", "audio"}),
                None,
            )
            if primary is None:
                raise AppError(
                    ErrorCode.INTERNAL_ERROR,
                    "分析媒体获取未生成可用媒体产物",
                    action="检查 FFmpeg 与存储状态后重试",
                )
            primary = await self._tag_source(
                primary.id,
                parent_job_id=parent_job.id,
                video_id=video_id,
                part_id=part_id,
            )
            delivery = await self.artifact_service.delivery(primary.id, None)
            return AcquiredMedia(
                artifact_id=primary.id,
                path=delivery.path,
                cleanup_required=True,
                work_job_id=parent_job.id,
            )
        except (DownloadCanceled, DownloadPaused, asyncio.CancelledError):
            await self.download_executor.discard_job_partials(parent_job.id)
            raise
        except Exception:
            await self.download_executor.discard_job_partials(parent_job.id)
            raise

    async def cleanup(self, media: AcquiredMedia) -> None:
        try:
            if media.cleanup_required:
                try:
                    await self.artifact_service.delete(media.artifact_id, delete_file=True)
                except AppError as exc:
                    if exc.code != ErrorCode.RESOURCE_NOT_FOUND:
                        raise
        finally:
            if media.work_job_id is not None:
                await self.download_executor.discard_job_partials(media.work_job_id)

    @staticmethod
    def _analysis_download_request(
        *,
        video_id: str,
        part_id: str,
        streams: StreamsRead,
        features: Sequence[AnalysisFeature],
        access_mode: AccessMode,
        ocr_resolution: OcrResolution,
    ) -> DownloadRequest:
        needs_video = any(feature in _VIDEO_FEATURES for feature in features)
        needs_audio = any(
            feature in {AnalysisFeature.MEDIA, AnalysisFeature.AUDIO, AnalysisFeature.ASR}
            for feature in features
        )
        video_stream = (
            DownloadAnalysisMediaAcquirer._select_analysis_video(
                streams.video,
                ocr_resolution=ocr_resolution,
                ocr_requested=AnalysisFeature.OCR in features,
            )
            if needs_video
            else None
        )
        audio_stream = (
            DownloadAnalysisMediaAcquirer._select_analysis_audio(streams.audio)
            if needs_audio
            else None
        )
        if video_stream is None and audio_stream is None:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "当前身份没有足够完成分析的媒体流",
                action="重新解析视频，或校验登录态后重试",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        audio_only_requires_transcode = (
            video_stream is None
            and audio_stream is not None
            and DownloadExecutor._codec_family(audio_stream.codec) != "aac"
        )
        return DownloadRequest(
            video_id=video_id,
            part_id=part_id,
            video_stream_id=video_stream.id if video_stream is not None else None,
            audio_stream_id=(audio_stream.id if audio_stream is not None else "none"),
            container=(OutputContainer.MKV if video_stream is not None else OutputContainer.M4A),
            processing_mode=(
                ProcessingMode.TRANSCODE if audio_only_requires_transcode else ProcessingMode.COPY
            ),
            access_mode=access_mode,
            filename="analysis-{bvid}-P{page}",
            include_subtitle=False,
            include_cover=False,
            include_metadata=False,
            cleanup_temporary=True,
            reuse_existing=False,
        )

    @staticmethod
    def _select_analysis_video(
        streams: Sequence[MediaStreamRead],
        *,
        ocr_resolution: OcrResolution,
        ocr_requested: bool,
    ) -> MediaStreamRead:
        candidates = [item for item in streams if item.kind == StreamKind.VIDEO]
        if not candidates:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "当前分 P 没有可用于画面分析的视频流",
                action="重新解析视频或取消 OCR/镜头分析",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        target_height = 480
        if ocr_requested:
            target_height = {
                OcrResolution.ECONOMY: 480,
                OcrResolution.BALANCED: 720,
                OcrResolution.DETAIL: 1080,
            }[ocr_resolution]
        known_heights: set[int] = {
            cast(int, item.height) for item in candidates if (item.height or 0) > 0
        }
        sufficient_heights = [height for height in known_heights if height >= target_height]
        selected_height = (
            min(sufficient_heights) if sufficient_heights else max(known_heights, default=0)
        )
        return min(
            (item for item in candidates if (item.height or 0) == selected_height),
            key=_stream_cost,
        )

    @staticmethod
    def _select_analysis_audio(
        streams: Sequence[MediaStreamRead],
    ) -> MediaStreamRead | None:
        candidates = [item for item in streams if item.kind == StreamKind.AUDIO]
        if not candidates:
            return None
        sufficient = [item for item in candidates if (item.bitrate or 0) >= 64_000]
        return min(sufficient or candidates, key=_stream_cost)

    async def _tag_source(
        self,
        artifact_id: str,
        *,
        parent_job_id: str,
        video_id: str,
        part_id: str,
    ) -> Artifact:
        async with self.session_factory() as session:
            artifact = await session.get(Artifact, artifact_id)
            if artifact is None:
                raise RuntimeError("Acquired analysis artifact disappeared")
            media_info = dict(artifact.media_info or {})
            media_info.update(
                {
                    "artifactRole": "analysis_source",
                    "analysisJobId": parent_job_id,
                    "videoId": video_id,
                    "partId": part_id,
                }
            )
            artifact.media_info = media_info
            await session.commit()
            await session.refresh(artifact)
            session.expunge(artifact)
            return artifact

    async def _recover_source(
        self,
        parent_job_id: str,
        *,
        video_id: str,
        part_id: str,
        requires_video: bool,
    ) -> AcquiredMedia | None:
        existing = await self.artifact_service.existing_all_for_job(
            parent_job_id,
            {"video", "audio", "media"},
        )
        matching: Artifact | None = None
        for artifact in existing:
            info = artifact.media_info if isinstance(artifact.media_info, dict) else {}
            matches_part = (
                info.get("artifactRole") == "analysis_source"
                and info.get("videoId") == video_id
                and info.get("partId") == part_id
            )
            matches_kind = not requires_video or (
                artifact.type in {"video", "media"}
                and artifact.mime_type.lower().startswith("video/")
            )
            if matching is None and matches_part and matches_kind:
                matching = artifact
                continue
            await self.artifact_service.delete(artifact.id, delete_file=True)
        if matching is None:
            return None
        delivery = await self.artifact_service.delivery(matching.id, None)
        return AcquiredMedia(
            artifact_id=matching.id,
            path=delivery.path,
            cleanup_required=True,
            work_job_id=parent_job_id,
        )


class ProviderSubtitleService:
    """Fetch public subtitle JSON with provider auth only on the metadata request."""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        provider: VideoProvider,
        auth_service: AuthService,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        validator: MediaURLValidator | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.provider = provider
        self.auth_service = auth_service
        self.validator = validator or MediaURLValidator(("hdslb.com", "bilibili.com"))
        self.maximum_bytes = min(settings.upstream_max_response_bytes, 5 * 1024 * 1024)
        self.temp_root = settings.temp_dir.expanduser().resolve()
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.downloader = HTTPMediaDownloader(
            self.validator,
            user_agent=settings.user_agent,
            timeout_seconds=settings.upstream_timeout_seconds,
            connect_timeout_seconds=settings.upstream_connect_timeout_seconds,
            chunk_size=16 * 1024,
            maximum_size_bytes=self.maximum_bytes,
            transport=transport,
        )

    async def reconfigure_temp_root(self, root: Path) -> None:
        candidate = await asyncio.to_thread(lambda: root.expanduser().resolve())
        await asyncio.to_thread(candidate.mkdir, parents=True, exist_ok=True)
        self.temp_root = candidate

    async def fetch(
        self,
        *,
        video_id: str,
        part_id: str,
        language: AnalysisLanguage,
        access_mode: AccessMode,
        checkpoint: DownloadCheckpoint,
    ) -> SubtitleDocument | None:
        video, part = await self._provider_context(video_id, part_id)
        cookies: CookieJar | None = None
        if access_mode == AccessMode.AUTHENTICATED:
            cookies = await self.auth_service.cookie_jar()
        subtitles = await self.provider.get_subtitles(video, part, cookies=cookies)
        selected = _select_provider_subtitle(subtitles, language)
        if selected is None:
            return None
        payload = await self._download_subtitle_json(selected.url, checkpoint)
        return from_bilibili_subtitle_json(
            payload,
            language=selected.language,
            generated_at=datetime.now(UTC),
        )

    async def _provider_context(
        self, video_id: str, part_id: str
    ) -> tuple[ProviderVideo, ProviderPart]:
        async with self.session_factory() as session:
            part_record = await session.scalar(
                select(VideoPart)
                .where(VideoPart.id == part_id, VideoPart.video_id == video_id)
                .options(joinedload(VideoPart.video).selectinload(Video.parts))
            )
            if part_record is None:
                raise AppError(
                    ErrorCode.RESOURCE_NOT_FOUND,
                    "视频或分 P 记录不存在",
                    action="重新解析视频后再获取公开字幕",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            video_record = part_record.video
            provider_parts = [
                ProviderPart(
                    cid=item.cid,
                    page_number=item.page_number,
                    title=item.title,
                    duration=item.duration,
                )
                for item in video_record.parts
            ]
        provider_video = ProviderVideo(
            provider=video_record.provider,
            bvid=video_record.bvid,
            aid=video_record.aid,
            title=video_record.title,
            description=video_record.description,
            cover_url=video_record.cover_url,
            owner_name=video_record.owner_name,
            duration=video_record.duration,
            published_at=video_record.published_at,
            stats=video_record.stats,
            tags=video_record.tags,
            rights=cast(dict[str, bool | int | str | None], video_record.rights),
            parts=provider_parts,
            raw_metadata=video_record.raw_metadata,
        )
        provider_part = next(item for item in provider_parts if item.cid == part_record.cid)
        return provider_video, provider_part

    async def _download_subtitle_json(
        self, url: str, checkpoint: DownloadCheckpoint
    ) -> dict[str, Any]:
        destination = safe_child_path(
            self.temp_root,
            f"subtitle-{uuid.uuid4().hex}.json",
        )

        async def ignore_progress(_: DownloadProgress) -> None:
            return None

        try:
            await self.downloader.download(
                url,
                destination,
                checkpoint=checkpoint,
                progress=ignore_progress,
            )
            buffer = await asyncio.to_thread(destination.read_bytes)
        except MediaDownloadError as exc:
            raise AppError(
                ErrorCode.UPSTREAM_NETWORK,
                "公开字幕暂时无法下载",
                action="稍后重试，或改用 ASR/OCR",
                status_code=status.HTTP_502_BAD_GATEWAY,
            ) from exc
        finally:
            self.downloader.discard_partial(destination)
        try:
            payload = json.loads(buffer)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError(
                ErrorCode.UPSTREAM_CHANGED,
                "公开字幕内容格式无法识别",
                action="重新获取字幕，或改用 ASR/OCR",
                status_code=status.HTTP_502_BAD_GATEWAY,
            ) from exc
        if not isinstance(payload, dict) or _json_depth(payload) > 30:
            raise AppError(
                ErrorCode.UPSTREAM_CHANGED,
                "公开字幕内容结构超过安全限制",
                action="改用 ASR/OCR，或稍后重试",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )
        return cast(dict[str, Any], payload)


@dataclass(frozen=True, slots=True)
class _ExecutionInput:
    video_id: str
    part_ids: tuple[str, ...]
    features: tuple[AnalysisFeature, ...]
    source_artifact_ids: dict[str, str]
    language: AnalysisLanguage
    access_mode: AccessMode
    asr_model: AsrModel
    device: AnalysisDevice
    ocr_resolution: OcrResolution
    sample_interval_seconds: float | None
    export_formats: tuple[AnalysisExportFormat, ...]
    maximum_duration_seconds: int | None
    scene_threshold: float
    maximum_keyframes: int

    @property
    def engine_options(self) -> AnalysisEngineOptions:
        return AnalysisEngineOptions(
            language=self.language,
            asr_model=self.asr_model,
            device=self.device,
            ocr_resolution=self.ocr_resolution,
            sample_interval_seconds=self.sample_interval_seconds,
            maximum_duration_seconds=self.maximum_duration_seconds,
        )


@dataclass(frozen=True, slots=True)
class _StepProduct:
    report: dict[str, object]
    artifacts: tuple[Artifact, ...]
    documents: tuple[SubtitleDocument, ...]
    model_name: str | None
    model_version: str | None


@dataclass(frozen=True, slots=True)
class _SummaryContext:
    documents: tuple[SubtitleDocument, ...]
    metadata_snapshots: tuple[MetadataSnapshot, ...]
    visual_evidence: tuple[VisualEvidence, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _StepOutcome:
    feature: AnalysisFeature
    part_id: str
    status: AnalysisResultStatus
    analysis_id: str
    artifact_ids: tuple[str, ...]
    error: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "feature": self.feature.value,
            "partId": self.part_id,
            "status": self.status.value,
            "analysisId": self.analysis_id,
            "artifactIds": list(self.artifact_ids),
        }
        if self.error is not None:
            value["error"] = self.error
        return value


class AnalysisService:
    """Create and execute isolated local-analysis steps over verified artifacts."""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        artifact_service: ArtifactService,
        job_service: JobService,
        *,
        settings_service: SettingsService | None = None,
        media_acquirer: AnalysisMediaAcquirer | None = None,
        subtitle_fetcher: PublicSubtitleFetcher | None = None,
        engine_factory: AnalysisEngineFactory | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.artifact_service = artifact_service
        self.job_service = job_service
        self.settings_service = settings_service
        self.media_acquirer = media_acquirer
        self.subtitle_fetcher = subtitle_fetcher
        self._owned_media: dict[str, list[AcquiredMedia]] = {}
        self._active_job_ids: set[str] = set()
        self._configuration_lock = asyncio.Lock()
        self._edit_lock = asyncio.Lock()
        self.artifact_root = settings.artifact_dir.expanduser().resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.engine_factory = engine_factory or _default_engine_factory

    async def create(self, request: AnalysisRequest) -> JobRead:
        video_title, part_titles, official_source = await self._validate_video_parts(
            request.video_id, request.part_ids
        )
        features = tuple(
            sorted(request.canonical_features, key=lambda feature: _EXECUTION_ORDER[feature])
        )
        engine_options = await self._effective_engine_options(request, features)
        source_artifact_ids: dict[str, str] = {}
        if any(feature in _MEDIA_FEATURES for feature in features):
            requires_video = any(feature in _VIDEO_FEATURES for feature in features)
            if request.artifact_id is not None:
                part_id = request.part_ids[0]
                await self._verified_source_artifact(
                    request.artifact_id,
                    video_id=request.video_id,
                    part_id=part_id,
                    requires_video=requires_video,
                )
                source_artifact_ids[part_id] = request.artifact_id
            else:
                for part_id in request.part_ids:
                    try:
                        source_artifact_ids[part_id] = await self._latest_source_artifact(
                            video_id=request.video_id,
                            part_id=part_id,
                            requires_video=requires_video,
                        )
                    except AppError as exc:
                        if (
                            self.media_acquirer is None
                            or exc.status_code != status.HTTP_409_CONFLICT
                        ):
                            raise

        payload: dict[str, object] = {
            "video_id": request.video_id,
            "video_title": video_title,
            "part_ids": list(request.part_ids),
            "part_titles": part_titles,
            "part_id": request.part_ids[0] if len(request.part_ids) == 1 else None,
            "part_title": (
                part_titles[request.part_ids[0]] if len(request.part_ids) == 1 else None
            ),
            "features": [feature.value for feature in features],
            "source_artifact_ids": source_artifact_ids,
            "language": engine_options.language.value,
            "access_mode": request.access_mode.value,
            "asr_model": engine_options.asr_model.value,
            "device": engine_options.device.value,
            "ocr_resolution": request.ocr_resolution.value,
            "sample_interval_seconds": engine_options.sample_interval_seconds,
            "export_formats": [value.value for value in request.export_formats],
            "maximum_duration_seconds": engine_options.maximum_duration_seconds,
            "scene_threshold": request.scene_threshold,
            "maximum_keyframes": request.maximum_keyframes,
            "official_source": official_source,
        }
        return await self.job_service.create_analysis(
            payload,
            reuse_existing=request.reuse_existing,
        )

    async def capabilities(self) -> AnalysisCapabilities:
        options, ocr_enabled = await self._configured_engine_options()
        engine = self.engine_factory(options)
        detected = await asyncio.to_thread(engine.capabilities)
        items = [
            AnalysisCapabilityRead(
                feature=AnalysisFeature.BASIC,
                component="structured-metadata",
                available=True,
                version="1.0.0",
                reason_code=None,
                message="本地结构化基础分析可用",
                action=None,
            ),
            AnalysisCapabilityRead(
                feature=AnalysisFeature.SUBTITLES,
                component="public-subtitle-artifact",
                available=True,
                version="1.0.0",
                reason_code=None,
                message="已有公开字幕可被复用和导出",
                action="若当前视频没有公开字幕，可改用 ASR 或 OCR",
            ),
        ]
        feature_by_component = {
            "ffprobe": AnalysisFeature.MEDIA,
            "ffmpeg-audio": AnalysisFeature.AUDIO,
            "ffmpeg-scenes": AnalysisFeature.SCENES,
            "faster-whisper": AnalysisFeature.ASR,
            "paddleocr": AnalysisFeature.OCR,
            "local-content-summary": AnalysisFeature.SUMMARY,
        }
        for capability in detected:
            feature = feature_by_component.get(capability.component)
            if feature is None:
                continue
            if feature == AnalysisFeature.OCR and not ocr_enabled:
                items.append(
                    AnalysisCapabilityRead(
                        feature=feature,
                        component=capability.component,
                        available=False,
                        version=capability.version,
                        reason_code="OCR_DISABLED",
                        message="OCR 已在应用设置中关闭",
                        action="在设置页启用 OCR 后重新创建分析任务",
                    )
                )
                continue
            items.append(
                AnalysisCapabilityRead(
                    feature=feature,
                    component=capability.component,
                    available=capability.available,
                    version=capability.version,
                    reason_code=capability.reason_code,
                    message=capability.message,
                    action=capability.action,
                )
            )
        return AnalysisCapabilities(items=items)

    async def _effective_engine_options(
        self,
        request: AnalysisRequest,
        features: Sequence[AnalysisFeature],
    ) -> AnalysisEngineOptions:
        if self.settings_service is None:
            return AnalysisEngineOptions(
                language=request.language,
                asr_model=request.asr_model,
                device=AnalysisDevice.AUTO,
                ocr_resolution=request.ocr_resolution,
                sample_interval_seconds=None,
                maximum_duration_seconds=request.maximum_duration_seconds,
            )
        application_settings = await self.settings_service.get()
        configured = application_settings.analysis
        if AnalysisFeature.OCR in features and not configured.ocr_enabled:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "OCR 已在应用设置中关闭",
                action="在设置页启用 OCR，或取消 OCR 分析选项",
                status_code=status.HTTP_409_CONFLICT,
            )
        requested_maximum = request.maximum_duration_seconds
        configured_maximum = configured.maximum_duration_seconds
        if "maximum_duration_seconds" in request.model_fields_set:
            exceeds_configured_limit = configured_maximum is not None and (
                requested_maximum is None or requested_maximum > configured_maximum
            )
            if exceeds_configured_limit:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    "请求的最长分析时长超过应用设置上限",
                    action="降低最长分析时长，或先在设置页调整上限",
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
            maximum_duration = requested_maximum
        else:
            maximum_duration = configured_maximum
        return AnalysisEngineOptions(
            language=(
                request.language if "language" in request.model_fields_set else configured.language
            ),
            asr_model=(
                request.asr_model
                if "asr_model" in request.model_fields_set
                else configured.asr_model
            ),
            device=configured.device,
            ocr_resolution=request.ocr_resolution,
            sample_interval_seconds=configured.sample_interval_seconds,
            maximum_duration_seconds=maximum_duration,
        )

    async def _configured_engine_options(
        self,
    ) -> tuple[AnalysisEngineOptions, bool]:
        if self.settings_service is None:
            return (
                AnalysisEngineOptions(
                    language=AnalysisLanguage.CHINESE_SIMPLIFIED,
                    asr_model=AsrModel.SMALL,
                    device=AnalysisDevice.AUTO,
                    ocr_resolution=OcrResolution.BALANCED,
                    sample_interval_seconds=None,
                    maximum_duration_seconds=3_600,
                ),
                True,
            )
        configured = (await self.settings_service.get()).analysis
        return (
            AnalysisEngineOptions(
                language=configured.language,
                asr_model=configured.asr_model,
                device=configured.device,
                ocr_resolution=OcrResolution.BALANCED,
                sample_interval_seconds=configured.sample_interval_seconds,
                maximum_duration_seconds=configured.maximum_duration_seconds,
            ),
            configured.ocr_enabled,
        )

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        video_id: str | None = None,
        part_id: str | None = None,
        feature: AnalysisFeature | None = None,
        result_status: AnalysisResultStatus | None = None,
    ) -> AnalysisList:
        filters = []
        if video_id is not None:
            filters.append(Analysis.video_id == video_id)
        if part_id is not None:
            filters.append(Analysis.part_id == part_id)
        if feature is not None:
            filters.append(Analysis.analysis_type == feature.canonical.value)
        if result_status is not None:
            filters.append(Analysis.status == result_status.value)
        async with self.session_factory() as session:
            total = await session.scalar(select(func.count(Analysis.id)).where(*filters))
            records = list(
                (
                    await session.scalars(
                        select(Analysis)
                        .where(*filters)
                        .order_by(Analysis.created_at.desc())
                        .offset(offset)
                        .limit(limit)
                    )
                ).all()
            )
        return AnalysisList(
            items=[self._to_read(record) for record in records],
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def get(self, analysis_id: str) -> AnalysisRead:
        async with self.session_factory() as session:
            record = await session.get(Analysis, analysis_id)
            if record is None:
                raise AppError(
                    ErrorCode.RESOURCE_NOT_FOUND,
                    "分析记录不存在",
                    action="刷新分析记录后重试",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            return self._to_read(record)

    async def edit_transcript(
        self, analysis_id: str, request: TranscriptEditRequest
    ) -> AnalysisRead:
        operation_id = f"edit:{analysis_id}:{uuid.uuid4().hex}"
        async with self._configuration_lock:
            self._active_job_ids.add(operation_id)
        try:
            async with self._edit_lock, self.artifact_service.mutation_guard():
                return await self._edit_transcript(analysis_id, request)
        finally:
            async with self._configuration_lock:
                self._active_job_ids.discard(operation_id)

    async def _edit_transcript(
        self, analysis_id: str, request: TranscriptEditRequest
    ) -> AnalysisRead:
        async with self.session_factory() as session:
            source = await session.get(Analysis, analysis_id)
            if source is None:
                raise AppError(
                    ErrorCode.RESOURCE_NOT_FOUND,
                    "待编辑的文本分析不存在",
                    action="刷新分析结果后重试",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            if (
                source.status != AnalysisResultStatus.COMPLETED.value
                or source.analysis_type not in {feature.value for feature in _TEXT_FEATURES}
                or not isinstance(source.result_json, dict)
            ):
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    "只有已完成的公开字幕、ASR 或 OCR 结果可以编辑",
                    action="选择一条已完成的文本分析结果后重试",
                    status_code=status.HTTP_409_CONFLICT,
                )
            source_document = _document_from_value(source.result_json.get("document"))
            if source_document is None:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    "该分析记录没有可安全编辑的时间轴文本",
                    action="重新运行字幕、ASR 或 OCR 后重试",
                    status_code=status.HTTP_409_CONFLICT,
                )
            source_parameters = (
                cast(dict[str, object], source.parameters)
                if isinstance(source.parameters, dict)
                else {}
            )
            job_id = source_parameters.get("jobId")
            job = await session.get(Job, job_id) if isinstance(job_id, str) else None
            part = (
                await session.get(VideoPart, source.part_id) if source.part_id is not None else None
            )
            if job is None or part is None:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    "该历史分析缺少可追踪的任务或分 P 上下文",
                    action="重新运行文本分析后再编辑",
                    status_code=status.HTTP_409_CONFLICT,
                )
            root_id_value = source_parameters.get("editRootAnalysisId")
            root_id = root_id_value if isinstance(root_id_value, str) else source.id
            related = list(
                (
                    await session.scalars(
                        select(Analysis).where(
                            Analysis.video_id == source.video_id,
                            Analysis.part_id == source.part_id,
                            Analysis.analysis_type == source.analysis_type,
                        )
                    )
                ).all()
            )
            revision = 1 + max(
                (
                    _edit_revision(item.parameters)
                    for item in related
                    if _edit_root(item, root_id) == root_id
                ),
                default=0,
            )
            source_updated_at = _as_utc(source.updated_at)

        source_maximum = max(
            (segment.end_seconds for segment in source_document.segments), default=0.0
        )
        allowed_end = max(float(part.duration) + 30.0, source_maximum + 5.0)
        if any(segment.end_seconds > allowed_end for segment in request.segments):
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "编辑后的时间戳明显超出当前分 P 时长",
                action="将结束时间调整到视频时长范围内后重试",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        edited_id = str(uuid.uuid4())
        generated_at = datetime.now(UTC)
        edited_document = SubtitleDocument(
            language=source_document.language,
            source=TranscriptSource.EDITED,
            segments=tuple(
                SubtitleSegment(
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                    text=segment.text,
                    source=TranscriptSource.EDITED,
                    language=source_document.language,
                    confidence=None,
                    evidence_id=f"edited:{edited_id}:{index:06d}",
                )
                for index, segment in enumerate(request.segments, start=1)
            ),
            model_name="manual-transcript-editor",
            model_version="1.0.0",
            generated_at=generated_at,
            warnings=("人工编辑版本；原始识别结果与原始导出产物保持不变。",),
        )
        provenance: dict[str, object] = {
            "sourceAnalysisId": source.id,
            "rootAnalysisId": root_id,
            "revision": revision,
            "editedAt": generated_at.isoformat(),
            "sourceUpdatedAt": source_updated_at.isoformat(),
            "sourceTranscriptSource": source_document.source.value,
        }
        published: list[Artifact] = []
        try:
            artifact_category = (
                "subtitle"
                if source.analysis_type == AnalysisFeature.SUBTITLES.value
                else "transcript"
            )
            for output_format in AnalysisExportFormat:
                content = await asyncio.to_thread(
                    export_subtitles, edited_document, output_format.value
                )
                artifact = await self._publish_bytes(
                    job_id=job.id,
                    artifact_type=artifact_category,
                    filename=(
                        f"edited-{source.analysis_type}-{_part_token(part.id)}-"
                        f"{edited_id[:8]}.{output_format.value}"
                    ),
                    mime_type=_subtitle_mime_type(output_format),
                    content=content,
                    media_info={
                        "analysisFeature": source.analysis_type,
                        "artifactRole": "edited_text_export",
                        "analysisId": edited_id,
                        "editedFromAnalysisId": source.id,
                        "editRootAnalysisId": root_id,
                        "editRevision": revision,
                        "partId": part.id,
                        "language": edited_document.language,
                        "source": TranscriptSource.EDITED.value,
                        "format": output_format.value,
                    },
                )
                published.append(artifact)
            report = self._safe_report(
                {
                    "feature": source.analysis_type,
                    "document": subtitle_document_to_dict(edited_document),
                    "editProvenance": provenance,
                    "artifactIds": [artifact.id for artifact in published],
                }
            )
            report_artifact = await self._publish_bytes(
                job_id=job.id,
                artifact_type="report",
                filename=(
                    f"edited-{source.analysis_type}-{_part_token(part.id)}-"
                    f"{edited_id[:8]}-report.json"
                ),
                mime_type="application/json",
                content=analysis_json_bytes(report),
                media_info={
                    "analysisFeature": source.analysis_type,
                    "artifactRole": "edited_text_report",
                    "analysisId": edited_id,
                    "editedFromAnalysisId": source.id,
                    "editRootAnalysisId": root_id,
                    "editRevision": revision,
                    "partId": part.id,
                },
            )
            published.insert(0, report_artifact)
            report["artifactIds"] = [artifact.id for artifact in published]
            record = Analysis(
                id=edited_id,
                video_id=source.video_id,
                part_id=part.id,
                analysis_type=source.analysis_type,
                status=AnalysisResultStatus.COMPLETED.value,
                result_json=report,
                model_name=edited_document.model_name,
                model_version=edited_document.model_version,
                parameters={
                    "jobId": job.id,
                    "editedFromAnalysisId": source.id,
                    "editRootAnalysisId": root_id,
                    "editRevision": revision,
                    "sourceUpdatedAt": source_updated_at.isoformat(),
                    "exportFormats": [value.value for value in AnalysisExportFormat],
                },
            )
            async with self.session_factory() as session:
                session.add(record)
                await session.commit()
                await session.refresh(record)
                session.expunge(record)
            return self._to_read(record)
        except Exception:
            await self._delete_published_artifacts(published)
            raise

    async def _delete_published_artifacts(self, artifacts: Sequence[Artifact]) -> None:
        for artifact in reversed(artifacts):
            try:
                await self.artifact_service.delete(artifact.id, delete_file=True)
            except Exception:
                logger.exception(
                    "Edited transcript artifact rollback failed",
                    extra={
                        "event": "edited_transcript_artifact_rollback_failed",
                        "artifact_id": artifact.id,
                    },
                )

    async def execute(
        self,
        job: Job,
        *,
        checkpoint: DownloadCheckpoint,
        reporter: DownloadExecutionReporter,
    ) -> Sequence[Artifact]:
        async with self._configuration_lock:
            self._active_job_ids.add(job.id)
        try:
            return await self._execute_job(
                job,
                checkpoint=checkpoint,
                reporter=reporter,
            )
        finally:
            try:
                await self._cleanup_owned_media(job.id)
            finally:
                async with self._configuration_lock:
                    self._active_job_ids.discard(job.id)

    async def reconfigure_storage(self, artifact_root: Path) -> None:
        candidate = await asyncio.to_thread(lambda: artifact_root.expanduser().resolve())
        async with self._configuration_lock:
            if self._active_job_ids:
                raise RuntimeError("Analysis storage cannot change while jobs are active")
            await asyncio.to_thread(candidate.mkdir, parents=True, exist_ok=True)
            self.artifact_root = candidate

    async def _execute_job(
        self,
        job: Job,
        *,
        checkpoint: DownloadCheckpoint,
        reporter: DownloadExecutionReporter,
    ) -> Sequence[Artifact]:
        execution = self._execution_input(cast(dict[str, object], job.input_json))
        engine = self.engine_factory(execution.engine_options)
        requires_video = any(feature in _VIDEO_FEATURES for feature in execution.features)
        requires_media = any(feature in _MEDIA_FEATURES for feature in execution.features)
        total_steps = len(execution.part_ids) * len(execution.features)
        processed_steps = 0
        outcomes: list[_StepOutcome] = []
        published: list[Artifact] = []
        await reporter.update(phase="analysis_preparing", progress=1.0)

        for part_id in execution.part_ids:
            source_path: Path | None = None
            acquired: AcquiredMedia | None = None
            if requires_media:
                await reporter.update(phase="analysis_media_acquisition", progress=2.0)
                artifact_id = execution.source_artifact_ids.get(part_id)
                if artifact_id is not None:
                    source_path = await self._verified_source_artifact(
                        artifact_id,
                        video_id=execution.video_id,
                        part_id=part_id,
                        requires_video=requires_video,
                    )
                else:
                    if self.media_acquirer is None:
                        raise AppError(
                            ErrorCode.RESOURCE_NOT_FOUND,
                            "分析任务缺少安全媒体来源",
                            action="重新创建分析任务以自动获取媒体",
                            status_code=status.HTTP_409_CONFLICT,
                        )
                    acquired = await self.media_acquirer.acquire(
                        parent_job=job,
                        video_id=execution.video_id,
                        part_id=part_id,
                        features=execution.features,
                        access_mode=execution.access_mode,
                        ocr_resolution=execution.ocr_resolution,
                        checkpoint=checkpoint,
                        reporter=reporter,
                    )
                    self._owned_media.setdefault(job.id, []).append(acquired)
                    source_path = acquired.path
            documents: list[SubtitleDocument] = []
            try:
                for feature in execution.features:
                    await checkpoint.checkpoint()
                    phase = f"analysis_{feature.value}"
                    await reporter.update(
                        phase=phase,
                        progress=self._step_progress(processed_steps, total_steps),
                    )
                    outcome, records, step_documents = await self._execute_step(
                        engine=engine,
                        execution=execution,
                        job=job,
                        part_id=part_id,
                        feature=feature,
                        source_path=source_path,
                        documents=documents,
                        checkpoint=checkpoint,
                    )
                    outcomes.append(outcome)
                    published.extend(records)
                    documents.extend(step_documents)
                    processed_steps += 1
                    await reporter.update(
                        phase=phase,
                        progress=self._step_progress(processed_steps, total_steps),
                    )
            finally:
                if acquired is not None:
                    await self._cleanup_one_owned_media(job.id, acquired)

        await checkpoint.checkpoint()
        await reporter.update(phase="analysis_manifest", progress=97.0)
        manifest = await self._publish_manifest(job, execution, outcomes)
        published.append(manifest)
        successful = sum(outcome.status == AnalysisResultStatus.COMPLETED for outcome in outcomes)
        if successful == 0:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "所选分析步骤均未成功，失败原因已保存在分析清单中",
                action="查看分析清单和能力状态，调整选项后重试",
            )
        await reporter.update(phase="completed", progress=100.0)
        return published

    async def _execute_step(
        self,
        *,
        engine: LocalAnalysisEngine,
        execution: _ExecutionInput,
        job: Job,
        part_id: str,
        feature: AnalysisFeature,
        source_path: Path | None,
        documents: Sequence[SubtitleDocument],
        checkpoint: DownloadCheckpoint,
    ) -> tuple[_StepOutcome, builtins.list[Artifact], tuple[SubtitleDocument, ...]]:
        reusable = await self._reusable_step(job.id, part_id, feature)
        if reusable is not None:
            return reusable
        record = await self._start_analysis_record(job, execution, part_id, feature)
        try:
            product = await self._run_feature(
                engine=engine,
                execution=execution,
                job=job,
                record=record,
                part_id=part_id,
                feature=feature,
                source_path=source_path,
                documents=documents,
                checkpoint=checkpoint,
            )
        except (DownloadCanceled, DownloadPaused, asyncio.CancelledError):
            await self._set_analysis_status(
                record.id,
                AnalysisResultStatus.CANCELED,
                result={
                    "error": {
                        "code": AnalysisErrorCode.CANCELED.value,
                        "message": "分析步骤已取消或暂停",
                        "action": "可继续、重试或重新创建分析任务",
                    }
                },
            )
            raise
        except AnalysisError as exc:
            outcome = await self._record_step_failure(
                record,
                feature,
                part_id,
                code=exc.failure.code.value,
                message=exc.failure.message,
                action=exc.failure.action,
            )
            return outcome, [], ()
        except Exception as exc:
            logger.error(
                "Analysis step failed unexpectedly (%s)",
                type(exc).__name__,
                extra={
                    "event": "analysis_step_unexpected_failure",
                    "job_id": job.id,
                    "analysis_id": record.id,
                    "feature": feature.value,
                },
            )
            outcome = await self._record_step_failure(
                record,
                feature,
                part_id,
                code="ANALYSIS_STEP_FAILED",
                message="分析步骤执行失败，其他已完成结果不受影响",
                action="查看能力状态和脱敏诊断后重试该分析",
            )
            return outcome, [], ()
        artifact_ids = tuple(item.id for item in product.artifacts)
        completed_report = dict(product.report)
        completed_report["artifactIds"] = list(artifact_ids)
        await self._complete_analysis_record(
            record.id,
            completed_report,
            model_name=product.model_name,
            model_version=product.model_version,
        )
        return (
            _StepOutcome(
                feature=feature,
                part_id=part_id,
                status=AnalysisResultStatus.COMPLETED,
                analysis_id=record.id,
                artifact_ids=artifact_ids,
            ),
            list(product.artifacts),
            product.documents,
        )

    async def discard_job_partials(self, job_id: str) -> None:
        await self._cleanup_owned_media(job_id)
        job_directory = safe_child_path(self.artifact_root, job_id)
        await asyncio.to_thread(self._remove_analysis_partials, job_directory)

    async def _cleanup_owned_media(self, job_id: str) -> None:
        sources = self._owned_media.pop(job_id, [])
        if self.media_acquirer is None:
            return
        for source in sources:
            try:
                await self.media_acquirer.cleanup(source)
            except Exception:
                logger.exception(
                    "Temporary analysis media could not be fully cleaned",
                    extra={"event": "analysis_source_cleanup_failed", "job_id": job_id},
                )

    async def _cleanup_one_owned_media(self, job_id: str, source: AcquiredMedia) -> None:
        if self.media_acquirer is not None:
            try:
                await self.media_acquirer.cleanup(source)
            except Exception:
                logger.exception(
                    "Temporary analysis media could not be fully cleaned after a part",
                    extra={"event": "analysis_source_cleanup_failed", "job_id": job_id},
                )
                return
        sources = self._owned_media.get(job_id)
        if sources is not None:
            self._owned_media[job_id] = [item for item in sources if item != source]
            if not self._owned_media[job_id]:
                self._owned_media.pop(job_id, None)

    async def _run_feature(
        self,
        *,
        engine: LocalAnalysisEngine,
        execution: _ExecutionInput,
        job: Job,
        record: Analysis,
        part_id: str,
        feature: AnalysisFeature,
        source_path: Path | None,
        documents: Sequence[SubtitleDocument],
        checkpoint: DownloadCheckpoint,
    ) -> _StepProduct:
        if feature == AnalysisFeature.BASIC:
            public_document, subtitle_availability = await self._subtitle_for_basic(
                video_id=execution.video_id,
                part_id=part_id,
                language=execution.language,
                access_mode=execution.access_mode,
                checkpoint=checkpoint,
            )
            report = await self._basic_report(
                execution.video_id,
                part_id,
                subtitle_availability=subtitle_availability,
            )
            artifact = await self._publish_report(job, record, part_id, feature, report)
            return _StepProduct(
                report=report,
                artifacts=(artifact,),
                documents=((public_document,) if public_document is not None else ()),
                model_name="structured-metadata",
                model_version="1.0.0",
            )

        if feature == AnalysisFeature.SUBTITLES:
            document = await self._public_subtitle_document(
                execution.video_id,
                part_id,
                execution.language,
                execution.access_mode,
                current_documents=documents,
                checkpoint=checkpoint,
            )
            return await self._publish_text_product(
                job=job,
                record=record,
                part_id=part_id,
                feature=feature,
                document=document,
                export_formats=execution.export_formats,
                checkpoint=checkpoint,
            )

        if feature == AnalysisFeature.SUMMARY:
            context = await self._summary_context(
                video_id=execution.video_id,
                part_id=part_id,
                language=execution.language,
                access_mode=execution.access_mode,
                current_documents=documents,
                checkpoint=checkpoint,
            )
            summary = await self._run_sync(
                lambda _event: engine.summary.analyze(
                    context.documents,
                    metadata_snapshots=context.metadata_snapshots,
                    visual_evidence=context.visual_evidence,
                    collection_warnings=context.warnings,
                ),
                checkpoint,
            )
            report = self._safe_report(
                {
                    "feature": feature.value,
                    "report": content_report_to_dict(summary),
                }
            )
            artifact = await self._publish_report(job, record, part_id, feature, report)
            return _StepProduct(
                report=report,
                artifacts=(artifact,),
                documents=(),
                model_name=summary.model_name,
                model_version=summary.model_version,
            )

        media_path = self._required_media_path(source_path)
        if feature == AnalysisFeature.MEDIA:
            technical = await self._run_sync(
                lambda event: engine.media.probe(
                    media_path,
                    include_keyframes=True,
                    cancellation_event=event,
                ),
                checkpoint,
            )
            report = self._safe_report(
                {"feature": feature.value, "report": to_json_compatible(technical)}
            )
            artifact = await self._publish_report(job, record, part_id, feature, report)
            return _StepProduct(
                report=report,
                artifacts=(artifact,),
                documents=(),
                model_name=technical.probe_name,
                model_version=technical.probe_version,
            )

        if feature == AnalysisFeature.AUDIO:
            audio = await self._run_sync(
                lambda event: engine.audio.analyze(
                    media_path,
                    maximum_analysis_seconds=(
                        float(execution.maximum_duration_seconds)
                        if execution.maximum_duration_seconds is not None
                        else None
                    ),
                    cancellation_event=event,
                ),
                checkpoint,
            )
            report = self._safe_report(
                {"feature": feature.value, "report": to_json_compatible(audio)}
            )
            artifact = await self._publish_report(job, record, part_id, feature, report)
            return _StepProduct(
                report=report,
                artifacts=(artifact,),
                documents=(),
                model_name=audio.analyzer_name,
                model_version=audio.analyzer_version,
            )

        if feature == AnalysisFeature.ASR:
            document = await self._run_sync(
                lambda event: engine.asr.transcribe(media_path, cancellation_event=event),
                checkpoint,
            )
            return await self._publish_text_product(
                job=job,
                record=record,
                part_id=part_id,
                feature=feature,
                document=document,
                export_formats=execution.export_formats,
                checkpoint=checkpoint,
            )

        if feature == AnalysisFeature.OCR:
            document = await self._run_sync(
                lambda event: engine.ocr.recognize(media_path, cancellation_event=event),
                checkpoint,
            )
            return await self._publish_text_product(
                job=job,
                record=record,
                part_id=part_id,
                feature=feature,
                document=document,
                export_formats=execution.export_formats,
                checkpoint=checkpoint,
            )

        if feature == AnalysisFeature.SCENES:
            return await self._scene_product(
                engine=engine,
                execution=execution,
                job=job,
                record=record,
                part_id=part_id,
                media_path=media_path,
                checkpoint=checkpoint,
            )

        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.INVALID_CONFIGURATION,
                message="分析任务包含不受支持的能力",
                action="刷新页面后重新选择分析能力",
            )
        )

    async def _scene_product(
        self,
        *,
        engine: LocalAnalysisEngine,
        execution: _ExecutionInput,
        job: Job,
        record: Analysis,
        part_id: str,
        media_path: Path,
        checkpoint: DownloadCheckpoint,
    ) -> _StepProduct:
        job_directory = safe_child_path(self.artifact_root, job.id)
        staging_directory = safe_child_path(
            job_directory,
            f".analysis-staging-{_part_token(part_id)}-{uuid.uuid4().hex}",
        )
        await asyncio.to_thread(staging_directory.mkdir, parents=True, exist_ok=False)
        try:
            scene_analysis, keyframe_analysis = await self._run_sync(
                lambda event: self._analyze_scenes(
                    engine,
                    media_path,
                    staging_directory,
                    threshold=execution.scene_threshold,
                    maximum_keyframes=execution.maximum_keyframes,
                    cancellation_event=event,
                ),
                checkpoint,
            )
            artifacts: list[Artifact] = []
            final_directory = safe_child_path(
                job_directory,
                _part_token(part_id),
                f"keyframes-{record.id[:8]}",
            )
            for keyframe in keyframe_analysis.artifacts:
                await checkpoint.checkpoint()
                artifact = await self.artifact_service.publish(
                    job_id=job.id,
                    artifact_type="keyframe",
                    staging_path=keyframe.path,
                    final_path=safe_child_path(final_directory, keyframe.filename),
                    filename=keyframe.filename,
                    mime_type="image/jpeg",
                    media_info={
                        "analysisFeature": AnalysisFeature.SCENES.value,
                        "artifactRole": "analysis_keyframe",
                        "analysisId": record.id,
                        "partId": part_id,
                        "sceneIndex": keyframe.scene_index,
                        "timestampSeconds": keyframe.timestamp_seconds,
                    },
                )
                artifacts.append(artifact)
            report = self._safe_report(
                {
                    "feature": AnalysisFeature.SCENES.value,
                    "sceneAnalysis": to_json_compatible(scene_analysis),
                    "keyframeAnalysis": to_json_compatible(keyframe_analysis),
                }
            )
            report_artifact = await self._publish_report(
                job, record, part_id, AnalysisFeature.SCENES, report
            )
            artifacts.append(report_artifact)
            return _StepProduct(
                report=report,
                artifacts=tuple(artifacts),
                documents=(),
                model_name=scene_analysis.analyzer_name,
                model_version=scene_analysis.analyzer_version,
            )
        finally:
            await asyncio.to_thread(shutil.rmtree, staging_directory, ignore_errors=True)

    @staticmethod
    def _analyze_scenes(
        engine: LocalAnalysisEngine,
        media_path: Path,
        output_directory: Path,
        *,
        threshold: float,
        maximum_keyframes: int,
        cancellation_event: threading.Event,
    ) -> tuple[Any, Any]:
        scene_analysis = engine.scenes.analyze(
            media_path,
            threshold=threshold,
            cancellation_event=cancellation_event,
        )
        keyframe_analysis = engine.scenes.extract_keyframes(
            media_path,
            scene_analysis,
            output_directory,
            maximum_keyframes=maximum_keyframes,
            cancellation_event=cancellation_event,
        )
        return scene_analysis, keyframe_analysis

    async def _publish_text_product(
        self,
        *,
        job: Job,
        record: Analysis,
        part_id: str,
        feature: AnalysisFeature,
        document: SubtitleDocument,
        export_formats: Sequence[AnalysisExportFormat],
        checkpoint: DownloadCheckpoint,
    ) -> _StepProduct:
        report = self._safe_report(
            {
                "feature": feature.value,
                "document": subtitle_document_to_dict(document),
            }
        )
        artifacts = [await self._publish_report(job, record, part_id, feature, report)]
        artifact_category = "subtitle" if feature == AnalysisFeature.SUBTITLES else "transcript"
        for output_format in export_formats:
            await checkpoint.checkpoint()
            content = await asyncio.to_thread(export_subtitles, document, output_format.value)
            filename = (
                f"{feature.value}-{_part_token(part_id)}-{record.id[:8]}.{output_format.value}"
            )
            artifacts.append(
                await self._publish_bytes(
                    job_id=job.id,
                    artifact_type=artifact_category,
                    filename=filename,
                    mime_type=_subtitle_mime_type(output_format),
                    content=content,
                    media_info={
                        "analysisFeature": feature.value,
                        "artifactRole": "analysis_text_export",
                        "analysisId": record.id,
                        "partId": part_id,
                        "language": document.language,
                        "source": document.source.value,
                        "modelName": document.model_name,
                        "modelVersion": document.model_version,
                        "format": output_format.value,
                    },
                )
            )
        return _StepProduct(
            report=report,
            artifacts=tuple(artifacts),
            documents=(document,),
            model_name=document.model_name,
            model_version=document.model_version,
        )

    async def _publish_report(
        self,
        job: Job,
        record: Analysis,
        part_id: str,
        feature: AnalysisFeature,
        report: dict[str, object],
    ) -> Artifact:
        filename = f"{feature.value}-{_part_token(part_id)}-{record.id[:8]}-report.json"
        return await self._publish_bytes(
            job_id=job.id,
            artifact_type="report",
            filename=filename,
            mime_type="application/json",
            content=analysis_json_bytes(report),
            media_info={
                "analysisFeature": feature.value,
                "artifactRole": "analysis_report",
                "analysisId": record.id,
                "partId": part_id,
            },
        )

    async def _publish_manifest(
        self,
        job: Job,
        execution: _ExecutionInput,
        outcomes: Sequence[_StepOutcome],
    ) -> Artifact:
        successful = sum(outcome.status == AnalysisResultStatus.COMPLETED for outcome in outcomes)
        failed = len(outcomes) - successful
        overall_status = "completed" if failed == 0 else ("partial" if successful else "failed")
        manifest = self._safe_report(
            {
                "jobId": job.id,
                "videoId": execution.video_id,
                "partIds": list(execution.part_ids),
                "overallStatus": overall_status,
                "completedSteps": successful,
                "failedSteps": failed,
                "generatedAt": datetime.now(UTC).isoformat(),
                "disclaimer": "自动分析结果可能存在误差，请结合时间戳或关键帧证据核对。",
                "steps": [outcome.as_dict() for outcome in outcomes],
            }
        )
        manifest_suffix = "" if job.retry_count == 0 else f"-retry-{job.retry_count:03d}"
        return await self._publish_bytes(
            job_id=job.id,
            artifact_type="report",
            filename=f"analysis-manifest{manifest_suffix}.json",
            mime_type="application/json",
            content=analysis_json_bytes(manifest),
            media_info={
                "artifactRole": "analysis_manifest",
                "attempt": job.retry_count,
                "overallStatus": overall_status,
                "completedSteps": successful,
                "failedSteps": failed,
            },
        )

    async def _publish_bytes(
        self,
        *,
        job_id: str,
        artifact_type: str,
        filename: str,
        mime_type: str,
        content: bytes,
        media_info: dict[str, object] | None,
    ) -> Artifact:
        if not content:
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.EXPORT_FAILED,
                    message="分析产物内容为空，未进行发布",
                    action="检查输入媒体或文本后重试",
                )
            )
        job_directory = safe_child_path(self.artifact_root, job_id)
        await asyncio.to_thread(job_directory.mkdir, parents=True, exist_ok=True)
        safe_filename = sanitize_filename(filename, fallback="analysis.json")
        final_path = safe_child_path(job_directory, safe_filename)
        staging_path = safe_child_path(
            job_directory,
            f".{safe_filename}.{uuid.uuid4().hex}.partial",
        )
        try:
            await asyncio.to_thread(self._write_staging_file, staging_path, content)
            return await self.artifact_service.publish(
                job_id=job_id,
                artifact_type=artifact_type,
                staging_path=staging_path,
                final_path=final_path,
                filename=safe_filename,
                mime_type=mime_type,
                media_info=media_info,
            )
        except OSError as exc:
            await asyncio.to_thread(staging_path.unlink, missing_ok=True)
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.EXPORT_FAILED,
                    message="分析产物写入失败",
                    action="检查产物目录权限和可用磁盘空间后重试",
                    diagnostic=f"artifact export failed: {type(exc).__name__}",
                )
            ) from exc

    async def _validate_video_parts(
        self, video_id: str, part_ids: Sequence[str]
    ) -> tuple[str, dict[str, str], str]:
        async with self.session_factory() as session:
            parts = list(
                (
                    await session.scalars(
                        select(VideoPart)
                        .where(
                            VideoPart.video_id == video_id,
                            VideoPart.id.in_(part_ids),
                        )
                        .options(joinedload(VideoPart.video))
                    )
                ).all()
            )
        found = {part.id: part.title for part in parts}
        if len(found) != len(part_ids):
            raise AppError(
                ErrorCode.RESOURCE_NOT_FOUND,
                "视频或分 P 记录不存在",
                action="返回视频详情页重新选择分 P",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return (
            parts[0].video.title,
            {part_id: found[part_id] for part_id in part_ids},
            VideoService.official_url(parts[0].video, parts[0]),
        )

    async def _verified_source_artifact(
        self,
        artifact_id: str,
        *,
        video_id: str,
        part_id: str,
        requires_video: bool,
    ) -> Path:
        async with self.session_factory() as session:
            artifact = await session.scalar(
                select(Artifact).where(Artifact.id == artifact_id).options(joinedload(Artifact.job))
            )
            if artifact is None:
                raise AppError(
                    ErrorCode.RESOURCE_NOT_FOUND,
                    "待分析媒体产物不存在",
                    action="刷新产物列表或先完成媒体下载",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            self._validate_source_record(
                artifact,
                video_id=video_id,
                part_id=part_id,
                requires_video=requires_video,
            )
        delivery = await self.artifact_service.delivery(artifact_id, None)
        return delivery.path

    async def _latest_source_artifact(
        self,
        *,
        video_id: str,
        part_id: str,
        requires_video: bool,
    ) -> str:
        async with self.session_factory() as session:
            candidates = list(
                (
                    await session.scalars(
                        select(Artifact)
                        .join(Artifact.job)
                        .where(
                            Artifact.type.in_(["video", "audio", "media"]),
                            Job.status == JobStatus.COMPLETED,
                        )
                        .options(joinedload(Artifact.job))
                        .order_by(Artifact.created_at.desc())
                        .limit(500)
                    )
                ).unique()
            )
            candidate_ids = [
                artifact.id
                for artifact in candidates
                if self._source_matches(
                    artifact,
                    video_id=video_id,
                    part_id=part_id,
                    requires_video=requires_video,
                )
            ]
        for artifact_id in candidate_ids:
            try:
                await self.artifact_service.delivery(artifact_id, None)
            except AppError:
                continue
            return artifact_id
        raise AppError(
            ErrorCode.RESOURCE_NOT_FOUND,
            "没有可用于分析的已完成媒体产物",
            action="先下载该分 P 的媒体文件，再创建所选分析任务",
            status_code=status.HTTP_409_CONFLICT,
        )

    def _validate_source_record(
        self,
        artifact: Artifact,
        *,
        video_id: str,
        part_id: str,
        requires_video: bool,
    ) -> None:
        if not self._source_matches(
            artifact,
            video_id=video_id,
            part_id=part_id,
            requires_video=requires_video,
        ):
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "所选产物不是该视频分 P 的已完成可分析媒体",
                action="从当前视频的已完成媒体产物中重新选择",
                status_code=status.HTTP_409_CONFLICT,
            )

    @staticmethod
    def _source_matches(
        artifact: Artifact,
        *,
        video_id: str,
        part_id: str,
        requires_video: bool,
    ) -> bool:
        if artifact.type not in {"video", "audio", "media"} or (
            artifact.job.status != JobStatus.COMPLETED
        ):
            return False
        if requires_video and (
            artifact.type not in {"video", "media"}
            or not artifact.mime_type.lower().startswith("video/")
        ):
            return False
        payload = artifact.job.input_json
        if not isinstance(payload, dict):
            return False
        return payload.get("video_id") == video_id and payload.get("part_id") == part_id

    async def _basic_report(
        self,
        video_id: str,
        part_id: str,
        *,
        subtitle_availability: str,
    ) -> dict[str, object]:
        async with self.session_factory() as session:
            part = await session.scalar(
                select(VideoPart)
                .where(VideoPart.id == part_id, VideoPart.video_id == video_id)
                .options(joinedload(VideoPart.video))
            )
            if part is None:
                raise AnalysisError(
                    AnalysisFailure(
                        code=AnalysisErrorCode.INVALID_MEDIA,
                        message="待分析视频分 P 记录不存在",
                        action="重新解析视频后再创建分析任务",
                    )
                )
            video = part.video
            return self._safe_report(
                {
                    "feature": AnalysisFeature.BASIC.value,
                    "generatedAt": datetime.now(UTC).isoformat(),
                    "video": {
                        "id": video.id,
                        "bvid": video.bvid,
                        "aid": video.aid,
                        "title": video.title,
                        "description": video.description,
                        "ownerName": video.owner_name,
                        "durationSeconds": video.duration,
                        "publishedAt": (
                            video.published_at.isoformat() if video.published_at else None
                        ),
                        "stats": video.stats,
                        "tags": video.tags,
                        "rights": video.rights,
                    },
                    "part": {
                        "id": part.id,
                        "cid": part.cid,
                        "pageNumber": part.page_number,
                        "title": part.title,
                        "durationSeconds": part.duration,
                    },
                    "subtitleAvailability": subtitle_availability,
                    "model": {
                        "name": "structured-metadata",
                        "version": "1.0.0",
                        "deterministic": True,
                    },
                }
            )

    async def _public_subtitle_document(
        self,
        video_id: str,
        part_id: str,
        language: AnalysisLanguage,
        access_mode: AccessMode,
        *,
        current_documents: Sequence[SubtitleDocument] = (),
        checkpoint: DownloadCheckpoint,
    ) -> SubtitleDocument:
        preferred = await self._find_public_subtitle(
            video_id=video_id,
            part_id=part_id,
            language=language,
            access_mode=access_mode,
            current_documents=current_documents,
            checkpoint=checkpoint,
        )
        if preferred is None:
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.DEPENDENCY_UNAVAILABLE,
                    message="当前视频没有可用的公开字幕",
                    action="选择 ASR 或 OCR 文本提取后重试",
                )
            )
        return preferred

    async def _find_public_subtitle(
        self,
        *,
        video_id: str,
        part_id: str,
        language: AnalysisLanguage,
        access_mode: AccessMode,
        current_documents: Sequence[SubtitleDocument] = (),
        checkpoint: DownloadCheckpoint,
    ) -> SubtitleDocument | None:
        documents = await self._historical_documents(video_id, part_id)
        public = [
            document
            for document in (*current_documents, *documents)
            if document.source == TranscriptSource.PUBLIC_SUBTITLE
        ]
        preferred = _preferred_document(public, language)
        if preferred is not None or self.subtitle_fetcher is None:
            return preferred
        return await self.subtitle_fetcher.fetch(
            video_id=video_id,
            part_id=part_id,
            language=language,
            access_mode=access_mode,
            checkpoint=checkpoint,
        )

    async def _subtitle_for_basic(
        self,
        *,
        video_id: str,
        part_id: str,
        language: AnalysisLanguage,
        access_mode: AccessMode,
        checkpoint: DownloadCheckpoint,
    ) -> tuple[SubtitleDocument | None, str]:
        try:
            document = await self._find_public_subtitle(
                video_id=video_id,
                part_id=part_id,
                language=language,
                access_mode=access_mode,
                checkpoint=checkpoint,
            )
        except (AppError, AnalysisError):
            return None, "unknown"
        return document, "available" if document is not None else "unavailable"

    async def _historical_documents(
        self, video_id: str, part_id: str
    ) -> builtins.list[SubtitleDocument]:
        async with self.session_factory() as session:
            records = list(
                (
                    await session.scalars(
                        select(Analysis)
                        .where(
                            Analysis.video_id == video_id,
                            Analysis.part_id == part_id,
                            Analysis.status == AnalysisResultStatus.COMPLETED.value,
                            Analysis.analysis_type.in_(
                                [feature.value for feature in _TEXT_FEATURES]
                            ),
                        )
                        .order_by(Analysis.created_at.desc())
                        .limit(100)
                    )
                ).all()
            )
        documents: builtins.list[SubtitleDocument] = []
        seen: set[tuple[TranscriptSource, str]] = set()
        for record in records:
            result = record.result_json
            raw_document = result.get("document") if isinstance(result, dict) else None
            document = _document_from_value(raw_document)
            if document is None:
                continue
            key = (document.source, document.language)
            if key in seen:
                continue
            seen.add(key)
            documents.append(document)
        return documents

    async def _summary_context(
        self,
        *,
        video_id: str,
        part_id: str,
        language: AnalysisLanguage,
        access_mode: AccessMode,
        current_documents: Sequence[SubtitleDocument],
        checkpoint: DownloadCheckpoint,
    ) -> _SummaryContext:
        historical_documents = await self._historical_documents(video_id, part_id)
        warnings: list[str] = []
        candidates = [*current_documents, *historical_documents]
        try:
            public_document = await self._find_public_subtitle(
                video_id=video_id,
                part_id=part_id,
                language=language,
                access_mode=access_mode,
                current_documents=candidates,
                checkpoint=checkpoint,
            )
        except (AppError, AnalysisError) as exc:
            public_document = None
            message = exc.failure.message if isinstance(exc, AnalysisError) else exc.message
            warnings.append(f"公开字幕收集未完成：{message}")
        if public_document is not None:
            candidates.insert(0, public_document)
        documents = _unique_documents(candidates)
        if not documents:
            warnings.append("未取得公开字幕或历史 ASR/OCR；本次仅生成元数据有限概览，未请求媒体")
        elif not any(document.source == TranscriptSource.PUBLIC_SUBTITLE for document in documents):
            warnings.append("当前视频没有可复用的公开字幕；文本结论来自 ASR/OCR 或人工编辑")
        metadata_snapshots = await self._summary_metadata_snapshots(video_id, part_id)
        visual_evidence = await self._summary_visual_evidence(video_id, part_id)
        return _SummaryContext(
            documents=documents,
            metadata_snapshots=metadata_snapshots,
            visual_evidence=visual_evidence,
            warnings=tuple(warnings),
        )

    async def _summary_metadata_snapshots(
        self, video_id: str, part_id: str
    ) -> tuple[MetadataSnapshot, ...]:
        async with self.session_factory() as session:
            part = await session.scalar(
                select(VideoPart)
                .where(VideoPart.id == part_id, VideoPart.video_id == video_id)
                .options(joinedload(VideoPart.video))
            )
            if part is None:
                raise AnalysisError(
                    AnalysisFailure(
                        code=AnalysisErrorCode.INVALID_MEDIA,
                        message="摘要所需的视频元数据不存在",
                        action="重新解析视频后再创建摘要任务",
                    )
                )
            history = list(
                (
                    await session.scalars(
                        select(Analysis)
                        .where(
                            Analysis.video_id == video_id,
                            Analysis.part_id == part_id,
                            Analysis.analysis_type == AnalysisFeature.BASIC.value,
                            Analysis.status == AnalysisResultStatus.COMPLETED.value,
                        )
                        .order_by(Analysis.created_at.desc())
                        .limit(20)
                    )
                ).all()
            )
            video = part.video
            current = MetadataSnapshot(
                title=_summary_text(video.title, 512),
                part_title=_summary_text(part.title, 512),
                description=_summary_text(video.description, 20_000),
                owner_name=_summary_text(video.owner_name, 256),
                tags=tuple(_summary_text(tag, 128) for tag in video.tags[:100]),
                stats=_metadata_stats(video.stats),
                published_at=_as_utc(video.published_at) if video.published_at else None,
                duration_seconds=float(part.duration),
                captured_at=_as_utc(video.parsed_at),
                evidence_id=f"metadata:{video.id}:{part.id}:current",
                current=True,
            )
        snapshots = [current]
        for record in history:
            snapshot = _metadata_snapshot_from_analysis(record)
            if snapshot is not None:
                snapshots.append(snapshot)
        return tuple(snapshots)

    async def _summary_visual_evidence(
        self, video_id: str, part_id: str
    ) -> tuple[VisualEvidence, ...]:
        async with self.session_factory() as session:
            records = list(
                (
                    await session.scalars(
                        select(Analysis)
                        .where(
                            Analysis.video_id == video_id,
                            Analysis.part_id == part_id,
                            Analysis.analysis_type == AnalysisFeature.SCENES.value,
                            Analysis.status == AnalysisResultStatus.COMPLETED.value,
                        )
                        .order_by(Analysis.created_at.desc())
                        .limit(10)
                    )
                ).all()
            )
            artifact_ids = {
                artifact_id
                for record in records
                for artifact_id in _analysis_artifact_ids(record.result_json)
            }
            artifacts = (
                list(
                    (
                        await session.scalars(
                            select(Artifact).where(
                                Artifact.id.in_(artifact_ids), Artifact.type == "keyframe"
                            )
                        )
                    ).all()
                )
                if artifact_ids
                else []
            )

        evidence: list[VisualEvidence] = []
        seen_scenes: set[tuple[float, float]] = set()
        for record in records:
            result = record.result_json if isinstance(record.result_json, dict) else {}
            scene_report = result.get("sceneAnalysis")
            if not isinstance(scene_report, dict):
                continue
            raw_scenes = scene_report.get("scenes")
            if not isinstance(raw_scenes, list):
                continue
            for index, raw_scene in enumerate(raw_scenes[:5_000], start=1):
                if not isinstance(raw_scene, dict):
                    continue
                start = _finite_non_negative(
                    raw_scene.get("startSeconds", raw_scene.get("start_seconds"))
                )
                end = _finite_non_negative(
                    raw_scene.get("endSeconds", raw_scene.get("end_seconds"))
                )
                if start is None or end is None or end < start or (start, end) in seen_scenes:
                    continue
                seen_scenes.add((start, end))
                scene_index = _safe_int(raw_scene.get("index"), index)
                evidence.append(
                    VisualEvidence(
                        start_seconds=start,
                        end_seconds=end,
                        source=ContentInputSource.SCENE,
                        evidence_id=f"scene:{record.id}:{scene_index}",
                        text=f"镜头 {scene_index}，时间范围 {start:.3f}–{end:.3f} 秒",
                    )
                )

        for artifact in artifacts:
            info = artifact.media_info if isinstance(artifact.media_info, dict) else {}
            timestamp = _finite_non_negative(info.get("timestampSeconds"))
            if timestamp is None:
                continue
            try:
                await self.artifact_service.delivery(artifact.id, None)
            except AppError:
                continue
            scene_index = _safe_int(info.get("sceneIndex"), 0)
            evidence.append(
                VisualEvidence(
                    start_seconds=timestamp,
                    end_seconds=timestamp,
                    source=ContentInputSource.KEYFRAME,
                    evidence_id=f"keyframe:{artifact.id}",
                    text=f"镜头 {scene_index} 的关键帧，定位于 {timestamp:.3f} 秒",
                    artifact_id=artifact.id,
                )
            )
        evidence.sort(
            key=lambda item: (
                item.start_seconds,
                item.source != ContentInputSource.KEYFRAME,
                item.evidence_id,
            )
        )
        return tuple(evidence[:20_000])

    async def _start_analysis_record(
        self,
        job: Job,
        execution: _ExecutionInput,
        part_id: str,
        feature: AnalysisFeature,
    ) -> Analysis:
        parameters: dict[str, object] = {
            "jobId": job.id,
            "language": execution.language.value,
            "accessMode": execution.access_mode.value,
            "asrModel": execution.asr_model.value,
            "device": execution.device.value,
            "ocrResolution": execution.ocr_resolution.value,
            "sampleIntervalSeconds": execution.sample_interval_seconds,
            "maximumDurationSeconds": execution.maximum_duration_seconds,
            "sceneThreshold": execution.scene_threshold,
            "maximumKeyframes": execution.maximum_keyframes,
        }
        record = Analysis(
            video_id=execution.video_id,
            part_id=part_id,
            analysis_type=feature.value,
            status=AnalysisResultStatus.RUNNING.value,
            result_json=None,
            model_name=None,
            model_version=None,
            parameters=parameters,
        )
        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            session.expunge(record)
        return record

    async def _complete_analysis_record(
        self,
        analysis_id: str,
        report: dict[str, object],
        *,
        model_name: str | None,
        model_version: str | None,
    ) -> None:
        async with self.session_factory() as session:
            record = await session.get(Analysis, analysis_id)
            if record is None:
                raise RuntimeError("Analysis row disappeared while completing a step")
            record.status = AnalysisResultStatus.COMPLETED.value
            record.result_json = self._safe_report(report)
            record.model_name = _safe_label(model_name)
            record.model_version = _safe_label(model_version)
            await session.commit()

    async def _set_analysis_status(
        self,
        analysis_id: str,
        result_status: AnalysisResultStatus,
        *,
        result: dict[str, object],
    ) -> None:
        async with self.session_factory() as session:
            record = await session.get(Analysis, analysis_id)
            if record is None:
                return
            record.status = result_status.value
            record.result_json = self._safe_report(result)
            await session.commit()

    async def _record_step_failure(
        self,
        record: Analysis,
        feature: AnalysisFeature,
        part_id: str,
        *,
        code: str,
        message: str,
        action: str,
    ) -> _StepOutcome:
        error = self._safe_report(
            {
                "code": code[:64],
                "message": message[:512],
                "action": action[:512],
            }
        )
        await self._set_analysis_status(
            record.id,
            AnalysisResultStatus.FAILED,
            result={"error": error},
        )
        return _StepOutcome(
            feature=feature,
            part_id=part_id,
            status=AnalysisResultStatus.FAILED,
            analysis_id=record.id,
            artifact_ids=(),
            error=error,
        )

    async def _reusable_step(
        self, job_id: str, part_id: str, feature: AnalysisFeature
    ) -> tuple[_StepOutcome, builtins.list[Artifact], tuple[SubtitleDocument, ...]] | None:
        async with self.session_factory() as session:
            analyses = list(
                (
                    await session.scalars(
                        select(Analysis)
                        .where(
                            Analysis.part_id == part_id,
                            Analysis.analysis_type == feature.value,
                            Analysis.status == AnalysisResultStatus.COMPLETED.value,
                        )
                        .order_by(Analysis.created_at.desc())
                        .limit(20)
                    )
                ).all()
            )
            record = next(
                (
                    item
                    for item in analyses
                    if isinstance(item.parameters, dict) and item.parameters.get("jobId") == job_id
                ),
                None,
            )
            if record is None:
                return None
            artifacts = list(
                (
                    await session.scalars(
                        select(Artifact)
                        .where(
                            Artifact.job_id == job_id,
                            Artifact.type.in_(["report", "subtitle", "transcript", "keyframe"]),
                        )
                        .order_by(Artifact.created_at)
                    )
                ).all()
            )
            artifacts = [
                artifact
                for artifact in artifacts
                if isinstance(artifact.media_info, dict)
                and artifact.media_info.get("analysisId") == record.id
            ]
        report_artifact = next(
            (
                item
                for item in artifacts
                if isinstance(item.media_info, dict)
                and item.media_info.get("artifactRole") == "analysis_report"
            ),
            None,
        )
        if report_artifact is None:
            return None
        for artifact in artifacts:
            try:
                await self.artifact_service.delivery(artifact.id, None)
            except AppError:
                return None
        documents: tuple[SubtitleDocument, ...] = ()
        if feature in _TEXT_FEATURES and isinstance(record.result_json, dict):
            document = _document_from_value(record.result_json.get("document"))
            if document is not None:
                documents = (document,)
        outcome = _StepOutcome(
            feature=feature,
            part_id=part_id,
            status=AnalysisResultStatus.COMPLETED,
            analysis_id=record.id,
            artifact_ids=tuple(item.id for item in artifacts),
        )
        return outcome, artifacts, documents

    @staticmethod
    def _execution_input(payload: Mapping[str, object]) -> _ExecutionInput:
        try:
            video_id = _required_text(payload, "video_id")
            part_ids = tuple(_required_text_list(payload, "part_ids"))
            feature_values = _required_text_list(payload, "features")
            features = tuple(AnalysisFeature(value).canonical for value in feature_values)
            source_values = payload.get("source_artifact_ids", {})
            if not isinstance(source_values, dict):
                raise ValueError("source artifact map is invalid")
            source_artifact_ids = {
                str(key): str(value)
                for key, value in source_values.items()
                if isinstance(key, str) and isinstance(value, str)
            }
            language = AnalysisLanguage(_required_text(payload, "language"))
            access_mode = AccessMode(_required_text(payload, "access_mode"))
            if access_mode == AccessMode.AUTO:
                raise ValueError("auto access mode is not valid for persisted analysis")
            asr_model = AsrModel(_required_text(payload, "asr_model"))
            device = AnalysisDevice(_required_text(payload, "device"))
            ocr_resolution = OcrResolution(_required_text(payload, "ocr_resolution"))
            sample_interval = _optional_float(payload.get("sample_interval_seconds"))
            export_formats = tuple(
                AnalysisExportFormat(value)
                for value in _required_text_list(payload, "export_formats")
            )
            maximum_duration = _optional_int(payload.get("maximum_duration_seconds"))
            scene_threshold = _required_float(payload, "scene_threshold")
            maximum_keyframes = _required_int(payload, "maximum_keyframes")
        except (KeyError, TypeError, ValueError) as exc:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "分析任务参数已损坏，无法安全执行",
                action="删除该任务并从视频详情页重新创建",
            ) from exc
        if (
            not part_ids
            or len(set(part_ids)) != len(part_ids)
            or not features
            or len(set(features)) != len(features)
            or not export_formats
            or len(set(export_formats)) != len(export_formats)
            or not 0.01 <= scene_threshold <= 0.99
            or not 1 <= maximum_keyframes <= 200
            or (sample_interval is not None and not 0.2 <= sample_interval <= 600)
            or (maximum_duration is not None and not 60 <= maximum_duration <= 86_400)
        ):
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "分析任务参数超出安全范围",
                action="删除该任务并重新创建",
            )
        if not set(source_artifact_ids).issubset(part_ids):
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "分析任务包含不匹配的媒体来源",
                action="删除该任务并重新创建",
            )
        return _ExecutionInput(
            video_id=video_id,
            part_ids=part_ids,
            features=tuple(sorted(features, key=lambda item: _EXECUTION_ORDER[item])),
            source_artifact_ids=source_artifact_ids,
            language=language,
            access_mode=access_mode,
            asr_model=asr_model,
            device=device,
            ocr_resolution=ocr_resolution,
            sample_interval_seconds=sample_interval,
            export_formats=export_formats,
            maximum_duration_seconds=maximum_duration,
            scene_threshold=scene_threshold,
            maximum_keyframes=maximum_keyframes,
        )

    @staticmethod
    async def _run_sync(
        function: Callable[[threading.Event], Any], checkpoint: DownloadCheckpoint
    ) -> Any:
        cancellation_event = threading.Event()
        worker = asyncio.create_task(asyncio.to_thread(function, cancellation_event))
        try:
            while not worker.done():
                done, _ = await asyncio.wait({worker}, timeout=0.2)
                if done:
                    break
                try:
                    await checkpoint.checkpoint()
                except (DownloadCanceled, DownloadPaused, asyncio.CancelledError):
                    cancellation_event.set()
                    try:
                        await asyncio.wait_for(asyncio.shield(worker), timeout=10.0)
                    except (TimeoutError, AnalysisError):
                        logger.debug(
                            "Analysis worker acknowledged cancellation or exceeded "
                            "its grace period",
                            extra={"event": "analysis_worker_cancellation_grace_finished"},
                        )
                    raise
            return worker.result()
        except asyncio.CancelledError:
            cancellation_event.set()
            raise

    @staticmethod
    def _required_media_path(value: Path | None) -> Path:
        if value is None:
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.INVALID_MEDIA,
                    message="分析步骤缺少已验证媒体产物",
                    action="先完成媒体下载，再重新创建分析任务",
                )
            )
        return value

    @staticmethod
    def _step_progress(processed: int, total: int) -> float:
        return min(96.0, 2.0 + (processed / max(1, total)) * 93.0)

    @staticmethod
    def _safe_report(value: object) -> dict[str, object]:
        sanitized = _sanitize_json(to_json_compatible(value))
        if not isinstance(sanitized, dict):
            raise TypeError("Analysis report root must be an object")
        return cast(dict[str, object], sanitized)

    @staticmethod
    def _to_read(record: Analysis) -> AnalysisRead:
        return AnalysisRead(
            id=record.id,
            video_id=record.video_id,
            part_id=record.part_id,
            feature=AnalysisFeature(record.analysis_type).canonical,
            status=AnalysisResultStatus(record.status),
            result=cast(dict[str, object] | None, record.result_json),
            model_name=record.model_name,
            model_version=record.model_version,
            parameters=cast(dict[str, object], record.parameters),
            created_at=_as_utc(record.created_at),
            updated_at=_as_utc(record.updated_at),
        )

    @staticmethod
    def _write_staging_file(path: Path, content: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _remove_analysis_partials(job_directory: Path) -> None:
        if not job_directory.is_dir() or job_directory.is_symlink():
            return
        for path in job_directory.rglob("*"):
            try:
                if path.is_symlink():
                    continue
                if path.is_file() and (path.name.endswith(".partial") or ".partial." in path.name):
                    path.unlink()
                elif path.is_dir() and path.name.startswith(".analysis-staging-"):
                    shutil.rmtree(path)
            except OSError:
                logger.warning(
                    "An analysis partial could not be removed",
                    extra={"event": "analysis_partial_cleanup_failed"},
                )


def _stream_cost(stream: MediaStreamRead) -> tuple[int, int, int]:
    return (
        stream.bitrate if stream.bitrate is not None and stream.bitrate > 0 else 2**63 - 1,
        (
            stream.estimated_size
            if stream.estimated_size is not None and stream.estimated_size > 0
            else 2**63 - 1
        ),
        stream.height or 0,
    )


def _select_provider_subtitle(
    subtitles: Sequence[ProviderSubtitle], language: AnalysisLanguage
) -> ProviderSubtitle | None:
    if not subtitles:
        return None
    if language == AnalysisLanguage.AUTO:
        return subtitles[0]
    requested = language.value.casefold().replace("_", "-")
    exact = next(
        (
            subtitle
            for subtitle in subtitles
            if subtitle.language.casefold().replace("_", "-") == requested
        ),
        None,
    )
    if exact is not None:
        return exact
    requested_base = requested.split("-", maxsplit=1)[0]
    return next(
        (
            subtitle
            for subtitle in subtitles
            if subtitle.language.casefold().split("-", maxsplit=1)[0] == requested_base
        ),
        subtitles[0],
    )


def _json_depth(value: object) -> int:
    maximum = 1
    stack: list[tuple[object, int]] = [(value, 1)]
    visited = 0
    while stack:
        current, depth = stack.pop()
        visited += 1
        if visited > 200_000 or depth > 30:
            return 31
        maximum = max(maximum, depth)
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    return maximum


def _default_engine_factory(options: AnalysisEngineOptions) -> LocalAnalysisEngine:
    intervals = {
        OcrResolution.ECONOMY: 4.0,
        OcrResolution.BALANCED: 2.0,
        OcrResolution.DETAIL: 0.75,
    }
    widths = {
        OcrResolution.ECONOMY: 960,
        OcrResolution.BALANCED: 1280,
        OcrResolution.DETAIL: 1920,
    }
    interval = options.sample_interval_seconds or intervals[options.ocr_resolution]
    maximum_frames = 300
    if options.maximum_duration_seconds is not None:
        maximum_frames = min(
            10_000,
            max(1, math.ceil(options.maximum_duration_seconds / interval)),
        )
    language = options.language.value
    asr_device = "cuda" if options.device == AnalysisDevice.GPU else options.device.value
    ocr_device = "gpu" if options.device == AnalysisDevice.GPU else "cpu"
    return LocalAnalysisEngine(
        asr_config=FasterWhisperConfig(
            model_size_or_path=options.asr_model.value,
            device=cast(Any, asr_device),
            language=language,
        ),
        ocr_config=PaddleOcrConfig(
            language=language,
            device=cast(Any, ocr_device),
            sample_interval_seconds=interval,
            maximum_frames=maximum_frames,
            maximum_width=widths[options.ocr_resolution],
        ),
    )


def _part_token(part_id: str) -> str:
    return hashlib.sha256(part_id.encode("ascii", errors="ignore")).hexdigest()[:12]


def _subtitle_mime_type(output_format: AnalysisExportFormat) -> str:
    return {
        AnalysisExportFormat.SRT: "application/x-subrip; charset=utf-8",
        AnalysisExportFormat.VTT: "text/vtt; charset=utf-8",
        AnalysisExportFormat.TXT: "text/plain; charset=utf-8",
        AnalysisExportFormat.JSON: "application/json",
    }[output_format]


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.casefold().replace("-", "_")
            if normalized in _SENSITIVE_KEYS or normalized.endswith("_url"):
                result[key] = "<redacted>"
            else:
                result[key] = _sanitize_json(item)
        return result
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, str):
        sanitized = value.replace("\x00", "")
        sanitized = _CREDENTIAL_PATTERN.sub("<redacted>", sanitized)
        sanitized = _URL_PATTERN.sub("<redacted>", sanitized)
        sanitized = _WINDOWS_PATH_PATTERN.sub("<redacted>", sanitized)
        sanitized = _POSIX_PATH_PATTERN.sub("<redacted>", sanitized)
        return sanitized
    return value


def _document_from_value(value: object) -> SubtitleDocument | None:
    if not isinstance(value, dict):
        return None
    try:
        language = str(value["language"])
        source = TranscriptSource(str(value["source"]))
        raw_segments = value["segments"]
        if not isinstance(raw_segments, list) or len(raw_segments) > 1_000_000:
            return None
        segments: list[SubtitleSegment] = []
        for raw_segment in raw_segments:
            if not isinstance(raw_segment, dict):
                continue
            confidence_value = raw_segment.get("confidence")
            confidence = float(confidence_value) if confidence_value is not None else None
            segments.append(
                SubtitleSegment(
                    start_seconds=float(raw_segment["startSeconds"]),
                    end_seconds=float(raw_segment["endSeconds"]),
                    text=str(raw_segment["text"]),
                    source=source,
                    language=language,
                    confidence=confidence,
                    evidence_id=(
                        str(raw_segment["evidenceId"])
                        if raw_segment.get("evidenceId") is not None
                        else None
                    ),
                )
            )
        generated_value = value.get("generatedAt")
        generated_at = (
            datetime.fromisoformat(str(generated_value).replace("Z", "+00:00"))
            if generated_value is not None
            else datetime.now(UTC)
        )
        warnings_value = value.get("warnings", [])
        warnings = (
            tuple(str(item) for item in warnings_value) if isinstance(warnings_value, list) else ()
        )
        return SubtitleDocument(
            language=language,
            source=source,
            segments=tuple(segments),
            model_name=(str(value["modelName"]) if value.get("modelName") is not None else None),
            model_version=(
                str(value["modelVersion"]) if value.get("modelVersion") is not None else None
            ),
            generated_at=generated_at,
            warnings=warnings,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _unique_documents(documents: Sequence[SubtitleDocument]) -> tuple[SubtitleDocument, ...]:
    unique: list[SubtitleDocument] = []
    seen: set[str] = set()
    for document in documents:
        encoded = json.dumps(
            subtitle_document_to_dict(document),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        unique.append(document)
        if len(unique) >= 100:
            break
    return tuple(unique)


def _summary_text(value: object, maximum: int) -> str:
    if value is None:
        return ""
    sanitized = str(_sanitize_json(str(value))).replace("\x00", "").strip()
    return sanitized[:maximum]


def _metadata_stats(value: object) -> tuple[tuple[str, int | float | None], ...]:
    if not isinstance(value, dict):
        return ()
    result: list[tuple[str, int | float | None]] = []
    for raw_key, raw_value in sorted(value.items(), key=lambda item: str(item[0]))[:100]:
        key = _summary_text(raw_key, 64)
        if not key:
            continue
        if raw_value is None:
            result.append((key, None))
        elif isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            numeric = float(raw_value)
            if math.isfinite(numeric):
                result.append((key, raw_value))
    return tuple(result)


def _metadata_snapshot_from_analysis(record: Analysis) -> MetadataSnapshot | None:
    result = record.result_json if isinstance(record.result_json, dict) else {}
    raw_video = result.get("video")
    raw_part = result.get("part")
    if not isinstance(raw_video, dict) or not isinstance(raw_part, dict):
        return None
    title = _summary_text(raw_video.get("title", ""), 512)
    part_title = _summary_text(raw_part.get("title", ""), 512)
    if not title or not part_title:
        return None
    raw_tags = raw_video.get("tags")
    tags = (
        tuple(_summary_text(item, 128) for item in raw_tags[:100])
        if isinstance(raw_tags, list)
        else ()
    )
    published_at = _optional_datetime(raw_video.get("publishedAt"))
    duration = _finite_non_negative(raw_part.get("durationSeconds"))
    return MetadataSnapshot(
        title=title,
        part_title=part_title,
        description=_summary_text(raw_video.get("description", ""), 20_000),
        owner_name=_summary_text(raw_video.get("ownerName", ""), 256),
        tags=tags,
        stats=_metadata_stats(raw_video.get("stats")),
        published_at=published_at,
        duration_seconds=duration,
        captured_at=_as_utc(record.updated_at),
        evidence_id=f"analysis:{record.id}",
        current=False,
    )


def _analysis_artifact_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    raw_ids = value.get("artifactIds")
    if not isinstance(raw_ids, list):
        return ()
    return tuple(item for item in raw_ids if isinstance(item, str) and len(item) <= 36)


def _finite_non_negative(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) and result >= 0 else None


def _safe_int(value: object, fallback: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return max(0, value)


def _optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _edit_revision(value: object) -> int:
    if not isinstance(value, dict):
        return 0
    revision = value.get("editRevision")
    return revision if isinstance(revision, int) and not isinstance(revision, bool) else 0


def _edit_root(record: Analysis, fallback: str) -> str:
    if not isinstance(record.parameters, dict):
        return fallback
    value = record.parameters.get("editRootAnalysisId")
    return value if isinstance(value, str) else record.id


def _preferred_document(
    documents: Sequence[SubtitleDocument], language: AnalysisLanguage
) -> SubtitleDocument | None:
    if not documents:
        return None
    if language == AnalysisLanguage.AUTO:
        return documents[0]
    requested = language.value.casefold()
    return next(
        (document for document in documents if document.language.casefold() == requested),
        documents[0],
    )


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be non-empty text")
    return value


def _required_text_list(payload: Mapping[str, object], key: str) -> list[str]:
    value = payload[key]
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise ValueError(f"{key} must be a non-empty text list")
    return cast(list[str], value)


def _required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("optional value must be an integer")
    return value


def _required_float(payload: Mapping[str, object], key: str) -> float:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{key} must be finite")
    return result


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("optional value must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("optional value must be finite")
    return result


def _safe_label(value: str | None) -> str | None:
    if value is None:
        return None
    sanitized = _sanitize_json(value)
    return str(sanitized)[:128]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
