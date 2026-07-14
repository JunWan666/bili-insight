from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.dependencies import get_analysis_service
from app.schemas.analyses import (
    AnalysisCapabilities,
    AnalysisFeature,
    AnalysisList,
    AnalysisRead,
    AnalysisRequest,
    AnalysisResultStatus,
    TranscriptEditRequest,
)
from app.schemas.jobs import JobRead
from app.services.analyses import AnalysisService

router = APIRouter(prefix="/analyses", tags=["analyses"])

_UUID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


@router.post("", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
async def create_analysis(
    request: AnalysisRequest,
    service: AnalysisService = Depends(get_analysis_service),
) -> JobRead:
    return await service.create(request)


@router.get("/capabilities", response_model=AnalysisCapabilities)
async def get_analysis_capabilities(
    service: AnalysisService = Depends(get_analysis_service),
) -> AnalysisCapabilities:
    return await service.capabilities()


@router.get("", response_model=AnalysisList)
async def list_analyses(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    video_id: str | None = Query(default=None, alias="videoId", pattern=_UUID_PATTERN),
    part_id: str | None = Query(default=None, alias="partId", pattern=_UUID_PATTERN),
    feature: AnalysisFeature | None = Query(default=None),
    result_status: AnalysisResultStatus | None = Query(default=None, alias="status"),
    service: AnalysisService = Depends(get_analysis_service),
) -> AnalysisList:
    return await service.list(
        limit=limit,
        offset=offset,
        video_id=video_id,
        part_id=part_id,
        feature=feature,
        result_status=result_status,
    )


@router.get("/{analysis_id}", response_model=AnalysisRead)
async def get_analysis(
    analysis_id: str = Path(pattern=_UUID_PATTERN),
    service: AnalysisService = Depends(get_analysis_service),
) -> AnalysisRead:
    return await service.get(analysis_id)


@router.patch(
    "/{analysis_id}/transcript",
    response_model=AnalysisRead,
    status_code=status.HTTP_201_CREATED,
)
async def edit_analysis_transcript(
    request: TranscriptEditRequest,
    analysis_id: str = Path(pattern=_UUID_PATTERN),
    service: AnalysisService = Depends(get_analysis_service),
) -> AnalysisRead:
    return await service.edit_transcript(analysis_id, request)
