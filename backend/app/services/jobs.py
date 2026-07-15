from __future__ import annotations

import asyncio
import builtins
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, cast, runtime_checkable

from fastapi import status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppError, ErrorCode
from app.db.models import Analysis, Artifact, Job, JobStatus, JobType, Video, VideoPart
from app.media.download import (
    DownloadCanceled,
    DownloadCheckpoint,
    DownloadPaused,
    MediaDownloadError,
)
from app.media.ffmpeg import FFmpegError
from app.schemas.jobs import (
    CompanionOutcome,
    DownloadBatchCreatedResponse,
    DownloadBatchRequest,
    DownloadCreatedResponse,
    DownloadRequest,
    JobEvent,
    JobList,
    JobRead,
    JobRuntimeRead,
)
from app.services.artifacts import ArtifactService
from app.services.downloads import DownloadExecutionReporter, DownloadExecutor

logger = logging.getLogger(__name__)

MaintenancePolicyProvider = Callable[[], Awaitable[tuple[int | None, int | None]]]

_ACTIVE_STATUSES = {
    JobStatus.PREPARING,
    JobStatus.RUNNING,
    JobStatus.POST_PROCESSING,
}
_TRACKED_STATUSES = {*_ACTIVE_STATUSES, JobStatus.QUEUED, JobStatus.PAUSED}
_TERMINAL_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.CANCELED,
    JobStatus.FAILED,
}
_FORBIDDEN_PERSISTED_KEYS = {
    "url",
    "urls",
    "media_url",
    "media_urls",
    "play_url",
    "playback_url",
    "backup_url",
    "backup_urls",
    "cookie",
    "cookies",
    "cookie_header",
    "authorization",
    "encrypted_cookies",
}


class JobExecutor(Protocol):
    async def execute(
        self,
        job: Job,
        *,
        checkpoint: DownloadCheckpoint,
        reporter: DownloadExecutionReporter,
    ) -> Sequence[Artifact]: ...


@runtime_checkable
class PartialCleaner(Protocol):
    async def discard_job_partials(self, job_id: str) -> None: ...


@dataclass(slots=True)
class _RuntimeState:
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed_bytes_per_second: float | None = None
    eta_seconds: float | None = None
    automatic_attempt: int = 0
    sample_bytes: int | None = None
    sample_time: float | None = None


@dataclass(frozen=True, slots=True)
class JobServiceHealth:
    status: Literal["healthy", "degraded", "stopped"]
    started: bool
    stopping: bool
    worker_count: int
    active_count: int
    queue_depth: int
    workers_by_lane: dict[str, int]
    active_by_lane: dict[str, int]
    queue_by_lane: dict[str, int]


class _JobCheckpoint:
    def __init__(self, service: JobService, job_id: str) -> None:
        self._service = service
        self._job_id = job_id

    async def checkpoint(self) -> None:
        requested = self._service._controls.get(self._job_id)
        if requested == "cancel":
            raise DownloadCanceled
        if requested == "pause":
            raise DownloadPaused
        if self._service._stopping:
            raise asyncio.CancelledError


class _JobReporter:
    def __init__(self, service: JobService, job_id: str) -> None:
        self._service = service
        self._job_id = job_id
        self._last_phase = ""
        self._last_emit = 0.0

    async def update(
        self,
        *,
        phase: str,
        progress: float,
        downloaded_bytes: int | None = None,
        total_bytes: int | None = None,
        automatic_attempt: int | None = None,
    ) -> None:
        now = time.monotonic()
        runtime = self._service._runtime.setdefault(self._job_id, _RuntimeState())
        if downloaded_bytes is not None:
            self._update_speed(runtime, downloaded_bytes, now)
            runtime.downloaded_bytes = max(0, downloaded_bytes)
        if total_bytes is not None:
            runtime.total_bytes = max(0, total_bytes)
        if automatic_attempt is not None:
            runtime.automatic_attempt = max(0, automatic_attempt)
        if (
            runtime.speed_bytes_per_second
            and runtime.total_bytes is not None
            and runtime.downloaded_bytes is not None
        ):
            remaining = max(0, runtime.total_bytes - runtime.downloaded_bytes)
            runtime.eta_seconds = remaining / runtime.speed_bytes_per_second
        else:
            runtime.eta_seconds = None

        force = phase != self._last_phase or progress >= 100.0
        if not force and now - self._last_emit < self._service.event_interval_seconds:
            return
        self._last_phase = phase
        self._last_emit = now
        await self._service._persist_progress(
            self._job_id,
            phase=phase,
            progress=progress,
        )

    @staticmethod
    def _update_speed(runtime: _RuntimeState, downloaded_bytes: int, now: float) -> None:
        if runtime.sample_bytes is not None and runtime.sample_time is not None:
            elapsed = now - runtime.sample_time
            delta = downloaded_bytes - runtime.sample_bytes
            if elapsed >= 0.25 and delta >= 0:
                current = delta / elapsed
                previous = runtime.speed_bytes_per_second
                runtime.speed_bytes_per_second = (
                    current if previous is None else previous * 0.65 + current * 0.35
                )
                runtime.sample_bytes = downloaded_bytes
                runtime.sample_time = now
                return
        if runtime.sample_time is None or downloaded_bytes < (runtime.sample_bytes or 0):
            runtime.sample_bytes = downloaded_bytes
            runtime.sample_time = now


class JobService:
    """Persistent bounded worker queue with cooperative controls and resumable events."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        artifact_service: ArtifactService,
        download_executor: DownloadExecutor,
        *,
        concurrency: int = 2,
        analysis_concurrency: int = 1,
        event_interval_seconds: float = 0.75,
    ) -> None:
        if not 1 <= concurrency <= 16:
            raise ValueError("Job concurrency is outside the safe range")
        if not 1 <= analysis_concurrency <= 16:
            raise ValueError("Analysis concurrency is outside the safe range")
        if not 0.1 <= event_interval_seconds <= 5.0:
            raise ValueError("Job event interval is outside the safe range")
        self.session_factory = session_factory
        self.artifact_service = artifact_service
        self.concurrency = concurrency
        self.analysis_concurrency = analysis_concurrency
        self.event_interval_seconds = event_interval_seconds
        self._executors: dict[JobType, JobExecutor] = {
            JobType.DOWNLOAD: download_executor,
        }
        self._queues: dict[str, asyncio.Queue[str]] = {
            "download": asyncio.Queue(),
            "analysis": asyncio.Queue(),
        }
        self._scheduled: set[str] = set()
        self._active: set[str] = set()
        self._active_lanes: dict[str, str] = {}
        self._inactive_events: dict[str, asyncio.Event] = {}
        self._controls: dict[str, str] = {}
        self._runtime: dict[str, _RuntimeState] = {}
        self._versions: dict[str, int] = {}
        self._event_kinds: dict[str, str] = {}
        self._condition = asyncio.Condition()
        self._mutation_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._workers: list[asyncio.Task[None]] = []
        self._workers_by_lane: dict[str, list[asyncio.Task[None]]] = {
            "download": [],
            "analysis": [],
        }
        self._started = False
        self._stopping = False
        self._maintenance_task: asyncio.Task[None] | None = None
        self._maintenance_stop = asyncio.Event()
        self._maintenance_provider: MaintenancePolicyProvider | None = None
        self._maintenance_interval_seconds = 3600.0

    def register_executor(self, job_type: JobType, executor: JobExecutor) -> None:
        if self._started:
            raise RuntimeError("Job executors must be registered before the service starts")
        self._executors[job_type] = executor

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started:
                return
            self._stopping = False
            recoverable = await self._recover_persisted_jobs()
            self._started = True
            self._workers_by_lane = {
                "download": [
                    asyncio.create_task(
                        self._worker("download", index),
                        name=f"download-job-worker-{index}",
                    )
                    for index in range(self.concurrency)
                ],
                "analysis": [
                    asyncio.create_task(
                        self._worker("analysis", index),
                        name=f"analysis-job-worker-{index}",
                    )
                    for index in range(self.analysis_concurrency)
                ],
            }
            self._workers = [
                *self._workers_by_lane["download"],
                *self._workers_by_lane["analysis"],
            ]
            for job_id, job_type in recoverable:
                self._enqueue(job_id, job_type)
            if self._maintenance_provider is not None:
                await self.start_maintenance(
                    self._maintenance_provider,
                    interval_seconds=self._maintenance_interval_seconds,
                )

    async def stop(self) -> None:
        await self.stop_maintenance(preserve_configuration=True)
        async with self._lifecycle_lock:
            if not self._started:
                return
            self._stopping = True
            workers = tuple(self._workers)
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            await self._recover_interrupted_rows()
            self._drain_queue()
            self._workers.clear()
            self._workers_by_lane = {"download": [], "analysis": []}
            self._scheduled.clear()
            self._active.clear()
            self._active_lanes.clear()
            self._controls.clear()
            self._started = False
            self._stopping = False

    async def start_maintenance(
        self,
        policy_provider: MaintenancePolicyProvider,
        *,
        interval_seconds: float = 3600.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Maintenance interval must be positive")
        self._maintenance_provider = policy_provider
        self._maintenance_interval_seconds = interval_seconds
        if self._maintenance_task is not None and not self._maintenance_task.done():
            return
        self._maintenance_stop.clear()
        self._maintenance_task = asyncio.create_task(
            self._maintenance_loop(policy_provider, interval_seconds),
            name="job-artifact-maintenance",
        )

    async def stop_maintenance(self, *, preserve_configuration: bool = False) -> None:
        task = self._maintenance_task
        if task is None:
            if not preserve_configuration:
                self._maintenance_provider = None
            return
        self._maintenance_stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._maintenance_task = None
        if not preserve_configuration:
            self._maintenance_provider = None

    async def run_maintenance(
        self,
        *,
        artifact_cleanup_days: int | None,
        history_retention_days: int | None,
        now: datetime | None = None,
    ) -> dict[str, int]:
        current = now or datetime.now(UTC)
        expired = await self.artifact_service.cleanup_expired(now=current)
        aged_artifacts = 0
        if artifact_cleanup_days is not None:
            aged_artifacts = await self.artifact_service.cleanup_older_than(
                older_than=current - timedelta(days=artifact_cleanup_days)
            )
        crash_remnants = await self.artifact_service.cleanup_untracked(
            older_than=current - timedelta(days=1)
        )
        history = 0
        history_analyses = 0
        history_artifacts = 0
        history_videos = 0
        if history_retention_days is not None:
            history_result = await self._cleanup_job_history(
                current - timedelta(days=history_retention_days)
            )
            history = history_result["jobs"]
            history_analyses = history_result["analyses"]
            history_artifacts = history_result["artifacts"]
            history_videos = history_result["videos"]
        return {
            "expiredArtifacts": expired,
            "agedArtifacts": aged_artifacts,
            "crashRemnants": crash_remnants,
            "historyJobs": history,
            "historyAnalyses": history_analyses,
            "historyArtifacts": history_artifacts,
            "historyVideos": history_videos,
        }

    async def _maintenance_loop(
        self,
        provider: MaintenancePolicyProvider,
        interval_seconds: float,
    ) -> None:
        while not self._maintenance_stop.is_set():
            try:
                artifact_days, history_days = await provider()
                await self.run_maintenance(
                    artifact_cleanup_days=artifact_days,
                    history_retention_days=history_days,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Scheduled maintenance failed",
                    extra={"event": "scheduled_maintenance_failed"},
                )
            try:
                await asyncio.wait_for(
                    self._maintenance_stop.wait(),
                    timeout=interval_seconds,
                )
            except TimeoutError:
                continue

    async def _cleanup_job_history(self, cutoff: datetime) -> dict[str, int]:
        blocked_job_ids = set(self._active) | set(self._scheduled)
        identifiers: list[str] = []
        analysis_count = 0
        artifact_count = 0
        video_count = 0
        async with (
            self._mutation_lock,
            self.artifact_service.mutation_guard(),
            self.session_factory() as session,
        ):
            filters = [
                Job.status.in_(_TERMINAL_STATUSES),
                func.coalesce(Job.finished_at, Job.updated_at, Job.created_at) < cutoff,
            ]
            if blocked_job_ids:
                filters.append(Job.id.not_in(blocked_job_ids))
            candidates = list(
                (
                    await session.scalars(
                        select(Job).where(*filters).options(selectinload(Job.artifacts))
                    )
                ).all()
            )
            candidate_by_id = {job.id: job for job in candidates}
            candidate_ids = set(candidate_by_id)
            remaining_statement = select(Job.id, Job.status, Job.input_json)
            if candidate_ids:
                remaining_statement = remaining_statement.where(Job.id.not_in(candidate_ids))
            remaining_rows = list((await session.execute(remaining_statement)).all())
            active_video_ids = {
                video_id
                for _, job_status, payload in remaining_rows
                if job_status not in _TERMINAL_STATUSES
                if (video_id := self._payload_identifier(payload, "video_id")) is not None
            }
            externally_referenced_artifacts: set[str] = set()
            for _, _, payload in remaining_rows:
                externally_referenced_artifacts.update(self._payload_artifact_ids(payload))

            artifact_owner = {
                artifact.id: job.id for job in candidates for artifact in job.artifacts
            }
            protected_candidates = {
                job.id
                for job in candidates
                if self._payload_identifier(job.input_json, "video_id") in active_video_ids
                or any(artifact.id in externally_referenced_artifacts for artifact in job.artifacts)
            }
            pending = list(protected_candidates)
            while pending:
                protected = candidate_by_id[pending.pop()]
                for artifact_id in self._payload_artifact_ids(protected.input_json):
                    owner = artifact_owner.get(artifact_id)
                    if owner is not None and owner not in protected_candidates:
                        protected_candidates.add(owner)
                        pending.append(owner)

            deleted_jobs = [job for job in candidates if job.id not in protected_candidates]
            identifiers = [job.id for job in deleted_jobs]
            artifacts = [artifact for job in deleted_jobs for artifact in job.artifacts]
            analysis_ids: list[str] = []
            if identifiers:
                analysis_ids = list(
                    (
                        await session.scalars(
                            select(Analysis.id).where(
                                Analysis.parameters["jobId"].as_string().in_(identifiers)
                            )
                        )
                    ).all()
                )
            analysis_count = len(analysis_ids)

            retention_stage = await self.artifact_service.retain_records_for_privacy_cleanup(
                artifacts
            )
            artifact_count = len(retention_stage.records)
            try:
                session.add_all(retention_stage.records)
                if analysis_ids:
                    await session.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
                if identifiers:
                    await session.execute(delete(Job).where(Job.id.in_(identifiers)))
                await session.flush()

                remaining_payloads = [payload for _, _, payload in remaining_rows]
                remaining_payloads.extend(
                    candidate_by_id[job_id].input_json for job_id in protected_candidates
                )
                referenced_video_ids = {
                    video_id
                    for payload in remaining_payloads
                    if (video_id := self._payload_identifier(payload, "video_id")) is not None
                }
                referenced_part_ids: set[str] = set()
                for payload in remaining_payloads:
                    referenced_part_ids.update(self._payload_part_ids(payload))
                if referenced_part_ids:
                    referenced_video_ids.update(
                        (
                            await session.scalars(
                                select(VideoPart.video_id).where(
                                    VideoPart.id.in_(referenced_part_ids)
                                )
                            )
                        ).all()
                    )
                orphan_video_ids = list(
                    (
                        await session.scalars(
                            select(Video.id).where(
                                Video.parsed_at < cutoff,
                                ~Video.analyses.any(),
                            )
                        )
                    ).all()
                )
                deletable_video_ids = [
                    video_id
                    for video_id in orphan_video_ids
                    if video_id not in referenced_video_ids
                ]
                if deletable_video_ids:
                    await session.execute(delete(Video).where(Video.id.in_(deletable_video_ids)))
                video_count = len(deletable_video_ids)
                await session.commit()
            except Exception:
                await session.rollback()
                await self.artifact_service.rollback_retained_stage(retention_stage)
                raise
            await self.artifact_service.complete_retained_stage(retention_stage)
        for job_id in identifiers:
            self._runtime.pop(job_id, None)
            self._versions.pop(job_id, None)
            self._event_kinds.pop(job_id, None)
            self._inactive_events.pop(job_id, None)
            self._controls.pop(job_id, None)
        return {
            "jobs": len(identifiers),
            "analyses": analysis_count,
            "artifacts": artifact_count,
            "videos": video_count,
        }

    @staticmethod
    def _payload_identifier(payload: object, key: str) -> str | None:
        if not isinstance(payload, Mapping):
            return None
        value = payload.get(key)
        return value if isinstance(value, str) and value else None

    @classmethod
    def _payload_artifact_ids(cls, payload: object) -> set[str]:
        if not isinstance(payload, Mapping):
            return set()
        identifiers: set[str] = set()
        direct = payload.get("artifact_id")
        if isinstance(direct, str) and direct:
            identifiers.add(direct)
        many = payload.get("artifact_ids")
        if isinstance(many, list):
            identifiers.update(item for item in many if isinstance(item, str) and item)
        sources = payload.get("source_artifact_ids")
        if isinstance(sources, Mapping):
            identifiers.update(item for item in sources.values() if isinstance(item, str) and item)
        return identifiers

    @classmethod
    def _payload_part_ids(cls, payload: object) -> set[str]:
        if not isinstance(payload, Mapping):
            return set()
        identifiers: set[str] = set()
        direct = cls._payload_identifier(payload, "part_id")
        if direct is not None:
            identifiers.add(direct)
        many = payload.get("part_ids")
        if isinstance(many, list):
            identifiers.update(item for item in many if isinstance(item, str) and item)
        return identifiers

    async def reconfigure_concurrency(
        self,
        *,
        download_concurrency: int,
        analysis_concurrency: int,
    ) -> None:
        if not 1 <= download_concurrency <= 16:
            raise ValueError("Job concurrency is outside the safe range")
        if not 1 <= analysis_concurrency <= 16:
            raise ValueError("Analysis concurrency is outside the safe range")
        was_started = self._started
        if was_started:
            await self.stop()
        self.concurrency = download_concurrency
        self.analysis_concurrency = analysis_concurrency
        if was_started:
            await self.start()

    def health(self) -> JobServiceHealth:
        workers_by_lane = {
            lane: sum(1 for worker in workers if not worker.done())
            for lane, workers in self._workers_by_lane.items()
        }
        queue_by_lane = {lane: queue.qsize() for lane, queue in self._queues.items()}
        active_by_lane = {
            lane: sum(1 for value in self._active_lanes.values() if value == lane)
            for lane in self._queues
        }
        expected = self.concurrency + self.analysis_concurrency
        live = sum(workers_by_lane.values())
        health_status: Literal["healthy", "degraded", "stopped"]
        if not self._started:
            health_status = "stopped"
        elif self._stopping or live != expected:
            health_status = "degraded"
        else:
            health_status = "healthy"
        return JobServiceHealth(
            status=health_status,
            started=self._started,
            stopping=self._stopping,
            worker_count=live,
            active_count=len(self._active),
            queue_depth=sum(queue_by_lane.values()),
            workers_by_lane=workers_by_lane,
            active_by_lane=active_by_lane,
            queue_by_lane=queue_by_lane,
        )

    async def create_download(self, request: DownloadRequest) -> DownloadCreatedResponse:
        executor = self._executors.get(JobType.DOWNLOAD)
        if not isinstance(executor, DownloadExecutor):
            raise RuntimeError("Download executor is not configured")
        payload = await executor.prepare(request)
        self._validate_persisted_payload(payload)
        async with self._mutation_lock:
            if request.reuse_existing:
                duplicate = await self._find_duplicate_download(payload)
                if duplicate is not None:
                    return DownloadCreatedResponse(job=await self._to_read(duplicate), reused=True)
            record = self._new_job_record(JobType.DOWNLOAD, payload)
            async with self.session_factory() as session:
                session.add(record)
                await session.commit()
                await session.refresh(record)
                session.expunge(record)
        self._enqueue(record.id, record.type)
        await self._publish(record.id, "state")
        return DownloadCreatedResponse(job=await self._to_read(record), reused=False)

    async def create_download_batch(
        self, request: DownloadBatchRequest
    ) -> DownloadBatchCreatedResponse:
        executor = self._executors.get(JobType.DOWNLOAD)
        if not isinstance(executor, DownloadExecutor):
            raise RuntimeError("Download executor is not configured")

        prepared: list[dict[str, object]] = []
        for item in request.downloads:
            payload = await executor.prepare(item)
            self._validate_persisted_payload(payload)
            prepared.append(payload)
        prepared_parts = [
            (str(payload.get("video_id")), str(payload.get("part_id"))) for payload in prepared
        ]
        if len(prepared_parts) != len(set(prepared_parts)):
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "批量下载包含重复的视频分 P",
                action="移除重复分 P 后重新提交",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        resolved: list[tuple[Job, bool] | None] = [None] * len(prepared)
        new_records: list[tuple[int, Job]] = []
        async with self._mutation_lock:
            for index, (item, payload) in enumerate(zip(request.downloads, prepared, strict=True)):
                duplicate = (
                    await self._find_duplicate_download(payload) if item.reuse_existing else None
                )
                if duplicate is not None:
                    resolved[index] = (duplicate, True)
                    continue
                record = self._new_job_record(JobType.DOWNLOAD, payload)
                new_records.append((index, record))
            if new_records:
                async with self.session_factory() as session:
                    session.add_all([record for _, record in new_records])
                    await session.commit()
                    for _, record in new_records:
                        await session.refresh(record)
                        session.expunge(record)
                for index, record in new_records:
                    resolved[index] = (record, False)

        for _, record in new_records:
            self._enqueue(record.id, record.type)
            await self._publish(record.id, "state")
        responses: list[DownloadCreatedResponse] = []
        for entry in resolved:
            if entry is None:
                raise RuntimeError("Batch download result was not resolved")
            record, reused = entry
            responses.append(
                DownloadCreatedResponse(job=await self._to_read(record), reused=reused)
            )
        reused_count = sum(item.reused for item in responses)
        return DownloadBatchCreatedResponse(
            items=responses,
            created_count=len(responses) - reused_count,
            reused_count=reused_count,
        )

    async def create(self, job_type: JobType, payload: dict[str, object]) -> JobRead:
        if job_type not in self._executors:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "当前任务类型没有可用执行器",
                action="检查相应处理能力是否已安装并启用",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        self._validate_persisted_payload(payload)
        job = self._new_job_record(job_type, payload)
        async with self.session_factory() as session:
            session.add(job)
            await session.commit()
            await session.refresh(job)
            session.expunge(job)
        self._enqueue(job.id, job.type)
        await self._publish(job.id, "state")
        return await self.get(job.id)

    async def create_analysis(
        self,
        payload: dict[str, object],
        *,
        reuse_existing: bool,
    ) -> JobRead:
        self._validate_persisted_payload(payload)
        async with self._mutation_lock:
            if reuse_existing:
                duplicate = await self._find_duplicate_analysis(payload)
                if duplicate is not None:
                    result = await self._to_read(duplicate)
                    return result.model_copy(update={"reused": True})
            record = self._new_job_record(JobType.ANALYSIS, payload)
            async with self.session_factory() as session:
                session.add(record)
                await session.commit()
                await session.refresh(record)
                session.expunge(record)
        self._enqueue(record.id, record.type)
        await self._publish(record.id, "state")
        return await self.get(record.id)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        job_type: JobType | None = None,
        job_status: JobStatus | None = None,
        active_only: bool = False,
    ) -> JobList:
        filters = []
        if job_type is not None:
            filters.append(Job.type == job_type)
        if job_status is not None:
            filters.append(Job.status == job_status)
        if active_only:
            filters.append(Job.status.in_(_TRACKED_STATUSES))
        async with self.session_factory() as session:
            total = await session.scalar(select(func.count(Job.id)).where(*filters))
            jobs = list(
                (
                    await session.scalars(
                        select(Job)
                        .where(*filters)
                        .options(selectinload(Job.artifacts))
                        .order_by(Job.created_at.desc())
                        .offset(offset)
                        .limit(limit)
                    )
                ).all()
            )
            for job in jobs:
                session.expunge(job)
        return JobList(
            items=[self._model_to_read(job) for job in jobs],
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def _new_job_record(job_type: JobType, payload: dict[str, object]) -> Job:
        return Job(
            type=job_type,
            status=JobStatus.QUEUED,
            phase="queued",
            progress=0.0,
            input_json=payload,
            retry_count=0,
            cancel_requested=False,
        )

    async def get(self, job_id: str) -> JobRead:
        job = await self._load(job_id)
        if job is None:
            raise self._not_found()
        return self._model_to_read(job)

    async def cancel(self, job_id: str) -> JobRead:
        clean_now = False
        job_type: JobType | None = None
        async with self._mutation_lock, self.session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                raise self._not_found()
            if job.status == JobStatus.CANCELED:
                return await self.get(job_id)
            if job.status in {JobStatus.COMPLETED, JobStatus.FAILED}:
                raise self._invalid_transition("该任务已经结束，无法取消")
            job.cancel_requested = True
            if job.status in {JobStatus.QUEUED, JobStatus.PAUSED}:
                job.status = JobStatus.CANCELED
                job.phase = "canceled"
                job.finished_at = datetime.now(UTC)
                clean_now = True
                job_type = job.type
            else:
                job.phase = "canceling"
            await session.commit()
            self._controls[job_id] = "cancel"
        if clean_now and job_type is not None:
            await self._discard_partials(job_id, job_type)
        await self._publish(job_id, "state")
        return await self.get(job_id)

    async def pause(self, job_id: str) -> JobRead:
        wait_for_worker = False
        async with self._mutation_lock, self.session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                raise self._not_found()
            if job.status == JobStatus.PAUSED:
                return await self.get(job_id)
            if job.status not in {JobStatus.QUEUED, *_ACTIVE_STATUSES}:
                raise self._invalid_transition("只有排队或执行中的任务可以暂停")
            wait_for_worker = job.status in _ACTIVE_STATUSES
            if wait_for_worker:
                job.phase = "pausing"
                event = self._inactive_events.setdefault(job_id, asyncio.Event())
                event.clear()
            else:
                job.status = JobStatus.PAUSED
                job.phase = "paused"
            job.cancel_requested = False
            await session.commit()
            self._controls[job_id] = "pause"
        await self._publish(job_id, "state")
        if wait_for_worker:
            await self._wait_until_inactive(job_id)
        return await self.get(job_id)

    async def _wait_until_inactive(self, job_id: str) -> None:
        if job_id not in self._active:
            return
        event = self._inactive_events.setdefault(job_id, asyncio.Event())
        try:
            async with asyncio.timeout(5.0):
                await event.wait()
        except TimeoutError as exc:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "任务暂停请求暂未被 Worker 确认",
                action="稍后刷新任务状态；若持续无响应可取消任务",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc

    async def resume(self, job_id: str) -> JobRead:
        async with self._mutation_lock, self.session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                raise self._not_found()
            if job.status != JobStatus.PAUSED:
                raise self._invalid_transition("只有已暂停任务可以继续")
            job.status = JobStatus.QUEUED
            job.phase = "queued"
            job.cancel_requested = False
            await session.commit()
            self._controls.pop(job_id, None)
        self._enqueue(job_id, job.type)
        await self._publish(job_id, "state")
        return await self.get(job_id)

    async def retry(self, job_id: str) -> JobRead:
        async with self._mutation_lock, self.session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                raise self._not_found()
            if job.status not in {JobStatus.FAILED, JobStatus.CANCELED}:
                raise self._invalid_transition("只有失败或已取消任务可以重试")
            job.status = JobStatus.QUEUED
            job.phase = "queued"
            job.progress = 0.0
            job.error_code = None
            job.error_message = None
            job.cancel_requested = False
            job.retry_count += 1
            job.started_at = None
            job.finished_at = None
            await session.commit()
            self._controls.pop(job_id, None)
            self._runtime.pop(job_id, None)
        self._enqueue(job_id, job.type)
        await self._publish(job_id, "state")
        return await self.get(job_id)

    async def events(
        self,
        job_id: str,
        *,
        last_event_id: str | None = None,
        heartbeat_seconds: float = 15.0,
    ) -> AsyncIterator[JobEvent | None]:
        await self.get(job_id)
        seen = self._event_version(last_event_id, job_id)
        initial = await self.get(job_id)
        current = self._versions.get(job_id, 0)
        yield self._event(job_id, current, "snapshot", initial)
        # A snapshot supersedes any in-memory event history.  Versions restart
        # after a process restart, so retaining a larger client version here
        # would suppress every subsequent event until the old number is reached.
        seen = current
        while True:
            try:
                target_version = seen

                def has_update(version: int = target_version) -> bool:
                    return self._versions.get(job_id, 0) > version

                async with self._condition:
                    await asyncio.wait_for(
                        self._condition.wait_for(has_update),
                        timeout=heartbeat_seconds,
                    )
            except TimeoutError:
                yield None
                continue
            version = self._versions.get(job_id, seen)
            event_kind = self._event_kinds.get(job_id, "progress")
            yield self._event(job_id, version, event_kind, await self.get(job_id))
            seen = version

    async def _worker(self, lane: str, index: int) -> None:
        queue = self._queues[lane]
        while True:
            job_id = await queue.get()
            self._scheduled.discard(job_id)
            try:
                job = await self._claim(job_id)
                if job is None:
                    continue
                self._active.add(job_id)
                self._active_lanes[job_id] = lane
                self._controls.pop(job_id, None)
                executor = self._executors.get(job.type)
                if executor is None:
                    await self._mark_failed(
                        job_id,
                        code="JOB_EXECUTOR_UNAVAILABLE",
                        message="任务执行器不可用",
                    )
                    continue
                reporter = _JobReporter(self, job_id)
                checkpoint = _JobCheckpoint(self, job_id)
                try:
                    await executor.execute(job, checkpoint=checkpoint, reporter=reporter)
                except DownloadPaused:
                    await self._mark_paused(job_id)
                except DownloadCanceled:
                    await self._mark_canceled(job_id, job.type)
                except asyncio.CancelledError:
                    await self._mark_interrupted(job_id)
                    raise
                except AppError as exc:
                    await self._mark_failed(job_id, code=exc.code.value, message=exc.message)
                except FFmpegError as exc:
                    await self._mark_failed(job_id, code=exc.code, message=exc.message)
                except MediaDownloadError as exc:
                    await self._mark_failed(job_id, code=exc.code, message=exc.message)
                except Exception:
                    logger.exception(
                        "Job execution failed unexpectedly",
                        extra={"event": "job_unhandled_failure", "job_id": job_id},
                    )
                    await self._mark_failed(
                        job_id,
                        code="JOB_EXECUTION_FAILED",
                        message="任务执行失败，请查看脱敏诊断后重试",
                    )
                else:
                    await self._mark_completed(job_id)
            finally:
                self._active.discard(job_id)
                self._active_lanes.pop(job_id, None)
                inactive = self._inactive_events.pop(job_id, None)
                if inactive is not None:
                    inactive.set()
                self._controls.pop(job_id, None)
                queue.task_done()
                logger.debug(
                    "Job worker cycle finished",
                    extra={
                        "event": "job_worker_cycle",
                        "worker_index": index,
                        "worker_lane": lane,
                    },
                )

    async def _claim(self, job_id: str) -> Job | None:
        async with self._mutation_lock, self.session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status != JobStatus.QUEUED:
                return None
            if job.cancel_requested:
                job.status = JobStatus.CANCELED
                job.phase = "canceled"
                job.finished_at = datetime.now(UTC)
                await session.commit()
                await self._publish(job_id, "state")
                return None
            job.status = JobStatus.PREPARING
            job.phase = "preparing"
            job.started_at = job.started_at or datetime.now(UTC)
            job.finished_at = None
            await session.commit()
            session.expunge(job)
        await self._publish(job_id, "state")
        return job

    async def _persist_progress(self, job_id: str, *, phase: str, progress: float) -> None:
        safe_phase = phase[:64] if phase else "running"
        async with self.session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status not in _ACTIVE_STATUSES:
                return
            job.phase = safe_phase
            job.progress = min(99.9, max(job.progress, max(0.0, progress)))
            job.status = self._status_for_phase(safe_phase)
            await session.commit()
        await self._publish(job_id, "progress")

    async def _mark_completed(self, job_id: str) -> None:
        async with self.session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            if job.cancel_requested:
                await session.rollback()
                await self._mark_canceled(job_id, job.type)
                return
            if job.status == JobStatus.PAUSED:
                return
            job.status = JobStatus.COMPLETED
            job.phase = "completed"
            job.progress = 100.0
            job.error_code = None
            job.error_message = None
            job.finished_at = datetime.now(UTC)
            await session.commit()
        await self._publish(job_id, "state")

    async def _mark_paused(self, job_id: str) -> None:
        async with self.session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status in _TERMINAL_STATUSES:
                return
            job.status = JobStatus.PAUSED
            job.phase = "paused"
            job.cancel_requested = False
            await session.commit()
        await self._publish(job_id, "state")

    async def _mark_canceled(self, job_id: str, job_type: JobType) -> None:
        async with self.session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status == JobStatus.COMPLETED:
                return
            job.status = JobStatus.CANCELED
            job.phase = "canceled"
            job.cancel_requested = True
            job.finished_at = datetime.now(UTC)
            await session.commit()
        await self._discard_partials(job_id, job_type)
        await self._publish(job_id, "state")

    async def _mark_failed(self, job_id: str, *, code: str, message: str) -> None:
        async with self.session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status in {JobStatus.COMPLETED, JobStatus.CANCELED}:
                return
            if job.cancel_requested:
                job_type = job.type
                await session.rollback()
                await self._mark_canceled(job_id, job_type)
                return
            job.status = JobStatus.FAILED
            job.phase = "failed"
            job.error_code = code[:64]
            job.error_message = message[:512]
            job.finished_at = datetime.now(UTC)
            await session.commit()
        await self._publish(job_id, "state")

    async def _mark_interrupted(self, job_id: str) -> None:
        async with self.session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status in _TERMINAL_STATUSES | {JobStatus.PAUSED}:
                return
            if job.cancel_requested:
                job.status = JobStatus.CANCELED
                job.phase = "canceled"
                job.finished_at = datetime.now(UTC)
            else:
                job.status = JobStatus.QUEUED
                job.phase = "recovering"
            await session.commit()
        await self._publish(job_id, "state")

    async def _recover_persisted_jobs(self) -> builtins.list[tuple[str, JobType]]:
        recoverable: builtins.list[tuple[str, JobType]] = []
        child_downloads: builtins.list[str] = []
        statuses = {JobStatus.QUEUED, *_ACTIVE_STATUSES}
        async with self.session_factory() as session:
            jobs = list((await session.scalars(select(Job).where(Job.status.in_(statuses)))).all())
            now = datetime.now(UTC)
            for job in jobs:
                if job.cancel_requested:
                    job.status = JobStatus.CANCELED
                    job.phase = "canceled"
                    job.finished_at = now
                elif (
                    job.type == JobType.DOWNLOAD
                    and isinstance(job.input_json, dict)
                    and isinstance(job.input_json.get("analysis_parent_job_id"), str)
                ):
                    job.status = JobStatus.CANCELED
                    job.phase = "canceled"
                    job.cancel_requested = True
                    job.finished_at = now
                    child_downloads.append(job.id)
                elif job.type not in self._executors:
                    job.status = JobStatus.FAILED
                    job.phase = "failed"
                    job.error_code = "JOB_EXECUTOR_UNAVAILABLE"
                    job.error_message = "任务执行器不可用"
                    job.finished_at = now
                else:
                    job.status = JobStatus.QUEUED
                    job.phase = "recovering" if job.started_at is not None else "queued"
                    recoverable.append((job.id, job.type))
            await session.commit()
        for job_id in child_downloads:
            await self._discard_partials(job_id, JobType.DOWNLOAD)
        return recoverable

    async def _recover_interrupted_rows(self) -> None:
        async with self.session_factory() as session:
            jobs = list(
                (await session.scalars(select(Job).where(Job.status.in_(_ACTIVE_STATUSES)))).all()
            )
            for job in jobs:
                if job.cancel_requested:
                    job.status = JobStatus.CANCELED
                    job.phase = "canceled"
                    job.finished_at = datetime.now(UTC)
                else:
                    job.status = JobStatus.QUEUED
                    job.phase = "recovering"
            await session.commit()

    async def _find_duplicate_download(self, payload: dict[str, object]) -> Job | None:
        statuses = {
            JobStatus.QUEUED,
            JobStatus.PREPARING,
            JobStatus.RUNNING,
            JobStatus.POST_PROCESSING,
            JobStatus.PAUSED,
            JobStatus.COMPLETED,
        }
        async with self.session_factory() as session:
            jobs = list(
                (
                    await session.scalars(
                        select(Job)
                        .where(Job.type == JobType.DOWNLOAD, Job.status.in_(statuses))
                        .options(selectinload(Job.artifacts))
                        .order_by(Job.created_at.desc())
                        .limit(200)
                    )
                ).all()
            )
            for job in jobs:
                if not self._same_download(cast(dict[str, object], job.input_json), payload):
                    continue
                completed_missing_artifacts = (
                    job.status == JobStatus.COMPLETED
                    and not await self._completed_download_satisfies(job, payload)
                )
                if completed_missing_artifacts:
                    continue
                session.expunge(job)
                return job
        return None

    async def _find_duplicate_analysis(self, payload: dict[str, object]) -> Job | None:
        statuses = {
            JobStatus.QUEUED,
            JobStatus.PREPARING,
            JobStatus.RUNNING,
            JobStatus.POST_PROCESSING,
            JobStatus.PAUSED,
            JobStatus.COMPLETED,
        }
        async with self.session_factory() as session:
            jobs = list(
                (
                    await session.scalars(
                        select(Job)
                        .where(Job.type == JobType.ANALYSIS, Job.status.in_(statuses))
                        .options(selectinload(Job.artifacts))
                        .order_by(Job.created_at.desc())
                        .limit(200)
                    )
                ).all()
            )
            for job in jobs:
                if self._same_analysis(cast(dict[str, object], job.input_json), payload):
                    session.expunge(job)
                    return job
        return None

    async def _completed_download_satisfies(self, job: Job, payload: Mapping[str, object]) -> bool:
        artifacts = await self.artifact_service.existing_all_for_job(job.id)
        artifact_types = {item.type for item in artifacts}
        container = str(payload.get("container", ""))
        primary_type = "audio" if container in {"m4a", "mp3", "flac"} else "video"
        if not ({primary_type, "media"} & artifact_types):
            return False
        stored_payload = cast(dict[str, object], job.input_json)
        outcomes = self._companion_outcomes(stored_payload)
        artifact_filenames = {
            artifact_type: {item.filename for item in artifacts if item.type == artifact_type}
            for artifact_type in {"cover", "metadata", "subtitle", "danmaku"}
        }
        requirements = (
            ("include_cover", "cover"),
            ("include_metadata", "metadata"),
            ("include_subtitle", "subtitle"),
            ("include_danmaku", "danmaku"),
        )
        for flag, artifact_type in requirements:
            if payload.get(flag) is not True:
                continue
            if outcomes.get(artifact_type) == "failed":
                return False
            if artifact_type == "subtitle":
                if outcomes.get("subtitle") == "not_available":
                    expected_subtitles = self._subtitle_expectation(stored_payload)
                    if expected_subtitles is None or not expected_subtitles:
                        continue
                    return False
                expected_subtitles = self._subtitle_expectation(stored_payload)
                if (
                    outcomes.get("subtitle") != "completed"
                    or expected_subtitles is None
                    or not set(expected_subtitles).issubset(artifact_filenames["subtitle"])
                ):
                    return False
                continue
            if artifact_type in artifact_types:
                continue
            if artifact_type == "cover" and outcomes.get(artifact_type) == "not_available":
                continue
            return False
        return True

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
                or "/" in filename
                or "\\" in filename
                or any(ord(character) < 32 for character in filename)
            ):
                return None
            validated.append(filename)
        if len(validated) != len(set(validated)):
            return None
        return tuple(validated)

    @staticmethod
    def _companion_outcomes(payload: Mapping[str, object]) -> dict[str, CompanionOutcome]:
        raw = payload.get("companion_outcomes")
        if not isinstance(raw, Mapping):
            return {}
        outcomes: dict[str, CompanionOutcome] = {}
        for artifact_type, outcome in raw.items():
            if (
                isinstance(artifact_type, str)
                and artifact_type in {"cover", "subtitle", "danmaku", "metadata"}
                and outcome in {"completed", "not_available", "failed"}
            ):
                outcomes[artifact_type] = cast(CompanionOutcome, outcome)
        return outcomes

    async def _load(self, job_id: str) -> Job | None:
        async with self.session_factory() as session:
            job = await session.scalar(
                select(Job).where(Job.id == job_id).options(selectinload(Job.artifacts))
            )
            if job is not None:
                session.expunge(job)
            return job

    async def _to_read(self, job: Job) -> JobRead:
        loaded = await self._load(job.id)
        if loaded is None:
            raise self._not_found()
        return self._model_to_read(loaded)

    def _model_to_read(self, job: Job) -> JobRead:
        runtime = self._runtime.get(job.id, _RuntimeState())
        payload = cast(dict[str, object], job.input_json)
        companion_outcomes = self._companion_outcomes(payload)
        artifacts = [
            self.artifact_service.to_read(
                artifact,
                job_input=payload,
                job_status=job.status,
            )
            for artifact in sorted(job.artifacts, key=lambda item: item.created_at)
        ]
        return JobRead(
            id=job.id,
            type=job.type,
            status=job.status,
            phase=job.phase,
            progress=job.progress,
            video_id=self._display_value(payload.get("video_id")),
            video_title=self._display_value(payload.get("video_title")),
            part_id=self._display_value(payload.get("part_id")),
            part_title=self._display_value(payload.get("part_title")),
            source_url=self._display_value(payload.get("official_source")),
            error_code=job.error_code,
            error_message=job.error_message,
            retry_count=job.retry_count,
            cancel_requested=job.cancel_requested,
            created_at=self._as_utc(job.created_at),
            started_at=self._as_utc(job.started_at) if job.started_at else None,
            finished_at=self._as_utc(job.finished_at) if job.finished_at else None,
            updated_at=self._as_utc(job.updated_at),
            runtime=JobRuntimeRead(
                downloaded_bytes=runtime.downloaded_bytes,
                total_bytes=runtime.total_bytes,
                speed_bytes_per_second=runtime.speed_bytes_per_second,
                eta_seconds=runtime.eta_seconds,
                automatic_attempt=runtime.automatic_attempt,
            ),
            companion_outcomes=companion_outcomes,
            has_warnings=any(
                outcome in {"failed", "not_available"} for outcome in companion_outcomes.values()
            ),
            artifacts=artifacts,
            artifact_ids=[artifact.id for artifact in artifacts],
        )

    async def _discard_partials(self, job_id: str, job_type: JobType) -> None:
        executor = self._executors.get(job_type)
        if isinstance(executor, PartialCleaner):
            try:
                await executor.discard_job_partials(job_id)
            except OSError:
                logger.warning(
                    "Canceled job partials could not be fully removed",
                    extra={"event": "job_partial_cleanup_failed", "job_id": job_id},
                )

    def _enqueue(self, job_id: str, job_type: JobType) -> None:
        if job_id in self._scheduled or job_id in self._active:
            return
        self._scheduled.add(job_id)
        self._queues[self._lane(job_type)].put_nowait(job_id)

    def _drain_queue(self) -> None:
        for queue in self._queues.values():
            while True:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                queue.task_done()

    @staticmethod
    def _lane(job_type: JobType) -> str:
        return "download" if job_type == JobType.DOWNLOAD else "analysis"

    async def _publish(self, job_id: str, kind: str) -> None:
        async with self._condition:
            self._versions[job_id] = self._versions.get(job_id, 0) + 1
            self._event_kinds[job_id] = kind
            self._condition.notify_all()

    @staticmethod
    def _event(
        job_id: str,
        version: int,
        kind: str,
        job: JobRead,
    ) -> JobEvent:
        event_kind: Literal["snapshot", "progress", "state"] = (
            "progress" if kind == "progress" else "state"
        )
        if kind == "snapshot":
            event_kind = "snapshot"
        return JobEvent(
            event_id=f"{job_id}:{version}",
            event=event_kind,
            emitted_at=datetime.now(UTC),
            job=job,
        )

    @staticmethod
    def _event_version(value: str | None, job_id: str) -> int:
        if not value:
            return 0
        prefix, separator, raw_version = value.rpartition(":")
        if not separator or prefix != job_id:
            return 0
        try:
            return max(0, int(raw_version))
        except ValueError:
            return 0

    @staticmethod
    def _status_for_phase(phase: str) -> JobStatus:
        if phase == "post_processing":
            return JobStatus.POST_PROCESSING
        if phase.startswith(("downloading_", "refreshing_")):
            return JobStatus.RUNNING
        if phase == "analysis_preparing":
            return JobStatus.PREPARING
        if phase.startswith("analysis_"):
            return JobStatus.RUNNING
        return JobStatus.PREPARING

    @staticmethod
    def _same_download(left: dict[str, object], right: dict[str, object]) -> bool:
        keys = (
            "video_id",
            "part_id",
            "video_stream_id",
            "audio_stream_id",
            "container",
            "processing_mode",
            "access_mode",
            "output_filename",
            "include_subtitle",
            "include_cover",
            "include_metadata",
            "include_danmaku",
        )
        return all(left.get(key) == right.get(key) for key in keys)

    @staticmethod
    def _same_analysis(left: dict[str, object], right: dict[str, object]) -> bool:
        keys = (
            "video_id",
            "part_ids",
            "features",
            "source_artifact_ids",
            "language",
            "access_mode",
            "asr_model",
            "device",
            "ocr_resolution",
            "sample_interval_seconds",
            "export_formats",
            "maximum_duration_seconds",
            "scene_threshold",
            "maximum_keyframes",
        )
        return all(left.get(key) == right.get(key) for key in keys)

    @classmethod
    def _validate_persisted_payload(cls, payload: Mapping[str, object]) -> None:
        try:
            serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Job payload must contain JSON-compatible values") from exc
        if len(serialized.encode("utf-8")) > 64 * 1024:
            raise ValueError("Job payload exceeds the persistence limit")

        def inspect(value: object, *, depth: int) -> None:
            if depth > 12:
                raise ValueError("Job payload exceeds the nesting limit")
            if isinstance(value, Mapping):
                for raw_key, child in value.items():
                    if not isinstance(raw_key, str):
                        raise ValueError("Job payload keys must be strings")
                    key = raw_key.lower().strip()
                    if key in _FORBIDDEN_PERSISTED_KEYS or key.endswith("_url"):
                        raise ValueError("Job payload cannot persist credentials or media URLs")
                    inspect(child, depth=depth + 1)
            elif isinstance(value, list | tuple):
                for child in value:
                    inspect(child, depth=depth + 1)

        inspect(payload, depth=0)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _display_value(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            ErrorCode.RESOURCE_NOT_FOUND,
            "任务记录不存在",
            action="刷新任务列表后重试",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    @staticmethod
    def _invalid_transition(message: str) -> AppError:
        return AppError(
            ErrorCode.VALIDATION_ERROR,
            message,
            action="刷新任务状态后选择可用操作",
            status_code=status.HTTP_409_CONFLICT,
        )
