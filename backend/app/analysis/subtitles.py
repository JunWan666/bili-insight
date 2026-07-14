from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.analysis.errors import AnalysisError, AnalysisErrorCode, AnalysisFailure
from app.analysis.models import SubtitleDocument, SubtitleSegment, TranscriptSource


class SubtitleFormat(StrEnum):
    SRT = "srt"
    VTT = "vtt"
    TXT = "txt"
    JSON = "json"


def subtitle_document(
    segments: Sequence[SubtitleSegment],
    *,
    language: str,
    source: TranscriptSource,
    model_name: str | None = None,
    model_version: str | None = None,
    generated_at: datetime | None = None,
    warnings: Sequence[str] = (),
) -> SubtitleDocument:
    return SubtitleDocument(
        language=language,
        source=source,
        segments=tuple(segments),
        model_name=model_name,
        model_version=model_version,
        generated_at=generated_at or datetime.now(UTC),
        warnings=tuple(warnings),
    )


def from_bilibili_subtitle_json(
    payload: Mapping[str, Any],
    *,
    language: str,
    generated_at: datetime | None = None,
) -> SubtitleDocument:
    raw_body = payload.get("body")
    if not isinstance(raw_body, list):
        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.INVALID_MEDIA,
                message="公开字幕内容格式无法识别",
                action="重新获取公开字幕；若仍失败可改用 ASR 或 OCR",
            )
        )
    segments: list[SubtitleSegment] = []
    for index, raw_item in enumerate(raw_body):
        if not isinstance(raw_item, dict):
            continue
        start = _number(raw_item.get("from"))
        end = _number(raw_item.get("to"))
        text = raw_item.get("content")
        if start is None or end is None or not isinstance(text, str) or end <= start:
            continue
        try:
            segments.append(
                SubtitleSegment(
                    start_seconds=start,
                    end_seconds=end,
                    text=text,
                    source=TranscriptSource.PUBLIC_SUBTITLE,
                    language=language,
                    evidence_id=f"public-subtitle-{index + 1}",
                )
            )
        except ValueError:
            continue
    if not segments:
        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.INVALID_MEDIA,
                message="公开字幕中没有可用的时间轴文本",
                action="重新获取字幕或选择 ASR/OCR 文本提取",
            )
        )
    return subtitle_document(
        segments,
        language=language,
        source=TranscriptSource.PUBLIC_SUBTITLE,
        model_name="bilibili-public-subtitle",
        model_version="1",
        generated_at=generated_at,
    )


def export_subtitles(document: SubtitleDocument, output_format: SubtitleFormat | str) -> bytes:
    try:
        format_value = SubtitleFormat(output_format)
    except ValueError as exc:
        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.INVALID_CONFIGURATION,
                message="不支持所选字幕导出格式",
                action="选择 SRT、VTT、TXT 或 JSON 格式",
            )
        ) from exc
    if format_value == SubtitleFormat.SRT:
        content = _to_srt(document)
    elif format_value == SubtitleFormat.VTT:
        content = _to_vtt(document)
    elif format_value == SubtitleFormat.TXT:
        content = _to_txt(document)
    else:
        content = (
            json.dumps(
                subtitle_document_to_dict(document),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        )
    return content.encode("utf-8")


def write_subtitle_export(
    document: SubtitleDocument,
    output_path: str | Path,
    *,
    output_format: SubtitleFormat | str | None = None,
) -> Path:
    target = Path(output_path).expanduser()
    if output_format is None:
        suffix = target.suffix.lower().lstrip(".")
        output_format = suffix
    content = export_subtitles(document, output_format)
    try:
        parent = target.parent.resolve()
        parent.mkdir(parents=True, exist_ok=True)
        resolved_target = parent / target.name
        if resolved_target.is_symlink() or resolved_target.is_dir():
            raise OSError("unsafe subtitle target")
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".partial", dir=parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as file_handle:
                file_handle.write(content)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(temporary_path, resolved_target)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.EXPORT_FAILED,
                message="字幕产物写入失败",
                action="检查产物目录权限与磁盘空间后重试",
                diagnostic=f"subtitle export failed: {type(exc).__name__}",
            )
        ) from exc
    return resolved_target


def subtitle_document_to_dict(document: SubtitleDocument) -> dict[str, Any]:
    return {
        "language": document.language,
        "source": document.source.value,
        "modelName": document.model_name,
        "modelVersion": document.model_version,
        "generatedAt": document.generated_at.astimezone(UTC).isoformat(),
        "warnings": list(document.warnings),
        "segments": [
            {
                "index": index,
                "startSeconds": segment.start_seconds,
                "endSeconds": segment.end_seconds,
                "text": segment.text,
                "source": segment.source.value,
                "language": segment.language,
                "confidence": segment.confidence,
                "evidenceId": segment.evidence_id,
            }
            for index, segment in enumerate(document.segments, start=1)
        ],
    }


def _to_srt(document: SubtitleDocument) -> str:
    blocks = [
        f"{index}\n{_timestamp(segment.start_seconds, comma=True)} --> "
        f"{_timestamp(segment.end_seconds, comma=True)}\n{segment.text}"
        for index, segment in enumerate(document.segments, start=1)
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _to_vtt(document: SubtitleDocument) -> str:
    blocks = [
        f"{index}\n{_timestamp(segment.start_seconds, comma=False)} --> "
        f"{_timestamp(segment.end_seconds, comma=False)}\n{segment.text}"
        for index, segment in enumerate(document.segments, start=1)
    ]
    body = "\n\n".join(blocks)
    return f"WEBVTT\n\n{body}{'\n' if blocks else ''}"


def _to_txt(document: SubtitleDocument) -> str:
    lines = [
        f"[{_timestamp(segment.start_seconds, comma=False)} --> "
        f"{_timestamp(segment.end_seconds, comma=False)}] "
        f"{segment.text.replace(chr(10), ' / ')}"
        for segment in document.segments
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def _timestamp(seconds: float, *, comma: bool) -> str:
    total_milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    separator = "," if comma else "."
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{separator}{milliseconds:03d}"


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed
