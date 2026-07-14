from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update

from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.db.models import AuthPersistence, AuthProfile, AuthStatus
from app.services.auth import AuthService, AuthSession
from tests.conftest import UpstreamFixtureServer


async def test_auth_session_upload_validate_and_clear(
    api_client: tuple[Any, Any], valid_cookie_json: bytes
) -> None:
    client, app = api_client
    initial = await client.get("/api/v1/auth/status")
    assert initial.status_code == 200
    assert initial.json()["status"] == "logged_out"

    uploaded = await client.post(
        "/api/v1/auth/cookies",
        files={"file": ("cookies.json", valid_cookie_json, "application/json")},
        data={"persistence": "session"},
    )
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["status"] == "premium"
    assert body["loggedIn"] is True
    assert body["membershipType"] == "annual_premium"
    assert body["maskedAccountName"] == "测**员"
    assert body["persistence"] == "session"
    assert "test-session-value" not in uploaded.text
    assert "test-csrf-value" not in uploaded.text

    validated = await client.post("/api/v1/auth/validate")
    assert validated.status_code == 200
    assert validated.json()["premium"] is True

    async with app.state.container.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuthProfile)) == 0

    cleared = await client.delete("/api/v1/auth/cookies")
    assert cleared.status_code == 200
    assert cleared.json()["status"] == "logged_out"
    assert cleared.json()["hasCredentials"] is False


async def test_auth_local_persistence_is_encrypted_and_deleted(
    api_client: tuple[Any, Any], valid_cookie_json: bytes
) -> None:
    client, app = api_client
    uploaded = await client.post(
        "/api/v1/auth/cookies",
        files={"file": ("cookies.json", valid_cookie_json, "application/json")},
        data={"persistence": "local"},
    )
    assert uploaded.status_code == 200
    async with app.state.container.session_factory() as session:
        profile = await session.scalar(select(AuthProfile))
        assert profile is not None
        assert b"test-session-value" not in profile.encrypted_cookies
        assert profile.status.value == "premium"
    await client.delete("/api/v1/auth/cookies")
    async with app.state.container.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuthProfile)) == 0


async def test_auth_rejects_invalid_extension_and_expired_cookie(
    api_client: tuple[Any, Any],
) -> None:
    client, _ = api_client
    wrong_type = await client.post(
        "/api/v1/auth/cookies",
        files={"file": ("cookies.txt", b"[]", "text/plain")},
    )
    assert wrong_type.status_code == 400
    assert wrong_type.json()["error"]["code"] == "COOKIE_FORMAT_INVALID"

    expired_payload = (
        b'[{"name":"SESSDATA","value":"expired-test","domain":".bilibili.com",'
        b'"path":"/","secure":true,"expires":1}]'
    )
    expired = await client.post(
        "/api/v1/auth/cookies",
        files={"file": ("cookies.json", expired_payload, "application/json")},
    )
    assert expired.status_code == 400
    assert expired.json()["error"]["code"] == "COOKIE_EXPIRED"
    assert "expired-test" not in expired.text


@pytest.mark.parametrize("expiration", ["NaN", "Infinity", "-Infinity", "1e400"])
async def test_auth_rejects_unsafe_cookie_expiration_as_format_error(
    api_client: tuple[Any, Any],
    expiration: str,
) -> None:
    client, _ = api_client
    payload = (
        '[{"name":"SESSDATA","value":"unsafe-expiration-test",'
        f'"domain":".bilibili.com","expires":"{expiration}"}}]'
    ).encode()
    response = await client.post(
        "/api/v1/auth/cookies",
        files={"file": ("cookies.json", payload, "application/json")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "COOKIE_FORMAT_INVALID"
    assert "unsafe-expiration-test" not in response.text


async def test_validate_without_credentials_returns_actionable_error(
    api_client: tuple[Any, Any],
) -> None:
    client, _ = api_client
    response = await client.post("/api/v1/auth/validate")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert response.json()["error"]["requestId"] == response.headers["X-Request-ID"]


async def test_persisted_authentication_restores_after_service_restart(
    api_client: tuple[Any, Any], valid_cookie_json: bytes, settings: Settings
) -> None:
    client, app = api_client
    response = await client.post(
        "/api/v1/auth/cookies",
        files={"file": ("cookies.json", valid_cookie_json, "application/json")},
        data={"persistence": "local"},
    )
    assert response.status_code == 200
    container = app.state.container
    restored = AuthService(settings, container.session_factory, container.provider)
    await restored.initialize()
    assert restored.status().status.value == "premium"
    assert restored.status().persistence.value == "local"
    assert len(list(await restored.cookie_jar())) == 2

    no_key_settings = settings.model_copy(
        update={"cookie_encryption_key": None, "cookie_encryption_key_file": None}
    )
    unavailable = AuthService(
        no_key_settings,
        container.session_factory,
        container.provider,
    )
    await unavailable.initialize()
    assert unavailable.status().status.value == "error"
    assert unavailable.status().has_credentials is True


async def test_invalid_revalidation_expires_and_removes_credentials(
    api_client: tuple[Any, Any],
    upstream: UpstreamFixtureServer,
    valid_cookie_json: bytes,
) -> None:
    client, app = api_client
    await client.post(
        "/api/v1/auth/cookies",
        files={"file": ("cookies.json", valid_cookie_json, "application/json")},
    )
    upstream.force_invalid_auth = True
    response = await client.post("/api/v1/auth/validate")
    assert response.status_code == 200
    assert response.json()["status"] == "expired"
    assert response.json()["hasCredentials"] is False
    async with app.state.container.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuthProfile)) == 0


async def test_invalid_upload_and_transient_auth_network_error_are_distinct(
    api_client: tuple[Any, Any],
    upstream: UpstreamFixtureServer,
    valid_cookie_json: bytes,
) -> None:
    client, app = api_client
    invalid_cookie = valid_cookie_json.replace(b"test-session-value", b"not-a-valid-session")
    invalid = await client.post(
        "/api/v1/auth/cookies",
        files={"file": ("cookies.json", invalid_cookie, "application/json")},
    )
    assert invalid.status_code == 200
    assert invalid.json()["status"] == "expired"

    upstream.force_auth_network_error = True
    failed = await client.post(
        "/api/v1/auth/cookies",
        files={"file": ("cookies.json", valid_cookie_json, "application/json")},
    )
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "UPSTREAM_NETWORK_ERROR"
    assert app.state.container.auth_service.status().status == AuthStatus.ERROR
    assert app.state.container.auth_service.status().has_credentials is True

    upstream.force_auth_network_error = False
    recovered = await client.post("/api/v1/auth/validate")
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "premium"


async def test_corrupt_and_expired_persisted_auth_are_not_loaded(
    api_client: tuple[Any, Any], valid_cookie_json: bytes, settings: Settings
) -> None:
    client, app = api_client
    container = app.state.container
    response = await client.post(
        "/api/v1/auth/cookies",
        files={"file": ("cookies.json", valid_cookie_json, "application/json")},
        data={"persistence": "local"},
    )
    assert response.status_code == 200
    async with container.session_factory() as session:
        await session.execute(update(AuthProfile).values(encrypted_cookies=b"corrupt-ciphertext"))
        await session.commit()
    corrupt = AuthService(settings, container.session_factory, container.provider)
    await corrupt.initialize()
    assert corrupt.status().status == AuthStatus.ERROR
    assert corrupt.status().has_credentials is True

    await client.post(
        "/api/v1/auth/cookies",
        files={"file": ("cookies.json", valid_cookie_json, "application/json")},
        data={"persistence": "local"},
    )
    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    async with container.session_factory() as session:
        await session.execute(update(AuthProfile).values(cookie_expires_at=expired_at))
        await session.commit()
    expired = AuthService(settings, container.session_factory, container.provider)
    await expired.initialize()
    assert expired.status().status == AuthStatus.EXPIRED
    async with container.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuthProfile)) == 0


async def test_auth_expiration_missing_key_and_masking_helpers(
    api_client: tuple[Any, Any], valid_cookie_json: bytes, settings: Settings
) -> None:
    _, app = api_client
    service = app.state.container.auth_service
    await service.upload(valid_cookie_json, AuthPersistence.SESSION)
    service._state.cookie_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(AppError) as expired:
        await service.cookie_jar()
    assert expired.value.code == ErrorCode.AUTH_REQUIRED
    assert service.status().status == AuthStatus.EXPIRED

    no_key = settings.model_copy(
        update={"cookie_encryption_key": None, "cookie_encryption_key_file": None}
    )
    no_key_service = AuthService(
        no_key,
        app.state.container.session_factory,
        app.state.container.provider,
    )
    with pytest.raises(AppError) as missing_key:
        await no_key_service.upload(valid_cookie_json, AuthPersistence.LOCAL)
    assert missing_key.value.code == ErrorCode.ENCRYPTION_KEY_MISSING
    with pytest.raises(RuntimeError, match="empty authentication"):
        await service._persist(AuthSession())

    assert service._mask_account_name(None) is None
    assert service._mask_account_name("甲") == "*"
    assert service._mask_account_name("甲乙") == "甲*"
    assert service._as_utc(None) is None
    assert service._as_utc(datetime(2026, 1, 1)).tzinfo == UTC
