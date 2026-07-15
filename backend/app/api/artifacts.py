from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_artifact_service
from app.db.models import JobStatus
from app.schemas.artifacts import (
    ArtifactBatchDeleteRequest,
    ArtifactBatchDeleteResponse,
    ArtifactDeleteResponse,
    ArtifactList,
    ArtifactRead,
    StorageStatus,
)
from app.services.artifacts import ArtifactService, RangeNotSatisfiable

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("", response_model=ArtifactList)
async def list_artifacts(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    artifact_type: str | None = Query(default=None, alias="type", max_length=64),
    job_id: str | None = Query(default=None, alias="jobId", max_length=36),
    search: str | None = Query(default=None, max_length=180),
    job_status: JobStatus | None = Query(default=None, alias="jobStatus"),
    created_from: datetime | None = Query(default=None, alias="from"),
    created_to: datetime | None = Query(default=None, alias="to"),
    service: ArtifactService = Depends(get_artifact_service),
) -> ArtifactList:
    return await service.list(
        limit=limit,
        offset=offset,
        artifact_type=artifact_type,
        job_id=job_id,
        search=search,
        job_status=job_status,
        created_from=created_from,
        created_to=created_to,
    )


@router.get("/storage", response_model=StorageStatus)
async def storage_status(
    service: ArtifactService = Depends(get_artifact_service),
) -> StorageStatus:
    return await service.storage_status()


@router.post("/batch-delete", response_model=ArtifactBatchDeleteResponse)
async def delete_artifact_batch(
    payload: ArtifactBatchDeleteRequest,
    service: ArtifactService = Depends(get_artifact_service),
) -> ArtifactBatchDeleteResponse:
    return await service.delete_many(
        payload.artifact_ids,
        delete_file=payload.delete_file,
    )


@router.get("/{artifact_id}", response_model=ArtifactRead)
async def get_artifact(
    artifact_id: str,
    service: ArtifactService = Depends(get_artifact_service),
) -> ArtifactRead:
    return await service.get(artifact_id)


@router.get("/{artifact_id}/content")
async def get_artifact_content(
    artifact_id: str,
    range_header: str | None = Header(default=None, alias="Range"),
    service: ArtifactService = Depends(get_artifact_service),
) -> Response:
    try:
        delivery = await service.delivery(artifact_id, range_header)
    except RangeNotSatisfiable as exc:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{exc.size}", "Accept-Ranges": "bytes"},
        )
    fallback = quote(delivery.filename, safe="")
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(delivery.length),
        "Content-Disposition": (f"attachment; filename=\"download\"; filename*=UTF-8''{fallback}"),
        "X-Content-Type-Options": "nosniff",
    }
    if delivery.status_code == status.HTTP_206_PARTIAL_CONTENT:
        headers["Content-Range"] = f"bytes {delivery.start}-{delivery.end}/{delivery.size}"
    return StreamingResponse(
        delivery.stream(),
        status_code=delivery.status_code,
        media_type=delivery.mime_type,
        headers=headers,
    )


@router.delete("/{artifact_id}", response_model=ArtifactDeleteResponse)
async def delete_artifact(
    artifact_id: str,
    delete_file: bool = Query(default=True, alias="deleteFile"),
    service: ArtifactService = Depends(get_artifact_service),
) -> ArtifactDeleteResponse:
    return await service.delete(artifact_id, delete_file=delete_file)
