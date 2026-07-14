from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.diagnostics import router as diagnostics_router
from app.core.config import Settings
from app.core.exceptions import install_exception_handlers
from app.db.models import Job, JobStatus, JobType
from app.db.session import create_engine, create_schema, create_session_factory
from app.schemas.diagnostics import ComponentHealth
from app.schemas.settings import AppSettings
from app.services.diagnostics import (
    DiagnosticsService,
    _directory_size,
    _probe_executable,
    _probe_package,
)
from app.services.settings import SettingsService


async def _available_executable(name: str) -> tuple[bool, str | None]:
    return True, "7.1.1" if name in {"ffmpeg", "ffprobe"} else None


def _available_package(module_name: str, distribution_name: str) -> tuple[bool, str | None]:
    if module_name in {"faster_whisper", "paddleocr", "paddle"}:
        return True, "1.2.3"
    return False, None


@pytest.mark.asyncio
async def test_diagnostics_reports_capabilities_disk_and_queue_without_secrets(
    settings: Settings,
    tmp_path: Path,
) -> None:
    unsafe_settings = settings.model_copy(
        update={
            "app_name": f"Unsafe {tmp_path}",
            "version": f"1.0.0 {tmp_path}",
        }
    )
    engine = create_engine(unsafe_settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    settings_service = SettingsService(unsafe_settings, factory)
    application_settings = AppSettings()
    application_settings.analysis.ocr_enabled = True
    await settings_service.update(application_settings)
    artifact_directory, temporary_directory = await settings_service.storage_directories()
    (artifact_directory / "video.bin").write_bytes(b"a" * 71)
    (temporary_directory / "partial.bin").write_bytes(b"b" * 29)
    nested = artifact_directory / "nested"
    nested.mkdir()
    (nested / "cover.bin").write_bytes(b"c" * 11)
    outside = tmp_path / "outside-diagnostic.bin"
    outside.write_bytes(b"secret" * 50)
    link_created = False
    try:
        (artifact_directory / "outside-link").symlink_to(outside)
    except OSError:
        link_created = False
    else:
        link_created = True

    now = datetime.now(UTC)
    secret_cookie = "diagnostic-secret-session"
    signed_url = "https://media.example.invalid/video?deadline=1&token=secret"
    async with factory() as session:
        session.add_all(
            [
                Job(
                    type=JobType.DOWNLOAD,
                    status=JobStatus.QUEUED,
                    phase="queued",
                    progress=0,
                    input_json={"cookie": secret_cookie, "url": signed_url},
                ),
                Job(
                    type=JobType.ANALYSIS,
                    status=JobStatus.RUNNING,
                    phase="running",
                    progress=50,
                    input_json={},
                    started_at=now,
                ),
                Job(
                    type=JobType.DOWNLOAD,
                    status=JobStatus.FAILED,
                    phase="download",
                    progress=20,
                    input_json={},
                    error_code="NETWORK",
                    error_message="fixed failure",
                    finished_at=now - timedelta(hours=1),
                ),
                Job(
                    type=JobType.DOWNLOAD,
                    status=JobStatus.FAILED,
                    phase="download",
                    progress=20,
                    input_json={},
                    error_code="NETWORK",
                    error_message="old failure",
                    finished_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                    created_at=now - timedelta(days=2),
                ),
            ]
        )
        await session.commit()

    service = DiagnosticsService(
        unsafe_settings,
        engine,
        factory,
        settings_service,
        started_at=now - timedelta(minutes=3),
        executable_probe=_available_executable,
        package_probe=_available_package,
    )
    report = await service.collect(request_id="diagnostic-request-1")
    assert report.application_version == "unknown"
    assert report.environment == "test"
    assert report.status == ComponentHealth.HEALTHY
    assert {component.name for component in report.components} == {
        "Database",
        "Storage",
        "FFmpeg",
        "FFprobe",
        "ASR",
        "OCR",
        "Summary",
        "ResourceLimits",
    }
    assert report.application_name == "Bili Insight API"
    assert report.disk.artifact_bytes == 82
    if link_created:
        assert report.disk.artifact_bytes < outside.stat().st_size
    assert report.disk.temporary_bytes == 29
    assert report.queue.queued == 1
    assert report.queue.running == 1
    assert report.queue.failed_last_24_hours == 1
    assert report.request_id == "diagnostic-request-1"
    limits = next(item for item in report.components if item.name == "ResourceLimits")
    assert "线程 4" in (limits.message or "")
    assert "4096 MiB" in (limits.message or "")

    serialized = json.dumps(
        report.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
    )
    assert secret_cookie not in serialized
    assert signed_url not in serialized
    assert str(tmp_path) not in serialized
    assert "account" not in serialized.lower()
    assert "sessdata" not in serialized.lower()
    await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostics_api_uses_frontend_contract_and_attachment(
    settings: Settings,
) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    settings.ensure_directories()
    factory = create_session_factory(engine)
    settings_service = SettingsService(settings, factory)
    service = DiagnosticsService(
        settings,
        engine,
        factory,
        settings_service,
        executable_probe=_available_executable,
        package_probe=_available_package,
    )
    application = FastAPI()
    application.state.container = SimpleNamespace(diagnostics_service=service)
    application.include_router(diagnostics_router, prefix="/api/v1")
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/diagnostics")
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {
            "applicationName",
            "applicationVersion",
            "environment",
            "startedAt",
            "status",
            "components",
            "disk",
            "queue",
            "requestId",
        }
        assert set(payload["disk"]) == {
            "totalBytes",
            "usedBytes",
            "freeBytes",
            "artifactBytes",
            "temporaryBytes",
        }
        assert set(payload["queue"]) == {
            "queued",
            "running",
            "failedLast24Hours",
        }

        attachment = await client.get("/api/v1/diagnostics/report")
        assert attachment.status_code == 200
        assert attachment.headers["content-type"].startswith("application/json")
        assert "attachment" in attachment.headers["content-disposition"]
        assert attachment.headers["cache-control"] == "no-store"
        assert str(settings.data_dir.resolve()) not in attachment.text
    await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostics_degrades_for_optional_capabilities_and_sanitizes_request_id(
    settings: Settings,
) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    settings.ensure_directories()
    factory = create_session_factory(engine)
    settings_service = SettingsService(settings, factory)

    def missing_packages(_: str, __: str) -> tuple[bool, str | None]:
        return False, None

    service = DiagnosticsService(
        settings,
        engine,
        factory,
        settings_service,
        executable_probe=_available_executable,
        package_probe=missing_packages,
    )
    report = await service.collect(request_id=r"C:\private\diagnostic")
    assert report.status == ComponentHealth.DEGRADED
    assert report.request_id is None
    components = {component.name: component for component in report.components}
    assert components["ASR"].status == ComponentHealth.UNAVAILABLE
    assert components["OCR"].status == ComponentHealth.DEGRADED
    await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostics_reports_core_failure_without_exception_details(
    settings: Settings,
) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    settings_service = SettingsService(settings, factory)

    async def failed_executable(_: str) -> tuple[bool, str | None]:
        raise OSError(str(settings.data_dir.resolve()))

    def failed_package(_: str, __: str) -> tuple[bool, str | None]:
        raise RuntimeError("token=diagnostic-secret")

    service = DiagnosticsService(
        settings,
        engine,
        factory,
        settings_service,
        executable_probe=failed_executable,
        package_probe=failed_package,
    )
    await service.apply_runtime_settings(AppSettings())
    report = await service.collect()
    assert report.status == ComponentHealth.UNAVAILABLE
    components = {component.name: component for component in report.components}
    assert components["Database"].status == ComponentHealth.UNAVAILABLE
    assert components["Storage"].status == ComponentHealth.UNAVAILABLE
    assert components["FFmpeg"].status == ComponentHealth.UNAVAILABLE
    serialized = json.dumps(report.model_dump(mode="json", by_alias=True))
    assert str(settings.data_dir.resolve()) not in serialized
    assert "diagnostic-secret" not in serialized
    await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostics_reports_missing_paddle_runtime(settings: Settings) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    settings_service = SettingsService(settings, factory)
    value = AppSettings()
    value.analysis.ocr_enabled = True
    await settings_service.update(value)

    def package_probe(module_name: str, _: str) -> tuple[bool, str | None]:
        return module_name != "paddle", "2.0.0" if module_name != "paddle" else None

    service = DiagnosticsService(
        settings,
        engine,
        factory,
        settings_service,
        executable_probe=_available_executable,
        package_probe=package_probe,
    )
    report = await service.collect()
    components = {component.name: component for component in report.components}
    assert components["OCR"].status == ComponentHealth.UNAVAILABLE
    assert "PaddlePaddle" in (components["OCR"].message or "")
    await engine.dispose()


@pytest.mark.asyncio
async def test_disabled_diagnostics_rejects_details_and_report_without_running_probes(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    settings_service = SettingsService(settings, factory)
    disabled = AppSettings()
    disabled.privacy.diagnostics_enabled = False
    await settings_service.update(disabled)
    probe_calls = 0

    async def executable_probe(_: str) -> tuple[bool, str | None]:
        nonlocal probe_calls
        probe_calls += 1
        return True, "1.0"

    service = DiagnosticsService(
        settings,
        engine,
        factory,
        settings_service,
        executable_probe=executable_probe,
        package_probe=_available_package,
    )
    application = FastAPI()
    install_exception_handlers(application)
    application.state.container = SimpleNamespace(diagnostics_service=service)
    application.include_router(diagnostics_router, prefix="/api/v1")
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for path in ("/api/v1/diagnostics", "/api/v1/diagnostics/report"):
            response = await client.get(path)
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "DIAGNOSTICS_DISABLED"
            assert "设置" in response.json()["error"]["action"]
            assert response.headers.get("content-disposition") is None

        async def unavailable_settings() -> AppSettings:
            raise RuntimeError("settings database unavailable")

        monkeypatch.setattr(settings_service, "get", unavailable_settings)
        fail_closed = await client.get("/api/v1/diagnostics")
        assert fail_closed.status_code == 403
        assert fail_closed.json()["error"]["code"] == "DIAGNOSTICS_DISABLED"
    assert probe_calls == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_default_capability_probes_and_directory_size_are_bounded(tmp_path: Path) -> None:
    missing_executable = await _probe_executable("biliscope-command-that-does-not-exist")
    assert missing_executable == (False, None)
    assert _probe_package("json", "biliscope-no-distribution") == (True, None)
    assert _probe_package("biliscope_no_such_module", "missing") == (False, None)
    missing = tmp_path / "missing"
    assert _directory_size(missing) == 0
    directory = tmp_path / "directory"
    directory.mkdir()
    (directory / "item.bin").write_bytes(b"x" * 9)
    assert _directory_size(directory) == 9
