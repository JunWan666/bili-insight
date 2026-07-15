from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core.exceptions import AppError, ErrorCode
from app.db.models import AccessContext, MediaStream, Video
from app.providers.models import ProviderStreams
from app.schemas.video import AccessMode
from tests.conftest import UpstreamFixtureServer


async def upload_auth(client: Any, payload: bytes) -> None:
    response = await client.post(
        "/api/v1/auth/cookies",
        files={"file": ("cookies.json", payload, "application/json")},
        data={"persistence": "session"},
    )
    assert response.status_code == 200


async def test_auto_mode_is_anonymous_even_when_credentials_exist(
    api_client: tuple[Any, Any],
    upstream: UpstreamFixtureServer,
    valid_cookie_json: bytes,
) -> None:
    client, _ = api_client
    await upload_auth(client, valid_cookie_json)
    upstream.cookie_headers.clear()
    response = await client.post(
        "/api/v1/videos/parse",
        json={
            "url": "https://www.bilibili.com/video/BV1FYT5zkE1q/"
            "?spm_id_from=tracking&p=2&vd_source=tracking",
            "accessMode": "auto",
            "forceRefresh": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access"]["requestedMode"] == "auto"
    assert body["access"]["actualMode"] == "anonymous"
    assert body["access"]["hasCredentials"] is True
    assert body["access"]["usedAuthentication"] is False
    assert body["streams"]["access"] == body["access"]
    assert body["selectedPartId"] == body["video"]["parts"][1]["id"]
    assert body["video"]["parts"][1]["pageNumber"] == 2
    assert body["video"]["stats"]["views"] == 1200
    assert body["video"]["rights"] == {
        "copyright": "原创",
        "copyrightCode": 1,
        "isPaid": False,
        "isPremiumOnly": False,
    }
    assert len(body["streams"]["video"]) == 2
    assert {item["codec"] for item in body["streams"]["video"]} == {
        "H.264/AVC",
        "H.265/HEVC",
    }
    relevant = [
        header
        for path, header in upstream.cookie_headers
        if path in {"/x/web-interface/view", "/x/tag/archive/tags", "/x/player/playurl"}
    ]
    assert relevant
    assert all("SESSDATA" not in header for header in relevant)
    assert "deadline=" not in response.text
    assert "token=" not in response.text


async def test_authenticated_parse_marks_added_capabilities(
    api_client: tuple[Any, Any], valid_cookie_json: bytes
) -> None:
    client, _ = api_client
    await upload_auth(client, valid_cookie_json)
    response = await client.post(
        "/api/v1/videos/parse",
        json={"url": "BV1FYT5zkE1q", "accessMode": "authenticated"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access"]["actualMode"] == "authenticated"
    assert body["access"]["usedAuthentication"] is True
    member = [item for item in body["streams"]["video"] if item["qualityCode"] == 112]
    assert len(member) == 3
    assert all(item["authRequired"] for item in member)
    assert all(item["premiumRequired"] for item in member)
    assert all(item["accessRequirement"] == "premium" for item in member)
    anonymous_equivalent = [item for item in body["streams"]["video"] if item["qualityCode"] == 64]
    assert all(not item["authRequired"] for item in anonymous_equivalent)
    assert all(item["accessRequirement"] == "none" for item in anonymous_equivalent)


async def test_bangumi_season_episode_parse_refresh_and_stream_resolution(
    api_client: tuple[Any, Any],
    upstream: UpstreamFixtureServer,
) -> None:
    client, app = api_client
    season_response = await client.post(
        "/api/v1/videos/parse",
        json={
            "url": ("https://www.bilibili.com/bangumi/play/ss28747?from_spmid=666.5.mylist.0"),
            "accessMode": "anonymous",
        },
    )

    assert season_response.status_code == 200, season_response.text
    season = season_response.json()
    assert season["normalizedUrl"] == "https://www.bilibili.com/bangumi/play/ss28747"
    assert season["video"]["provider"] == "bilibili_pgc"
    assert season["video"]["bvid"] == "SS28747"
    assert season["video"]["aid"] == 28747
    assert season["video"]["partCount"] == 2
    assert season["selectedPartId"] == season["video"]["parts"][0]["id"]
    assert {item["qualityCode"] for item in season["streams"]["video"]} == {32}
    assert {item["codec"] for item in season["streams"]["video"]} == {
        "H.264/AVC",
        "H.265/HEVC",
    }
    assert all(item["previewSupported"] for item in season["streams"]["video"])
    assert all(item["mimeType"] == "video/mp4" for item in season["streams"]["video"])
    assert "mountaintoys" not in season_response.text
    assert "deadline=" not in season_response.text
    assert "token=" not in season_response.text
    anonymous_headers = [
        header
        for path, header in upstream.cookie_headers
        if path in {"/pgc/view/web/season", "/pgc/player/web/playurl"}
    ]
    assert anonymous_headers
    assert all("SESSDATA" not in header for header in anonymous_headers)

    episode_response = await client.post(
        "/api/v1/videos/parse",
        json={
            "url": "https://www.bilibili.com/bangumi/play/ep733317",
            "accessMode": "anonymous",
        },
    )
    assert episode_response.status_code == 200, episode_response.text
    episode = episode_response.json()
    assert episode["normalizedUrl"] == "https://www.bilibili.com/bangumi/play/ep733317"
    assert episode["selectedPartId"] == episode["video"]["parts"][1]["id"]

    video_id = episode["video"]["id"]
    part_id = episode["selectedPartId"]
    refreshed = await client.post(
        f"/api/v1/videos/{video_id}/refresh",
        json={"accessMode": "anonymous", "partId": part_id},
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["normalizedUrl"] == ("https://www.bilibili.com/bangumi/play/ep733317")
    assert refreshed.json()["selectedPartId"] == part_id

    stream_id = refreshed.json()["streams"]["video"][0]["id"]
    service = app.state.container.video_service
    service._source_urls.clear()
    playurl_count = upstream.counts["/pgc/player/web/playurl"]
    resolved = await service.resolve_stream(stream_id, AccessMode.ANONYMOUS)
    assert resolved.stream_id == stream_id
    assert resolved.url.startswith("https://")
    assert upstream.counts["/pgc/player/web/playurl"] == playurl_count + 1


async def test_bangumi_section_episodes_remain_stable_across_ep_and_season_refreshes(
    api_client: tuple[Any, Any],
) -> None:
    client, _ = api_client

    first = await client.post(
        "/api/v1/videos/parse",
        json={"url": "ep800001", "accessMode": "anonymous", "forceRefresh": True},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()

    second = await client.post(
        "/api/v1/videos/parse",
        json={"url": "ep800002", "accessMode": "anonymous", "forceRefresh": True},
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["video"]["id"] == first_body["video"]["id"]
    assert {part["cid"] for part in second_body["video"]["parts"]} >= {
        1022370999,
        1022371000,
    }
    assert second_body["selectedPartId"] != first_body["selectedPartId"]

    season = await client.post(
        "/api/v1/videos/parse",
        json={"url": "ss28747", "accessMode": "anonymous", "forceRefresh": True},
    )
    assert season.status_code == 200, season.text
    season_body = season.json()
    assert season_body["video"]["id"] == first_body["video"]["id"]
    assert {part["cid"] for part in season_body["video"]["parts"]} >= {
        1022370999,
        1022371000,
    }
    page_numbers = [part["pageNumber"] for part in season_body["video"]["parts"]]
    assert len(page_numbers) == len(set(page_numbers))


async def test_authenticated_bangumi_parse_adds_member_only_quality(
    api_client: tuple[Any, Any],
    upstream: UpstreamFixtureServer,
    valid_cookie_json: bytes,
) -> None:
    client, _ = api_client
    await upload_auth(client, valid_cookie_json)
    upstream.cookie_headers.clear()

    response = await client.post(
        "/api/v1/videos/parse",
        json={
            "url": "https://www.bilibili.com/bangumi/play/ep733316",
            "accessMode": "authenticated",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    premium = next(item for item in body["streams"]["video"] if item["qualityCode"] == 120)
    assert premium["width"] == 3840
    assert premium["height"] == 2160
    assert premium["authRequired"] is True
    assert premium["premiumRequired"] is True
    assert premium["accessRequirement"] == "premium"
    assert premium["previewSupported"] is True
    assert premium["codecString"] == "hev1.2.4.L153.B0"
    assert body["access"]["actualMode"] == "authenticated"
    pgc_headers = [
        header
        for path, header in upstream.cookie_headers
        if path in {"/pgc/view/web/season", "/pgc/player/web/playurl"}
    ]
    assert any("SESSDATA=test-session-value" in header for header in pgc_headers)
    assert "test-session-value" not in response.text
    assert "mountaintoys" not in response.text


async def test_authenticated_bangumi_parse_survives_anonymous_preview_only_response(
    api_client: tuple[Any, Any],
    upstream: UpstreamFixtureServer,
    valid_cookie_json: bytes,
) -> None:
    client, _ = api_client
    await upload_auth(client, valid_cookie_json)
    upstream.force_pgc_anonymous_preview = True

    response = await client.post(
        "/api/v1/videos/parse",
        json={
            "url": "https://www.bilibili.com/bangumi/play/ep733316",
            "accessMode": "authenticated",
            "forceRefresh": True,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access"]["actualMode"] == "authenticated"
    assert max(item["height"] or 0 for item in body["streams"]["video"]) == 2160
    assert all(item["authRequired"] for item in body["streams"]["video"])


async def test_video_endpoints_and_cache(
    api_client: tuple[Any, Any], upstream: UpstreamFixtureServer
) -> None:
    client, app = api_client
    first = await client.post(
        "/api/v1/videos/parse",
        json={"url": "BV1FYT5zkE1q", "accessMode": "anonymous"},
    )
    assert first.status_code == 200
    first_body = first.json()
    playurl_count = upstream.counts["/x/player/playurl"]
    second = await client.post(
        "/api/v1/videos/parse",
        json={"url": "BV1FYT5zkE1q", "accessMode": "anonymous"},
    )
    assert second.status_code == 200
    assert second.json()["cacheHit"] is True
    assert upstream.counts["/x/player/playurl"] == playurl_count

    video_id = first_body["video"]["id"]
    part_id = first_body["selectedPartId"]
    async with app.state.container.session_factory() as session:
        persisted_video = await session.get(Video, video_id)
        assert persisted_video is not None
        persisted_video.cover_url = "http://i0.hdslb.com/bfs/archive/sample.jpg"
        await session.commit()
    details = await client.get(f"/api/v1/videos/{video_id}")
    assert details.status_code == 200
    assert details.json()["bvid"] == "BV1FYT5zkE1q"
    recent = await client.get("/api/v1/videos", params={"limit": 8})
    assert recent.status_code == 200
    assert recent.json() == [
        {
            "id": video_id,
            "bvid": "BV1FYT5zkE1q",
            "title": "固定响应解析样例",
            "coverUrl": "https://i0.hdslb.com/bfs/archive/sample.jpg",
            "ownerName": "样例UP主",
            "duration": 218,
            "parsedAt": recent.json()[0]["parsedAt"],
            "normalizedUrl": "https://www.bilibili.com/video/BV1FYT5zkE1q/",
        }
    ]
    parts = await client.get(f"/api/v1/videos/{video_id}/parts")
    assert parts.status_code == 200
    assert len(parts.json()) == 2
    streams = await client.get(
        f"/api/v1/videos/{video_id}/parts/{part_id}/streams",
        params={"accessMode": "anonymous"},
    )
    assert streams.status_code == 200
    assert streams.json()["partId"] == part_id
    assert streams.json()["access"] == {
        "requestedMode": "anonymous",
        "actualMode": "anonymous",
        "hasCredentials": False,
        "usedAuthentication": False,
        "membershipType": "none",
    }
    cached_streams = await client.get(
        f"/api/v1/videos/{video_id}/parts/{part_id}/streams",
        params={"accessMode": "auto"},
    )
    assert cached_streams.status_code == 200
    assert cached_streams.json()["cacheHit"] is True
    assert cached_streams.json()["access"]["requestedMode"] == "auto"
    assert cached_streams.json()["access"]["actualMode"] == "anonymous"
    refreshed = await client.post(
        f"/api/v1/videos/{video_id}/refresh",
        json={"accessMode": "anonymous", "partId": part_id},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["cacheHit"] is False


async def test_authenticated_parse_requires_valid_credentials(api_client: tuple[Any, Any]) -> None:
    client, _ = api_client
    response = await client.post(
        "/api/v1/videos/parse",
        json={"url": "BV1FYT5zkE1q", "accessMode": "authenticated"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


async def test_invalid_links_have_distinct_errors(api_client: tuple[Any, Any]) -> None:
    client, _ = api_client
    invalid = await client.post(
        "/api/v1/videos/parse",
        json={"url": "https://127.0.0.1/video/BV1FYT5zkE1q"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "INVALID_LINK"
    short = await client.post("/api/v1/videos/parse", json={"url": "https://b23.tv/example"})
    assert short.status_code == 400
    assert short.json()["error"]["code"] == "UNSUPPORTED_CONTENT"


async def test_stream_resolution_refreshes_and_range_verifies(
    api_client: tuple[Any, Any], upstream: UpstreamFixtureServer
) -> None:
    client, app = api_client
    parsed = await client.post(
        "/api/v1/videos/parse",
        json={"url": "BV1FYT5zkE1q", "accessMode": "anonymous"},
    )
    assert parsed.status_code == 200
    stream_id = parsed.json()["streams"]["video"][0]["id"]
    service = app.state.container.video_service
    cached = await service.resolve_stream(stream_id, AccessMode.ANONYMOUS)
    assert cached.stream_id == stream_id
    assert cached.url.startswith("https://cdn-a.bilivideo.com/")
    service._source_urls.clear()
    refreshed = await service.resolve_stream(stream_id, AccessMode.ANONYMOUS)
    assert refreshed.codec in {"H.264/AVC", "H.265/HEVC"}
    assert upstream.counts["/x/player/playurl"] >= 2


async def test_stream_verification_api_returns_only_safe_evidence(
    api_client: tuple[Any, Any],
) -> None:
    client, _ = api_client
    parsed = await client.post(
        "/api/v1/videos/parse",
        json={"url": "BV1FYT5zkE1q", "accessMode": "anonymous"},
    )
    stream_id = parsed.json()["streams"]["video"][0]["id"]

    response = await client.post(
        f"/api/v1/videos/streams/{stream_id}/verify",
        json={"accessMode": "anonymous"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["streamId"] == stream_id
    assert response.json()["verifiedAt"]
    assert set(response.json()) == {"streamId", "verifiedAt"}
    assert "bilivideo" not in response.text
    assert "deadline=" not in response.text
    assert "token=" not in response.text


async def test_missing_video_part_and_invalid_request_are_safe(api_client: tuple[Any, Any]) -> None:
    client, _ = api_client
    missing = await client.get("/api/v1/videos/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    invalid = await client.post(
        "/api/v1/videos/parse",
        json={"url": "BV1FYT5zkE1q", "accessMode": "not-a-mode"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_video_part_refresh_and_stream_not_found_paths(api_client: tuple[Any, Any]) -> None:
    client, _ = api_client
    missing_id = "00000000-0000-0000-0000-000000000000"
    parts = await client.get(f"/api/v1/videos/{missing_id}/parts")
    assert parts.status_code == 404
    streams = await client.get(f"/api/v1/videos/{missing_id}/parts/{missing_id}/streams")
    assert streams.status_code == 404
    refresh = await client.post(
        f"/api/v1/videos/{missing_id}/refresh",
        json={"accessMode": "anonymous"},
    )
    assert refresh.status_code == 404

    parsed = await client.post(
        "/api/v1/videos/parse",
        json={"url": "BV1FYT5zkE1q", "accessMode": "anonymous"},
    )
    body = parsed.json()
    video_id = body["video"]["id"]
    missing_part = await client.get(f"/api/v1/videos/{video_id}/parts/{missing_id}/streams")
    assert missing_part.status_code == 404
    bad_refresh = await client.post(
        f"/api/v1/videos/{video_id}/refresh",
        json={"accessMode": "anonymous", "partId": missing_id},
    )
    assert bad_refresh.status_code == 404
    invalid_page = await client.post(
        "/api/v1/videos/parse",
        json={"url": "https://www.bilibili.com/video/BV1FYT5zkE1q?p=99"},
    )
    assert invalid_page.status_code == 422


async def test_authenticated_stream_identity_is_enforced_and_cleared(
    api_client: tuple[Any, Any], valid_cookie_json: bytes
) -> None:
    client, app = api_client
    await upload_auth(client, valid_cookie_json)
    parsed = await client.post(
        "/api/v1/videos/parse",
        json={"url": "BV1FYT5zkE1q", "accessMode": "authenticated"},
    )
    video_id = parsed.json()["video"]["id"]
    part_id = parsed.json()["selectedPartId"]
    authenticated_streams = await client.get(
        f"/api/v1/videos/{video_id}/parts/{part_id}/streams",
        params={"accessMode": "authenticated"},
    )
    assert authenticated_streams.status_code == 200
    assert authenticated_streams.json()["access"] == {
        "requestedMode": "authenticated",
        "actualMode": "authenticated",
        "hasCredentials": True,
        "usedAuthentication": True,
        "membershipType": "annual_premium",
    }
    assert "test-session-value" not in authenticated_streams.text
    auth_stream_id = next(
        item["id"] for item in parsed.json()["streams"]["video"] if item["qualityCode"] == 112
    )
    service = app.state.container.video_service
    with pytest.raises(AppError) as wrong_identity:
        await service.resolve_stream(auth_stream_id, AccessMode.ANONYMOUS)
    assert wrong_identity.value.code == ErrorCode.PERMISSION_DENIED
    wrong_identity_api = await client.post(
        f"/api/v1/videos/streams/{auth_stream_id}/verify",
        json={"accessMode": "anonymous"},
    )
    assert wrong_identity_api.status_code == 403
    assert wrong_identity_api.json()["error"]["code"] == "PERMISSION_DENIED"
    with pytest.raises(AppError) as missing:
        await service.resolve_stream(
            "00000000-0000-0000-0000-000000000000",
            AccessMode.ANONYMOUS,
        )
    assert missing.value.code == ErrorCode.RESOURCE_NOT_FOUND

    await client.delete("/api/v1/auth/cookies")
    async with app.state.container.session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(MediaStream)
            .where(MediaStream.access_context == AccessContext.AUTHENTICATED)
        )
    assert count == 0


async def test_video_and_stream_upserts_update_and_remove_stale_rows(
    api_client: tuple[Any, Any],
) -> None:
    _, app = api_client
    container = app.state.container
    reference = await container.provider.normalize_url("BV1FYT5zkE1q")
    source = await container.provider.get_video(reference)
    created = await container.video_service._upsert_video(source)
    assert len(created.parts) == 2

    updated_source = replace(
        source,
        title="更新后的固定标题",
        parts=[replace(source.parts[0], title="更新后的第一分 P")],
    )
    updated = await container.video_service._upsert_video(updated_source)
    assert updated.id == created.id
    assert updated.title == "更新后的固定标题"
    assert len(updated.parts) == 1
    assert updated.parts[0].title == "更新后的第一分 P"

    provider_streams = await container.provider.get_streams(updated_source, updated_source.parts[0])
    first = await container.video_service._persist_streams(
        updated.parts[0].id,
        AccessContext.ANONYMOUS,
        provider_streams,
        anonymous_capabilities=set(),
    )
    assert len(first) == 3
    second = await container.video_service._persist_streams(
        updated.parts[0].id,
        AccessContext.ANONYMOUS,
        ProviderStreams(video=provider_streams.video[:1], audio=provider_streams.audio),
        anonymous_capabilities=set(),
    )
    assert len(second) == 2
    async with container.session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(MediaStream))
    assert count == 2


async def test_video_upsert_identity_is_scoped_by_provider(api_client: tuple[Any, Any]) -> None:
    _, app = api_client
    container = app.state.container
    reference = await container.provider.normalize_url("BV1FYT5zkE1q")
    source = await container.provider.get_video(reference)
    standard = await container.video_service._upsert_video(source)
    pgc = await container.video_service._upsert_video(
        replace(
            source,
            provider="bilibili_pgc",
            bvid="SS28747",
            title="相同 aid 的固定番剧",
        )
    )

    assert pgc.id != standard.id
    assert pgc.provider == "bilibili_pgc"
    assert pgc.aid == standard.aid
