from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import logging
import math
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Literal

from app.analysis.errors import AnalysisError, AnalysisErrorCode, AnalysisFailure
from app.analysis.media import _validated_media_path
from app.analysis.models import (
    CapabilityStatus,
    SubtitleDocument,
    SubtitleSegment,
    TranscriptSource,
)
from app.analysis.subtitles import subtitle_document
from app.core.process_limits import (
    DEFAULT_PROCESS_MAX_THREADS,
    apply_current_process_thread_limit,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FasterWhisperConfig:
    model_size_or_path: str = "small"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    compute_type: str = "default"
    language: str | None = "zh-CN"
    beam_size: int = 5
    vad_filter: bool = True
    cpu_threads: int = DEFAULT_PROCESS_MAX_THREADS
    num_workers: int = 1
    download_root: Path | None = None
    local_files_only: bool = False

    def __post_init__(self) -> None:
        if not self.model_size_or_path.strip() or len(self.model_size_or_path) > 512:
            raise ValueError("faster-whisper model name or path is invalid")
        if not self.compute_type.strip() or len(self.compute_type) > 64:
            raise ValueError("faster-whisper compute type is invalid")
        if not 1 <= self.beam_size <= 100:
            raise ValueError("beam size must be between 1 and 100")
        if not 1 <= self.cpu_threads <= 32:
            raise ValueError("cpu thread count must be between 1 and 32")
        if not 1 <= self.num_workers <= 32:
            raise ValueError("worker count must be between 1 and 32")


class FasterWhisperAdapter:
    def __init__(self, config: FasterWhisperConfig | None = None) -> None:
        self.config = config or FasterWhisperConfig()
        self._model: Any | None = None
        self._model_lock = threading.Lock()

    def capability(self) -> CapabilityStatus:
        try:
            apply_current_process_thread_limit(self.config.cpu_threads)
            module = importlib.import_module("faster_whisper")
            model_class = module.WhisperModel
            if not callable(model_class):
                raise AttributeError("WhisperModel is unavailable")
            version = importlib.metadata.version("faster-whisper")
        except Exception as exc:
            logger.debug(
                "faster-whisper capability check failed",
                extra={"error": type(exc).__name__},
            )
            return CapabilityStatus(
                component="faster-whisper",
                available=False,
                version=None,
                reason_code="FASTER_WHISPER_NOT_INSTALLED",
                message="faster-whisper 未安装，语音转写当前不可用",
                action="执行 pip install faster-whisper 后重新检查模型状态",
            )
        return CapabilityStatus(
            component="faster-whisper",
            available=True,
            version=version,
            reason_code=None,
            message="faster-whisper 语音转写可用",
            action=None,
        )

    def transcribe(
        self,
        media_path: str | Path,
        *,
        cancellation_event: threading.Event | None = None,
        generated_at: datetime | None = None,
    ) -> SubtitleDocument:
        path = _validated_media_path(media_path)
        capability = self.capability()
        if not capability.available:
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.DEPENDENCY_UNAVAILABLE,
                    message=capability.message,
                    action=capability.action or "安装语音转写依赖后重试",
                )
            )
        if cancellation_event is not None and cancellation_event.is_set():
            raise _canceled_error()
        try:
            model_label = _safe_model_label(self.config.model_size_or_path)
            logger.info(
                "Starting local speech transcription",
                extra={"model": model_label, "device": self.config.device},
            )
            model = self._load_model()
            language = _whisper_language(self.config.language)
            segments_iterator, info = model.transcribe(
                str(path),
                language=language,
                beam_size=self.config.beam_size,
                vad_filter=self.config.vad_filter,
                word_timestamps=True,
            )
            detected_language = _detected_language(info, language)
            segments: list[SubtitleSegment] = []
            for index, raw_segment in enumerate(segments_iterator, start=1):
                if cancellation_event is not None and cancellation_event.is_set():
                    raise _canceled_error()
                start = _safe_float(getattr(raw_segment, "start", None))
                end = _safe_float(getattr(raw_segment, "end", None))
                text = getattr(raw_segment, "text", None)
                if start is None or end is None or end <= start or not isinstance(text, str):
                    continue
                confidence = _segment_confidence(raw_segment)
                try:
                    segments.append(
                        SubtitleSegment(
                            start_seconds=max(0.0, start),
                            end_seconds=end,
                            text=text,
                            source=TranscriptSource.ASR,
                            language=detected_language,
                            confidence=confidence,
                            evidence_id=f"asr-{index}",
                        )
                    )
                except ValueError:
                    continue
        except AnalysisError:
            raise
        except Exception as exc:
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.MODEL_FAILED,
                    message="faster-whisper 语音转写执行失败",
                    action="检查模型文件、运行设备和可用内存后重试",
                    diagnostic=f"faster-whisper failed: {type(exc).__name__}",
                )
            ) from exc
        warnings = ["自动语音转写可能受音乐、方言、多人重叠说话和录音质量影响"]
        if not segments:
            warnings.append("音频中未识别到可用语音文本")
        logger.info(
            "Local speech transcription completed",
            extra={"model": model_label, "segment_count": len(segments)},
        )
        return subtitle_document(
            segments,
            language=detected_language,
            source=TranscriptSource.ASR,
            model_name=f"faster-whisper:{model_label}",
            model_version=capability.version,
            generated_at=generated_at or datetime.now(UTC),
            warnings=warnings,
        )

    def _load_model(self) -> Any:
        with self._model_lock:
            if self._model is not None:
                return self._model
            apply_current_process_thread_limit(self.config.cpu_threads)
            module = importlib.import_module("faster_whisper")
            model_class = module.WhisperModel
            kwargs: dict[str, Any] = {
                "device": self.config.device,
                "compute_type": self.config.compute_type,
                "cpu_threads": self.config.cpu_threads,
                "num_workers": self.config.num_workers,
                "local_files_only": self.config.local_files_only,
            }
            if self.config.download_root is not None:
                kwargs["download_root"] = str(self.config.download_root.expanduser().resolve())
            self._model = model_class(self.config.model_size_or_path, **kwargs)
            return self._model


def _whisper_language(language: str | None) -> str | None:
    if language is None or language.lower() in {"auto", "und"}:
        return None
    normalized = language.strip().lower().replace("_", "-")
    aliases = {
        "zh-cn": "zh",
        "zh-hans": "zh",
        "zh-tw": "zh",
        "zh-hant": "zh",
        "en-us": "en",
        "en-gb": "en",
        "ja-jp": "ja",
    }
    return aliases.get(normalized, normalized.split("-", 1)[0])


def _safe_model_label(model_size_or_path: str) -> str:
    value = model_size_or_path.strip()
    expanded = Path(value).expanduser()
    looks_local = (
        expanded.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or value.startswith(("./", "../", ".\\", "..\\"))
    )
    if not looks_local:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"local-model-{digest}"


def _detected_language(info: object, configured: str | None) -> str:
    detected = getattr(info, "language", None)
    if isinstance(detected, str) and detected.strip():
        return detected.strip()
    return configured or "und"


def _segment_confidence(segment: object) -> float | None:
    average_log_probability = _safe_float(getattr(segment, "avg_logprob", None))
    if average_log_probability is None:
        return None
    no_speech_probability = _safe_float(getattr(segment, "no_speech_prob", 0.0)) or 0.0
    confidence = math.exp(min(0.0, average_log_probability)) * (
        1.0 - min(1.0, max(0.0, no_speech_probability))
    )
    return min(1.0, max(0.0, confidence))


def _safe_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _canceled_error() -> AnalysisError:
    return AnalysisError(
        AnalysisFailure(
            code=AnalysisErrorCode.CANCELED,
            message="语音转写任务已取消",
            action="可按需重新创建分析任务",
        )
    )
