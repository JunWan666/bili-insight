from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import stat
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol, cast

import httpx

from app.media.security import (
    MediaURLValidator,
    UnsafeMediaURLError,
    ValidatedMediaTarget,
)

logger = logging.getLogger(__name__)

_CONTENT_RANGE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.IGNORECASE)
_UNSATISFIED_RANGE = re.compile(r"^bytes\s+\*/(\d+)$", re.IGNORECASE)


class DownloadCanceled(Exception):
    """Cooperative cancellation requested by the job controller."""


class DownloadPaused(Exception):
    """Cooperative pause requested by the job controller."""


class MediaDownloadError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class DownloadCheckpoint(Protocol):
    async def checkpoint(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    downloaded_bytes: int
    total_bytes: int | None


@dataclass(frozen=True, slots=True)
class DownloadResult:
    path: Path
    size: int
    expected_size: int | None
    resumed_from: int


ProgressCallback = Callable[[DownloadProgress], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _ResumeMetadata:
    etag: str | None
    last_modified: str | None
    total_size: int | None

    @property
    def if_range(self) -> str | None:
        if self.etag and not self.etag.lstrip().lower().startswith("w/"):
            return self.etag
        return self.last_modified


class HTTPMediaDownloader:
    """Bounded streaming HTTP downloader with safe Range resume semantics."""

    def __init__(
        self,
        validator: MediaURLValidator,
        *,
        user_agent: str,
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: float = 8.0,
        chunk_size: int = 256 * 1024,
        maximum_size_bytes: int = 1_099_511_627_776,
        rate_limit_bytes_per_second: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if chunk_size < 16 * 1024 or chunk_size > 4 * 1024 * 1024:
            raise ValueError("Download chunk size is outside the safe range")
        if maximum_size_bytes < chunk_size:
            raise ValueError("Download size limit is outside the safe range")
        if rate_limit_bytes_per_second is not None and rate_limit_bytes_per_second <= 0:
            raise ValueError("Download rate limit is outside the safe range")
        self.validator = validator
        self.user_agent = user_agent
        self.timeout = httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds)
        self.chunk_size = chunk_size
        self.maximum_size_bytes = maximum_size_bytes
        self.rate_limit_bytes_per_second = rate_limit_bytes_per_second
        self.transport = transport
        self._address_cursor: dict[tuple[str, int], int] = {}

    async def probe(self, url: str, *, max_bytes: int = 1_024) -> int:
        """Read at most a tiny prefix and return the declared total size when known."""

        if not 1 <= max_bytes <= 64 * 1024:
            raise ValueError("Probe byte limit is outside the safe range")
        target: ValidatedMediaTarget | None = None
        address: str | None = None
        try:
            target = await self.validator.resolve(url)
            address = self._select_address(target)
            headers = self._headers()
            headers["Host"] = target.host_header
            headers["Range"] = f"bytes=0-{max_bytes - 1}"
            async with self._client() as client:
                async with client.stream(
                    "GET",
                    target.pinned_url(address),
                    headers=headers,
                    extensions={"sni_hostname": target.host},
                ) as response:
                    self._raise_for_status(response)
                    if response.status_code not in {200, 206}:
                        raise MediaDownloadError(
                            "MEDIA_PROBE_FAILED",
                            "所选媒体流无法完成小范围读取验证",
                        )
                    self._validate_identity_encoding(response)
                    if response.status_code == 206 and self._range_start(response) != 0:
                        raise MediaDownloadError(
                            "UPSTREAM_PROTOCOL_ERROR",
                            "媒体服务器返回了无效的探测范围",
                        )
                    received = 0
                    async for chunk in response.aiter_raw(max_bytes):
                        received += len(chunk)
                        if received >= max_bytes:
                            break
                    if received == 0:
                        raise MediaDownloadError(
                            "MEDIA_EMPTY_RESPONSE",
                            "所选媒体流没有返回有效内容",
                        )
                    total = self._response_total(response, offset=0)
                    if total is not None and total > self.maximum_size_bytes:
                        raise MediaDownloadError(
                            "MEDIA_SIZE_LIMIT",
                            "媒体文件超过允许的单文件大小",
                        )
                    return total if total is not None else received
        except UnsafeMediaURLError as exc:
            raise MediaDownloadError("UNSAFE_MEDIA_URL", str(exc)) from exc
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            if target is not None and address is not None:
                self._mark_network_failure(target, address)
            raise MediaDownloadError(
                "UPSTREAM_NETWORK_ERROR",
                "媒体流访问超时或网络连接失败",
                retryable=True,
            ) from exc

    async def download(
        self,
        url: str,
        destination: Path,
        *,
        checkpoint: DownloadCheckpoint,
        progress: ProgressCallback,
    ) -> DownloadResult:
        target: ValidatedMediaTarget | None = None
        address: str | None = None
        try:
            target = await self.validator.resolve(url)
            address = self._select_address(target)
            await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
            metadata_path = self._metadata_path(destination)
            metadata, offset = await asyncio.to_thread(
                self._resume_state,
                destination,
                metadata_path,
            )
            resumed_from = offset
            if offset > self.maximum_size_bytes:
                self.discard_partial(destination)
                raise MediaDownloadError(
                    "MEDIA_SIZE_LIMIT",
                    "媒体文件超过允许的单文件大小",
                )
            headers = self._headers()
            headers["Host"] = target.host_header
            if offset:
                headers["Range"] = f"bytes={offset}-"
                if metadata.if_range:
                    headers["If-Range"] = metadata.if_range

            await checkpoint.checkpoint()
            async with self._client() as client:
                async with client.stream(
                    "GET",
                    target.pinned_url(address),
                    headers=headers,
                    extensions={"sni_hostname": target.host},
                ) as response:
                    if response.status_code == 416 and offset:
                        total = self._unsatisfied_total(response)
                        if (
                            total is not None
                            and total == offset
                            and (metadata.total_size is None or metadata.total_size == total)
                            and self._response_validator_matches(metadata, response)
                        ):
                            metadata_path.unlink(missing_ok=True)
                            await progress(DownloadProgress(offset, total))
                            return DownloadResult(destination, offset, total, resumed_from)
                        self.discard_partial(destination)
                        raise MediaDownloadError(
                            "RANGE_MISMATCH",
                            "媒体服务器不再接受已保存的断点位置",
                            retryable=True,
                        )
                    self._raise_for_status(response)
                    if response.status_code not in {200, 206}:
                        raise MediaDownloadError(
                            "UPSTREAM_PROTOCOL_ERROR",
                            "媒体服务器返回了不支持的响应",
                        )
                    self._validate_identity_encoding(response)

                    mode = "ab"
                    if offset and response.status_code == 206:
                        range_start = self._range_start(response)
                        if range_start != offset:
                            raise MediaDownloadError(
                                "RANGE_MISMATCH",
                                "媒体服务器返回的断点位置不一致",
                                retryable=True,
                            )
                        if self._validator_changed(metadata, response):
                            raise MediaDownloadError(
                                "MEDIA_CHANGED",
                                "媒体内容已变化，需要重新开始下载",
                                retryable=True,
                            )
                    elif response.status_code == 200:
                        mode = "wb"
                        offset = 0
                        resumed_from = 0
                    else:
                        if self._range_start(response) != 0:
                            raise MediaDownloadError(
                                "UPSTREAM_PROTOCOL_ERROR",
                                "媒体服务器返回的起始范围无效",
                            )
                        mode = "wb"
                        offset = 0

                    total_size = self._response_total(response, offset=offset)
                    if total_size is not None and total_size > self.maximum_size_bytes:
                        raise MediaDownloadError(
                            "MEDIA_SIZE_LIMIT",
                            "媒体文件超过允许的单文件大小",
                        )
                    current_metadata = _ResumeMetadata(
                        etag=response.headers.get("ETag"),
                        last_modified=response.headers.get("Last-Modified"),
                        total_size=total_size,
                    )
                    self._write_metadata(metadata_path, current_metadata)
                    downloaded = offset
                    transfer_started = time.monotonic()
                    await progress(DownloadProgress(downloaded, total_size))
                    with self._open_destination(destination, mode) as output_file:
                        async for chunk in response.aiter_bytes(self.chunk_size):
                            await checkpoint.checkpoint()
                            if not chunk:
                                continue
                            if downloaded + len(chunk) > self.maximum_size_bytes:
                                raise MediaDownloadError(
                                    "MEDIA_SIZE_LIMIT",
                                    "媒体文件超过允许的单文件大小",
                                )
                            output_file.write(chunk)
                            downloaded += len(chunk)
                            await progress(DownloadProgress(downloaded, total_size))
                            await self._throttle(downloaded - offset, transfer_started)
                        output_file.flush()
                        os.fsync(output_file.fileno())

            final_size = await asyncio.to_thread(self._file_size, destination)
            if final_size <= 0:
                raise MediaDownloadError(
                    "MEDIA_EMPTY_RESPONSE",
                    "媒体流没有返回有效内容",
                    retryable=True,
                )
            if total_size is not None and final_size != total_size:
                raise MediaDownloadError(
                    "INCOMPLETE_DOWNLOAD",
                    "媒体流下载不完整",
                    retryable=True,
                )
            metadata_path.unlink(missing_ok=True)
            return DownloadResult(destination, final_size, total_size, resumed_from)
        except (DownloadCanceled, DownloadPaused, asyncio.CancelledError):
            raise
        except MediaDownloadError as exc:
            if exc.code == "MEDIA_SIZE_LIMIT":
                self.discard_partial(destination)
            raise
        except UnsafeMediaURLError as exc:
            raise MediaDownloadError("UNSAFE_MEDIA_URL", str(exc)) from exc
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            if target is not None and address is not None:
                self._mark_network_failure(target, address)
            raise MediaDownloadError(
                "UPSTREAM_NETWORK_ERROR",
                "下载过程中网络连接中断",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise MediaDownloadError("STORAGE_WRITE_FAILED", "无法写入媒体临时文件") from exc

    def discard_partial(self, destination: Path) -> None:
        destination.unlink(missing_ok=True)
        self._metadata_path(destination).unlink(missing_ok=True)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self.transport,
            timeout=self.timeout,
            follow_redirects=False,
            trust_env=False,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Referer": "https://www.bilibili.com/",
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        }

    def _select_address(self, target: ValidatedMediaTarget) -> str:
        key = (target.host, target.port)
        index = self._address_cursor.get(key, 0) % len(target.addresses)
        return target.addresses[index]

    def _mark_network_failure(self, target: ValidatedMediaTarget, address: str) -> None:
        key = (target.host, target.port)
        try:
            current = target.addresses.index(address)
        except ValueError:
            current = self._address_cursor.get(key, 0)
        self._address_cursor[key] = (current + 1) % len(target.addresses)

    def configure_limits(
        self,
        *,
        maximum_size_bytes: int,
        rate_limit_bytes_per_second: int | None,
    ) -> None:
        if maximum_size_bytes < self.chunk_size:
            raise ValueError("Download size limit is outside the safe range")
        if rate_limit_bytes_per_second is not None and rate_limit_bytes_per_second <= 0:
            raise ValueError("Download rate limit is outside the safe range")
        self.maximum_size_bytes = maximum_size_bytes
        self.rate_limit_bytes_per_second = rate_limit_bytes_per_second

    def configure_timeout(
        self,
        *,
        timeout_seconds: float,
        connect_timeout_seconds: float,
    ) -> None:
        if not math.isfinite(timeout_seconds) or not 1.0 <= timeout_seconds <= 3_600.0:
            raise ValueError("Download timeout is outside the safe range")
        if not math.isfinite(connect_timeout_seconds) or connect_timeout_seconds < 0.1:
            raise ValueError("Download connect timeout is outside the safe range")
        bounded_connect_timeout = min(connect_timeout_seconds, timeout_seconds)
        self.timeout = httpx.Timeout(
            timeout_seconds,
            connect=bounded_connect_timeout,
        )

    async def _throttle(self, transferred: int, started: float) -> None:
        limit = self.rate_limit_bytes_per_second
        if limit is None or transferred <= 0:
            return
        delay = transferred / limit - (time.monotonic() - started)
        if delay > 0:
            await asyncio.sleep(delay)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status in {301, 302, 303, 307, 308}:
            raise MediaDownloadError(
                "UNSAFE_REDIRECT",
                "媒体服务器返回了未经验证的跳转地址",
            )
        if status in {401, 403, 404, 410}:
            raise MediaDownloadError(
                "MEDIA_URL_EXPIRED",
                "媒体地址已失效，需要重新解析",
                retryable=True,
            )
        if status in {408, 425, 429} or status >= 500:
            raise MediaDownloadError(
                "UPSTREAM_TEMPORARY_ERROR",
                "媒体服务器暂时不可用",
                retryable=True,
            )
        if status >= 400:
            raise MediaDownloadError(
                "UPSTREAM_DOWNLOAD_REJECTED",
                "媒体服务器拒绝了下载请求",
            )

    @staticmethod
    def _validate_identity_encoding(response: httpx.Response) -> None:
        content_encoding = response.headers.get("Content-Encoding", "identity").lower()
        if content_encoding not in {"", "identity"}:
            raise MediaDownloadError(
                "UPSTREAM_PROTOCOL_ERROR",
                "媒体服务器返回了不支持的压缩响应",
            )

    @staticmethod
    def _response_total(response: httpx.Response, *, offset: int) -> int | None:
        content_range = response.headers.get("Content-Range")
        if content_range:
            _, _, total = HTTPMediaDownloader._parsed_content_range(response)
            return total
        content_length = response.headers.get("Content-Length")
        if content_length is None:
            return None
        try:
            if len(content_length.strip()) > 20:
                raise ValueError("content length is too large")
            length = int(content_length)
        except ValueError as exc:
            raise MediaDownloadError(
                "UPSTREAM_PROTOCOL_ERROR",
                "媒体服务器返回了无效的长度信息",
            ) from exc
        if length < 0:
            raise MediaDownloadError(
                "UPSTREAM_PROTOCOL_ERROR",
                "媒体服务器返回了无效的长度信息",
            )
        return offset + length if response.status_code == 206 else length

    @staticmethod
    def _range_start(response: httpx.Response) -> int | None:
        value = response.headers.get("Content-Range", "")
        if not value:
            return None
        start, _, _ = HTTPMediaDownloader._parsed_content_range(response)
        return start

    @staticmethod
    def _parsed_content_range(response: httpx.Response) -> tuple[int, int, int | None]:
        value = response.headers.get("Content-Range", "")
        match = _CONTENT_RANGE.fullmatch(value.strip())
        if match is None:
            raise MediaDownloadError(
                "UPSTREAM_PROTOCOL_ERROR",
                "媒体服务器返回了无效的范围信息",
            )
        try:
            start = HTTPMediaDownloader._bounded_uint(match.group(1))
            end = HTTPMediaDownloader._bounded_uint(match.group(2))
            raw_total = match.group(3)
            total = HTTPMediaDownloader._bounded_uint(raw_total) if raw_total != "*" else None
        except ValueError as exc:
            raise MediaDownloadError(
                "UPSTREAM_PROTOCOL_ERROR",
                "媒体服务器返回了过大的范围信息",
            ) from exc
        if end < start or (total is not None and (total <= 0 or end >= total)):
            raise MediaDownloadError(
                "UPSTREAM_PROTOCOL_ERROR",
                "媒体服务器返回了不一致的范围信息",
            )
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared_length = HTTPMediaDownloader._bounded_uint(content_length.strip())
            except ValueError as exc:
                raise MediaDownloadError(
                    "UPSTREAM_PROTOCOL_ERROR",
                    "媒体服务器返回了无效的长度信息",
                ) from exc
            if declared_length != end - start + 1:
                raise MediaDownloadError(
                    "UPSTREAM_PROTOCOL_ERROR",
                    "媒体服务器返回的范围长度不一致",
                )
        return start, end, total

    @staticmethod
    def _unsatisfied_total(response: httpx.Response) -> int | None:
        value = response.headers.get("Content-Range", "")
        match = _UNSATISFIED_RANGE.fullmatch(value.strip())
        if match is None:
            return None
        try:
            return HTTPMediaDownloader._bounded_uint(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def _bounded_uint(value: str) -> int:
        if not value or len(value) > 20 or not value.isascii() or not value.isdigit():
            raise ValueError("integer is outside the supported range")
        parsed = int(value)
        if parsed > 9_223_372_036_854_775_807:
            raise ValueError("integer is outside the supported range")
        return parsed

    @staticmethod
    def _metadata_path(destination: Path) -> Path:
        return destination.with_name(f"{destination.name}.resume.json")

    @staticmethod
    def _load_metadata(path: Path) -> _ResumeMetadata:
        if not path.is_file():
            return _ResumeMetadata(None, None, None)
        try:
            if path.is_symlink():
                raise ValueError("resume metadata cannot be a symbolic link")
            if path.stat().st_size > 4_096:
                raise ValueError("resume metadata is too large")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("resume metadata is not an object")
            etag = payload.get("etag")
            modified = payload.get("last_modified")
            total = payload.get("total_size")
            return _ResumeMetadata(
                etag=etag if isinstance(etag, str) else None,
                last_modified=modified if isinstance(modified, str) else None,
                total_size=total if isinstance(total, int) and total >= 0 else None,
            )
        except (OSError, ValueError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            return _ResumeMetadata(None, None, None)

    @staticmethod
    def _write_metadata(path: Path, metadata: _ResumeMetadata) -> None:
        temporary = path.with_name(f"{path.name}.tmp")
        payload = {
            "etag": metadata.etag,
            "last_modified": metadata.last_modified,
            "total_size": metadata.total_size,
        }
        with temporary.open("w", encoding="utf-8") as target:
            target.write(json.dumps(payload, separators=(",", ":")))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)

    @staticmethod
    def _validator_changed(metadata: _ResumeMetadata, response: httpx.Response) -> bool:
        old_etag = metadata.etag
        new_etag = response.headers.get("ETag")
        if old_etag and new_etag and old_etag != new_etag:
            return True
        old_modified = metadata.last_modified
        new_modified = response.headers.get("Last-Modified")
        if old_modified and new_modified and old_modified != new_modified:
            return True
        response_total = HTTPMediaDownloader._response_total(response, offset=0)
        return bool(
            metadata.total_size is not None
            and response_total is not None
            and metadata.total_size != response_total
        )

    @staticmethod
    def _response_validator_matches(
        metadata: _ResumeMetadata,
        response: httpx.Response,
    ) -> bool:
        etag = metadata.etag
        if etag and not etag.lstrip().lower().startswith("w/"):
            return bool(response.headers.get("ETag") == etag)
        modified = metadata.last_modified
        return bool(modified and response.headers.get("Last-Modified") == modified)

    @classmethod
    def _resume_state(
        cls,
        destination: Path,
        metadata_path: Path,
    ) -> tuple[_ResumeMetadata, int]:
        metadata = cls._load_metadata(metadata_path)
        if not destination.is_file():
            return metadata, 0
        if destination.is_symlink():
            destination.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            return _ResumeMetadata(None, None, None), 0
        offset = destination.stat().st_size
        if offset <= 0:
            return metadata, 0
        if metadata.if_range is None:
            destination.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            return _ResumeMetadata(None, None, None), 0
        if metadata.total_size is not None and offset > metadata.total_size:
            cls._metadata_path(destination).unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            return _ResumeMetadata(None, None, None), 0
        return metadata, offset

    @staticmethod
    def _file_size(path: Path) -> int:
        return path.stat().st_size

    @staticmethod
    def _open_destination(path: Path, mode: str) -> BinaryIO:
        if path.is_symlink():
            raise OSError("download destination cannot be a symbolic link")
        flags = os.O_WRONLY | os.O_CREAT
        flags |= os.O_APPEND if mode == "ab" else os.O_TRUNC
        flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("download destination is not a regular file")
            return cast(BinaryIO, os.fdopen(descriptor, mode))
        except Exception:
            os.close(descriptor)
            raise


async def iter_file(
    path: Path,
    *,
    start: int,
    length: int,
    chunk_size: int = 256 * 1024,
) -> AsyncIterator[bytes]:
    """Stream a bounded file segment without loading the artifact into memory."""

    remaining = length
    if await asyncio.to_thread(path.is_symlink):
        raise OSError("artifact source cannot be a symbolic link")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("artifact source is not a regular file")
        source = os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise
    with source:
        source.seek(start)
        while remaining > 0:
            chunk = await asyncio.to_thread(source.read, min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
