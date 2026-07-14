from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import request_id_context, sanitize

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    INVALID_LINK = "INVALID_LINK"
    UNSUPPORTED_CONTENT = "UNSUPPORTED_CONTENT"
    VIDEO_NOT_FOUND = "VIDEO_NOT_FOUND"
    REGION_RESTRICTED = "REGION_RESTRICTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RISK_CONTROL = "RISK_CONTROL"
    UPSTREAM_NETWORK = "UPSTREAM_NETWORK_ERROR"
    UPSTREAM_CHANGED = "UPSTREAM_RESPONSE_CHANGED"
    AUTH_FORMAT_INVALID = "COOKIE_FORMAT_INVALID"
    AUTH_EXPIRED = "COOKIE_EXPIRED"
    AUTH_REQUIRED = "AUTHENTICATION_REQUIRED"
    AUTH_VALIDATION = "AUTH_VALIDATION_ERROR"
    ENCRYPTION_KEY_MISSING = "ENCRYPTION_KEY_MISSING"
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    DIAGNOSTICS_DISABLED = "DIAGNOSTICS_DISABLED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        action: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        log_context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.action = action
        self.status_code = status_code
        self.log_context = log_context or {}


def error_payload(
    code: ErrorCode, message: str, action: str, request_id: str | None = None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code.value,
            "message": message,
            "action": action,
            "requestId": request_id or request_id_context.get(),
        }
    }


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.warning(
        "Application request rejected: %s",
        exc.code.value,
        extra={"event": "application_error", **sanitize(exc.log_context)},
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(
            exc.code,
            exc.message,
            exc.action,
            getattr(request.state, "request_id", None),
        ),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    safe_errors = []
    for error in exc.errors():
        safe_errors.append(
            {
                "location": [str(item) for item in error.get("loc", ()) if item != "body"],
                "message": str(error.get("msg", "输入格式无效")),
                "type": str(error.get("type", "validation_error")),
            }
        )
    payload = error_payload(
        ErrorCode.VALIDATION_ERROR,
        "请求参数格式不正确，请检查后重试",
        "检查标记字段并重新提交",
        getattr(request.state, "request_id", None),
    )
    payload["error"]["fields"] = safe_errors
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content=payload)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled application error",
        exc_info=exc,
        extra={"event": "unhandled_error"},
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_payload(
            ErrorCode.INTERNAL_ERROR,
            "服务暂时无法完成请求",
            "稍后重试；若持续失败，请查看脱敏诊断信息",
            getattr(request.state, "request_id", None),
        ),
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)
