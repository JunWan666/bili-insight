from __future__ import annotations

from typing import cast

from fastapi import Request

from app.container import ApplicationContainer
from app.services.analyses import AnalysisService
from app.services.app_auth import AppAuthService
from app.services.artifacts import ArtifactService
from app.services.auth import AuthService
from app.services.diagnostics import DiagnosticsService
from app.services.downloads import DownloadExecutor
from app.services.jobs import JobService
from app.services.settings import SettingsService
from app.services.videos import VideoService


def get_container(request: Request) -> ApplicationContainer:
    return cast(ApplicationContainer, request.app.state.container)


def get_auth_service(request: Request) -> AuthService:
    return get_container(request).auth_service


def get_app_auth_service(request: Request) -> AppAuthService:
    return get_container(request).app_auth_service


def get_video_service(request: Request) -> VideoService:
    return get_container(request).video_service


def get_job_service(request: Request) -> JobService:
    return get_container(request).job_service


def get_artifact_service(request: Request) -> ArtifactService:
    return get_container(request).artifact_service


def get_download_executor(request: Request) -> DownloadExecutor:
    return get_container(request).download_executor


def get_analysis_service(request: Request) -> AnalysisService:
    return get_container(request).analysis_service


def get_settings_service(request: Request) -> SettingsService:
    return get_container(request).settings_service


def get_diagnostics_service(request: Request) -> DiagnosticsService:
    return get_container(request).diagnostics_service
