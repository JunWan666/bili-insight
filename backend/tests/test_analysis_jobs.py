from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import pytest_asyncio
from fastapi import status
from sqlalchemy import func, select

from app.analysis import (
    AnalysisError,
    AnalysisErrorCode,
    AnalysisFailure,
    AudioAnalysis,
    CapabilityStatus,
    ContainerTechnicalInfo,
    KeyframeAnalysis,
    KeyframeArtifact,
    LocalAnalysisEngine,
    LocalContentAnalyzer,
    MediaTechnicalReport,
    SceneAnalysis,
    SceneSegment,
    SubtitleDocument,
    SubtitleSegment,
    TranscriptSource,
    subtitle_document_to_dict,
)
from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.db.models import (
    Analysis,
    Artifact,
    Job,
    JobStatus,
    JobType,
    RetainedFile,
    StreamAccessRequirement,
    StreamKind,
    Video,
    VideoPart,
)
from app.db.session import create_engine, create_schema, create_session_factory
from app.media.download import DownloadPaused
from app.media.security import MediaURLValidator
from app.providers.models import ProviderPart, ProviderSubtitle, ProviderVideo, VideoProvider
from app.schemas.analyses import (
    AnalysisFeature,
    AnalysisRequest,
    AnalysisResultStatus,
    OcrResolution,
    TranscriptEditRequest,
    TranscriptEditSegment,
)
from app.schemas.jobs import DownloadRequest, OutputContainer, ProcessingMode
from app.schemas.settings import (
    AnalysisDevice,
    AnalysisLanguage,
    AnalysisSettings,
    AppSettings,
    AsrModel,
)
from app.schemas.video import AccessMode, AccessRead, MediaStreamRead, StreamsRead
from app.services.analyses import (
    AcquiredMedia,
    AnalysisEngineOptions,
    AnalysisService,
    DownloadAnalysisMediaAcquirer,
    ProviderSubtitleService,
    _as_utc,
    _default_engine_factory,
    _document_from_value,
    _preferred_document,
    _sanitize_json,
)
from app.services.artifacts import ArtifactService
from app.services.auth import AuthService
from app.services.downloads import DownloadExecutor
from app.services.jobs import JobService
from app.services.settings import SettingsService
from app.services.videos import VideoService


class FakeMediaAnalyzer:
    def __init__(self, owner: FakeAnalysisEngine) -> None:
        self.owner = owner

    def probe(
        self,
        _: Path,
        *,
        include_keyframes: bool,
        cancellation_event: threading.Event,
    ) -> MediaTechnicalReport:
        assert include_keyframes
        if self.owner.block_media:
            cancellation_event.wait(timeout=10)
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.CANCELED,
                    message="分析任务已取消",
                    action="可重新创建任务",
                )
            )
        return MediaTechnicalReport(
            probe_name="fake-ffprobe",
            probe_version="1.2.3",
            container=ContainerTechnicalInfo(
                format_names=("mov", "mp4"),
                format_long_name="QuickTime",
                duration_seconds=4.0,
                size_bytes=12,
                bit_rate=24,
                start_time_seconds=0,
                tags={
                    "comment": "https://signed.example.invalid/media?token=secret",
                    "absolute": "C:\\private\\source.mp4",
                    "cookie": "SESSDATA=never-return-this",
                },
            ),
            video_streams=(),
            audio_streams=(),
            subtitle_streams=(),
            chapters=(),
            warnings=(),
        )


class FakeAudioAnalyzer:
    def __init__(self, owner: FakeAnalysisEngine) -> None:
        self.owner = owner

    def analyze(self, _: Path, **__: object) -> AudioAnalysis:
        if self.owner.fail_audio:
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.DEPENDENCY_UNAVAILABLE,
                    message="音频分析依赖不可用",
                    action="安装 FFmpeg 后重试",
                )
            )
        return AudioAnalysis(
            analyzer_name="fake-audio",
            analyzer_version="1.0",
            stream_index=0,
            integrated_loudness_lufs=-14,
            loudness_range_lu=5,
            sample_peak_dbfs=-1,
            true_peak_dbfs=-0.9,
            mean_volume_db=-12,
            silence_threshold_db=-50,
            minimum_silence_seconds=0.5,
            silence_intervals=(),
            loudness_curve=(),
            warnings=(),
        )


class FakeAsrAdapter:
    def __init__(self, owner: FakeAnalysisEngine) -> None:
        self.owner = owner

    def transcribe(self, _: Path, **__: object) -> SubtitleDocument:
        if self.owner.fail_asr:
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.DEPENDENCY_UNAVAILABLE,
                    message="faster-whisper 未安装",
                    action="安装语音模型后重试",
                )
            )
        return _document(TranscriptSource.ASR, "asr", "这是一段可定位的测试转写。")


class FakeOcrAdapter:
    def __init__(self, owner: FakeAnalysisEngine) -> None:
        self.owner = owner

    def recognize(self, _: Path, **__: object) -> SubtitleDocument:
        if self.owner.fail_ocr_unexpectedly:
            raise ValueError("C:\\private\\must-not-be-returned")
        return _document(TranscriptSource.OCR, "ocr", "画面中的测试文字。")


class FakeSceneAnalyzer:
    def analyze(self, _: Path, **__: object) -> SceneAnalysis:
        return SceneAnalysis(
            analyzer_name="fake-scenes",
            analyzer_version="1.0",
            threshold=0.3,
            duration_seconds=4,
            scenes=(
                SceneSegment(
                    index=1,
                    start_seconds=0,
                    end_seconds=4,
                    duration_seconds=4,
                    transition_score=None,
                ),
            ),
            average_scene_length_seconds=4,
            scene_density_per_minute=15,
            truncated=False,
            warnings=(),
        )

    def extract_keyframes(
        self,
        _: Path,
        scene_analysis: SceneAnalysis,
        output_directory: Path,
        **__: object,
    ) -> KeyframeAnalysis:
        assert scene_analysis.scenes[0].index == 1
        output = output_directory / "keyframe_0001.jpg"
        output.write_bytes(b"safe-jpeg-data")
        return KeyframeAnalysis(
            extractor_name="fake-keyframes",
            extractor_version="1.0",
            artifacts=(
                KeyframeArtifact(
                    index=1,
                    timestamp_seconds=2,
                    scene_index=1,
                    filename=output.name,
                    path=output,
                    size_bytes=output.stat().st_size,
                    sha256="08e576adf167c34db9e4c8d065aff1687bd282c297a45510a72a1d4f42c991e",
                ),
            ),
            warnings=(),
        )


class FakeAnalysisEngine:
    def __init__(self) -> None:
        self.fail_audio = False
        self.fail_asr = False
        self.fail_ocr_unexpectedly = False
        self.block_media = False
        self.media = FakeMediaAnalyzer(self)
        self.audio = FakeAudioAnalyzer(self)
        self.asr = FakeAsrAdapter(self)
        self.ocr = FakeOcrAdapter(self)
        self.scenes = FakeSceneAnalyzer()
        self.summary = LocalContentAnalyzer()

    def capabilities(self) -> tuple[CapabilityStatus, ...]:
        return (
            _capability("ffprobe"),
            _capability("ffmpeg-audio"),
            _capability("ffmpeg-scenes"),
            _capability("faster-whisper"),
            CapabilityStatus(
                component="paddleocr",
                available=False,
                version=None,
                reason_code="PADDLEOCR_NOT_INSTALLED",
                message="PaddleOCR 未安装",
                action="安装 OCR 依赖",
            ),
            _capability("local-content-summary"),
        )


class FakeAnalysisVideoService:
    def __init__(self, streams: StreamsRead) -> None:
        self.streams = streams
        self.calls: list[tuple[str, str, AccessMode, bool]] = []

    async def get_part_streams(
        self,
        video_id: str,
        part_id: str,
        access_mode: AccessMode,
        *,
        force_refresh: bool,
    ) -> StreamsRead:
        self.calls.append((video_id, part_id, access_mode, force_refresh))
        return self.streams.model_copy(update={"part_id": part_id})


class FakeAnalysisDownloadExecutor:
    def __init__(
        self,
        settings: Settings,
        artifact_service: ArtifactService,
    ) -> None:
        self.settings = settings
        self.artifact_service = artifact_service
        self.requests: list[DownloadRequest] = []
        self.discarded: list[str] = []
        self.preexisting_source_counts: list[int] = []
        self.fail_with_pause = False
        self.fail_with_error = False

    async def prepare(self, request: DownloadRequest) -> dict[str, object]:
        self.requests.append(request)
        return {
            "video_id": request.video_id,
            "part_id": request.part_id,
            "container": request.container.value,
            "output_filename": f"analysis-{request.part_id}.{request.container.value}",
        }

    async def execute(
        self,
        job: Job,
        *,
        checkpoint: Any,
        reporter: Any,
    ) -> list[Artifact]:
        existing_sources = await self.artifact_service.existing_all_for_job(
            job.id, {"video", "audio", "media"}
        )
        self.preexisting_source_counts.append(len(existing_sources))
        marker = self.settings.temp_dir.resolve() / job.id / "download.partial"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b"partial")
        if self.fail_with_pause:
            raise DownloadPaused
        if self.fail_with_error:
            raise OSError("simulated acquisition failure")
        await checkpoint.checkpoint()
        await reporter.update(phase="downloading", progress=50.0)
        payload = cast(dict[str, object], job.input_json)
        output_directory = self.settings.artifact_dir.resolve() / job.id
        output_directory.mkdir(parents=True, exist_ok=True)
        container = str(payload["container"])
        output = output_directory / str(payload["output_filename"])
        output.write_bytes(b"bounded-analysis-media")
        artifact = await self.artifact_service.create_from_file(
            job_id=job.id,
            artifact_type="audio" if container == "m4a" else "video",
            path=output,
            filename=output.name,
            mime_type="audio/mp4" if container == "m4a" else "video/x-matroska",
            media_info={"durationSeconds": 4},
        )
        return [artifact]

    async def discard_job_partials(self, job_id: str) -> None:
        self.discarded.append(job_id)
        directory = self.settings.temp_dir.resolve() / job_id
        await asyncio.to_thread(shutil.rmtree, directory, ignore_errors=True)


@dataclass(slots=True)
class AnalysisEnvironment:
    settings: Settings
    session_factory: Any
    artifact_service: ArtifactService
    job_service: JobService
    analysis_service: AnalysisService
    fake_engine: FakeAnalysisEngine
    database_engine: Any


@dataclass(frozen=True, slots=True)
class SourceFixture:
    video_id: str
    part_id: str
    artifact_id: str
    source_path: Path


@dataclass(frozen=True, slots=True)
class VideoFixture:
    video_id: str
    part_ids: tuple[str, ...]


class FakeSubtitleProvider:
    def __init__(self, subtitles: list[ProviderSubtitle]) -> None:
        self.subtitles = subtitles
        self.calls: list[tuple[ProviderVideo, ProviderPart, object | None]] = []
        self.error: Exception | None = None

    async def get_subtitles(
        self,
        video: ProviderVideo,
        part: ProviderPart,
        cookies: object | None = None,
    ) -> list[ProviderSubtitle]:
        self.calls.append((video, part, cookies))
        if self.error is not None:
            raise self.error
        return self.subtitles


class FakeSubtitleAuth:
    def __init__(self) -> None:
        self.jar = object()
        self.calls = 0

    async def cookie_jar(self) -> object:
        self.calls += 1
        return self.jar


class FakeSummarySubtitleFetcher:
    def __init__(self, document: SubtitleDocument | None) -> None:
        self.document = document
        self.calls = 0

    async def fetch(self, **_: object) -> SubtitleDocument | None:
        self.calls += 1
        return self.document


class FailOnceCleanupAcquirer:
    def __init__(self, inner: DownloadAnalysisMediaAcquirer) -> None:
        self.inner = inner
        self.cleanup_calls = 0

    async def acquire(self, **kwargs: Any) -> AcquiredMedia:
        return await self.inner.acquire(**kwargs)

    async def cleanup(self, media: AcquiredMedia) -> None:
        self.cleanup_calls += 1
        if self.cleanup_calls == 1:
            raise OSError("transient cleanup failure")
        await self.inner.cleanup(media)


class PassiveCheckpoint:
    async def checkpoint(self) -> None:
        return None


class RecordingReporter:
    def __init__(self) -> None:
        self.updates: list[tuple[str, float]] = []

    async def update(
        self,
        *,
        phase: str,
        progress: float,
        downloaded_bytes: int | None = None,
        total_bytes: int | None = None,
        automatic_attempt: int | None = None,
    ) -> None:
        assert downloaded_bytes is None
        assert total_bytes is None
        assert automatic_attempt is None
        self.updates.append((phase, progress))


@pytest_asyncio.fixture
async def analysis_environment(settings: Settings) -> Any:
    database_engine = create_engine(settings)
    await create_schema(database_engine)
    session_factory = create_session_factory(database_engine)
    artifact_service = ArtifactService(settings, session_factory)
    job_service = JobService(
        session_factory,
        artifact_service,
        cast(DownloadExecutor, object()),
        concurrency=1,
        event_interval_seconds=0.1,
    )
    fake_engine = FakeAnalysisEngine()

    def engine_factory(_: AnalysisEngineOptions) -> LocalAnalysisEngine:
        return cast(LocalAnalysisEngine, fake_engine)

    analysis_service = AnalysisService(
        settings,
        session_factory,
        artifact_service,
        job_service,
        engine_factory=cast(Any, engine_factory),
    )
    job_service.register_executor(JobType.ANALYSIS, analysis_service)
    await job_service.start()
    environment = AnalysisEnvironment(
        settings=settings,
        session_factory=session_factory,
        artifact_service=artifact_service,
        job_service=job_service,
        analysis_service=analysis_service,
        fake_engine=fake_engine,
        database_engine=database_engine,
    )
    try:
        yield environment
    finally:
        await job_service.stop()
        await database_engine.dispose()


def _capability(component: str) -> CapabilityStatus:
    return CapabilityStatus(
        component=component,
        available=True,
        version="1.0",
        reason_code=None,
        message=f"{component} 可用",
        action=None,
    )


def _document(source: TranscriptSource, prefix: str, text: str) -> SubtitleDocument:
    return SubtitleDocument(
        language="zh-CN",
        source=source,
        segments=(
            SubtitleSegment(
                start_seconds=0,
                end_seconds=2,
                text=text,
                source=source,
                language="zh-CN",
                confidence=0.91,
                evidence_id=f"{prefix}-1",
            ),
        ),
        model_name=f"fake-{prefix}",
        model_version="1.0",
        generated_at=datetime(2026, 7, 14, tzinfo=UTC),
    )


async def _seed_source(
    environment: AnalysisEnvironment,
    *,
    title: str = "测试视频",
    mime_type: str = "video/mp4",
) -> SourceFixture:
    video = Video(
        provider="bilibili",
        bvid=f"BV{time.time_ns() % 10**10:010d}",
        aid=time.time_ns() % 9_000_000_000_000_000_000,
        title=title,
        description=(
            "说明 https://private.example.invalid/a?token=secret "
            "SESSDATA=never-return C:\\private\\cookie.txt"
        ),
        cover_url="https://cover.example.invalid/signed",
        owner_name="测试 UP",
        duration=4,
        published_at=datetime(2026, 7, 14, tzinfo=UTC),
        stats={"view": 10},
        tags=["测试"],
        rights={"download": True},
        raw_metadata={"signed_url": "https://never-return.invalid"},
    )
    part = VideoPart(
        video=video,
        cid=time.time_ns() % 9_000_000_000_000_000_000,
        page_number=1,
        title="第一集",
        duration=4,
    )
    source_job = Job(
        type=JobType.DOWNLOAD,
        status=JobStatus.COMPLETED,
        phase="completed",
        progress=100,
        input_json={},
        retry_count=0,
        cancel_requested=False,
        finished_at=datetime.now(UTC),
    )
    async with environment.session_factory() as session:
        session.add_all([video, part, source_job])
        await session.flush()
        source_job.input_json = {
            "video_id": video.id,
            "part_id": part.id,
            "output_filename": "source.mp4",
        }
        await session.commit()
    source_directory = environment.settings.artifact_dir.resolve() / source_job.id
    source_directory.mkdir(parents=True, exist_ok=True)
    source_path = source_directory / "source.mp4"
    source_path.write_bytes(b"not-real-media-but-verified-by-fake-engine")
    artifact = await environment.artifact_service.create_from_file(
        job_id=source_job.id,
        artifact_type="video" if mime_type.startswith("video/") else "audio",
        path=source_path,
        filename="source.mp4",
        mime_type=mime_type,
        media_info={"durationSeconds": 4},
    )
    return SourceFixture(
        video_id=video.id,
        part_id=part.id,
        artifact_id=artifact.id,
        source_path=source_path,
    )


async def _seed_video(
    environment: AnalysisEnvironment,
    *,
    part_count: int = 1,
) -> VideoFixture:
    video = Video(
        provider="bilibili",
        bvid=f"BV{time.time_ns() % 10**10:010d}",
        aid=time.time_ns() % 9_000_000_000_000_000_000,
        title="On-demand analysis fixture",
        description="Safe provider context",
        cover_url="https://i0.hdslb.com/fixture.jpg",
        owner_name="Fixture owner",
        duration=part_count * 4,
        published_at=datetime(2026, 7, 14, tzinfo=UTC),
        stats={"view": 10},
        tags=["analysis"],
        rights={"download": True},
        raw_metadata={"fixture": True},
    )
    parts = [
        VideoPart(
            video=video,
            cid=(time.time_ns() + index) % 9_000_000_000_000_000_000,
            page_number=index + 1,
            title=f"Part {index + 1}",
            duration=4,
        )
        for index in range(part_count)
    ]
    async with environment.session_factory() as session:
        session.add(video)
        session.add_all(parts)
        await session.commit()
    return VideoFixture(video_id=video.id, part_ids=tuple(part.id for part in parts))


def _media_stream(
    *,
    kind: StreamKind,
    quality_code: int,
    bitrate: int,
    height: int | None = None,
) -> MediaStreamRead:
    return MediaStreamRead(
        id=str(uuid.uuid4()),
        kind=kind,
        quality_code=quality_code,
        quality_label=f"q{quality_code}",
        codec="avc1" if kind == StreamKind.VIDEO else "mp4a",
        container="mp4" if kind == StreamKind.VIDEO else "m4a",
        width=height * 16 // 9 if height is not None else None,
        height=height,
        fps=30 if kind == StreamKind.VIDEO else None,
        bitrate=bitrate,
        hdr_type=None,
        audio_channels=2 if kind == StreamKind.AUDIO else None,
        sample_rate=48_000 if kind == StreamKind.AUDIO else None,
        estimated_size=bitrate * 4 // 8,
        auth_required=False,
        premium_required=False,
        access_requirement=StreamAccessRequirement.NONE,
        verified_at=datetime.now(UTC),
        compatibility="native",
    )


def _analysis_streams(part_id: str) -> StreamsRead:
    return StreamsRead(
        part_id=part_id,
        video=[
            _media_stream(
                kind=StreamKind.VIDEO,
                quality_code=80,
                bitrate=1_000_000,
                height=1080,
            ),
            _media_stream(
                kind=StreamKind.VIDEO,
                quality_code=32,
                bitrate=2_000_000,
                height=480,
            ),
            _media_stream(
                kind=StreamKind.VIDEO,
                quality_code=64,
                bitrate=3_000_000,
                height=720,
            ),
        ],
        audio=[
            _media_stream(kind=StreamKind.AUDIO, quality_code=30280, bitrate=192_000),
            _media_stream(kind=StreamKind.AUDIO, quality_code=30216, bitrate=64_000),
        ],
        fetched_at=datetime.now(UTC),
        cache_hit=False,
        access=AccessRead(
            requested_mode=AccessMode.ANONYMOUS,
            actual_mode=AccessMode.ANONYMOUS,
            has_credentials=False,
            used_authentication=False,
            membership_type="none",
        ),
    )


async def _wait_for_terminal(job_service: JobService, job_id: str) -> Any:
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        job = await job_service.get(job_id)
        if job.status in {JobStatus.COMPLETED, JobStatus.CANCELED, JobStatus.FAILED}:
            return job
        await asyncio.sleep(0.025)
    raise AssertionError("analysis job did not reach a terminal state")


async def _wait_for_running(job_service: JobService, job_id: str) -> Any:
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        job = await job_service.get(job_id)
        if job.status == JobStatus.RUNNING:
            return job
        await asyncio.sleep(0.025)
    raise AssertionError("analysis job did not start")


async def _artifact_json(artifact_service: ArtifactService, artifact: Any) -> dict[str, object]:
    delivery = await artifact_service.delivery(artifact.id, None)
    return cast(
        dict[str, object],
        json.loads(await asyncio.to_thread(delivery.path.read_text, encoding="utf-8")),
    )


def _artifact_info(artifact: Any) -> dict[str, object]:
    return cast(dict[str, object], artifact.media_info or {})


async def _persisted_job_payload(
    environment: AnalysisEnvironment, job_id: str
) -> dict[str, object]:
    async with environment.session_factory() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        return cast(dict[str, object], job.input_json)


@pytest.mark.asyncio
async def test_analysis_job_keeps_successful_steps_when_optional_steps_fail(
    analysis_environment: AnalysisEnvironment,
) -> None:
    source = await _seed_source(analysis_environment)
    analysis_environment.fake_engine.fail_audio = True
    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[
                AnalysisFeature.BASIC,
                AnalysisFeature.MEDIA,
                AnalysisFeature.AUDIO,
                AnalysisFeature.SUBTITLES,
                AnalysisFeature.ASR,
                AnalysisFeature.OCR,
                AnalysisFeature.SUMMARY,
            ],
            access_mode=AccessMode.ANONYMOUS,
        )
    )
    completed = await _wait_for_terminal(analysis_environment.job_service, created.id)

    assert completed.status == JobStatus.COMPLETED
    assert completed.error_code is None
    assert all("url" not in key.casefold() for key in completed.input)
    types = {artifact.type for artifact in completed.artifacts}
    assert {"report", "transcript"}.issubset(types)
    exported_formats = {
        _artifact_info(artifact).get("format")
        for artifact in completed.artifacts
        if artifact.type == "transcript"
    }
    assert {"srt", "vtt", "txt", "json"}.issubset(exported_formats)

    manifest_artifact = next(
        artifact
        for artifact in completed.artifacts
        if _artifact_info(artifact).get("artifactRole") == "analysis_manifest"
    )
    manifest = await _artifact_json(analysis_environment.artifact_service, manifest_artifact)
    assert manifest["overallStatus"] == "partial"
    steps = cast(list[dict[str, object]], manifest["steps"])
    failures = {step["feature"]: step["error"] for step in steps if "error" in step}
    assert cast(dict[str, object], failures["audio"])["code"] == ("ANALYSIS_DEPENDENCY_UNAVAILABLE")
    assert cast(dict[str, object], failures["subtitles"])["code"] == (
        "ANALYSIS_DEPENDENCY_UNAVAILABLE"
    )

    basic_artifact = next(
        artifact
        for artifact in completed.artifacts
        if _artifact_info(artifact).get("artifactRole") == "analysis_report"
        and _artifact_info(artifact).get("analysisFeature") == "basic"
    )
    basic_delivery = await analysis_environment.artifact_service.delivery(basic_artifact.id, None)
    basic_text = await asyncio.to_thread(basic_delivery.path.read_text, encoding="utf-8")
    assert "https://" not in basic_text
    assert "SESSDATA=" not in basic_text
    assert "C:\\private" not in basic_text
    assert "<redacted>" in basic_text

    srt_artifact = next(
        artifact
        for artifact in completed.artifacts
        if artifact.type == "transcript" and _artifact_info(artifact).get("format") == "srt"
    )
    srt_delivery = await analysis_environment.artifact_service.delivery(srt_artifact.id, None)
    srt_text = await asyncio.to_thread(srt_delivery.path.read_text, encoding="utf-8")
    assert "00:00:00,000 --> 00:00:02,000" in srt_text
    assert "测试转写" in srt_text

    async with analysis_environment.session_factory() as session:
        records = list(
            (
                await session.scalars(select(Analysis).where(Analysis.video_id == source.video_id))
            ).all()
        )
    assert len(records) == 7
    assert sum(record.status == "completed" for record in records) == 5
    assert sum(record.status == "failed" for record in records) == 2

    listing = await analysis_environment.analysis_service.list(
        limit=20,
        offset=0,
        video_id=source.video_id,
        feature=AnalysisFeature.METADATA,
    )
    assert listing.total == 1
    assert listing.items[0].feature == AnalysisFeature.BASIC
    detail = await analysis_environment.analysis_service.get(listing.items[0].id)
    assert detail.result is not None
    capabilities = await analysis_environment.analysis_service.capabilities()
    assert any(
        item.feature == AnalysisFeature.OCR and not item.available for item in capabilities.items
    )


@pytest.mark.asyncio
async def test_scene_analysis_atomically_publishes_keyframes_and_report(
    analysis_environment: AnalysisEnvironment,
) -> None:
    source = await _seed_source(analysis_environment)
    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            artifact_id=source.artifact_id,
            features=[AnalysisFeature.SCENES],
            access_mode=AccessMode.AUTHENTICATED,
        )
    )
    completed = await _wait_for_terminal(analysis_environment.job_service, created.id)

    assert completed.status == JobStatus.COMPLETED
    keyframe = next(artifact for artifact in completed.artifacts if artifact.type == "keyframe")
    assert keyframe.mime_type == "image/jpeg"
    assert keyframe.checksum.startswith("sha256:")
    delivery = await analysis_environment.artifact_service.delivery(keyframe.id, None)
    assert await asyncio.to_thread(delivery.path.read_bytes) == b"safe-jpeg-data"
    job_directory = analysis_environment.settings.artifact_dir.resolve() / created.id
    assert not any(
        path.name.startswith(".analysis-staging-") or ".partial" in path.name
        for path in job_directory.rglob("*")
    )


@pytest.mark.asyncio
async def test_all_failed_analysis_keeps_manifest_and_can_retry(
    analysis_environment: AnalysisEnvironment,
) -> None:
    source = await _seed_source(analysis_environment)
    analysis_environment.fake_engine.fail_asr = True
    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.ASR],
        )
    )
    failed = await _wait_for_terminal(analysis_environment.job_service, created.id)

    assert failed.status == JobStatus.FAILED
    assert failed.error_code == "INTERNAL_ERROR"
    manifest = next(
        artifact
        for artifact in failed.artifacts
        if _artifact_info(artifact).get("artifactRole") == "analysis_manifest"
    )
    assert (await _artifact_json(analysis_environment.artifact_service, manifest))[
        "overallStatus"
    ] == "failed"

    analysis_environment.fake_engine.fail_asr = False
    retried = await analysis_environment.job_service.retry(created.id)
    completed = await _wait_for_terminal(analysis_environment.job_service, retried.id)
    assert completed.status == JobStatus.COMPLETED
    assert any(
        artifact.filename == "analysis-manifest-retry-001.json" for artifact in completed.artifacts
    )
    assert any(
        artifact.type == "transcript" and _artifact_info(artifact).get("format") == "srt"
        for artifact in completed.artifacts
    )


@pytest.mark.asyncio
async def test_analysis_cancellation_stops_thread_work_and_cleans_partials(
    analysis_environment: AnalysisEnvironment,
) -> None:
    source = await _seed_source(analysis_environment)
    analysis_environment.fake_engine.block_media = True
    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.MEDIA],
        )
    )
    await _wait_for_running(analysis_environment.job_service, created.id)
    await analysis_environment.job_service.cancel(created.id)
    canceled = await _wait_for_terminal(analysis_environment.job_service, created.id)

    assert canceled.status == JobStatus.CANCELED
    assert source.source_path.exists()
    assert not canceled.artifacts
    async with analysis_environment.session_factory() as session:
        record = await session.scalar(select(Analysis).where(Analysis.video_id == source.video_id))
    assert record is not None
    assert record.status == AnalysisResultStatus.CANCELED.value


@pytest.mark.asyncio
async def test_explicit_source_must_match_part_and_media_kind(
    analysis_environment: AnalysisEnvironment,
) -> None:
    first = await _seed_source(analysis_environment)
    second = await _seed_source(analysis_environment, title="另一个视频")
    audio_only = await _seed_source(
        analysis_environment,
        title="仅音频",
        mime_type="audio/mp4",
    )

    with pytest.raises(AppError) as mismatch:
        await analysis_environment.analysis_service.create(
            AnalysisRequest(
                video_id=second.video_id,
                part_ids=[second.part_id],
                artifact_id=first.artifact_id,
                features=[AnalysisFeature.MEDIA],
            )
        )
    assert mismatch.value.status_code == status.HTTP_409_CONFLICT

    with pytest.raises(AppError) as wrong_kind:
        await analysis_environment.analysis_service.create(
            AnalysisRequest(
                video_id=audio_only.video_id,
                part_ids=[audio_only.part_id],
                artifact_id=audio_only.artifact_id,
                features=[AnalysisFeature.SCENES],
            )
        )
    assert wrong_kind.value.status_code == status.HTTP_409_CONFLICT

    basic = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=second.video_id,
            part_ids=[second.part_id],
            features=[AnalysisFeature.BASIC],
            language=AnalysisLanguage.AUTO,
        )
    )
    assert (await _wait_for_terminal(analysis_environment.job_service, basic.id)).status == (
        JobStatus.COMPLETED
    )


@pytest.mark.asyncio
async def test_public_subtitle_history_is_preferred_and_summary_can_reuse_it(
    analysis_environment: AnalysisEnvironment,
) -> None:
    source = await _seed_source(analysis_environment)
    public_document = _document(
        TranscriptSource.PUBLIC_SUBTITLE,
        "public",
        "平台公开字幕提供了带时间戳的可靠证据。",
    )
    historical = Analysis(
        video_id=source.video_id,
        part_id=source.part_id,
        analysis_type=AnalysisFeature.SUBTITLES.value,
        status=AnalysisResultStatus.COMPLETED.value,
        result_json={"document": subtitle_document_to_dict(public_document)},
        model_name=public_document.model_name,
        model_version=public_document.model_version,
        parameters={"jobId": "historical"},
    )
    async with analysis_environment.session_factory() as session:
        session.add(historical)
        await session.commit()

    subtitle_job = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.SUBTITLES],
            language=AnalysisLanguage.AUTO,
        )
    )
    subtitle_result = await _wait_for_terminal(analysis_environment.job_service, subtitle_job.id)
    assert subtitle_result.status == JobStatus.COMPLETED
    subtitle_json = next(
        artifact
        for artifact in subtitle_result.artifacts
        if artifact.type == "subtitle" and _artifact_info(artifact).get("format") == "json"
    )
    exported = await _artifact_json(analysis_environment.artifact_service, subtitle_json)
    assert exported["source"] == "public_subtitle"

    summary_job = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.SUMMARY],
        )
    )
    summary_result = await _wait_for_terminal(analysis_environment.job_service, summary_job.id)
    assert summary_result.status == JobStatus.COMPLETED
    summary_artifact = next(
        artifact
        for artifact in summary_result.artifacts
        if _artifact_info(artifact).get("artifactRole") == "analysis_report"
        and _artifact_info(artifact).get("analysisFeature") == "summary"
    )
    summary_report = await _artifact_json(analysis_environment.artifact_service, summary_artifact)
    report = cast(dict[str, object], summary_report["report"])
    assert "可靠证据" in str(report["summary"])


@pytest.mark.asyncio
async def test_transcript_edit_creates_traced_derivative_and_preserves_original_artifacts(
    analysis_environment: AnalysisEnvironment,
) -> None:
    source = await _seed_source(analysis_environment)
    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.ASR],
        )
    )
    completed = await _wait_for_terminal(analysis_environment.job_service, created.id)
    assert completed.status == JobStatus.COMPLETED
    listing = await analysis_environment.analysis_service.list(
        limit=10,
        offset=0,
        video_id=source.video_id,
        part_id=source.part_id,
        feature=AnalysisFeature.ASR,
    )
    original = listing.items[0]
    assert original.result is not None
    original_artifact_ids = cast(list[str], original.result["artifactIds"])
    original_bytes: dict[str, bytes] = {}
    for artifact_id in original_artifact_ids:
        delivery = await analysis_environment.artifact_service.delivery(artifact_id, None)
        original_bytes[artifact_id] = await asyncio.to_thread(delivery.path.read_bytes)

    edited = await analysis_environment.analysis_service.edit_transcript(
        original.id,
        TranscriptEditRequest(
            segments=[
                TranscriptEditSegment(
                    start_seconds=0,
                    end_seconds=1.5,
                    text="人工校正后的 <script>不会执行</script> 第一段。",
                ),
                TranscriptEditSegment(
                    start_seconds=1.5,
                    end_seconds=3.5,
                    text="第二句仍然保留可定位时间戳。",
                ),
            ]
        ),
    )

    assert edited.id != original.id
    assert edited.feature == AnalysisFeature.ASR
    assert edited.model_name == "manual-transcript-editor"
    assert edited.parameters["editedFromAnalysisId"] == original.id
    assert edited.parameters["editRevision"] == 1
    assert edited.result is not None
    document = cast(dict[str, object], edited.result["document"])
    assert document["source"] == "edited"
    edited_segments = cast(list[dict[str, object]], document["segments"])
    assert [item["text"] for item in edited_segments] == [
        "人工校正后的 <script>不会执行</script> 第一段。",
        "第二句仍然保留可定位时间戳。",
    ]
    edited_artifact_ids = cast(list[str], edited.result["artifactIds"])
    assert len(edited_artifact_ids) == 5
    edited_artifacts = [
        await analysis_environment.artifact_service.get(artifact_id)
        for artifact_id in edited_artifact_ids
    ]
    assert {
        cast(dict[str, object], item.media_info or {}).get("format") for item in edited_artifacts
    } >= {"srt", "vtt", "txt", "json"}
    assert set(original_artifact_ids).isdisjoint(edited_artifact_ids)
    srt = next(
        item
        for item in edited_artifacts
        if cast(dict[str, object], item.media_info or {}).get("format") == "srt"
    )
    srt_delivery = await analysis_environment.artifact_service.delivery(srt.id, None)
    assert "人工校正" in await asyncio.to_thread(srt_delivery.path.read_text, encoding="utf-8")

    unchanged = await analysis_environment.analysis_service.get(original.id)
    assert unchanged.result == original.result
    for artifact_id, expected in original_bytes.items():
        delivery = await analysis_environment.artifact_service.delivery(artifact_id, None)
        assert await asyncio.to_thread(delivery.path.read_bytes) == expected


@pytest.mark.asyncio
async def test_transcript_edit_rolls_back_all_derivative_artifacts_on_batch_failure(
    analysis_environment: AnalysisEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = await _seed_source(analysis_environment)
    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.ASR],
        )
    )
    completed = await _wait_for_terminal(analysis_environment.job_service, created.id)
    listing = await analysis_environment.analysis_service.list(
        limit=10,
        offset=0,
        video_id=source.video_id,
        part_id=source.part_id,
        feature=AnalysisFeature.ASR,
    )
    original = listing.items[0]
    before_ids = {artifact.id for artifact in completed.artifacts}
    publish = analysis_environment.analysis_service._publish_bytes
    call_count = 0

    async def fail_third_publish(**kwargs: Any) -> Artifact:
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise OSError("simulated edited export failure")
        return await publish(**kwargs)

    monkeypatch.setattr(
        analysis_environment.analysis_service,
        "_publish_bytes",
        fail_third_publish,
    )
    with pytest.raises(OSError, match="simulated edited export failure"):
        await analysis_environment.analysis_service.edit_transcript(
            original.id,
            TranscriptEditRequest(
                segments=[
                    TranscriptEditSegment(
                        start_seconds=0,
                        end_seconds=2,
                        text="不会留下半成品的编辑文本。",
                    )
                ]
            ),
        )

    async with analysis_environment.session_factory() as session:
        artifact_ids = set(
            (await session.scalars(select(Artifact.id).where(Artifact.job_id == created.id))).all()
        )
        analysis_count = await session.scalar(
            select(func.count(Analysis.id)).where(
                Analysis.video_id == source.video_id,
                Analysis.analysis_type == AnalysisFeature.ASR.value,
            )
        )
    assert artifact_ids == before_ids
    assert analysis_count == 1


@pytest.mark.asyncio
async def test_transcript_edit_and_history_cleanup_are_serialized(
    analysis_environment: AnalysisEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = await _seed_source(analysis_environment)
    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.ASR],
        )
    )
    assert (await _wait_for_terminal(analysis_environment.job_service, created.id)).status == (
        JobStatus.COMPLETED
    )
    listing = await analysis_environment.analysis_service.list(
        limit=10,
        offset=0,
        video_id=source.video_id,
        part_id=source.part_id,
        feature=AnalysisFeature.ASR,
    )
    original = listing.items[0]
    old = datetime(2020, 1, 1, tzinfo=UTC)
    current = datetime(2026, 7, 14, tzinfo=UTC)
    async with analysis_environment.session_factory() as session:
        job = await session.get(Job, created.id)
        assert job is not None
        job.created_at = old
        job.started_at = old
        job.finished_at = old
        job.updated_at = old
        await session.commit()

    publish_started = asyncio.Event()
    release_edit = asyncio.Event()
    publish = analysis_environment.analysis_service._publish_bytes
    first_publish = True

    async def blocking_first_publish(**kwargs: Any) -> Artifact:
        nonlocal first_publish
        if first_publish:
            first_publish = False
            publish_started.set()
            await release_edit.wait()
        return await publish(**kwargs)

    monkeypatch.setattr(
        analysis_environment.analysis_service,
        "_publish_bytes",
        blocking_first_publish,
    )
    edit_task = asyncio.create_task(
        analysis_environment.analysis_service.edit_transcript(
            original.id,
            TranscriptEditRequest(
                segments=[
                    TranscriptEditSegment(
                        start_seconds=0,
                        end_seconds=2,
                        text="与历史清理串行提交的人工修订。",
                    )
                ]
            ),
        )
    )
    await asyncio.wait_for(publish_started.wait(), timeout=2)
    cleanup_task = asyncio.create_task(
        analysis_environment.job_service.run_maintenance(
            artifact_cleanup_days=None,
            history_retention_days=30,
            now=current,
        )
    )
    try:
        await asyncio.sleep(0)
        assert not cleanup_task.done()
    finally:
        release_edit.set()

    edited = await edit_task
    cleanup = await cleanup_task
    assert cleanup["historyJobs"] == 1
    assert cleanup["historyAnalyses"] == 2
    assert edited.result is not None
    edited_artifact_ids = cast(list[str], edited.result["artifactIds"])
    assert edited_artifact_ids
    for artifact_id in edited_artifact_ids:
        retained = await analysis_environment.artifact_service.get(artifact_id)
        assert retained.retained is True
        assert retained.job_id is None
    async with analysis_environment.session_factory() as session:
        assert await session.get(Job, created.id) is None
        assert await session.get(Analysis, original.id) is None
        assert await session.get(Analysis, edited.id) is None
        assert (
            await session.scalar(
                select(func.count(Artifact.id)).where(Artifact.job_id == created.id)
            )
            == 0
        )
        assert await session.scalar(select(func.count(RetainedFile.id))) >= len(edited_artifact_ids)


@pytest.mark.asyncio
async def test_summary_only_collects_metadata_public_subtitle_and_saved_keyframe_evidence(
    analysis_environment: AnalysisEnvironment,
) -> None:
    source = await _seed_source(analysis_environment)
    scene_job = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            artifact_id=source.artifact_id,
            features=[AnalysisFeature.SCENES],
        )
    )
    assert (await _wait_for_terminal(analysis_environment.job_service, scene_job.id)).status == (
        JobStatus.COMPLETED
    )
    fetcher = FakeSummarySubtitleFetcher(
        _document(
            TranscriptSource.PUBLIC_SUBTITLE,
            "public-summary",
            "公开字幕让单独摘要也有可定位证据。",
        )
    )
    analysis_environment.analysis_service.subtitle_fetcher = fetcher

    summary_job = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.SUMMARY],
        )
    )
    completed = await _wait_for_terminal(analysis_environment.job_service, summary_job.id)
    assert completed.status == JobStatus.COMPLETED
    assert fetcher.calls == 1
    summary_artifact = next(
        artifact
        for artifact in completed.artifacts
        if _artifact_info(artifact).get("analysisFeature") == "summary"
    )
    payload = await _artifact_json(analysis_environment.artifact_service, summary_artifact)
    report = cast(dict[str, object], payload["report"])
    assert set(cast(list[str], report["inputSources"])) >= {
        "metadata",
        "public_subtitle",
        "scene",
        "keyframe",
    }
    assert report["coverage"] == "text_and_visual_evidence"
    assert cast(dict[str, object], report["inputDetails"])["metadataSnapshotCount"] >= 1
    visual = cast(list[dict[str, object]], report["visualEvidence"])
    assert any(item.get("artifactId") for item in visual if item["source"] == "keyframe")


@pytest.mark.asyncio
async def test_summary_only_without_text_is_an_explicit_metadata_limited_result(
    analysis_environment: AnalysisEnvironment,
) -> None:
    source = await _seed_source(analysis_environment)
    fetcher = FakeSummarySubtitleFetcher(None)
    analysis_environment.analysis_service.subtitle_fetcher = fetcher
    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.SUMMARY],
        )
    )
    completed = await _wait_for_terminal(analysis_environment.job_service, created.id)
    assert completed.status == JobStatus.COMPLETED
    artifact = next(
        item
        for item in completed.artifacts
        if _artifact_info(item).get("analysisFeature") == "summary"
    )
    payload = await _artifact_json(analysis_environment.artifact_service, artifact)
    report = cast(dict[str, object], payload["report"])
    assert report["coverage"] == "metadata_only"
    assert "无法可靠推断" in str(report["summary"])
    assert cast(list[object], report["summarySentences"])
    assert any("未请求媒体" in str(item) for item in cast(list[object], report["warnings"]))


@pytest.mark.asyncio
async def test_completed_steps_are_reused_during_recovery_without_reanalysis(
    analysis_environment: AnalysisEnvironment,
) -> None:
    source = await _seed_source(analysis_environment)
    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.ASR],
        )
    )
    completed = await _wait_for_terminal(analysis_environment.job_service, created.id)
    assert completed.status == JobStatus.COMPLETED

    analysis_environment.fake_engine.fail_asr = True
    async with analysis_environment.session_factory() as session:
        job = await session.get(Job, created.id)
        assert job is not None
        session.expunge(job)
    job.retry_count = 2
    reporter = RecordingReporter()
    reused = await analysis_environment.analysis_service.execute(
        job,
        checkpoint=PassiveCheckpoint(),
        reporter=reporter,
    )

    assert any(
        artifact.type == "transcript" and _artifact_info(artifact).get("format") == "srt"
        for artifact in reused
    )
    assert any(artifact.filename == "analysis-manifest-retry-002.json" for artifact in reused)
    assert reporter.updates[-1] == ("completed", 100.0)


@pytest.mark.asyncio
async def test_unexpected_step_error_is_sanitized_and_other_result_survives(
    analysis_environment: AnalysisEnvironment,
) -> None:
    source = await _seed_source(analysis_environment)
    analysis_environment.fake_engine.fail_ocr_unexpectedly = True
    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.BASIC, AnalysisFeature.OCR],
        )
    )
    completed = await _wait_for_terminal(analysis_environment.job_service, created.id)

    assert completed.status == JobStatus.COMPLETED
    manifest = next(
        artifact
        for artifact in completed.artifacts
        if _artifact_info(artifact).get("artifactRole") == "analysis_manifest"
    )
    manifest_text = json.dumps(
        await _artifact_json(analysis_environment.artifact_service, manifest),
        ensure_ascii=False,
    )
    assert "ANALYSIS_STEP_FAILED" in manifest_text
    assert "C:\\private" not in manifest_text


@pytest.mark.asyncio
async def test_source_and_record_not_found_errors_are_actionable(
    analysis_environment: AnalysisEnvironment,
) -> None:
    source = await _seed_source(analysis_environment)
    missing_id = "123e4567-e89b-42d3-a456-426614174099"

    with pytest.raises(AppError) as missing_part:
        await analysis_environment.analysis_service.create(
            AnalysisRequest(
                video_id=source.video_id,
                part_ids=[missing_id],
                features=[AnalysisFeature.BASIC],
            )
        )
    assert missing_part.value.status_code == status.HTTP_404_NOT_FOUND

    with pytest.raises(AppError) as missing_artifact:
        await analysis_environment.analysis_service.create(
            AnalysisRequest(
                video_id=source.video_id,
                part_ids=[source.part_id],
                artifact_id=missing_id,
                features=[AnalysisFeature.MEDIA],
            )
        )
    assert missing_artifact.value.status_code == status.HTTP_404_NOT_FOUND

    source.source_path.unlink()
    with pytest.raises(AppError) as no_usable_media:
        await analysis_environment.analysis_service.create(
            AnalysisRequest(
                video_id=source.video_id,
                part_ids=[source.part_id],
                features=[AnalysisFeature.MEDIA],
            )
        )
    assert no_usable_media.value.status_code == status.HTTP_409_CONFLICT

    with pytest.raises(AppError) as missing_record:
        await analysis_environment.analysis_service.get(missing_id)
    assert missing_record.value.status_code == status.HTTP_404_NOT_FOUND


def test_analysis_media_selection_uses_bounded_target_quality() -> None:
    streams = _analysis_streams(str(uuid.uuid4()))

    media_request = DownloadAnalysisMediaAcquirer._analysis_download_request(
        video_id=str(uuid.uuid4()),
        part_id=streams.part_id,
        streams=streams,
        features=[AnalysisFeature.MEDIA],
        access_mode=AccessMode.ANONYMOUS,
        ocr_resolution=OcrResolution.BALANCED,
    )
    selected_media_video = next(
        item for item in streams.video if item.id == media_request.video_stream_id
    )
    selected_media_audio = next(
        item for item in streams.audio if item.id == media_request.audio_stream_id
    )
    assert selected_media_video.height == 480
    assert selected_media_audio.bitrate == 64_000
    assert media_request.reuse_existing is False

    detail_request = DownloadAnalysisMediaAcquirer._analysis_download_request(
        video_id=str(uuid.uuid4()),
        part_id=streams.part_id,
        streams=streams,
        features=[AnalysisFeature.OCR],
        access_mode=AccessMode.AUTHENTICATED,
        ocr_resolution=OcrResolution.DETAIL,
    )
    selected_detail_video = next(
        item for item in streams.video if item.id == detail_request.video_stream_id
    )
    assert selected_detail_video.height == 1080
    assert detail_request.audio_stream_id == "none"

    asr_request = DownloadAnalysisMediaAcquirer._analysis_download_request(
        video_id=str(uuid.uuid4()),
        part_id=streams.part_id,
        streams=streams,
        features=[AnalysisFeature.ASR],
        access_mode=AccessMode.ANONYMOUS,
        ocr_resolution=OcrResolution.ECONOMY,
    )
    selected_asr_audio = next(
        item for item in streams.audio if item.id == asr_request.audio_stream_id
    )
    assert asr_request.video_stream_id is None
    assert selected_asr_audio.bitrate == 64_000

    flac_streams = streams.model_copy(
        update={
            "audio": [streams.audio[1].model_copy(update={"codec": "FLAC", "container": "m4s"})]
        }
    )
    flac_asr_request = DownloadAnalysisMediaAcquirer._analysis_download_request(
        video_id=str(uuid.uuid4()),
        part_id=flac_streams.part_id,
        streams=flac_streams,
        features=[AnalysisFeature.ASR],
        access_mode=AccessMode.ANONYMOUS,
        ocr_resolution=OcrResolution.ECONOMY,
    )
    assert flac_asr_request.container == OutputContainer.M4A
    assert flac_asr_request.processing_mode == ProcessingMode.TRANSCODE


@pytest.mark.asyncio
async def test_on_demand_media_is_acquired_per_part_without_persisted_child_jobs(
    analysis_environment: AnalysisEnvironment,
) -> None:
    fixture = await _seed_video(analysis_environment, part_count=2)
    streams = _analysis_streams(fixture.part_ids[0])
    video_service = FakeAnalysisVideoService(streams)
    executor = FakeAnalysisDownloadExecutor(
        analysis_environment.settings,
        analysis_environment.artifact_service,
    )
    acquirer = DownloadAnalysisMediaAcquirer(
        analysis_environment.session_factory,
        cast(VideoService, video_service),
        cast(DownloadExecutor, executor),
        analysis_environment.artifact_service,
    )
    analysis_environment.analysis_service.media_acquirer = acquirer

    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=fixture.video_id,
            part_ids=list(fixture.part_ids),
            features=[AnalysisFeature.MEDIA],
        )
    )
    completed = await _wait_for_terminal(analysis_environment.job_service, created.id)

    assert completed.status == JobStatus.COMPLETED
    assert [call[1] for call in video_service.calls] == list(fixture.part_ids)
    assert all(call[3] is True for call in video_service.calls)
    assert len(executor.requests) == 2
    assert executor.preexisting_source_counts == [0, 0]
    for request in executor.requests:
        selected_video = next(item for item in streams.video if item.id == request.video_stream_id)
        selected_audio = next(item for item in streams.audio if item.id == request.audio_stream_id)
        assert selected_video.height == 480
        assert selected_audio.bitrate == 64_000
    assert all(artifact.type == "report" for artifact in completed.artifacts)
    assert not (analysis_environment.settings.temp_dir / created.id).exists()

    async with analysis_environment.session_factory() as session:
        job_count = int(await session.scalar(select(func.count(Job.id))) or 0)
        source_count = int(
            await session.scalar(
                select(func.count(Artifact.id)).where(
                    Artifact.job_id == created.id,
                    Artifact.type.in_(["video", "audio", "media"]),
                )
            )
            or 0
        )
    assert job_count == 1
    assert source_count == 0


@pytest.mark.asyncio
async def test_existing_completed_media_is_preferred_over_on_demand_acquisition(
    analysis_environment: AnalysisEnvironment,
) -> None:
    source = await _seed_source(analysis_environment)
    video_service = FakeAnalysisVideoService(_analysis_streams(source.part_id))
    executor = FakeAnalysisDownloadExecutor(
        analysis_environment.settings,
        analysis_environment.artifact_service,
    )
    analysis_environment.analysis_service.media_acquirer = DownloadAnalysisMediaAcquirer(
        analysis_environment.session_factory,
        cast(VideoService, video_service),
        cast(DownloadExecutor, executor),
        analysis_environment.artifact_service,
    )

    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.MEDIA],
        )
    )
    completed = await _wait_for_terminal(analysis_environment.job_service, created.id)

    assert completed.status == JobStatus.COMPLETED
    assert not video_service.calls
    assert not executor.requests
    assert source.source_path.exists()


@pytest.mark.asyncio
async def test_transient_source_cleanup_failure_does_not_replace_analysis_results(
    analysis_environment: AnalysisEnvironment,
) -> None:
    fixture = await _seed_video(analysis_environment)
    streams = _analysis_streams(fixture.part_ids[0])
    executor = FakeAnalysisDownloadExecutor(
        analysis_environment.settings,
        analysis_environment.artifact_service,
    )
    inner = DownloadAnalysisMediaAcquirer(
        analysis_environment.session_factory,
        cast(VideoService, FakeAnalysisVideoService(streams)),
        cast(DownloadExecutor, executor),
        analysis_environment.artifact_service,
    )
    flaky = FailOnceCleanupAcquirer(inner)
    analysis_environment.analysis_service.media_acquirer = cast(Any, flaky)

    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=fixture.video_id,
            part_ids=[fixture.part_ids[0]],
            features=[AnalysisFeature.MEDIA],
        )
    )
    completed = await _wait_for_terminal(analysis_environment.job_service, created.id)

    assert completed.status == JobStatus.COMPLETED
    assert flaky.cleanup_calls == 2
    assert any(
        _artifact_info(artifact).get("artifactRole") == "analysis_manifest"
        for artifact in completed.artifacts
    )
    async with analysis_environment.session_factory() as session:
        source_count = int(
            await session.scalar(
                select(func.count(Artifact.id)).where(
                    Artifact.job_id == created.id,
                    Artifact.type.in_(["video", "audio", "media"]),
                )
            )
            or 0
        )
    assert source_count == 0


@pytest.mark.asyncio
async def test_analysis_storage_reconfiguration_is_rejected_while_active(
    analysis_environment: AnalysisEnvironment,
) -> None:
    source = await _seed_source(analysis_environment)
    analysis_environment.fake_engine.block_media = True
    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.MEDIA],
        )
    )
    await _wait_for_running(analysis_environment.job_service, created.id)
    new_artifact_root = analysis_environment.settings.data_dir / "runtime-artifacts"

    with pytest.raises(RuntimeError):
        await analysis_environment.analysis_service.reconfigure_storage(new_artifact_root)

    await analysis_environment.job_service.cancel(created.id)
    canceled = await _wait_for_terminal(analysis_environment.job_service, created.id)
    assert canceled.status == JobStatus.CANCELED
    await analysis_environment.analysis_service.reconfigure_storage(new_artifact_root)
    assert analysis_environment.analysis_service.artifact_root == new_artifact_root.resolve()
    assert new_artifact_root.is_dir()

    subtitle_service = ProviderSubtitleService(
        analysis_environment.settings,
        analysis_environment.session_factory,
        cast(VideoProvider, FakeSubtitleProvider([])),
        cast(AuthService, FakeSubtitleAuth()),
    )
    new_temp_root = analysis_environment.settings.data_dir / "runtime-temp"
    await subtitle_service.reconfigure_temp_root(new_temp_root)
    assert subtitle_service.temp_root == new_temp_root.resolve()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["pause", "error"])
async def test_media_acquisition_discards_parent_partials_on_failure(
    analysis_environment: AnalysisEnvironment,
    failure: str,
) -> None:
    fixture = await _seed_video(analysis_environment)
    streams = _analysis_streams(fixture.part_ids[0])
    video_service = FakeAnalysisVideoService(streams)
    executor = FakeAnalysisDownloadExecutor(
        analysis_environment.settings,
        analysis_environment.artifact_service,
    )
    executor.fail_with_pause = failure == "pause"
    executor.fail_with_error = failure == "error"
    acquirer = DownloadAnalysisMediaAcquirer(
        analysis_environment.session_factory,
        cast(VideoService, video_service),
        cast(DownloadExecutor, executor),
        analysis_environment.artifact_service,
    )
    parent = Job(
        id=str(uuid.uuid4()),
        type=JobType.ANALYSIS,
        status=JobStatus.RUNNING,
        phase="analysis_preparing",
        progress=1,
        input_json={},
        retry_count=0,
        cancel_requested=False,
        started_at=datetime.now(UTC),
    )

    expected_error = DownloadPaused if failure == "pause" else OSError
    with pytest.raises(expected_error):
        await acquirer.acquire(
            parent_job=parent,
            video_id=fixture.video_id,
            part_id=fixture.part_ids[0],
            features=[AnalysisFeature.ASR],
            access_mode=AccessMode.ANONYMOUS,
            ocr_resolution=OcrResolution.ECONOMY,
            checkpoint=PassiveCheckpoint(),
            reporter=RecordingReporter(),
        )

    assert executor.discarded.count(parent.id) >= 2
    assert not (analysis_environment.settings.temp_dir / parent.id).exists()


@pytest.mark.asyncio
async def test_media_acquisition_recovers_matching_crash_source(
    analysis_environment: AnalysisEnvironment,
) -> None:
    fixture = await _seed_video(analysis_environment)
    parent = Job(
        id=str(uuid.uuid4()),
        type=JobType.ANALYSIS,
        status=JobStatus.RUNNING,
        phase="analysis_preparing",
        progress=1,
        input_json={},
        retry_count=0,
        cancel_requested=False,
        started_at=datetime.now(UTC),
    )
    async with analysis_environment.session_factory() as session:
        session.add(parent)
        await session.commit()
    source_directory = analysis_environment.settings.artifact_dir / parent.id
    source_directory.mkdir(parents=True, exist_ok=True)
    source_path = source_directory / "recovered.m4a"
    source_path.write_bytes(b"recovered-analysis-audio")
    artifact = await analysis_environment.artifact_service.create_from_file(
        job_id=parent.id,
        artifact_type="audio",
        path=source_path,
        filename=source_path.name,
        mime_type="audio/mp4",
        media_info={
            "artifactRole": "analysis_source",
            "analysisJobId": parent.id,
            "videoId": fixture.video_id,
            "partId": fixture.part_ids[0],
        },
    )
    video_service = FakeAnalysisVideoService(_analysis_streams(fixture.part_ids[0]))
    executor = FakeAnalysisDownloadExecutor(
        analysis_environment.settings,
        analysis_environment.artifact_service,
    )
    acquirer = DownloadAnalysisMediaAcquirer(
        analysis_environment.session_factory,
        cast(VideoService, video_service),
        cast(DownloadExecutor, executor),
        analysis_environment.artifact_service,
    )

    recovered = await acquirer.acquire(
        parent_job=parent,
        video_id=fixture.video_id,
        part_id=fixture.part_ids[0],
        features=[AnalysisFeature.ASR],
        access_mode=AccessMode.ANONYMOUS,
        ocr_resolution=OcrResolution.ECONOMY,
        checkpoint=PassiveCheckpoint(),
        reporter=RecordingReporter(),
    )

    assert recovered.artifact_id == artifact.id
    assert recovered.path == source_path.resolve()
    assert not video_service.calls
    assert not executor.requests
    await acquirer.cleanup(recovered)
    with pytest.raises(AppError):
        await analysis_environment.artifact_service.delivery(artifact.id, None)
    assert not source_path.exists()


@pytest.mark.asyncio
async def test_media_acquisition_discards_untagged_crash_output_before_next_part(
    analysis_environment: AnalysisEnvironment,
) -> None:
    fixture = await _seed_video(analysis_environment)
    parent = Job(
        id=str(uuid.uuid4()),
        type=JobType.ANALYSIS,
        status=JobStatus.RUNNING,
        phase="analysis_preparing",
        progress=1,
        input_json={},
        retry_count=0,
        cancel_requested=False,
        started_at=datetime.now(UTC),
    )
    async with analysis_environment.session_factory() as session:
        session.add(parent)
        await session.commit()
    source_directory = analysis_environment.settings.artifact_dir / parent.id
    source_directory.mkdir(parents=True, exist_ok=True)
    stale_path = source_directory / "unlabeled-previous-part.mkv"
    stale_path.write_bytes(b"cannot-be-safely-associated-with-a-part")
    stale = await analysis_environment.artifact_service.create_from_file(
        job_id=parent.id,
        artifact_type="video",
        path=stale_path,
        filename=stale_path.name,
        mime_type="video/x-matroska",
        media_info={"durationSeconds": 4},
    )
    streams = _analysis_streams(fixture.part_ids[0])
    video_service = FakeAnalysisVideoService(streams)
    executor = FakeAnalysisDownloadExecutor(
        analysis_environment.settings,
        analysis_environment.artifact_service,
    )
    acquirer = DownloadAnalysisMediaAcquirer(
        analysis_environment.session_factory,
        cast(VideoService, video_service),
        cast(DownloadExecutor, executor),
        analysis_environment.artifact_service,
    )

    acquired = await acquirer.acquire(
        parent_job=parent,
        video_id=fixture.video_id,
        part_id=fixture.part_ids[0],
        features=[AnalysisFeature.MEDIA],
        access_mode=AccessMode.ANONYMOUS,
        ocr_resolution=OcrResolution.ECONOMY,
        checkpoint=PassiveCheckpoint(),
        reporter=RecordingReporter(),
    )

    assert acquired.artifact_id != stale.id
    assert executor.preexisting_source_counts == [0]
    assert len(video_service.calls) == 1
    with pytest.raises(AppError):
        await analysis_environment.artifact_service.delivery(stale.id, None)
    assert not stale_path.exists()
    await acquirer.cleanup(acquired)


@pytest.mark.asyncio
async def test_provider_subtitles_use_auth_only_for_metadata_and_pinned_cdn(
    analysis_environment: AnalysisEnvironment,
) -> None:
    fixture = await _seed_video(analysis_environment)
    provider = FakeSubtitleProvider(
        [
            ProviderSubtitle("en", "en", "English", "https://aisubtitle.hdslb.com/en.json"),
            ProviderSubtitle("zh", "zh-CN", "Chinese", "https://aisubtitle.hdslb.com/zh.json"),
        ]
    )
    auth = FakeSubtitleAuth()
    requests: list[httpx.Request] = []

    async def resolver(host: str, port: int) -> list[str]:
        assert host == "aisubtitle.hdslb.com"
        assert port == 443
        return ["8.8.8.8"]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"body": [{"from": 0, "to": 1.5, "content": "Pinned subtitle"}]},
            request=request,
        )

    subtitle_service = ProviderSubtitleService(
        analysis_environment.settings,
        analysis_environment.session_factory,
        cast(VideoProvider, provider),
        cast(AuthService, auth),
        transport=httpx.MockTransport(handler),
        validator=MediaURLValidator(("hdslb.com",), resolver=resolver),
    )
    document = await subtitle_service.fetch(
        video_id=fixture.video_id,
        part_id=fixture.part_ids[0],
        language=AnalysisLanguage.CHINESE_SIMPLIFIED,
        access_mode=AccessMode.AUTHENTICATED,
        checkpoint=PassiveCheckpoint(),
    )

    assert document is not None
    assert document.language == "zh-CN"
    assert document.segments[0].text == "Pinned subtitle"
    assert auth.calls == 1
    assert provider.calls[0][2] is auth.jar
    assert provider.calls[0][0].parts[0].cid == provider.calls[0][1].cid
    assert len(requests) == 1
    request = requests[0]
    assert request.url.host == "8.8.8.8"
    assert request.url.path == "/zh.json"
    assert request.headers["host"] == "aisubtitle.hdslb.com"
    assert request.extensions["sni_hostname"] == "aisubtitle.hdslb.com"
    assert "cookie" not in request.headers
    assert not list(analysis_environment.settings.temp_dir.glob("subtitle-*.json"))

    analysis_environment.analysis_service.subtitle_fetcher = subtitle_service
    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=fixture.video_id,
            part_ids=[fixture.part_ids[0]],
            features=[AnalysisFeature.SUBTITLES],
            language=AnalysisLanguage.CHINESE_SIMPLIFIED,
        )
    )
    completed = await _wait_for_terminal(analysis_environment.job_service, created.id)
    assert completed.status == JobStatus.COMPLETED
    assert {artifact.type for artifact in completed.artifacts} == {"report", "subtitle"}
    exports = [
        artifact
        for artifact in completed.artifacts
        if _artifact_info(artifact).get("artifactRole") == "analysis_text_export"
    ]
    assert {str(_artifact_info(artifact)["format"]) for artifact in exports} == {
        "srt",
        "vtt",
        "txt",
        "json",
    }

    basic_job = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=fixture.video_id,
            part_ids=[fixture.part_ids[0]],
            features=[AnalysisFeature.BASIC],
        )
    )
    basic_completed = await _wait_for_terminal(analysis_environment.job_service, basic_job.id)
    basic_artifact = next(
        artifact
        for artifact in basic_completed.artifacts
        if _artifact_info(artifact).get("analysisFeature") == "basic"
    )
    basic_report = await _artifact_json(analysis_environment.artifact_service, basic_artifact)
    assert basic_report["subtitleAvailability"] == "available"


@pytest.mark.asyncio
async def test_basic_subtitle_availability_distinguishes_absent_and_unknown(
    analysis_environment: AnalysisEnvironment,
) -> None:
    fixture = await _seed_video(analysis_environment, part_count=2)
    provider = FakeSubtitleProvider([])
    subtitle_service = ProviderSubtitleService(
        analysis_environment.settings,
        analysis_environment.session_factory,
        cast(VideoProvider, provider),
        cast(AuthService, FakeSubtitleAuth()),
    )
    analysis_environment.analysis_service.subtitle_fetcher = subtitle_service

    unavailable_job = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=fixture.video_id,
            part_ids=[fixture.part_ids[0]],
            features=[AnalysisFeature.BASIC],
        )
    )
    unavailable = await _wait_for_terminal(analysis_environment.job_service, unavailable_job.id)
    unavailable_artifact = next(
        artifact
        for artifact in unavailable.artifacts
        if _artifact_info(artifact).get("analysisFeature") == "basic"
    )
    unavailable_report = await _artifact_json(
        analysis_environment.artifact_service, unavailable_artifact
    )
    assert unavailable_report["subtitleAvailability"] == "unavailable"

    provider.error = AppError(
        ErrorCode.UPSTREAM_NETWORK,
        "subtitle metadata unavailable",
        action="retry later",
        status_code=status.HTTP_502_BAD_GATEWAY,
    )
    unknown_job = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=fixture.video_id,
            part_ids=[fixture.part_ids[1]],
            features=[AnalysisFeature.BASIC],
        )
    )
    unknown = await _wait_for_terminal(analysis_environment.job_service, unknown_job.id)
    unknown_artifact = next(
        artifact
        for artifact in unknown.artifacts
        if _artifact_info(artifact).get("analysisFeature") == "basic"
    )
    unknown_report = await _artifact_json(analysis_environment.artifact_service, unknown_artifact)
    assert unknown_report["subtitleAvailability"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("response_kind", ["redirect", "malformed", "deep", "oversized"])
async def test_provider_subtitle_payload_security_limits(
    analysis_environment: AnalysisEnvironment,
    response_kind: str,
) -> None:
    fixture = await _seed_video(analysis_environment)
    provider = FakeSubtitleProvider(
        [ProviderSubtitle("zh", "zh-CN", "Chinese", "https://aisubtitle.hdslb.com/subtitle.json")]
    )
    auth = FakeSubtitleAuth()

    async def resolver(_: str, __: int) -> list[str]:
        return ["8.8.8.8"]

    deep_payload: object = {"body": []}
    for _ in range(35):
        deep_payload = {"nested": deep_payload}

    def handler(request: httpx.Request) -> httpx.Response:
        if response_kind == "redirect":
            return httpx.Response(
                302,
                headers={"Location": "https://evil.example.invalid/steal"},
                request=request,
            )
        if response_kind == "malformed":
            return httpx.Response(200, content=b"not-json", request=request)
        if response_kind == "deep":
            return httpx.Response(200, json=deep_payload, request=request)
        return httpx.Response(200, content=b"x" * 65_537, request=request)

    bounded_settings = analysis_environment.settings.model_copy(
        update={"upstream_max_response_bytes": 65_536}
    )
    subtitle_service = ProviderSubtitleService(
        bounded_settings,
        analysis_environment.session_factory,
        cast(VideoProvider, provider),
        cast(AuthService, auth),
        transport=httpx.MockTransport(handler),
        validator=MediaURLValidator(("hdslb.com",), resolver=resolver),
    )

    with pytest.raises(AppError) as rejected:
        await subtitle_service.fetch(
            video_id=fixture.video_id,
            part_id=fixture.part_ids[0],
            language=AnalysisLanguage.CHINESE_SIMPLIFIED,
            access_mode=AccessMode.ANONYMOUS,
            checkpoint=PassiveCheckpoint(),
        )
    assert rejected.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert auth.calls == 0
    assert not list(bounded_settings.temp_dir.glob("subtitle-*.json"))


@pytest.mark.asyncio
async def test_missing_public_subtitle_fails_only_that_step_and_asr_survives(
    analysis_environment: AnalysisEnvironment,
) -> None:
    fixture = await _seed_video(analysis_environment)
    provider = FakeSubtitleProvider([])
    auth = FakeSubtitleAuth()
    subtitle_service = ProviderSubtitleService(
        analysis_environment.settings,
        analysis_environment.session_factory,
        cast(VideoProvider, provider),
        cast(AuthService, auth),
    )
    streams = _analysis_streams(fixture.part_ids[0])
    executor = FakeAnalysisDownloadExecutor(
        analysis_environment.settings,
        analysis_environment.artifact_service,
    )
    acquirer = DownloadAnalysisMediaAcquirer(
        analysis_environment.session_factory,
        cast(VideoService, FakeAnalysisVideoService(streams)),
        cast(DownloadExecutor, executor),
        analysis_environment.artifact_service,
    )
    analysis_environment.analysis_service.subtitle_fetcher = subtitle_service
    analysis_environment.analysis_service.media_acquirer = acquirer

    created = await analysis_environment.analysis_service.create(
        AnalysisRequest(
            video_id=fixture.video_id,
            part_ids=[fixture.part_ids[0]],
            features=[AnalysisFeature.SUBTITLES, AnalysisFeature.ASR],
        )
    )
    completed = await _wait_for_terminal(analysis_environment.job_service, created.id)

    assert completed.status == JobStatus.COMPLETED
    manifest_artifact = next(
        artifact
        for artifact in completed.artifacts
        if _artifact_info(artifact).get("artifactRole") == "analysis_manifest"
    )
    manifest = await _artifact_json(analysis_environment.artifact_service, manifest_artifact)
    assert manifest["overallStatus"] == "partial"
    steps = cast(list[dict[str, object]], manifest["steps"])
    assert {str(step["feature"]): str(step["status"]) for step in steps} == {
        "subtitles": "failed",
        "asr": "completed",
    }
    assert any(artifact.type == "transcript" for artifact in completed.artifacts)
    assert not any(artifact.type in {"video", "audio", "media"} for artifact in completed.artifacts)


def test_analysis_payload_and_serialization_security_boundaries() -> None:
    valid_payload: dict[str, object] = {
        "video_id": "123e4567-e89b-42d3-a456-426614174000",
        "part_ids": ["123e4567-e89b-42d3-a456-426614174001"],
        "features": ["basic"],
        "source_artifact_ids": {},
        "language": "zh-CN",
        "access_mode": "anonymous",
        "asr_model": "small",
        "device": "auto",
        "ocr_resolution": "balanced",
        "sample_interval_seconds": None,
        "export_formats": ["json"],
        "maximum_duration_seconds": None,
        "scene_threshold": 0.3,
        "maximum_keyframes": 24,
    }
    parsed = AnalysisService._execution_input(valid_payload)
    assert parsed.maximum_duration_seconds is None

    invalid_payloads = [
        {**valid_payload, "features": []},
        {**valid_payload, "access_mode": "auto"},
        {**valid_payload, "source_artifact_ids": []},
        {**valid_payload, "maximum_keyframes": True},
        {**valid_payload, "scene_threshold": float("inf")},
        {
            **valid_payload,
            "features": ["media"],
            "source_artifact_ids": {"unexpected-part": "unexpected-artifact"},
        },
    ]
    for payload in invalid_payloads:
        with pytest.raises(AppError):
            AnalysisService._execution_input(payload)

    safe = _sanitize_json(
        {
            "url": "https://signed.invalid",
            "nested": ["file:///private/source", "SESSDATA=secret", 7],
        }
    )
    assert safe == {
        "url": "<redacted>",
        "nested": ["<redacted>", "<redacted>", 7],
    }
    assert _document_from_value(None) is None
    assert _document_from_value({"language": "zh-CN"}) is None
    public = _document(TranscriptSource.PUBLIC_SUBTITLE, "public", "证据")
    assert _preferred_document([], AnalysisLanguage.AUTO) is None
    assert _preferred_document([public], AnalysisLanguage.AUTO) is public
    assert _as_utc(datetime(2026, 7, 14, tzinfo=UTC)).tzinfo == UTC


def test_default_engine_factory_applies_bounded_request_options() -> None:
    economy = _default_engine_factory(
        AnalysisEngineOptions(
            language=AnalysisLanguage.ENGLISH,
            asr_model=AsrModel.TINY,
            device=AnalysisDevice.CPU,
            ocr_resolution=OcrResolution.ECONOMY,
            sample_interval_seconds=None,
            maximum_duration_seconds=None,
        )
    )
    assert economy.asr.config.language == "en"
    assert economy.ocr.config.maximum_frames == 300

    detail = _default_engine_factory(
        AnalysisEngineOptions(
            language=AnalysisLanguage.CHINESE_SIMPLIFIED,
            asr_model=AsrModel.SMALL,
            device=AnalysisDevice.GPU,
            ocr_resolution=OcrResolution.DETAIL,
            sample_interval_seconds=1.5,
            maximum_duration_seconds=86_400,
        )
    )
    assert detail.ocr.config.maximum_frames == 10_000
    assert detail.ocr.config.maximum_width == 1920
    assert detail.asr.config.device == "cuda"
    assert detail.ocr.config.device == "gpu"


@pytest.mark.asyncio
async def test_application_analysis_preferences_supply_defaults_and_enforce_limits(
    analysis_environment: AnalysisEnvironment,
) -> None:
    source = await _seed_source(analysis_environment)
    settings_service = SettingsService(
        analysis_environment.settings,
        analysis_environment.session_factory,
    )
    constrained = AnalysisService(
        analysis_environment.settings,
        analysis_environment.session_factory,
        analysis_environment.artifact_service,
        analysis_environment.job_service,
        settings_service=settings_service,
        engine_factory=lambda _: cast(LocalAnalysisEngine, analysis_environment.fake_engine),
    )
    disabled_capabilities = await constrained.capabilities()
    disabled_ocr = next(
        item for item in disabled_capabilities.items if item.feature == AnalysisFeature.OCR
    )
    assert not disabled_ocr.available
    assert disabled_ocr.reason_code == "OCR_DISABLED"

    with pytest.raises(AppError) as disabled:
        await constrained.create(
            AnalysisRequest(
                video_id=source.video_id,
                part_ids=[source.part_id],
                features=[AnalysisFeature.OCR],
            )
        )
    assert disabled.value.status_code == status.HTTP_409_CONFLICT

    await settings_service.update(
        AppSettings(
            analysis=AnalysisSettings(
                language=AnalysisLanguage.ENGLISH,
                asr_model=AsrModel.TINY,
                ocr_enabled=True,
                device=AnalysisDevice.CPU,
                sample_interval_seconds=1.25,
                maximum_duration_seconds=120,
            )
        )
    )
    defaulted = await constrained.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.BASIC],
        )
    )
    defaulted_payload = await _persisted_job_payload(analysis_environment, defaulted.id)
    assert defaulted_payload["language"] == "en"
    assert defaulted_payload["asr_model"] == "tiny"
    assert defaulted_payload["device"] == "cpu"
    assert defaulted_payload["sample_interval_seconds"] == 1.25
    assert defaulted_payload["maximum_duration_seconds"] == 120
    assert (await _wait_for_terminal(analysis_environment.job_service, defaulted.id)).status == (
        JobStatus.COMPLETED
    )

    with pytest.raises(AppError) as excessive:
        await constrained.create(
            AnalysisRequest(
                video_id=source.video_id,
                part_ids=[source.part_id],
                features=[AnalysisFeature.BASIC],
                maximum_duration_seconds=180,
            )
        )
    assert excessive.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    overridden = await constrained.create(
        AnalysisRequest(
            video_id=source.video_id,
            part_ids=[source.part_id],
            features=[AnalysisFeature.BASIC],
            language=AnalysisLanguage.JAPANESE,
            asr_model=AsrModel.BASE,
            maximum_duration_seconds=60,
        )
    )
    overridden_payload = await _persisted_job_payload(analysis_environment, overridden.id)
    assert overridden_payload["language"] == "ja"
    assert overridden_payload["asr_model"] == "base"
    assert overridden_payload["maximum_duration_seconds"] == 60
