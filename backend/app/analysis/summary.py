from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.analysis.errors import AnalysisError, AnalysisErrorCode, AnalysisFailure
from app.analysis.models import (
    ChapterResult,
    ContentAnalysisReport,
    ContentInputSource,
    EmotionPoint,
    EntityCandidate,
    EvidenceReference,
    KeywordResult,
    MetadataSnapshot,
    SemanticCapability,
    SubtitleDocument,
    SubtitleSegment,
    SummarySentence,
    TopicResult,
    TranscriptSource,
    VisualEvidence,
)

_MODEL_NAME = "local-extractive-evidence-analyzer"
_MODEL_VERSION = "2.0.0"
_DISCLAIMER = "自动分析结果，可能存在误差；请结合时间戳证据核对原始内容。"
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])|\n+")
_LATIN_WORD = re.compile(r"[a-z0-9][a-z0-9_+#.-]{1,63}", re.IGNORECASE)
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
_WHITESPACE = re.compile(r"\s+")
_SOURCE_WEIGHT = {
    TranscriptSource.EDITED: 1.15,
    TranscriptSource.PUBLIC_SUBTITLE: 1.1,
    TranscriptSource.ASR: 0.95,
    TranscriptSource.OCR: 0.8,
}
_SOURCE_ORDER = {
    TranscriptSource.EDITED: 0,
    TranscriptSource.PUBLIC_SUBTITLE: 1,
    TranscriptSource.ASR: 2,
    TranscriptSource.OCR: 3,
}
_INPUT_SOURCE_ORDER = {
    ContentInputSource.METADATA: 0,
    ContentInputSource.EDITED: 1,
    ContentInputSource.PUBLIC_SUBTITLE: 2,
    ContentInputSource.ASR: 3,
    ContentInputSource.OCR: 4,
    ContentInputSource.SCENE: 5,
    ContentInputSource.KEYFRAME: 6,
}
_TRANSCRIPT_INPUT_SOURCE = {
    TranscriptSource.PUBLIC_SUBTITLE: ContentInputSource.PUBLIC_SUBTITLE,
    TranscriptSource.ASR: ContentInputSource.ASR,
    TranscriptSource.OCR: ContentInputSource.OCR,
    TranscriptSource.EDITED: ContentInputSource.EDITED,
}
_POSITIVE_TERMS = {
    "开心",
    "快乐",
    "成功",
    "喜欢",
    "优秀",
    "激动",
    "希望",
    "赞",
    "happy",
    "success",
    "love",
}
_NEGATIVE_TERMS = {
    "难过",
    "失败",
    "错误",
    "困难",
    "痛苦",
    "担忧",
    "生气",
    "风险",
    "挑战",
    "sad",
    "failure",
    "risk",
}
_STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "because",
    "before",
    "but",
    "can",
    "could",
    "from",
    "have",
    "into",
    "just",
    "more",
    "not",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "they",
    "this",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "will",
    "with",
    "would",
    "一个",
    "一些",
    "以及",
    "但是",
    "你们",
    "他们",
    "什么",
    "今天",
    "仍然",
    "可以",
    "因为",
    "大家",
    "如果",
    "已经",
    "我们",
    "所以",
    "时候",
    "然后",
    "现在",
    "这个",
    "这里",
    "还是",
    "进行",
    "那个",
    "那里",
    "就是",
    "这种",
}


@dataclass(frozen=True, slots=True)
class LocalSummaryConfig:
    maximum_summary_sentences: int = 5
    maximum_keywords: int = 12
    maximum_keyword_evidence: int = 3
    target_chapter_seconds: float = 180.0
    minimum_chapter_seconds: float = 30.0
    maximum_chapters: int = 24
    maximum_evidence_per_chapter: int = 3

    def __post_init__(self) -> None:
        if not 1 <= self.maximum_summary_sentences <= 50:
            raise ValueError("maximum summary sentences must be between 1 and 50")
        if not 1 <= self.maximum_keywords <= 100:
            raise ValueError("maximum keywords must be between 1 and 100")
        if not 1 <= self.maximum_keyword_evidence <= 20:
            raise ValueError("maximum keyword evidence must be between 1 and 20")
        if not math.isfinite(self.target_chapter_seconds) or not (
            5 <= self.target_chapter_seconds <= 7_200
        ):
            raise ValueError("target chapter length must be between 5 and 7200 seconds")
        if not math.isfinite(self.minimum_chapter_seconds) or not (
            1 <= self.minimum_chapter_seconds <= self.target_chapter_seconds
        ):
            raise ValueError("minimum chapter length is invalid")
        if not 1 <= self.maximum_chapters <= 200:
            raise ValueError("maximum chapters must be between 1 and 200")
        if not 1 <= self.maximum_evidence_per_chapter <= 20:
            raise ValueError("chapter evidence limit must be between 1 and 20")


@dataclass(frozen=True, slots=True)
class _Unit:
    start: float
    end: float
    text: str
    normalized_text: str
    source: TranscriptSource
    confidence: float | None
    evidence_id: str | None
    tokens: tuple[str, ...]

    def evidence(self) -> EvidenceReference:
        return EvidenceReference(
            start_seconds=self.start,
            end_seconds=self.end,
            text=self.text,
            source=_TRANSCRIPT_INPUT_SOURCE[self.source],
            confidence=self.confidence,
            evidence_id=self.evidence_id,
            locator=f"time:{self.start:.3f}-{self.end:.3f}",
        )


class LocalContentAnalyzer:
    def __init__(self, config: LocalSummaryConfig | None = None) -> None:
        self.config = config or LocalSummaryConfig()

    def analyze(
        self,
        documents: Sequence[SubtitleDocument],
        *,
        metadata_snapshots: Sequence[MetadataSnapshot] = (),
        visual_evidence: Sequence[VisualEvidence] = (),
        collection_warnings: Sequence[str] = (),
        generated_at: datetime | None = None,
    ) -> ContentAnalysisReport:
        validate_content_documents(documents)
        _validate_context_inputs(metadata_snapshots, visual_evidence)
        timestamp = _utc_datetime(generated_at or datetime.now(UTC))
        digest = _input_digest(documents, metadata_snapshots, visual_evidence)
        units = _build_units(documents)
        parameters = self._parameters()
        warnings = [*_input_warnings(documents), *collection_warnings]
        current_metadata = next(
            (snapshot for snapshot in metadata_snapshots if snapshot.current),
            metadata_snapshots[0] if metadata_snapshots else None,
        )
        if not units and current_metadata is None:
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.INVALID_CONFIGURATION,
                    message="没有可用于摘要的元数据或带证据文本",
                    action="重新解析视频，或先运行公开字幕、ASR/OCR 后重试",
                )
            )

        token_statistics = _token_statistics(units) if units else {}
        transcript_sentence_limit = self.config.maximum_summary_sentences
        metadata_sentence = (
            _metadata_summary_sentence(current_metadata) if current_metadata is not None else None
        )
        if metadata_sentence is not None and transcript_sentence_limit > 1:
            transcript_sentence_limit -= 1
        transcript_sentences = (
            _select_summary_sentences(units, token_statistics, transcript_sentence_limit)
            if units
            else ()
        )
        summary_sentences = (
            (metadata_sentence,) if metadata_sentence is not None else ()
        ) + transcript_sentences
        transcript_keywords = (
            _select_keywords(
                units,
                token_statistics,
                self.config.maximum_keywords,
                self.config.maximum_keyword_evidence,
            )
            if units
            else ()
        )
        keywords = _merge_keywords(
            transcript_keywords,
            _metadata_keywords(current_metadata, self.config.maximum_keywords),
            maximum=self.config.maximum_keywords,
        )
        chapters = (
            _attach_visual_evidence(
                _build_chapters(units, token_statistics, self.config),
                visual_evidence,
                maximum_per_chapter=self.config.maximum_evidence_per_chapter,
            )
            if units
            else ()
        )
        summary = "；".join(item.text.rstrip("。！？!?；; ") for item in summary_sentences)
        if summary:
            summary += "。"
        if not units:
            summary += (
                " 本次未取得带时间戳的字幕、ASR 或 OCR，"
                "无法可靠推断章节内容、视频内人物/对象及情绪走向。"
            )
            warnings.append("当前为元数据有限概览；若需语义结论，请补充公开字幕、ASR 或 OCR")
        if _metadata_history_changed(metadata_snapshots):
            warnings.append("检测到历史元数据与当前值存在差异；概览以当前解析结果为准")
        if visual_evidence:
            warnings.append("镜头与关键帧仅用于结构定位；未运行视觉实体模型，不据此识别人脸或对象")

        topics = tuple(
            TopicResult(topic=item.keyword, score=item.score, evidence=item.evidence)
            for item in keywords[: min(6, len(keywords))]
        )
        entity_candidates = (
            (_creator_candidate(current_metadata),) if current_metadata is not None else ()
        )
        emotion_timeline = _emotion_timeline(units)
        visual_references = tuple(_visual_reference(item) for item in visual_evidence[:200])
        coverage = _coverage(units, visual_evidence)
        capabilities = _semantic_capabilities(
            chapters=chapters,
            topics=topics,
            emotion_timeline=emotion_timeline,
            visual_evidence=visual_evidence,
        )
        source_values = {ContentInputSource.METADATA} if metadata_snapshots else set()
        source_values.update(_TRANSCRIPT_INPUT_SOURCE[item.source] for item in documents)
        source_values.update(item.source for item in visual_evidence)
        sources = tuple(sorted(source_values, key=_INPUT_SOURCE_ORDER.__getitem__))
        return ContentAnalysisReport(
            summary=summary,
            summary_sentences=summary_sentences,
            keywords=keywords,
            chapters=chapters,
            topics=topics,
            entity_candidates=entity_candidates,
            emotion_timeline=emotion_timeline,
            visual_evidence=visual_references,
            semantic_capabilities=capabilities,
            coverage=coverage,
            model_name=_MODEL_NAME,
            model_version=_MODEL_VERSION,
            parameters=parameters,
            generated_at=timestamp,
            input_sources=sources,
            input_details={
                "metadataSnapshotCount": len(metadata_snapshots),
                "textDocumentCount": len(documents),
                "textSegmentCount": sum(len(document.segments) for document in documents),
                "sceneEvidenceCount": sum(
                    item.source == ContentInputSource.SCENE for item in visual_evidence
                ),
                "keyframeEvidenceCount": sum(
                    item.source == ContentInputSource.KEYFRAME for item in visual_evidence
                ),
                "coverage": coverage,
            },
            input_digest_sha256=digest,
            disclaimer=_DISCLAIMER,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def _parameters(self) -> dict[str, int | float | str | bool]:
        return {
            "maximumSummarySentences": self.config.maximum_summary_sentences,
            "maximumKeywords": self.config.maximum_keywords,
            "maximumKeywordEvidence": self.config.maximum_keyword_evidence,
            "targetChapterSeconds": self.config.target_chapter_seconds,
            "minimumChapterSeconds": self.config.minimum_chapter_seconds,
            "maximumChapters": self.config.maximum_chapters,
            "maximumEvidencePerChapter": self.config.maximum_evidence_per_chapter,
            "algorithm": "deterministic-evidence-v2",
            "metadataPolicy": "current-with-history-digest",
            "visualPolicy": "structural-location-only",
            "entityRecognition": False,
            "emotionMethod": "explicit-lexicon-signals-only",
        }


def content_report_to_dict(report: ContentAnalysisReport) -> dict[str, Any]:
    return {
        "summary": report.summary,
        "summarySentences": [
            {
                "text": item.text,
                "score": item.score,
                "evidence": _evidence_to_dict(item.evidence),
            }
            for item in report.summary_sentences
        ],
        "keywords": [
            {
                "keyword": item.keyword,
                "score": item.score,
                "occurrences": item.occurrences,
                "evidence": [_evidence_to_dict(value) for value in item.evidence],
            }
            for item in report.keywords
        ],
        "chapters": [
            {
                "index": item.index,
                "title": item.title,
                "startSeconds": item.start_seconds,
                "endSeconds": item.end_seconds,
                "summary": item.summary,
                "keywords": list(item.keywords),
                "evidence": [_evidence_to_dict(value) for value in item.evidence],
            }
            for item in report.chapters
        ],
        "topics": [
            {
                "topic": item.topic,
                "score": item.score,
                "evidence": [_evidence_to_dict(value) for value in item.evidence],
            }
            for item in report.topics
        ],
        "entityCandidates": [
            {
                "name": item.name,
                "category": item.category,
                "evidence": _evidence_to_dict(item.evidence),
                "limitation": item.limitation,
            }
            for item in report.entity_candidates
        ],
        "emotionTimeline": [
            {
                "startSeconds": item.start_seconds,
                "endSeconds": item.end_seconds,
                "label": item.label,
                "score": item.score,
                "evidence": _evidence_to_dict(item.evidence),
            }
            for item in report.emotion_timeline
        ],
        "visualEvidence": [_evidence_to_dict(item) for item in report.visual_evidence],
        "semanticCapabilities": [
            {
                "name": item.name,
                "status": item.status,
                "method": item.method,
                "message": item.message,
            }
            for item in report.semantic_capabilities
        ],
        "coverage": report.coverage,
        "modelName": report.model_name,
        "modelVersion": report.model_version,
        "parameters": report.parameters,
        "generatedAt": report.generated_at.astimezone(UTC).isoformat(),
        "inputSources": [item.value for item in report.input_sources],
        "inputDetails": report.input_details,
        "inputDigestSha256": report.input_digest_sha256,
        "disclaimer": report.disclaimer,
        "warnings": list(report.warnings),
    }


def content_report_json(report: ContentAnalysisReport) -> bytes:
    return (
        json.dumps(
            content_report_to_dict(report),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _build_units(documents: Sequence[SubtitleDocument]) -> list[_Unit]:
    segments = [segment for document in documents for segment in document.segments]
    segments.sort(
        key=lambda item: (
            item.start_seconds,
            _SOURCE_ORDER[item.source],
            item.end_seconds,
            item.text,
        )
    )
    best_by_overlap: list[SubtitleSegment] = []
    for segment in segments:
        normalized = _normalize_text(segment.text)
        replacement_index: int | None = None
        duplicate = False
        for index in range(len(best_by_overlap) - 1, -1, -1):
            existing = best_by_overlap[index]
            if existing.end_seconds + 2 < segment.start_seconds:
                break
            if _normalize_text(existing.text) != normalized:
                continue
            duplicate = True
            if _SOURCE_ORDER[segment.source] < _SOURCE_ORDER[existing.source]:
                replacement_index = index
            break
        if replacement_index is not None:
            best_by_overlap[replacement_index] = segment
        elif not duplicate:
            best_by_overlap.append(segment)

    units: list[_Unit] = []
    for segment in best_by_overlap:
        pieces = [item.strip() for item in _SENTENCE_SPLIT.split(segment.text) if item.strip()]
        for piece in pieces:
            normalized = _normalize_text(piece)
            if len(normalized) < 2:
                continue
            units.append(
                _Unit(
                    start=segment.start_seconds,
                    end=segment.end_seconds,
                    text=piece[:1_000],
                    normalized_text=normalized[:1_000],
                    source=segment.source,
                    confidence=segment.confidence,
                    evidence_id=segment.evidence_id,
                    tokens=tuple(_tokens(normalized)),
                )
            )
    collapsed = _collapse_repeated_ocr(units)
    return sorted(collapsed, key=lambda item: (item.start, item.end, item.text))


def _collapse_repeated_ocr(units: list[_Unit]) -> list[_Unit]:
    collapsed: list[_Unit] = []
    for unit in units:
        if (
            collapsed
            and unit.source == TranscriptSource.OCR
            and collapsed[-1].source == TranscriptSource.OCR
            and unit.normalized_text == collapsed[-1].normalized_text
            and unit.start - collapsed[-1].end <= 10
        ):
            previous = collapsed[-1]
            confidences = [
                value for value in (previous.confidence, unit.confidence) if value is not None
            ]
            collapsed[-1] = _Unit(
                start=previous.start,
                end=max(previous.end, unit.end),
                text=previous.text,
                normalized_text=previous.normalized_text,
                source=previous.source,
                confidence=sum(confidences) / len(confidences) if confidences else None,
                evidence_id=previous.evidence_id,
                tokens=previous.tokens,
            )
        else:
            collapsed.append(unit)
    return collapsed


def _token_statistics(units: Sequence[_Unit]) -> dict[str, float]:
    occurrences: Counter[str] = Counter()
    document_frequency: Counter[str] = Counter()
    for unit in units:
        occurrences.update(unit.tokens)
        document_frequency.update(set(unit.tokens))
    total_units = len(units)
    statistics: dict[str, float] = {}
    for token, count in occurrences.items():
        document_count = document_frequency[token]
        inverse_frequency = math.log(1.0 + total_units / document_count)
        length_weight = 1.0 + min(len(token), 4) * 0.08
        statistics[token] = count * inverse_frequency * length_weight
    return statistics


def _select_summary_sentences(
    units: Sequence[_Unit], token_statistics: Mapping[str, float], maximum: int
) -> tuple[SummarySentence, ...]:
    scored = [(unit, _unit_score(unit, token_statistics)) for unit in units]
    selected: list[tuple[_Unit, float]] = []
    candidates = sorted(scored, key=lambda item: (-item[1], item[0].start, item[0].text))
    while candidates and len(selected) < maximum:
        best_index = 0
        best_adjusted = -math.inf
        for index, (unit, base_score) in enumerate(candidates):
            similarity = max(
                (_jaccard(unit.tokens, chosen.tokens) for chosen, _ in selected), default=0.0
            )
            adjusted = base_score * (1.0 - 0.65 * similarity)
            if adjusted > best_adjusted:
                best_adjusted = adjusted
                best_index = index
        unit, base_score = candidates.pop(best_index)
        if any(
            unit.normalized_text in chosen.normalized_text
            or chosen.normalized_text in unit.normalized_text
            for chosen, _ in selected
        ):
            continue
        selected.append((unit, base_score))
    selected.sort(key=lambda item: (item[0].start, item[0].end, item[0].text))
    return tuple(
        SummarySentence(text=unit.text, evidence=unit.evidence(), score=round(score, 6))
        for unit, score in selected
    )


def _select_keywords(
    units: Sequence[_Unit],
    token_statistics: Mapping[str, float],
    maximum: int,
    evidence_limit: int,
) -> tuple[KeywordResult, ...]:
    occurrences: Counter[str] = Counter(token for unit in units for token in unit.tokens)
    ranked = sorted(
        token_statistics,
        key=lambda token: (-token_statistics[token], -len(token), token),
    )
    selected: list[str] = []
    for token in ranked:
        if token in _STOP_WORDS or _uninformative_token(token):
            continue
        if any(
            (token in existing or existing in token)
            and min(len(token), len(existing)) / max(len(token), len(existing)) >= 0.5
            for existing in selected
        ):
            continue
        selected.append(token)
        if len(selected) >= maximum:
            break
    results: list[KeywordResult] = []
    for token in selected:
        evidence = tuple(unit.evidence() for unit in units if token in unit.tokens)[:evidence_limit]
        results.append(
            KeywordResult(
                keyword=token,
                score=round(token_statistics[token], 6),
                occurrences=occurrences[token],
                evidence=evidence,
            )
        )
    return tuple(results)


def _build_chapters(
    units: Sequence[_Unit],
    global_statistics: Mapping[str, float],
    config: LocalSummaryConfig,
) -> tuple[ChapterResult, ...]:
    groups: list[list[_Unit]] = []
    current: list[_Unit] = []
    current_start = units[0].start
    previous_end = units[0].start
    gap_threshold = max(5.0, min(20.0, config.target_chapter_seconds * 0.15))
    for unit in units:
        elapsed = unit.start - current_start
        gap = unit.start - previous_end
        can_split = len(groups) + 1 < config.maximum_chapters
        if (
            current
            and can_split
            and (
                elapsed >= config.target_chapter_seconds
                or (elapsed >= config.minimum_chapter_seconds and gap >= gap_threshold)
            )
        ):
            groups.append(current)
            current = []
            current_start = unit.start
        current.append(unit)
        previous_end = max(previous_end, unit.end)
    if current:
        groups.append(current)

    results: list[ChapterResult] = []
    for index, group in enumerate(groups, start=1):
        chapter_statistics = _token_statistics(group)
        chapter_keywords = _chapter_keywords(chapter_statistics, 3)
        title = "、".join(chapter_keywords) if chapter_keywords else f"第 {index} 章"
        ranked = sorted(
            group,
            key=lambda unit: (-_unit_score(unit, global_statistics), unit.start, unit.text),
        )
        evidence_units = sorted(
            ranked[: config.maximum_evidence_per_chapter],
            key=lambda unit: (unit.start, unit.end, unit.text),
        )
        representative = ranked[0]
        results.append(
            ChapterResult(
                index=index,
                title=title,
                start_seconds=group[0].start,
                end_seconds=max(item.end for item in group),
                summary=representative.text,
                keywords=chapter_keywords,
                evidence=tuple(item.evidence() for item in evidence_units),
            )
        )
    return tuple(results)


def _chapter_keywords(statistics: Mapping[str, float], maximum: int) -> tuple[str, ...]:
    selected: list[str] = []
    for token in sorted(statistics, key=lambda item: (-statistics[item], -len(item), item)):
        if token in _STOP_WORDS or _uninformative_token(token):
            continue
        if any(token in existing or existing in token for existing in selected):
            continue
        selected.append(token)
        if len(selected) >= maximum:
            break
    return tuple(selected)


def _unit_score(unit: _Unit, token_statistics: Mapping[str, float]) -> float:
    unique_tokens = set(unit.tokens)
    keyword_score = sum(token_statistics.get(token, 0.0) for token in unique_tokens)
    length_normalizer = math.sqrt(max(1, len(unique_tokens)))
    confidence = unit.confidence if unit.confidence is not None else 0.75
    return (
        keyword_score / length_normalizer * _SOURCE_WEIGHT[unit.source] * (0.65 + 0.35 * confidence)
    )


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _LATIN_WORD.finditer(text):
        token = match.group(0).lower().strip(".-_")
        if len(token) >= 2 and token not in _STOP_WORDS:
            tokens.append(token)
    for match in _CJK_RUN.finditer(text):
        run = match.group(0)
        for size in (2, 3, 4):
            if len(run) < size:
                continue
            for start in range(len(run) - size + 1):
                token = run[start : start + size]
                if token not in _STOP_WORDS:
                    tokens.append(token)
    return tokens


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return _WHITESPACE.sub(" ", normalized).strip()


def _uninformative_token(token: str) -> bool:
    if len(set(token)) == 1:
        return True
    particles = "的了呢啊吧吗呀哦嗯是有在和与及"
    return token.isdigit() or all(character in particles for character in token)


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    return len(left_set & right_set) / len(union) if union else 0.0


def _metadata_reference(
    snapshot: MetadataSnapshot,
    *,
    locator: str = "video.metadata",
    text: str | None = None,
) -> EvidenceReference:
    return EvidenceReference(
        start_seconds=None,
        end_seconds=None,
        text=text or snapshot.title,
        source=ContentInputSource.METADATA,
        confidence=None,
        evidence_id=snapshot.evidence_id,
        locator=locator,
    )


def _metadata_summary_sentence(snapshot: MetadataSnapshot) -> SummarySentence:
    details = [f"视频《{snapshot.title}》"]
    if snapshot.part_title and snapshot.part_title != snapshot.title:
        details.append(f"当前分 P 为《{snapshot.part_title}》")
    if snapshot.owner_name:
        details.append(f"投稿者为 {snapshot.owner_name}")
    if snapshot.tags:
        details.append(f"公开标签包括 {'、'.join(snapshot.tags[:5])}")
    available_stats = [(key, value) for key, value in snapshot.stats if value is not None]
    if available_stats:
        details.append(
            "公开统计包含 " + "、".join(f"{key}={value:g}" for key, value in available_stats[:3])
        )
    text = "，".join(details) + "。"
    return SummarySentence(
        text=text,
        evidence=_metadata_reference(
            snapshot,
            locator="video.title,part.title,video.ownerName,video.tags,video.stats",
            text="；".join(details),
        ),
        score=1.0,
    )


def _metadata_keywords(
    snapshot: MetadataSnapshot | None,
    maximum: int,
) -> tuple[KeywordResult, ...]:
    if snapshot is None:
        return ()
    text = " ".join(
        [
            snapshot.title,
            snapshot.part_title,
            snapshot.description,
            snapshot.owner_name,
            *snapshot.tags,
        ]
    )
    occurrences = Counter(_tokens(_normalize_text(text)))
    selected = [
        token
        for token, _ in sorted(
            occurrences.items(), key=lambda item: (-item[1], -len(item[0]), item[0])
        )
        if token not in _STOP_WORDS and not _uninformative_token(token)
    ][:maximum]
    evidence = _metadata_reference(
        snapshot,
        locator="video.title,video.description,video.tags",
        text=text[:1_000],
    )
    return tuple(
        KeywordResult(
            keyword=token,
            score=round(float(occurrences[token]) * 0.5, 6),
            occurrences=occurrences[token],
            evidence=(evidence,),
        )
        for token in selected
    )


def _merge_keywords(
    primary: Sequence[KeywordResult],
    secondary: Sequence[KeywordResult],
    *,
    maximum: int,
) -> tuple[KeywordResult, ...]:
    merged: dict[str, KeywordResult] = {}
    for item in (*primary, *secondary):
        existing = merged.get(item.keyword)
        if existing is None:
            merged[item.keyword] = item
            continue
        evidence_by_key = {
            (value.source, value.evidence_id, value.start_seconds, value.locator): value
            for value in (*existing.evidence, *item.evidence)
        }
        merged[item.keyword] = KeywordResult(
            keyword=item.keyword,
            score=round(existing.score + item.score, 6),
            occurrences=existing.occurrences + item.occurrences,
            evidence=tuple(evidence_by_key.values())[:5],
        )
    return tuple(
        sorted(
            merged.values(),
            key=lambda item: (-item.score, -item.occurrences, -len(item.keyword), item.keyword),
        )[:maximum]
    )


def _visual_reference(value: VisualEvidence) -> EvidenceReference:
    return EvidenceReference(
        start_seconds=value.start_seconds,
        end_seconds=value.end_seconds,
        text=value.text,
        source=value.source,
        confidence=None,
        evidence_id=value.evidence_id,
        locator=f"time:{value.start_seconds:.3f}-{value.end_seconds:.3f}",
        artifact_id=value.artifact_id,
    )


def _attach_visual_evidence(
    chapters: Sequence[ChapterResult],
    visual_evidence: Sequence[VisualEvidence],
    *,
    maximum_per_chapter: int,
) -> tuple[ChapterResult, ...]:
    results: list[ChapterResult] = []
    for chapter in chapters:
        candidates = [
            item
            for item in visual_evidence
            if chapter.start_seconds <= item.start_seconds <= chapter.end_seconds
        ]
        candidates.sort(
            key=lambda item: (
                item.source != ContentInputSource.KEYFRAME,
                abs(item.start_seconds - chapter.start_seconds),
                item.evidence_id,
            )
        )
        visual = tuple(_visual_reference(item) for item in candidates[:1])
        text_limit = maximum_per_chapter - len(visual)
        evidence = tuple(chapter.evidence[: max(0, text_limit)]) + visual
        results.append(
            ChapterResult(
                index=chapter.index,
                title=chapter.title,
                start_seconds=chapter.start_seconds,
                end_seconds=chapter.end_seconds,
                summary=chapter.summary,
                keywords=chapter.keywords,
                evidence=evidence,
            )
        )
    return tuple(results)


def _creator_candidate(snapshot: MetadataSnapshot) -> EntityCandidate:
    name = snapshot.owner_name or "未标注投稿者"
    return EntityCandidate(
        name=name,
        category="creator_metadata",
        evidence=_metadata_reference(snapshot, locator="video.ownerName", text=name),
        limitation="仅表示投稿者元数据，不代表视频画面中的人物或对象。",
    )


def _emotion_timeline(units: Sequence[_Unit]) -> tuple[EmotionPoint, ...]:
    points: list[EmotionPoint] = []
    for unit in units:
        normalized = unit.normalized_text
        positive = sum(term in normalized for term in _POSITIVE_TERMS)
        negative = sum(term in normalized for term in _NEGATIVE_TERMS)
        total = positive + negative
        if total == 0:
            continue
        score = (positive - negative) / total
        label = "正向措辞线索" if score > 0 else "负向措辞线索"
        points.append(
            EmotionPoint(
                start_seconds=unit.start,
                end_seconds=unit.end,
                label=label,
                score=round(score, 6),
                evidence=unit.evidence(),
            )
        )
    return tuple(points[:200])


def _semantic_capabilities(
    *,
    chapters: Sequence[ChapterResult],
    topics: Sequence[TopicResult],
    emotion_timeline: Sequence[EmotionPoint],
    visual_evidence: Sequence[VisualEvidence],
) -> tuple[SemanticCapability, ...]:
    return (
        SemanticCapability(
            name="chapters",
            status="available" if chapters else "unavailable",
            method="deterministic-timeline-segmentation",
            message=(
                "章节按时间轴文本与邻近镜头/关键帧证据确定。"
                if chapters
                else "缺少带时间戳文本，未生成语义章节。"
            ),
        ),
        SemanticCapability(
            name="topics",
            status="limited" if topics else "unavailable",
            method="deterministic-keyword-ranking",
            message=(
                "主题为关键词统计候选，不等同于生成式语义理解。"
                if topics
                else "没有足够文本或元数据生成主题候选。"
            ),
        ),
        SemanticCapability(
            name="entities",
            status="limited",
            method="metadata-only-no-visual-identification",
            message="仅展示投稿者等明确元数据；未识别视频内人物身份或画面对象。",
        ),
        SemanticCapability(
            name="emotion",
            status="limited" if emotion_timeline else "unavailable",
            method="explicit-lexicon-signals-only",
            message=(
                "情绪走向只反映文本中的明确正负向措辞，不代表人物真实情绪。"
                if emotion_timeline
                else "未发现足够明确的情绪措辞，未推断中性或人物真实情绪。"
            ),
        ),
        SemanticCapability(
            name="visual",
            status="limited" if visual_evidence else "unavailable",
            method="scene-and-keyframe-location-only",
            message=(
                "镜头与关键帧仅用于结构定位，未执行视觉实体识别。"
                if visual_evidence
                else "没有可复用的镜头或关键帧证据。"
            ),
        ),
    )


def _coverage(units: Sequence[_Unit], visual_evidence: Sequence[VisualEvidence]) -> str:
    if not units:
        return "metadata_only"
    return "text_and_visual_evidence" if visual_evidence else "text_evidence"


def _metadata_history_changed(snapshots: Sequence[MetadataSnapshot]) -> bool:
    current = next((item for item in snapshots if item.current), None)
    if current is None:
        return False
    return any(
        item.title != current.title
        or item.part_title != current.part_title
        or item.description != current.description
        or item.tags != current.tags
        for item in snapshots
        if not item.current
    )


def _input_digest(
    documents: Sequence[SubtitleDocument],
    metadata_snapshots: Sequence[MetadataSnapshot],
    visual_evidence: Sequence[VisualEvidence],
) -> str:
    canonical_documents = [
        {
            "language": document.language,
            "source": document.source.value,
            "modelName": document.model_name,
            "modelVersion": document.model_version,
            "segments": [
                {
                    "start": segment.start_seconds,
                    "end": segment.end_seconds,
                    "text": segment.text,
                    "confidence": segment.confidence,
                    "evidenceId": segment.evidence_id,
                }
                for segment in document.segments
            ],
        }
        for document in documents
    ]
    canonical_documents.sort(
        key=lambda item: json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    canonical_metadata = [
        {
            "title": item.title,
            "partTitle": item.part_title,
            "description": item.description,
            "ownerName": item.owner_name,
            "tags": list(item.tags),
            "stats": list(item.stats),
            "publishedAt": _utc_datetime(item.published_at).isoformat()
            if item.published_at is not None
            else None,
            "durationSeconds": item.duration_seconds,
            "capturedAt": _utc_datetime(item.captured_at).isoformat(),
            "evidenceId": item.evidence_id,
            "current": item.current,
        }
        for item in metadata_snapshots
    ]
    canonical_metadata.sort(key=lambda item: (not bool(item["current"]), str(item["evidenceId"])))
    canonical_visual = [
        {
            "startSeconds": item.start_seconds,
            "endSeconds": item.end_seconds,
            "source": item.source.value,
            "evidenceId": item.evidence_id,
            "text": item.text,
            "artifactId": item.artifact_id,
        }
        for item in visual_evidence
    ]
    canonical_visual.sort(
        key=lambda item: (
            str(item["startSeconds"]),
            str(item["source"]),
            str(item["evidenceId"]),
        )
    )
    encoded = json.dumps(
        {
            "documents": canonical_documents,
            "metadata": canonical_metadata,
            "visualEvidence": canonical_visual,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _input_warnings(documents: Sequence[SubtitleDocument]) -> list[str]:
    warnings = [warning for document in documents for warning in document.warnings]
    sources = {document.source for document in documents}
    if TranscriptSource.PUBLIC_SUBTITLE not in sources and TranscriptSource.ASR in sources:
        warnings.append("未使用公开字幕，摘要依据自动语音转写生成")
    if sources and sources <= {TranscriptSource.OCR}:
        warnings.append("摘要仅依据画面 OCR，无法覆盖未显示为文字的语音内容")
    return warnings


def _evidence_to_dict(evidence: EvidenceReference) -> dict[str, Any]:
    return {
        "startSeconds": evidence.start_seconds,
        "endSeconds": evidence.end_seconds,
        "text": evidence.text,
        "source": evidence.source.value,
        "confidence": evidence.confidence,
        "evidenceId": evidence.evidence_id,
        "locator": evidence.locator,
        "artifactId": evidence.artifact_id,
    }


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def validate_content_documents(documents: Sequence[SubtitleDocument]) -> None:
    if len(documents) > 100:
        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.INVALID_CONFIGURATION,
                message="内容分析输入文档数量超过安全上限",
                action="减少字幕、ASR 或 OCR 文档数量后重试",
            )
        )
    segment_count = sum(len(document.segments) for document in documents)
    text_characters = sum(
        len(segment.text) for document in documents for segment in document.segments
    )
    if segment_count > 1_000_000 or text_characters > 100_000_000:
        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.INVALID_CONFIGURATION,
                message="内容分析输入规模超过本地安全上限",
                action="按分 P 或时间范围拆分文本后重试",
            )
        )


def _validate_context_inputs(
    metadata_snapshots: Sequence[MetadataSnapshot],
    visual_evidence: Sequence[VisualEvidence],
) -> None:
    metadata_characters = sum(
        len(item.title)
        + len(item.part_title)
        + len(item.description)
        + len(item.owner_name)
        + sum(len(tag) for tag in item.tags)
        + sum(len(key) for key, _ in item.stats)
        for item in metadata_snapshots
    )
    if len(metadata_snapshots) > 50 or metadata_characters > 2_000_000:
        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.INVALID_CONFIGURATION,
                message="内容摘要的元数据历史超过安全上限",
                action="减少历史快照后重试",
            )
        )
    if sum(item.current for item in metadata_snapshots) > 1:
        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.INVALID_CONFIGURATION,
                message="内容摘要包含多个当前元数据快照",
                action="重新解析视频后重试",
            )
        )
    if len(visual_evidence) > 20_000:
        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.INVALID_CONFIGURATION,
                message="镜头与关键帧证据数量超过安全上限",
                action="减少镜头证据或按分 P 分析",
            )
        )
