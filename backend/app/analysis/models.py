from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class TranscriptSource(StrEnum):
    PUBLIC_SUBTITLE = "public_subtitle"
    ASR = "asr"
    OCR = "ocr"
    EDITED = "edited"


class ContentInputSource(StrEnum):
    METADATA = "metadata"
    PUBLIC_SUBTITLE = "public_subtitle"
    ASR = "asr"
    OCR = "ocr"
    EDITED = "edited"
    SCENE = "scene"
    KEYFRAME = "keyframe"


@dataclass(frozen=True, slots=True)
class CapabilityStatus:
    component: str
    available: bool
    version: str | None
    reason_code: str | None
    message: str
    action: str | None


@dataclass(frozen=True, slots=True)
class ContainerTechnicalInfo:
    format_names: tuple[str, ...]
    format_long_name: str | None
    duration_seconds: float | None
    size_bytes: int | None
    bit_rate: int | None
    start_time_seconds: float | None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class KeyframeStatistics:
    count: int
    timestamps_seconds: tuple[float, ...]
    average_interval_seconds: float | None
    minimum_interval_seconds: float | None
    maximum_interval_seconds: float | None
    truncated: bool


@dataclass(frozen=True, slots=True)
class VideoTechnicalInfo:
    index: int
    codec_name: str | None
    codec_long_name: str | None
    profile: str | None
    level: int | None
    width: int | None
    height: int | None
    pixel_format: str | None
    average_frame_rate: float | None
    real_frame_rate: float | None
    duration_seconds: float | None
    bit_rate: int | None
    frame_count: int | None
    color_range: str | None
    color_space: str | None
    color_transfer: str | None
    color_primaries: str | None
    hdr_type: str
    keyframes: KeyframeStatistics | None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AudioTechnicalInfo:
    index: int
    codec_name: str | None
    codec_long_name: str | None
    profile: str | None
    sample_format: str | None
    sample_rate_hz: int | None
    channels: int | None
    channel_layout: str | None
    duration_seconds: float | None
    bit_rate: int | None
    bits_per_sample: int | None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SubtitleTechnicalInfo:
    index: int
    codec_name: str | None
    language: str | None
    title: str | None


@dataclass(frozen=True, slots=True)
class ChapterTechnicalInfo:
    index: int
    start_seconds: float
    end_seconds: float
    title: str | None


@dataclass(frozen=True, slots=True)
class MediaTechnicalReport:
    probe_name: str
    probe_version: str
    container: ContainerTechnicalInfo
    video_streams: tuple[VideoTechnicalInfo, ...]
    audio_streams: tuple[AudioTechnicalInfo, ...]
    subtitle_streams: tuple[SubtitleTechnicalInfo, ...]
    chapters: tuple[ChapterTechnicalInfo, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LoudnessPoint:
    timestamp_seconds: float
    momentary_lufs: float | None
    short_term_lufs: float | None
    integrated_lufs: float | None
    loudness_range_lu: float | None


@dataclass(frozen=True, slots=True)
class SilenceInterval:
    start_seconds: float
    end_seconds: float
    duration_seconds: float


class AudioContentLabel(StrEnum):
    SILENCE = "silence"
    SPEECH_LIKELY = "speech_likely"
    MUSIC_LIKELY = "music_likely"
    MIXED_OR_UNCERTAIN = "mixed_or_uncertain"


@dataclass(frozen=True, slots=True)
class SpectrumBand:
    key: str
    label: str
    minimum_frequency_hz: float
    maximum_frequency_hz: float
    relative_magnitude: float
    magnitude_share: float
    peak_magnitude: float


@dataclass(frozen=True, slots=True)
class SpectrumOverview:
    analyzer_name: str
    analyzer_version: str
    frequency_scale: str
    minimum_frequency_hz: float
    maximum_frequency_hz: float
    analyzed_duration_seconds: float | None
    time_bins: int
    frequency_bins: int
    dominant_frequency_hz: float | None
    spectral_centroid_hz: float | None
    bands: tuple[SpectrumBand, ...]
    disclaimer: str


@dataclass(frozen=True, slots=True)
class AudioContentSegment:
    index: int
    start_seconds: float
    end_seconds: float
    label: AudioContentLabel
    confidence: float
    speech_band_ratio: float
    spectral_flatness: float
    explanation: str


@dataclass(frozen=True, slots=True)
class AudioContentClassification:
    classifier_name: str
    classifier_version: str
    heuristic: bool
    segments: tuple[AudioContentSegment, ...]
    disclaimer: str
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AudioAnalysis:
    analyzer_name: str
    analyzer_version: str
    stream_index: int
    integrated_loudness_lufs: float | None
    loudness_range_lu: float | None
    sample_peak_dbfs: float | None
    true_peak_dbfs: float | None
    mean_volume_db: float | None
    silence_threshold_db: float
    minimum_silence_seconds: float
    silence_intervals: tuple[SilenceInterval, ...]
    loudness_curve: tuple[LoudnessPoint, ...]
    spectrum_overview: SpectrumOverview | None = None
    content_classification: AudioContentClassification | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SceneSegment:
    index: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    transition_score: float | None


@dataclass(frozen=True, slots=True)
class SceneAnalysis:
    analyzer_name: str
    analyzer_version: str
    threshold: float
    duration_seconds: float
    scenes: tuple[SceneSegment, ...]
    average_scene_length_seconds: float
    scene_density_per_minute: float
    truncated: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KeyframeArtifact:
    index: int
    timestamp_seconds: float
    scene_index: int
    filename: str
    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class KeyframeAnalysis:
    extractor_name: str
    extractor_version: str
    artifacts: tuple[KeyframeArtifact, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SubtitleSegment:
    start_seconds: float
    end_seconds: float
    text: str
    source: TranscriptSource
    language: str
    confidence: float | None = None
    evidence_id: str | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.start_seconds) or self.start_seconds < 0:
            raise ValueError("subtitle segment start must be a finite non-negative number")
        if not math.isfinite(self.end_seconds) or self.end_seconds <= self.start_seconds:
            raise ValueError("subtitle segment end must be greater than start")
        cleaned = "\n".join(
            line.strip() for line in self.text.replace("\x00", "").splitlines() if line.strip()
        )
        if not cleaned:
            raise ValueError("subtitle segment text cannot be empty")
        if len(cleaned) > 20_000:
            raise ValueError("subtitle segment text exceeds the safety limit")
        if self.confidence is not None and (
            not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1
        ):
            raise ValueError("subtitle confidence must be between 0 and 1")
        language = self.language.strip()
        if not language or len(language) > 35:
            raise ValueError("subtitle language is invalid")
        object.__setattr__(self, "text", cleaned)
        object.__setattr__(self, "language", language)


@dataclass(frozen=True, slots=True)
class SubtitleDocument:
    language: str
    source: TranscriptSource
    segments: tuple[SubtitleSegment, ...]
    model_name: str | None
    model_version: str | None
    generated_at: datetime
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ordered = tuple(
            sorted(
                self.segments, key=lambda item: (item.start_seconds, item.end_seconds, item.text)
            )
        )
        if any(segment.source != self.source for segment in ordered):
            raise ValueError("all subtitle segments must match the document source")
        if any(segment.language != self.language for segment in ordered):
            raise ValueError("all subtitle segments must match the document language")
        generated_at = (
            self.generated_at.replace(tzinfo=UTC)
            if self.generated_at.tzinfo is None
            else self.generated_at.astimezone(UTC)
        )
        object.__setattr__(self, "segments", ordered)
        object.__setattr__(self, "generated_at", generated_at)


@dataclass(frozen=True, slots=True)
class MetadataSnapshot:
    title: str
    part_title: str
    description: str
    owner_name: str
    tags: tuple[str, ...]
    stats: tuple[tuple[str, int | float | None], ...]
    published_at: datetime | None
    duration_seconds: float | None
    captured_at: datetime
    evidence_id: str
    current: bool

    def __post_init__(self) -> None:
        text_values = {
            "title": self.title,
            "part_title": self.part_title,
            "description": self.description,
            "owner_name": self.owner_name,
        }
        for name, value in text_values.items():
            cleaned = value.replace("\x00", "").strip()
            if name in {"title", "part_title"} and not cleaned:
                raise ValueError("metadata title fields cannot be empty")
            object.__setattr__(self, name, cleaned)
        tags = tuple(
            dict.fromkeys(tag.replace("\x00", "").strip() for tag in self.tags if tag.strip())
        )
        object.__setattr__(self, "tags", tags)
        if self.duration_seconds is not None and (
            not math.isfinite(self.duration_seconds) or self.duration_seconds < 0
        ):
            raise ValueError("metadata duration must be finite and non-negative")
        for _, stat_value in self.stats:
            if stat_value is not None and not math.isfinite(float(stat_value)):
                raise ValueError("metadata statistics must be finite")
        captured_at = (
            self.captured_at.replace(tzinfo=UTC)
            if self.captured_at.tzinfo is None
            else self.captured_at.astimezone(UTC)
        )
        published_at = self.published_at
        if published_at is not None:
            published_at = (
                published_at.replace(tzinfo=UTC)
                if published_at.tzinfo is None
                else published_at.astimezone(UTC)
            )
        object.__setattr__(self, "captured_at", captured_at)
        object.__setattr__(self, "published_at", published_at)


@dataclass(frozen=True, slots=True)
class VisualEvidence:
    start_seconds: float
    end_seconds: float
    source: ContentInputSource
    evidence_id: str
    text: str
    artifact_id: str | None = None

    def __post_init__(self) -> None:
        if self.source not in {ContentInputSource.SCENE, ContentInputSource.KEYFRAME}:
            raise ValueError("visual evidence source must be scene or keyframe")
        if not math.isfinite(self.start_seconds) or self.start_seconds < 0:
            raise ValueError("visual evidence start must be finite and non-negative")
        if not math.isfinite(self.end_seconds) or self.end_seconds < self.start_seconds:
            raise ValueError("visual evidence end must not precede start")
        text = " ".join(self.text.replace("\x00", "").split())
        if not text or len(text) > 1_000:
            raise ValueError("visual evidence text is invalid")
        object.__setattr__(self, "text", text)


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    start_seconds: float | None
    end_seconds: float | None
    text: str
    source: ContentInputSource
    confidence: float | None
    evidence_id: str | None
    locator: str | None = None
    artifact_id: str | None = None

    def __post_init__(self) -> None:
        if (self.start_seconds is None) != (self.end_seconds is None):
            raise ValueError("evidence timestamps must both be present or absent")
        if self.start_seconds is not None and self.end_seconds is not None:
            if (
                not math.isfinite(self.start_seconds)
                or not math.isfinite(self.end_seconds)
                or self.start_seconds < 0
                or self.end_seconds < self.start_seconds
            ):
                raise ValueError("evidence timestamps are invalid")
        if self.confidence is not None and (
            not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1
        ):
            raise ValueError("evidence confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class SummarySentence:
    text: str
    evidence: EvidenceReference
    score: float


@dataclass(frozen=True, slots=True)
class KeywordResult:
    keyword: str
    score: float
    occurrences: int
    evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True, slots=True)
class ChapterResult:
    index: int
    title: str
    start_seconds: float
    end_seconds: float
    summary: str
    keywords: tuple[str, ...]
    evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True, slots=True)
class TopicResult:
    topic: str
    score: float
    evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    name: str
    category: str
    evidence: EvidenceReference
    limitation: str


@dataclass(frozen=True, slots=True)
class EmotionPoint:
    start_seconds: float
    end_seconds: float
    label: str
    score: float
    evidence: EvidenceReference


@dataclass(frozen=True, slots=True)
class SemanticCapability:
    name: str
    status: str
    method: str
    message: str


@dataclass(frozen=True, slots=True)
class ContentAnalysisReport:
    summary: str
    summary_sentences: tuple[SummarySentence, ...]
    keywords: tuple[KeywordResult, ...]
    chapters: tuple[ChapterResult, ...]
    topics: tuple[TopicResult, ...]
    entity_candidates: tuple[EntityCandidate, ...]
    emotion_timeline: tuple[EmotionPoint, ...]
    visual_evidence: tuple[EvidenceReference, ...]
    semantic_capabilities: tuple[SemanticCapability, ...]
    coverage: str
    model_name: str
    model_version: str
    parameters: dict[str, int | float | str | bool]
    generated_at: datetime
    input_sources: tuple[ContentInputSource, ...]
    input_details: dict[str, int | float | str | bool]
    input_digest_sha256: str
    disclaimer: str
    warnings: tuple[str, ...] = ()
