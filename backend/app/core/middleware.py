from __future__ import annotations

import hmac
import logging
import time
import uuid

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import Settings
from app.core.exceptions import ErrorCode, error_payload
from app.core.logging import request_id_context

logger = logging.getLogger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied_request_id
            if supplied_request_id.isascii()
            and supplied_request_id.replace("-", "").isalnum()
            and len(supplied_request_id) <= 64
            else str(uuid.uuid4())
        )
        token = request_id_context.set(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()
        response_status = status.HTTP_500_INTERNAL_SERVER_ERROR
        try:
            if self._requires_api_key(request):
                api_key = request.headers.get("X-API-Key", "")
                expected = self.settings.api_key_value or ""
                if not api_key or not hmac.compare_digest(api_key, expected):
                    response_status = status.HTTP_401_UNAUTHORIZED
                    return JSONResponse(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        content=error_payload(
                            ErrorCode.PERMISSION_DENIED,
                            "应用访问凭据无效",
                            "配置正确的访问凭据后重试",
                        ),
                    )
            response = await call_next(request)
            response_status = response.status_code
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            return response
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "Request completed",
                extra={
                    "event": "request_completed",
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                    "status_code": response_status,
                },
            )
            request_id_context.reset(token)

    def _requires_api_key(self, request: Request) -> bool:
        if self.settings.network_mode != "public":
            return False
        return request.url.path not in {
            "/api/v1/health",
            "/api/v1/health/ready",
            "/docs",
            "/openapi.json",
        }
