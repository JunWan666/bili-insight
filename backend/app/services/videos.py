from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.cookiejar import CookieJar
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.db.models import (
    AccessContext,
    MediaStream,
    StreamAccessRequirement,
    StreamKind,
    Video,
    VideoPart,
)
from app.providers.models import (
    ProviderPart,
    ProviderStream,
    ProviderStreams,
    ProviderVideo,
    VideoProvider,
    VideoReference,
)
from app.schemas.video import (
    AccessMode,
    AccessRead,
    MediaStreamRead,
    ParseVideoResponse,
    RecentVideoRead,
    StreamsRead,
    StreamVerificationRead,
    VideoPartRead,
    VideoRead,
    VideoStats,
)
from app.services.auth import AuthService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CachedSource:
    url: str
    backup_urls: tuple[str, ...]
    expires_at: datetime
    access_context: AccessContext


@dataclass(frozen=True, slots=True)
class ResolvedStream:
    stream_id: str
    url: str
    backup_urls: tuple[str, ...]
    kind: StreamKind
    codec: str
    container: str


class VideoService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        provider: VideoProvider,
        auth_service: AuthService,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.provider = provider
        self.auth_service = auth_service
        self._source_urls: dict[str, CachedSource] = {}
        self._lock = asyncio.Lock()

    async def parse(
        self,
        value: str,
        access_mode: AccessMode,
        *,
        force_refresh: bool = False,
    ) -> ParseVideoResponse:
        reference = await self.provider.normalize_url(value)
        actual_mode = (
            AccessMode.AUTHENTICATED
            if access_mode == AccessMode.AUTHENTICATED
            else AccessMode.ANONYMOUS
        )
        cookies = (
            await self.auth_service.cookie_jar()
            if actual_mode == AccessMode.AUTHENTICATED
            else None
        )
        context = (
            AccessContext.AUTHENTICATED
            if actual_mode == AccessMode.AUTHENTICATED
            else AccessContext.ANONYMOUS
        )

        async with self._lock:
            video, metadata_cache_hit = await self._get_or_fetch_video(
                reference, cookies=cookies, force_refresh=force_refresh
            )
            part = self._select_part(video, reference)
            streams, stream_cache_hit = await self._get_or_fetch_streams(
                video,
                part,
                context=context,
                cookies=cookies,
                force_refresh=force_refresh,
            )
        auth_status = self.auth_service.status()
        access = AccessRead(
            requested_mode=access_mode,
            actual_mode=actual_mode,
            has_credentials=auth_status.has_credentials,
            used_authentication=actual_mode == AccessMode.AUTHENTICATED,
            membership_type=auth_status.membership_type,
        )
        return ParseVideoResponse(
            video=self._video_read(video),
            streams=self._streams_read(part.id, streams, stream_cache_hit, access),
            normalized_url=reference.normalized_url,
            selected_part_id=part.id,
            source_time=datetime.now(UTC),
            cache_hit=metadata_cache_hit and stream_cache_hit,
            access=access,
        )

    async def get_video(self, video_id: str) -> VideoRead:
        async with self.session_factory() as session:
            video = await self._load_video(session, video_id)
            if video is None:
                raise self._not_found("视频记录不存在", "返回首页重新解析视频")
            return self._video_read(video)

    async def list_recent(self, limit: int) -> list[RecentVideoRead]:
        async with self.session_factory() as session:
            videos = list(
                (
                    await session.scalars(
                        select(Video).order_by(Video.parsed_at.desc()).limit(limit)
                    )
                ).all()
            )
        return [
            RecentVideoRead(
                id=video.id,
                bvid=video.bvid,
                title=video.title,
                cover_url=self._https_resource_url(video.cover_url),
                owner_name=video.owner_name,
                duration=video.duration,
                parsed_at=self._as_utc(video.parsed_at),
                normalized_url=self._part_url(video, video.parts[0]),
            )
            for video in videos
        ]

    async def get_parts(self, video_id: str) -> list[VideoPartRead]:
        async with self.session_factory() as session:
            video = await self._load_video(session, video_id)
            if video is None:
                raise self._not_found("视频记录不存在", "返回首页重新解析视频")
            return [self._part_read(part) for part in video.parts]

    async def get_part_streams(
        self,
        video_id: str,
        part_id: str,
        access_mode: AccessMode,
        *,
        force_refresh: bool = False,
    ) -> StreamsRead:
        actual_mode = (
            AccessMode.AUTHENTICATED
            if access_mode == AccessMode.AUTHENTICATED
            else AccessMode.ANONYMOUS
        )
        cookies = (
            await self.auth_service.cookie_jar()
            if actual_mode == AccessMode.AUTHENTICATED
            else None
        )
        context = (
            AccessContext.AUTHENTICATED
            if actual_mode == AccessMode.AUTHENTICATED
            else AccessContext.ANONYMOUS
        )
        async with self._lock, self.session_factory() as session:
            video = await self._load_video(session, video_id)
            if video is None:
                raise self._not_found("视频记录不存在", "返回首页重新解析视频")
            part = next((item for item in video.parts if item.id == part_id), None)
            if part is None:
                raise self._not_found("分 P 记录不存在", "重新选择视频分 P")
            streams, cache_hit = await self._get_or_fetch_streams(
                video,
                part,
                context=context,
                cookies=cookies,
                force_refresh=force_refresh,
            )
            auth_status = self.auth_service.status()
            return self._streams_read(
                part.id,
                streams,
                cache_hit,
                AccessRead(
                    requested_mode=access_mode,
                    actual_mode=actual_mode,
                    has_credentials=auth_status.has_credentials,
                    used_authentication=actual_mode == AccessMode.AUTHENTICATED,
                    membership_type=auth_status.membership_type,
                ),
            )

    async def refresh(
        self, video_id: str, access_mode: AccessMode, part_id: str | None
    ) -> ParseVideoResponse:
        async with self.session_factory() as session:
            video = await self._load_video(session, video_id)
            if video is None:
                raise self._not_found("视频记录不存在", "返回首页重新解析视频")
            selected = (
                next((part for part in video.parts if part.id == part_id), None)
                if part_id
                else video.parts[0]
            )
            if selected is None:
                raise self._not_found("分 P 记录不存在", "重新选择视频分 P")
            value = self._part_url(video, selected)
        return await self.parse(value, access_mode, force_refresh=True)

    async def resolve_stream(
        self,
        stream_id: str,
        access_mode: AccessMode,
        *,
        verify: bool = True,
        force_refresh: bool = False,
    ) -> ResolvedStream:
        now = datetime.now(UTC)
        cached = self._source_urls.get(stream_id)
        if not force_refresh and cached is not None and cached.expires_at > now:
            async with self.session_factory() as session:
                stream = await session.get(MediaStream, stream_id)
                if stream is not None and self._context_allowed(stream.access_context, access_mode):
                    if verify:
                        await self.provider.verify_stream(
                            cached.url,
                            await self.auth_service.cookie_jar()
                            if stream.access_context == AccessContext.AUTHENTICATED
                            else None,
                        )
                        stream.verified_at = now
                        await session.commit()
                    return ResolvedStream(
                        stream_id=stream.id,
                        url=cached.url,
                        backup_urls=cached.backup_urls,
                        kind=stream.kind,
                        codec=stream.codec,
                        container=stream.container,
                    )

        async with self._lock, self.session_factory() as session:
            stream = await session.scalar(
                select(MediaStream)
                .where(MediaStream.id == stream_id)
                .options(
                    selectinload(MediaStream.part)
                    .selectinload(VideoPart.video)
                    .selectinload(Video.parts)
                )
            )
            if stream is None:
                raise self._not_found("媒体流记录不存在", "重新解析并选择媒体规格")
            if not self._context_allowed(stream.access_context, access_mode):
                raise AppError(
                    ErrorCode.PERMISSION_DENIED,
                    "任务身份策略与所选媒体流不一致",
                    action="按当前身份重新解析并选择媒体规格",
                    status_code=403,
                )
            cookies = (
                await self.auth_service.cookie_jar()
                if stream.access_context == AccessContext.AUTHENTICATED
                else None
            )
            provider_video = self._provider_video(stream.part.video)
            provider_part = self._provider_part(stream.part)
            refreshed = await self.provider.get_streams(provider_video, provider_part, cookies)
            selected = self._match_stream(stream, refreshed)
            if selected is None:
                raise AppError(
                    ErrorCode.PERMISSION_DENIED,
                    "所选媒体规格已不再可用",
                    action="重新解析后选择其他规格",
                    status_code=409,
                )
            if verify:
                await self.provider.verify_stream(selected.url, cookies)
                stream.verified_at = now
            self._apply_provider_stream(stream, selected)
            stream.fetched_at = now
            await session.commit()
            self._source_urls[stream.id] = CachedSource(
                url=selected.url,
                backup_urls=selected.backup_urls,
                expires_at=now + timedelta(seconds=self.settings.stream_cache_ttl_seconds),
                access_context=stream.access_context,
            )
            return ResolvedStream(
                stream_id=stream.id,
                url=selected.url,
                backup_urls=selected.backup_urls,
                kind=stream.kind,
                codec=stream.codec,
                container=stream.container,
            )

    async def verify_stream(
        self, stream_id: str, access_mode: AccessMode
    ) -> StreamVerificationRead:
        await self.resolve_stream(stream_id, access_mode, verify=True)
        async with self.session_factory() as session:
            stream = await session.get(MediaStream, stream_id)
            if stream is None or stream.verified_at is None:
                raise self._not_found("媒体流记录不存在", "重新解析并选择媒体规格")
            return StreamVerificationRead(
                stream_id=stream.id,
                verified_at=self._as_utc(stream.verified_at),
            )

    async def clear_authenticated_cache(self) -> None:
        async with self.session_factory() as session:
            await session.execute(
                delete(MediaStream).where(MediaStream.access_context == AccessContext.AUTHENTICATED)
            )
            await session.commit()
        self._source_urls = {
            key: value
            for key, value in self._source_urls.items()
            if value.access_context != AccessContext.AUTHENTICATED
        }

    async def record_stream_verification(
        self,
        stream_id: str,
        *,
        sample_rate: int | None = None,
        audio_channels: int | None = None,
    ) -> bool:
        """Persist evidence from a completed, FFprobe-validated media track."""

        async with self.session_factory() as session:
            stream = await session.get(MediaStream, stream_id)
            if stream is None:
                return False
            stream.verified_at = datetime.now(UTC)
            if stream.kind == StreamKind.AUDIO:
                if sample_rate is not None and sample_rate > 0:
                    stream.sample_rate = sample_rate
                if audio_channels is not None and audio_channels > 0:
                    stream.audio_channels = audio_channels
            await session.commit()
            return True

    async def _get_or_fetch_video(
        self,
        reference: VideoReference,
        *,
        cookies: CookieJar | None,
        force_refresh: bool,
    ) -> tuple[Video, bool]:
        cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.metadata_cache_ttl_seconds)
        async with self.session_factory() as session:
            existing: Video | None = None
            if reference.episode_id is not None:
                candidates = list(
                    (
                        await session.scalars(
                            select(Video)
                            .where(Video.provider == reference.provider)
                            .options(selectinload(Video.parts))
                        )
                    ).all()
                )
                existing = next(
                    (
                        item
                        for item in candidates
                        if self._episode_cid(item, reference.episode_id) is not None
                    ),
                    None,
                )
            else:
                statement = (
                    select(Video)
                    .where(Video.provider == reference.provider)
                    .options(selectinload(Video.parts))
                )
                if reference.bvid:
                    statement = statement.where(Video.bvid == reference.bvid)
                else:
                    statement = statement.where(Video.aid == reference.aid)
                existing = await session.scalar(statement)
            if (
                existing is not None
                and not force_refresh
                and self._as_utc(existing.parsed_at) >= cutoff
            ):
                return existing, True

        provider_video = await self.provider.get_video(reference, cookies)
        return await self._upsert_video(provider_video), False

    async def _upsert_video(self, source: ProviderVideo) -> Video:
        async with self.session_factory() as session:
            video = await session.scalar(
                select(Video)
                .where(
                    Video.provider == source.provider,
                    or_(Video.bvid == source.bvid, Video.aid == source.aid),
                )
                .options(selectinload(Video.parts))
            )
            raw_metadata = source.raw_metadata
            if video is not None and source.provider == "bilibili_pgc":
                raw_metadata = self._merge_pgc_raw_metadata(
                    video.raw_metadata,
                    source.raw_metadata,
                )
            if video is None:
                video = Video(
                    provider=source.provider,
                    bvid=source.bvid,
                    aid=source.aid,
                    title=source.title,
                    description=source.description,
                    cover_url=source.cover_url,
                    owner_name=source.owner_name,
                    duration=source.duration,
                    published_at=source.published_at,
                    stats=source.stats,
                    tags=source.tags,
                    rights=source.rights,
                    raw_metadata=raw_metadata,
                    parsed_at=datetime.now(UTC),
                    parts=[],
                )
                session.add(video)
                await session.flush()
            else:
                video.provider = source.provider
                video.bvid = source.bvid
                video.aid = source.aid
                video.title = source.title
                video.description = source.description
                video.cover_url = source.cover_url
                video.owner_name = source.owner_name
                video.duration = source.duration
                video.published_at = source.published_at
                video.stats = source.stats
                video.tags = source.tags
                video.rights = source.rights
                video.raw_metadata = raw_metadata
                video.parsed_at = datetime.now(UTC)

            existing_parts = {part.cid: part for part in video.parts}
            source_cids = {part.cid for part in source.parts}
            preserved_section_cids = self._pgc_section_episode_cids(video.raw_metadata)
            source_section_cids = self._pgc_section_episode_cids(source.raw_metadata)
            source_cids.update(preserved_section_cids)
            used_page_numbers = {part.page_number for part in video.parts}
            next_section_page = max(used_page_numbers, default=0) + 1
            for old_part in list(video.parts):
                if old_part.cid not in source_cids:
                    await session.delete(old_part)
            for source_part in source.parts:
                part = existing_parts.get(source_part.cid)
                if part is None:
                    page_number = source_part.page_number
                    if source_part.cid in source_section_cids and page_number in used_page_numbers:
                        page_number = next_section_page
                        next_section_page += 1
                    used_page_numbers.add(page_number)
                    part = VideoPart(
                        video_id=video.id,
                        cid=source_part.cid,
                        page_number=page_number,
                        title=source_part.title,
                        duration=source_part.duration,
                    )
                    session.add(part)
                else:
                    if source_part.cid not in preserved_section_cids:
                        part.page_number = source_part.page_number
                    part.title = source_part.title
                    part.duration = source_part.duration
            await session.commit()
            refreshed = await self._load_video(session, video.id)
            if refreshed is None:
                raise RuntimeError("Video upsert did not produce a database row")
            return refreshed

    async def _get_or_fetch_streams(
        self,
        video: Video,
        part: VideoPart,
        *,
        context: AccessContext,
        cookies: CookieJar | None,
        force_refresh: bool,
    ) -> tuple[list[MediaStream], bool]:
        cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.stream_cache_ttl_seconds)
        async with self.session_factory() as session:
            cached = list(
                (
                    await session.scalars(
                        select(MediaStream)
                        .where(
                            MediaStream.part_id == part.id,
                            MediaStream.access_context == context,
                            MediaStream.fetched_at >= cutoff,
                        )
                        .order_by(MediaStream.kind.desc(), MediaStream.quality_code.desc())
                    )
                ).all()
            )
        if cached and not force_refresh:
            return cached, True

        provider_video = self._provider_video(video)
        provider_part = self._provider_part(part)
        anonymous_capabilities: set[tuple[object, ...]] = set()
        if context == AccessContext.AUTHENTICATED:
            try:
                anonymous, _ = await self._get_or_fetch_streams(
                    video,
                    part,
                    context=AccessContext.ANONYMOUS,
                    cookies=None,
                    force_refresh=force_refresh,
                )
            except AppError as exc:
                logger.info(
                    "Anonymous capability baseline unavailable during authenticated parse: %s",
                    exc.code.value,
                    extra={"event": "anonymous_stream_baseline_unavailable"},
                )
            else:
                anonymous_capabilities = {self._capability_key(item) for item in anonymous}
        provider_streams = await self.provider.get_streams(provider_video, provider_part, cookies)
        persisted = await self._persist_streams(
            part.id,
            context,
            provider_streams,
            anonymous_capabilities=anonymous_capabilities,
        )
        return persisted, False

    async def _persist_streams(
        self,
        part_id: str,
        context: AccessContext,
        streams: ProviderStreams,
        *,
        anonymous_capabilities: set[tuple[object, ...]],
    ) -> list[MediaStream]:
        now = datetime.now(UTC)
        self._source_urls = {
            key: value for key, value in self._source_urls.items() if value.expires_at > now
        }
        source_streams = [*streams.video, *streams.audio]
        async with self.session_factory() as session:
            existing = list(
                (
                    await session.scalars(
                        select(MediaStream).where(
                            MediaStream.part_id == part_id,
                            MediaStream.access_context == context,
                        )
                    )
                ).all()
            )
            by_key = {stream.source_key: stream for stream in existing}
            source_keys = {stream.source_key for stream in source_streams}
            for stale in existing:
                if stale.source_key not in source_keys:
                    self._source_urls.pop(stale.id, None)
                    await session.delete(stale)
            result = []
            for source in source_streams:
                stream = by_key.get(source.source_key)
                auth_required = (
                    context == AccessContext.AUTHENTICATED
                    and self._capability_key(source) not in anonymous_capabilities
                )
                access_requirement = source.access_requirement
                if auth_required and access_requirement == StreamAccessRequirement.NONE:
                    access_requirement = StreamAccessRequirement.LOGIN
                if stream is None:
                    stream = MediaStream(
                        part_id=part_id,
                        access_context=context,
                        source_key=source.source_key,
                        kind=source.kind,
                        quality_code=source.quality_code,
                        quality_label=source.quality_label,
                        codec=source.codec,
                        container=source.container,
                        mime_type=source.mime_type,
                        codec_string=source.codec_string,
                        init_range_start=source.init_range_start,
                        init_range_end=source.init_range_end,
                        index_range_start=source.index_range_start,
                        index_range_end=source.index_range_end,
                        width=source.width,
                        height=source.height,
                        fps=source.fps,
                        bitrate=source.bitrate,
                        hdr_type=source.hdr_type,
                        audio_channels=source.audio_channels,
                        sample_rate=source.sample_rate,
                        estimated_size=source.estimated_size,
                        auth_required=auth_required,
                        access_requirement=access_requirement,
                        compatibility=source.compatibility,
                        fetched_at=now,
                    )
                    session.add(stream)
                    await session.flush()
                else:
                    stream.kind = source.kind
                    stream.quality_code = source.quality_code
                    stream.quality_label = source.quality_label
                    stream.codec = source.codec
                    stream.container = source.container
                    stream.mime_type = source.mime_type
                    stream.codec_string = source.codec_string
                    stream.init_range_start = source.init_range_start
                    stream.init_range_end = source.init_range_end
                    stream.index_range_start = source.index_range_start
                    stream.index_range_end = source.index_range_end
                    stream.width = source.width
                    stream.height = source.height
                    stream.fps = source.fps
                    stream.bitrate = source.bitrate
                    stream.hdr_type = source.hdr_type
                    stream.audio_channels = source.audio_channels
                    stream.sample_rate = source.sample_rate
                    stream.estimated_size = source.estimated_size
                    stream.auth_required = auth_required
                    stream.access_requirement = access_requirement
                    stream.compatibility = source.compatibility
                    stream.fetched_at = now
                self._source_urls[stream.id] = CachedSource(
                    url=source.url,
                    backup_urls=source.backup_urls,
                    expires_at=now + timedelta(seconds=self.settings.stream_cache_ttl_seconds),
                    access_context=context,
                )
                result.append(stream)
            await session.commit()
            return result

    @staticmethod
    async def _load_video(session: AsyncSession, video_id: str) -> Video | None:
        result: Video | None = await session.scalar(
            select(Video)
            .where(Video.id == video_id)
            .options(selectinload(Video.parts))
            .execution_options(populate_existing=True)
        )
        return result

    @classmethod
    def _select_part(cls, video: Video, reference: VideoReference) -> VideoPart:
        selected_cid = (
            cls._episode_cid(video, reference.episode_id)
            if reference.episode_id is not None
            else None
        )
        part = (
            next((item for item in video.parts if item.cid == selected_cid), None)
            if selected_cid is not None
            else next(
                (item for item in video.parts if item.page_number == reference.page_number),
                None,
            )
        )
        if part is None:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "链接指定的分 P 或剧集不存在",
                action="选择该视频当前已有的分 P 或剧集",
                status_code=422,
            )
        return part

    @classmethod
    def _part_url(cls, video: Video, part: VideoPart) -> str:
        if video.provider == "bilibili_pgc":
            episode = cls._episode_metadata(video, cid=part.cid)
            episode_id = episode.get("episodeId") if episode is not None else None
            if isinstance(episode_id, int) and episode_id > 0:
                return f"https://www.bilibili.com/bangumi/play/ep{episode_id}"
            season_id = video.raw_metadata.get("seasonId")
            if isinstance(season_id, int) and season_id > 0:
                return f"https://www.bilibili.com/bangumi/play/ss{season_id}"
            raise AppError(
                ErrorCode.UPSTREAM_CHANGED,
                "番剧定位信息不完整，暂时无法刷新",
                action="返回首页重新解析该番剧链接",
                status_code=502,
            )
        suffix = f"?p={part.page_number}" if part.page_number != 1 else ""
        return f"https://www.bilibili.com/video/{video.bvid}/{suffix}"

    @classmethod
    def official_url(cls, video: Video, part: VideoPart) -> str:
        return cls._part_url(video, part)

    @classmethod
    def _episode_cid(cls, video: Video, episode_id: int) -> int | None:
        episode = cls._episode_metadata(video, episode_id=episode_id)
        cid = episode.get("cid") if episode is not None else None
        return cid if isinstance(cid, int) and cid > 0 else None

    @staticmethod
    def _pgc_section_episode_cids(metadata: dict[str, Any]) -> set[int]:
        episodes = metadata.get("episodes")
        if not isinstance(episodes, list):
            return set()
        return {
            cid
            for item in episodes
            if isinstance(item, dict) and item.get("sectionEpisode") is True
            for cid in [item.get("cid")]
            if isinstance(cid, int) and not isinstance(cid, bool) and cid > 0
        }

    @classmethod
    def _merge_pgc_raw_metadata(
        cls,
        existing: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        if incoming.get("contentType") != "bangumi":
            return incoming
        incoming_episodes = incoming.get("episodes")
        existing_episodes = existing.get("episodes")
        if not isinstance(incoming_episodes, list) or not isinstance(existing_episodes, list):
            return incoming

        merged_episodes: list[dict[str, Any]] = []
        seen_episode_ids: set[int] = set()
        seen_cids: set[int] = set()
        candidates = [
            *(item for item in incoming_episodes if isinstance(item, dict)),
            *(
                item
                for item in existing_episodes
                if isinstance(item, dict) and item.get("sectionEpisode") is True
            ),
        ]
        for item in candidates:
            episode_id = item.get("episodeId")
            cid = item.get("cid")
            normalized_episode_id = (
                episode_id
                if isinstance(episode_id, int)
                and not isinstance(episode_id, bool)
                and episode_id > 0
                else None
            )
            normalized_cid = (
                cid if isinstance(cid, int) and not isinstance(cid, bool) and cid > 0 else None
            )
            if (
                normalized_episode_id is not None and normalized_episode_id in seen_episode_ids
            ) or (normalized_cid is not None and normalized_cid in seen_cids):
                continue
            if normalized_episode_id is not None:
                seen_episode_ids.add(normalized_episode_id)
            if normalized_cid is not None:
                seen_cids.add(normalized_cid)
            merged_episodes.append(item)

        merged = dict(incoming)
        merged["episodes"] = merged_episodes
        return merged

    @staticmethod
    def _episode_metadata(
        video: Video,
        *,
        episode_id: int | None = None,
        cid: int | None = None,
    ) -> dict[str, object] | None:
        if video.provider != "bilibili_pgc" or video.raw_metadata.get("contentType") != "bangumi":
            return None
        episodes = video.raw_metadata.get("episodes")
        if not isinstance(episodes, list):
            return None
        return next(
            (
                item
                for item in episodes
                if isinstance(item, dict)
                and (
                    (episode_id is not None and item.get("episodeId") == episode_id)
                    or (cid is not None and item.get("cid") == cid)
                )
            ),
            None,
        )

    @classmethod
    def _video_read(cls, video: Video) -> VideoRead:
        stats = video.stats
        return VideoRead(
            id=video.id,
            provider=video.provider,
            bvid=video.bvid,
            aid=video.aid,
            title=video.title,
            description=video.description,
            cover_url=cls._https_resource_url(video.cover_url),
            owner_name=video.owner_name,
            published_at=cls._as_utc_optional(video.published_at),
            duration=video.duration,
            part_count=len(video.parts),
            parts=[cls._part_read(part) for part in video.parts],
            stats=VideoStats(
                views=stats.get("views"),
                likes=stats.get("likes"),
                favorites=stats.get("favorites"),
                danmaku=stats.get("danmaku"),
                coins=stats.get("coins"),
                shares=stats.get("shares"),
            ),
            tags=video.tags,
            rights=video.rights,
            parsed_at=cls._as_utc(video.parsed_at),
            normalized_url=cls._part_url(video, video.parts[0]),
        )

    @staticmethod
    def _part_read(part: VideoPart) -> VideoPartRead:
        return VideoPartRead(
            id=part.id,
            video_id=part.video_id,
            cid=part.cid,
            page_number=part.page_number,
            title=part.title,
            duration=part.duration,
        )

    @classmethod
    def _streams_read(
        cls,
        part_id: str,
        streams: list[MediaStream],
        cache_hit: bool,
        access: AccessRead,
    ) -> StreamsRead:
        fetched_at = max(
            (cls._as_utc(item.fetched_at) for item in streams),
            default=datetime.now(UTC),
        )
        reads = [cls._stream_read(item) for item in streams]
        return StreamsRead(
            part_id=part_id,
            video=[item for item in reads if item.kind == StreamKind.VIDEO],
            audio=[item for item in reads if item.kind == StreamKind.AUDIO],
            fetched_at=fetched_at,
            cache_hit=cache_hit,
            access=access,
        )

    @staticmethod
    def _stream_read(stream: MediaStream) -> MediaStreamRead:
        preview_supported = all(
            value is not None
            for value in (
                stream.mime_type,
                stream.codec_string,
                stream.init_range_start,
                stream.init_range_end,
                stream.index_range_start,
                stream.index_range_end,
            )
        )
        return MediaStreamRead(
            id=stream.id,
            kind=stream.kind,
            quality_code=stream.quality_code,
            quality_label=stream.quality_label,
            codec=stream.codec,
            container=stream.container,
            mime_type=stream.mime_type,
            codec_string=stream.codec_string,
            preview_supported=preview_supported,
            width=stream.width,
            height=stream.height,
            fps=stream.fps,
            bitrate=stream.bitrate,
            hdr_type=stream.hdr_type,
            audio_channels=stream.audio_channels,
            sample_rate=stream.sample_rate,
            estimated_size=stream.estimated_size,
            auth_required=stream.auth_required,
            premium_required=(stream.access_requirement == StreamAccessRequirement.PREMIUM),
            access_requirement=stream.access_requirement,
            verified_at=VideoService._as_utc_optional(stream.verified_at),
            compatibility=stream.compatibility,
        )

    @staticmethod
    def _apply_provider_stream(stream: MediaStream, source: ProviderStream) -> None:
        stream.kind = source.kind
        stream.quality_code = source.quality_code
        stream.quality_label = source.quality_label
        stream.codec = source.codec
        stream.container = source.container
        stream.mime_type = source.mime_type
        stream.codec_string = source.codec_string
        stream.init_range_start = source.init_range_start
        stream.init_range_end = source.init_range_end
        stream.index_range_start = source.index_range_start
        stream.index_range_end = source.index_range_end
        stream.width = source.width
        stream.height = source.height
        stream.fps = source.fps
        stream.bitrate = source.bitrate
        stream.hdr_type = source.hdr_type
        stream.audio_channels = source.audio_channels
        stream.sample_rate = source.sample_rate
        stream.estimated_size = source.estimated_size
        stream.compatibility = source.compatibility

    @staticmethod
    def _https_resource_url(value: str) -> str:
        if value.startswith("//"):
            return f"https:{value}"
        if value.startswith("http://"):
            return f"https://{value[7:]}"
        return value

    @staticmethod
    def _provider_video(video: Video) -> ProviderVideo:
        return ProviderVideo(
            provider=video.provider,
            bvid=video.bvid,
            aid=video.aid,
            title=video.title,
            description=video.description,
            cover_url=video.cover_url,
            owner_name=video.owner_name,
            duration=video.duration,
            published_at=VideoService._as_utc_optional(video.published_at),
            stats=video.stats,
            tags=video.tags,
            rights=video.rights,
            parts=[VideoService._provider_part(part) for part in video.parts],
            raw_metadata=video.raw_metadata,
        )

    @staticmethod
    def _provider_part(part: VideoPart) -> ProviderPart:
        return ProviderPart(
            cid=part.cid,
            page_number=part.page_number,
            title=part.title,
            duration=part.duration,
        )

    @staticmethod
    def _capability_key(stream: ProviderStream | MediaStream) -> tuple[object, ...]:
        return (
            stream.kind,
            stream.quality_code,
            stream.codec,
            stream.width,
            stream.height,
        )

    @staticmethod
    def _match_stream(target: MediaStream, collection: ProviderStreams) -> ProviderStream | None:
        candidates = collection.video if target.kind == StreamKind.VIDEO else collection.audio
        target_key = VideoService._capability_key(target)
        return next(
            (
                candidate
                for candidate in candidates
                if VideoService._capability_key(candidate) == target_key
            ),
            None,
        )

    @staticmethod
    def _context_allowed(context: AccessContext, mode: AccessMode) -> bool:
        if context == AccessContext.ANONYMOUS:
            return mode in {AccessMode.AUTO, AccessMode.ANONYMOUS}
        return mode == AccessMode.AUTHENTICATED

    @staticmethod
    def _not_found(message: str, action: str) -> AppError:
        return AppError(
            ErrorCode.RESOURCE_NOT_FOUND,
            message,
            action=action,
            status_code=404,
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _as_utc_optional(value: datetime | None) -> datetime | None:
        return VideoService._as_utc(value) if value is not None else None
