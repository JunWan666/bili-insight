from __future__ import annotations

import asyncio
import copy
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.api.settings import router as settings_router
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, install_exception_handlers
from app.db.models import AppSetting
from app.db.session import create_engine, create_schema, create_session_factory
from app.schemas.settings import (
    GIBIBYTE,
    MAX_RATE_LIMIT_BYTES_PER_SECOND,
    MAX_STORAGE_QUOTA_BYTES,
    AppSettings,
    DownloadSettings,
)
from app.services.settings import SettingsService


@pytest.mark.asyncio
async def test_settings_crud_persists_one_camel_case_document(settings: Settings) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    service = SettingsService(settings, factory)
    application = FastAPI()
    application.state.container = SimpleNamespace(settings_service=service)
    install_exception_handlers(application)
    application.include_router(settings_router, prefix="/api/v1")

    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/settings")
        assert response.status_code == 200
        defaults = response.json()
        assert defaults["download"]["defaultPreset"] == "best_quality"
        assert defaults["download"]["concurrency"] == 2
        assert defaults["download"]["minimumResolutionHeight"] is None
        assert defaults["storage"]["artifactDirectory"] == "artifacts"
        assert defaults["storage"]["temporaryDirectory"] == "tmp"
        assert "artifact_directory" not in defaults["storage"]

        defaults["download"]["concurrency"] = 4
        defaults["download"]["retryLimit"] = 5
        defaults["download"]["minimumResolutionHeight"] = 720
        defaults["storage"]["artifactDirectory"] = "media/finished"
        defaults["storage"]["temporaryDirectory"] = "work/partial"
        defaults["storage"]["quotaBytes"] = 4 * GIBIBYTE
        defaults["analysis"]["asrModel"] = "large-v3"
        defaults["analysis"]["maximumDurationSeconds"] = 86_400
        defaults["network"]["rateLimitBytesPerSecond"] = 104_858
        updated = await client.put("/api/v1/settings", json=defaults)
        assert updated.status_code == 200
        assert updated.json() == defaults

    restored = SettingsService(settings, factory)
    persisted = await restored.get()
    assert persisted.download.concurrency == 4
    assert persisted.download.minimum_resolution_height == 720
    assert persisted.storage.artifact_directory == "media/finished"
    assert persisted.analysis.asr_model.value == "large-v3"
    artifact_directory, temporary_directory = await restored.storage_directories()
    assert artifact_directory.is_relative_to(restored.storage_root)
    assert temporary_directory.is_relative_to(restored.storage_root)
    assert artifact_directory.is_dir()
    assert temporary_directory.is_dir()
    async with factory() as session:
        count = await session.scalar(select(func.count()).select_from(AppSetting))
    assert count == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_settings_updates_preserve_singleton(settings: Settings) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    left_service = SettingsService(settings, factory)
    right_service = SettingsService(settings, factory)
    left = AppSettings.model_validate(
        {**AppSettings().model_dump(), "download": {"concurrency": 1}}
    )
    right = AppSettings.model_validate(
        {**AppSettings().model_dump(), "download": {"concurrency": 4}}
    )

    await asyncio.gather(left_service.update(left), right_service.update(right))
    current = await left_service.get()
    assert current.download.concurrency in {1, 4}
    async with factory() as session:
        count = await session.scalar(select(func.count()).select_from(AppSetting))
    assert count == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_settings_update_notifies_runtime_once(settings: Settings) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    service = SettingsService(settings, create_session_factory(engine))
    observed: list[int] = []

    async def apply_runtime(value: AppSettings) -> None:
        observed.append(value.download.concurrency)

    service.register_update_callback(apply_runtime)
    service.register_update_callback(apply_runtime)
    value = AppSettings.model_validate(
        {**AppSettings().model_dump(), "download": {"concurrency": 3}}
    )
    await service.update(value)

    assert observed == [3]
    await engine.dispose()


@pytest.mark.asyncio
async def test_settings_update_rolls_back_when_runtime_rejects_value(
    settings: Settings,
) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    service = SettingsService(settings, create_session_factory(engine))
    observed: list[int] = []

    async def apply_runtime(value: AppSettings) -> None:
        observed.append(value.download.concurrency)
        if value.download.concurrency == 3:
            raise RuntimeError("fixed runtime rejection")

    service.register_update_callback(apply_runtime)
    value = AppSettings.model_validate(
        {**AppSettings().model_dump(), "download": {"concurrency": 3}}
    )
    with pytest.raises(AppError, match="无法应用"):
        await service.update(value)

    assert (await service.get()).download.concurrency == 2
    assert observed == [3, 2]
    await engine.dispose()


@pytest.mark.asyncio
async def test_settings_retries_a_singleton_insert_race(settings: Settings) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    service = SettingsService(settings, factory)
    original = service._persist
    retry = AsyncMock(
        side_effect=[
            IntegrityError("insert", {}, Exception("fixed race")),
            None,
        ]
    )
    with patch.object(service, "_persist", retry):
        updated = await service.update(AppSettings())
    assert updated.download.concurrency == 2
    assert retry.await_count == 2
    await original(AppSettings().model_dump(mode="json"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_settings_maps_repeated_insert_conflict_to_safe_error(settings: Settings) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    service = SettingsService(settings, factory)
    conflict = IntegrityError("insert", {}, Exception("fixed race"))
    retry = AsyncMock(side_effect=[conflict, conflict])
    with patch.object(service, "_persist", retry):
        with pytest.raises(AppError) as captured:
            await service.update(AppSettings())
    assert captured.value.code.value == "DATABASE_ERROR"
    assert "fixed race" not in captured.value.message
    assert retry.await_count == 2
    await engine.dispose()


@pytest.mark.parametrize(
    "artifact_directory",
    [
        "/outside",
        r"C:\outside",
        r"\\server\share",
        "../outside",
        "safe/../../outside",
        ".",
        "safe/./outside",
        "safe//outside",
        "CON/files",
    ],
)
def test_settings_reject_absolute_and_ambiguous_paths(artifact_directory: str) -> None:
    payload = AppSettings().model_dump(mode="json")
    payload["storage"]["artifact_directory"] = artifact_directory
    with pytest.raises(ValidationError):
        AppSettings.model_validate(payload)


def test_settings_reject_overlapping_storage_directories() -> None:
    payload = AppSettings().model_dump(mode="json")
    payload["storage"]["artifact_directory"] = "files"
    payload["storage"]["temporary_directory"] = "files/tmp"
    with pytest.raises(ValidationError):
        AppSettings.model_validate(payload)


@pytest.mark.parametrize(
    "template",
    [
        "",
        "name\nvalue",
        "unsafe:name",
        "../outside",
        "{unknown}",
        "{title!r}",
        "{title:>10}",
        "{title",
    ],
)
def test_settings_reject_unsafe_filename_templates(template: str) -> None:
    with pytest.raises(ValidationError):
        DownloadSettings(filename_template=template)


@pytest.mark.parametrize(
    "directory",
    [
        "",
        "a" * 241,
        f"safe/{'b' * 129}",
        "safe/trailing.",
        "safe/trailing ",
        "safe/control\x01",
        "safe/star*",
        "LPT9/output",
    ],
)
def test_settings_reject_invalid_storage_segments(directory: str) -> None:
    payload = AppSettings().model_dump(mode="json")
    payload["storage"]["artifact_directory"] = directory
    with pytest.raises(ValidationError):
        AppSettings.model_validate(payload)


@pytest.mark.asyncio
async def test_settings_reject_symlink_escape(settings: Settings, tmp_path: Path) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    service = SettingsService(settings, factory)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = service.storage_root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        await engine.dispose()
        pytest.skip("The current platform does not permit creating directory symlinks")
    payload = AppSettings().model_dump(mode="json")
    payload["storage"]["artifact_directory"] = "escape/artifacts"
    value = AppSettings.model_validate(payload)

    with pytest.raises(AppError) as captured:
        await service.update(value)
    assert captured.value.code.value == "VALIDATION_ERROR"
    assert not (outside / "artifacts").exists()
    await engine.dispose()


@pytest.mark.asyncio
async def test_settings_reject_file_where_directory_is_required(settings: Settings) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    service = SettingsService(settings, factory)
    (service.storage_root / "blocked").write_text("not a directory", encoding="utf-8")
    payload = AppSettings().model_dump(mode="json")
    payload["storage"]["artifact_directory"] = "blocked/artifacts"

    with pytest.raises(AppError) as captured:
        await service.update(AppSettings.model_validate(payload))
    assert captured.value.status_code == 422
    await engine.dispose()


@pytest.mark.asyncio
async def test_settings_reject_corrupt_persisted_document(settings: Settings) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with factory() as session:
        session.add(AppSetting(id=1, payload={"download": {"concurrency": 99}}))
        await session.commit()
    service = SettingsService(settings, factory)

    with pytest.raises(AppError) as captured:
        await service.get()
    assert captured.value.code.value == "DATABASE_ERROR"
    assert captured.value.status_code == 500
    await engine.dispose()


@pytest.mark.asyncio
async def test_settings_maps_database_failure_to_safe_error(settings: Settings) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    service = SettingsService(settings, factory)

    with pytest.raises(AppError) as captured:
        await service.get()
    assert captured.value.code.value == "DATABASE_ERROR"
    assert str(settings.database_url) not in captured.value.message
    await engine.dispose()


@pytest.mark.asyncio
async def test_settings_derive_configured_directories_relative_to_storage_root(
    settings: Settings,
) -> None:
    configured = settings.model_copy(
        update={
            "artifact_dir": settings.data_dir / "media" / "finished",
            "temp_dir": settings.data_dir / "work" / "partial",
        }
    )
    engine = create_engine(configured)
    service = SettingsService(configured, create_session_factory(engine))
    defaults = service._defaults
    assert defaults.storage.artifact_directory == "media/finished"
    assert defaults.storage.temporary_directory == "work/partial"
    artifact, temporary = service.default_storage_directories()
    assert artifact.is_relative_to(service.storage_root)
    assert temporary.is_relative_to(service.storage_root)
    await engine.dispose()


@pytest.mark.asyncio
async def test_settings_supports_sibling_runtime_storage_directories(
    settings: Settings,
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    configured = settings.model_copy(
        update={
            "data_dir": runtime / "data",
            "artifact_dir": runtime / "artifacts",
            "temp_dir": runtime / "tmp",
            "log_dir": runtime / "logs",
        }
    )
    engine = create_engine(configured)
    service = SettingsService(configured, create_session_factory(engine))
    defaults = service.defaults()
    assert service.storage_root == runtime.resolve()
    assert defaults.storage.artifact_directory == "artifacts"
    assert defaults.storage.temporary_directory == "tmp"
    await engine.dispose()


@pytest.mark.parametrize(
    ("field_path", "invalid_value"),
    [
        (("download", "concurrency"), 0),
        (("download", "concurrency"), 5),
        (("download", "retry_limit"), 6),
        (("download", "minimum_resolution_height"), 240),
        (("download", "minimum_resolution_height"), 2160),
        (("storage", "quota_bytes"), GIBIBYTE - 1),
        (("storage", "quota_bytes"), MAX_STORAGE_QUOTA_BYTES + 1),
        (("storage", "cleanup_after_days"), 0),
        (("analysis", "asr_model"), "local/model"),
        (("analysis", "asr_model"), "large-v4"),
        (("analysis", "sample_interval_seconds"), 0.1),
        (("analysis", "sample_interval_seconds"), float("inf")),
        (("analysis", "maximum_duration_seconds"), 59),
        (("analysis", "maximum_duration_seconds"), 86_401),
        (("network", "timeout_seconds"), 4),
        (("network", "rate_limit_bytes_per_second"), 104_857),
        (
            ("network", "rate_limit_bytes_per_second"),
            MAX_RATE_LIMIT_BYTES_PER_SECOND + 1,
        ),
        (("network", "upstream_interval_milliseconds"), 60_001),
        (("privacy", "history_retention_days"), 0),
    ],
)
def test_settings_validate_numeric_and_model_boundaries(
    field_path: tuple[str, str], invalid_value: object
) -> None:
    payload = copy.deepcopy(AppSettings().model_dump(mode="json"))
    payload[field_path[0]][field_path[1]] = invalid_value
    with pytest.raises(ValidationError):
        AppSettings.model_validate(payload)


def test_settings_accept_documented_boundaries() -> None:
    payload = AppSettings().model_dump(mode="json")
    payload["download"].update(
        {"concurrency": 4, "retry_limit": 5, "minimum_resolution_height": 1080}
    )
    payload["storage"].update({"quota_bytes": MAX_STORAGE_QUOTA_BYTES, "cleanup_after_days": 3650})
    payload["analysis"].update(
        {
            "sample_interval_seconds": 60,
            "maximum_duration_seconds": 86_400,
            "asr_model": "large-v3",
        }
    )
    payload["network"].update(
        {
            "timeout_seconds": 300,
            "rate_limit_bytes_per_second": MAX_RATE_LIMIT_BYTES_PER_SECOND,
            "upstream_interval_milliseconds": 60_000,
        }
    )
    value = AppSettings.model_validate(payload)
    assert value.download.concurrency == 4
    assert value.download.minimum_resolution_height == 1080
    assert value.storage.quota_bytes == MAX_STORAGE_QUOTA_BYTES
    assert value.analysis.maximum_duration_seconds == 86_400


def test_sqlite_alembic_migration_creates_and_drops_settings_singleton(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "migration.db"
    monkeypatch.setenv("APP_DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_ARTIFACT_DIR", str(tmp_path / "data" / "artifacts"))
    monkeypatch.setenv("APP_TEMP_DIR", str(tmp_path / "data" / "tmp"))
    monkeypatch.setenv("APP_LOG_DIR", str(tmp_path / "data" / "logs"))
    get_settings.cache_clear()
    config_path = Path(__file__).parents[1] / "alembic.ini"
    config = Config(str(config_path))
    try:
        command.upgrade(config, "head")
        with closing(sqlite3.connect(database_path)) as connection, connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            connection.execute(
                "INSERT INTO app_settings (id, payload, created_at, updated_at) "
                "VALUES (1, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO app_settings (id, payload, created_at, updated_at) "
                    "VALUES (2, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
        assert "app_settings" in tables
        assert "retained_files" in tables
        assert revision == (ScriptDirectory.from_config(config).get_current_head(),)

        with closing(sqlite3.connect(database_path)) as connection, connection:
            connection.execute(
                "INSERT INTO retained_files "
                "(id, type, filename, storage_key, mime_type, size, checksum, protected, "
                "retention_reason, expires_at, created_at, retained_at) VALUES "
                "('retained-test', 'video', 'kept.mp4', '.retained/test/kept.mp4', "
                "'video/mp4', 1, 'sha256:test', 1, 'user_retained', NULL, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        with pytest.raises(RuntimeError, match="managed retained files exist"):
            command.downgrade(config, "0003_stream_access_requirement")
        with closing(sqlite3.connect(database_path)) as connection, connection:
            assert connection.execute("SELECT COUNT(*) FROM retained_files").fetchone() == (1,)
            connection.execute("DELETE FROM retained_files")
        command.downgrade(config, "0003_stream_access_requirement")
        with closing(sqlite3.connect(database_path)) as connection, connection:
            retained_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'retained_files'"
            ).fetchone()
        assert retained_table is None

        command.downgrade(config, "d906dc4b1e71")
        with closing(sqlite3.connect(database_path)) as connection, connection:
            downgraded_tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        assert "app_settings" not in downgraded_tables
    finally:
        get_settings.cache_clear()
