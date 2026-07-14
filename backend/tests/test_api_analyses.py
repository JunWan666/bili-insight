from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
from fastapi import FastAPI

from app.api.analyses import get_analysis_service, router
from app.core.exceptions import install_exception_handlers
from app.db.models import JobStatus, JobType
from app.schemas.analyses import (
    AnalysisCapabilities,
    AnalysisCapabilityRead,
    AnalysisFeature,
    AnalysisList,
    AnalysisRead,
    AnalysisRequest,
    AnalysisResultStatus,
    TranscriptEditRequest,
)
from app.schemas.jobs import JobRead, JobRuntimeRead
from app.services.analyses import AnalysisService

VIDEO_ID = "123e4567-e89b-42d3-a456-426614174000"
PART_ID = "123e4567-e89b-42d3-a456-426614174001"
JOB_ID = "123e4567-e89b-42d3-a456-426614174002"
ANALYSIS_ID = "123e4567-e89b-42d3-a456-426614174003"


class StubAnalysisService:
    def __init__(self) -> None:
        self.created_request: AnalysisRequest | None = None
        self.edited_request: TranscriptEditRequest | None = None

    async def create(self, request: AnalysisRequest) -> JobRead:
        self.created_request = request
        return _job_read()

    async def capabilities(self) -> AnalysisCapabilities:
        return AnalysisCapabilities(
            items=[
                AnalysisCapabilityRead(
                    feature=AnalysisFeature.MEDIA,
                    component="ffprobe",
                    available=True,
                    version="7.1",
                    reason_code=None,
                    message="可用",
                    action=None,
                )
            ]
        )

    async def list(self, **_: object) -> AnalysisList:
        return AnalysisList(items=[_analysis_read()], total=1, limit=20, offset=0)

    async def get(self, analysis_id: str) -> AnalysisRead:
        assert analysis_id == ANALYSIS_ID
        return _analysis_read()

    async def edit_transcript(
        self, analysis_id: str, request: TranscriptEditRequest
    ) -> AnalysisRead:
        assert analysis_id == ANALYSIS_ID
        self.edited_request = request
        return _analysis_read().model_copy(
            update={
                "id": "123e4567-e89b-42d3-a456-426614174004",
                "model_name": "manual-transcript-editor",
            }
        )


def _job_read() -> JobRead:
    timestamp = datetime(2026, 7, 14, tzinfo=UTC)
    return JobRead(
        id=JOB_ID,
        type=JobType.ANALYSIS,
        status=JobStatus.QUEUED,
        phase="queued",
        progress=0,
        input={
            "video_id": VIDEO_ID,
            "part_ids": [PART_ID],
            "features": ["basic"],
        },
        error_code=None,
        error_message=None,
        retry_count=0,
        cancel_requested=False,
        created_at=timestamp,
        started_at=None,
        finished_at=None,
        updated_at=timestamp,
        runtime=JobRuntimeRead(),
        artifacts=[],
    )


def _analysis_read() -> AnalysisRead:
    timestamp = datetime(2026, 7, 14, tzinfo=UTC)
    return AnalysisRead(
        id=ANALYSIS_ID,
        video_id=VIDEO_ID,
        part_id=PART_ID,
        feature=AnalysisFeature.BASIC,
        status=AnalysisResultStatus.COMPLETED,
        result={"title": "安全结果"},
        model_name="structured-metadata",
        model_version="1.0.0",
        parameters={},
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest.fixture
def api_app() -> tuple[FastAPI, StubAnalysisService]:
    application = FastAPI()
    install_exception_handlers(application)
    application.include_router(router, prefix="/api/v1")
    service = StubAnalysisService()
    application.dependency_overrides[get_analysis_service] = lambda: cast(AnalysisService, service)
    return application, service


@pytest.mark.asyncio
async def test_create_analysis_accepts_frontend_contract_and_normalizes_metadata(
    api_app: tuple[FastAPI, StubAnalysisService],
) -> None:
    application, service = api_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/analyses",
            json={
                "videoId": VIDEO_ID,
                "partIds": [PART_ID],
                "features": [
                    "metadata",
                    "media",
                    "audio",
                    "subtitles",
                    "asr",
                    "ocr",
                    "scenes",
                    "summary",
                ],
                "language": "zh-CN",
                "accessMode": "anonymous",
                "asrModel": "small",
                "ocrResolution": "balanced",
            },
        )

    assert response.status_code == 202
    assert response.json()["type"] == "analysis"
    assert service.created_request is not None
    assert service.created_request.canonical_features[0] == AnalysisFeature.BASIC
    assert set(service.created_request.canonical_features) == {
        AnalysisFeature.BASIC,
        AnalysisFeature.MEDIA,
        AnalysisFeature.AUDIO,
        AnalysisFeature.SUBTITLES,
        AnalysisFeature.ASR,
        AnalysisFeature.OCR,
        AnalysisFeature.SCENES,
        AnalysisFeature.SUMMARY,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload_update",
    [
        {"artifactId": "../../windows/system.ini"},
        {"accessMode": "auto"},
        {"partIds": [PART_ID, PART_ID]},
        {"features": ["metadata", "basic"]},
        {"exportFormats": ["srt", "srt"]},
        {"sceneThreshold": "NaN"},
    ],
)
async def test_create_analysis_rejects_unsafe_or_ambiguous_input(
    api_app: tuple[FastAPI, StubAnalysisService],
    payload_update: dict[str, object],
) -> None:
    application, service = api_app
    payload: dict[str, object] = {
        "videoId": VIDEO_ID,
        "partIds": [PART_ID],
        "features": ["basic"],
        "accessMode": "anonymous",
    }
    payload.update(payload_update)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        response = await client.post("/api/v1/analyses", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert service.created_request is None


@pytest.mark.asyncio
async def test_analysis_query_and_capability_routes_are_unambiguous(
    api_app: tuple[FastAPI, StubAnalysisService],
) -> None:
    application, _ = api_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        capabilities = await client.get("/api/v1/analyses/capabilities")
        listing = await client.get(
            "/api/v1/analyses",
            params={
                "limit": 20,
                "videoId": VIDEO_ID,
                "partId": PART_ID,
                "feature": "metadata",
                "status": "completed",
            },
        )
        detail = await client.get(f"/api/v1/analyses/{ANALYSIS_ID}")
        unsafe_detail = await client.get("/api/v1/analyses/..%2F..%2Fsecret")

    assert capabilities.status_code == 200
    assert capabilities.json()["items"][0]["component"] == "ffprobe"
    assert listing.status_code == 200
    assert listing.json()["items"][0]["feature"] == "basic"
    assert detail.status_code == 200
    assert detail.json()["id"] == ANALYSIS_ID
    assert unsafe_detail.status_code in {404, 422}


@pytest.mark.asyncio
async def test_transcript_edit_route_validates_timeline_and_preserves_plain_markup_text(
    api_app: tuple[FastAPI, StubAnalysisService],
) -> None:
    application, _ = api_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        accepted = await client.patch(
            f"/api/v1/analyses/{ANALYSIS_ID}/transcript",
            json={"segments": [{"startSeconds": 0, "endSeconds": 2, "text": "人工校正文案"}]},
        )
        invalid_uuid = await client.patch(
            "/api/v1/analyses/not-a-uuid/transcript",
            json={"segments": [{"startSeconds": 0, "endSeconds": 2, "text": "人工校正文案"}]},
        )
        invalid_timeline = await client.patch(
            f"/api/v1/analyses/{ANALYSIS_ID}/transcript",
            json={"segments": [{"startSeconds": 5, "endSeconds": 2, "text": "人工校正文案"}]},
        )
        overlapping_timeline = await client.patch(
            f"/api/v1/analyses/{ANALYSIS_ID}/transcript",
            json={
                "segments": [
                    {"startSeconds": 0, "endSeconds": 3, "text": "第一位说话者"},
                    {"startSeconds": 2, "endSeconds": 4, "text": "重叠的第二位说话者"},
                ]
            },
        )
        out_of_order_timeline = await client.patch(
            f"/api/v1/analyses/{ANALYSIS_ID}/transcript",
            json={
                "segments": [
                    {"startSeconds": 2, "endSeconds": 3, "text": "第二段"},
                    {"startSeconds": 1, "endSeconds": 4, "text": "乱序第一段"},
                ]
            },
        )
        active_markup = await client.patch(
            f"/api/v1/analyses/{ANALYSIS_ID}/transcript",
            json={
                "segments": [
                    {
                        "startSeconds": 0,
                        "endSeconds": 2,
                        "text": "<img src=x onerror=alert(1)>",
                    }
                ]
            },
        )
        unsafe_control = await client.patch(
            f"/api/v1/analyses/{ANALYSIS_ID}/transcript",
            json={"segments": [{"startSeconds": 0, "endSeconds": 2, "text": "控制字\u0000符"}]},
        )

    assert accepted.status_code == 201
    assert accepted.json()["modelName"] == "manual-transcript-editor"
    assert overlapping_timeline.status_code == 201
    assert active_markup.status_code == 201
    assert api_app[1].edited_request is not None
    assert api_app[1].edited_request.segments[0].text == "<img src=x onerror=alert(1)>"
    for rejected in (invalid_uuid, invalid_timeline, out_of_order_timeline, unsafe_control):
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
