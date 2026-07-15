from __future__ import annotations

import asyncio
import shutil
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.process_limits import run_bounded_child_process


async def test_health_and_readiness_are_available_without_credentials(
    api_client: tuple[Any, Any],
) -> None:
    client, _ = api_client
    health = await client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "version": "1.2.1"}
    assert health.headers["X-Content-Type-Options"] == "nosniff"
    ready = await client.get("/api/v1/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["database"] == "ok"
    assert ready.json()["checks"]["ffmpeg"] == "ok"
    assert ready.json()["checks"]["ffprobe"] == "ok"


async def test_readiness_reports_missing_media_dependency_and_storage_failures(
    api_client: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api_client

    async def unavailable(name: str) -> bool:
        return name != "ffmpeg"

    monkeypatch.setattr("app.api.health.probe_media_executable", unavailable)
    response = await client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["ffmpeg"] == "unavailable"

    async def available(_name: str) -> bool:
        return True

    monkeypatch.setattr("app.api.health.probe_media_executable", available)
    real_disk_usage = shutil.disk_usage
    monkeypatch.setattr(
        "app.api.health.shutil.disk_usage",
        lambda _directory: SimpleNamespace(free=1),
    )
    response = await client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["storage"] == "unavailable"

    monkeypatch.setattr("app.api.health.shutil.disk_usage", real_disk_usage)

    def denied_probe(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("fixed readiness write failure")

    monkeypatch.setattr("app.api.health.tempfile.NamedTemporaryFile", denied_probe)
    response = await client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["storage"] == "unavailable"


async def test_runtime_version_probe_uses_global_limits_without_preexec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_spawn = asyncio.create_subprocess_exec
    spawn_keywords: dict[str, object] = {}
    applied: list[tuple[int, int]] = []

    async def recording_spawn(*arguments: object, **kwargs: object) -> asyncio.subprocess.Process:
        spawn_keywords.update(kwargs)
        return await real_spawn(*arguments, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("app.core.process_limits.asyncio.create_subprocess_exec", recording_spawn)
    monkeypatch.setattr(
        "app.core.process_limits.apply_process_resource_limits",
        lambda process_id, memory: applied.append((process_id, memory)) or True,
    )
    result = await run_bounded_child_process(
        sys.executable,
        "-c",
        "import os; print(os.environ['OMP_NUM_THREADS'])",
        timeout_seconds=5,
    )

    assert result is not None
    assert result.return_code == 0
    assert result.stdout.strip() == b"4"
    assert result.output_exceeded is False
    assert "preexec_fn" not in spawn_keywords
    assert applied


async def test_runtime_probe_stops_timeout_and_bounds_captured_output() -> None:
    timed_out = await run_bounded_child_process(
        sys.executable,
        "-c",
        "import time; time.sleep(2)",
        timeout_seconds=0.05,
    )
    assert timed_out is None

    excessive = await run_bounded_child_process(
        sys.executable,
        "-c",
        "print('x' * 4096)",
        timeout_seconds=5,
        output_limit_bytes=1024,
    )
    assert excessive is not None
    assert len(excessive.stdout) == 1024
    assert excessive.output_exceeded is True
