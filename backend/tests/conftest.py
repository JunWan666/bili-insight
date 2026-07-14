from __future__ import annotations

import json
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from app.core.config import Settings
from app.main import create_app
from app.providers.bilibili import BilibiliProvider

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


async def fixture_media_resolver(_host: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


@dataclass(slots=True)
class UpstreamFixtureServer:
    counts: Counter[str] = field(default_factory=Counter)
    cookie_headers: list[tuple[str, str]] = field(default_factory=list)
    force_invalid_auth: bool = False
    force_auth_network_error: bool = False
    force_pgc_anonymous_preview: bool = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.counts[path] += 1
        cookie_header = request.headers.get("Cookie", "")
        self.cookie_headers.append((path, cookie_header))
        if path == "/x/web-interface/nav":
            if self.force_auth_network_error:
                raise httpx.ConnectError("fixed auth network error", request=request)
            fixture = (
                "nav_valid.json"
                if ("SESSDATA=test-session-value" in cookie_header and not self.force_invalid_auth)
                else "nav_invalid.json"
            )
            return httpx.Response(200, json=load_fixture(fixture), request=request)
        if path == "/x/web-interface/view":
            return httpx.Response(200, json=load_fixture("view.json"), request=request)
        if path == "/pgc/view/web/season":
            return httpx.Response(200, json=load_fixture("pgc_season.json"), request=request)
        if path == "/x/tag/archive/tags":
            return httpx.Response(200, json=load_fixture("tags.json"), request=request)
        if path == "/x/player/playurl":
            fixture = (
                "playurl_authenticated.json"
                if "SESSDATA=test-session-value" in cookie_header
                else "playurl_anonymous.json"
            )
            return httpx.Response(200, json=load_fixture(fixture), request=request)
        if path == "/pgc/player/web/playurl":
            if (
                self.force_pgc_anonymous_preview
                and "SESSDATA=test-session-value" not in cookie_header
            ):
                preview = load_fixture("pgc_playurl_anonymous.json")
                result = preview.get("result")
                if isinstance(result, dict):
                    result["is_preview"] = 1
                return httpx.Response(200, json=preview, request=request)
            fixture = (
                "pgc_playurl_authenticated.json"
                if "SESSDATA=test-session-value" in cookie_header
                else "pgc_playurl_anonymous.json"
            )
            return httpx.Response(200, json=load_fixture(fixture), request=request)
        if path == "/x/player/v2":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "subtitle": {
                            "subtitles": [
                                {
                                    "id": 1,
                                    "lan": "zh-CN",
                                    "lan_doc": "中文（简体）",
                                    "subtitle_url": "//aisubtitle.hdslb.com/sample.json",
                                }
                            ]
                        }
                    },
                },
                request=request,
            )
        if path == "/x/v1/dm/list.so":
            return httpx.Response(
                200,
                headers={"Content-Type": "application/xml; charset=utf-8"},
                content=(
                    b'<?xml version="1.0" encoding="UTF-8"?>'
                    b"<i><chatserver>fixture</chatserver>"
                    b'<d p="1.25,1,25,16777215,0,0,fixture,1">fixed danmaku</d></i>'
                ),
                request=request,
            )
        request_host = (request.headers.get("Host") or request.url.host or "").split(":", 1)[0]
        if request_host.endswith(("bilivideo.com", "bilivideo.cn", "edge.mountaintoys.cn")):
            return httpx.Response(
                206,
                headers={"Content-Range": "bytes 0-1023/4096"},
                stream=httpx.ByteStream(b"0" * 1_024),
                request=request,
            )
        return httpx.Response(404, json={"code": -404}, request=request)


@pytest.fixture
def upstream() -> UpstreamFixtureServer:
    return UpstreamFixtureServer()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    key = Fernet.generate_key().decode("ascii")
    return Settings(
        environment="test",
        host="127.0.0.1",
        network_mode="local",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        data_dir=tmp_path / "data",
        artifact_dir=tmp_path / "artifacts",
        temp_dir=tmp_path / "tmp",
        log_dir=tmp_path / "logs",
        cookie_encryption_key=key,
        auto_create_schema=True,
        log_json=False,
        upstream_retries=0,
        cors_origins="",
    )


@pytest_asyncio.fixture
async def api_client(
    settings: Settings,
    upstream: UpstreamFixtureServer,
) -> AsyncIterator[tuple[httpx.AsyncClient, object]]:
    transport = httpx.MockTransport(upstream.handle)
    provider = BilibiliProvider(
        settings,
        transport=transport,
        media_resolver=fixture_media_resolver,
    )
    app = create_app(
        settings,
        provider=provider,
        transport=transport,
        media_resolver=fixture_media_resolver,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, app


@pytest.fixture
def valid_cookie_json() -> bytes:
    return json.dumps(
        [
            {
                "name": "SESSDATA",
                "value": "test-session-value",
                "domain": ".bilibili.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "expires": 4_102_444_800,
            },
            {
                "name": "bili_jct",
                "value": "test-csrf-value",
                "domain": ".bilibili.com",
                "path": "/",
                "secure": True,
                "expires": 0,
            },
            {
                "name": "unrelated",
                "value": "discarded-value",
                "domain": ".example.com",
                "path": "/",
                "secure": True,
                "expires": 4_102_444_800,
            },
        ]
    ).encode()
