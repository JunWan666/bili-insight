from __future__ import annotations

import math
import re
from datetime import datetime
from enum import StrEnum

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.schemas.base import CamelModel
from app.schemas.settings import AnalysisLanguage, AsrModel
from app.schemas.video import AccessMode

_UUID_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_UNSAFE_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class AnalysisFeature(StrEnum):
    BASIC = "basic"
    METADATA = "metadata"
    MEDIA = "media"
    AUDIO = "audio"
    SUBTITLES = "subtitles"
    ASR = "asr"
    OCR = "ocr"
    SCENES = "scenes"
    SUMMARY = "summary"

    @property
    def canonical(self) -> AnalysisFeature:
        return AnalysisFeature.BASIC if self == AnalysisFeature.METADATA else self


class OcrResolution(StrEnum):
    ECONOMY = "economy"
    BALANCED = "balanced"
    DETAIL = "detail"


class AnalysisExportFormat(StrEnum):
    SRT = "srt"
    VTT = "vtt"
    TXT = "txt"
    JSON = "json"


class AnalysisResultStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class AnalysisRequest(CamelModel):
    model_config = ConfigDict(allow_inf_nan=False)

    video_id: str = Field(pattern=_UUID_PATTERN)
    part_ids: list[str] = Field(min_length=1, max_length=20)
    artifact_id: str | None = Field(default=None, pattern=_UUID_PATTERN)
    features: list[AnalysisFeature] = Field(min_length=1, max_length=9)
    language: AnalysisLanguage = AnalysisLanguage.CHINESE_SIMPLIFIED
    access_mode: AccessMode = AccessMode.ANONYMOUS
    asr_model: AsrModel = AsrModel.SMALL
    ocr_resolution: OcrResolution = OcrResolution.BALANCED
    export_formats: list[AnalysisExportFormat] = Field(
        default_factory=lambda: [
            AnalysisExportFormat.SRT,
            AnalysisExportFormat.VTT,
            AnalysisExportFormat.TXT,
            AnalysisExportFormat.JSON,
        ],
        min_length=1,
        max_length=4,
    )
    maximum_duration_seconds: int | None = Field(default=3_600, ge=60, le=86_400)
    scene_threshold: float = Field(default=0.3, ge=0.01, le=0.99)
    maximum_keyframes: int = Field(default=24, ge=1, le=200)

    @field_validator("part_ids")
    @classmethod
    def validate_part_ids(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("part IDs must be unique")
        if any(not _matches_uuid(value) for value in values):
            raise ValueError("part ID format is invalid")
        return values

    @field_validator("features")
    @classmethod
    def validate_features(cls, values: list[AnalysisFeature]) -> list[AnalysisFeature]:
        canonical = [value.canonical for value in values]
        if len(set(canonical)) != len(canonical):
            raise ValueError("analysis features must be unique")
        return values

    @field_validator("export_formats")
    @classmethod
    def validate_export_formats(
        cls, values: list[AnalysisExportFormat]
    ) -> list[AnalysisExportFormat]:
        if len(set(values)) != len(values):
            raise ValueError("analysis export formats must be unique")
        return values

    @field_validator("scene_threshold")
    @classmethod
    def validate_scene_threshold(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("scene threshold must be finite")
        return value

    @model_validator(mode="after")
    def validate_source_selection(self) -> AnalysisRequest:
        if self.access_mode == AccessMode.AUTO:
            raise ValueError("analysis access mode must be anonymous or authenticated")
        if self.artifact_id is not None and len(self.part_ids) != 1:
            raise ValueError("an explicit artifact can only be used with one video part")
        return self

    @property
    def canonical_features(self) -> tuple[AnalysisFeature, ...]:
        return tuple(value.canonical for value in self.features)


class TranscriptEditSegment(CamelModel):
    model_config = ConfigDict(allow_inf_nan=False)

    start_seconds: float = Field(ge=0, le=604_800)
    end_seconds: float = Field(gt=0, le=604_800)
    text: str = Field(min_length=1, max_length=5_000)

    @field_validator("text")
    @classmethod
    def validate_safe_text(cls, value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise ValueError("transcript text cannot be empty")
        if _UNSAFE_CONTROL_PATTERN.search(normalized):
            raise ValueError("transcript text contains unsafe control characters")
        return normalized

    @model_validator(mode="after")
    def validate_timeline(self) -> TranscriptEditSegment:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("transcript segment end must be greater than start")
        return self


class TranscriptEditRequest(CamelModel):
    segments: list[TranscriptEditSegment] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_segments(self) -> TranscriptEditRequest:
        if sum(len(segment.text) for segment in self.segments) > 2_000_000:
            raise ValueError("edited transcript exceeds the total text safety limit")
        if any(
            current.start_seconds < previous.start_seconds
            for previous, current in zip(self.segments, self.segments[1:], strict=False)
        ):
            raise ValueError("transcript segments must be ordered by start time")
        return self


class AnalysisRead(CamelModel):
    id: str
    video_id: str
    part_id: str | None
    feature: AnalysisFeature
    status: AnalysisResultStatus
    result: dict[str, object] | None
    model_name: str | None
    model_version: str | None
    parameters: dict[str, object]
    created_at: datetime
    updated_at: datetime


class AnalysisList(CamelModel):
    items: list[AnalysisRead]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class AnalysisCapabilityRead(CamelModel):
    feature: AnalysisFeature
    component: str
    available: bool
    version: str | None
    reason_code: str | None
    message: str
    action: str | None


class AnalysisCapabilities(CamelModel):
    items: list[AnalysisCapabilityRead]


def _matches_uuid(value: str) -> bool:
    return re.fullmatch(_UUID_PATTERN, value) is not None
