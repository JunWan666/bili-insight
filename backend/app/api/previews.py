from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import StreamingResponse

from app.db.models import StreamKind
from app.schemas.previews import CreatePreviewRequest, PreviewRead
from app.services.previews import (
    PreviewRangeNotSatisfiable,
    PreviewService,
)

router = APIRouter(prefix="/previews", tags=["previews"])


def get_preview_service(request: Request) -> PreviewService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "preview_service", None)
    if service is None:
        raise RuntimeError("Preview service is not configured")
    return cast(PreviewService, service)


@router.post("", response_model=PreviewRead, status_code=status.HTTP_201_CREATED)
async def create_preview(
    payload: CreatePreviewRequest,
    service: PreviewService = Depends(get_preview_service),
) -> PreviewRead:
    return await service.create(
        payload.video_stream_id,
        payload.audio_stream_id,
        payload.access_mode,
    )


@router.get("/{preview_id}/manifest.mpd")
async def preview_manifest(
    preview_id: str,
    service: PreviewService = Depends(get_preview_service),
) -> Response:
    return Response(
        content=await service.manifest(preview_id),
        media_type="application/dash+xml",
        headers={"Cache-Control": "private, no-store"},
    )


@router.api_route("/{preview_id}/media/{track_kind}", methods=["GET", "HEAD"])
async def preview_media(
    preview_id: str,
    track_kind: Literal["video", "audio"],
    request: Request,
    range_header: str | None = Header(default=None, alias="Range"),
    service: PreviewService = Depends(get_preview_service),
) -> Response:
    try:
        delivery = await service.media(
            preview_id,
            StreamKind(track_kind),
            range_header,
            head=request.method == "HEAD",
        )
    except PreviewRangeNotSatisfiable as exc:
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store"}
        if exc.total_size is not None:
            headers["Content-Range"] = f"bytes */{exc.total_size}"
        return Response(status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE, headers=headers)

    if request.method == "HEAD":
        await delivery.close()
        return Response(
            status_code=delivery.status_code,
            media_type=delivery.media_type,
            headers=delivery.headers,
        )
    return StreamingResponse(
        delivery.stream(),
        status_code=delivery.status_code,
        media_type=delivery.media_type,
        headers=delivery.headers,
    )


@router.delete("/{preview_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_preview(
    preview_id: str,
    service: PreviewService = Depends(get_preview_service),
) -> Response:
    await service.delete(preview_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
