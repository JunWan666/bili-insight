from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings

request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_SENSITIVE_KEY = re.compile(
    r"(?:cookie|authorization|token|secret|password|sessdata|bili_jct|csrf|access_key|"
    r"refresh_token|dedeuserid|mid|account_id)",
    re.IGNORECASE,
)
_COOKIE_PAIR = re.compile(
    r"(?i)\b(SESSDATA|bili_jct|DedeUserID|DedeUserID__ckMd5|access_key|refresh_token)"
    r"\s*[=:]\s*([^;\s,&]+)"
)
_AUTH_HEADER = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_URL = re.compile(r"https?://[^\s\]\[\"'<>]+")
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:[A-Z]:[\\/](?:[^\\/\r\n\"'<>|?*]+[\\/])*"
    r"[^\\/\r\n\"'<>|?*,;)]*)"
)
_UNC_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![\\A-Za-z0-9_])(?:\\\\[^\\/\s\r\n\"'<>|?*]+"
    r"[\\/][^\\/\s\r\n\"'<>|?*]+(?:[\\/][^\r\n\"'<>|?*,;)]*)?)"
)
_FORWARD_UNC_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9_:])//[^/\s\r\n\"'<>:|?*]+/[^\s\r\n\"'<>:|?*,;)]*"
)
_QUOTED_POSIX_ABSOLUTE_PATH = re.compile(r"(?P<quote>[\"'])/(?!/)[^\r\n\"']*(?P=quote)")
_POSIX_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_:/])/(?!/)(?:[^/\s\r\n\"'<>:,;()]+/)*"
    r"[^/\s\r\n\"'<>:,;()]*"
)
_OBSERVABLE_REQUEST_PATH = re.compile(r"/api/v1(?:/[^\s\r\n\"'<>?]*)?(?:\?[^\s]*)?$")


def _redact_url(match: re.Match[str]) -> str:
    value = match.group(0)
    query_index = value.find("?")
    if query_index < 0:
        return value
    trailing = ""
    while value and value[-1] in ".,;):":
        trailing = value[-1] + trailing
        value = value[:-1]
    query_index = value.find("?")
    return f"{value[:query_index]}?<redacted>{trailing}"


def _redact_server_path(match: re.Match[str]) -> str:
    value = match.group(0)
    if _OBSERVABLE_REQUEST_PATH.fullmatch(value):
        path, separator, _ = value.partition("?")
        return f"{path}?<redacted>" if separator else path
    return "<path>"


def _redact_quoted_server_path(match: re.Match[str]) -> str:
    quote = match.group("quote")
    return f"{quote}<path>{quote}"


def redact_text(value: str) -> str:
    """Remove credentials, signed queries, and server filesystem paths."""

    result = _COOKIE_PAIR.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    result = _AUTH_HEADER.sub(lambda match: f"{match.group(1)} <redacted>", result)
    urls: list[str] = []

    def preserve_url(match: re.Match[str]) -> str:
        urls.append(_redact_url(match))
        return f"\x00URL{len(urls) - 1}\x00"

    result = _URL.sub(preserve_url, result)
    result = _UNC_ABSOLUTE_PATH.sub(_redact_server_path, result)
    result = _FORWARD_UNC_ABSOLUTE_PATH.sub(_redact_server_path, result)
    result = _WINDOWS_ABSOLUTE_PATH.sub(_redact_server_path, result)
    result = _QUOTED_POSIX_ABSOLUTE_PATH.sub(_redact_quoted_server_path, result)
    result = _POSIX_ABSOLUTE_PATH.sub(_redact_server_path, result)
    for index, url in enumerate(urls):
        result = result.replace(f"\x00URL{index}\x00", url)
    return result


def sanitize(value: Any) -> Any:
    """Recursively sanitize values before they reach logs or diagnostics."""

    if isinstance(value, Mapping):
        return {
            str(key): "<redacted>" if _SENSITIVE_KEY.search(str(key)) else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(sanitize(item) for item in value)
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, set):
        return sorted((sanitize(item) for item in value), key=str)
    if isinstance(value, bytes):
        return "<binary>"
    if isinstance(value, os.PathLike):
        return "<path>"
    if isinstance(value, str):
        return redact_text(value)
    return value


class RedactingFilter(logging.Filter):
    """Sanitize every log record, including positional and mapping arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize(record.msg)
        record.args = sanitize(record.args)
        return True


class JsonFormatter(logging.Formatter):
    """Small deterministic JSON formatter suitable for local and container logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "requestId": request_id_context.get(),
            "message": redact_text(record.getMessage()),
        }
        for key in (
            "event",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "upstream_status",
            "provider_code",
            "retry_count",
            "job_id",
        ):
            if hasattr(record, key):
                payload[key] = sanitize(getattr(record, key))
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        return redact_text(message)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            ConsoleFormatter(fmt="%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s")
        )

    class RequestIdFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.request_id = request_id_context.get()
            return True

    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)
    for noisy_logger in ("httpx", "httpcore", "aiosqlite"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
