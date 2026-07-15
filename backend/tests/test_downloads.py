from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.db.models import (
    AccessContext,
    Job,
    JobStatus,
    JobType,
    MediaStream,
    StreamKind,
    Video,
    VideoPart,
)
from app.db.session import create_engine, create_schema, create_session_factory
from app.media.download import (
    DownloadCheckpoint,
    DownloadPaused,
    DownloadProgress,
    DownloadResult,
    HTTPMediaDownloader,
    MediaDownloadError,
)
from app.media.ffmpeg import FFmpegError, FFmpegProcessor, MediaProbe, MediaValidationError
from app.media.security import MediaURLValidator
from app.providers.models import ProviderPart, ProviderSubtitle, ProviderVideo
from app.schemas.jobs import DownloadRequest, OutputContainer, ProcessingMode
from app.schemas.video import AccessMode
from app.services.artifacts import ArtifactService
from app.services.downloads import (
    DownloadExecutor,
    DownloadRuntimeConfig,
)
from app.services.videos import ResolvedStream, VideoService


class Checkpoint:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def checkpoint(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


class Reporter:
    def __init__(self) -> None:
        self.values: list[dict[str, object]] = []

    async def update(
        self,
        *,
        phase: str,
        progress: float,
        downloaded_bytes: int | None = None,
        total_bytes: int | None = None,
        automatic_attempt: int | None = None,
    ) -> None:
        self.values.append(
            {
                "phase": phase,
                "progress": progress,
                "downloaded": downloaded_bytes,
                "total": total_bytes,
                "attempt": automatic_attempt,
            }
        )


class FakeDownloader:
    def __init__(self, *, resource: bool = False) -> None:
        self.resource = resource
        self.probes: list[str] = []
        self.downloads: list[tuple[str, Path]] = []
        self.discarded: list[Path] = []
        self.failures: list[MediaDownloadError] = []
        self.failure_by_download: dict[int, Exception] = {}

    async def probe(self, url: str, *, max_bytes: int = 1_024) -> int:
        del max_bytes
        self.probes.append(url)
        return 128

    async def download(
        self,
        url: str,
        destination: Path,
        *,
        checkpoint: DownloadCheckpoint,
        progress: object,
    ) -> DownloadResult:
        await checkpoint.checkpoint()
        self.downloads.append((url, destination))
        numbered_failure = self.failure_by_download.pop(len(self.downloads), None)
        if numbered_failure is not None:
            raise numbered_failure
        if self.failures:
            raise self.failures.pop(0)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.resource and "cover" in destination.name:
            content = b"\xff\xd8\xff" + b"image" * 10
        elif self.resource and "subtitle" in destination.name:
            content = json.dumps(
                {"body": [{"from": 0, "to": 1, "content": "字幕内容"}]},
                ensure_ascii=False,
            ).encode()
        else:
            content = (b"video" if "video" in destination.name else b"audio") * 32
        await asyncio.to_thread(destination.write_bytes, content)
        callback = cast(object, progress)
        await callback(DownloadProgress(len(content), len(content)))  # type: ignore[operator]
        return DownloadResult(destination, len(content), len(content), 0)

    def discard_partial(self, destination: Path) -> None:
        self.discarded.append(destination)
        destination.unlink(missing_ok=True)
        destination.with_name(f"{destination.name}.resume.json").unlink(missing_ok=True)


class FakeProcessor:
    def __init__(self) -> None:
        self.available_checks = 0
        self.process_calls = 0
        self.validated: list[tuple[Path, str]] = []
        self.pause_once = False
        self.fail_once: FFmpegError | None = None

    def check_available(self) -> None:
        self.available_checks += 1

    async def validate_input(self, path: Path, *, expected_kind: str) -> MediaProbe:
        self.validated.append((path, expected_kind))
        stream = (
            {"type": "video", "codec": "h264", "width": 1920, "height": 1080, "duration": 10.0}
            if expected_kind == "video"
            else {
                "type": "audio",
                "codec": "aac",
                "sampleRate": 48_000,
                "channels": 2,
                "duration": 10.0,
            }
        )
        size = await asyncio.to_thread(lambda: path.stat().st_size)
        return MediaProbe(10, size, None, "fake", (stream,))

    async def process(
        self,
        *,
        video_path: Path | None,
        audio_path: Path | None,
        output_path: Path,
        container: str,
        processing_mode: str,
        expected_duration: float,
        checkpoint: DownloadCheckpoint,
        progress: object,
    ) -> MediaProbe:
        del container, processing_mode
        self.process_calls += 1
        await checkpoint.checkpoint()
        if self.pause_once:
            self.pause_once = False
            raise DownloadPaused
        if self.fail_once is not None:
            error = self.fail_once
            self.fail_once = None
            raise error
        assert video_path is not None or audio_path is not None
        assert expected_duration == 10
        await asyncio.to_thread(output_path.write_bytes, b"final-media")
        callback = cast(object, progress)
        await callback(1.0)  # type: ignore[operator]
        streams: list[dict[str, object]] = []
        if video_path is not None:
            streams.append(
                {"type": "video", "codec": "h264", "width": 1920, "height": 1080, "duration": 10.0}
            )
        if audio_path is not None:
            streams.append({"type": "audio", "codec": "aac", "duration": 10.0})
        return MediaProbe(10, len(b"final-media"), None, "mp4", tuple(streams))


class FakeAuth:
    async def cookie_jar(self) -> CookieJar:
        return CookieJar()


class FakeProvider:
    def __init__(self) -> None:
        self.subtitle_calls = 0
        self.danmaku_calls = 0
        self.danmaku_error: Exception | None = None
        self.danmaku_document = b'<?xml version="1.0"?><i><d p="1,1,1,1,1,1,1,1">fixed</d></i>'
        self.subtitles = [
            ProviderSubtitle(
                subtitle_id="1",
                language="zh-CN",
                language_label="中文",
                url="https://aisubtitle.hdslb.com/subtitle.json",
            )
        ]

    async def get_subtitles(
        self,
        video: ProviderVideo,
        part: ProviderPart,
        cookies: CookieJar | None = None,
    ) -> list[ProviderSubtitle]:
        del video, part, cookies
        self.subtitle_calls += 1
        return list(self.subtitles)

    async def get_danmaku(self, video: ProviderVideo, part: ProviderPart) -> bytes:
        del video, part
        self.danmaku_calls += 1
        if self.danmaku_error is not None:
            raise self.danmaku_error
        return self.danmaku_document


class FakeVideoService:
    _provider_video = staticmethod(VideoService._provider_video)
    _provider_part = staticmethod(VideoService._provider_part)
    official_url = staticmethod(VideoService.official_url)

    def __init__(self) -> None:
        self.provider = FakeProvider()
        self.auth_service = FakeAuth()
        self.refresh_calls = 0
        self.resolve_calls = 0
        self.app_error: AppError | None = None
        self.verification_calls: list[tuple[str, int | None, int | None]] = []

    async def get_part_streams(
        self,
        video_id: str,
        part_id: str,
        access_mode: AccessMode,
        *,
        force_refresh: bool = False,
    ) -> object:
        del video_id, part_id, access_mode
        assert force_refresh is True
        self.refresh_calls += 1
        if self.app_error is not None:
            raise self.app_error
        return object()

    async def resolve_stream(
        self,
        stream_id: str,
        access_mode: AccessMode,
        *,
        verify: bool = True,
    ) -> ResolvedStream:
        del access_mode
        assert verify is False
        self.resolve_calls += 1
        kind = StreamKind.VIDEO if "video" in stream_id else StreamKind.AUDIO
        return ResolvedStream(
            stream_id=stream_id,
            url=f"https://cdn.bilivideo.com/{stream_id}",
            backup_urls=(f"https://backup.bilivideo.com/{stream_id}",),
            kind=kind,
            codec="h264" if kind == StreamKind.VIDEO else "aac",
            container="m4s",
        )

    async def record_stream_verification(
        self,
        stream_id: str,
        *,
        sample_rate: int | None = None,
        audio_channels: int | None = None,
    ) -> bool:
        self.verification_calls.append((stream_id, sample_rate, audio_channels))
        return True


@pytest.fixture
async def download_environment(
    settings: Settings,
) -> AsyncIterator[dict[str, object]]:
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    video = Video(
        provider="bilibili",
        bvid="BV1TEST12345",
        aid=12345,
        title="标题/含非法字符",
        description="description",
        cover_url="https://i0.hdslb.com/cover.jpg",
        owner_name="owner",
        duration=10,
        stats={},
        tags=[],
        rights={},
        raw_metadata={},
        parsed_at=datetime.now(UTC),
    )
    part = VideoPart(cid=999, page_number=2, title="第二/集", duration=10)
    video.parts.append(part)
    video_stream = MediaStream(
        access_context=AccessContext.ANONYMOUS,
        source_key="video-source",
        kind=StreamKind.VIDEO,
        quality_code=80,
        quality_label="1080P",
        codec="H.264/AVC",
        container="m4s",
        width=1920,
        height=1080,
        fps=30,
        bitrate=1_000_000,
        estimated_size=1_250_000,
        auth_required=False,
        compatibility="compatible",
    )
    audio_stream = MediaStream(
        access_context=AccessContext.ANONYMOUS,
        source_key="audio-source",
        kind=StreamKind.AUDIO,
        quality_code=30280,
        quality_label="192K",
        codec="AAC",
        container="m4s",
        bitrate=192_000,
        estimated_size=240_000,
        auth_required=False,
        compatibility="compatible",
    )
    auth_video = MediaStream(
        access_context=AccessContext.AUTHENTICATED,
        source_key="auth-video",
        kind=StreamKind.VIDEO,
        quality_code=112,
        quality_label="1080P+",
        codec="H.264/AVC",
        container="m4s",
        width=1920,
        height=1080,
        fps=60,
        bitrate=2_000_000,
        estimated_size=None,
        auth_required=True,
        compatibility="compatible",
    )
    part.streams.extend((video_stream, audio_stream, auth_video))
    async with factory() as session:
        session.add(video)
        await session.commit()
        await session.refresh(video)
        await session.refresh(part)
        await session.refresh(video_stream)
        await session.refresh(audio_stream)
        await session.refresh(auth_video)

    artifact_service = ArtifactService(settings, factory)
    media_downloader = FakeDownloader()
    resource_downloader = FakeDownloader(resource=True)
    processor = FakeProcessor()
    video_service = FakeVideoService()
    executor = DownloadExecutor(
        settings,
        factory,
        cast(VideoService, video_service),
        artifact_service,
        downloader=cast(HTTPMediaDownloader, media_downloader),
        resource_downloader=cast(HTTPMediaDownloader, resource_downloader),
        processor=cast(FFmpegProcessor, processor),
        runtime=DownloadRuntimeConfig(retries=2, retry_base_delay_seconds=0),
    )
    yield {
        "engine": engine,
        "factory": factory,
        "video": video,
        "part": part,
        "video_stream": video_stream,
        "audio_stream": audio_stream,
        "auth_video": auth_video,
        "artifacts": artifact_service,
        "downloader": media_downloader,
        "resource_downloader": resource_downloader,
        "processor": processor,
        "video_service": video_service,
        "executor": executor,
        "settings": settings,
    }
    await engine.dispose()


async def create_job(factory: object, payload: dict[str, object]) -> Job:
    job = Job(
        type=JobType.DOWNLOAD,
        status=JobStatus.RUNNING,
        phase="preparing",
        progress=0,
        input_json=payload,
        retry_count=0,
        cancel_requested=False,
    )
    async with factory() as session:  # type: ignore[operator]
        session.add(job)
        await session.commit()
        await session.refresh(job)
        session.expunge(job)
    return job


def request_for(environment: dict[str, object], **overrides: object) -> DownloadRequest:
    values: dict[str, object] = {
        "video_id": environment["video"].id,  # type: ignore[union-attr]
        "part_id": environment["part"].id,  # type: ignore[union-attr]
        "video_stream_id": environment["video_stream"].id,  # type: ignore[union-attr]
        "audio_stream_id": environment["audio_stream"].id,  # type: ignore[union-attr]
        "filename": "{title}-{bvid}-P{page}-{part}-{quality}",
    }
    values.update(overrides)
    return DownloadRequest.model_validate(values)


async def test_prepare_expands_template_and_persists_only_safe_specifications(
    download_environment: dict[str, object],
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    payload = await executor.prepare(request_for(download_environment))
    assert payload["output_filename"].endswith(".mp4")
    assert "{" not in str(payload["output_filename"])
    assert "/" not in str(payload["output_filename"])
    assert payload["bvid"] == "BV1TEST12345"
    assert payload["page_number"] == 2
    assert payload["quality_label"] == "1080P"
    assert payload["expected_size"] == 1_490_000
    assert payload["video_codec"] == "H.264/AVC"
    assert payload["audio_codec"] == "AAC"
    assert payload["include_danmaku"] is False
    serialized = json.dumps(payload)
    assert "bilivideo.com" not in serialized
    assert "cookie" not in serialized.casefold()


async def test_execute_downloads_merges_and_publishes_companion_artifacts(
    download_environment: dict[str, object],
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    factory = download_environment["factory"]
    payload = await executor.prepare(request_for(download_environment))
    job = await create_job(factory, payload)
    reporter = Reporter()
    records = await executor.execute(job, checkpoint=Checkpoint(), reporter=reporter)

    assert {item.type for item in records} == {"video", "cover", "subtitle", "metadata"}
    assert all((executor.artifact_root / item.storage_key).is_file() for item in records)
    assert not (executor.temp_root / job.id).exists()
    assert reporter.values[-1]["phase"] == "completed"
    metadata = next(item for item in records if item.type == "metadata")
    document = json.loads(
        (executor.artifact_root / metadata.storage_key).read_text(encoding="utf-8")
    )
    assert document["bvid"] == "BV1TEST12345"
    assert document["includedResources"] == {
        "subtitle": True,
        "cover": True,
        "metadata": True,
        "danmaku": False,
    }
    assert document["companionOutcomes"] == {
        "cover": "completed",
        "metadata": "completed",
        "subtitle": "completed",
    }
    subtitle = next(item for item in records if item.type == "subtitle")
    assert subtitle.media_info["language"] == "zh-CN"
    verification_calls = cast(
        FakeVideoService, download_environment["video_service"]
    ).verification_calls
    assert verification_calls == [
        (download_environment["video_stream"].id, None, None),  # type: ignore[union-attr]
        (download_environment["audio_stream"].id, 48_000, 2),  # type: ignore[union-attr]
    ]

    download_count = len(cast(FakeDownloader, download_environment["downloader"]).downloads)
    second = await executor.execute(job, checkpoint=Checkpoint(), reporter=Reporter())
    assert {item.id for item in second} == {item.id for item in records}
    assert len(cast(FakeDownloader, download_environment["downloader"]).downloads) == download_count


async def test_execute_publishes_safe_danmaku_and_persists_companion_outcomes(
    download_environment: dict[str, object],
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    factory = download_environment["factory"]
    payload = await executor.prepare(request_for(download_environment, include_danmaku=True))
    job = await create_job(factory, payload)

    records = await executor.execute(job, checkpoint=Checkpoint(), reporter=Reporter())

    assert {item.type for item in records} == {
        "video",
        "cover",
        "subtitle",
        "danmaku",
        "metadata",
    }
    danmaku = next(item for item in records if item.type == "danmaku")
    assert danmaku.filename.endswith(".danmaku.xml")
    assert danmaku.mime_type == "application/xml"
    assert (executor.artifact_root / danmaku.storage_key).read_bytes().endswith(b"</i>")
    metadata = next(item for item in records if item.type == "metadata")
    document = json.loads(
        (executor.artifact_root / metadata.storage_key).read_text(encoding="utf-8")
    )
    assert document["includedResources"]["danmaku"] is True
    assert document["companionOutcomes"] == {
        "cover": "completed",
        "danmaku": "completed",
        "metadata": "completed",
        "subtitle": "completed",
    }
    async with factory() as session:  # type: ignore[operator]
        persisted = await session.get(Job, job.id)
        assert persisted is not None
        assert persisted.input_json["companion_outcomes"] == document["companionOutcomes"]


async def test_zero_entry_danmaku_is_a_completed_artifact(
    download_environment: dict[str, object],
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    provider = cast(FakeVideoService, download_environment["video_service"]).provider
    provider.danmaku_document = b"<i></i>"
    payload = await executor.prepare(
        request_for(
            download_environment,
            include_cover=False,
            include_subtitle=False,
            include_metadata=False,
            include_danmaku=True,
        )
    )
    job = await create_job(download_environment["factory"], payload)

    records = await executor.execute(job, checkpoint=Checkpoint(), reporter=Reporter())

    assert {item.type for item in records} == {"video", "danmaku"}
    danmaku = next(item for item in records if item.type == "danmaku")
    assert (executor.artifact_root / danmaku.storage_key).read_bytes() == b"<i></i>"
    assert job.input_json["companion_outcomes"] == {"danmaku": "completed"}


async def test_resume_completes_all_expected_subtitle_tracks_after_partial_publish(
    download_environment: dict[str, object],
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    provider = cast(FakeVideoService, download_environment["video_service"]).provider
    provider.subtitles = [
        ProviderSubtitle(
            subtitle_id="1",
            language="zh-CN",
            language_label="中文",
            url="https://aisubtitle.hdslb.com/subtitle-zh.json",
        ),
        ProviderSubtitle(
            subtitle_id="2",
            language="en-US",
            language_label="English",
            url="https://aisubtitle.hdslb.com/subtitle-en.json",
        ),
    ]
    resource = cast(FakeDownloader, download_environment["resource_downloader"])
    resource.failure_by_download[2] = DownloadPaused()
    payload = await executor.prepare(
        request_for(
            download_environment,
            include_cover=False,
            include_metadata=False,
            include_danmaku=False,
        )
    )
    job = await create_job(download_environment["factory"], payload)

    with pytest.raises(DownloadPaused):
        await executor.execute(job, checkpoint=Checkpoint(), reporter=Reporter())

    partial = await executor.artifact_service.existing_all_for_job(job.id)
    assert len([item for item in partial if item.type == "subtitle"]) == 1
    expected = job.input_json["companion_expectations"]["subtitle_filenames"]
    assert isinstance(expected, list) and len(expected) == 2
    assert job.input_json["companion_outcomes"].get("subtitle") is None

    completed = await executor.execute(job, checkpoint=Checkpoint(), reporter=Reporter())

    subtitles = [item for item in completed if item.type == "subtitle"]
    assert len(subtitles) == 2
    assert {item.filename for item in subtitles} == set(expected)
    assert job.input_json["companion_outcomes"]["subtitle"] == "completed"
    assert provider.subtitle_calls == 2


async def test_partial_subtitle_failure_is_reported_with_expected_manifest(
    download_environment: dict[str, object],
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    provider = cast(FakeVideoService, download_environment["video_service"]).provider
    provider.subtitles = [
        ProviderSubtitle(
            subtitle_id="1",
            language="zh-CN",
            language_label="中文",
            url="https://aisubtitle.hdslb.com/subtitle-zh.json",
        ),
        ProviderSubtitle(
            subtitle_id="2",
            language="en-US",
            language_label="English",
            url="https://aisubtitle.hdslb.com/subtitle-en.json",
        ),
    ]
    resource = cast(FakeDownloader, download_environment["resource_downloader"])
    resource.failure_by_download[2] = MediaDownloadError(
        "SUBTITLE_UPSTREAM_FAILED",
        "fixed subtitle failure",
    )
    payload = await executor.prepare(
        request_for(
            download_environment,
            include_cover=False,
            include_metadata=False,
            include_danmaku=False,
        )
    )
    job = await create_job(download_environment["factory"], payload)

    records = await executor.execute(job, checkpoint=Checkpoint(), reporter=Reporter())

    assert "video" in {item.type for item in records}
    persisted_artifacts = await executor.artifact_service.existing_all_for_job(job.id)
    assert len([item for item in persisted_artifacts if item.type == "subtitle"]) == 1
    assert len(job.input_json["companion_expectations"]["subtitle_filenames"]) == 2
    assert job.input_json["companion_outcomes"]["subtitle"] == "failed"


async def test_analysis_media_download_does_not_replace_parent_job_input(
    download_environment: dict[str, object],
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    factory = download_environment["factory"]
    parent_payload: dict[str, object] = {
        "video_id": download_environment["video"].id,  # type: ignore[union-attr]
        "part_id": download_environment["part"].id,  # type: ignore[union-attr]
        "features": ["basic", "media"],
        "source_artifact_ids": {},
    }
    parent = Job(
        type=JobType.ANALYSIS,
        status=JobStatus.RUNNING,
        phase="analysis_media_acquisition",
        progress=1,
        input_json=parent_payload,
        retry_count=0,
        cancel_requested=False,
    )
    async with factory() as session:  # type: ignore[operator]
        session.add(parent)
        await session.commit()
        await session.refresh(parent)
        session.expunge(parent)

    prepared = await executor.prepare(
        request_for(
            download_environment,
            include_cover=False,
            include_subtitle=False,
            include_metadata=False,
            include_danmaku=False,
        )
    )
    prepared["analysis_parent_job_id"] = parent.id
    download_view = Job(
        id=parent.id,
        type=JobType.DOWNLOAD,
        status=JobStatus.RUNNING,
        phase="analysis_media_acquisition",
        progress=1,
        input_json=prepared,
        retry_count=0,
        cancel_requested=False,
    )

    records = await executor.execute(
        download_view,
        checkpoint=Checkpoint(),
        reporter=Reporter(),
    )

    assert any(item.type == "video" for item in records)
    async with factory() as session:  # type: ignore[operator]
        persisted = await session.get(Job, parent.id)
        assert persisted is not None
        assert persisted.input_json == parent_payload


async def test_danmaku_failure_is_local_and_does_not_expose_raw_error(
    download_environment: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    factory = download_environment["factory"]
    provider = cast(FakeVideoService, download_environment["video_service"]).provider
    provider.danmaku_error = RuntimeError("sensitive upstream response")
    payload = await executor.prepare(request_for(download_environment, include_danmaku=True))
    job = await create_job(factory, payload)

    records = await executor.execute(job, checkpoint=Checkpoint(), reporter=Reporter())

    assert "video" in {item.type for item in records}
    assert "danmaku" not in {item.type for item in records}
    metadata = next(item for item in records if item.type == "metadata")
    document = json.loads(
        (executor.artifact_root / metadata.storage_key).read_text(encoding="utf-8")
    )
    assert document["companionOutcomes"]["danmaku"] == "failed"
    assert "sensitive upstream response" not in caplog.text
    async with factory() as session:  # type: ignore[operator]
        persisted = await session.get(Job, job.id)
        assert persisted is not None
        assert persisted.input_json["companion_outcomes"]["danmaku"] == "failed"


async def test_pause_in_post_processing_reuses_completed_tracks(
    download_environment: dict[str, object],
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    processor = cast(FakeProcessor, download_environment["processor"])
    request = request_for(
        download_environment,
        include_cover=False,
        include_subtitle=False,
        include_metadata=False,
    )
    payload = await executor.prepare(request)
    job = await create_job(download_environment["factory"], payload)
    processor.pause_once = True
    with pytest.raises(DownloadPaused):
        await executor.execute(job, checkpoint=Checkpoint(), reporter=Reporter())
    downloader = cast(FakeDownloader, download_environment["downloader"])
    first_count = len(downloader.downloads)
    assert first_count == 2
    assert list((executor.temp_root / job.id).glob("*.complete.json"))

    records = await executor.execute(job, checkpoint=Checkpoint(), reporter=Reporter())
    assert len(records) == 1 and records[0].type == "video"
    assert len(downloader.downloads) == first_count
    assert processor.process_calls == 2


async def test_retry_refreshes_expired_url_and_uses_backup_candidates(
    download_environment: dict[str, object],
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    downloader = cast(FakeDownloader, download_environment["downloader"])
    request = request_for(
        download_environment,
        audio_stream_id="none",
        include_cover=False,
        include_subtitle=False,
        include_metadata=False,
    )
    payload = await executor.prepare(request)
    job = await create_job(download_environment["factory"], payload)
    downloader.failures.append(MediaDownloadError("MEDIA_URL_EXPIRED", "expired", retryable=True))
    downloader.failures.append(
        MediaDownloadError("MEDIA_URL_EXPIRED", "expired backup", retryable=True)
    )
    records = await executor.execute(job, checkpoint=Checkpoint(), reporter=Reporter())
    assert records[0].type == "video"
    assert cast(FakeVideoService, download_environment["video_service"]).refresh_calls >= 2
    assert any("backup.bilivideo.com" in url for url, _ in downloader.downloads)


async def test_nonretryable_auth_or_risk_error_is_not_repeated(
    download_environment: dict[str, object],
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    video_service = cast(FakeVideoService, download_environment["video_service"])
    video_service.app_error = AppError(
        ErrorCode.RISK_CONTROL,
        "上游要求验证",
        action="稍后重试",
        status_code=403,
    )
    payload = await executor.prepare(
        request_for(
            download_environment,
            audio_stream_id="none",
            include_cover=False,
            include_subtitle=False,
            include_metadata=False,
        )
    )
    job = await create_job(download_environment["factory"], payload)
    with pytest.raises(AppError) as caught:
        await executor.execute(job, checkpoint=Checkpoint(), reporter=Reporter())
    assert caught.value.code == ErrorCode.RISK_CONTROL
    assert video_service.refresh_calls == 1


async def test_cleanup_policy_can_retain_completed_tracks(
    download_environment: dict[str, object],
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    payload = await executor.prepare(
        request_for(
            download_environment,
            include_cover=False,
            include_subtitle=False,
            include_metadata=False,
            cleanup_temporary=False,
        )
    )
    job = await create_job(download_environment["factory"], payload)
    await executor.execute(job, checkpoint=Checkpoint(), reporter=Reporter())
    assert (executor.temp_root / job.id / "video.media.part").exists()
    assert (executor.temp_root / job.id / "audio.media.part.complete.json").exists()
    await executor.discard_job_partials(job.id)
    assert not (executor.temp_root / job.id).exists()


async def test_prepare_rejects_wrong_context_missing_part_and_audio(
    download_environment: dict[str, object],
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    with pytest.raises(AppError):
        await executor.prepare(
            request_for(
                download_environment,
                video_stream_id=download_environment["auth_video"].id,  # type: ignore[union-attr]
                access_mode=AccessMode.ANONYMOUS,
            )
        )
    with pytest.raises(AppError):
        await executor.prepare(
            DownloadRequest(
                video_id=download_environment["video"].id,  # type: ignore[union-attr]
                part_id="missing",
                video_stream_id=download_environment["video_stream"].id,  # type: ignore[union-attr]
            )
        )
    with pytest.raises(AppError):
        await executor.prepare(
            DownloadRequest(
                video_id=download_environment["video"].id,  # type: ignore[union-attr]
                part_id=download_environment["part"].id,  # type: ignore[union-attr]
                video_stream_id=None,
                audio_stream_id="auto",
                container=OutputContainer.M4A,
                access_mode=AccessMode.AUTHENTICATED,
            )
        )


async def test_prepare_authoritatively_rejects_unsafe_copy_combinations(
    download_environment: dict[str, object],
) -> None:
    factory = download_environment["factory"]
    part = download_environment["part"]
    extra_streams: list[MediaStream] = []
    for source_key, codec in (("flac-source", "FLAC"), ("dolby-source", "Dolby E-AC-3")):
        stream = MediaStream(
            part_id=part.id,  # type: ignore[union-attr]
            access_context=AccessContext.ANONYMOUS,
            source_key=source_key,
            kind=StreamKind.AUDIO,
            quality_code=30_251,
            quality_label=codec,
            codec=codec,
            container="m4s",
            bitrate=512_000,
            estimated_size=640_000,
            auth_required=False,
            compatibility="limited",
        )
        async with factory() as session:  # type: ignore[operator]
            session.add(stream)
            await session.commit()
            await session.refresh(stream)
        extra_streams.append(stream)

    executor = cast(DownloadExecutor, download_environment["executor"])
    for stream in extra_streams:
        with pytest.raises(AppError) as mp4_error:
            await executor.prepare(request_for(download_environment, audio_stream_id=stream.id))
        assert mp4_error.value.status_code == 422
        assert "兼容转码" in (mp4_error.value.action or "")

        with pytest.raises(AppError) as m4a_error:
            await executor.prepare(
                request_for(
                    download_environment,
                    video_stream_id=None,
                    audio_stream_id=stream.id,
                    container=OutputContainer.M4A,
                )
            )
        assert m4a_error.value.status_code == 422

        transcoded = await executor.prepare(
            request_for(
                download_environment,
                audio_stream_id=stream.id,
                processing_mode=ProcessingMode.TRANSCODE,
            )
        )
        assert transcoded["processing_mode"] == "transcode"

        lossless_mkv = await executor.prepare(
            request_for(
                download_environment,
                audio_stream_id=stream.id,
                container=OutputContainer.MKV,
            )
        )
        assert lossless_mkv["container"] == "mkv"
        assert lossless_mkv["processing_mode"] == "copy"


async def test_video_service_records_ffprobe_verification_details(
    download_environment: dict[str, object],
) -> None:
    service = object.__new__(VideoService)
    service.session_factory = download_environment["factory"]  # type: ignore[attr-defined]
    audio_stream = download_environment["audio_stream"]

    updated = await service.record_stream_verification(
        audio_stream.id,  # type: ignore[union-attr]
        sample_rate=44_100,
        audio_channels=6,
    )
    assert updated is True
    async with download_environment["factory"]() as session:  # type: ignore[operator]
        persisted = await session.get(MediaStream, audio_stream.id)  # type: ignore[union-attr]
        assert persisted is not None
        assert persisted.verified_at is not None
        assert persisted.sample_rate == 44_100
        assert persisted.audio_channels == 6

    assert await service.record_stream_verification("missing") is False


def test_download_request_supports_flac_and_validates_combinations() -> None:
    request = DownloadRequest(
        video_id="video",
        part_id="part",
        video_stream_id=None,
        audio_stream_id="audio",
        container=OutputContainer.FLAC,
        processing_mode=ProcessingMode.TRANSCODE,
    )
    assert request.container == OutputContainer.FLAC
    for values in (
        {"video_stream_id": None, "audio_stream_id": "none", "container": "m4a"},
        {"video_stream_id": "video", "audio_stream_id": "audio", "container": "mp3"},
        {"video_stream_id": None, "audio_stream_id": "audio", "container": "flac"},
        {
            "video_stream_id": "video",
            "audio_stream_id": "audio",
            "container": "mp4",
            "filename": "{bad}",
        },
    ):
        with pytest.raises(ValidationError):
            DownloadRequest(video_id="video", part_id="part", **values)  # type: ignore[arg-type]


def test_companion_validation_and_codec_matching(tmp_path: Path) -> None:
    jpeg = tmp_path / "cover"
    jpeg.write_bytes(b"\xff\xd8\xffdata")
    assert DownloadExecutor._image_type(jpeg) == ("jpg", "image/jpeg")
    for header, expected in (
        (b"\x89PNG\r\n\x1a\nrest", ("png", "image/png")),
        (b"RIFFxxxxWEBPrest", ("webp", "image/webp")),
        (b"xxxxftypavifrest", ("avif", "image/avif")),
    ):
        jpeg.write_bytes(header)
        assert DownloadExecutor._image_type(jpeg) == expected
    jpeg.write_bytes(b"unknown")
    with pytest.raises(MediaDownloadError):
        DownloadExecutor._image_type(jpeg)

    subtitle = tmp_path / "subtitle.json"
    subtitle.write_text('{"body":[{"from":0,"to":1,"content":"ok"}]}', encoding="utf-8")
    DownloadExecutor._validate_subtitle(subtitle)
    subtitle.write_text('{"body":[{"from":2,"to":1,"content":"bad"}]}', encoding="utf-8")
    with pytest.raises(MediaDownloadError):
        DownloadExecutor._validate_subtitle(subtitle)

    danmaku = tmp_path / "danmaku.xml"
    danmaku.write_bytes(b'<i><d p="1,1,1,1,1,1,1,1">safe</d></i>')
    DownloadExecutor._validate_danmaku(danmaku)
    danmaku.write_bytes(b'<!DOCTYPE i [<!ENTITY x "unsafe">]><i>&x;</i>')
    with pytest.raises(MediaDownloadError):
        DownloadExecutor._validate_danmaku(danmaku)

    assert DownloadExecutor._codec_matches("H.264/AVC", "h264")
    assert DownloadExecutor._codec_matches("AAC", "aac")
    assert not DownloadExecutor._codec_matches("AV1", "h264")


def test_storage_runtime_and_static_helpers(
    download_environment: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    usage = type("Usage", (), {"free": 1, "total": 2, "used": 1})()
    monkeypatch.setattr("app.services.downloads.shutil.disk_usage", lambda _path: usage)
    with pytest.raises(AppError) as storage:
        executor.preflight_disk(100)
    assert storage.value.status_code == 507

    assert DownloadExecutor._download_progress({"video": 50}, {"video": 100}) == 43
    assert DownloadExecutor._combined_total({"video": 10, "audio": 20}, None) == 30
    assert DownloadExecutor._combined_total({"video": None}, 50) == 50
    assert DownloadExecutor._primary_artifact_type("flac") == "audio"
    assert DownloadExecutor._primary_artifact_type("mkv") == "video"
    assert DownloadExecutor._mime_type("mp3") == "audio/mpeg"
    assert DownloadExecutor._optional_int(-1) is None
    with pytest.raises(ValueError):
        DownloadExecutor._required_int({}, "missing")
    with pytest.raises(ValueError):
        DownloadRuntimeConfig(retries=99)


async def test_track_specification_mismatch_and_corrupt_marker(
    download_environment: dict[str, object],
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    probe = MediaProbe(
        10,
        1,
        None,
        "fake",
        ({"type": "video", "codec": "av1", "width": 1280, "height": 720},),
    )
    with pytest.raises(MediaValidationError):
        executor._validate_track_specification(
            probe,
            kind="video",
            payload={"video_codec": "H.264/AVC", "video_width": 1920, "video_height": 1080},
        )
    with pytest.raises(MediaValidationError):
        executor._validate_track_duration(5, 10)

    destination = executor.temp_root / "track.part"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"video" * 32)
    marker = executor._track_marker_path(destination)
    marker.write_text("invalid", encoding="utf-8")
    reused = await executor._reuse_completed_track(
        destination=destination,
        stream_id="video-stream",
        kind="video",
        expected_size=None,
        expected_duration=10,
        payload={
            "video_codec": "H.264/AVC",
            "video_width": 1920,
            "video_height": 1080,
        },
    )
    assert reused is False
    assert not marker.exists()


async def test_executor_runtime_reconfigure_applies_to_subsequent_tasks(
    download_environment: dict[str, object],
    tmp_path: Path,
) -> None:
    executor = cast(DownloadExecutor, download_environment["executor"])
    runtime = DownloadRuntimeConfig(
        retries=4,
        artifact_quota_bytes=2 * 1024 * 1024 * 1024,
        rate_limit_bytes_per_second=512 * 1024,
    )
    artifact_root = tmp_path / "configured-artifacts"
    temp_root = tmp_path / "configured-temp"
    artifact_service = cast(ArtifactService, download_environment["artifacts"])
    validator = MediaURLValidator(("bilivideo.com",))
    configured_downloader = HTTPMediaDownloader(validator, user_agent="test-agent")
    configured_resource_downloader = HTTPMediaDownloader(validator, user_agent="test-agent")
    executor.downloader = configured_downloader
    executor.resource_downloader = configured_resource_downloader
    await artifact_service.reconfigure_root(artifact_root)
    await executor.reconfigure(
        runtime=runtime,
        artifact_root=artifact_root,
        temp_root=temp_root,
        default_filename_template="configured-{bvid}-P{page}",
        timeout_seconds=75.0,
    )
    payload = await executor.prepare(request_for(download_environment, filename=None))
    assert executor.runtime.retries == 4
    assert executor.artifact_root == artifact_root.resolve()
    assert executor.temp_root == temp_root.resolve()
    assert str(payload["output_filename"]).startswith("configured-BV1TEST12345-P2")
    assert configured_downloader.timeout.read == 75.0
    assert configured_downloader.timeout.connect == 5.0
    assert configured_resource_downloader.timeout.read == 75.0
    assert configured_resource_downloader.timeout.connect == 5.0

    with pytest.raises(ValueError):
        await executor.reconfigure(
            runtime=runtime,
            artifact_root=artifact_root,
            temp_root=artifact_root,
            default_filename_template="{title}",
            timeout_seconds=75.0,
        )
