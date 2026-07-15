from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.providers.models import VideoProvider
from app.services.analyses import (
    AnalysisService,
    DownloadAnalysisMediaAcquirer,
    ProviderSubtitleService,
)
from app.services.app_auth import AppAuthService
from app.services.artifacts import ArtifactService
from app.services.auth import AuthService
from app.services.diagnostics import DiagnosticsService
from app.services.downloads import DownloadExecutor
from app.services.jobs import JobService
from app.services.previews import PreviewService
from app.services.runtime_settings import RuntimeSettingsCoordinator
from app.services.settings import SettingsService
from app.services.videos import VideoService


@dataclass(slots=True)
class ApplicationContainer:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    provider: VideoProvider
    auth_service: AuthService
    app_auth_service: AppAuthService
    video_service: VideoService
    preview_service: PreviewService
    settings_service: SettingsService
    artifact_service: ArtifactService
    download_executor: DownloadExecutor
    job_service: JobService
    analysis_media_acquirer: DownloadAnalysisMediaAcquirer
    subtitle_service: ProviderSubtitleService
    analysis_service: AnalysisService
    runtime_settings: RuntimeSettingsCoordinator
    diagnostics_service: DiagnosticsService
