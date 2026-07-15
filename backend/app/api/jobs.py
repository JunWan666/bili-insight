from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_job_service
from app.db.models import JobStatus, JobType
from app.schemas.jobs import (
    JobBatchDeleteRequest,
    JobBatchDeleteResponse,
    JobDeleteResponse,
    JobList,
    JobRead,
)
from app.services.jobs import JobService

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=JobList)
async def list_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    job_type: JobType | None = Query(default=None, alias="type"),
    job_status: JobStatus | None = Query(default=None, alias="status"),
    active_only: bool = Query(default=False, alias="activeOnly"),
    service: JobService = Depends(get_job_service),
) -> JobList:
    return await service.list(
        limit=limit,
        offset=offset,
        job_type=job_type,
        job_status=job_status,
        active_only=active_only,
    )


@router.post("/batch-delete", response_model=JobBatchDeleteResponse)
async def delete_jobs(
    payload: JobBatchDeleteRequest,
    service: JobService = Depends(get_job_service),
) -> JobBatchDeleteResponse:
    return await service.delete_many(payload.job_ids)


@router.get("/{job_id}", response_model=JobRead)
async def get_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> JobRead:
    return await service.get(job_id)


@router.delete("/{job_id}", response_model=JobDeleteResponse)
async def delete_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> JobDeleteResponse:
    return await service.delete(job_id)


@router.get("/{job_id}/events")
async def stream_job_events(
    job_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    service: JobService = Depends(get_job_service),
) -> StreamingResponse:
    async def generate() -> AsyncIterator[bytes]:
        async for event in service.events(job_id, last_event_id=last_event_id):
            if event is None:
                yield b": heartbeat\n\n"
                continue
            payload = json.dumps(
                event.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"id: {event.event_id}\nevent: {event.event}\ndata: {payload}\n\n".encode()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{job_id}/cancel", response_model=JobRead)
async def cancel_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> JobRead:
    return await service.cancel(job_id)


@router.post("/{job_id}/pause", response_model=JobRead)
async def pause_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> JobRead:
    return await service.pause(job_id)


@router.post("/{job_id}/resume", response_model=JobRead)
async def resume_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> JobRead:
    return await service.resume(job_id)


@router.post("/{job_id}/retry", response_model=JobRead)
async def retry_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> JobRead:
    return await service.retry(job_id)
