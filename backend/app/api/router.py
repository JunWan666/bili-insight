from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api import (
    analyses,
    app_auth,
    artifacts,
    auth,
    diagnostics,
    downloads,
    health,
    jobs,
    previews,
    settings,
    videos,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(app_auth.router)

protected_router = APIRouter(dependencies=[Depends(app_auth.require_app_session)])
protected_router.include_router(auth.router)
protected_router.include_router(videos.router)
protected_router.include_router(previews.router)
protected_router.include_router(downloads.router)
protected_router.include_router(jobs.router)
protected_router.include_router(artifacts.router)
protected_router.include_router(analyses.router)
protected_router.include_router(settings.router)
protected_router.include_router(diagnostics.router)
api_router.include_router(protected_router)
