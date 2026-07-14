from __future__ import annotations

import asyncio
import re
import uuid
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

import httpx
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.db.models import AccessContext, MediaStream, StreamKind
from app.media.security import DNSResolver, MediaURLValidator, UnsafeMediaURLError
from app.schemas.previews import PreviewRead, PreviewTrackRead
from app.schemas.video import AccessMode

_DASH_NAMESPACE = "urn:mpeg:dash:schema:mpd:2011"
_BYTE_RANGE = re.compile(r"^bytes=(?:(\d+)-(\d*)|-(\d+))$", re.IGNORECASE)
_CONTENT_RANGE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.IGNORECASE)
_UNSATISFIED_RANGE = re.compile(r"^bytes\s+\*/(\d+)$", re.IGNORECASE)
_MIME_TYPE = re.compile(r"^(?:video|audio)/[a-z0-9][a-z0-9.+-]{0,63}$")
_CODEC_STRING = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ResolvedPreviewSource(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def backup_urls(self) -> tuple[str, ...]: ...


class PreviewStreamResolver(Protocol):
    async def resolve_stream(
        self,
        stream_id: str,
        access_mode: AccessMode,
        *,
        verify: bool = True,
        force_refresh: bool = False,
    ) -> ResolvedPreviewSource: ...


@dataclass(frozen=True, slots=True)
class PreviewStreamRecord:
    id: str
    part_id: str
    part_duration: int
    kind: StreamKind
    access_context: AccessContext
    mime_type: str
    codec_string: str
    bitrate: int | None
    width: int | None
    height: int | None
    fps: float | None
    sample_rate: int | None
    init_range_start: int
    init_range_end: int
    index_range_start: int
    index_range_end: int


@dataclass(frozen=True, slots=True)
class PreviewTrack:
    record: PreviewStreamRecord
    url: str
    backup_urls: tuple[str, ...]


@dataclass(slots=True)
class PreviewSession:
    id: str
    access_mode: AccessMode
    part_id: str
    duration: int
    tracks: dict[StreamKind, PreviewTrack]
    created_at: datetime
    last_accessed_at: datetime
    refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


@dataclass(frozen=True, slots=True)
class RequestedRange:
    raw: str
    start: int | None
    end: int | None
    suffix_length: int | None

    @property
    def requested_length(self) -> int | None:
        if self.start is not None and self.end is not None:
            return self.end - self.start + 1
        return self.suffix_length


class PreviewRangeNotSatisfiable(ValueError):
    def __init__(self, total_size: int | None = None) -> None:
        super().__init__("requested preview range is not satisfiable")
        self.total_size = total_size


class PreviewStreamInterrupted(RuntimeError):
    """Raised after response headers when the upstream body becomes invalid."""


@dataclass(slots=True)
class PreviewMediaDelivery:
    status_code: int
    media_type: str
    headers: dict[str, str]
    response: httpx.Response
    client: httpx.AsyncClient
    maximum_bytes: int
    declared_length: int | None
    release_slot: Callable[[], None] | None = None
    closed: bool = False

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            await self.response.aclose()
        finally:
            try:
                await self.client.aclose()
            finally:
                if self.release_slot is not None:
                    self.release_slot()
                    self.release_slot = None

    async def stream(self) -> AsyncIterator[bytes]:
        received = 0
        try:
            async for chunk in self.response.aiter_raw():
                if not chunk:
                    continue
                received += len(chunk)
                if received > self.maximum_bytes or (
                    self.declared_length is not None and received > self.declared_length
                ):
                    raise PreviewStreamInterrupted("preview response exceeded its byte limit")
                yield chunk
            if self.declared_length is not None and received != self.declared_length:
                raise PreviewStreamInterrupted("preview response ended before its declared length")
        except (httpx.HTTPError, httpx.StreamError, PreviewStreamInterrupted):
            raise PreviewStreamInterrupted("preview media transfer was interrupted") from None
        finally:
            await self.close()


Clock = Callable[[], datetime]


class PreviewService:
    """Create short-lived DASH manifests and proxy their selected media tracks."""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession] | None,
        stream_resolver: PreviewStreamResolver,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        media_resolver: DNSResolver | None = None,
        idle_ttl_seconds: int = 1_800,
        maximum_lifetime_seconds: int = 21_600,
        cleanup_interval_seconds: int = 60,
        maximum_sessions: int = 32,
        maximum_range_bytes: int = 64 * 1024 * 1024,
        concurrency: int = 8,
        clock: Clock | None = None,
    ) -> None:
        if not 30 <= idle_ttl_seconds <= 86_400:
            raise ValueError("Preview idle TTL is outside the safe range")
        if maximum_lifetime_seconds < idle_ttl_seconds:
            raise ValueError("Preview maximum lifetime must cover the idle TTL")
        if not 1 <= cleanup_interval_seconds <= idle_ttl_seconds:
            raise ValueError("Preview cleanup interval is outside the safe range")
        if not 1 <= maximum_sessions <= 256:
            raise ValueError("Preview session limit is outside the safe range")
        if not 1_024 <= maximum_range_bytes <= 512 * 1024 * 1024:
            raise ValueError("Preview range limit is outside the safe range")
        if not 1 <= concurrency <= 32:
            raise ValueError("Preview concurrency is outside the safe range")

        self.settings = settings
        self.session_factory = session_factory
        self.stream_resolver = stream_resolver
        self.transport = transport
        self.idle_ttl = timedelta(seconds=idle_ttl_seconds)
        self.maximum_lifetime = timedelta(seconds=maximum_lifetime_seconds)
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self.maximum_sessions = maximum_sessions
        self.maximum_range_bytes = maximum_range_bytes
        self._clock = clock or (lambda: datetime.now(UTC))
        self._validator = MediaURLValidator(
            settings.media_host_suffixes,
            resolver=media_resolver,
        )
        self._sessions: dict[str, PreviewSession] = {}
        self._lock = asyncio.Lock()
        self._request_slots = asyncio.Semaphore(concurrency)
        self._cleanup_task: asyncio.Task[None] | None = None
        self._stop_cleanup = asyncio.Event()

    async def start(self) -> None:
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        self._stop_cleanup.clear()
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(),
            name="preview-session-cleanup",
        )

    async def stop(self) -> None:
        task = self._cleanup_task
        if task is None:
            return
        self._stop_cleanup.set()
        try:
            await task
        finally:
            self._cleanup_task = None

    async def create(
        self,
        video_stream_id: str,
        audio_stream_id: str | None,
        access_mode: AccessMode,
    ) -> PreviewRead:
        identifiers = [video_stream_id]
        if audio_stream_id is not None:
            identifiers.append(audio_stream_id)
        resolved = await self._resolve_stream_sources(identifiers, access_mode)
        records = {item.id: item for item in await self._load_stream_records(identifiers)}
        video = records.get(video_stream_id)
        audio = records.get(audio_stream_id) if audio_stream_id is not None else None
        if video is None or (audio_stream_id is not None and audio is None):
            raise self._not_found()
        self._validate_track(video, StreamKind.VIDEO, access_mode)
        if audio is not None:
            self._validate_track(audio, StreamKind.AUDIO, access_mode)
            if audio.part_id != video.part_id:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    "预览音视频轨不属于同一分 P",
                    action="为当前分 P 重新选择音频流",
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )

        selected_records = [video, *([audio] if audio is not None else [])]
        sources = dict(zip(identifiers, resolved, strict=True))
        tracks = {
            item.kind: PreviewTrack(
                record=item,
                url=sources[item.id].url,
                backup_urls=sources[item.id].backup_urls,
            )
            for item in selected_records
        }
        now = self._now()
        session = PreviewSession(
            id=str(uuid.uuid4()),
            access_mode=access_mode,
            part_id=video.part_id,
            duration=max(1, video.part_duration),
            tracks=tracks,
            created_at=now,
            last_accessed_at=now,
        )
        async with self._lock:
            self._purge_expired_unlocked(now)
            if len(self._sessions) >= self.maximum_sessions:
                oldest = min(
                    self._sessions.values(),
                    key=lambda item: item.last_accessed_at,
                )
                self._sessions.pop(oldest.id, None)
            self._sessions[session.id] = session
        return self._session_read(session)

    async def _resolve_stream_sources(
        self,
        stream_ids: Sequence[str],
        access_mode: AccessMode,
    ) -> list[ResolvedPreviewSource]:
        tasks = [
            asyncio.create_task(
                self.stream_resolver.resolve_stream(
                    stream_id,
                    access_mode,
                    verify=False,
                ),
                name=f"preview-resolve-{stream_id}",
            )
            for stream_id in stream_ids
        ]
        try:
            return list(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def manifest(self, preview_id: str) -> bytes:
        session = await self._get_session(preview_id)
        return self._build_mpd(session)

    async def media(
        self,
        preview_id: str,
        kind: StreamKind,
        range_header: str | None,
        *,
        head: bool = False,
    ) -> PreviewMediaDelivery:
        session = await self._get_session(preview_id)
        track = session.tracks.get(kind)
        if track is None:
            raise self._not_found("预览音轨不存在")
        requested_range = None if head else self._parse_range(range_header, required=True)
        await self._request_slots.acquire()
        try:
            session = await self._get_session(preview_id)
            track = session.tracks.get(kind)
            if track is None:
                raise self._not_found()
            delivery = await self._open_media(
                session,
                kind,
                track,
                requested_range,
                head=head,
            )
        except BaseException:
            self._request_slots.release()
            raise
        delivery.release_slot = self._request_slots.release
        return delivery

    async def delete(self, preview_id: str) -> bool:
        async with self._lock:
            return self._sessions.pop(preview_id, None) is not None

    async def clear_authenticated_sessions(self) -> None:
        async with self._lock:
            self._sessions = {
                key: session
                for key, session in self._sessions.items()
                if session.access_mode != AccessMode.AUTHENTICATED
            }

    async def cleanup_expired(self) -> int:
        now = self._now()
        async with self._lock:
            before = len(self._sessions)
            self._purge_expired_unlocked(now)
            return before - len(self._sessions)

    async def _cleanup_loop(self) -> None:
        while not self._stop_cleanup.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_cleanup.wait(),
                    timeout=self.cleanup_interval_seconds,
                )
            except TimeoutError:
                await self.cleanup_expired()

    async def _get_session(self, preview_id: str) -> PreviewSession:
        now = self._now()
        async with self._lock:
            self._purge_expired_unlocked(now)
            session = self._sessions.get(preview_id)
            if session is None:
                raise self._not_found("预览会话不存在或已过期")
            session.last_accessed_at = now
            return session

    def _purge_expired_unlocked(self, now: datetime) -> None:
        expired = [
            key for key, session in self._sessions.items() if self._expires_at(session) <= now
        ]
        for key in expired:
            self._sessions.pop(key, None)

    def _expires_at(self, session: PreviewSession) -> datetime:
        return min(
            session.last_accessed_at + self.idle_ttl,
            session.created_at + self.maximum_lifetime,
        )

    async def _open_media(
        self,
        session: PreviewSession,
        kind: StreamKind,
        initial_track: PreviewTrack,
        requested_range: RequestedRange | None,
        *,
        head: bool,
    ) -> PreviewMediaDelivery:
        track = initial_track
        saw_network_failure = False
        saw_expired_source = False
        for attempt in range(2):
            result, network_failure, expired_source = await self._try_sources(
                track,
                requested_range,
                head=head,
            )
            if result is not None:
                return result
            saw_network_failure = saw_network_failure or network_failure
            saw_expired_source = saw_expired_source or expired_source
            if attempt == 0:
                track = await self._refresh_track(session, kind, track)

        if saw_expired_source:
            raise AppError(
                ErrorCode.PERMISSION_DENIED,
                "预览媒体地址已失效",
                action="关闭预览后重新选择该规格",
                status_code=status.HTTP_409_CONFLICT,
            )
        if saw_network_failure:
            raise AppError(
                ErrorCode.UPSTREAM_NETWORK,
                "预览媒体暂时无法连接",
                action="检查网络后重新播放",
                status_code=status.HTTP_502_BAD_GATEWAY,
            )
        raise self._upstream_error()

    async def _try_sources(
        self,
        track: PreviewTrack,
        requested_range: RequestedRange | None,
        *,
        head: bool,
    ) -> tuple[PreviewMediaDelivery | None, bool, bool]:
        network_failure = False
        expired_source = False
        for url in self._unique_sources(track.url, track.backup_urls):
            try:
                target = await self._validator.resolve(url)
            except UnsafeMediaURLError:
                raise self._upstream_error() from None
            for address in target.addresses:
                client = self._client()
                headers = self._request_headers(
                    target.host_header,
                    requested_range.raw if requested_range is not None else None,
                )
                try:
                    request = client.build_request(
                        "HEAD" if head else "GET",
                        target.pinned_url(address),
                        headers=headers,
                        extensions={"sni_hostname": target.host},
                    )
                    response = await client.send(request, stream=True)
                except asyncio.CancelledError:
                    await client.aclose()
                    raise
                except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                    network_failure = True
                    await client.aclose()
                    continue

                if response.status_code in {401, 403, 404, 410}:
                    expired_source = True
                    await response.aclose()
                    await client.aclose()
                    continue
                if response.status_code == status.HTTP_416_RANGE_NOT_SATISFIABLE:
                    total = self._unsatisfied_total(response.headers.get("Content-Range"))
                    await response.aclose()
                    await client.aclose()
                    raise PreviewRangeNotSatisfiable(total)
                try:
                    delivery = self._validated_delivery(
                        track,
                        response,
                        client,
                        requested_range,
                        head=head,
                    )
                except Exception:
                    await response.aclose()
                    await client.aclose()
                    raise
                return delivery, network_failure, expired_source
        return None, network_failure, expired_source

    async def _refresh_track(
        self,
        session: PreviewSession,
        kind: StreamKind,
        stale_track: PreviewTrack,
    ) -> PreviewTrack:
        async with session.refresh_lock:
            current = session.tracks[kind]
            if current.url != stale_track.url or current.backup_urls != stale_track.backup_urls:
                return current
            source = await self.stream_resolver.resolve_stream(
                current.record.id,
                session.access_mode,
                verify=False,
                force_refresh=True,
            )
            records = await self._load_stream_records([current.record.id])
            if not records:
                raise self._not_found()
            refreshed_record = records[0]
            if self._representation_key(refreshed_record) != self._representation_key(
                current.record
            ):
                raise AppError(
                    ErrorCode.UPSTREAM_CHANGED,
                    "预览媒体结构已变化",
                    action="关闭预览并重新解析该视频",
                    status_code=status.HTTP_409_CONFLICT,
                )
            refreshed = PreviewTrack(
                record=refreshed_record,
                url=source.url,
                backup_urls=source.backup_urls,
            )
            session.tracks[kind] = refreshed
            return refreshed

    def _validated_delivery(
        self,
        track: PreviewTrack,
        response: httpx.Response,
        client: httpx.AsyncClient,
        requested_range: RequestedRange | None,
        *,
        head: bool,
    ) -> PreviewMediaDelivery:
        expected_statuses = {status.HTTP_200_OK, status.HTTP_206_PARTIAL_CONTENT}
        if response.status_code not in expected_statuses:
            raise self._upstream_error(response.status_code)
        if requested_range is not None and response.status_code != status.HTTP_206_PARTIAL_CONTENT:
            raise self._upstream_error(response.status_code)
        encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
        if encoding not in {"", "identity"}:
            raise self._upstream_error(response.status_code)
        content_type = response.headers.get("Content-Type")
        if content_type is not None:
            media_type = content_type.partition(";")[0].strip().lower()
            allowed_media_types = {
                track.record.mime_type.lower(),
                "application/octet-stream",
                "binary/octet-stream",
            }
            expected_major, separator, expected_subtype = track.record.mime_type.partition("/")
            if separator and expected_major in {"audio", "video"}:
                alternate_major = "video" if expected_major == "audio" else "audio"
                allowed_media_types.add(f"{alternate_major}/{expected_subtype}".lower())
            if media_type not in allowed_media_types:
                raise self._upstream_error(response.status_code)

        content_range: tuple[int, int, int | None] | None = None
        if response.status_code == status.HTTP_206_PARTIAL_CONTENT:
            content_range = self._parse_content_range(response.headers.get("Content-Range"))
            if content_range is None:
                raise self._upstream_error(response.status_code)
            self._validate_content_range(requested_range, content_range)

        declared_length = self._content_length(response.headers.get("Content-Length"))
        if content_range is not None:
            range_length = content_range[1] - content_range[0] + 1
            if declared_length is not None and declared_length != range_length:
                raise self._upstream_error(response.status_code)
            declared_length = range_length
        if not head and declared_length is not None and declared_length > self.maximum_range_bytes:
            raise PreviewRangeNotSatisfiable(content_range[2] if content_range else None)

        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        }
        if declared_length is not None:
            headers["Content-Length"] = str(declared_length)
        if content_range is not None:
            start, end, total = content_range
            headers["Content-Range"] = f"bytes {start}-{end}/{total if total is not None else '*'}"
        for name in ("ETag", "Last-Modified"):
            value = response.headers.get(name)
            if value and "\r" not in value and "\n" not in value and len(value) <= 512:
                headers[name] = value
        return PreviewMediaDelivery(
            status_code=response.status_code,
            media_type=track.record.mime_type,
            headers=headers,
            response=response,
            client=client,
            maximum_bytes=self.maximum_range_bytes,
            declared_length=0 if head else declared_length,
        )

    def _build_mpd(self, session: PreviewSession) -> bytes:
        ET.register_namespace("", _DASH_NAMESPACE)
        root = ET.Element(
            f"{{{_DASH_NAMESPACE}}}MPD",
            {
                "type": "static",
                "profiles": "urn:mpeg:dash:profile:isoff-on-demand:2011",
                "minBufferTime": "PT1.5S",
                "mediaPresentationDuration": f"PT{session.duration}S",
            },
        )
        period = ET.SubElement(root, f"{{{_DASH_NAMESPACE}}}Period", {"start": "PT0S"})
        for kind in (StreamKind.VIDEO, StreamKind.AUDIO):
            track = session.tracks.get(kind)
            if track is None:
                continue
            record = track.record
            adaptation = ET.SubElement(
                period,
                f"{{{_DASH_NAMESPACE}}}AdaptationSet",
                {
                    "id": kind.value,
                    "contentType": kind.value,
                    "mimeType": record.mime_type,
                    "segmentAlignment": "true",
                    "startWithSAP": "1",
                },
            )
            attributes = {
                "id": record.id,
                "bandwidth": str(max(1, record.bitrate or 1)),
                "codecs": record.codec_string,
            }
            if kind == StreamKind.VIDEO:
                if record.width is not None:
                    attributes["width"] = str(record.width)
                if record.height is not None:
                    attributes["height"] = str(record.height)
                if record.fps is not None:
                    attributes["frameRate"] = self._number_text(record.fps)
            elif record.sample_rate is not None:
                attributes["audioSamplingRate"] = str(record.sample_rate)
            representation = ET.SubElement(
                adaptation,
                f"{{{_DASH_NAMESPACE}}}Representation",
                attributes,
            )
            base_url = ET.SubElement(representation, f"{{{_DASH_NAMESPACE}}}BaseURL")
            base_url.text = f"media/{kind.value}"
            segment_base = ET.SubElement(
                representation,
                f"{{{_DASH_NAMESPACE}}}SegmentBase",
                {"indexRange": f"{record.index_range_start}-{record.index_range_end}"},
            )
            ET.SubElement(
                segment_base,
                f"{{{_DASH_NAMESPACE}}}Initialization",
                {"range": f"{record.init_range_start}-{record.init_range_end}"},
            )
        return cast(bytes, ET.tostring(root, encoding="utf-8", xml_declaration=True))

    async def _load_stream_records(
        self,
        stream_ids: Sequence[str],
    ) -> list[PreviewStreamRecord]:
        if self.session_factory is None:
            raise RuntimeError("Preview stream persistence is not configured")
        async with self.session_factory() as session:
            streams = list(
                (
                    await session.scalars(
                        select(MediaStream)
                        .where(MediaStream.id.in_(stream_ids))
                        .options(selectinload(MediaStream.part))
                    )
                ).all()
            )
        return [self._record_from_model(stream) for stream in streams]

    @classmethod
    def _record_from_model(cls, stream: MediaStream) -> PreviewStreamRecord:
        return PreviewStreamRecord(
            id=stream.id,
            part_id=stream.part_id,
            part_duration=stream.part.duration,
            kind=stream.kind,
            access_context=stream.access_context,
            mime_type=cls._required_text(stream, "mime_type"),
            codec_string=cls._required_text(stream, "codec_string"),
            bitrate=stream.bitrate,
            width=stream.width,
            height=stream.height,
            fps=stream.fps,
            sample_rate=stream.sample_rate,
            init_range_start=cls._required_integer(stream, "init_range_start"),
            init_range_end=cls._required_integer(stream, "init_range_end"),
            index_range_start=cls._required_integer(stream, "index_range_start"),
            index_range_end=cls._required_integer(stream, "index_range_end"),
        )

    @staticmethod
    def _required_text(stream: MediaStream, name: str) -> str:
        value = getattr(stream, name, None)
        if not isinstance(value, str) or not value:
            raise PreviewService._preview_unavailable()
        return value

    @staticmethod
    def _required_integer(stream: MediaStream, name: str) -> int:
        value = getattr(stream, name, None)
        if not isinstance(value, int) or isinstance(value, bool):
            raise PreviewService._preview_unavailable()
        return value

    def _validate_track(
        self,
        record: PreviewStreamRecord,
        expected_kind: StreamKind,
        access_mode: AccessMode,
    ) -> None:
        if record.kind != expected_kind:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "预览媒体轨类型不正确",
                action="重新选择视频和音频规格",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        expected_context = (
            AccessContext.AUTHENTICATED
            if access_mode == AccessMode.AUTHENTICATED
            else AccessContext.ANONYMOUS
        )
        if record.access_context != expected_context:
            raise AppError(
                ErrorCode.PERMISSION_DENIED,
                "预览身份策略与所选媒体流不一致",
                action="按当前身份重新解析并选择媒体规格",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if not _MIME_TYPE.fullmatch(record.mime_type) or not record.mime_type.startswith(
            f"{record.kind.value}/"
        ):
            raise self._preview_unavailable()
        if not _CODEC_STRING.fullmatch(record.codec_string):
            raise self._preview_unavailable()
        for start, end in (
            (record.init_range_start, record.init_range_end),
            (record.index_range_start, record.index_range_end),
        ):
            if start < 0 or end < start or end > self.maximum_range_bytes:
                raise self._preview_unavailable()

    def _session_read(self, session: PreviewSession) -> PreviewRead:
        video = session.tracks[StreamKind.VIDEO].record
        audio_track = session.tracks.get(StreamKind.AUDIO)
        return PreviewRead(
            id=session.id,
            manifest_url=f"/api/v1/previews/{session.id}/manifest.mpd",
            expires_at=self._expires_at(session),
            duration=session.duration,
            video=self._track_read(video),
            audio=self._track_read(audio_track.record) if audio_track is not None else None,
        )

    @staticmethod
    def _track_read(record: PreviewStreamRecord) -> PreviewTrackRead:
        return PreviewTrackRead(
            stream_id=record.id,
            mime_type=record.mime_type,
            codec_string=record.codec_string,
        )

    @staticmethod
    def _representation_key(record: PreviewStreamRecord) -> tuple[object, ...]:
        return (
            record.part_id,
            record.kind,
            record.mime_type,
            record.codec_string,
            record.init_range_start,
            record.init_range_end,
            record.index_range_start,
            record.index_range_end,
        )

    def _parse_range(self, value: str | None, *, required: bool) -> RequestedRange | None:
        if value is None:
            if required:
                raise PreviewRangeNotSatisfiable()
            return None
        if len(value) > 128 or "," in value:
            raise PreviewRangeNotSatisfiable()
        match = _BYTE_RANGE.fullmatch(value.strip())
        if match is None:
            raise PreviewRangeNotSatisfiable()
        if match.group(3) is not None:
            suffix = int(match.group(3))
            if suffix <= 0 or suffix > self.maximum_range_bytes:
                raise PreviewRangeNotSatisfiable()
            return RequestedRange(value.strip(), None, None, suffix)
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        if end is not None and (end < start or end - start + 1 > self.maximum_range_bytes):
            raise PreviewRangeNotSatisfiable()
        return RequestedRange(value.strip(), start, end, None)

    def _validate_content_range(
        self,
        requested: RequestedRange | None,
        actual: tuple[int, int, int | None],
    ) -> None:
        if requested is None:
            return
        start, end, total = actual
        length = end - start + 1
        if length > self.maximum_range_bytes:
            raise PreviewRangeNotSatisfiable(total)
        if requested.start is not None:
            if start != requested.start:
                raise self._upstream_error()
            if requested.end is not None and end > requested.end:
                raise self._upstream_error()
        elif requested.suffix_length is not None:
            if total is None or end != total - 1 or length > requested.suffix_length:
                raise self._upstream_error()

    @staticmethod
    def _parse_content_range(value: str | None) -> tuple[int, int, int | None] | None:
        if value is None:
            return None
        match = _CONTENT_RANGE.fullmatch(value.strip())
        if match is None:
            return None
        start = int(match.group(1))
        end = int(match.group(2))
        total = None if match.group(3) == "*" else int(match.group(3))
        if end < start or (total is not None and (total <= end or total <= 0)):
            return None
        return start, end, total

    @staticmethod
    def _unsatisfied_total(value: str | None) -> int | None:
        if value is None:
            return None
        match = _UNSATISFIED_RANGE.fullmatch(value.strip())
        return int(match.group(1)) if match is not None else None

    @staticmethod
    def _content_length(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except ValueError:
            raise PreviewService._upstream_error() from None
        if parsed < 0:
            raise PreviewService._upstream_error()
        return parsed

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(
                self.settings.upstream_timeout_seconds,
                connect=self.settings.upstream_connect_timeout_seconds,
            ),
            follow_redirects=False,
            trust_env=False,
        )

    def _request_headers(self, host: str, range_header: str | None) -> dict[str, str]:
        headers = {
            "Host": host,
            "User-Agent": self.settings.user_agent,
            "Referer": "https://www.bilibili.com/",
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        }
        if range_header is not None:
            headers["Range"] = range_header
        return headers

    @staticmethod
    def _unique_sources(primary: str, backups: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys((primary, *backups)))

    @staticmethod
    def _number_text(value: float) -> str:
        return str(int(value)) if value.is_integer() else f"{value:.3f}".rstrip("0").rstrip(".")

    def _now(self) -> datetime:
        value = self._clock()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _not_found(message: str = "预览媒体流不存在") -> AppError:
        return AppError(
            ErrorCode.RESOURCE_NOT_FOUND,
            message,
            action="重新解析并选择媒体规格",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    @staticmethod
    def _preview_unavailable() -> AppError:
        return AppError(
            ErrorCode.UNSUPPORTED_CONTENT,
            "所选媒体流缺少浏览器预览所需的 DASH 索引信息",
            action="选择其他规格，或直接创建下载任务",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    @staticmethod
    def _upstream_error(upstream_status: int | None = None) -> AppError:
        return AppError(
            ErrorCode.UPSTREAM_CHANGED,
            "预览媒体服务器返回了无法安全处理的响应",
            action="关闭预览后重新解析该视频",
            status_code=status.HTTP_502_BAD_GATEWAY,
            log_context={"upstream_status": upstream_status} if upstream_status else {},
        )
