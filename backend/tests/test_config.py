from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Settings


def test_local_network_mode_rejects_non_loopback_host() -> None:
    with pytest.raises(ValidationError, match="loopback"):
        Settings(host="0.0.0.0", network_mode="local")


def test_public_mode_requires_api_key() -> None:
    with pytest.raises(ValidationError, match="APP_API_KEY"):
        Settings(host="0.0.0.0", network_mode="public")

    configured = Settings(host="0.0.0.0", network_mode="public", api_key="strong-test-key")
    assert configured.api_key_value == "strong-test-key"


def test_cookie_key_must_be_fernet_key() -> None:
    with pytest.raises(ValidationError, match="Fernet"):
        Settings(cookie_encryption_key="invalid")
    valid = Fernet.generate_key().decode("ascii")
    assert Settings(cookie_encryption_key=valid).load_cookie_encryption_key() == valid.encode()


def test_cors_accepts_empty_comma_and_json_forms() -> None:
    assert Settings(cors_origins="[]").cors_origin_list == []
    configured = Settings(cors_origins='["http://localhost:5173"]')
    assert configured.cors_origin_list == ["http://localhost:5173"]
    configured = Settings(cors_origins="http://localhost:5173,http://127.0.0.1:5173")
    assert configured.cors_origin_list == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
