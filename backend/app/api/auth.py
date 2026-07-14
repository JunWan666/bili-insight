from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.dependencies import get_auth_service
from app.core.exceptions import AppError, ErrorCode
from app.db.models import AuthPersistence
from app.schemas.auth import AuthStatusResponse
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/cookies", response_model=AuthStatusResponse)
async def upload_cookies(
    file: Annotated[UploadFile, File(description="Browser-exported Bilibili Cookie JSON")],
    persistence: Annotated[AuthPersistence, Form()] = AuthPersistence.SESSION,
    service: AuthService = Depends(get_auth_service),
) -> AuthStatusResponse:
    filename = (file.filename or "").lower()
    if not filename.endswith(".json"):
        await file.close()
        raise AppError(
            ErrorCode.AUTH_FORMAT_INVALID,
            "只接受 JSON 格式的 Cookie 文件",
            action="请选择扩展名为 .json 的浏览器 Cookie 导出文件",
        )
    accepted_types = {"application/json", "text/json", "application/octet-stream", ""}
    if (file.content_type or "").lower() not in accepted_types:
        await file.close()
        raise AppError(
            ErrorCode.AUTH_FORMAT_INVALID,
            "Cookie 文件的媒体类型不是 JSON",
            action="请选择浏览器导出的 Cookie JSON 文件",
        )
    chunks = bytearray()
    try:
        while True:
            chunk = await file.read(65_536)
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > service.settings.cookie_upload_max_bytes:
                raise AppError(
                    ErrorCode.UPLOAD_TOO_LARGE,
                    "Cookie 文件超过允许的大小",
                    action="请选择不超过 1 MB 的 JSON 文件",
                    status_code=413,
                )
        return await service.upload(bytes(chunks), persistence)
    finally:
        for index in range(len(chunks)):
            chunks[index] = 0
        await file.close()


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(service: AuthService = Depends(get_auth_service)) -> AuthStatusResponse:
    return service.status()


@router.post("/validate", response_model=AuthStatusResponse)
async def validate_auth(service: AuthService = Depends(get_auth_service)) -> AuthStatusResponse:
    return await service.validate()


@router.delete("/cookies", response_model=AuthStatusResponse)
async def clear_auth(service: AuthService = Depends(get_auth_service)) -> AuthStatusResponse:
    return await service.clear()
