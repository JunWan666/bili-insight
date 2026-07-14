from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from pathlib import Path

import httpx
import pytest

from app.media.download import (
    DownloadCanceled,
    DownloadPaused,
    DownloadProgress,
    HTTPMediaDownloader,
    MediaDownloadError,
    _ResumeMetadata,
    iter_file,
)
from app.media.security import MediaURLValidator


class Checkpoint:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    async def checkpoint(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


class BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"a" * 16_384
        raise httpx.RemoteProtocolError("fixed truncated response")


async def resolver(_host: str, _port: int) -> Iterable[str]:
    return ("93.184.216.34",)


def downloader(
    handler: httpx.MockTransport | object,
    *,
    maximum_size_bytes: int = 1024 * 1024,
    custom_resolver: object = resolver,
) -> HTTPMediaDownloader:
    validator = MediaURLValidator(("bilivideo.com",), resolver=custom_resolver)  # type: ignore[arg-type]
    return HTTPMediaDownloader(
        validator,
        user_agent="test-agent",
        chunk_size=16_384,
        maximum_size_bytes=maximum_size_bytes,
        transport=handler,  # type: ignore[arg-type]
    )


async def collect_progress(values: list[DownloadProgress], value: DownloadProgress) -> None:
    values.append(value)


async def test_probe_and_full_download_use_pinned_ip_and_host_header(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    body = b"media-content"

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Range"):
            return httpx.Response(
                206,
                headers={
                    "Content-Range": f"bytes 0-{len(body) - 1}/{len(body)}",
                    "ETag": '"v1"',
                },
                stream=httpx.ByteStream(body),
                request=request,
            )
        return httpx.Response(
            200,
            headers={"ETag": '"v1"', "Content-Length": str(len(body))},
            stream=httpx.ByteStream(body),
            request=request,
        )

    media = downloader(httpx.MockTransport(handle))
    assert await media.probe("https://cdn.bilivideo.com/media.m4s") == len(body)
    destination = tmp_path / "track.part"
    updates: list[DownloadProgress] = []
    result = await media.download(
        "https://cdn.bilivideo.com/media.m4s",
        destination,
        checkpoint=Checkpoint(),
        progress=lambda value: collect_progress(updates, value),
    )

    assert result.size == len(body)
    assert result.resumed_from == 0
    assert destination.read_bytes() == body
    assert updates[-1] == DownloadProgress(len(body), len(body))
    assert all(request.url.host == "93.184.216.34" for request in requests)
    assert all(request.headers["Host"] == "cdn.bilivideo.com" for request in requests)
    assert all(request.headers["Accept-Encoding"] == "identity" for request in requests)


async def test_download_resumes_with_strong_validator(tmp_path: Path) -> None:
    destination = tmp_path / "track.part"
    destination.write_bytes(b"abc")
    metadata = destination.with_name(f"{destination.name}.resume.json")
    HTTPMediaDownloader._write_metadata(metadata, _ResumeMetadata('"v1"', None, 6))

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["Range"] == "bytes=3-"
        assert request.headers["If-Range"] == '"v1"'
        return httpx.Response(
            206,
            headers={"Content-Range": "bytes 3-5/6", "ETag": '"v1"'},
            content=b"def",
            request=request,
        )

    media = downloader(httpx.MockTransport(handle))
    result = await media.download(
        "https://cdn.bilivideo.com/a",
        destination,
        checkpoint=Checkpoint(),
        progress=lambda _value: collect_progress([], _value),
    )
    assert result.resumed_from == 3
    assert destination.read_bytes() == b"abcdef"
    assert not metadata.exists()


async def test_weak_validator_discards_partial_and_server_200_restarts(tmp_path: Path) -> None:
    destination = tmp_path / "track.part"
    destination.write_bytes(b"old")
    metadata = destination.with_name(f"{destination.name}.resume.json")
    HTTPMediaDownloader._write_metadata(metadata, _ResumeMetadata('W/"weak"', None, 3))

    def handle(request: httpx.Request) -> httpx.Response:
        assert "Range" not in request.headers
        return httpx.Response(200, content=b"new-content", request=request)

    media = downloader(httpx.MockTransport(handle))
    result = await media.download(
        "https://cdn.bilivideo.com/a",
        destination,
        checkpoint=Checkpoint(),
        progress=lambda _value: collect_progress([], _value),
    )
    assert result.resumed_from == 0
    assert destination.read_bytes() == b"new-content"


async def test_server_ignoring_range_restarts_without_claiming_resume(tmp_path: Path) -> None:
    destination = tmp_path / "track.part"
    destination.write_bytes(b"old")
    metadata = destination.with_name(f"{destination.name}.resume.json")
    HTTPMediaDownloader._write_metadata(metadata, _ResumeMetadata('"v1"', None, 10))

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["Range"] == "bytes=3-"
        return httpx.Response(200, content=b"replacement", request=request)

    result = await downloader(httpx.MockTransport(handle)).download(
        "https://cdn.bilivideo.com/a",
        destination,
        checkpoint=Checkpoint(),
        progress=lambda _value: collect_progress([], _value),
    )
    assert result.resumed_from == 0
    assert destination.read_bytes() == b"replacement"


async def test_416_requires_matching_total_and_validator(tmp_path: Path) -> None:
    destination = tmp_path / "track.part"
    destination.write_bytes(b"complete")
    metadata = destination.with_name(f"{destination.name}.resume.json")
    HTTPMediaDownloader._write_metadata(metadata, _ResumeMetadata('"v1"', None, 8))

    def matching(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            416,
            headers={"Content-Range": "bytes */8", "ETag": '"v1"'},
            request=request,
        )

    result = await downloader(httpx.MockTransport(matching)).download(
        "https://cdn.bilivideo.com/a",
        destination,
        checkpoint=Checkpoint(),
        progress=lambda _value: collect_progress([], _value),
    )
    assert result.size == 8

    destination.write_bytes(b"complete")
    HTTPMediaDownloader._write_metadata(metadata, _ResumeMetadata('"v1"', None, 8))

    def changed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            416,
            headers={"Content-Range": "bytes */8", "ETag": '"v2"'},
            request=request,
        )

    with pytest.raises(MediaDownloadError, match="断点") as caught:
        await downloader(httpx.MockTransport(changed)).download(
            "https://cdn.bilivideo.com/a",
            destination,
            checkpoint=Checkpoint(),
            progress=lambda _value: collect_progress([], _value),
        )
    assert caught.value.code == "RANGE_MISMATCH"
    assert caught.value.retryable is True
    assert not destination.exists()


@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (302, "UNSAFE_REDIRECT", False),
        (403, "MEDIA_URL_EXPIRED", True),
        (410, "MEDIA_URL_EXPIRED", True),
        (429, "UPSTREAM_TEMPORARY_ERROR", True),
        (503, "UPSTREAM_TEMPORARY_ERROR", True),
        (400, "UPSTREAM_DOWNLOAD_REJECTED", False),
    ],
)
def test_status_classification(status_code: int, expected_code: str, retryable: bool) -> None:
    response = httpx.Response(status_code, request=httpx.Request("GET", "https://example.com"))
    with pytest.raises(MediaDownloadError) as caught:
        HTTPMediaDownloader._raise_for_status(response)
    assert caught.value.code == expected_code
    assert caught.value.retryable is retryable


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Range": "broken"},
        {"Content-Range": "bytes 5-4/10"},
        {"Content-Range": "bytes 0-10/10"},
        {"Content-Range": "bytes 0-4/10", "Content-Length": "4"},
        {"Content-Range": f"bytes {'9' * 30}-{'9' * 30}/{'9' * 30}"},
    ],
)
def test_invalid_content_ranges_are_rejected(headers: dict[str, str]) -> None:
    response = httpx.Response(
        206,
        headers=headers,
        request=httpx.Request("GET", "https://example.com"),
    )
    with pytest.raises(MediaDownloadError) as caught:
        HTTPMediaDownloader._response_total(response, offset=0)
    assert caught.value.code == "UPSTREAM_PROTOCOL_ERROR"


async def test_size_encoding_empty_and_incomplete_guards(tmp_path: Path) -> None:
    cases = [
        httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=httpx.ByteStream(b"data"),
        ),
        httpx.Response(200, stream=httpx.ByteStream(b"")),
        httpx.Response(200, headers={"Content-Length": "999999"}, stream=BrokenStream()),
    ]
    for index, response in enumerate(cases):

        def handle(request: httpx.Request, value: httpx.Response = response) -> httpx.Response:
            value.request = request
            return value

        destination = tmp_path / f"case-{index}.part"
        with pytest.raises(MediaDownloadError):
            await downloader(httpx.MockTransport(handle)).download(
                "https://cdn.bilivideo.com/a",
                destination,
                checkpoint=Checkpoint(),
                progress=lambda _value: collect_progress([], _value),
            )


async def test_size_limit_discards_partial(tmp_path: Path) -> None:
    body = b"x" * 32_768

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, request=request)

    media = downloader(httpx.MockTransport(handle), maximum_size_bytes=16_384)
    destination = tmp_path / "large.part"
    with pytest.raises(MediaDownloadError) as caught:
        await media.download(
            "https://cdn.bilivideo.com/a",
            destination,
            checkpoint=Checkpoint(),
            progress=lambda _value: collect_progress([], _value),
        )
    assert caught.value.code == "MEDIA_SIZE_LIMIT"
    assert not destination.exists()


async def test_network_failure_rotates_pinned_address() -> None:
    async def multiple(_host: str, _port: int) -> Iterable[str]:
        return ("93.184.216.34", "93.184.216.35")

    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.host))
        if request.url.host == "93.184.216.34":
            raise httpx.ConnectError("fixed connection failure", request=request)
        return httpx.Response(
            206,
            headers={"Content-Range": "bytes 0-3/4"},
            stream=httpx.ByteStream(b"data"),
            request=request,
        )

    media = downloader(httpx.MockTransport(handle), custom_resolver=multiple)
    with pytest.raises(MediaDownloadError) as caught:
        await media.probe("https://cdn.bilivideo.com/a")
    assert caught.value.retryable is True
    assert await media.probe("https://cdn.bilivideo.com/a") == 4
    assert seen == ["93.184.216.34", "93.184.216.35"]


@pytest.mark.parametrize("error", [DownloadCanceled(), DownloadPaused()])
async def test_checkpoint_control_errors_propagate(tmp_path: Path, error: Exception) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"data", request=request)

    with pytest.raises(type(error)):
        await downloader(httpx.MockTransport(handle)).download(
            "https://cdn.bilivideo.com/a",
            tmp_path / "controlled.part",
            checkpoint=Checkpoint(error),
            progress=lambda _value: collect_progress([], _value),
        )


def test_resume_metadata_and_helpers_reject_corruption(tmp_path: Path) -> None:
    destination = tmp_path / "track.part"
    metadata = destination.with_name(f"{destination.name}.resume.json")
    metadata.write_text("not-json", encoding="utf-8")
    assert HTTPMediaDownloader._load_metadata(metadata) == _ResumeMetadata(None, None, None)
    assert not metadata.exists()

    destination.write_bytes(b"too-long")
    HTTPMediaDownloader._write_metadata(metadata, _ResumeMetadata('"v1"', None, 2))
    loaded, offset = HTTPMediaDownloader._resume_state(destination, metadata)
    assert loaded == _ResumeMetadata(None, None, None)
    assert offset == 0
    assert not destination.exists()

    assert HTTPMediaDownloader._bounded_uint("123") == 123
    with pytest.raises(ValueError):
        HTTPMediaDownloader._bounded_uint("9" * 30)
    assert (
        HTTPMediaDownloader._unsatisfied_total(
            httpx.Response(416, headers={"Content-Range": "bytes */12"})
        )
        == 12
    )


async def test_iter_file_streams_exact_range(tmp_path: Path) -> None:
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"0123456789")
    chunks = [chunk async for chunk in iter_file(path, start=3, length=4, chunk_size=2)]
    assert b"".join(chunks) == b"3456"

    symlink = tmp_path / "link.bin"
    try:
        symlink.symlink_to(path)
    except OSError:
        return
    with pytest.raises(OSError):
        _ = [chunk async for chunk in iter_file(symlink, start=0, length=1)]


async def test_runtime_limits_and_rate_throttle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = downloader(httpx.MockTransport(lambda request: httpx.Response(200, request=request)))
    media.configure_limits(
        maximum_size_bytes=2 * 1024 * 1024,
        rate_limit_bytes_per_second=1024,
    )
    delays: list[float] = []

    async def fake_sleep(value: float) -> None:
        delays.append(value)

    monkeypatch.setattr("app.media.download.time.monotonic", lambda: 10.0)
    monkeypatch.setattr("app.media.download.asyncio.sleep", fake_sleep)
    await media._throttle(2048, 9.0)
    assert delays == [1.0]

    media.configure_limits(
        maximum_size_bytes=2 * 1024 * 1024,
        rate_limit_bytes_per_second=None,
    )
    await media._throttle(2048, 9.0)
    assert delays == [1.0]
    with pytest.raises(ValueError):
        media.configure_limits(maximum_size_bytes=1, rate_limit_bytes_per_second=None)
    with pytest.raises(ValueError):
        media.configure_limits(maximum_size_bytes=2 * 1024 * 1024, rate_limit_bytes_per_second=0)

    media.configure_timeout(timeout_seconds=8.0, connect_timeout_seconds=30.0)
    assert media.timeout.read == 8.0
    assert media.timeout.write == 8.0
    assert media.timeout.pool == 8.0
    assert media.timeout.connect == 8.0
    with pytest.raises(ValueError):
        media.configure_timeout(timeout_seconds=float("nan"), connect_timeout_seconds=1.0)
    with pytest.raises(ValueError):
        media.configure_timeout(timeout_seconds=30.0, connect_timeout_seconds=0.0)
