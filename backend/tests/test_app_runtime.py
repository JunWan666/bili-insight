from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.db.models import Artifact, Job, JobStatus, JobType
from app.main import create_app
from app.media.download import DownloadCheckpoint
from app.services.downloads import DownloadExecutionReporter, DownloadRuntimeConfig
from tests.conftest import UpstreamFixtureServer


def _safe_runtime_error() -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR,
        "运行时设置与当前任务冲突",
        action="等待任务结束后重试",
        status_code=409,
    )


async def test_complete_application_routes_and_worker_lifecycle(
    settings: Settings,
    upstream: UpstreamFixtureServer,
) -> None:
    application = create_app(settings, transport=httpx.MockTransport(upstream.handle))
    container = application.state.container
    assert container.job_service.health().status == "stopped"

    async with application.router.lifespan_context(application):
        assert container.job_service.health().status == "healthy"
        assert container.job_service._maintenance_task is not None
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            requests = (
                client.get("/api/v1/jobs"),
                client.get("/api/v1/artifacts"),
                client.get("/api/v1/analyses/capabilities"),
                client.get("/api/v1/settings"),
                client.get("/api/v1/diagnostics"),
                client.post("/api/v1/downloads", json={}),
                client.post("/api/v1/analyses", json={}),
            )
            responses = await asyncio.gather(*requests)
            assert all(response.status_code != 404 for response in responses)
            assert responses[0].status_code == 200
            assert responses[1].status_code == 200
            assert responses[2].status_code == 200
            assert responses[3].status_code == 200
            assert responses[4].status_code == 200
            assert responses[5].status_code == 422
            assert responses[6].status_code == 422
            worker = next(
                item for item in responses[4].json()["components"] if item["name"] == "Worker"
            )
            assert worker["status"] == "healthy"
            assert "payload" not in responses[4].text.lower()

    assert container.job_service.health().status == "stopped"
    assert container.job_service._maintenance_task is None


async def test_runtime_settings_hot_apply_and_rollback(
    api_client: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, application = api_client
    container = application.state.container
    current = (await client.get("/api/v1/settings")).json()
    updated = dict(current)
    updated["download"] = {
        **current["download"],
        "concurrency": 3,
        "retryLimit": 4,
        "filenameTemplate": "{bvid} - P{page} - {quality}",
    }
    updated["network"] = {
        **current["network"],
        "timeoutSeconds": 45,
        "rateLimitBytesPerSecond": 104_858,
        "upstreamIntervalMilliseconds": 375,
    }
    updated["storage"] = {
        **current["storage"],
        "quotaBytes": 1_073_741_824,
        "cleanupAfterDays": 7,
    }
    updated["privacy"] = {
        **current["privacy"],
        "historyRetentionDays": 30,
    }
    response = await client.put("/api/v1/settings", json=updated)
    assert response.status_code == 200, response.text
    assert container.download_executor.runtime.retries == 4
    assert container.download_executor.runtime.artifact_quota_bytes == 1_073_741_824
    assert container.download_executor.runtime.rate_limit_bytes_per_second == 104_858
    assert container.download_executor.downloader.timeout.read == 45
    assert (
        container.download_executor.default_filename_template
        == updated["download"]["filenameTemplate"]
    )
    assert container.job_service.health().workers_by_lane == {"download": 3, "analysis": 1}
    assert container.job_service._maintenance_provider is not None
    assert container.job_service._maintenance_task is not None
    assert container.provider._request_interval_seconds == pytest.approx(0.375)  # type: ignore[attr-defined]
    assert await container.runtime_settings.maintenance_policy() == (7, 30)

    original_reconfigure = container.download_executor.reconfigure

    async def reject_new_runtime(
        *,
        runtime: DownloadRuntimeConfig,
        artifact_root: Path,
        temp_root: Path,
        default_filename_template: str,
        timeout_seconds: float,
    ) -> None:
        if runtime.retries == 5:
            raise RuntimeError("fixed runtime rejection")
        await original_reconfigure(
            runtime=runtime,
            artifact_root=artifact_root,
            temp_root=temp_root,
            default_filename_template=default_filename_template,
            timeout_seconds=timeout_seconds,
        )

    monkeypatch.setattr(container.download_executor, "reconfigure", reject_new_runtime)
    rejected = {
        **updated,
        "download": {**updated["download"], "concurrency": 4, "retryLimit": 5},
    }
    failed = await client.put("/api/v1/settings", json=rejected)
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "INTERNAL_ERROR"
    restored = (await client.get("/api/v1/settings")).json()
    assert restored == updated
    assert container.download_executor.runtime.retries == 4
    assert container.job_service.health().status == "healthy"
    assert container.job_service.health().workers_by_lane == {"download": 3, "analysis": 1}
    assert container.job_service._maintenance_provider is not None
    assert container.job_service._maintenance_task is not None


@pytest.mark.parametrize("failed_stage", ["stop", "reconfigure", "start"])
async def test_each_runtime_stage_failure_rolls_back_and_recovers_workers(
    api_client: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
    failed_stage: str,
) -> None:
    client, application = api_client
    container = application.state.container
    previous = (await client.get("/api/v1/settings")).json()
    candidate = {
        **previous,
        "download": {
            **previous["download"],
            "concurrency": 4,
            "retryLimit": 5,
        },
    }

    if failed_stage == "stop":
        original_stop = container.job_service.stop
        calls = 0

        async def fail_stop_once() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _safe_runtime_error()
            await original_stop()

        monkeypatch.setattr(container.job_service, "stop", fail_stop_once)
    elif failed_stage == "reconfigure":
        original_reconfigure = container.download_executor.reconfigure

        async def fail_candidate_reconfigure(
            *,
            runtime: DownloadRuntimeConfig,
            artifact_root: Path,
            temp_root: Path,
            default_filename_template: str,
            timeout_seconds: float,
        ) -> None:
            if runtime.retries == 5:
                raise _safe_runtime_error()
            await original_reconfigure(
                runtime=runtime,
                artifact_root=artifact_root,
                temp_root=temp_root,
                default_filename_template=default_filename_template,
                timeout_seconds=timeout_seconds,
            )

        monkeypatch.setattr(
            container.download_executor,
            "reconfigure",
            fail_candidate_reconfigure,
        )
    else:
        original_start = container.job_service.start
        calls = 0

        async def fail_start_once() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _safe_runtime_error()
            await original_start()

        monkeypatch.setattr(container.job_service, "start", fail_start_once)

    response = await client.put("/api/v1/settings", json=candidate)
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "VALIDATION_ERROR",
        "message": "运行时设置与当前任务冲突",
        "action": "等待任务结束后重试",
        "requestId": response.headers["X-Request-ID"],
    }
    assert (await client.get("/api/v1/settings")).json() == previous
    assert container.download_executor.runtime.retries == previous["download"]["retryLimit"]
    assert container.job_service.health().status == "healthy"
    assert container.job_service._maintenance_provider is not None
    assert container.job_service._maintenance_task is not None


async def test_rollback_failure_does_not_mask_original_safe_error_or_stop_workers(
    api_client: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, application = api_client
    container = application.state.container
    previous = (await client.get("/api/v1/settings")).json()
    original_reconfigure = container.download_executor.reconfigure

    async def reject_candidate_and_rollback(
        *,
        runtime: DownloadRuntimeConfig,
        artifact_root: Path,
        temp_root: Path,
        default_filename_template: str,
        timeout_seconds: float,
    ) -> None:
        if runtime.retries == 5:
            raise _safe_runtime_error()
        if runtime.retries == previous["download"]["retryLimit"]:
            raise RuntimeError("fixed rollback rejection")
        await original_reconfigure(
            runtime=runtime,
            artifact_root=artifact_root,
            temp_root=temp_root,
            default_filename_template=default_filename_template,
            timeout_seconds=timeout_seconds,
        )

    monkeypatch.setattr(
        container.download_executor,
        "reconfigure",
        reject_candidate_and_rollback,
    )
    candidate = {
        **previous,
        "download": {**previous["download"], "retryLimit": 5},
    }
    response = await client.put("/api/v1/settings", json=candidate)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["message"] == "运行时设置与当前任务冲突"
    assert (await client.get("/api/v1/settings")).json() == previous
    assert container.job_service.health().status == "healthy"
    assert container.job_service._maintenance_provider is not None
    assert container.job_service._maintenance_task is not None


async def test_startup_configuration_failure_leaves_no_workers_or_maintenance(
    settings: Settings,
    upstream: UpstreamFixtureServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = create_app(settings, transport=httpx.MockTransport(upstream.handle))
    container = application.state.container

    async def reject_startup_configuration(**_: object) -> None:
        raise RuntimeError("fixed startup configuration rejection")

    monkeypatch.setattr(
        container.download_executor,
        "reconfigure",
        reject_startup_configuration,
    )
    with pytest.raises(RuntimeError, match="startup configuration rejection"):
        async with application.router.lifespan_context(application):
            pytest.fail("lifespan must not yield after startup configuration failure")
    assert container.job_service.health().status == "stopped"
    assert container.job_service._workers == []
    assert container.job_service._maintenance_provider is None
    assert container.job_service._maintenance_task is None


async def test_storage_root_change_is_rejected_while_resumable_job_exists(
    api_client: tuple[Any, Any],
) -> None:
    client, application = api_client
    container = application.state.container
    original_root = container.artifact_service.root
    async with container.session_factory() as session:
        session.add(
            Job(
                type=JobType.DOWNLOAD,
                status=JobStatus.PAUSED,
                phase="paused",
                progress=30,
                input_json={"video_id": "paused-storage-proof"},
            )
        )
        await session.commit()
    payload = (await client.get("/api/v1/settings")).json()
    payload["storage"]["artifactDirectory"] = "changed/artifacts"
    payload["storage"]["temporaryDirectory"] = "changed/temporary"

    response = await client.put("/api/v1/settings", json=payload)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "任务" in response.json()["error"]["message"]
    assert container.artifact_service.root == original_root
    assert container.job_service.health().status == "healthy"
    assert container.job_service._maintenance_task is not None


async def test_persisted_runtime_storage_is_reapplied_after_restart(
    settings: Settings,
    upstream: UpstreamFixtureServer,
) -> None:
    first = create_app(settings, transport=httpx.MockTransport(upstream.handle))
    async with first.router.lifespan_context(first):
        transport = httpx.ASGITransport(app=first, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            payload = (await client.get("/api/v1/settings")).json()
            payload["download"]["concurrency"] = 4
            payload["storage"]["artifactDirectory"] = "persisted/artifacts"
            payload["storage"]["temporaryDirectory"] = "persisted/temporary"
            response = await client.put("/api/v1/settings", json=payload)
            assert response.status_code == 200, response.text
        container = first.state.container
        async with container.session_factory() as session:
            job = Job(
                type=JobType.DOWNLOAD,
                status=JobStatus.COMPLETED,
                phase="completed",
                progress=100,
                input_json={"video_id": "restart-proof"},
                finished_at=None,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
        retained = container.artifact_service.root / "restart-proof.txt"
        retained.write_text("restart-safe", encoding="utf-8")
        artifact = await container.artifact_service.create_from_file(
            job_id=job.id,
            artifact_type="report",
            path=retained,
            filename="restart-proof.txt",
            mime_type="text/plain",
            media_info=None,
        )
        interrupted = (
            container.artifact_service.root / ".retained" / artifact.id / artifact.filename
        )
        interrupted.parent.mkdir(parents=True, exist_ok=True)
        retained.replace(interrupted)
        assert not retained.exists()
    assert first.state.container.job_service.health().status == "stopped"

    second = create_app(settings, transport=httpx.MockTransport(upstream.handle))
    async with second.router.lifespan_context(second):
        container = second.state.container
        expected_artifacts = (
            container.settings_service.storage_root / "persisted" / "artifacts"
        ).resolve()
        expected_temporary = (
            container.settings_service.storage_root / "persisted" / "temporary"
        ).resolve()
        assert container.artifact_service.root == expected_artifacts
        assert container.download_executor.artifact_root == expected_artifacts
        assert container.analysis_service.artifact_root == expected_artifacts
        assert container.download_executor.temp_root == expected_temporary
        assert container.subtitle_service.temp_root == expected_temporary
        assert container.job_service.health().workers_by_lane == {"download": 4, "analysis": 1}
        transport = httpx.ASGITransport(app=second, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            persisted = (await client.get("/api/v1/settings")).json()
            ready = await client.get("/api/v1/health/ready")
        assert persisted["storage"]["artifactDirectory"] == "persisted/artifacts"
        assert ready.status_code == 200
        assert ready.json()["checks"]["storage"] == "ok"
        assert ready.json()["checks"]["worker"] == "ok"
        delivery = await container.artifact_service.delivery(artifact.id, None)
        assert not interrupted.exists()
        assert delivery.path.read_text(encoding="utf-8") == "restart-safe"


async def test_cors_preflight_allows_configured_methods_without_wildcards(
    settings: Settings,
    upstream: UpstreamFixtureServer,
) -> None:
    configured = settings.model_copy(update={"cors_origins": "http://frontend.test"})
    application = create_app(configured, transport=httpx.MockTransport(upstream.handle))
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            settings_preflight = await client.options(
                "/api/v1/settings",
                headers={
                    "Origin": "http://frontend.test",
                    "Access-Control-Request-Method": "PUT",
                },
            )
            transcript_preflight = await client.options(
                "/api/v1/analyses/00000000-0000-4000-8000-000000000000/transcript",
                headers={
                    "Origin": "http://frontend.test",
                    "Access-Control-Request-Method": "PATCH",
                },
            )
            untrusted_origin = await client.options(
                "/api/v1/settings",
                headers={
                    "Origin": "http://untrusted.test",
                    "Access-Control-Request-Method": "PUT",
                },
            )
            unsupported_method = await client.options(
                "/api/v1/settings",
                headers={
                    "Origin": "http://frontend.test",
                    "Access-Control-Request-Method": "TRACE",
                },
            )
    assert settings_preflight.status_code == 200
    assert transcript_preflight.status_code == 200
    allowed_methods = transcript_preflight.headers["Access-Control-Allow-Methods"]
    assert "PUT" in allowed_methods
    assert "PATCH" in allowed_methods
    assert "*" not in allowed_methods
    assert transcript_preflight.headers["Access-Control-Allow-Origin"] == ("http://frontend.test")
    assert untrusted_origin.status_code == 400
    assert "Access-Control-Allow-Origin" not in untrusted_origin.headers
    assert unsupported_method.status_code == 400
    assert "*" not in unsupported_method.headers.get("Access-Control-Allow-Methods", "")


async def test_readiness_tracks_worker_state_and_preserves_maintenance(
    api_client: tuple[Any, Any],
) -> None:
    client, application = api_client
    container = application.state.container
    ready = await client.get("/api/v1/health/ready")
    assert ready.status_code == 200

    await container.job_service.stop()
    stopped = await client.get("/api/v1/health/ready")
    assert stopped.status_code == 503
    assert stopped.json()["checks"]["worker"] == "stopped"
    assert container.job_service._maintenance_provider is not None

    await container.job_service.start()
    recovered = await client.get("/api/v1/health/ready")
    assert recovered.status_code == 200
    assert container.job_service._maintenance_task is not None


async def test_disabling_diagnostics_preserves_health_but_blocks_detailed_reports(
    api_client: tuple[Any, Any],
) -> None:
    client, _ = api_client
    current = (await client.get("/api/v1/settings")).json()
    disabled = {
        **current,
        "privacy": {**current["privacy"], "diagnosticsEnabled": False},
    }
    updated = await client.put("/api/v1/settings", json=disabled)
    assert updated.status_code == 200
    assert updated.json()["privacy"]["diagnosticsEnabled"] is False

    health, readiness, details, report = await asyncio.gather(
        client.get("/api/v1/health"),
        client.get("/api/v1/health/ready"),
        client.get("/api/v1/diagnostics"),
        client.get("/api/v1/diagnostics/report"),
    )
    assert health.status_code == 200
    assert readiness.status_code == 200
    for response in (details, report):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "DIAGNOSTICS_DISABLED"
        assert response.headers.get("content-disposition") is None


async def test_full_app_download_contract_uses_registered_executor(
    api_client: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, application = api_client
    container = application.state.container

    async def complete_without_media(
        job: Job,
        *,
        checkpoint: DownloadCheckpoint,
        reporter: DownloadExecutionReporter,
    ) -> Sequence[Artifact]:
        await checkpoint.checkpoint()
        await reporter.update(phase="post_processing", progress=95)
        assert job.input_json["video_id"]
        return []

    monkeypatch.setattr(container.download_executor, "execute", complete_without_media)
    parsed = await client.post(
        "/api/v1/videos/parse",
        json={"url": "BV1FYT5zkE1q", "accessMode": "anonymous"},
    )
    assert parsed.status_code == 200, parsed.text
    body = parsed.json()
    request = {
        "videoId": body["video"]["id"],
        "partId": body["selectedPartId"],
        "videoStreamId": body["streams"]["video"][0]["id"],
        "audioStreamId": body["streams"]["audio"][0]["id"],
        "container": "mp4",
        "processingMode": "copy",
        "accessMode": "anonymous",
        "includeSubtitle": False,
        "includeCover": False,
        "includeMetadata": False,
    }
    created = await client.post("/api/v1/downloads", json=request)
    assert created.status_code == 202, created.text
    payload = created.json()
    assert payload["reused"] is False
    assert payload["job"]["videoId"] == body["video"]["id"]
    assert payload["job"]["videoTitle"] == body["video"]["title"]
    assert "input" not in payload["job"]
    job_id = payload["job"]["id"]

    async def wait_for_completion() -> dict[str, object]:
        while True:
            detail = await client.get(f"/api/v1/jobs/{job_id}")
            value = detail.json()
            if value["status"] == "completed":
                return value
            await asyncio.sleep(0.01)

    completed = await asyncio.wait_for(wait_for_completion(), timeout=2)
    assert completed["progress"] == 100
    capabilities = await client.get("/api/v1/analyses/capabilities")
    assert capabilities.status_code == 200
    assert {item["feature"] for item in capabilities.json()["items"]} >= {
        "basic",
        "media",
        "summary",
    }
