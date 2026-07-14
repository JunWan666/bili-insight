from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator

from app.db.models import StreamAccessRequirement, StreamKind
from app.schemas.base import CamelModel


class AccessMode(StrEnum):
    AUTO = "auto"
    ANONYMOUS = "anonymous"
    AUTHENTICATED = "authenticated"


class ParseVideoRequest(CamelModel):
    url: str = Field(min_length=2, max_length=2_048)
    access_mode: AccessMode = AccessMode.AUTO
    force_refresh: bool = False

    @field_validator("url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        return value.strip()


class RefreshVideoRequest(CamelModel):
    access_mode: AccessMode = AccessMode.AUTO
    part_id: str | None = Field(default=None, max_length=36)


class VerifyStreamRequest(CamelModel):
    access_mode: AccessMode


class StreamVerificationRead(CamelModel):
    stream_id: str
    verified_at: datetime


class VideoPartRead(CamelModel):
    id: str
    video_id: str
    cid: int
    page_number: int
    title: str
    duration: int


class VideoStats(CamelModel):
    views: int | None = None
    likes: int | None = None
    favorites: int | None = None
    danmaku: int | None = None
    coins: int | None = None
    shares: int | None = None


class VideoRead(CamelModel):
    id: str
    provider: str
    bvid: str
    aid: int
    title: str
    description: str
    cover_url: str
    owner_name: str
    published_at: datetime | None
    duration: int
    part_count: int
    parts: list[VideoPartRead]
    stats: VideoStats
    tags: list[str]
    rights: dict[str, bool | int | str | None]
    parsed_at: datetime


class RecentVideoRead(CamelModel):
    id: str
    bvid: str
    title: str
    cover_url: str
    owner_name: str
    duration: int
    parsed_at: datetime


class MediaStreamRead(CamelModel):
    id: str
    kind: StreamKind
    quality_code: int
    quality_label: str
    codec: str
    container: str
    width: int | None
    height: int | None
    fps: float | None
    bitrate: int | None
    hdr_type: str | None
    audio_channels: int | None
    sample_rate: int | None
    estimated_size: int | None
    auth_required: bool
    premium_required: bool
    access_requirement: StreamAccessRequirement
    verified_at: datetime | None
    compatibility: str


class AccessRead(CamelModel):
    requested_mode: AccessMode
    actual_mode: AccessMode
    has_credentials: bool
    used_authentication: bool
    membership_type: str


class StreamsRead(CamelModel):
    part_id: str
    video: list[MediaStreamRead]
    audio: list[MediaStreamRead]
    fetched_at: datetime
    cache_hit: bool
    access: AccessRead


class ParseVideoResponse(CamelModel):
    video: VideoRead
    streams: StreamsRead
    normalized_url: str
    selected_part_id: str
    source_time: datetime
    cache_hit: bool
    access: AccessRead
