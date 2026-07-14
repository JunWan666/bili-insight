from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.analysis import (
    AnalysisError,
    SubtitleDocument,
    SubtitleFormat,
    TranscriptSource,
    export_subtitles,
    from_bilibili_subtitle_json,
    write_subtitle_export,
)


def _public_document() -> SubtitleDocument:
    return from_bilibili_subtitle_json(
        {
            "body": [
                {"from": 2.25, "to": 4.5, "content": "第二句\n换行"},
                {"from": 0.0, "to": 1.2346, "content": "第一句"},
                {"from": "invalid", "to": 9, "content": "无效项"},
            ]
        },
        language="zh-CN",
        generated_at=datetime(2026, 7, 14, tzinfo=UTC),
    )


def test_bilibili_public_subtitle_preserves_source_and_timeline() -> None:
    document = _public_document()
    assert document.source == TranscriptSource.PUBLIC_SUBTITLE
    assert [item.text for item in document.segments] == ["第一句", "第二句\n换行"]
    assert document.segments[0].evidence_id == "public-subtitle-2"


def test_subtitle_exports_srt_vtt_txt_and_json() -> None:
    document = _public_document()
    srt = export_subtitles(document, SubtitleFormat.SRT).decode()
    assert "00:00:00,000 --> 00:00:01,235" in srt
    assert "2\n00:00:02,250 --> 00:00:04,500" in srt

    vtt = export_subtitles(document, "vtt").decode()
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:01.235" in vtt

    text = export_subtitles(document, "txt").decode()
    assert "第二句 / 换行" in text

    payload = json.loads(export_subtitles(document, "json"))
    assert payload["source"] == "public_subtitle"
    assert payload["modelName"] == "bilibili-public-subtitle"
    assert payload["segments"][0]["startSeconds"] == 0


def test_subtitle_export_is_written_atomically(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "captions.srt"
    returned = write_subtitle_export(_public_document(), target)
    assert returned == target.resolve()
    assert returned.read_text(encoding="utf-8").startswith("1\n")
    assert not list(target.parent.glob("*.partial"))


def test_invalid_public_subtitle_and_format_are_actionable() -> None:
    with pytest.raises(AnalysisError) as missing_body:
        from_bilibili_subtitle_json({}, language="zh-CN")
    assert "ASR" in missing_body.value.failure.action

    with pytest.raises(AnalysisError) as bad_format:
        export_subtitles(_public_document(), "ass")
    assert "SRT" in bad_format.value.failure.action


def test_empty_document_exports_valid_empty_formats() -> None:
    document = SubtitleDocument(
        language="zh-CN",
        source=TranscriptSource.OCR,
        segments=(),
        model_name="paddleocr",
        model_version="3",
        generated_at=datetime(2026, 7, 14, tzinfo=UTC),
    )
    assert export_subtitles(document, "srt") == b""
    assert export_subtitles(document, "vtt") == b"WEBVTT\n\n"
    assert export_subtitles(document, "txt") == b""
