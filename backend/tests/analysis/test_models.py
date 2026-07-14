from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.analysis import SubtitleDocument, SubtitleSegment, TranscriptSource


def test_subtitle_segment_normalizes_and_validates_values() -> None:
    segment = SubtitleSegment(
        0,
        1,
        "  第一行\x00  \n\n 第二行 ",
        TranscriptSource.ASR,
        " zh-CN ",
        confidence=0.8,
    )
    assert segment.text == "第一行\n第二行"
    assert segment.language == "zh-CN"

    invalid_values: list[tuple[float, float, str, float | None]] = [
        (-1, 1, "文本", None),
        (1, 1, "文本", None),
        (0, 1, "", None),
        (0, 1, "文本", 2),
    ]
    for start, end, text, confidence in invalid_values:
        with pytest.raises(ValueError):
            SubtitleSegment(
                start_seconds=start,
                end_seconds=end,
                text=text,
                source=TranscriptSource.ASR,
                language="zh-CN",
                confidence=confidence,
            )


def test_subtitle_document_orders_segments_and_normalizes_timestamp() -> None:
    later = SubtitleSegment(2, 3, "后", TranscriptSource.OCR, "zh-CN")
    earlier = SubtitleSegment(0, 1, "前", TranscriptSource.OCR, "zh-CN")
    document = SubtitleDocument(
        language="zh-CN",
        source=TranscriptSource.OCR,
        segments=(later, earlier),
        model_name="model",
        model_version="1",
        generated_at=datetime(2026, 7, 14),
    )
    assert document.segments == (earlier, later)
    assert document.generated_at == datetime(2026, 7, 14, tzinfo=UTC)

    wrong_source = SubtitleSegment(0, 1, "错", TranscriptSource.ASR, "zh-CN")
    with pytest.raises(ValueError):
        SubtitleDocument(
            language="zh-CN",
            source=TranscriptSource.OCR,
            segments=(wrong_source,),
            model_name=None,
            model_version=None,
            generated_at=datetime.now(UTC),
        )
