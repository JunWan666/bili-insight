from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import httpx
import orjson
from fastapi import status

from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.db.models import StreamAccessRequirement, StreamKind
from app.media.danmaku import DanmakuValidationError, validate_danmaku_xml
from app.media.security import DNSResolver, MediaURLValidator, UnsafeMediaURLError
from app.providers.models import (
    AuthValidation,
    ProviderPart,
    ProviderStream,
    ProviderStreams,
    ProviderSubtitle,
    ProviderVideo,
    VideoReference,
)

logger = logging.getLogger(__name__)

_BVID = re.compile(r"^BV[0-9A-Za-z]{10}$", re.IGNORECASE)
_AID = re.compile(r"^av([1-9][0-9]{0,19})$", re.IGNORECASE)
_SEASON = re.compile(r"^ss([1-9][0-9]{0,18})$", re.IGNORECASE)
_EPISODE = re.compile(r"^ep([1-9][0-9]{0,18})$", re.IGNORECASE)
_VIDEO_PATH = re.compile(r"^/video/(BV[0-9A-Za-z]{10}|av[1-9][0-9]{0,19})/?$", re.IGNORECASE)
_BANGUMI_PATH = re.compile(r"^/bangumi/play/(ss|ep)([1-9][0-9]{0,18})/?$", re.IGNORECASE)
_INPUT_HOSTS = frozenset({"bilibili.com", "www.bilibili.com", "m.bilibili.com"})
_MAX_AID = (1 << 63) - 1
_DASH_BYTE_RANGE = re.compile(r"^(0|[1-9][0-9]*)-(0|[1-9][0-9]*)$")
_MAX_DASH_METADATA_OFFSET = 64 * 1024 * 1024
_PGC_CDN_HTTPS_PORT = 4483
_PGC_CDN_SUFFIX = "edge.mountaintoys.cn"

_VIEW_API = "https://api.bilibili.com/x/web-interface/view"
_TAGS_API = "https://api.bilibili.com/x/tag/archive/tags"
_PLAYURL_API = "https://api.bilibili.com/x/player/playurl"
_PLAYER_API = "https://api.bilibili.com/x/player/v2"
_NAV_API = "https://api.bilibili.com/x/web-interface/nav"
_DANMAKU_API = "https://api.bilibili.com/x/v1/dm/list.so"
_PGC_SEASON_API = "https://api.bilibili.com/pgc/view/web/season"
_PGC_PLAYURL_API = "https://api.bilibili.com/pgc/player/web/playurl"

_QUALITY_LABELS = {
    6: "240P",
    16: "360P",
    32: "480P",
    64: "720P",
    74: "720P 60帧",
    80: "1080P",
    112: "1080P+",
    116: "1080P 60帧",
    120: "4K",
    125: "HDR",
    126: "杜比视界",
    127: "8K",
}


class BilibiliProvider:
    """Strict adapter around Bilibili's public web APIs."""

    provider_name = "bilibili"
    pgc_provider_name = "bilibili_pgc"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        media_resolver: DNSResolver | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.timeout = httpx.Timeout(
            settings.upstream_timeout_seconds,
            connect=settings.upstream_connect_timeout_seconds,
        )
        self._request_interval_seconds = 0.0
        self._request_gate = asyncio.Lock()
        self._last_request_at = 0.0
        self._clock = time.monotonic
        self._sleep = asyncio.sleep
        self._media_validator = MediaURLValidator(
            settings.media_host_suffixes,
            resolver=media_resolver,
        )

    def configure_runtime(
        self,
        *,
        timeout_seconds: float,
        upstream_interval_milliseconds: int,
    ) -> None:
        if not 1.0 <= timeout_seconds <= 300.0:
            raise ValueError("Provider timeout is outside the safe range")
        if not 0 <= upstream_interval_milliseconds <= 60_000:
            raise ValueError("Provider request interval is outside the safe range")
        self.timeout = httpx.Timeout(
            timeout_seconds,
            connect=min(self.settings.upstream_connect_timeout_seconds, timeout_seconds),
        )
        self._request_interval_seconds = upstream_interval_milliseconds / 1_000

    async def normalize_url(self, value: str) -> VideoReference:
        raw = value.strip()
        if not raw or len(raw) > 2_048 or any(ord(char) < 32 for char in raw):
            raise self._invalid_link()

        page_number = 1
        identifier = raw
        season_id: int | None = None
        episode_id: int | None = None
        if "://" in raw:
            parsed = urlsplit(raw)
            if parsed.scheme.lower() != "https":
                raise self._invalid_link()
            if parsed.username or parsed.password or parsed.port not in (None, 443):
                raise self._invalid_link()
            host = (parsed.hostname or "").lower().rstrip(".")
            if host not in _INPUT_HOSTS:
                if host == "b23.tv" or host.endswith(".b23.tv"):
                    raise AppError(
                        ErrorCode.UNSUPPORTED_CONTENT,
                        "当前版本暂不支持短链接",
                        action="在 Bilibili 页面复制完整的视频或番剧链接后重试",
                    )
                raise self._invalid_link()
            path_match = _VIDEO_PATH.fullmatch(parsed.path)
            bangumi_match = _BANGUMI_PATH.fullmatch(parsed.path)
            if path_match is not None:
                identifier = path_match.group(1)
            elif bangumi_match is not None:
                path_content_id = int(bangumi_match.group(2))
                if path_content_id > _MAX_AID:
                    raise self._invalid_link()
                if bangumi_match.group(1).lower() == "ss":
                    season_id = path_content_id
                    identifier = f"ss{path_content_id}"
                else:
                    episode_id = path_content_id
                    identifier = f"ep{path_content_id}"
            else:
                raise AppError(
                    ErrorCode.UNSUPPORTED_CONTENT,
                    "当前支持普通 BV/AV 投稿视频与番剧 ss/ep 链接",
                    action="请输入 bilibili.com/video/... 或 bilibili.com/bangumi/play/... 链接",
                )
            if path_match is not None:
                query = parse_qs(parsed.query, keep_blank_values=True)
                if "p" in query:
                    if len(query["p"]) != 1 or not query["p"][0].isdigit():
                        raise self._invalid_link()
                    page_number = int(query["p"][0])
                    if not 1 <= page_number <= 10_000:
                        raise self._invalid_link()

        if season_id is None and episode_id is None:
            season_match = _SEASON.fullmatch(identifier)
            episode_match = _EPISODE.fullmatch(identifier)
            if season_match is not None:
                season_id = int(season_match.group(1))
            elif episode_match is not None:
                episode_id = int(episode_match.group(1))

        if season_id is not None or episode_id is not None:
            resolved_content_id = season_id if season_id is not None else episode_id
            prefix = "ss" if season_id is not None else "ep"
            assert resolved_content_id is not None
            return VideoReference(
                bvid=f"{prefix.upper()}{resolved_content_id}",
                aid=resolved_content_id,
                page_number=1,
                normalized_url=(
                    f"https://www.bilibili.com/bangumi/play/{prefix}{resolved_content_id}"
                ),
                provider=self.pgc_provider_name,
                season_id=season_id,
                episode_id=episode_id,
            )

        bvid: str | None = None
        aid: int | None = None
        if _BVID.fullmatch(identifier):
            bvid = f"BV{identifier[2:]}"
            normalized_identifier = bvid
        else:
            aid_match = _AID.fullmatch(identifier)
            if aid_match is None:
                raise self._invalid_link()
            aid = int(aid_match.group(1))
            if aid > _MAX_AID:
                raise self._invalid_link()
            normalized_identifier = f"av{aid}"
        suffix = f"?p={page_number}" if page_number != 1 else ""
        return VideoReference(
            bvid=bvid,
            aid=aid,
            page_number=page_number,
            normalized_url=f"https://www.bilibili.com/video/{normalized_identifier}/{suffix}",
            provider=self.provider_name,
        )

    async def get_video(
        self, reference: VideoReference, cookies: CookieJar | None = None
    ) -> ProviderVideo:
        if reference.provider == self.pgc_provider_name:
            return await self._get_pgc_video(reference, cookies)
        if reference.provider != self.provider_name:
            raise self._upstream_changed()
        params = {"bvid": reference.bvid} if reference.bvid else {"aid": reference.aid}
        response = await self._request_json(_VIEW_API, params=params, cookies=cookies)
        data = self._response_data(response)
        video = self._parse_video(data)
        try:
            tags_response = await self._request_json(
                _TAGS_API, params={"bvid": video.bvid}, cookies=cookies
            )
            tags_data = self._response_data(tags_response)
            tags = (
                [
                    str(item["tag_name"])
                    for item in tags_data
                    if isinstance(item, dict) and isinstance(item.get("tag_name"), str)
                ]
                if isinstance(tags_data, list)
                else []
            )
        except AppError as exc:
            logger.warning(
                "Optional video tags could not be loaded: %s",
                exc.code.value,
                extra={"event": "provider_optional_tags_failed"},
            )
            tags = []
        return ProviderVideo(
            provider=video.provider,
            bvid=video.bvid,
            aid=video.aid,
            title=video.title,
            description=video.description,
            cover_url=video.cover_url,
            owner_name=video.owner_name,
            duration=video.duration,
            published_at=video.published_at,
            stats=video.stats,
            tags=tags,
            rights=video.rights,
            parts=video.parts,
            raw_metadata=video.raw_metadata,
        )

    async def _get_pgc_video(
        self, reference: VideoReference, cookies: CookieJar | None
    ) -> ProviderVideo:
        if reference.season_id is not None:
            params: dict[str, Any] = {"season_id": reference.season_id}
        elif reference.episode_id is not None:
            params = {"ep_id": reference.episode_id}
        else:
            raise self._upstream_changed()
        response = await self._request_json(
            _PGC_SEASON_API,
            params=params,
            cookies=cookies,
            referer=reference.normalized_url,
        )
        data = self._response_result(response)
        return self._parse_pgc_video(data, selected_episode_id=reference.episode_id)

    async def get_streams(
        self, video: ProviderVideo, part: ProviderPart, cookies: CookieJar | None = None
    ) -> ProviderStreams:
        if video.provider == self.pgc_provider_name:
            episode = self._pgc_episode(video, part)
            params: dict[str, Any] = {
                "ep_id": episode["episodeId"],
                "cid": part.cid,
                "qn": 127,
                "fnver": 0,
                "fnval": 4048,
                "fourk": 1,
            }
            episode_bvid = episode.get("bvid")
            episode_aid = episode.get("aid")
            if isinstance(episode_bvid, str) and episode_bvid:
                params["bvid"] = episode_bvid
            elif isinstance(episode_aid, int):
                params["avid"] = episode_aid
            payload = await self._request_json(
                _PGC_PLAYURL_API,
                params=params,
                cookies=cookies,
                referer=self._part_referer(video, part),
            )
            data = self._response_result(payload)
            nested_code = self._optional_int(data.get("code"))
            if nested_code not in (None, 0):
                self._raise_for_provider_code({"code": nested_code})
            if self._truthy(data.get("is_preview")):
                raise AppError(
                    ErrorCode.AUTH_REQUIRED if cookies is None else ErrorCode.PERMISSION_DENIED,
                    "当前剧集仅返回试看流，无法作为完整媒体处理",
                    action=(
                        "上传并校验有权限的 Cookie 后重试"
                        if cookies is None
                        else "在 Bilibili 官方页面确认当前账号的观看权益"
                    ),
                    status_code=(
                        status.HTTP_401_UNAUTHORIZED
                        if cookies is None
                        else status.HTTP_403_FORBIDDEN
                    ),
                )
        elif video.provider == self.provider_name:
            payload = await self._request_json(
                _PLAYURL_API,
                params={
                    "bvid": video.bvid,
                    "cid": part.cid,
                    "qn": 127,
                    "fnver": 0,
                    "fnval": 4048,
                    "fourk": 1,
                },
                cookies=cookies,
                referer=self._part_referer(video, part),
            )
            data = self._response_data(payload)
        else:
            raise self._upstream_changed()
        return self._parse_streams(data, part)

    def _parse_streams(self, data: Any, part: ProviderPart) -> ProviderStreams:
        if not isinstance(data, dict):
            raise self._upstream_changed()
        dash = data.get("dash")
        if not isinstance(dash, dict):
            raise AppError(
                ErrorCode.UNSUPPORTED_CONTENT,
                "该视频当前未提供可用的 DASH 媒体流",
                action="稍后重新解析，或在官方页面确认视频状态",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        duration = self._number(dash.get("duration")) or float(part.duration)
        descriptions = self._quality_descriptions(data)
        access_requirements = self._quality_access_requirements(data)

        raw_video = dash.get("video")
        raw_audio = dash.get("audio")
        if not isinstance(raw_video, list):
            raise self._upstream_changed()
        if not isinstance(raw_audio, list):
            raw_audio = []

        video_streams = [
            self._parse_stream(
                item,
                kind=StreamKind.VIDEO,
                duration=duration,
                quality_descriptions=descriptions,
                access_requirements=access_requirements,
            )
            for item in raw_video
            if isinstance(item, dict)
        ]
        audio_items = [
            (item, StreamAccessRequirement.NONE) for item in raw_audio if isinstance(item, dict)
        ]
        dolby = dash.get("dolby")
        if isinstance(dolby, dict):
            dolby_audio = dolby.get("audio")
            if isinstance(dolby_audio, list):
                audio_items.extend(
                    (item, StreamAccessRequirement.PREMIUM)
                    for item in dolby_audio
                    if isinstance(item, dict)
                )
        flac = dash.get("flac")
        if isinstance(flac, dict) and isinstance(flac.get("audio"), dict):
            audio_items.append((flac["audio"], StreamAccessRequirement.PREMIUM))
        audio_streams = [
            self._parse_stream(
                item,
                kind=StreamKind.AUDIO,
                duration=duration,
                quality_descriptions=descriptions,
                access_requirements=access_requirements,
                forced_access_requirement=requirement,
            )
            for item, requirement in audio_items
        ]
        if not video_streams:
            raise self._upstream_changed()
        return ProviderStreams(video=video_streams, audio=audio_streams)

    async def get_subtitles(
        self, video: ProviderVideo, part: ProviderPart, cookies: CookieJar | None = None
    ) -> list[ProviderSubtitle]:
        params: dict[str, Any] = {"cid": part.cid}
        if video.provider == self.pgc_provider_name:
            episode = self._pgc_episode(video, part)
            episode_bvid = episode.get("bvid")
            episode_aid = episode.get("aid")
            if isinstance(episode_bvid, str) and episode_bvid:
                params["bvid"] = episode_bvid
            elif isinstance(episode_aid, int):
                params["aid"] = episode_aid
            else:
                raise self._upstream_changed()
        elif video.provider == self.provider_name:
            params["bvid"] = video.bvid
        else:
            raise self._upstream_changed()
        payload = await self._request_json(
            _PLAYER_API,
            params=params,
            cookies=cookies,
            referer=self._part_referer(video, part),
        )
        data = self._response_data(payload)
        if not isinstance(data, dict):
            raise self._upstream_changed()
        subtitle_root = data.get("subtitle")
        if not isinstance(subtitle_root, dict):
            return []
        entries = subtitle_root.get("subtitles")
        if not isinstance(entries, list):
            return []
        subtitles = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            url_value = item.get("subtitle_url")
            if not isinstance(url_value, str):
                continue
            url = self._absolute_https_url(url_value)
            self._validate_resource_url(url, extra_suffixes=("hdslb.com",))
            subtitles.append(
                ProviderSubtitle(
                    subtitle_id=str(item.get("id", "")),
                    language=str(item.get("lan", "und")),
                    language_label=str(item.get("lan_doc", item.get("lan", "未知"))),
                    url=url,
                )
            )
        return subtitles

    async def get_danmaku(self, video: ProviderVideo, part: ProviderPart) -> bytes:
        payload = await self._request_bytes(
            _DANMAKU_API,
            params={"oid": part.cid},
            maximum_bytes=min(self.settings.upstream_max_response_bytes, 8 * 1024 * 1024),
            referer=self._part_referer(video, part),
        )
        self._validate_danmaku_xml(payload)
        return payload

    async def validate_auth(self, cookies: CookieJar) -> AuthValidation:
        payload = await self._request_json(_NAV_API, params={}, cookies=cookies)
        data = self._response_data(payload)
        if not isinstance(data, dict):
            raise self._upstream_changed()
        logged_in = data.get("isLogin") is True
        if not logged_in:
            return AuthValidation(
                logged_in=False,
                account_name=None,
                membership_type="none",
                premium=False,
            )
        vip_status = self._optional_int(data.get("vipStatus")) or 0
        vip_type = self._optional_int(data.get("vipType")) or 0
        premium = vip_status == 1 and vip_type > 0
        if not premium:
            membership = "none"
        elif vip_type == 2:
            membership = "annual_premium"
        else:
            membership = "premium"
        account_name = data.get("uname") if isinstance(data.get("uname"), str) else None
        return AuthValidation(
            logged_in=True,
            account_name=account_name,
            membership_type=membership,
            premium=premium,
        )

    async def verify_stream(self, url: str, cookies: CookieJar | None = None) -> None:
        headers = {
            "Range": "bytes=0-1023",
            "Referer": "https://www.bilibili.com/",
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        }
        try:
            target = await self._media_validator.resolve(url)
        except UnsafeMediaURLError as exc:
            raise self._upstream_changed() from exc

        last_network_error: httpx.HTTPError | None = None
        for address in target.addresses:
            request_headers = {**headers, "Host": target.host_header}
            try:
                await self._wait_for_request_slot()
                async with self._new_client(cookies) as client:
                    async with client.stream(
                        "GET",
                        target.pinned_url(address),
                        headers=request_headers,
                        extensions={"sni_hostname": target.host},
                    ) as response:
                        if response.status_code not in {200, 206}:
                            raise AppError(
                                ErrorCode.PERMISSION_DENIED,
                                "所选媒体流当前无法访问",
                                action="重新解析后选择可访问的规格",
                                status_code=status.HTTP_403_FORBIDDEN,
                                log_context={"upstream_status": response.status_code},
                            )
                        if response.headers.get("Content-Encoding", "identity").lower() not in {
                            "",
                            "identity",
                        }:
                            raise self._upstream_changed()
                        received = 0
                        async for chunk in response.aiter_bytes():
                            received += len(chunk)
                            if received >= 1_024:
                                break
                        if received == 0:
                            raise self._upstream_changed()
                        return
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                last_network_error = exc
        raise self._network_error() from last_network_error

    async def _request_json(
        self,
        url: str,
        *,
        params: dict[str, Any],
        cookies: CookieJar | None,
        referer: str = "https://www.bilibili.com/",
    ) -> dict[str, Any]:
        for attempt in range(self.settings.upstream_retries + 1):
            try:
                await self._wait_for_request_slot()
                async with self._new_client(cookies) as client:
                    async with client.stream(
                        "GET", url, params=params, headers={"Referer": referer}
                    ) as response:
                        if response.status_code in {412, 429}:
                            raise AppError(
                                ErrorCode.RISK_CONTROL,
                                "Bilibili 暂时要求额外验证，本工具不会绕过",
                                action="稍后重试，或前往官方页面完成验证",
                                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                log_context={"upstream_status": response.status_code},
                            )
                        if response.status_code >= 500 and attempt < self.settings.upstream_retries:
                            await response.aread()
                            await asyncio.sleep(0.15 * (2**attempt))
                            continue
                        if response.status_code != 200:
                            raise self._network_error(response.status_code)
                        body = bytearray()
                        content_length = response.headers.get("Content-Length")
                        if content_length:
                            try:
                                declared_length = int(content_length)
                            except ValueError as exc:
                                raise self._upstream_changed() from exc
                            if declared_length > self.settings.upstream_max_response_bytes:
                                raise self._upstream_changed()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > self.settings.upstream_max_response_bytes:
                                raise self._upstream_changed()
                try:
                    decoded = orjson.loads(body)
                except orjson.JSONDecodeError as exc:
                    raise self._upstream_changed() from exc
                if not isinstance(decoded, dict):
                    raise self._upstream_changed()
                self._raise_for_provider_code(decoded)
                return decoded
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt < self.settings.upstream_retries:
                    await asyncio.sleep(0.15 * (2**attempt))
                    continue
                raise self._network_error() from exc
        raise self._network_error()

    async def _request_bytes(
        self,
        url: str,
        *,
        params: dict[str, Any],
        maximum_bytes: int,
        referer: str,
    ) -> bytes:
        if url != _DANMAKU_API or not 1 <= maximum_bytes <= 8 * 1024 * 1024:
            raise self._upstream_changed()
        for attempt in range(self.settings.upstream_retries + 1):
            try:
                await self._wait_for_request_slot()
                async with self._new_client(None) as client:
                    async with client.stream(
                        "GET", url, params=params, headers={"Referer": referer}
                    ) as response:
                        if response.status_code in {412, 429}:
                            raise AppError(
                                ErrorCode.RISK_CONTROL,
                                "Bilibili 暂时要求额外验证，本工具不会绕过",
                                action="稍后重试，或前往官方页面完成验证",
                                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                log_context={"upstream_status": response.status_code},
                            )
                        if response.status_code >= 500 and attempt < self.settings.upstream_retries:
                            await response.aread()
                            await asyncio.sleep(0.15 * (2**attempt))
                            continue
                        if response.status_code != 200:
                            raise self._network_error(response.status_code)
                        content_length = response.headers.get("Content-Length")
                        if content_length is not None:
                            try:
                                declared_length = int(content_length)
                            except ValueError as exc:
                                raise self._upstream_changed() from exc
                            if declared_length < 0 or declared_length > maximum_bytes:
                                raise self._upstream_changed()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > maximum_bytes:
                                raise self._upstream_changed()
                        return bytes(body)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt < self.settings.upstream_retries:
                    await asyncio.sleep(0.15 * (2**attempt))
                    continue
                raise self._network_error() from exc
        raise self._network_error()

    @staticmethod
    def _validate_danmaku_xml(payload: bytes) -> None:
        try:
            validate_danmaku_xml(payload)
        except DanmakuValidationError as exc:
            raise BilibiliProvider._upstream_changed() from exc

    async def _wait_for_request_slot(self) -> None:
        async with self._request_gate:
            interval = self._request_interval_seconds
            if interval > 0:
                remaining = interval - (self._clock() - self._last_request_at)
                if remaining > 0:
                    await self._sleep(remaining)
            self._last_request_at = self._clock()

    def _new_client(self, cookies: CookieJar | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self.transport,
            cookies=cookies,
            timeout=self.timeout,
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )

    def _parse_video(self, data: Any) -> ProviderVideo:
        if not isinstance(data, dict):
            raise self._upstream_changed()
        required = ("bvid", "aid", "title", "pic", "owner", "duration", "pages")
        if any(key not in data for key in required):
            raise self._upstream_changed()
        owner = data["owner"]
        pages = data["pages"]
        if not isinstance(owner, dict) or not isinstance(pages, list) or not pages:
            raise self._upstream_changed()
        parts = []
        for item in pages:
            if not isinstance(item, dict):
                raise self._upstream_changed()
            try:
                parts.append(
                    ProviderPart(
                        cid=int(item["cid"]),
                        page_number=int(item["page"]),
                        title=str(item.get("part") or f"P{item['page']}"),
                        duration=int(item.get("duration") or 0),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise self._upstream_changed() from exc
        owner = cast(dict[str, Any], owner)
        stat = cast(dict[str, Any], data.get("stat") if isinstance(data.get("stat"), dict) else {})
        rights_value = cast(
            dict[str, Any],
            data.get("rights") if isinstance(data.get("rights"), dict) else {},
        )
        copyright_code = self._optional_int(rights_value.get("copyright"))
        copyright_labels = {
            1: "原创",
            2: "转载",
        }
        copyright_label = (
            copyright_labels.get(copyright_code) if copyright_code is not None else None
        )
        if copyright_label is None and isinstance(rights_value.get("copyright"), str):
            copyright_label = str(rights_value["copyright"])
        rights: dict[str, bool | int | str | None] = {
            "copyright": copyright_label,
            "copyrightCode": copyright_code,
            "isPaid": any(
                self._truthy(rights_value.get(key))
                for key in ("pay", "ugc_pay", "arc_pay", "is_paid")
            ),
            "isPremiumOnly": any(
                self._truthy(rights_value.get(key))
                for key in ("is_vip_only", "vip_only", "only_vip")
            ),
        }
        pubdate = self._optional_int(data.get("pubdate"))
        return ProviderVideo(
            provider=self.provider_name,
            bvid=str(data["bvid"]),
            aid=int(data["aid"]),
            title=str(data["title"]),
            description=str(data.get("desc") or ""),
            cover_url=self._absolute_https_url(str(data["pic"])),
            owner_name=str(owner.get("name") or "未知 UP 主"),
            duration=int(data["duration"]),
            published_at=datetime.fromtimestamp(pubdate, tz=UTC) if pubdate else None,
            stats={
                "views": self._optional_int(stat.get("view")),
                "likes": self._optional_int(stat.get("like")),
                "favorites": self._optional_int(stat.get("favorite")),
                "danmaku": self._optional_int(stat.get("danmaku")),
                "coins": self._optional_int(stat.get("coin")),
                "shares": self._optional_int(stat.get("share")),
            },
            tags=[],
            rights=rights,
            parts=parts,
            raw_metadata=data,
        )

    def _parse_pgc_video(self, data: Any, *, selected_episode_id: int | None) -> ProviderVideo:
        if not isinstance(data, dict):
            raise self._upstream_changed()
        season_id = self._optional_int(data.get("season_id"))
        title = data.get("title")
        cover = data.get("cover")
        if season_id is None or season_id <= 0 or not isinstance(title, str):
            raise self._upstream_changed()
        if not isinstance(cover, str) or not cover:
            raise self._upstream_changed()

        episode_items = self._pgc_episode_items(data, selected_episode_id)
        parts: list[ProviderPart] = []
        safe_episodes: list[dict[str, Any]] = []
        for page_number, item in enumerate(episode_items, start=1):
            episode_id = self._optional_int(item.get("ep_id", item.get("id")))
            cid = self._optional_int(item.get("cid"))
            if episode_id is None or episode_id <= 0 or cid is None or cid <= 0:
                continue
            duration = self._pgc_duration_seconds(item.get("duration"))
            short_title = str(item.get("title") or "").strip()
            long_title = str(item.get("long_title") or "").strip()
            show_title = str(item.get("show_title") or "").strip()
            part_title = " ".join(value for value in (short_title, long_title) if value)
            part_title = part_title or show_title or f"第 {page_number} 集"
            episode_bvid = item.get("bvid")
            if not isinstance(episode_bvid, str) or not _BVID.fullmatch(episode_bvid):
                episode_bvid = None
            episode_aid = self._optional_int(item.get("aid"))
            if episode_aid is not None and not 0 < episode_aid <= _MAX_AID:
                episode_aid = None
            badge_info = item.get("badge_info")
            badge = item.get("badge") if isinstance(item.get("badge"), str) else ""
            if isinstance(badge_info, dict) and isinstance(badge_info.get("text"), str):
                badge = str(badge_info["text"])
            episode_cover = item.get("cover")
            safe_episodes.append(
                {
                    "episodeId": episode_id,
                    "cid": cid,
                    "aid": episode_aid,
                    "bvid": episode_bvid,
                    "pageNumber": page_number,
                    "title": part_title,
                    "duration": duration,
                    "coverUrl": (
                        self._absolute_https_url(episode_cover)
                        if isinstance(episode_cover, str) and episode_cover
                        else None
                    ),
                    "publishedAt": self._optional_int(item.get("pub_time")),
                    "badge": badge,
                    "sectionEpisode": self._truthy(item.get("_bili_insight_section_episode")),
                }
            )
            parts.append(
                ProviderPart(
                    cid=cid,
                    page_number=page_number,
                    title=part_title,
                    duration=duration,
                )
            )
        if not parts:
            raise self._upstream_changed()
        if selected_episode_id is not None and not any(
            item["episodeId"] == selected_episode_id for item in safe_episodes
        ):
            raise AppError(
                ErrorCode.VIDEO_NOT_FOUND,
                "剧集不存在、已删除或当前不可见",
                action="确认 ep 链接后重试，或在官方番剧页面选择其他剧集",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        stat = cast(dict[str, Any], data.get("stat") if isinstance(data.get("stat"), dict) else {})
        up_info = cast(
            dict[str, Any], data.get("up_info") if isinstance(data.get("up_info"), dict) else {}
        )
        publish = cast(
            dict[str, Any], data.get("publish") if isinstance(data.get("publish"), dict) else {}
        )
        season_rights = cast(
            dict[str, Any], data.get("rights") if isinstance(data.get("rights"), dict) else {}
        )
        payment = cast(
            dict[str, Any], data.get("payment") if isinstance(data.get("payment"), dict) else {}
        )
        price = payment.get("price")
        try:
            is_paid = float(price) > 0 if isinstance(price, str | int | float) else False
        except ValueError:
            is_paid = False
        badges = [str(item.get("badge") or "") for item in safe_episodes]
        is_premium_only = any("会员" in badge or "vip" in badge.casefold() for badge in badges)
        copyright_value = season_rights.get("copyright")
        copyright_label = (
            {"dujia": "独家", "bilibili": "哔哩哔哩"}.get(copyright_value, copyright_value)
            if isinstance(copyright_value, str)
            else None
        )
        tags: list[str] = []
        for collection_name in ("styles", "areas"):
            collection = data.get(collection_name)
            if not isinstance(collection, list):
                continue
            for item in collection:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    name = str(item["name"]).strip()
                    if name and name not in tags:
                        tags.append(name)

        published_at = self._parse_pgc_datetime(publish.get("pub_time"))
        media_id = self._optional_int(data.get("media_id"))
        raw_metadata = {
            "contentType": "bangumi",
            "seasonId": season_id,
            "mediaId": media_id,
            "seasonType": self._optional_int(data.get("type")),
            "seasonTypeName": (
                str(data["show_season_type"])
                if isinstance(data.get("show_season_type"), str)
                else None
            ),
            "episodes": safe_episodes,
        }
        return ProviderVideo(
            provider=self.pgc_provider_name,
            bvid=f"SS{season_id}",
            aid=season_id,
            title=title,
            description=str(data.get("evaluate") or ""),
            cover_url=self._absolute_https_url(cover),
            owner_name=str(up_info.get("uname") or "哔哩哔哩番剧"),
            duration=sum(part.duration for part in parts),
            published_at=published_at,
            stats={
                "views": self._optional_int(stat.get("views")),
                "likes": self._optional_int(stat.get("likes")),
                "favorites": self._optional_int(stat.get("favorites", stat.get("favorite"))),
                "danmaku": self._optional_int(stat.get("danmakus")),
                "coins": self._optional_int(stat.get("coins")),
                "shares": self._optional_int(stat.get("share")),
            },
            tags=tags,
            rights={
                "copyright": copyright_label,
                "copyrightCode": None,
                "isPaid": is_paid,
                "isPremiumOnly": is_premium_only,
                "contentType": "bangumi",
                "seasonId": season_id,
                "mediaId": media_id,
            },
            parts=parts,
            raw_metadata=raw_metadata,
        )

    @classmethod
    def _pgc_episode_items(
        cls, data: dict[str, Any], selected_episode_id: int | None
    ) -> list[dict[str, Any]]:
        raw_episodes = data.get("episodes")
        episodes = (
            [item for item in raw_episodes if isinstance(item, dict)]
            if isinstance(raw_episodes, list)
            else []
        )
        if selected_episode_id is not None and not any(
            cls._optional_int(item.get("ep_id", item.get("id"))) == selected_episode_id
            for item in episodes
        ):
            sections = data.get("section")
            if isinstance(sections, list):
                for section in sections:
                    if not isinstance(section, dict) or not isinstance(
                        section.get("episodes"), list
                    ):
                        continue
                    selected = next(
                        (
                            item
                            for item in section["episodes"]
                            if isinstance(item, dict)
                            and cls._optional_int(item.get("ep_id", item.get("id")))
                            == selected_episode_id
                        ),
                        None,
                    )
                    if selected is not None:
                        episodes.append({**selected, "_bili_insight_section_episode": True})
                        break
        if not episodes:
            sections = data.get("section")
            if isinstance(sections, list):
                for section in sections:
                    if isinstance(section, dict) and isinstance(section.get("episodes"), list):
                        episodes.extend(
                            {**item, "_bili_insight_section_episode": True}
                            for item in section["episodes"]
                            if isinstance(item, dict)
                        )
        deduplicated: list[dict[str, Any]] = []
        seen_episode_ids: set[int] = set()
        seen_cids: set[int] = set()
        for item in episodes:
            episode_id = cls._optional_int(item.get("ep_id", item.get("id")))
            cid = cls._optional_int(item.get("cid"))
            if (episode_id is not None and episode_id in seen_episode_ids) or (
                cid is not None and cid in seen_cids
            ):
                continue
            if episode_id is not None:
                seen_episode_ids.add(episode_id)
            if cid is not None:
                seen_cids.add(cid)
            deduplicated.append(item)
        return deduplicated

    @staticmethod
    def _pgc_duration_seconds(value: Any) -> int:
        duration = BilibiliProvider._optional_int(value)
        if duration is None or duration <= 0:
            return 0
        return math.ceil(duration / 1_000) if duration > 10_000 else duration

    @staticmethod
    def _parse_pgc_datetime(value: Any) -> datetime | None:
        timestamp = BilibiliProvider._optional_int(value)
        if timestamp is not None and timestamp > 0:
            try:
                return datetime.fromtimestamp(timestamp, tz=UTC)
            except (OSError, OverflowError, ValueError):
                return None
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        return parsed.replace(tzinfo=UTC)

    @classmethod
    def _pgc_episode(cls, video: ProviderVideo, part: ProviderPart) -> dict[str, Any]:
        if video.raw_metadata.get("contentType") != "bangumi":
            raise cls._upstream_changed()
        episodes = video.raw_metadata.get("episodes")
        if not isinstance(episodes, list):
            raise cls._upstream_changed()
        episode = next(
            (
                item
                for item in episodes
                if isinstance(item, dict) and cls._optional_int(item.get("cid")) == part.cid
            ),
            None,
        )
        if episode is None or cls._optional_int(episode.get("episodeId")) is None:
            raise cls._upstream_changed()
        return episode

    @classmethod
    def _part_referer(cls, video: ProviderVideo, part: ProviderPart) -> str:
        if video.provider == cls.pgc_provider_name:
            episode = cls._pgc_episode(video, part)
            return f"https://www.bilibili.com/bangumi/play/ep{episode['episodeId']}"
        return f"https://www.bilibili.com/video/{video.bvid}/?p={part.page_number}"

    def _parse_stream(
        self,
        item: dict[str, Any],
        *,
        kind: StreamKind,
        duration: float,
        quality_descriptions: dict[int, str],
        access_requirements: dict[int, StreamAccessRequirement] | None = None,
        forced_access_requirement: StreamAccessRequirement | None = None,
    ) -> ProviderStream:
        url_value = item.get("baseUrl", item.get("base_url"))
        if not isinstance(url_value, str):
            raise self._upstream_changed()
        url = self._absolute_https_url(url_value)
        self._validate_resource_url(url)
        backup_value = item.get("backupUrl", item.get("backup_url", []))
        backup_urls: list[str] = []
        if isinstance(backup_value, list):
            for candidate in backup_value:
                if isinstance(candidate, str):
                    absolute = self._absolute_https_url(candidate)
                    self._validate_resource_url(absolute)
                    backup_urls.append(absolute)
        quality = self._optional_int(item.get("id"))
        if quality is None:
            raise self._upstream_changed()
        raw_codec = str(item.get("codecs") or "unknown")
        codec = self._codec_label(raw_codec, kind)
        bitrate = self._optional_int(item.get("bandwidth"))
        estimated_size = math.ceil(bitrate * max(duration, 0) / 8) if bitrate is not None else None
        mime_type = str(item.get("mimeType", item.get("mime_type", "application/octet-stream")))
        container = mime_type.partition("/")[2].partition(";")[0] or "unknown"
        segment_base = item.get("segmentBase", item.get("segment_base"))
        initialization_range: tuple[int, int] | None = None
        index_range: tuple[int, int] | None = None
        if isinstance(segment_base, dict):
            initialization_range = self._dash_byte_range(segment_base.get("initialization"))
            index_range = self._dash_byte_range(
                segment_base.get("indexRange", segment_base.get("index_range"))
            )
        width = self._optional_int(item.get("width")) if kind == StreamKind.VIDEO else None
        height = self._optional_int(item.get("height")) if kind == StreamKind.VIDEO else None
        frame_rate = item.get("frameRate", item.get("frame_rate"))
        fps = self._parse_fps(frame_rate) if kind == StreamKind.VIDEO else None
        codecid = self._optional_int(item.get("codecid"))
        source_key = (
            f"{kind.value}:{quality}:{codecid or raw_codec}:{width or 0}x{height or 0}:"
            f"{bitrate or 0}"
        )
        return ProviderStream(
            source_key=source_key,
            kind=kind,
            quality_code=quality,
            quality_label=(
                quality_descriptions.get(quality)
                or _QUALITY_LABELS.get(quality)
                or (f"音频 {round(bitrate / 1000)} kbps" if bitrate else f"规格 {quality}")
            ),
            codec=codec,
            container=container,
            width=width,
            height=height,
            fps=fps,
            bitrate=bitrate,
            hdr_type=self._hdr_type(quality) if kind == StreamKind.VIDEO else None,
            audio_channels=None,
            sample_rate=None,
            estimated_size=estimated_size,
            access_requirement=(
                forced_access_requirement
                or (access_requirements or {}).get(quality, StreamAccessRequirement.NONE)
            ),
            compatibility=self._compatibility(codec),
            url=url,
            mime_type=mime_type if len(mime_type) <= 64 else None,
            codec_string=raw_codec if len(raw_codec) <= 128 else None,
            init_range_start=initialization_range[0] if initialization_range else None,
            init_range_end=initialization_range[1] if initialization_range else None,
            index_range_start=index_range[0] if index_range else None,
            index_range_end=index_range[1] if index_range else None,
            backup_urls=tuple(backup_urls),
        )

    @staticmethod
    def _dash_byte_range(value: Any) -> tuple[int, int] | None:
        if not isinstance(value, str) or len(value) > 64:
            return None
        match = _DASH_BYTE_RANGE.fullmatch(value.strip())
        if match is None:
            return None
        start = int(match.group(1))
        end = int(match.group(2))
        if end < start or end > _MAX_DASH_METADATA_OFFSET:
            return None
        return start, end

    def _validate_resource_url(self, url: str, *, extra_suffixes: tuple[str, ...] = ()) -> None:
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise self._upstream_changed() from exc
        if parsed.scheme != "https" or parsed.username or parsed.password:
            raise self._upstream_changed()
        host = (parsed.hostname or "").lower().rstrip(".")
        suffixes = (*self.settings.media_host_suffixes, *extra_suffixes)
        if not any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes):
            raise self._upstream_changed()
        is_bilibili_media_cdn = host == "bilivideo.cn" or host.endswith(".bilivideo.cn")
        is_pgc_media_cdn = host == _PGC_CDN_SUFFIX or host.endswith(f".{_PGC_CDN_SUFFIX}")
        special_port_allowed = (port == 8082 and is_bilibili_media_cdn) or (
            port == _PGC_CDN_HTTPS_PORT and is_pgc_media_cdn
        )
        if port not in (None, 443) and not special_port_allowed:
            raise self._upstream_changed()

    @staticmethod
    def _quality_descriptions(data: dict[str, Any]) -> dict[int, str]:
        qualities = data.get("accept_quality")
        descriptions = data.get("accept_description")
        if not isinstance(qualities, list) or not isinstance(descriptions, list):
            return {}
        result = {}
        for quality, description in zip(qualities, descriptions, strict=False):
            if isinstance(quality, int) and isinstance(description, str):
                result[quality] = description
        return result

    @classmethod
    def _quality_access_requirements(
        cls, data: dict[str, Any]
    ) -> dict[int, StreamAccessRequirement]:
        formats = data.get("support_formats")
        if not isinstance(formats, list):
            return {}
        result: dict[int, StreamAccessRequirement] = {}
        for item in formats:
            if not isinstance(item, dict):
                continue
            quality = cls._optional_int(item.get("quality"))
            if quality is None:
                continue
            if cls._truthy(item.get("need_vip", item.get("needVip"))):
                result[quality] = StreamAccessRequirement.PREMIUM
            elif cls._truthy(item.get("need_login", item.get("needLogin"))):
                result[quality] = StreamAccessRequirement.LOGIN
            else:
                result.setdefault(quality, StreamAccessRequirement.NONE)
        return result

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes"}
        return False

    @staticmethod
    def _codec_label(codec: str, kind: StreamKind) -> str:
        lowered = codec.lower()
        if lowered.startswith(("avc", "h264")):
            return "H.264/AVC"
        if lowered.startswith(("hev", "hvc", "h265")):
            return "H.265/HEVC"
        if lowered.startswith("av01"):
            return "AV1"
        if lowered.startswith("mp4a"):
            return "AAC"
        if lowered.startswith(("ec-3", "eac3")):
            return "Dolby E-AC-3"
        if lowered.startswith("fLaC".lower()):
            return "FLAC"
        if codec != "unknown":
            return codec
        return "未知视频编码" if kind == StreamKind.VIDEO else "未知音频编码"

    @staticmethod
    def _compatibility(codec: str) -> str:
        if codec in {"H.264/AVC", "AAC"}:
            return "兼容性最佳，适合主流浏览器、手机和电视"
        if codec == "H.265/HEVC":
            return "压缩效率较高，部分浏览器或旧设备可能无法播放"
        if codec == "AV1":
            return "压缩效率最高，需要较新的硬件或播放器"
        if codec in {"FLAC", "Dolby E-AC-3"}:
            return "高质量音频，部分移动设备或浏览器支持有限"
        return "兼容性取决于目标播放器"

    @staticmethod
    def _hdr_type(quality: int) -> str:
        if quality == 126:
            return "Dolby Vision"
        if quality == 125:
            return "HDR"
        return "SDR"

    @staticmethod
    def _parse_fps(value: Any) -> float | None:
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value) if value > 0 else None
        if not isinstance(value, str) or not value:
            return None
        try:
            if "/" in value:
                numerator, denominator = value.split("/", maxsplit=1)
                denominator_value = float(denominator)
                return float(numerator) / denominator_value if denominator_value else None
            result = float(value)
            return result if result > 0 else None
        except ValueError:
            return None

    @staticmethod
    def _number(value: Any) -> float | None:
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        return None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return None

    @staticmethod
    def _absolute_https_url(value: str) -> str:
        if value.startswith("//"):
            return f"https:{value}"
        if value.startswith("http://"):
            return f"https://{value[7:]}"
        return value

    @staticmethod
    def _response_data(response: dict[str, Any]) -> Any:
        if "data" not in response:
            raise BilibiliProvider._upstream_changed()
        return response["data"]

    @staticmethod
    def _response_result(response: dict[str, Any]) -> dict[str, Any]:
        result = response.get("result")
        if not isinstance(result, dict):
            raise BilibiliProvider._upstream_changed()
        return result

    @staticmethod
    def _raise_for_provider_code(response: dict[str, Any]) -> None:
        code = response.get("code")
        if code == 0:
            return
        if code in {-404, 62002, 62004}:
            raise AppError(
                ErrorCode.VIDEO_NOT_FOUND,
                "视频不存在、已删除或当前不可见",
                action="确认链接后重试，或在官方页面查看视频状态",
                status_code=status.HTTP_404_NOT_FOUND,
                log_context={"provider_code": code},
            )
        if code in {-101, -111}:
            raise AppError(
                ErrorCode.AUTH_REQUIRED,
                "当前内容需要有效登录态",
                action="上传或重新校验 Cookie，也可选择匿名可用内容",
                status_code=status.HTTP_401_UNAUTHORIZED,
                log_context={"provider_code": code},
            )
        if code in {-403, 10015002}:
            raise AppError(
                ErrorCode.PERMISSION_DENIED,
                "当前账号无权访问该内容或规格",
                action="确认账号权益，或选择匿名可访问规格",
                status_code=status.HTTP_403_FORBIDDEN,
                log_context={"provider_code": code},
            )
        if code in {-10403, 6002105}:
            raise AppError(
                ErrorCode.REGION_RESTRICTED,
                "该内容受地区或版权限制，当前无法访问",
                action="在 Bilibili 官方页面确认可用地区",
                status_code=status.HTTP_451_UNAVAILABLE_FOR_LEGAL_REASONS,
                log_context={"provider_code": code},
            )
        if code in {-352, -412}:
            raise AppError(
                ErrorCode.RISK_CONTROL,
                "Bilibili 暂时要求额外验证，本工具不会绕过",
                action="稍后重试，或前往官方页面完成验证",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                log_context={"provider_code": code},
            )
        raise AppError(
            ErrorCode.UPSTREAM_CHANGED,
            "Bilibili 返回了暂时无法识别的结果",
            action="稍后重新解析；若持续失败，请更新应用",
            status_code=status.HTTP_502_BAD_GATEWAY,
            log_context={"provider_code": code},
        )

    @staticmethod
    def _invalid_link() -> AppError:
        return AppError(
            ErrorCode.INVALID_LINK,
            "无法识别该链接，请使用 BV/AV 视频或番剧 ss/ep 链接",
            action="输入视频标识，或复制 Bilibili 视频/番剧的完整 HTTPS 链接",
        )

    @staticmethod
    def _upstream_changed() -> AppError:
        return AppError(
            ErrorCode.UPSTREAM_CHANGED,
            "Bilibili 返回的数据结构已变化，暂时无法解析",
            action="稍后重试；若持续失败，请更新应用",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    @staticmethod
    def _network_error(upstream_status: int | None = None) -> AppError:
        return AppError(
            ErrorCode.UPSTREAM_NETWORK,
            "暂时无法连接 Bilibili",
            action="检查网络后重试",
            status_code=status.HTTP_502_BAD_GATEWAY,
            log_context={"upstream_status": upstream_status} if upstream_status else {},
        )
