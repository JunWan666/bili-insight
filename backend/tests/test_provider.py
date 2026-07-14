from __future__ import annotations

from http.cookiejar import CookieJar

import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.db.models import StreamAccessRequirement, StreamKind
from app.providers.bilibili import BilibiliProvider
from app.providers.models import ProviderPart, ProviderVideo
from app.security.cookies import CookieFileParser
from tests.conftest import UpstreamFixtureServer


async def public_media_resolver(_host: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


@pytest.fixture
def provider(settings: Settings, upstream: UpstreamFixtureServer) -> BilibiliProvider:
    return BilibiliProvider(
        settings,
        transport=httpx.MockTransport(upstream.handle),
        media_resolver=public_media_resolver,
    )


def provider_video() -> ProviderVideo:
    return ProviderVideo(
        provider="bilibili",
        bvid="BV1FYT5zkE1q",
        aid=1,
        title="fixed",
        description="",
        cover_url="https://i0.hdslb.com/fixed.jpg",
        owner_name="fixed",
        duration=10,
        published_at=None,
        stats={},
        tags=[],
        rights={},
        parts=[],
        raw_metadata={},
    )


@pytest.mark.parametrize(
    ("value", "expected_bvid", "expected_aid", "expected_page"),
    [
        ("BV1FYT5zkE1q", "BV1FYT5zkE1q", None, 1),
        ("av114857171391061", None, 114857171391061, 1),
        (
            "https://www.bilibili.com/video/BV1FYT5zkE1q/"
            "?spm_id_from=333.337&p=2&vd_source=removed",
            "BV1FYT5zkE1q",
            None,
            2,
        ),
    ],
)
async def test_normalize_url(
    provider: BilibiliProvider,
    value: str,
    expected_bvid: str | None,
    expected_aid: int | None,
    expected_page: int,
) -> None:
    result = await provider.normalize_url(value)
    assert result.bvid == expected_bvid
    assert result.aid == expected_aid
    assert result.page_number == expected_page
    assert "spm_id_from" not in result.normalized_url
    assert "vd_source" not in result.normalized_url


@pytest.mark.parametrize(
    ("value", "expected_season", "expected_episode", "expected_url"),
    [
        (
            "https://www.bilibili.com/bangumi/play/ss28747?from_spmid=tracking",
            28747,
            None,
            "https://www.bilibili.com/bangumi/play/ss28747",
        ),
        (
            "https://m.bilibili.com/bangumi/play/ep733317?from_spmid=tracking",
            None,
            733317,
            "https://www.bilibili.com/bangumi/play/ep733317",
        ),
        ("ss28747", 28747, None, "https://www.bilibili.com/bangumi/play/ss28747"),
        ("ep733317", None, 733317, "https://www.bilibili.com/bangumi/play/ep733317"),
    ],
)
async def test_normalize_bangumi_urls(
    provider: BilibiliProvider,
    value: str,
    expected_season: int | None,
    expected_episode: int | None,
    expected_url: str,
) -> None:
    result = await provider.normalize_url(value)

    assert result.provider == "bilibili_pgc"
    assert result.season_id == expected_season
    assert result.episode_id == expected_episode
    assert result.normalized_url == expected_url
    assert "from_spmid" not in result.normalized_url


@pytest.mark.parametrize(
    "value",
    [
        "http://www.bilibili.com/video/BV1FYT5zkE1q",
        "https://evil.example/video/BV1FYT5zkE1q",
        "https://127.0.0.1/video/BV1FYT5zkE1q",
        "file:///etc/passwd",
        "https://www.bilibili.com@evil.example/video/BV1FYT5zkE1q",
        "av99999999999999999999",
    ],
)
async def test_normalize_url_blocks_ssrf_inputs(provider: BilibiliProvider, value: str) -> None:
    with pytest.raises(AppError) as caught:
        await provider.normalize_url(value)
    assert caught.value.code in {ErrorCode.INVALID_LINK, ErrorCode.UNSUPPORTED_CONTENT}


async def test_provider_parses_metadata_streams_and_subtypes(
    provider: BilibiliProvider,
) -> None:
    reference = await provider.normalize_url("https://www.bilibili.com/video/BV1FYT5zkE1q?p=2")
    video = await provider.get_video(reference)
    assert video.bvid == "BV1FYT5zkE1q"
    assert video.title == "固定响应解析样例"
    assert video.cover_url == "https://i0.hdslb.com/bfs/archive/sample.jpg"
    assert video.tags == ["动画", "剧情"]
    assert video.rights == {
        "copyright": "原创",
        "copyrightCode": 1,
        "isPaid": False,
        "isPremiumOnly": False,
    }
    assert len(video.parts) == 2
    streams = await provider.get_streams(video, video.parts[1])
    assert {stream.codec for stream in streams.video} == {"H.264/AVC", "H.265/HEVC"}
    assert all(stream.quality_code == 64 for stream in streams.video)
    assert streams.video[0].estimated_size is not None
    assert streams.audio[0].codec == "AAC"
    assert "token=" not in repr(streams.video[0]) or streams.video[0].url.startswith("https://")


async def test_authenticated_provider_adds_member_streams(
    provider: BilibiliProvider, valid_cookie_json: bytes
) -> None:
    parsed = CookieFileParser(max_bytes=1_048_576, max_items=100).parse(valid_cookie_json)
    validation = await provider.validate_auth(parsed.jar)
    assert validation.logged_in is True
    assert validation.premium is True
    assert validation.membership_type == "annual_premium"
    reference = await provider.normalize_url("BV1FYT5zkE1q")
    video = await provider.get_video(reference, parsed.jar)
    streams = await provider.get_streams(video, video.parts[0], parsed.jar)
    member_streams = [item for item in streams.video if item.quality_code == 112]
    assert {item.codec for item in member_streams} == {
        "H.264/AVC",
        "H.265/HEVC",
        "AV1",
    }
    assert all(
        item.access_requirement == StreamAccessRequirement.PREMIUM for item in member_streams
    )
    assert all(
        item.access_requirement == StreamAccessRequirement.NONE
        for item in streams.video
        if item.quality_code == 64
    )


async def test_provider_parses_bangumi_season_episode_and_member_streams(
    provider: BilibiliProvider,
    valid_cookie_json: bytes,
) -> None:
    season_reference = await provider.normalize_url(
        "https://www.bilibili.com/bangumi/play/ss28747?from_spmid=removed"
    )
    season = await provider.get_video(season_reference)

    assert season.provider == "bilibili_pgc"
    assert season.bvid == "SS28747"
    assert season.aid == 28747
    assert season.title == "固定番剧解析样例"
    assert season.cover_url.startswith("https://")
    assert season.owner_name == "固定番剧出品方"
    assert season.duration == 2415
    assert len(season.parts) == 2
    assert season.parts[1].title == "2 固定第二集"
    assert season.rights["contentType"] == "bangumi"
    assert season.rights["isPremiumOnly"] is True
    assert season.stats["views"] == 987654
    assert season.tags == ["动画", "剧情", "中国大陆"]
    assert set(season.raw_metadata) == {
        "contentType",
        "seasonId",
        "mediaId",
        "seasonType",
        "seasonTypeName",
        "episodes",
    }

    anonymous_streams = await provider.get_streams(season, season.parts[0])
    assert {item.quality_code for item in anonymous_streams.video} == {32}
    assert {item.codec for item in anonymous_streams.video} == {
        "H.264/AVC",
        "H.265/HEVC",
    }
    assert anonymous_streams.audio[0].codec == "AAC"
    assert anonymous_streams.video[0].mime_type == "video/mp4"
    assert anonymous_streams.video[0].codec_string == "avc1.64001E"
    assert anonymous_streams.video[0].init_range_start == 0
    assert anonymous_streams.video[0].index_range_end == 1_999
    assert anonymous_streams.audio[0].mime_type == "audio/mp4"

    parsed = CookieFileParser(max_bytes=1_048_576, max_items=100).parse(valid_cookie_json)
    authenticated_streams = await provider.get_streams(season, season.parts[0], parsed.jar)
    premium_stream = next(item for item in authenticated_streams.video if item.quality_code == 120)
    assert (premium_stream.width, premium_stream.height) == (3840, 2160)
    assert premium_stream.codec == "H.265/HEVC"
    assert premium_stream.access_requirement == StreamAccessRequirement.PREMIUM
    assert premium_stream.codec_string == "hev1.2.4.L153.B0"
    assert premium_stream.init_range_end == 1_099

    episode_reference = await provider.normalize_url(
        "https://www.bilibili.com/bangumi/play/ep733317"
    )
    episode_season = await provider.get_video(episode_reference)
    assert len(episode_season.parts) == 2
    assert any(item.get("episodeId") == 733317 for item in episode_season.raw_metadata["episodes"])

    special_reference = await provider.normalize_url("ep800001")
    special_season = await provider.get_video(special_reference)
    assert len(special_season.parts) == 3
    special_metadata = next(
        item for item in special_season.raw_metadata["episodes"] if item.get("episodeId") == 800001
    )
    assert special_metadata["sectionEpisode"] is True
    assert special_season.parts[-1].title == "特别篇 固定特别篇"

    subtitles = await provider.get_subtitles(season, season.parts[0])
    assert subtitles[0].language == "zh-CN"


async def test_provider_normalizes_bangumi_preview_and_malformed_payloads(
    settings: Settings,
    upstream: UpstreamFixtureServer,
) -> None:
    def preview_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/pgc/player/web/playurl":
            return httpx.Response(
                200,
                json={"code": 0, "result": {"code": 0, "is_preview": 1}},
                request=request,
            )
        return upstream.handle(request)

    preview_provider = BilibiliProvider(
        settings,
        transport=httpx.MockTransport(preview_handler),
        media_resolver=public_media_resolver,
    )
    reference = await preview_provider.normalize_url("ss28747")
    video = await preview_provider.get_video(reference)
    with pytest.raises(AppError) as preview:
        await preview_provider.get_streams(video, video.parts[0])
    assert preview.value.code == ErrorCode.AUTH_REQUIRED
    assert preview.value.status_code == 401

    malformed_provider = BilibiliProvider(
        settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"code": 0, "result": {"season_id": 28747, "title": "缺少剧集"}},
                request=request,
            )
        ),
    )
    with pytest.raises(AppError) as malformed:
        await malformed_provider.get_video(reference)
    assert malformed.value.code == ErrorCode.UPSTREAM_CHANGED


async def test_verify_stream_rejects_non_bilibili_host(provider: BilibiliProvider) -> None:
    with pytest.raises(AppError) as caught:
        await provider.verify_stream("https://127.0.0.1/private")
    assert caught.value.code == ErrorCode.UPSTREAM_CHANGED


async def test_verify_stream_accepts_official_bilivideo_cn_cdn(
    provider: BilibiliProvider,
) -> None:
    await provider.verify_stream("https://sample.mcdn.bilivideo.cn/video/sample.m4s")


async def test_verify_stream_accepts_only_the_scoped_pgc_cdn_and_sends_no_cookie(
    provider: BilibiliProvider,
    upstream: UpstreamFixtureServer,
    valid_cookie_json: bytes,
) -> None:
    parsed = CookieFileParser(max_bytes=1_048_576, max_items=100).parse(valid_cookie_json)
    await provider.verify_stream(
        "https://vu5bt87a.edge.mountaintoys.cn:4483/video/sample.m4s",
        parsed.jar,
    )
    assert upstream.cookie_headers[-1][1] == ""

    with pytest.raises(AppError) as sibling:
        await provider.verify_stream("https://edge.mountaintoys.cn.evil.example/sample.m4s")
    assert sibling.value.code == ErrorCode.UPSTREAM_CHANGED

    with pytest.raises(AppError) as broad_parent:
        await provider.verify_stream("https://unrelated.mountaintoys.cn/sample.m4s")
    assert broad_parent.value.code == ErrorCode.UPSTREAM_CHANGED


async def test_verify_stream_rejects_private_dns_answers_before_request(
    settings: Settings,
) -> None:
    requests: list[httpx.Request] = []

    async def private_resolver(_host: str, _port: int) -> list[str]:
        return ["127.0.0.1"]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(206, content=b"never reached", request=request)

    provider = BilibiliProvider(
        settings,
        transport=httpx.MockTransport(handler),
        media_resolver=private_resolver,
    )

    with pytest.raises(AppError) as caught:
        await provider.verify_stream("https://cdn-a.bilivideo.com/video/sample.m4s")

    assert caught.value.code == ErrorCode.UPSTREAM_CHANGED
    assert requests == []


async def test_verify_stream_connects_to_validated_ip_with_original_host_and_sni(
    settings: Settings,
) -> None:
    requests: list[httpx.Request] = []

    async def resolver(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(206, content=b"verified", request=request)

    provider = BilibiliProvider(
        settings,
        transport=httpx.MockTransport(handler),
        media_resolver=resolver,
    )

    await provider.verify_stream("https://cdn-a.bilivideo.com/video/sample.m4s")

    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["Host"] == "cdn-a.bilivideo.com"
    assert requests[0].headers["Range"] == "bytes=0-1023"
    assert requests[0].headers["Referer"] == "https://www.bilibili.com/"
    assert requests[0].headers["Accept"] == "*/*"
    assert requests[0].headers["Accept-Encoding"] == "identity"
    assert requests[0].extensions["sni_hostname"] == "cdn-a.bilivideo.com"


async def test_invalid_auth_is_not_logged_in(provider: BilibiliProvider) -> None:
    result = await provider.validate_auth(CookieJar())
    assert result.logged_in is False
    assert result.premium is False


async def test_provider_subtitles_and_stream_verification(provider: BilibiliProvider) -> None:
    reference = await provider.normalize_url("BV1FYT5zkE1q")
    video = await provider.get_video(reference)
    subtitles = await provider.get_subtitles(video, video.parts[0])
    assert len(subtitles) == 1
    assert subtitles[0].language == "zh-CN"
    assert subtitles[0].url.startswith("https://aisubtitle.hdslb.com/")
    await provider.verify_stream(
        "https://cdn-a.bilivideo.com/video/sample.m4s?deadline=1&token=fixed"
    )


async def test_provider_downloads_bounded_danmaku_without_cookie(
    provider: BilibiliProvider,
    upstream: UpstreamFixtureServer,
    valid_cookie_json: bytes,
) -> None:
    parsed = CookieFileParser(max_bytes=1_048_576, max_items=100).parse(valid_cookie_json)
    reference = await provider.normalize_url("BV1FYT5zkE1q")
    video = await provider.get_video(reference, parsed.jar)
    document = await provider.get_danmaku(video, video.parts[1])

    assert document.startswith(b'<?xml version="1.0"')
    requests = [cookie for path, cookie in upstream.cookie_headers if path == "/x/v1/dm/list.so"]
    assert requests == [""]


async def test_provider_uses_part_cid_for_danmaku(settings: Settings) -> None:
    seen_oid: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_oid.append(request.url.params.get("oid"))
        return httpx.Response(200, content=b'<i><d p="1,1,1,1,1,1,1,1">ok</d></i>', request=request)

    local = BilibiliProvider(settings, transport=httpx.MockTransport(handler))
    part = ProviderPart(cid=31000000002, page_number=2, title="P2", duration=10)
    await local.get_danmaku(provider_video(), part)
    assert seen_oid == ["31000000002"]


def test_provider_accepts_zero_entry_danmaku_document() -> None:
    BilibiliProvider._validate_danmaku_xml(b"<i></i>")


@pytest.mark.parametrize(
    "document",
    [
        b"not xml",
        b"<root />",
        b'<!DOCTYPE i [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><i>&xxe;</i>',
        b'<i><d p="1">',
        b"<i><d>missing parameters</d></i>",
    ],
)
async def test_provider_rejects_unsafe_danmaku_xml(
    settings: Settings,
    document: bytes,
) -> None:
    provider = BilibiliProvider(
        settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=document, request=request)
        ),
    )
    part = ProviderPart(cid=1, page_number=1, title="P1", duration=10)
    with pytest.raises(AppError) as caught:
        await provider.get_danmaku(provider_video(), part)
    assert caught.value.code == ErrorCode.UPSTREAM_CHANGED


@pytest.mark.parametrize("declared", ["65537", "invalid", "-1"])
async def test_provider_rejects_invalid_danmaku_content_length(
    settings: Settings,
    declared: str,
) -> None:
    limited = settings.model_copy(update={"upstream_max_response_bytes": 65_536})
    provider = BilibiliProvider(
        limited,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"Content-Length": declared},
                content=b"<i />",
                request=request,
            )
        ),
    )
    part = ProviderPart(cid=1, page_number=1, title="P1", duration=10)
    with pytest.raises(AppError) as caught:
        await provider.get_danmaku(provider_video(), part)
    assert caught.value.code == ErrorCode.UPSTREAM_CHANGED


async def test_provider_rejects_streamed_danmaku_over_limit(settings: Settings) -> None:
    limited = settings.model_copy(update={"upstream_max_response_bytes": 65_536})
    provider = BilibiliProvider(
        limited,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * 65_537, request=request)
        ),
    )
    part = ProviderPart(cid=1, page_number=1, title="P1", duration=10)
    with pytest.raises(AppError) as caught:
        await provider.get_danmaku(provider_video(), part)
    assert caught.value.code == ErrorCode.UPSTREAM_CHANGED


def test_provider_accepts_known_mcdn_tls_port_and_rejects_other_ports(
    provider: BilibiliProvider,
) -> None:
    provider._validate_resource_url(
        "https://edge.mcdn.bilivideo.cn:8082/video/sample.m4s?deadline=1"
    )
    for url in (
        "https://edge.mcdn.bilivideo.cn:8080/video/sample.m4s",
        "https://cdn-a.bilivideo.com:8082/video/sample.m4s",
        "https://edge.mcdn.bilivideo.cn:invalid/video/sample.m4s",
    ):
        with pytest.raises(AppError) as caught:
            provider._validate_resource_url(url)
        assert caught.value.code == ErrorCode.UPSTREAM_CHANGED


def test_provider_accepts_current_pgc_cdn_tls_port_only_for_scoped_host(
    provider: BilibiliProvider,
) -> None:
    provider._validate_resource_url(
        "https://vu5bt87a.edge.mountaintoys.cn:4483/video/sample.m4s?deadline=1"
    )
    for url in (
        "https://unrelated.mountaintoys.cn:4483/video/sample.m4s",
        "https://edge.mountaintoys.cn.evil.example:4483/video/sample.m4s",
        "https://vu5bt87a.edge.mountaintoys.cn:4484/video/sample.m4s",
    ):
        with pytest.raises(AppError) as caught:
            provider._validate_resource_url(url)
        assert caught.value.code == ErrorCode.UPSTREAM_CHANGED


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (-404, ErrorCode.VIDEO_NOT_FOUND),
        (-101, ErrorCode.AUTH_REQUIRED),
        (-403, ErrorCode.PERMISSION_DENIED),
        (-10403, ErrorCode.REGION_RESTRICTED),
        (-352, ErrorCode.RISK_CONTROL),
        (999999, ErrorCode.UPSTREAM_CHANGED),
    ],
)
def test_provider_error_code_normalization(code: int, expected: ErrorCode) -> None:
    with pytest.raises(AppError) as caught:
        BilibiliProvider._raise_for_provider_code({"code": code})
    assert caught.value.code == expected
    assert caught.value.action


async def test_provider_normalizes_network_and_malformed_responses(settings: Settings) -> None:
    def network_failure(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("fixed connection failure", request=request)

    network_provider = BilibiliProvider(settings, transport=httpx.MockTransport(network_failure))
    reference = await network_provider.normalize_url("BV1FYT5zkE1q")
    with pytest.raises(AppError) as network_error:
        await network_provider.get_video(reference)
    assert network_error.value.code == ErrorCode.UPSTREAM_NETWORK

    malformed_provider = BilibiliProvider(
        settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"code": 0, "unexpected": True}, request=request
            )
        ),
    )
    with pytest.raises(AppError) as malformed_error:
        await malformed_provider.get_video(reference)
    assert malformed_error.value.code == ErrorCode.UPSTREAM_CHANGED


async def test_provider_maps_http_risk_control(settings: Settings) -> None:
    risk_provider = BilibiliProvider(
        settings,
        transport=httpx.MockTransport(lambda request: httpx.Response(412, request=request)),
    )
    reference = await risk_provider.normalize_url("BV1FYT5zkE1q")
    with pytest.raises(AppError) as caught:
        await risk_provider.get_video(reference)
    assert caught.value.code == ErrorCode.RISK_CONTROL


def test_stream_parser_handles_codec_fps_hdr_and_fallbacks(
    provider: BilibiliProvider,
) -> None:
    base = {
        "id": 126,
        "base_url": "//cdn-a.bilivideo.com/video/sample.m4s?token=fixed",
        "backup_url": ["//cdn-b.bilivideo.com/video/sample.m4s?token=fixed"],
        "bandwidth": 1_000_000,
        "mime_type": "video/mp4",
        "codecs": "av01.0.08M.08",
        "codecid": 13,
        "width": 1920,
        "height": 1080,
        "frame_rate": "30000/1001",
    }
    video = provider._parse_stream(
        base,
        kind=StreamKind.VIDEO,
        duration=10.0,
        quality_descriptions={},
    )
    assert video.codec == "AV1"
    assert video.hdr_type == "Dolby Vision"
    assert video.fps == pytest.approx(29.97, rel=0.001)
    assert len(video.backup_urls) == 1

    audio = provider._parse_stream(
        {
            "id": 30251,
            "baseUrl": "https://cdn-a.bilivideo.com/audio/flac.m4s",
            "bandwidth": 900_000,
            "mimeType": "audio/mp4",
            "codecs": "fLaC",
        },
        kind=StreamKind.AUDIO,
        duration=2.0,
        quality_descriptions={},
    )
    assert audio.codec == "FLAC"
    assert audio.quality_label == "音频 900 kbps"
    assert "支持有限" in audio.compatibility

    assert provider._codec_label("ec-3", StreamKind.AUDIO) == "Dolby E-AC-3"
    assert provider._codec_label("mystery", StreamKind.VIDEO) == "mystery"
    assert provider._codec_label("unknown", StreamKind.VIDEO) == "未知视频编码"
    assert provider._hdr_type(125) == "HDR"
    assert provider._hdr_type(80) == "SDR"
    assert provider._parse_fps(50) == 50.0
    assert provider._parse_fps("invalid") is None
    assert provider._parse_fps("25/0") is None
    assert provider._parse_fps(0) is None
    assert provider._quality_descriptions({}) == {}
    assert provider._optional_int(True) is None
    assert provider._optional_int(25.0) == 25
    assert provider._optional_int(25.5) is None


async def test_optional_tags_failure_does_not_break_metadata(
    settings: Settings, upstream: UpstreamFixtureServer
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/tag/archive/tags":
            return httpx.Response(200, json={"code": 9999}, request=request)
        return upstream.handle(request)

    provider = BilibiliProvider(settings, transport=httpx.MockTransport(handler))
    reference = await provider.normalize_url("BV1FYT5zkE1q")
    video = await provider.get_video(reference)
    assert video.tags == []
    assert video.title == "固定响应解析样例"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, headers={"Content-Length": "999999"}, content=b"{}"),
        httpx.Response(200, headers={"Content-Length": "invalid"}, content=b"{}"),
    ],
)
async def test_provider_rejects_malformed_or_oversized_payloads(
    settings: Settings, response: httpx.Response
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )

    provider = BilibiliProvider(settings, transport=httpx.MockTransport(handler))
    reference = await provider.normalize_url("BV1FYT5zkE1q")
    with pytest.raises(AppError) as caught:
        await provider.get_video(reference)
    assert caught.value.code == ErrorCode.UPSTREAM_CHANGED


def test_provider_rejects_incomplete_internal_records(provider: BilibiliProvider) -> None:
    with pytest.raises(AppError) as missing_video:
        provider._parse_video({"title": "incomplete"})
    assert missing_video.value.code == ErrorCode.UPSTREAM_CHANGED
    with pytest.raises(AppError) as missing_stream:
        provider._parse_stream(
            {"id": 80},
            kind=StreamKind.VIDEO,
            duration=1.0,
            quality_descriptions={},
        )
    assert missing_stream.value.code == ErrorCode.UPSTREAM_CHANGED
    with pytest.raises(AppError):
        provider._response_data({"code": 0})


async def test_provider_runtime_request_interval_is_validated_and_enforced(
    provider: BilibiliProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="timeout"):
        provider.configure_runtime(timeout_seconds=0.5, upstream_interval_milliseconds=0)
    with pytest.raises(ValueError, match="interval"):
        provider.configure_runtime(timeout_seconds=30, upstream_interval_milliseconds=60_001)

    provider.configure_runtime(timeout_seconds=25, upstream_interval_milliseconds=250)
    assert provider.timeout.connect == 5.0
    clock = iter((10.0, 10.0, 10.1, 10.25))
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(provider, "_clock", lambda: next(clock))
    monkeypatch.setattr(provider, "_sleep", record_sleep)
    await provider._wait_for_request_slot()
    await provider._wait_for_request_slot()

    assert delays == pytest.approx([0.15])
