from __future__ import annotations

from typing import Any

import httpx


async def test_application_auth_protects_business_routes(
    api_client: tuple[httpx.AsyncClient, Any],
) -> None:
    client, _ = api_client
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)

    status = await client.get("/api/v1/app-auth/status")
    protected = await client.get("/api/v1/jobs")
    health = await client.get("/api/v1/health")

    assert status.status_code == 200
    assert status.json() == {
        "initialized": True,
        "authenticated": False,
        "username": None,
        "csrfToken": None,
        "sessionExpiresAt": None,
    }
    assert protected.status_code == 401
    assert protected.json()["error"]["code"] == "APP_AUTHENTICATION_REQUIRED"
    assert health.status_code == 200


async def test_login_csrf_logout_and_password_rotation(
    api_client: tuple[httpx.AsyncClient, Any],
) -> None:
    client, _ = api_client
    original_token = client.cookies.get("bili_insight_session")
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)

    invalid = await client.post(
        "/api/v1/app-auth/login",
        json={"username": "test-admin", "password": "incorrect-password"},
    )
    assert invalid.status_code == 401

    login = await client.post(
        "/api/v1/app-auth/login",
        json={"username": "test-admin", "password": "test-admin-password-2026"},
    )
    assert login.status_code == 200, login.text
    csrf = login.json()["csrfToken"]
    assert csrf
    assert client.cookies.get("bili_insight_session")

    no_csrf = await client.post("/api/v1/jobs/not-a-job/cancel")
    assert no_csrf.status_code == 403

    client.headers["X-CSRF-Token"] = csrf
    changed = await client.put(
        "/api/v1/app-auth/password",
        json={
            "currentPassword": "test-admin-password-2026",
            "newPassword": "replacement-password-2026",
            "confirmPassword": "replacement-password-2026",
        },
    )
    assert changed.status_code == 200, changed.text
    client.headers["X-CSRF-Token"] = changed.json()["csrfToken"]
    assert client.cookies.get("bili_insight_session") != original_token

    logout = await client.post("/api/v1/app-auth/logout")
    assert logout.status_code == 200
    assert logout.json()["authenticated"] is False
    assert (await client.get("/api/v1/jobs")).status_code == 401

    old_password = await client.post(
        "/api/v1/app-auth/login",
        json={"username": "test-admin", "password": "test-admin-password-2026"},
    )
    assert old_password.status_code == 401
    new_password = await client.post(
        "/api/v1/app-auth/login",
        json={"username": "test-admin", "password": "replacement-password-2026"},
    )
    assert new_password.status_code == 200


async def test_rejects_second_application_setup(
    api_client: tuple[httpx.AsyncClient, Any],
) -> None:
    client, _ = api_client
    response = await client.post(
        "/api/v1/app-auth/setup",
        json={
            "username": "another-admin",
            "password": "another-password-2026",
            "confirmPassword": "another-password-2026",
        },
    )
    assert response.status_code == 409
