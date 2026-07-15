from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response

from app.api.dependencies import get_app_auth_service
from app.schemas.app_auth import (
    AppAuthStatusRead,
    AppLoginRequest,
    AppPasswordChangeRequest,
    AppSetupRequest,
)
from app.services.app_auth import AppAuthService, AppPrincipal, CreatedAppSession

router = APIRouter(prefix="/app-auth", tags=["app-auth"])


def _token(request: Request, service: AppAuthService) -> str | None:
    return request.cookies.get(service.cookie_name)


def _set_session_cookie(
    response: Response,
    request: Request,
    service: AppAuthService,
    created: CreatedAppSession,
) -> None:
    response.set_cookie(
        key=service.cookie_name,
        value=created.token,
        max_age=service.settings.app_session_ttl_seconds,
        httponly=True,
        secure=service.settings.app_session_cookie_secure or request.url.scheme == "https",
        samesite="strict",
        path="/",
    )


async def require_app_session(
    request: Request,
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    service: AppAuthService = Depends(get_app_auth_service),
) -> AppPrincipal:
    principal = await service.authenticate(
        _token(request, service),
        csrf_token=csrf_token,
        require_csrf=request.method not in {"GET", "HEAD", "OPTIONS"},
    )
    if principal is None:
        raise RuntimeError("Required application session was not resolved")
    return principal


@router.get("/status", response_model=AppAuthStatusRead)
async def app_auth_status(
    request: Request,
    service: AppAuthService = Depends(get_app_auth_service),
) -> AppAuthStatusRead:
    return await service.status(_token(request, service))


@router.post("/setup", response_model=AppAuthStatusRead)
async def setup_app_admin(
    payload: AppSetupRequest,
    request: Request,
    response: Response,
    service: AppAuthService = Depends(get_app_auth_service),
) -> AppAuthStatusRead:
    created = await service.setup(payload)
    _set_session_cookie(response, request, service, created)
    return created.status


@router.post("/login", response_model=AppAuthStatusRead)
async def login_app_admin(
    payload: AppLoginRequest,
    request: Request,
    response: Response,
    service: AppAuthService = Depends(get_app_auth_service),
) -> AppAuthStatusRead:
    client_key = request.client.host if request.client else "unknown"
    created = await service.login(payload, client_key=client_key)
    _set_session_cookie(response, request, service, created)
    return created.status


@router.post("/logout", response_model=AppAuthStatusRead)
async def logout_app_admin(
    request: Request,
    response: Response,
    _: AppPrincipal = Depends(require_app_session),
    service: AppAuthService = Depends(get_app_auth_service),
) -> AppAuthStatusRead:
    await service.logout(_token(request, service))
    response.delete_cookie(service.cookie_name, path="/", httponly=True, samesite="strict")
    return AppAuthStatusRead(initialized=True, authenticated=False)


@router.put("/password", response_model=AppAuthStatusRead)
async def change_app_password(
    payload: AppPasswordChangeRequest,
    request: Request,
    response: Response,
    principal: AppPrincipal = Depends(require_app_session),
    service: AppAuthService = Depends(get_app_auth_service),
) -> AppAuthStatusRead:
    created = await service.change_password(principal, payload)
    _set_session_cookie(response, request, service, created)
    return created.status
