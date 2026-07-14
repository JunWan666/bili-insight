from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.analysis import (
    AnalysisError,
    ContentInputSource,
    LocalContentAnalyzer,
    LocalSummaryConfig,
    MetadataSnapshot,
    SubtitleDocument,
    SubtitleSegment,
    TranscriptSource,
    VisualEvidence,
    content_report_json,
    subtitle_document,
)

GENERATED_AT = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


def _documents() -> list[SubtitleDocument]:
    public = subtitle_document(
        [
            SubtitleSegment(
                0,
                5,
                "量子计算使用量子比特处理信息。",
                TranscriptSource.PUBLIC_SUBTITLE,
                "zh-CN",
                evidence_id="subtitle-1",
            ),
            SubtitleSegment(
                6,
                11,
                "量子叠加让量子计算同时表达多种状态。",
                TranscriptSource.PUBLIC_SUBTITLE,
                "zh-CN",
                evidence_id="subtitle-2",
            ),
            SubtitleSegment(
                18,
                24,
                "随后介绍量子纠错如何保护脆弱的量子信息。",
                TranscriptSource.PUBLIC_SUBTITLE,
                "zh-CN",
                evidence_id="subtitle-3",
            ),
            SubtitleSegment(
                30,
                36,
                "最后总结量子计算仍面临工程挑战。",
                TranscriptSource.PUBLIC_SUBTITLE,
                "zh-CN",
                evidence_id="subtitle-4",
            ),
        ],
        language="zh-CN",
        source=TranscriptSource.PUBLIC_SUBTITLE,
        model_name="public",
        model_version="1",
        generated_at=GENERATED_AT,
    )
    duplicate_asr = subtitle_document(
        [
            SubtitleSegment(
                0,
                5,
                "量子计算使用量子比特处理信息。",
                TranscriptSource.ASR,
                "zh-CN",
                confidence=0.82,
                evidence_id="asr-duplicate",
            )
        ],
        language="zh-CN",
        source=TranscriptSource.ASR,
        model_name="faster-whisper:small",
        model_version="1.1",
        generated_at=GENERATED_AT,
    )
    ocr = subtitle_document(
        [
            SubtitleSegment(
                17,
                20,
                "量子纠错",
                TranscriptSource.OCR,
                "zh-CN",
                confidence=0.94,
                evidence_id="keyframe-17.jpg",
            )
        ],
        language="zh-CN",
        source=TranscriptSource.OCR,
        model_name="paddleocr:ch",
        model_version="3",
        generated_at=GENERATED_AT,
    )
    return [public, duplicate_asr, ocr]


def test_local_summary_is_reproducible_and_evidence_backed() -> None:
    analyzer = LocalContentAnalyzer(
        LocalSummaryConfig(
            maximum_summary_sentences=3,
            maximum_keywords=6,
            target_chapter_seconds=12,
            minimum_chapter_seconds=5,
        )
    )
    documents = _documents()
    first = analyzer.analyze(documents, generated_at=GENERATED_AT)
    second = analyzer.analyze(list(reversed(documents)), generated_at=GENERATED_AT)

    assert content_report_json(first) == content_report_json(second)
    assert first.model_name == "local-extractive-evidence-analyzer"
    assert first.model_version == "2.0.0"
    assert first.generated_at == GENERATED_AT
    assert len(first.input_digest_sha256) == 64
    assert "可能存在误差" in first.disclaimer
    assert first.summary_sentences
    assert all(
        item.evidence.start_seconds is not None
        and item.evidence.end_seconds is not None
        and item.evidence.end_seconds > item.evidence.start_seconds
        for item in first.summary_sentences
    )
    assert all(item.evidence.evidence_id for item in first.summary_sentences)
    assert len(first.chapters) >= 2
    assert all(chapter.evidence for chapter in first.chapters)
    assert any(keyword.keyword.startswith("量子") for keyword in first.keywords)
    duplicate_evidence = [
        item.evidence.evidence_id
        for item in first.summary_sentences
        if item.text == "量子计算使用量子比特处理信息。"
    ]
    assert "asr-duplicate" not in duplicate_evidence

    payload = json.loads(content_report_json(first))
    assert payload["inputDigestSha256"] == first.input_digest_sha256
    assert payload["summarySentences"][0]["evidence"]["startSeconds"] >= 0


def test_empty_context_is_rejected_instead_of_publishing_an_empty_summary() -> None:
    with pytest.raises(AnalysisError, match="没有可用于摘要"):
        LocalContentAnalyzer().analyze([], generated_at=GENERATED_AT)


def test_metadata_only_and_visual_context_disclose_coverage_and_capability_limits() -> None:
    metadata = MetadataSnapshot(
        title="量子计算入门",
        part_title="纠错与挑战",
        description="从量子比特讲到工程挑战。",
        owner_name="科学频道",
        tags=("量子计算", "科普"),
        stats=(("view", 1200), ("like", 80)),
        published_at=GENERATED_AT,
        duration_seconds=40,
        captured_at=GENERATED_AT,
        evidence_id="metadata-current",
        current=True,
    )
    metadata_only = LocalContentAnalyzer().analyze(
        [], metadata_snapshots=[metadata], generated_at=GENERATED_AT
    )

    assert metadata_only.coverage == "metadata_only"
    assert metadata_only.input_sources == (ContentInputSource.METADATA,)
    assert metadata_only.summary_sentences[0].evidence.start_seconds is None
    assert "无法可靠推断" in metadata_only.summary
    assert not metadata_only.chapters
    entity_capability = next(
        item for item in metadata_only.semantic_capabilities if item.name == "entities"
    )
    assert entity_capability.status == "limited"
    assert "未识别" in entity_capability.message

    visual = VisualEvidence(
        start_seconds=18,
        end_seconds=18,
        source=ContentInputSource.KEYFRAME,
        evidence_id="keyframe-18",
        text="第二个镜头的关键帧",
        artifact_id="artifact-keyframe",
    )
    combined = LocalContentAnalyzer(
        LocalSummaryConfig(target_chapter_seconds=12, minimum_chapter_seconds=5)
    ).analyze(
        _documents(),
        metadata_snapshots=[metadata],
        visual_evidence=[visual],
        generated_at=GENERATED_AT,
    )

    assert combined.coverage == "text_and_visual_evidence"
    assert ContentInputSource.KEYFRAME in combined.input_sources
    assert combined.input_details["keyframeEvidenceCount"] == 1
    assert any(
        evidence.artifact_id == "artifact-keyframe"
        for chapter in combined.chapters
        for evidence in chapter.evidence
    )


def test_ocr_only_summary_discloses_source_limit() -> None:
    document = subtitle_document(
        [
            SubtitleSegment(
                0,
                2,
                "片头标题",
                TranscriptSource.OCR,
                "zh-CN",
                confidence=0.8,
            )
        ],
        language="zh-CN",
        source=TranscriptSource.OCR,
        generated_at=GENERATED_AT,
    )
    result = LocalContentAnalyzer().analyze([document], generated_at=GENERATED_AT)
    assert any("仅依据画面 OCR" in warning for warning in result.warnings)


def test_summary_configuration_bounds() -> None:
    invalid_values = [
        {"maximum_summary_sentences": 0},
        {"maximum_keywords": 0},
        {"maximum_keyword_evidence": 0},
        {"target_chapter_seconds": 1},
        {"minimum_chapter_seconds": 500},
        {"maximum_chapters": 0},
        {"maximum_evidence_per_chapter": 0},
    ]
    for values in invalid_values:
        with pytest.raises(ValueError):
            LocalSummaryConfig(**values)
