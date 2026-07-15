from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.db.models import JobStatus, JobType
from app.media.security import render_filename_template
from app.schemas.artifacts import ArtifactRead
from app.schemas.base import CamelModel
from app.schemas.video import AccessMode


class OutputContainer(StrEnum):
    MP4 = "mp4"
    MKV = "mkv"
    M4A = "m4a"
    MP3 = "mp3"
    FLAC = "flac"


class ProcessingMode(StrEnum):
    COPY = "copy"
    TRANSCODE = "transcode"


type CompanionOutcome = Literal["completed", "not_available", "failed"]


class DownloadRequest(CamelModel):
    video_id: str = Field(min_length=1, max_length=36)
    part_id: str = Field(min_length=1, max_length=36)
    video_stream_id: str | None = Field(default=None, max_length=36)
    audio_stream_id: str | Literal["auto", "none"] = Field(default="auto", max_length=36)
    container: OutputContainer = OutputContainer.MP4
    processing_mode: ProcessingMode = ProcessingMode.COPY
    access_mode: AccessMode = AccessMode.AUTO
    filename: str | None = Field(default=None, min_length=1, max_length=180)
    include_subtitle: bool = True
    include_cover: bool = True
    include_metadata: bool = True
    include_danmaku: bool = False
    cleanup_temporary: bool = True
    reuse_existing: bool = True

    @field_validator("filename")
    @classmethod
    def validate_filename_template(cls, value: str | None) -> str | None:
        if value is None:
            return None
        template = value.strip()
        render_filename_template(
            template,
            {"title": "title", "bvid": "BV", "page": 1, "part": "part", "quality": "HD"},
            extension="mp4",
        )
        return template

    @model_validator(mode="after")
    def validate_combination(self) -> DownloadRequest:
        audio_only = self.container in {
            OutputContainer.M4A,
            OutputContainer.MP3,
            OutputContainer.FLAC,
        }
        if audio_only and self.video_stream_id is not None:
            raise ValueError("M4A/MP3 输出不能选择视频流")
        if not audio_only and self.video_stream_id is None:
            raise ValueError("MP4/MKV 输出必须选择视频流")
        if audio_only and self.audio_stream_id == "none":
            raise ValueError("仅音频输出必须选择音频流")
        if self.container in {OutputContainer.MP3, OutputContainer.FLAC} and (
            self.processing_mode != ProcessingMode.TRANSCODE
        ):
            raise ValueError("MP3/FLAC 输出必须使用转码模式")
        return self


class JobRuntimeRead(CamelModel):
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed_bytes_per_second: float | None = None
    eta_seconds: float | None = None
    automatic_attempt: int = Field(default=0, ge=0)


class JobRead(CamelModel):
    id: str
    type: JobType
    status: JobStatus
    phase: str
    progress: float = Field(ge=0, le=100)
    video_id: str | None = None
    video_title: str | None = None
    part_id: str | None = None
    part_title: str | None = None
    source_url: str | None = None
    reused: bool = False
    input: dict[str, object] = Field(default_factory=dict, exclude=True)
    error_code: str | None
    error_message: str | None
    retry_count: int = Field(ge=0)
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    runtime: JobRuntimeRead
    companion_outcomes: dict[str, CompanionOutcome] = Field(default_factory=dict)
    has_warnings: bool = False
    artifacts: list[ArtifactRead]
    artifact_ids: list[str] = Field(default_factory=list)


class DownloadCreatedResponse(CamelModel):
    job: JobRead
    reused: bool


class DownloadBatchRequest(CamelModel):
    downloads: list[DownloadRequest] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def validate_parts(self) -> DownloadBatchRequest:
        video_ids = {item.video_id for item in self.downloads}
        if len(video_ids) != 1:
            raise ValueError("batch downloads must belong to one video")
        part_ids = [item.part_id for item in self.downloads]
        if len(part_ids) != len(set(part_ids)):
            raise ValueError("batch download parts must be unique")
        return self


class DownloadBatchCreatedResponse(CamelModel):
    items: list[DownloadCreatedResponse]
    created_count: int = Field(ge=0)
    reused_count: int = Field(ge=0)


class JobList(CamelModel):
    items: list[JobRead]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class JobDeleteResponse(CamelModel):
    id: str
    deleted: bool
    retained_artifact_count: int = Field(ge=0)


class JobBatchDeleteRequest(CamelModel):
    job_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("job_ids")
    @classmethod
    def unique_jobs(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("job IDs must be unique")
        return values


class JobBatchDeleteResponse(CamelModel):
    results: list[JobDeleteResponse]
    failed_ids: list[str]
    deleted_count: int = Field(ge=0)


class JobEvent(CamelModel):
    event_id: str
    event: Literal["snapshot", "progress", "state"]
    emitted_at: datetime
    job: JobRead
