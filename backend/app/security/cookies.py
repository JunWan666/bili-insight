from __future__ import annotations

import copy
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from http.cookiejar import Cookie, CookieJar
from typing import Any

import orjson
from cryptography.fernet import Fernet, InvalidToken
from fastapi import status

from app.core.exceptions import AppError, ErrorCode

_COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,256}$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class ParsedCookies:
    jar: CookieJar
    expires_at: datetime | None
    accepted_count: int
    discarded_count: int


def clone_cookie_jar(source: CookieJar) -> CookieJar:
    target = CookieJar()
    for cookie in source:
        target.set_cookie(copy.copy(cookie))
    return target


def _is_bilibili_domain(domain: str) -> bool:
    canonical = domain.lower().strip().rstrip(".").lstrip(".")
    return canonical == "bilibili.com" or canonical.endswith(".bilibili.com")


class CookieFileParser:
    def __init__(self, *, max_bytes: int, max_items: int) -> None:
        self.max_bytes = max_bytes
        self.max_items = max_items

    def parse(self, payload: bytes) -> ParsedCookies:
        if not payload:
            raise self._format_error()
        if len(payload) > self.max_bytes:
            raise AppError(
                ErrorCode.UPLOAD_TOO_LARGE,
                "Cookie 文件超过允许的大小",
                action="请选择不超过 1 MB 的 JSON 文件",
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            )
        try:
            decoded = payload.decode("utf-8-sig")
            data = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise self._format_error() from exc
        if (
            not isinstance(data, list)
            or len(data) > self.max_items
            or _exceeds_json_depth(data, maximum_depth=8)
        ):
            raise self._format_error()

        jar = CookieJar()
        now = int(time.time())
        accepted = 0
        discarded = 0
        expired_bilibili = 0
        authentication_expirations: list[int] = []
        all_expirations: list[int] = []

        for item in data:
            if not isinstance(item, dict):
                raise self._format_error()
            parsed = self._parse_item(item)
            if parsed is None:
                discarded += 1
                continue
            if parsed.expires is not None and parsed.expires <= now:
                expired_bilibili += 1
                discarded += 1
                continue
            jar.set_cookie(parsed)
            accepted += 1
            if parsed.expires is not None:
                all_expirations.append(parsed.expires)
                if parsed.name.lower() == "sessdata":
                    authentication_expirations.append(parsed.expires)

        if accepted == 0:
            if expired_bilibili:
                raise AppError(
                    ErrorCode.AUTH_EXPIRED,
                    "Cookie 已过期，当前将继续使用匿名模式",
                    action="从浏览器重新导出 Cookie 后再上传",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            raise self._format_error()

        expiration_values = authentication_expirations or all_expirations
        expires_at = (
            datetime.fromtimestamp(min(expiration_values), tz=UTC) if expiration_values else None
        )
        return ParsedCookies(
            jar=jar,
            expires_at=expires_at,
            accepted_count=accepted,
            discarded_count=discarded,
        )

    def _parse_item(self, item: dict[str, Any]) -> Cookie | None:
        name = item.get("name")
        value = item.get("value")
        domain_value = item.get("domain")
        if not isinstance(name, str) or not _COOKIE_NAME.fullmatch(name):
            raise self._format_error()
        if not isinstance(value, str) or len(value) > 16_384 or _CONTROL_CHARACTER.search(value):
            raise self._format_error()
        if not isinstance(domain_value, str) or len(domain_value) > 255:
            raise self._format_error()
        domain = domain_value.lower().strip().rstrip(".")
        if not _is_bilibili_domain(domain):
            return None

        path_value = item.get("path", "/")
        if not isinstance(path_value, str) or not path_value.startswith("/"):
            raise self._format_error()
        if len(path_value) > 2_048 or _CONTROL_CHARACTER.search(path_value):
            raise self._format_error()

        expires = self._parse_expiration(item.get("expires", item.get("expirationDate")))

        secure = item.get("secure", False)
        http_only = item.get("httpOnly", item.get("http_only", False))
        if not isinstance(secure, bool) or not isinstance(http_only, bool):
            raise self._format_error()

        return Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=domain.startswith("."),
            path=path_value,
            path_specified=True,
            secure=secure,
            expires=expires,
            discard=expires is None,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": "true"} if http_only else {},
            rfc2109=False,
        )

    def _parse_expiration(self, value: object) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise self._format_error()
        numeric: int | float
        if isinstance(value, int):
            numeric = value
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise self._format_error()
            numeric = value
        elif isinstance(value, str):
            try:
                numeric = float(value)
            except ValueError as exc:
                raise self._format_error() from exc
            if not math.isfinite(numeric):
                raise self._format_error()
        else:
            raise self._format_error()
        if numeric <= 0:
            return None
        try:
            expires = int(numeric)
            datetime.fromtimestamp(expires, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise self._format_error() from exc
        return expires

    @staticmethod
    def _format_error() -> AppError:
        return AppError(
            ErrorCode.AUTH_FORMAT_INVALID,
            "Cookie 文件格式无法识别，未保存任何内容",
            action="请选择浏览器导出的 Bilibili Cookie JSON 数组",
        )


class CookieCipher:
    """Authenticated encryption for cookies explicitly remembered on the local machine."""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, jar: CookieJar) -> bytes:
        serialized = []
        for cookie in jar:
            serialized.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": cookie.secure,
                    "expires": cookie.expires,
                    "discard": cookie.discard,
                    "httpOnly": cookie.has_nonstandard_attr("HttpOnly"),
                }
            )
        return self._fernet.encrypt(orjson.dumps(serialized))

    def decrypt(self, ciphertext: bytes) -> CookieJar:
        try:
            payload = self._fernet.decrypt(ciphertext)
            decoded = orjson.loads(payload)
        except (InvalidToken, orjson.JSONDecodeError) as exc:
            raise AppError(
                ErrorCode.AUTH_VALIDATION,
                "已保存的登录状态无法解密",
                action="检查服务端主密钥，或清除后重新上传 Cookie",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        parser = CookieFileParser(max_bytes=max(len(payload) * 2, 1_024), max_items=2_000)
        return parser.parse(orjson.dumps(decoded)).jar


def _exceeds_json_depth(value: object, *, maximum_depth: int) -> bool:
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if isinstance(current, dict):
            if depth >= maximum_depth and current:
                return True
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            if depth >= maximum_depth and current:
                return True
            stack.extend((item, depth + 1) for item in current)
    return False
