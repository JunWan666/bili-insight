from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    analyses,
    artifacts,
    auth,
    diagnostics,
    downloads,
    health,
    jobs,
    settings,
    videos,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(videos.router)
api_router.include_router(downloads.router)
api_router.include_router(jobs.router)
api_router.include_router(artifacts.router)
api_router.include_router(analyses.router)
api_router.include_router(settings.router)
api_router.include_router(diagnostics.router)
