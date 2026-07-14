from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from http.cookiejar import CookieJar
from typing import Any, Protocol

from app.db.models import StreamAccessRequirement, StreamKind


@dataclass(frozen=True, slots=True)
class VideoReference:
    bvid: str | None
    aid: int | None
    page_number: int
    normalized_url: str
    provider: str = "bilibili"
    season_id: int | None = None
    episode_id: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderPart:
    cid: int
    page_number: int
    title: str
    duration: int


@dataclass(frozen=True, slots=True)
class ProviderVideo:
    provider: str
    bvid: str
    aid: int
    title: str
    description: str
    cover_url: str
    owner_name: str
    duration: int
    published_at: datetime | None
    stats: dict[str, int | None]
    tags: list[str]
    rights: dict[str, bool | int | str | None]
    parts: list[ProviderPart]
    raw_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderStream:
    source_key: str
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
    access_requirement: StreamAccessRequirement
    compatibility: str
    url: str
    mime_type: str | None = None
    codec_string: str | None = None
    init_range_start: int | None = None
    init_range_end: int | None = None
    index_range_start: int | None = None
    index_range_end: int | None = None
    backup_urls: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ProviderStreams:
    video: list[ProviderStream]
    audio: list[ProviderStream]


@dataclass(frozen=True, slots=True)
class ProviderSubtitle:
    subtitle_id: str
    language: str
    language_label: str
    url: str


@dataclass(frozen=True, slots=True)
class AuthValidation:
    logged_in: bool
    account_name: str | None
    membership_type: str
    premium: bool


class VideoProvider(Protocol):
    async def normalize_url(self, value: str) -> VideoReference: ...

    async def get_video(
        self, reference: VideoReference, cookies: CookieJar | None = None
    ) -> ProviderVideo: ...

    async def get_streams(
        self, video: ProviderVideo, part: ProviderPart, cookies: CookieJar | None = None
    ) -> ProviderStreams: ...

    async def get_subtitles(
        self, video: ProviderVideo, part: ProviderPart, cookies: CookieJar | None = None
    ) -> list[ProviderSubtitle]: ...

    async def get_danmaku(self, video: ProviderVideo, part: ProviderPart) -> bytes: ...

    async def validate_auth(self, cookies: CookieJar) -> AuthValidation: ...

    async def verify_stream(self, url: str, cookies: CookieJar | None = None) -> None: ...
