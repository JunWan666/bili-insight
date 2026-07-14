from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.schemas.base import CamelModel


class ComponentHealth(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class HealthComponent(CamelModel):
    name: str = Field(min_length=1, max_length=64)
    status: ComponentHealth
    version: str | None = Field(default=None, max_length=64)
    message: str | None = Field(default=None, max_length=256)


class DiskStatus(CamelModel):
    total_bytes: int = Field(ge=0)
    used_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)
    artifact_bytes: int = Field(ge=0)
    temporary_bytes: int = Field(ge=0)


class QueueStatus(CamelModel):
    queued: int = Field(ge=0)
    running: int = Field(ge=0)
    failed_last_24_hours: int = Field(ge=0)


class Diagnostics(CamelModel):
    application_name: str = Field(min_length=1, max_length=128)
    application_version: str = Field(min_length=1, max_length=64)
    environment: str = Field(min_length=1, max_length=32)
    started_at: datetime
    status: ComponentHealth
    components: list[HealthComponent]
    disk: DiskStatus
    queue: QueueStatus
    request_id: str | None = Field(default=None, max_length=64)
