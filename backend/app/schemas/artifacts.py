from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.db.models import JobStatus
from app.schemas.base import CamelModel


class ArtifactRead(CamelModel):
    id: str
    job_id: str | None = None
    video_id: str | None
    video_title: str | None
    part_id: str | None
    part_title: str | None
    source_url: str | None = None
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


class ArtifactBatchDeleteRequest(CamelModel):
    artifact_ids: list[str] = Field(min_length=1, max_length=100)
    delete_file: bool = True

    @field_validator("artifact_ids")
    @classmethod
    def unique_artifacts(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("artifact IDs must be unique")
        return values


class ArtifactBatchDeleteResponse(CamelModel):
    results: list[ArtifactDeleteResponse]
    failed_ids: list[str]
    deleted_count: int = Field(ge=0)


class StorageStatus(CamelModel):
    artifact_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
