from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from fastapi import FastAPI

from app.api.previews import router as previews_router
from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode, install_exception_handlers
from app.db.models import AccessContext, MediaStream, StreamKind
from app.schemas.video import AccessMode
from app.services.previews import (
    PreviewRangeNotSatisfiable,
    PreviewService,
    PreviewStreamInterrupted,
    PreviewStreamRecord,
    ResolvedPreviewSource,
)


async def public_resolver(_host: str, _port: int) -> Iterable[str]:
    return ("93.184.216.34",)


@dataclass(frozen=True, slots=True)
class Source:
    url: str
    backup_urls: tuple[str, ...] = ()


class SourceResolver:
    def __init__(self, sources: dict[str, list[Source]]) -> None:
        self.sources = sources
        self.calls: Counter[str] = Counter()

    async def resolve_stream(
        self,
        stream_id: str,
        access_mode: AccessMode,
        *,
        verify: bool = True,
        force_refresh: bool = False,
    ) -> ResolvedPreviewSource:
        del access_mode, verify, force_refresh
        values = self.sources[stream_id]
        index = min(self.calls[stream_id], len(values) - 1)
        self.calls[stream_id] += 1
        return values[index]


class MetadataRefreshingResolver(SourceResolver):
    def __init__(
        self,
        sources: dict[str, list[Source]],
        refreshed_records: dict[str, PreviewStreamRecord],
    ) -> None:
        super().__init__(sources)
        self.refreshed_records = refreshed_records
        self.records: dict[str, PreviewStreamRecord] | None = None

    async def resolve_stream(
        self,
        stream_id: str,
        access_mode: AccessMode,
        *,
        verify: bool = True,
        force_refresh: bool = False,
    ) -> ResolvedPreviewSource:
        source = await super().resolve_stream(
            stream_id,
            access_mode,
            verify=verify,
            force_refresh=force_refresh,
        )
        if self.records is None:
            raise RuntimeError("fixture records are not connected")
        self.records[stream_id] = self.refreshed_records[stream_id]
        return source


class FailingTrackResolver(SourceResolver):
    def __init__(self, sources: dict[str, list[Source]]) -> None:
        super().__init__(sources)
        self.video_started = asyncio.Event()
        self.video_cancelled = asyncio.Event()

    async def resolve_stream(
        self,
        stream_id: str,
        access_mode: AccessMode,
        *,
        verify: bool = True,
        force_refresh: bool = False,
    ) -> ResolvedPreviewSource:
        del access_mode, verify, force_refresh
        if stream_id == "audio-stream":
            await self.video_started.wait()
            raise RuntimeError("fixed audio resolution failure")
        self.video_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.video_cancelled.set()
            raise
        raise AssertionError("unreachable")


@dataclass(slots=True)
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class FixturePreviewService(PreviewService):
    def __init__(
        self,
        settings: Settings,
        resolver: SourceResolver,
        records: Iterable[PreviewStreamRecord],
        **kwargs: object,
    ) -> None:
        self.records = {item.id: item for item in records}
        super().__init__(
            settings,
            None,
            resolver,
            media_resolver=public_resolver,
            **kwargs,  # type: ignore[arg-type]
        )

    async def _load_stream_records(
        self,
        stream_ids: Sequence[str],
    ) -> list[PreviewStreamRecord]:
        return [self.records[item] for item in stream_ids if item in self.records]


def video_record(
    identifier: str = "video-stream",
    *,
    context: AccessContext = AccessContext.ANONYMOUS,
    part_id: str = "part-1",
) -> PreviewStreamRecord:
    return PreviewStreamRecord(
        id=identifier,
        part_id=part_id,
        part_duration=125,
        kind=StreamKind.VIDEO,
        access_context=context,
        mime_type="video/mp4",
        codec_string="avc1.640028",
        bitrate=2_500_000,
        width=1920,
        height=1080,
        fps=25.0,
        sample_rate=None,
        init_range_start=0,
        init_range_end=999,
        index_range_start=1_000,
        index_range_end=1_999,
    )


def audio_record(
    identifier: str = "audio-stream",
    *,
    context: AccessContext = AccessContext.ANONYMOUS,
    part_id: str = "part-1",
) -> PreviewStreamRecord:
    return PreviewStreamRecord(
        id=identifier,
        part_id=part_id,
        part_duration=125,
        kind=StreamKind.AUDIO,
        access_context=context,
        mime_type="audio/mp4",
        codec_string="mp4a.40.2",
        bitrate=192_000,
        width=None,
        height=None,
        fps=None,
        sample_rate=48_000,
        init_range_start=0,
        init_range_end=599,
        index_range_start=600,
        index_range_end=999,
    )


def sources(*, second_video: str | None = None) -> dict[str, list[Source]]:
    video_sources = [Source("https://cdn.bilivideo.com/video.m4s?token=redacted")]
    if second_video is not None:
        video_sources.append(Source(second_video))
    return {
        "video-stream": video_sources,
        "audio-stream": [Source("https://cdn.bilivideo.com/audio.m4s?token=redacted")],
    }


def service(
    settings: Settings,
    handler: httpx.MockTransport | None = None,
    *,
    records: Iterable[PreviewStreamRecord] | None = None,
    source_map: dict[str, list[Source]] | None = None,
    clock: MutableClock | None = None,
    **kwargs: object,
) -> tuple[FixturePreviewService, SourceResolver]:
    resolver = SourceResolver(source_map or sources())
    preview = FixturePreviewService(
        settings,
        resolver,
        records if records is not None else (video_record(), audio_record()),
        transport=handler,
        clock=clock,
        **kwargs,
    )
    return preview, resolver


async def create_session(preview: PreviewService, mode: AccessMode = AccessMode.ANONYMOUS) -> str:
    result = await preview.create("video-stream", "audio-stream", mode)
    return result.id


async def collect(delivery: object) -> bytes:
    stream = cast(AsyncIterator[bytes], delivery)
    return b"".join([chunk async for chunk in stream])


async def test_create_builds_internal_segment_base_manifest(settings: Settings) -> None:
    preview, resolver = service(settings)
    result = await preview.create("video-stream", "audio-stream", AccessMode.ANONYMOUS)
    manifest = (await preview.manifest(result.id)).decode()

    assert result.manifest_url == f"/api/v1/previews/{result.id}/manifest.mpd"
    assert result.video.codec_string == "avc1.640028"
    assert result.audio is not None and result.audio.codec_string == "mp4a.40.2"
    assert 'mediaPresentationDuration="PT125S"' in manifest
    assert 'indexRange="1000-1999"' in manifest
    assert 'range="0-999"' in manifest
    assert "media/video" in manifest and "media/audio" in manifest
    assert "bilivideo.com" not in manifest
    assert "token=redacted" not in manifest
    assert resolver.calls == Counter({"video-stream": 1, "audio-stream": 1})


async def test_create_uses_metadata_refreshed_by_both_resolved_tracks(
    settings: Settings,
) -> None:
    refreshed_video = replace(
        video_record(),
        codec_string="hev1.1.6.L120.90",
        init_range_end=1_499,
        index_range_start=1_500,
        index_range_end=2_499,
    )
    refreshed_audio = replace(
        audio_record(),
        codec_string="mp4a.40.5",
        init_range_end=799,
        index_range_start=800,
        index_range_end=1_199,
    )
    resolver = MetadataRefreshingResolver(
        sources(),
        {
            refreshed_video.id: refreshed_video,
            refreshed_audio.id: refreshed_audio,
        },
    )
    preview = FixturePreviewService(
        settings,
        resolver,
        (video_record(), audio_record()),
    )
    resolver.records = preview.records

    result = await preview.create(
        "video-stream",
        "audio-stream",
        AccessMode.ANONYMOUS,
    )
    manifest = (await preview.manifest(result.id)).decode()

    assert result.video.codec_string == refreshed_video.codec_string
    assert result.audio is not None
    assert result.audio.codec_string == refreshed_audio.codec_string
    assert 'indexRange="1500-2499"' in manifest
    assert 'range="0-1499"' in manifest
    assert 'indexRange="800-1199"' in manifest
    assert 'range="0-799"' in manifest


async def test_create_cancels_other_resolution_when_one_track_fails(
    settings: Settings,
) -> None:
    resolver = FailingTrackResolver(sources())
    preview = FixturePreviewService(
        settings,
        resolver,
        (video_record(), audio_record()),
    )

    with pytest.raises(RuntimeError, match="fixed audio resolution failure"):
        await asyncio.wait_for(
            preview.create("video-stream", "audio-stream", AccessMode.ANONYMOUS),
            timeout=1,
        )

    assert resolver.video_cancelled.is_set()
    assert not preview._sessions


async def test_video_only_manifest_omits_audio(settings: Settings) -> None:
    preview, _ = service(settings)
    result = await preview.create("video-stream", None, AccessMode.ANONYMOUS)
    manifest = (await preview.manifest(result.id)).decode()
    assert result.audio is None
    assert 'contentType="audio"' not in manifest


async def test_create_validates_tracks_part_and_access_context(settings: Settings) -> None:
    mismatched_audio = audio_record(part_id="part-2")
    preview, _ = service(settings, records=(video_record(), mismatched_audio))
    with pytest.raises(AppError) as part_error:
        await preview.create("video-stream", "audio-stream", AccessMode.ANONYMOUS)
    assert part_error.value.code == ErrorCode.VALIDATION_ERROR

    authenticated = video_record(context=AccessContext.AUTHENTICATED)
    preview, _ = service(settings, records=(authenticated, audio_record()))
    with pytest.raises(AppError) as access_error:
        await preview.create("video-stream", None, AccessMode.ANONYMOUS)
    assert access_error.value.code == ErrorCode.PERMISSION_DENIED


async def test_create_rejects_missing_or_invalid_dash_metadata(settings: Settings) -> None:
    invalid = video_record()
    invalid = replace(invalid, codec_string="invalid codec")
    preview, _ = service(settings, records=(invalid,))
    with pytest.raises(AppError) as codec_error:
        await preview.create("video-stream", None, AccessMode.ANONYMOUS)
    assert codec_error.value.code == ErrorCode.UNSUPPORTED_CONTENT

    preview, _ = service(settings, records=())
    with pytest.raises(AppError) as missing_error:
        await preview.create("video-stream", None, AccessMode.ANONYMOUS)
    assert missing_error.value.code == ErrorCode.RESOURCE_NOT_FOUND


async def test_range_proxy_pins_address_and_filters_headers(settings: Settings) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            206,
            headers={
                "Content-Range": "bytes 0-3/1000",
                "Content-Length": "4",
                "Content-Encoding": "identity",
                "ETag": '"fixture"',
                "Set-Cookie": "secret=value",
                "Location": "https://private.example/",
                "Server": "private-server",
            },
            stream=httpx.ByteStream(b"data"),
            request=request,
        )

    preview, _ = service(settings, httpx.MockTransport(handle))
    preview_id = await create_session(preview)
    delivery = await preview.media(preview_id, StreamKind.VIDEO, "bytes=0-3")
    body = await collect(delivery.stream())

    assert body == b"data"
    assert delivery.status_code == 206
    assert delivery.headers["Content-Range"] == "bytes 0-3/1000"
    assert delivery.headers["Cache-Control"] == "private, no-store"
    assert "Set-Cookie" not in delivery.headers
    assert "Location" not in delivery.headers
    assert "Server" not in delivery.headers
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["Host"] == "cdn.bilivideo.com"
    assert requests[0].headers["Referer"] == "https://www.bilibili.com/"
    assert requests[0].headers["Accept-Encoding"] == "identity"
    assert "Cookie" not in requests[0].headers
    assert "Authorization" not in requests[0].headers
    assert "Origin" not in requests[0].headers


async def test_audio_proxy_accepts_bilibili_video_mp4_content_type(
    settings: Settings,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            206,
            headers={
                "Content-Range": "bytes 0-3/1000",
                "Content-Length": "4",
                "Content-Type": "video/mp4",
            },
            stream=httpx.ByteStream(b"data"),
            request=request,
        )

    preview, _ = service(settings, httpx.MockTransport(handle))
    preview_id = await create_session(preview)
    delivery = await preview.media(preview_id, StreamKind.AUDIO, "bytes=0-3")
    assert delivery.media_type == "audio/mp4"
    assert await collect(delivery.stream()) == b"data"


@pytest.mark.parametrize(
    "range_header",
    [None, "bytes=0-1,4-5", "items=0-3", "bytes=4-3", "bytes=-0"],
)
async def test_invalid_or_missing_get_range_is_rejected(
    settings: Settings,
    range_header: str | None,
) -> None:
    preview, _ = service(settings)
    preview_id = await create_session(preview)
    with pytest.raises(PreviewRangeNotSatisfiable):
        await preview.media(preview_id, StreamKind.VIDEO, range_header)


async def test_upstream_416_returns_known_total(settings: Settings) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            416,
            headers={"Content-Range": "bytes */1000"},
            request=request,
        )

    preview, _ = service(settings, httpx.MockTransport(handle))
    preview_id = await create_session(preview)
    with pytest.raises(PreviewRangeNotSatisfiable) as error:
        await preview.media(preview_id, StreamKind.VIDEO, "bytes=1000-1001")
    assert error.value.total_size == 1000


@pytest.mark.parametrize(
    ("status_code", "headers"),
    [
        (200, {"Content-Length": "4"}),
        (206, {"Content-Range": "bytes 1-4/1000", "Content-Length": "4"}),
        (
            206,
            {
                "Content-Range": "bytes 0-3/1000",
                "Content-Length": "4",
                "Content-Encoding": "gzip",
            },
        ),
        (
            206,
            {
                "Content-Range": "bytes 0-3/1000",
                "Content-Length": "4",
                "Content-Type": "text/html",
            },
        ),
    ],
)
async def test_unsafe_upstream_range_responses_are_rejected(
    settings: Settings,
    status_code: int,
    headers: dict[str, str],
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers=headers,
            stream=httpx.ByteStream(b"data"),
            request=request,
        )

    preview, _ = service(settings, httpx.MockTransport(handle))
    preview_id = await create_session(preview)
    with pytest.raises(AppError) as error:
        await preview.media(preview_id, StreamKind.VIDEO, "bytes=0-3")
    assert error.value.code == ErrorCode.UPSTREAM_CHANGED


async def test_expired_source_is_refreshed_once(settings: Settings) -> None:
    requests: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        host_path = request.url.path
        requests.append(host_path)
        if host_path == "/expired.m4s":
            return httpx.Response(403, request=request)
        return httpx.Response(
            206,
            headers={"Content-Range": "bytes 0-3/1000", "Content-Length": "4"},
            stream=httpx.ByteStream(b"new!"),
            request=request,
        )

    source_map = sources(second_video="https://cdn.bilivideo.com/fresh.m4s?token=redacted")
    source_map["video-stream"][0] = Source("https://cdn.bilivideo.com/expired.m4s?token=redacted")
    preview, resolver = service(
        settings,
        httpx.MockTransport(handle),
        source_map=source_map,
    )
    preview_id = await create_session(preview)
    delivery = await preview.media(preview_id, StreamKind.VIDEO, "bytes=0-3")
    assert await collect(delivery.stream()) == b"new!"
    assert requests == ["/expired.m4s", "/fresh.m4s"]
    assert resolver.calls["video-stream"] == 2


async def test_backup_source_is_used_after_network_failure(settings: Settings) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/primary.m4s":
            raise httpx.ConnectError("fixed network failure", request=request)
        return httpx.Response(
            206,
            headers={"Content-Range": "bytes 0-3/1000", "Content-Length": "4"},
            stream=httpx.ByteStream(b"back"),
            request=request,
        )

    source_map = sources()
    source_map["video-stream"] = [
        Source(
            "https://cdn.bilivideo.com/primary.m4s",
            ("https://backup.bilivideo.com/backup.m4s",),
        )
    ]
    preview, _ = service(settings, httpx.MockTransport(handle), source_map=source_map)
    preview_id = await create_session(preview)
    delivery = await preview.media(preview_id, StreamKind.VIDEO, "bytes=0-3")
    assert await collect(delivery.stream()) == b"back"


async def test_head_can_probe_without_range(settings: Settings) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        assert "Range" not in request.headers
        return httpx.Response(
            200,
            headers={"Content-Length": "1000", "Accept-Ranges": "bytes"},
            request=request,
        )

    preview, _ = service(settings, httpx.MockTransport(handle))
    preview_id = await create_session(preview)
    delivery = await preview.media(
        preview_id,
        StreamKind.VIDEO,
        None,
        head=True,
    )
    assert delivery.status_code == 200
    assert delivery.headers["Content-Length"] == "1000"
    await delivery.close()


async def test_head_ignores_client_range_header(settings: Settings) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        assert "Range" not in request.headers
        return httpx.Response(
            200,
            headers={"Content-Length": "1000", "Accept-Ranges": "bytes"},
            request=request,
        )

    preview, _ = service(settings, httpx.MockTransport(handle))
    preview_id = await create_session(preview)
    delivery = await preview.media(
        preview_id,
        StreamKind.VIDEO,
        "bytes=0-3",
        head=True,
    )
    assert delivery.status_code == 200
    assert "Content-Range" not in delivery.headers
    await delivery.close()


async def test_concurrency_slot_is_held_until_delivery_closes(settings: Settings) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            206,
            headers={"Content-Range": "bytes 0-3/1000", "Content-Length": "4"},
            stream=httpx.ByteStream(b"data"),
            request=request,
        )

    preview, _ = service(settings, httpx.MockTransport(handle), concurrency=1)
    preview_id = await create_session(preview)
    first = await preview.media(preview_id, StreamKind.VIDEO, "bytes=0-3")
    waiting = asyncio.create_task(preview.media(preview_id, StreamKind.AUDIO, "bytes=0-3"))
    await asyncio.sleep(0)
    assert not waiting.done()
    await first.close()
    second = await asyncio.wait_for(waiting, timeout=1)
    await second.close()
    await second.close()


async def test_waiting_authenticated_request_is_rejected_after_auth_clear(
    settings: Settings,
) -> None:
    requests = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            206,
            headers={"Content-Range": "bytes 0-3/1000", "Content-Length": "4"},
            stream=httpx.ByteStream(b"data"),
            request=request,
        )

    records = (
        video_record(context=AccessContext.AUTHENTICATED),
        audio_record(context=AccessContext.AUTHENTICATED),
    )
    preview, _ = service(
        settings,
        httpx.MockTransport(handle),
        records=records,
        concurrency=1,
    )
    preview_id = await create_session(preview, AccessMode.AUTHENTICATED)
    first = await preview.media(preview_id, StreamKind.VIDEO, "bytes=0-3")
    waiting = asyncio.create_task(preview.media(preview_id, StreamKind.AUDIO, "bytes=0-3"))
    await asyncio.sleep(0)
    assert not waiting.done()

    await preview.clear_authenticated_sessions()
    await first.close()
    with pytest.raises(AppError) as error:
        await asyncio.wait_for(waiting, timeout=1)
    assert error.value.code == ErrorCode.RESOURCE_NOT_FOUND
    assert requests == 1


async def test_truncated_body_raises_safe_stream_error(settings: Settings) -> None:
    class TruncatedStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"abc"

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            206,
            headers={"Content-Range": "bytes 0-3/1000", "Content-Length": "4"},
            stream=TruncatedStream(),
            request=request,
        )

    preview, _ = service(settings, httpx.MockTransport(handle))
    preview_id = await create_session(preview)
    delivery = await preview.media(preview_id, StreamKind.VIDEO, "bytes=0-3")
    with pytest.raises(PreviewStreamInterrupted):
        await collect(delivery.stream())


async def test_body_chunk_larger_than_declared_length_is_not_forwarded(
    settings: Settings,
) -> None:
    class OverlongStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"unexpected"

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            206,
            headers={"Content-Range": "bytes 0-3/1000", "Content-Length": "4"},
            stream=OverlongStream(),
            request=request,
        )

    preview, _ = service(settings, httpx.MockTransport(handle))
    preview_id = await create_session(preview)
    delivery = await preview.media(preview_id, StreamKind.VIDEO, "bytes=0-3")
    iterator = delivery.stream()
    with pytest.raises(PreviewStreamInterrupted):
        await anext(iterator)


async def test_ttl_cleanup_eviction_delete_and_auth_clear(settings: Settings) -> None:
    clock = MutableClock(datetime(2026, 7, 14, tzinfo=UTC))
    records = (
        video_record(),
        audio_record(),
        video_record("auth-video", context=AccessContext.AUTHENTICATED),
    )
    source_map = {
        **sources(),
        "auth-video": [Source("https://cdn.bilivideo.com/auth.m4s")],
    }
    preview, _ = service(
        settings,
        records=records,
        source_map=source_map,
        clock=clock,
        idle_ttl_seconds=30,
        maximum_lifetime_seconds=60,
        cleanup_interval_seconds=30,
        maximum_sessions=2,
    )
    first = await preview.create("video-stream", None, AccessMode.ANONYMOUS)
    clock.advance(1)
    authenticated = await preview.create("auth-video", None, AccessMode.AUTHENTICATED)
    clock.advance(1)
    newest = await preview.create("video-stream", None, AccessMode.ANONYMOUS)
    with pytest.raises(AppError):
        await preview.manifest(first.id)

    await preview.clear_authenticated_sessions()
    with pytest.raises(AppError):
        await preview.manifest(authenticated.id)
    assert await preview.delete(newest.id) is True
    assert await preview.delete(newest.id) is False

    expiring = await preview.create("video-stream", None, AccessMode.ANONYMOUS)
    clock.advance(31)
    assert await preview.cleanup_expired() == 1
    with pytest.raises(AppError):
        await preview.manifest(expiring.id)


async def test_cleanup_task_start_stop_is_idempotent(settings: Settings) -> None:
    preview, _ = service(
        settings,
        idle_ttl_seconds=30,
        maximum_lifetime_seconds=60,
        cleanup_interval_seconds=1,
    )
    await preview.start()
    await preview.start()
    await preview.stop()
    await preview.stop()


async def test_api_serves_manifest_range_and_delete(settings: Settings) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            206,
            headers={"Content-Range": "bytes 0-3/1000", "Content-Length": "4"},
            stream=httpx.ByteStream(b"api!"),
            request=request,
        )

    preview, _ = service(settings, httpx.MockTransport(handle))
    app = FastAPI()
    install_exception_handlers(app)
    app.state.container = SimpleNamespace(preview_service=preview)
    app.include_router(previews_router, prefix="/api/v1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        created = await client.post(
            "/api/v1/previews",
            json={
                "videoStreamId": "video-stream",
                "audioStreamId": "audio-stream",
                "accessMode": "anonymous",
            },
        )
        assert created.status_code == 201
        preview_id = created.json()["id"]
        manifest = await client.get(f"/api/v1/previews/{preview_id}/manifest.mpd")
        assert manifest.status_code == 200
        assert manifest.headers["Content-Type"].startswith("application/dash+xml")
        media = await client.get(
            f"/api/v1/previews/{preview_id}/media/video",
            headers={"Range": "bytes=0-3"},
        )
        assert media.status_code == 206
        assert media.content == b"api!"
        invalid = await client.get(f"/api/v1/previews/{preview_id}/media/video")
        assert invalid.status_code == 416
        deleted = await client.delete(f"/api/v1/previews/{preview_id}")
        assert deleted.status_code == 204


async def test_main_application_wires_parse_manifest_range_and_cleanup(
    api_client: tuple[httpx.AsyncClient, object],
) -> None:
    client, _ = api_client
    parsed = await client.post(
        "/api/v1/videos/parse",
        json={"url": "BV1FYT5zkE1q", "accessMode": "anonymous"},
    )
    assert parsed.status_code == 200
    streams = parsed.json()["streams"]
    video = streams["video"][0]
    audio = streams["audio"][0]
    assert video["previewSupported"] is True
    assert audio["previewSupported"] is True

    created = await client.post(
        "/api/v1/previews",
        json={
            "videoStreamId": video["id"],
            "audioStreamId": audio["id"],
            "accessMode": "anonymous",
        },
    )
    assert created.status_code == 201, created.text
    preview_id = created.json()["id"]
    manifest = await client.get(created.json()["manifestUrl"])
    assert manifest.status_code == 200
    assert "media/video" in manifest.text and "media/audio" in manifest.text
    assert "bilivideo.com" not in manifest.text
    assert "token=" not in manifest.text

    media = await client.get(
        f"/api/v1/previews/{preview_id}/media/video",
        headers={"Range": "bytes=0-1023"},
    )
    assert media.status_code == 206
    assert len(media.content) == 1_024
    assert "set-cookie" not in media.headers

    deleted = await client.delete(f"/api/v1/previews/{preview_id}")
    assert deleted.status_code == 204
    missing = await client.get(f"/api/v1/previews/{preview_id}/manifest.mpd")
    assert missing.status_code == 404


def test_model_adapter_reads_future_persisted_fields() -> None:
    model = SimpleNamespace(
        id="stream",
        part_id="part",
        part=SimpleNamespace(duration=10),
        kind=StreamKind.VIDEO,
        access_context=AccessContext.ANONYMOUS,
        mime_type="video/mp4",
        codec_string="avc1.4D401F",
        bitrate=1,
        width=1,
        height=1,
        fps=25.0,
        sample_rate=None,
        init_range_start=0,
        init_range_end=1,
        index_range_start=2,
        index_range_end=3,
    )
    record = PreviewService._record_from_model(cast(MediaStream, model))
    assert record.codec_string == "avc1.4D401F"

    del model.codec_string
    with pytest.raises(AppError):
        PreviewService._record_from_model(cast(MediaStream, model))
