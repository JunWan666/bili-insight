from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_video_service
from app.schemas.video import (
    AccessMode,
    ParseVideoRequest,
    ParseVideoResponse,
    RecentVideoRead,
    RefreshVideoRequest,
    StreamsRead,
    StreamVerificationRead,
    VerifyStreamRequest,
    VideoBatchDeleteRequest,
    VideoBatchDeleteResponse,
    VideoDeleteResponse,
    VideoPartRead,
    VideoRead,
)
from app.services.videos import VideoService

router = APIRouter(prefix="/videos", tags=["videos"])


@router.post("/parse", response_model=ParseVideoResponse)
async def parse_video(
    payload: ParseVideoRequest,
    service: VideoService = Depends(get_video_service),
) -> ParseVideoResponse:
    return await service.parse(
        payload.url,
        payload.access_mode,
        force_refresh=payload.force_refresh,
    )


@router.get("", response_model=list[RecentVideoRead])
async def list_recent_videos(
    limit: Annotated[int, Query(ge=1, le=50)] = 8,
    service: VideoService = Depends(get_video_service),
) -> list[RecentVideoRead]:
    return await service.list_recent(limit)


@router.post("/batch-delete", response_model=VideoBatchDeleteResponse)
async def delete_videos(
    payload: VideoBatchDeleteRequest,
    service: VideoService = Depends(get_video_service),
) -> VideoBatchDeleteResponse:
    return await service.delete_many(payload.video_ids)


@router.post("/streams/{stream_id}/verify", response_model=StreamVerificationRead)
async def verify_stream(
    stream_id: str,
    payload: VerifyStreamRequest,
    service: VideoService = Depends(get_video_service),
) -> StreamVerificationRead:
    return await service.verify_stream(stream_id, payload.access_mode)


@router.get("/{video_id}", response_model=VideoRead)
async def get_video(
    video_id: str,
    service: VideoService = Depends(get_video_service),
) -> VideoRead:
    return await service.get_video(video_id)


@router.delete("/{video_id}", response_model=VideoDeleteResponse)
async def delete_video(
    video_id: str,
    service: VideoService = Depends(get_video_service),
) -> VideoDeleteResponse:
    return await service.delete(video_id)


@router.get("/{video_id}/parts", response_model=list[VideoPartRead])
async def get_video_parts(
    video_id: str,
    service: VideoService = Depends(get_video_service),
) -> list[VideoPartRead]:
    return await service.get_parts(video_id)


@router.get("/{video_id}/parts/{part_id}/streams", response_model=StreamsRead)
async def get_part_streams(
    video_id: str,
    part_id: str,
    access_mode: Annotated[AccessMode, Query(alias="accessMode")] = AccessMode.AUTO,
    force_refresh: Annotated[bool, Query(alias="forceRefresh")] = False,
    service: VideoService = Depends(get_video_service),
) -> StreamsRead:
    return await service.get_part_streams(
        video_id,
        part_id,
        access_mode,
        force_refresh=force_refresh,
    )


@router.post("/{video_id}/refresh", response_model=ParseVideoResponse)
async def refresh_video(
    video_id: str,
    payload: RefreshVideoRequest,
    service: VideoService = Depends(get_video_service),
) -> ParseVideoResponse:
    return await service.refresh(video_id, payload.access_mode, payload.part_id)
