from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import string
import unicodedata
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

_DNS_NAME = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
_MCDN_HTTPS_PORT = 8082
_MCDN_SUFFIX = "mcdn.bilivideo.cn"
_PGC_CDN_HTTPS_PORT = 4483
_PGC_CDN_SUFFIX = "edge.mountaintoys.cn"
_NAT64_PREFIXES = (
    ipaddress.IPv6Network("64:ff9b::/96"),
    ipaddress.IPv6Network("64:ff9b:1::/48"),
)
_FILENAME_TEMPLATE_FIELDS = frozenset({"title", "bvid", "page", "part", "quality"})


class UnsafeMediaURLError(ValueError):
    """Raised when an upstream media URL fails the outbound request policy."""


DNSResolver = Callable[[str, int], Awaitable[Iterable[str]]]


@dataclass(frozen=True, slots=True)
class ValidatedMediaTarget:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]

    @property
    def host_header(self) -> str:
        return self.host if self.port == 443 else f"{self.host}:{self.port}"

    def pinned_url(self, address: str) -> str:
        parsed = urlsplit(self.url)
        literal = ipaddress.ip_address(address)
        host = f"[{literal.compressed}]" if literal.version == 6 else literal.compressed
        netloc = host if self.port == 443 else f"{host}:{self.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))


async def _resolve_public_addresses(host: str, port: int) -> Iterable[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(
        host,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return {str(record[4][0]).split("%", maxsplit=1)[0] for record in records}


class MediaURLValidator:
    """Validate provider-issued URLs before every media request.

    Media URLs are still treated as untrusted even though they originate from a
    provider adapter.  Only HTTPS, explicitly configured host suffixes and
    globally routable DNS answers are accepted.  Redirects are deliberately
    handled by the downloader as failures, so every new target must pass this
    policy independently.
    """

    def __init__(
        self,
        allowed_host_suffixes: Iterable[str],
        *,
        resolver: DNSResolver | None = None,
    ) -> None:
        suffixes = tuple(
            self._normalize_configured_suffix(value)
            for value in allowed_host_suffixes
            if value.strip()
        )
        if not suffixes:
            raise ValueError("At least one media host suffix is required")
        self._suffixes = suffixes
        self._resolver = resolver or _resolve_public_addresses

    async def validate(self, url: str) -> str:
        return (await self.resolve(url)).url

    async def resolve(self, url: str) -> ValidatedMediaTarget:
        if not url or len(url) > 8_192 or any(ord(char) < 32 for char in url):
            raise UnsafeMediaURLError("媒体地址格式无效")
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise UnsafeMediaURLError("媒体地址格式无效") from exc
        if parsed.scheme.lower() != "https" or parsed.fragment:
            raise UnsafeMediaURLError("媒体地址必须使用 HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeMediaURLError("媒体地址不得包含用户凭据")
        try:
            host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        except UnicodeError as exc:
            raise UnsafeMediaURLError("媒体地址域名格式无效") from exc
        if not host or not self._host_allowed(host):
            raise UnsafeMediaURLError("媒体地址域名不在允许范围内")
        try:
            explicit_port = parsed.port
        except ValueError as exc:
            raise UnsafeMediaURLError("媒体地址端口无效") from exc
        port = 443 if explicit_port is None else explicit_port
        special_port_allowed = (
            port == _MCDN_HTTPS_PORT and self._host_matches_suffix(host, _MCDN_SUFFIX)
        ) or (port == _PGC_CDN_HTTPS_PORT and self._host_matches_suffix(host, _PGC_CDN_SUFFIX))
        if port != 443 and not special_port_allowed:
            raise UnsafeMediaURLError("媒体地址端口不在允许范围内")
        decoded_path = unquote(parsed.path).replace("\\", "/")
        if decoded_path.startswith("//") or any(ord(char) < 32 for char in decoded_path):
            raise UnsafeMediaURLError("媒体地址路径无效")

        try:
            addresses = tuple(await asyncio.wait_for(self._resolver(host, port), timeout=5.0))
        except (TimeoutError, OSError, UnicodeError) as exc:
            raise UnsafeMediaURLError("媒体地址域名无法安全解析") from exc
        if not addresses:
            raise UnsafeMediaURLError("媒体地址域名没有可用地址") from None
        try:
            parsed_addresses = tuple(ipaddress.ip_address(item) for item in addresses)
        except ValueError as exc:
            raise UnsafeMediaURLError("媒体地址域名返回了无效地址") from exc
        if any(not self._is_public_address(address) for address in parsed_addresses):
            raise UnsafeMediaURLError("媒体地址解析到了非公网地址") from None
        unique = sorted(set(parsed_addresses), key=lambda item: (item.version, int(item)))
        return ValidatedMediaTarget(
            url=url,
            host=host,
            port=port,
            addresses=tuple(item.compressed for item in unique),
        )

    def _host_allowed(self, host: str) -> bool:
        return any(self._host_matches_suffix(host, suffix) for suffix in self._suffixes)

    @staticmethod
    def _host_matches_suffix(host: str, suffix: str) -> bool:
        normalized = host.lower().rstrip(".")
        return normalized == suffix or normalized.endswith(f".{suffix}")

    @staticmethod
    def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        if (
            not address.is_global
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_private
            or address.is_reserved
            or address.is_unspecified
        ):
            return False
        if isinstance(address, ipaddress.IPv6Address):
            if address.is_site_local or any(address in network for network in _NAT64_PREFIXES):
                return False
            if address.ipv4_mapped is not None or address.sixtofour is not None:
                return False
            if address.teredo is not None:
                return False
        return True

    @staticmethod
    def _normalize_configured_suffix(value: str) -> str:
        try:
            suffix = value.strip().lstrip(".").rstrip(".").encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Media host suffix is not a valid DNS name") from exc
        suffix = suffix.lower()
        if not _DNS_NAME.fullmatch(suffix):
            raise ValueError("Media host suffix is not a valid DNS name")
        return suffix


_INVALID_FILENAME = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069<>:\"/\\|?*]+"
)
_WHITESPACE = re.compile(r"\s+")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def sanitize_filename(value: str, *, fallback: str = "media", max_length: int = 180) -> str:
    """Return a portable filename component without changing its extension."""

    normalized = unicodedata.normalize("NFKC", value)
    normalized = _INVALID_FILENAME.sub("_", normalized)
    normalized = _WHITESPACE.sub(" ", normalized).strip(" .")
    if not normalized or normalized in {".", ".."}:
        normalized = fallback

    path = Path(normalized)
    suffix = _truncate_filename_text(path.suffix, max_characters=16, max_bytes=32, max_utf16=32)
    stem = path.stem.strip(" .") or fallback
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    available = max(1, max_length - len(suffix))
    stem = (
        _truncate_filename_text(
            stem,
            max_characters=available,
            max_bytes=max(1, 240 - len(suffix.encode("utf-8"))),
            max_utf16=max(1, 240 - len(suffix.encode("utf-16-le")) // 2),
        ).rstrip(" .")
        or fallback[:available]
    )
    result = f"{stem}{suffix}"
    if result.upper().split(".", maxsplit=1)[0] in _WINDOWS_RESERVED:
        result = f"_{result}"
    return result[:max_length].rstrip(" .")


def _truncate_filename_text(
    value: str,
    *,
    max_characters: int,
    max_bytes: int,
    max_utf16: int,
) -> str:
    result: list[str] = []
    byte_count = 0
    utf16_count = 0
    for character in value:
        encoded_bytes = len(character.encode("utf-8"))
        encoded_utf16 = len(character.encode("utf-16-le")) // 2
        if (
            len(result) >= max_characters
            or byte_count + encoded_bytes > max_bytes
            or utf16_count + encoded_utf16 > max_utf16
        ):
            break
        result.append(character)
        byte_count += encoded_bytes
        utf16_count += encoded_utf16
    return "".join(result)


def render_filename_template(
    template: str,
    values: Mapping[str, object],
    *,
    extension: str,
    fallback: str = "media",
    max_length: int = 180,
) -> str:
    """Expand the supported filename fields and return one portable file component."""

    normalized_template = unicodedata.normalize("NFKC", template).strip()
    if not normalized_template or len(normalized_template) > max_length:
        raise ValueError("文件名模板为空或过长")
    if _INVALID_FILENAME.search(normalized_template):
        raise ValueError("文件名模板包含保留字符或路径分隔符")
    try:
        parsed = tuple(string.Formatter().parse(normalized_template))
    except ValueError as exc:
        raise ValueError("文件名模板的大括号格式无效") from exc
    for _, field_name, format_spec, conversion in parsed:
        if field_name is None:
            continue
        if field_name not in _FILENAME_TEMPLATE_FIELDS:
            raise ValueError("文件名模板包含不支持的字段")
        if format_spec or conversion:
            raise ValueError("文件名模板不支持格式化修饰符")
    rendered = normalized_template.format_map(
        {field: str(values.get(field, "")) for field in _FILENAME_TEMPLATE_FIELDS}
    )
    clean_extension = extension.lower().lstrip(".")
    if not clean_extension or not clean_extension.isascii() or not clean_extension.isalnum():
        raise ValueError("输出文件扩展名无效")
    suffix_length = len(clean_extension) + 1
    base = sanitize_filename(
        rendered,
        fallback=fallback,
        max_length=max(1, max_length - suffix_length),
    )
    return sanitize_filename(
        f"{base}.{clean_extension}",
        fallback=f"{fallback}.{clean_extension}",
        max_length=max_length,
    )


def safe_child_path(root: Path, *components: str) -> Path:
    """Resolve a server-controlled child path and enforce root containment."""

    resolved_root = root.expanduser().resolve()
    candidate = resolved_root.joinpath(*components).resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError("文件路径超出允许的存储目录")
    return candidate
