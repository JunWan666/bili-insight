from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.container import ApplicationContainer
from app.core.config import Settings, get_settings
from app.core.exceptions import install_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.models import JobType
from app.db.session import create_engine, create_schema, create_session_factory
from app.media.security import DNSResolver
from app.providers.bilibili import BilibiliProvider
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


def create_app(
    settings: Settings | None = None,
    *,
    provider: VideoProvider | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    media_resolver: DNSResolver | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    resolved_settings.ensure_directories()
    engine = create_engine(resolved_settings)
    session_factory = create_session_factory(engine)
    resolved_provider = provider or BilibiliProvider(
        resolved_settings,
        transport=transport,
        media_resolver=media_resolver,
    )
    auth_service = AuthService(resolved_settings, session_factory, resolved_provider)
    app_auth_service = AppAuthService(resolved_settings, session_factory)
    video_service = VideoService(
        resolved_settings, session_factory, resolved_provider, auth_service
    )
    preview_service = PreviewService(
        resolved_settings,
        session_factory,
        video_service,
        transport=transport,
        media_resolver=media_resolver,
    )
    settings_service = SettingsService(resolved_settings, session_factory)
    artifact_service = ArtifactService(resolved_settings, session_factory)
    download_executor = DownloadExecutor(
        resolved_settings,
        session_factory,
        video_service,
        artifact_service,
    )
    default_application_settings = settings_service.defaults()
    job_service = JobService(
        session_factory,
        artifact_service,
        download_executor,
        concurrency=default_application_settings.download.concurrency,
        analysis_concurrency=1,
    )
    analysis_media_acquirer = DownloadAnalysisMediaAcquirer(
        session_factory,
        video_service,
        download_executor,
        artifact_service,
    )
    subtitle_service = ProviderSubtitleService(
        resolved_settings,
        session_factory,
        resolved_provider,
        auth_service,
        transport=transport,
    )
    analysis_service = AnalysisService(
        resolved_settings,
        session_factory,
        artifact_service,
        job_service,
        settings_service=settings_service,
        media_acquirer=analysis_media_acquirer,
        subtitle_fetcher=subtitle_service,
    )
    job_service.register_executor(JobType.ANALYSIS, analysis_service)
    runtime_settings = RuntimeSettingsCoordinator(
        settings_service,
        artifact_service,
        download_executor,
        analysis_service,
        subtitle_service,
        resolved_provider,
        job_service,
    )
    settings_service.register_update_callback(runtime_settings.apply_runtime)
    diagnostics_service = DiagnosticsService(
        resolved_settings,
        engine,
        session_factory,
        settings_service,
        job_service=job_service,
    )
    settings_service.register_update_callback(diagnostics_service.apply_runtime_settings)
    auth_service.register_clear_callback(video_service.clear_authenticated_cache)
    auth_service.register_clear_callback(preview_service.clear_authenticated_sessions)
    container = ApplicationContainer(
        settings=resolved_settings,
        engine=engine,
        session_factory=session_factory,
        provider=resolved_provider,
        auth_service=auth_service,
        app_auth_service=app_auth_service,
        video_service=video_service,
        preview_service=preview_service,
        settings_service=settings_service,
        artifact_service=artifact_service,
        download_executor=download_executor,
        job_service=job_service,
        analysis_media_acquirer=analysis_media_acquirer,
        subtitle_service=subtitle_service,
        analysis_service=analysis_service,
        runtime_settings=runtime_settings,
        diagnostics_service=diagnostics_service,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            if resolved_settings.auto_create_schema:
                await create_schema(engine)
            await app_auth_service.initialize()
            await auth_service.initialize()
            persisted_settings = await settings_service.get()
            await diagnostics_service.apply_runtime_settings(persisted_settings)
            await runtime_settings.apply_startup(persisted_settings)
            # The persisted storage directory may differ from the bootstrap
            # directory in the environment.  Reconcile only after that
            # directory has become the active artifact root so interrupted
            # retention moves cannot be mistaken for untracked files.
            await artifact_service.reconcile_retained_files()
            await preview_service.start()
            await job_service.start()
            await job_service.start_maintenance(runtime_settings.maintenance_policy)
            application.state.container = container
            yield
        finally:
            try:
                await job_service.stop()
            finally:
                try:
                    await preview_service.stop()
                finally:
                    await engine.dispose()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.version,
        description=(
            "Local-first Bilibili video parsing API. Only process content you are allowed to use."
        ),
        lifespan=lifespan,
    )
    application.state.container = container
    install_exception_handlers(application)
    application.add_middleware(RequestContextMiddleware, settings=resolved_settings)
    if resolved_settings.cors_origin_list:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=resolved_settings.cors_origin_list,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-Request-ID", "X-API-Key", "X-CSRF-Token"],
            expose_headers=["X-Request-ID", "Content-Range", "Accept-Ranges"],
        )
    application.include_router(api_router)
    return application


app = create_app()


if __name__ == "__main__":
    runtime_settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=runtime_settings.host,
        port=runtime_settings.port,
        reload=False,
        log_config=None,
    )
