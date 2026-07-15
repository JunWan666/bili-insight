from __future__ import annotations

import ipaddress
import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated application configuration loaded from ``APP_*`` variables."""

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Bili Insight API"
    version: str = "1.2.2"
    environment: Literal["development", "test", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    network_mode: Literal["local", "trusted_proxy", "public"] = "local"
    api_key: SecretStr | None = None

    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    auto_create_schema: bool = True
    data_dir: Path = Path("data")
    artifact_dir: Path = Path("data/artifacts")
    temp_dir: Path = Path("data/tmp")
    log_dir: Path = Path("data/logs")

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = True
    cors_origins: str = ""

    cookie_encryption_key: SecretStr | None = None
    cookie_encryption_key_file: Path | None = None
    cookie_upload_max_bytes: int = Field(default=1_048_576, ge=1_024, le=10_485_760)
    cookie_max_items: int = Field(default=300, ge=1, le=2_000)
    app_session_ttl_seconds: int = Field(default=604_800, ge=1_800, le=2_592_000)
    app_session_cookie_secure: bool = False

    metadata_cache_ttl_seconds: int = Field(default=1_200, ge=60, le=86_400)
    stream_cache_ttl_seconds: int = Field(default=300, ge=30, le=3_600)
    upstream_timeout_seconds: float = Field(default=15.0, ge=1.0, le=120.0)
    upstream_connect_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0)
    upstream_retries: int = Field(default=2, ge=0, le=5)
    upstream_max_response_bytes: int = Field(default=5_242_880, ge=65_536, le=52_428_800)
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
    allowed_media_host_suffixes: str = (
        "bilivideo.com,bilivideo.cn,bilibili.com,biliapi.net,edge.mountaintoys.cn"
    )

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        host = value.strip()
        if not host:
            raise ValueError("host cannot be empty")
        return host

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            return ""
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError("CORS origins JSON is invalid") from exc
            if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
                raise ValueError("CORS origins JSON must be an array of strings")
            origins = [item.strip() for item in decoded if item.strip()]
        else:
            origins = [item.strip() for item in stripped.split(",") if item.strip()]
        if any(origin == "*" for origin in origins):
            raise ValueError("wildcard CORS origins are not allowed")
        if any(not origin.startswith(("http://", "https://")) for origin in origins):
            raise ValueError("CORS origins must use http or https")
        return ",".join(origins)

    @model_validator(mode="after")
    def validate_security_configuration(self) -> Settings:
        is_loopback = False
        try:
            is_loopback = ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            is_loopback = self.host.lower() == "localhost"

        if self.network_mode == "local" and not is_loopback:
            raise ValueError(
                "local network mode requires a loopback APP_HOST; use trusted_proxy only "
                "behind a local-only gateway"
            )
        if self.network_mode == "public" and not self.api_key_value:
            raise ValueError("public network mode requires APP_API_KEY")
        if self.cookie_encryption_key is not None and self.cookie_encryption_key_file is not None:
            raise ValueError(
                "configure either APP_COOKIE_ENCRYPTION_KEY or "
                "APP_COOKIE_ENCRYPTION_KEY_FILE, not both"
            )
        if self.cookie_encryption_key is not None:
            self._validate_fernet_key(self.cookie_encryption_key.get_secret_value().encode("ascii"))
        return self

    @staticmethod
    def _validate_fernet_key(key: bytes) -> None:
        try:
            Fernet(key)
        except (ValueError, TypeError) as exc:
            raise ValueError("Cookie encryption key must be a valid Fernet key") from exc

    @property
    def api_key_value(self) -> str | None:
        if self.api_key is None:
            return None
        value = self.api_key.get_secret_value()
        return value if value else None

    @property
    def cors_origin_list(self) -> list[str]:
        return [item for item in self.cors_origins.split(",") if item]

    @property
    def media_host_suffixes(self) -> tuple[str, ...]:
        return tuple(
            item.lower().lstrip(".")
            for item in self.allowed_media_host_suffixes.split(",")
            if item.strip()
        )

    def load_cookie_encryption_key(self) -> bytes | None:
        if self.cookie_encryption_key is not None:
            return self.cookie_encryption_key.get_secret_value().encode("ascii")
        if self.cookie_encryption_key_file is None:
            return None
        key_file = self.cookie_encryption_key_file.expanduser().resolve()
        key = key_file.read_bytes().strip()
        self._validate_fernet_key(key)
        return key

    def ensure_directories(self) -> None:
        for directory in (self.data_dir, self.artifact_dir, self.temp_dir, self.log_dir):
            directory.expanduser().resolve().mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
