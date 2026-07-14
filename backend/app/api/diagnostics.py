from __future__ import annotations

import json
from typing import Annotated, Protocol, cast

from fastapi import APIRouter, Depends, Request, Response

from app.api.dependencies import get_container
from app.container import ApplicationContainer
from app.schemas.diagnostics import Diagnostics
from app.services.diagnostics import DiagnosticsService

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


class _DiagnosticsContainer(Protocol):
    diagnostics_service: DiagnosticsService


def get_diagnostics_service(
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> DiagnosticsService:
    return cast(_DiagnosticsContainer, container).diagnostics_service


@router.get("", response_model=Diagnostics)
async def diagnostics(
    request: Request,
    service: Annotated[DiagnosticsService, Depends(get_diagnostics_service)],
) -> Diagnostics:
    return await service.collect(request_id=getattr(request.state, "request_id", None))


@router.get("/report", response_model=None)
async def diagnostics_report(
    request: Request,
    service: Annotated[DiagnosticsService, Depends(get_diagnostics_service)],
) -> Response:
    report = await service.collect(request_id=getattr(request.state, "request_id", None))
    content = json.dumps(
        report.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return Response(
        content=content.encode("utf-8"),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="bili-insight-diagnostics.json"',
            "Cache-Control": "no-store",
        },
    )
