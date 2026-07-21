from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.downloads import router as downloads_router
from app.api.jobs import router as jobs_router
from app.api.jobs import stream_job_events
from app.core.config import Settings
from app.core.exceptions import AppError, install_exception_handlers
from app.db.models import (
    Analysis,
    Artifact,
    Job,
    JobStatus,
    JobType,
    RetainedFile,
    Video,
    VideoPart,
)
from app.db.session import create_engine, create_schema, create_session_factory
from app.media.download import DownloadCheckpoint, MediaDownloadError
from app.schemas.jobs import DownloadBatchRequest, DownloadRequest
from app.services.artifacts import ArtifactService, RetainedFileStage
from app.services.downloads import DownloadExecutionReporter, DownloadExecutor
from app.services.jobs import JobService


class ControlledExecutor(DownloadExecutor):
    def __init__(self) -> None:
        self.calls = 0
        self.discarded: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.fail = False
        self.unexpected = False
        self.prepare_fail_part_id: str | None = None
        self.prepare_calls: list[str] = []
        self.payload: dict[str, object] = {
            "video_id": "video-id",
            "part_id": "part-id",
            "video_title": "任务视频",
            "part_title": "第一集",
            "video_stream_id": "video-stream",
            "audio_stream_id": "audio-stream",
            "container": "mp4",
            "processing_mode": "copy",
            "access_mode": "anonymous",
            "output_filename": "output.mp4",
            "include_subtitle": False,
            "include_cover": False,
            "include_metadata": False,
            "include_danmaku": False,
        }

    async def prepare(self, request: DownloadRequest) -> dict[str, object]:
        self.prepare_calls.append(request.part_id)
        if request.part_id == self.prepare_fail_part_id:
            raise RuntimeError("fixed prepare failure")
        payload = dict(self.payload)
        payload.update(
            {
                "video_id": request.video_id,
                "part_id": request.part_id,
                "part_title": f"分 P {request.part_id}",
                "video_stream_id": request.video_stream_id,
                "audio_stream_id": (
                    self.payload["audio_stream_id"]
                    if request.audio_stream_id == "auto"
                    else None
                    if request.audio_stream_id == "none"
                    else request.audio_stream_id
                ),
                "container": request.container.value,
                "processing_mode": request.processing_mode.value,
                "access_mode": (
                    "anonymous"
                    if request.access_mode.value == "auto"
                    else request.access_mode.value
                ),
                "output_filename": f"{request.part_id}.{request.container.value}",
                "include_subtitle": request.include_subtitle,
                "include_cover": request.include_cover,
                "include_metadata": request.include_metadata,
                "include_danmaku": request.include_danmaku,
            }
        )
        return payload

    async def execute(
        self,
        job: Job,
        *,
        checkpoint: DownloadCheckpoint,
        reporter: DownloadExecutionReporter,
    ) -> Sequence[Artifact]:
        self.calls += 1
        self.started.set()
        await reporter.update(
            phase="downloading_video",
            progress=25,
            downloaded_bytes=100,
            total_bytes=400,
        )
        while self.block and not self.release.is_set():
            await checkpoint.checkpoint()
            await asyncio.sleep(0.01)
        await checkpoint.checkpoint()
        if self.unexpected:
            raise RuntimeError("fixed unexpected secret=https://signed.example")
        if self.fail:
            raise MediaDownloadError("FIXED_FAILURE", "固定的可重试失败")
        await reporter.update(
            phase="post_processing",
            progress=95,
            downloaded_bytes=400,
            total_bytes=400,
        )
        return []

    async def discard_job_partials(self, job_id: str) -> None:
        self.discarded.append(job_id)


@pytest.fixture
async def job_environment(
    settings: Settings,
) -> AsyncIterator[tuple[JobService, ControlledExecutor, ControlledExecutor, object, object]]:
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    artifacts = ArtifactService(settings, factory)
    download = ControlledExecutor()
    analysis = ControlledExecutor()
    service = JobService(
        factory,
        artifacts,
        download,
        concurrency=1,
        analysis_concurrency=1,
        event_interval_seconds=0.1,
    )
    service.register_executor(JobType.ANALYSIS, analysis)
    yield service, download, analysis, factory, engine
    await service.stop()
    await engine.dispose()


async def wait_status(
    service: JobService,
    job_id: str,
    statuses: set[JobStatus],
    *,
    wait_seconds: float = 3,
) -> object:
    async def poll() -> object:
        while True:
            current = await service.get(job_id)
            if current.status in statuses:
                return current
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(poll(), timeout=wait_seconds)


def download_request(
    part_id: str, *, video_id: str = "video-id", **values: object
) -> DownloadRequest:
    payload: dict[str, object] = {
        "video_id": video_id,
        "part_id": part_id,
        "video_stream_id": f"video-{part_id}",
        "audio_stream_id": f"audio-{part_id}",
        "include_subtitle": False,
        "include_cover": False,
        "include_metadata": False,
    }
    payload.update(values)
    return DownloadRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("preparing", JobStatus.PREPARING),
        ("analysis_preparing", JobStatus.PREPARING),
        ("analysis_media_acquisition", JobStatus.RUNNING),
        ("analysis_asr", JobStatus.RUNNING),
        ("analysis_manifest", JobStatus.RUNNING),
        ("downloading_video", JobStatus.RUNNING),
        ("downloading_audio", JobStatus.RUNNING),
        ("downloading_cover", JobStatus.RUNNING),
        ("downloading_subtitle", JobStatus.RUNNING),
        ("downloading_danmaku", JobStatus.RUNNING),
        ("post_processing", JobStatus.POST_PROCESSING),
    ],
)
def test_job_phase_maps_to_persisted_status(phase: str, expected: JobStatus) -> None:
    assert JobService._status_for_phase(phase) == expected


async def publish_job_artifact(
    service: JobService,
    job_id: str,
    artifact_type: str,
) -> object:
    path = service.artifact_service.root / job_id / f"{artifact_type}.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"fixed-{artifact_type}".encode())
    return await service.artifact_service.create_from_file(
        job_id=job_id,
        artifact_type=artifact_type,
        path=path,
        filename=path.name,
        mime_type="application/octet-stream",
        media_info=None,
    )


async def test_download_and_analysis_have_independent_worker_pools(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, download, analysis, _, _ = job_environment
    download.block = True
    analysis.block = True
    await service.start()
    download_job = await service.create(JobType.DOWNLOAD, dict(download.payload))
    analysis_job = await service.create(JobType.ANALYSIS, dict(analysis.payload))

    await asyncio.wait_for(download.started.wait(), timeout=1)
    await asyncio.wait_for(analysis.started.wait(), timeout=1)
    health = service.health()
    assert health.status == "healthy"
    assert health.worker_count == 2
    assert health.active_by_lane == {"download": 1, "analysis": 1}

    download.release.set()
    analysis.release.set()
    assert (await wait_status(service, download_job.id, {JobStatus.COMPLETED})).status == (
        JobStatus.COMPLETED
    )
    assert (await wait_status(service, analysis_job.id, {JobStatus.COMPLETED})).status == (
        JobStatus.COMPLETED
    )


async def test_pause_resume_cancel_retry_and_failure(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, download, _, _, _ = job_environment
    download.block = True
    await service.start()
    job = await service.create(JobType.DOWNLOAD, dict(download.payload))
    await download.started.wait()

    paused_response = await service.pause(job.id)
    assert paused_response.status == JobStatus.PAUSED
    await wait_status(service, job.id, {JobStatus.PAUSED})
    download.block = False
    resumed = await service.resume(job.id)
    assert resumed.status in {
        JobStatus.QUEUED,
        JobStatus.PREPARING,
        JobStatus.RUNNING,
        JobStatus.POST_PROCESSING,
        JobStatus.COMPLETED,
    }
    completed = await wait_status(service, job.id, {JobStatus.COMPLETED})
    assert completed.progress == 100
    assert completed.video_title == "任务视频"
    assert completed.artifact_ids == []

    download.started.clear()
    download.block = True
    download.release.clear()
    canceled_job = await service.create(JobType.DOWNLOAD, {**download.payload, "part_id": "part-2"})
    await download.started.wait()
    canceled = await service.cancel(canceled_job.id)
    assert canceled.cancel_requested is True
    await wait_status(service, canceled_job.id, {JobStatus.CANCELED})
    assert canceled_job.id in download.discarded

    download.block = False
    retried = await service.retry(canceled_job.id)
    assert retried.retry_count == 1
    assert (await wait_status(service, canceled_job.id, {JobStatus.COMPLETED})).status == (
        JobStatus.COMPLETED
    )

    download.fail = True
    failed_job = await service.create(JobType.DOWNLOAD, {**download.payload, "part_id": "part-3"})
    failed = await wait_status(service, failed_job.id, {JobStatus.FAILED})
    assert failed.error_code == "FIXED_FAILURE"
    download.fail = False
    await service.retry(failed_job.id)
    assert (await wait_status(service, failed_job.id, {JobStatus.COMPLETED})).status == (
        JobStatus.COMPLETED
    )


async def test_queued_pause_cancel_and_invalid_transitions(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, download, analysis, _, _ = job_environment
    paused_job = await service.create(JobType.DOWNLOAD, dict(download.payload))
    assert (await service.pause(paused_job.id)).status == JobStatus.PAUSED
    assert (await service.pause(paused_job.id)).status == JobStatus.PAUSED
    canceled = await service.cancel(paused_job.id)
    assert canceled.status == JobStatus.CANCELED
    assert (await service.cancel(paused_job.id)).status == JobStatus.CANCELED
    with pytest.raises(AppError):
        await service.resume(paused_job.id)

    analysis_job = await service.create(JobType.ANALYSIS, dict(analysis.payload))
    assert (await service.cancel(analysis_job.id)).status == JobStatus.CANCELED
    assert analysis_job.id in analysis.discarded
    assert analysis_job.id not in download.discarded

    completed_job = Job(
        type=JobType.DOWNLOAD,
        status=JobStatus.COMPLETED,
        phase="completed",
        progress=100,
        input_json=dict(download.payload),
        retry_count=0,
        cancel_requested=False,
        finished_at=datetime.now(UTC),
    )
    async with service.session_factory() as session:
        session.add(completed_job)
        await session.commit()
        await session.refresh(completed_job)
    with pytest.raises(AppError):
        await service.cancel(completed_job.id)
    with pytest.raises(AppError):
        await service.pause(completed_job.id)
    with pytest.raises(AppError):
        await service.retry(completed_job.id)
    with pytest.raises(AppError):
        await service.get("missing")


async def test_restart_recovers_active_jobs_and_cancels_analysis_children(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, download, _, factory, _ = job_environment
    download.block = True
    await service.start()
    active = await service.create(JobType.DOWNLOAD, dict(download.payload))
    await download.started.wait()
    await service.stop()
    assert (await service.get(active.id)).status == JobStatus.QUEUED

    child = Job(
        type=JobType.DOWNLOAD,
        status=JobStatus.RUNNING,
        phase="downloading_video",
        progress=40,
        input_json={**download.payload, "analysis_parent_job_id": "parent-job"},
        retry_count=0,
        cancel_requested=False,
        started_at=datetime.now(UTC),
    )
    async with factory() as session:  # type: ignore[operator]
        session.add(child)
        await session.commit()
        await session.refresh(child)
    download.block = False
    download.release.set()
    await service.start()
    assert (await wait_status(service, active.id, {JobStatus.COMPLETED})).status == (
        JobStatus.COMPLETED
    )
    child_read = await service.get(child.id)
    assert child_read.status == JobStatus.CANCELED
    assert child.id in download.discarded


async def test_reconfigure_concurrency_recovers_work_and_health_states(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, download, _, _, _ = job_environment
    assert service.health().status == "stopped"
    await service.start()
    assert service.health().status == "healthy"
    await service.reconfigure_concurrency(download_concurrency=2, analysis_concurrency=2)
    health = service.health()
    assert health.worker_count == 4
    assert health.workers_by_lane == {"download": 2, "analysis": 2}

    worker = service._workers_by_lane["download"][0]
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    assert service.health().status == "degraded"
    await service.stop()
    assert service.health().status == "stopped"

    with pytest.raises(ValueError):
        await service.reconfigure_concurrency(download_concurrency=0, analysis_concurrency=1)
    with pytest.raises(ValueError):
        JobService(
            service.session_factory,
            service.artifact_service,
            download,
            analysis_concurrency=0,
        )


async def test_events_snapshot_handles_old_process_version(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, download, _, _, _ = job_environment
    job = await service.create(JobType.DOWNLOAD, dict(download.payload))
    events = service.events(job.id, last_event_id=f"{job.id}:999", heartbeat_seconds=0.05)
    snapshot = await anext(events)
    assert snapshot is not None and snapshot.event == "snapshot"
    assert snapshot.event_id.startswith(job.id)

    await service.pause(job.id)
    state = await asyncio.wait_for(anext(events), timeout=1)
    assert state is not None and state.job.status == JobStatus.PAUSED
    await events.aclose()
    assert service._event_version("invalid", job.id) == 0
    assert service._event_version("other:3", job.id) == 0

    response = await stream_job_events(job.id, None, service)
    chunk = await anext(response.body_iterator)
    assert b"event: snapshot" in chunk
    assert b"data:" in chunk
    await response.body_iterator.aclose()


async def test_payload_security_limits_and_executor_availability(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, download, _, _, _ = job_environment
    for payload in (
        {"url": "https://signed.example"},
        {"nested": {"cookie": "secret"}},
        {"media_url": "https://signed.example"},
        {"deep": [[[[[[[[[[[[["too deep"]]]]]]]]]]]]]},
    ):
        with pytest.raises(ValueError):
            await service.create(JobType.DOWNLOAD, cast(dict[str, object], payload))
    with pytest.raises(ValueError):
        await service.create(JobType.DOWNLOAD, {"large": "x" * (65 * 1024)})
    with pytest.raises(AppError):
        await service.create(JobType.CLEANUP, {"safe": True})
    await service.start()
    with pytest.raises(RuntimeError):
        service.register_executor(JobType.CLEANUP, download)


async def test_duplicate_download_is_reused_before_workers_start(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, _, _, _, _ = job_environment
    request = DownloadRequest(
        video_id="video-id",
        part_id="part-id",
        video_stream_id="video-stream",
        audio_stream_id="audio-stream",
    )
    first = await service.create_download(request)
    second = await service.create_download(request)
    assert first.reused is False
    assert second.reused is True
    assert first.job.id == second.job.id


async def test_duplicate_analysis_is_reused_only_when_effective_parameters_match(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, _, _, _, _ = job_environment
    payload: dict[str, object] = {
        "video_id": "video-analysis",
        "video_title": "分析复用样例",
        "part_ids": ["part-analysis"],
        "features": ["basic", "media"],
        "source_artifact_ids": {},
        "language": "zh-CN",
        "access_mode": "anonymous",
        "asr_model": "small",
        "device": "auto",
        "ocr_resolution": "balanced",
        "sample_interval_seconds": 2.0,
        "export_formats": ["json"],
        "maximum_duration_seconds": 3600,
        "scene_threshold": 0.3,
        "maximum_keyframes": 24,
        "official_source": "https://www.bilibili.com/video/BV1Analysis/",
    }

    first = await service.create_analysis(payload, reuse_existing=True)
    duplicate = await service.create_analysis(dict(payload), reuse_existing=True)
    changed = await service.create_analysis(
        {**payload, "features": ["basic", "media", "audio"]},
        reuse_existing=True,
    )
    forced = await service.create_analysis(dict(payload), reuse_existing=False)

    assert first.reused is False
    assert duplicate.reused is True
    assert duplicate.id == first.id
    assert duplicate.source_url == "https://www.bilibili.com/video/BV1Analysis/"
    assert changed.id != first.id
    assert forced.id not in {first.id, changed.id}


async def test_concurrent_single_download_creation_is_deduplicated_atomically(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, _, _, factory, _ = job_environment
    request = download_request("part-concurrent")

    first, second = await asyncio.gather(
        service.create_download(request),
        service.create_download(request),
    )

    assert first.job.id == second.job.id
    assert sorted((first.reused, second.reused)) == [False, True]
    async with factory() as session:  # type: ignore[operator]
        assert int(await session.scalar(select(func.count(Job.id))) or 0) == 1


async def test_single_and_batch_creation_share_the_same_deduplication_lock(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, _, _, factory, _ = job_environment
    overlap = download_request("part-overlap")
    batch_request = DownloadBatchRequest(downloads=[overlap, download_request("part-batch-only")])

    single, batch = await asyncio.gather(
        service.create_download(overlap),
        service.create_download_batch(batch_request),
    )

    assert single.job.id == batch.items[0].job.id
    assert sorted((single.reused, batch.items[0].reused)) == [False, True]
    assert batch.items[1].reused is False
    async with factory() as session:  # type: ignore[operator]
        assert int(await session.scalar(select(func.count(Job.id))) or 0) == 2


def test_download_batch_schema_enforces_bounds_identity_and_unique_parts() -> None:
    with pytest.raises(ValidationError):
        DownloadBatchRequest(downloads=[download_request("part-1")])
    with pytest.raises(ValidationError):
        DownloadBatchRequest(downloads=[download_request(f"part-{index}") for index in range(21)])
    with pytest.raises(ValidationError):
        DownloadBatchRequest(downloads=[download_request("part-1"), download_request("part-1")])
    with pytest.raises(ValidationError):
        DownloadBatchRequest(
            downloads=[
                download_request("part-1", video_id="video-1"),
                download_request("part-2", video_id="video-2"),
            ]
        )


async def test_download_batch_prepares_every_item_before_atomic_creation(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, download, _, factory, _ = job_environment
    download.prepare_fail_part_id = "part-2"
    request = DownloadBatchRequest(
        downloads=[download_request("part-1"), download_request("part-2")]
    )

    with pytest.raises(RuntimeError, match="fixed prepare failure"):
        await service.create_download_batch(request)

    async with factory() as session:  # type: ignore[operator]
        assert int(await session.scalar(select(func.count(Job.id))) or 0) == 0
    assert download.prepare_calls == ["part-1", "part-2"]


async def test_download_batch_preserves_order_and_reports_mixed_reuse(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, _, _, factory, _ = job_environment
    existing = await service.create_download(download_request("part-1"))

    result = await service.create_download_batch(
        DownloadBatchRequest(downloads=[download_request("part-1"), download_request("part-2")])
    )

    assert [item.job.part_id for item in result.items] == ["part-1", "part-2"]
    assert [item.reused for item in result.items] == [True, False]
    assert result.items[0].job.id == existing.job.id
    assert result.created_count == 1
    assert result.reused_count == 1
    async with factory() as session:  # type: ignore[operator]
        assert int(await session.scalar(select(func.count(Job.id))) or 0) == 2


@pytest.mark.parametrize(
    "missing_type",
    ["video", "metadata", "cover", "subtitle", "danmaku"],
)
async def test_completed_download_is_not_reused_when_requested_artifact_is_missing(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
    missing_type: str,
) -> None:
    service, _, _, factory, _ = job_environment
    request = download_request(
        "part-complete",
        include_subtitle=True,
        include_cover=True,
        include_metadata=True,
        include_danmaku=True,
    )
    created = await service.create_download(request)
    async with factory() as session:  # type: ignore[operator]
        job = await session.get(Job, created.job.id)
        assert job is not None
        job.status = JobStatus.COMPLETED
        job.phase = "completed"
        job.progress = 100
        job.finished_at = datetime.now(UTC)
        payload = dict(job.input_json)
        payload["companion_outcomes"] = {
            "metadata": "completed",
            "cover": "completed",
            "subtitle": "completed",
            "danmaku": "completed",
        }
        payload["companion_expectations"] = {"subtitle_filenames": ["subtitle.bin"]}
        job.input_json = payload
        await session.commit()
    records = {
        artifact_type: cast(
            Artifact,
            await publish_job_artifact(service, created.job.id, artifact_type),
        )
        for artifact_type in ("video", "metadata", "cover", "subtitle", "danmaku")
    }
    missing = records[missing_type]
    (service.artifact_service.root / missing.storage_key).unlink()

    duplicate = await service.create_download(request)

    assert duplicate.reused is False
    assert duplicate.job.id != created.job.id


async def test_completed_download_reuses_explicitly_unavailable_subtitle(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, _, _, factory, _ = job_environment
    request = download_request("part-no-subtitle", include_subtitle=True)
    created = await service.create_download(request)
    async with factory() as session:  # type: ignore[operator]
        job = await session.get(Job, created.job.id)
        assert job is not None
        job.status = JobStatus.COMPLETED
        job.phase = "completed"
        job.progress = 100
        job.finished_at = datetime.now(UTC)
        payload = dict(job.input_json)
        payload["companion_outcomes"] = {"subtitle": "not_available"}
        job.input_json = payload
        await session.commit()
    await publish_job_artifact(service, created.job.id, "video")

    duplicate = await service.create_download(request)

    assert duplicate.reused is True
    assert duplicate.job.id == created.job.id
    assert duplicate.job.companion_outcomes == {"subtitle": "not_available"}
    assert duplicate.job.has_warnings is True


async def test_legacy_completed_subtitle_without_manifest_is_recreated_safely(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, _, _, factory, _ = job_environment
    request = download_request("part-legacy-subtitle", include_subtitle=True)
    created = await service.create_download(request)
    async with factory() as session:  # type: ignore[operator]
        job = await session.get(Job, created.job.id)
        assert job is not None
        job.status = JobStatus.COMPLETED
        job.phase = "completed"
        job.progress = 100
        job.finished_at = datetime.now(UTC)
        payload = dict(job.input_json)
        payload["companion_outcomes"] = {"subtitle": "completed"}
        job.input_json = payload
        await session.commit()
    await publish_job_artifact(service, created.job.id, "video")
    await publish_job_artifact(service, created.job.id, "subtitle")

    duplicate = await service.create_download(request)

    assert duplicate.reused is False


async def test_completed_download_requires_every_expected_subtitle_file(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, _, _, factory, _ = job_environment
    request = download_request("part-multiple-subtitles", include_subtitle=True)
    created = await service.create_download(request)
    async with factory() as session:  # type: ignore[operator]
        job = await session.get(Job, created.job.id)
        assert job is not None
        job.status = JobStatus.COMPLETED
        job.phase = "completed"
        job.progress = 100
        job.finished_at = datetime.now(UTC)
        payload = dict(job.input_json)
        payload["companion_outcomes"] = {"subtitle": "completed"}
        payload["companion_expectations"] = {
            "subtitle_filenames": ["subtitle-one.bin", "subtitle-two.bin"]
        }
        job.input_json = payload
        await session.commit()
    first = service.artifact_service.root / created.job.id / "subtitle-one.bin"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"first subtitle")
    await service.artifact_service.create_from_file(
        job_id=created.job.id,
        artifact_type="subtitle",
        path=first,
        filename=first.name,
        mime_type="application/json",
        media_info=None,
    )
    await publish_job_artifact(service, created.job.id, "video")

    duplicate = await service.create_download(request)

    assert duplicate.reused is False


async def test_failed_companion_outcome_prevents_reuse_even_if_partial_artifact_exists(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, _, _, factory, _ = job_environment
    request = download_request("part-partial-danmaku", include_danmaku=True)
    created = await service.create_download(request)
    async with factory() as session:  # type: ignore[operator]
        job = await session.get(Job, created.job.id)
        assert job is not None
        job.status = JobStatus.COMPLETED
        job.phase = "completed"
        job.progress = 100
        job.finished_at = datetime.now(UTC)
        payload = dict(job.input_json)
        payload["companion_outcomes"] = {"danmaku": "failed"}
        job.input_json = payload
        await session.commit()
    await publish_job_artifact(service, created.job.id, "video")
    await publish_job_artifact(service, created.job.id, "danmaku")

    warning = await service.get(created.job.id)
    assert warning.companion_outcomes == {"danmaku": "failed"}
    assert warning.has_warnings is True

    duplicate = await service.create_download(request)

    assert duplicate.reused is False


async def test_unexpected_failure_is_sanitized(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, download, _, _, _ = job_environment
    download.unexpected = True
    await service.start()
    job = await service.create(JobType.DOWNLOAD, dict(download.payload))
    failed = await wait_status(service, job.id, {JobStatus.FAILED})
    assert failed.error_code == "JOB_EXECUTION_FAILED"
    assert "signed.example" not in (failed.error_message or "")


async def test_job_and_download_api_contract(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, _, _, _, _ = job_environment
    app = FastAPI()
    app.state.container = SimpleNamespace(job_service=service)
    install_exception_handlers(app)
    app.include_router(downloads_router, prefix="/api/v1")
    app.include_router(jobs_router, prefix="/api/v1")
    payload = {
        "videoId": "video-id",
        "partId": "part-id",
        "videoStreamId": "video-stream",
        "audioStreamId": "audio-stream",
        "container": "mp4",
        "processingMode": "copy",
        "accessMode": "anonymous",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        created = await client.post("/api/v1/downloads", json=payload)
        assert created.status_code == 202
        body = created.json()
        assert body["job"]["videoTitle"] == "任务视频"
        assert "input" not in body["job"]
        assert body["job"]["companionOutcomes"] == {}
        assert body["job"]["hasWarnings"] is False
        job_id = body["job"]["id"]

        listed = await client.get("/api/v1/jobs", params={"type": "download", "status": "queued"})
        assert listed.status_code == 200 and listed.json()["total"] == 1
        active = await client.get("/api/v1/jobs", params={"activeOnly": "true"})
        assert active.status_code == 200 and active.json()["total"] == 1

        second_payload = {
            **payload,
            "partId": "part-id-2",
            "videoStreamId": "video-stream-2",
            "audioStreamId": "audio-stream-2",
        }
        batch = await client.post(
            "/api/v1/downloads/batch",
            json={"downloads": [payload, second_payload]},
        )
        assert batch.status_code == 202
        assert [item["job"]["partId"] for item in batch.json()["items"]] == [
            "part-id",
            "part-id-2",
        ]
        assert batch.json()["createdCount"] == 1
        assert batch.json()["reusedCount"] == 1
        invalid_batch = await client.post(
            "/api/v1/downloads/batch",
            json={"downloads": [payload]},
        )
        assert invalid_batch.status_code == 422

        detail = await client.get(f"/api/v1/jobs/{job_id}")
        assert detail.status_code == 200
        paused = await client.post(f"/api/v1/jobs/{job_id}/pause")
        assert paused.status_code == 200 and paused.json()["status"] == "paused"
        resumed = await client.post(f"/api/v1/jobs/{job_id}/resume")
        assert resumed.status_code == 200 and resumed.json()["status"] == "queued"
        canceled = await client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert canceled.status_code == 200 and canceled.json()["status"] == "canceled"
        retried = await client.post(f"/api/v1/jobs/{job_id}/retry")
        assert retried.status_code == 200 and retried.json()["retryCount"] == 1


async def test_terminal_jobs_can_be_deleted_individually_and_in_batches(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, download, _, factory, _ = job_environment
    terminal_jobs = [
        Job(
            type=JobType.DOWNLOAD,
            status=JobStatus.COMPLETED,
            phase="completed",
            progress=100,
            input_json={**download.payload, "part_id": f"part-{index}"},
            retry_count=0,
            cancel_requested=False,
            finished_at=datetime.now(UTC),
        )
        for index in range(2)
    ]
    active_job = Job(
        type=JobType.DOWNLOAD,
        status=JobStatus.PAUSED,
        phase="paused",
        progress=25,
        input_json={**download.payload, "part_id": "active-part"},
        retry_count=0,
        cancel_requested=False,
    )
    async with factory() as session:  # type: ignore[operator]
        session.add_all([*terminal_jobs, active_job])
        await session.commit()
        for job in [*terminal_jobs, active_job]:
            await session.refresh(job)

    artifact = await publish_job_artifact(service, terminal_jobs[0].id, "video")
    deleted = await service.delete(terminal_jobs[0].id)
    assert deleted.deleted is True
    assert deleted.retained_artifact_count == 1
    assert await service.artifact_service.get(artifact.id)
    async with factory() as session:  # type: ignore[operator]
        retained = await session.get(RetainedFile, artifact.id)
        assert retained is not None
        assert retained.protected is True
        assert retained.retention_reason == "user_retained"

    result = await service.delete_many([terminal_jobs[1].id, active_job.id, "missing-job"])
    assert result.deleted_count == 1
    assert [item.id for item in result.results] == [terminal_jobs[1].id]
    assert result.failed_ids == [active_job.id, "missing-job"]
    assert (await service.get(active_job.id)).status == JobStatus.PAUSED


async def test_job_delete_api_rejects_active_and_preserves_completed_artifacts(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, download, _, factory, _ = job_environment
    completed = Job(
        type=JobType.DOWNLOAD,
        status=JobStatus.COMPLETED,
        phase="completed",
        progress=100,
        input_json=dict(download.payload),
        retry_count=0,
        cancel_requested=False,
        finished_at=datetime.now(UTC),
    )
    queued = Job(
        type=JobType.DOWNLOAD,
        status=JobStatus.QUEUED,
        phase="queued",
        progress=0,
        input_json={**download.payload, "part_id": "queued-part"},
        retry_count=0,
        cancel_requested=False,
    )
    async with factory() as session:  # type: ignore[operator]
        session.add_all([completed, queued])
        await session.commit()
        await session.refresh(completed)
        await session.refresh(queued)
    artifact = await publish_job_artifact(service, completed.id, "audio")

    app = FastAPI()
    app.state.container = SimpleNamespace(job_service=service)
    install_exception_handlers(app)
    app.include_router(jobs_router, prefix="/api/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        blocked = await client.delete(f"/api/v1/jobs/{queued.id}")
        assert blocked.status_code == 409
        assert blocked.json()["error"]["action"].startswith("先取消任务")
        deleted = await client.delete(f"/api/v1/jobs/{completed.id}")
        assert deleted.status_code == 200
        assert deleted.json() == {
            "id": completed.id,
            "deleted": True,
            "retainedArtifactCount": 1,
        }
        batch = await client.post(
            "/api/v1/jobs/batch-delete",
            json={"jobIds": [queued.id, "missing-job"]},
        )
        assert batch.status_code == 200
        assert batch.json()["failedIds"] == [queued.id, "missing-job"]
    assert await service.artifact_service.get(artifact.id)


async def test_maintenance_cleans_terminal_history_and_is_stoppable(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, download, _, factory, _ = job_environment
    old = datetime.now(UTC).replace(year=2020)
    stale = Job(
        type=JobType.DOWNLOAD,
        status=JobStatus.COMPLETED,
        phase="completed",
        progress=100,
        input_json=dict(download.payload),
        retry_count=0,
        cancel_requested=False,
        created_at=old,
        started_at=old,
        finished_at=old,
        updated_at=old,
    )
    async with factory() as session:  # type: ignore[operator]
        session.add(stale)
        await session.commit()
        await session.refresh(stale)

    result = await service.run_maintenance(
        artifact_cleanup_days=None,
        history_retention_days=30,
        now=datetime(2026, 7, 14, tzinfo=UTC),
    )
    assert result["historyJobs"] == 1
    with pytest.raises(AppError):
        await service.get(stale.id)

    calls = 0
    called = asyncio.Event()

    async def provider() -> tuple[int | None, int | None]:
        nonlocal calls
        calls += 1
        called.set()
        return None, None

    await service.start_maintenance(provider, interval_seconds=0.02)
    await asyncio.wait_for(called.wait(), timeout=1)
    await service.start_maintenance(provider, interval_seconds=0.02)
    await service.start()
    before_reconfigure = calls
    called.clear()
    await service.reconfigure_concurrency(download_concurrency=2, analysis_concurrency=1)
    await asyncio.wait_for(called.wait(), timeout=1)
    assert calls > before_reconfigure
    await service.stop_maintenance()
    assert calls >= 1
    with pytest.raises(ValueError):
        await service.start_maintenance(provider, interval_seconds=0)


async def test_history_retention_removes_private_records_and_managed_files_only(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
) -> None:
    service, download, _, factory, _ = job_environment
    old = datetime(2020, 1, 1, tzinfo=UTC)
    current = datetime(2026, 7, 14, tzinfo=UTC)
    history_video = _history_video("BV1History001", 1001, old)
    history_part = VideoPart(
        video=history_video,
        cid=1101,
        page_number=1,
        title="隐私分 P",
        duration=60,
    )
    parsed_only_video = _history_video("BV1History002", 1002, old)
    source_video = _history_video("BV1History003", 1003, old)
    source_part = VideoPart(
        video=source_video,
        cid=1103,
        page_number=1,
        title="仍在使用",
        duration=60,
    )
    recent_video = _history_video("BV1History004", 1004, old)
    recent_part = VideoPart(
        video=recent_video,
        cid=1104,
        page_number=1,
        title="近期记录",
        duration=60,
    )
    managed_job = _terminal_history_job(download.payload, old)
    retained_job = _terminal_history_job(download.payload, old)
    source_job = _terminal_history_job(
        {**download.payload, "video_id": source_video.id, "part_id": source_part.id}, old
    )
    recent_job = _terminal_history_job(
        {**download.payload, "video_id": recent_video.id, "part_id": recent_part.id}, current
    )
    managed_job.input_json = {
        **download.payload,
        "video_id": history_video.id,
        "part_id": history_part.id,
    }
    retained_job.input_json = dict(managed_job.input_json)
    analysis = Analysis(
        video=history_video,
        part=history_part,
        analysis_type="asr",
        status="completed",
        result_json={"document": {"segments": [{"text": "应被删除的私密转写"}]}},
        parameters={"jobId": managed_job.id},
        created_at=old,
        updated_at=old,
    )
    async with factory() as session:  # type: ignore[operator]
        session.add_all(
            [
                history_video,
                parsed_only_video,
                source_video,
                recent_video,
                managed_job,
                retained_job,
                source_job,
                recent_job,
                analysis,
            ]
        )
        await session.commit()

    managed_path = service.artifact_service.root / managed_job.id / "managed-report.json"
    managed_path.parent.mkdir(parents=True, exist_ok=True)
    managed_path.write_text("private analysis payload", encoding="utf-8")
    managed_artifact = await service.artifact_service.create_from_file(
        job_id=managed_job.id,
        artifact_type="report",
        path=managed_path,
        filename=managed_path.name,
        mime_type="application/json",
        media_info=None,
    )
    retained_path = service.artifact_service.root / retained_job.id / "kept-by-user.txt"
    retained_path.parent.mkdir(parents=True, exist_ok=True)
    retained_path.write_text("explicitly retained", encoding="utf-8")
    retained_artifact = await service.artifact_service.create_from_file(
        job_id=retained_job.id,
        artifact_type="transcript",
        path=retained_path,
        filename=retained_path.name,
        mime_type="text/plain",
        media_info=None,
    )
    await service.artifact_service.delete(retained_artifact.id, delete_file=False)
    explicit_retained_path = (
        service.artifact_service.root
        / ".retained"
        / retained_artifact.id
        / retained_artifact.filename
    )
    source_path = service.artifact_service.root / source_job.id / "active-source.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"still referenced")
    source_artifact = await service.artifact_service.create_from_file(
        job_id=source_job.id,
        artifact_type="video",
        path=source_path,
        filename=source_path.name,
        mime_type="video/mp4",
        media_info=None,
    )
    active_job = Job(
        type=JobType.ANALYSIS,
        status=JobStatus.RUNNING,
        phase="analysis",
        progress=25,
        input_json={
            "video_id": source_video.id,
            "part_ids": [source_part.id],
            "source_artifact_ids": {source_part.id: source_artifact.id},
        },
        created_at=current,
        started_at=current,
        updated_at=current,
    )
    async with factory() as session:  # type: ignore[operator]
        session.add(active_job)
        await session.commit()

    result = await service.run_maintenance(
        artifact_cleanup_days=None,
        history_retention_days=30,
        now=current,
    )
    assert result["historyJobs"] == 2
    assert result["historyAnalyses"] == 1
    assert result["historyArtifacts"] == 1
    assert result["historyVideos"] == 2
    managed_retained_path = (
        service.artifact_service.root
        / ".retained"
        / managed_artifact.id
        / managed_artifact.filename
    )
    assert not managed_path.exists()
    assert managed_retained_path.read_text(encoding="utf-8") == "private analysis payload"
    assert explicit_retained_path.read_text(encoding="utf-8") == "explicitly retained"
    assert source_path.read_bytes() == b"still referenced"

    async with factory() as session:  # type: ignore[operator]
        assert await session.get(Job, managed_job.id) is None
        assert await session.get(Job, retained_job.id) is None
        assert await session.get(Analysis, analysis.id) is None
        assert await session.get(Artifact, managed_artifact.id) is None
        managed_retained = await session.get(RetainedFile, managed_artifact.id)
        assert managed_retained is not None
        assert managed_retained.protected is False
        assert managed_retained.retention_reason == "history_retention"
        assert await session.get(RetainedFile, retained_artifact.id) is not None
        assert await session.get(Video, history_video.id) is None
        assert await session.get(Video, parsed_only_video.id) is None
        assert await session.get(Job, source_job.id) is not None
        assert await session.get(Artifact, source_artifact.id) is not None
        assert await session.get(Video, source_video.id) is not None
        assert await session.get(Video, recent_video.id) is not None
    managed_read = await service.artifact_service.get(managed_artifact.id)
    assert managed_read.retained is True
    assert managed_read.video_id is None
    assert managed_read.video_title is None
    assert managed_read.media_info is None
    delivery = await service.artifact_service.delivery(managed_artifact.id, None)
    assert delivery.path == managed_retained_path
    cleanup_cutoff = datetime.now(UTC) + timedelta(days=1)
    assert await service.artifact_service.cleanup_older_than(older_than=cleanup_cutoff) == 1
    assert not managed_retained_path.exists()
    assert explicit_retained_path.exists()


async def test_history_cleanup_rolls_back_database_and_files_together(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, download, _, factory, _ = job_environment
    old = datetime(2020, 1, 1, tzinfo=UTC)
    job = _terminal_history_job(download.payload, old)
    async with factory() as session:  # type: ignore[operator]
        session.add(job)
        await session.commit()
    path = service.artifact_service.root / job.id / "rollback.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"must survive failed cleanup")
    artifact = await service.artifact_service.create_from_file(
        job_id=job.id,
        artifact_type="report",
        path=path,
        filename=path.name,
        mime_type="application/octet-stream",
        media_info=None,
    )

    async def fail_commit(_: AsyncSession) -> None:
        raise RuntimeError("forced transaction failure")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="forced transaction failure"):
        await service.run_maintenance(
            artifact_cleanup_days=None,
            history_retention_days=30,
            now=datetime(2026, 7, 14, tzinfo=UTC),
        )
    assert path.read_bytes() == b"must survive failed cleanup"
    async with factory() as session:  # type: ignore[operator]
        assert await session.scalar(select(Job).where(Job.id == job.id)) is not None
        assert await session.scalar(select(Artifact).where(Artifact.id == artifact.id)) is not None
        assert await session.get(RetainedFile, artifact.id) is None


async def test_history_cleanup_and_thorough_delete_are_serialized(
    job_environment: tuple[JobService, ControlledExecutor, ControlledExecutor, object, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, download, _, factory, _ = job_environment
    old = datetime(2020, 1, 1, tzinfo=UTC)
    current = datetime(2026, 7, 14, tzinfo=UTC)
    job = _terminal_history_job(download.payload, old)
    async with factory() as session:  # type: ignore[operator]
        session.add(job)
        await session.commit()
    path = service.artifact_service.root / job.id / "delete-race.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"must not be resurrected")
    artifact = await service.artifact_service.create_from_file(
        job_id=job.id,
        artifact_type="report",
        path=path,
        filename=path.name,
        mime_type="application/octet-stream",
        media_info=None,
    )
    staged = asyncio.Event()
    release_cleanup = asyncio.Event()
    retain = service.artifact_service.retain_records_for_privacy_cleanup

    async def blocking_retain(
        records: Sequence[Artifact],
        *,
        reason: str = "history_retention",
        protected: bool = False,
    ) -> RetainedFileStage:
        stage = await retain(records, reason=reason, protected=protected)
        staged.set()
        await release_cleanup.wait()
        return stage

    monkeypatch.setattr(
        service.artifact_service,
        "retain_records_for_privacy_cleanup",
        blocking_retain,
    )
    cleanup_task = asyncio.create_task(
        service.run_maintenance(
            artifact_cleanup_days=None,
            history_retention_days=30,
            now=current,
        )
    )
    await asyncio.wait_for(staged.wait(), timeout=2)
    delete_task = asyncio.create_task(
        service.artifact_service.delete(artifact.id, delete_file=True)
    )
    try:
        await asyncio.sleep(0)
        assert not delete_task.done()
    finally:
        release_cleanup.set()

    cleanup_result = await cleanup_task
    delete_result = await delete_task
    assert cleanup_result["historyJobs"] == 1
    assert delete_result.record_deleted is True
    assert delete_result.file_deleted is True
    assert not path.exists()
    assert not (
        service.artifact_service.root / ".retained" / artifact.id / artifact.filename
    ).exists()
    async with factory() as session:  # type: ignore[operator]
        assert await session.get(Artifact, artifact.id) is None
        assert await session.get(RetainedFile, artifact.id) is None


def _history_video(bvid: str, aid: int, parsed_at: datetime) -> Video:
    return Video(
        id=str(uuid.uuid4()),
        provider="bilibili",
        bvid=bvid,
        aid=aid,
        title="历史视频",
        description="包含待清理的历史元数据",
        cover_url="https://i.example.invalid/cover.jpg",
        owner_name="历史用户",
        duration=60,
        stats={},
        tags=[],
        rights={},
        raw_metadata={"privateRawMetadata": "remove-me"},
        parsed_at=parsed_at,
        created_at=parsed_at,
        updated_at=parsed_at,
    )


def _terminal_history_job(payload: dict[str, object], timestamp: datetime) -> Job:
    return Job(
        id=str(uuid.uuid4()),
        type=JobType.DOWNLOAD,
        status=JobStatus.COMPLETED,
        phase="completed",
        progress=100,
        input_json=dict(payload),
        retry_count=0,
        cancel_requested=False,
        created_at=timestamp,
        started_at=timestamp,
        finished_at=timestamp,
        updated_at=timestamp,
    )
