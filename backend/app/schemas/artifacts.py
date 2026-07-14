from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.db.models import JobStatus
from app.schemas.base import CamelModel


class ArtifactRead(CamelModel):
    id: str
    job_id: str | None = None
    video_id: str | None
    video_title: str | None
    part_id: str | None
    part_title: str | None
    job_status: JobStatus | None = None
    type: str
    filename: str
    mime_type: str
    size: int
    checksum: str
    media_info: dict[str, object] | None
    expires_at: datetime | None
    created_at: datetime
    content_url: str
    retained: bool = False
    protected: bool = False
    retention_reason: str | None = None
    retained_at: datetime | None = None


class ArtifactList(CamelModel):
    items: list[ArtifactRead]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ArtifactDeleteResponse(CamelModel):
    id: str
    record_deleted: bool
    file_deleted: bool
    retained: bool = False


class StorageStatus(CamelModel):
    artifact_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
