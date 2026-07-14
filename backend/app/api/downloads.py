from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_job_service
from app.schemas.jobs import (
    DownloadBatchCreatedResponse,
    DownloadBatchRequest,
    DownloadCreatedResponse,
    DownloadRequest,
)
from app.services.jobs import JobService

router = APIRouter(prefix="/downloads", tags=["downloads"])


@router.post("", response_model=DownloadCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_download(
    request: DownloadRequest,
    service: JobService = Depends(get_job_service),
) -> DownloadCreatedResponse:
    return await service.create_download(request)


@router.post(
    "/batch",
    response_model=DownloadBatchCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_download_batch(
    request: DownloadBatchRequest,
    service: JobService = Depends(get_job_service),
) -> DownloadBatchCreatedResponse:
    return await service.create_download_batch(request)
