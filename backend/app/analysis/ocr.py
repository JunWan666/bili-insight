from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import json
import logging
import math
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.analysis.errors import AnalysisError, AnalysisErrorCode, AnalysisFailure
from app.analysis.media import MediaProbeService, _validated_media_path
from app.analysis.models import (
    CapabilityStatus,
    SubtitleDocument,
    SubtitleSegment,
    TranscriptSource,
)
from app.analysis.process import ProcessRunner
from app.analysis.subtitles import subtitle_document
from app.core.process_limits import (
    DEFAULT_PROCESS_MAX_THREADS,
    apply_current_process_thread_limit,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PaddleOcrConfig:
    language: str = "zh-CN"
    device: Literal["cpu", "gpu"] = "cpu"
    sample_interval_seconds: float = 2.0
    maximum_frames: int = 300
    maximum_width: int = 1920
    minimum_confidence: float = 0.35
    cpu_threads: int = DEFAULT_PROCESS_MAX_THREADS

    def __post_init__(self) -> None:
        if not self.language.strip() or len(self.language) > 35:
            raise ValueError("OCR language is invalid")
        if not math.isfinite(self.sample_interval_seconds) or not (
            0.2 <= self.sample_interval_seconds <= 600
        ):
            raise ValueError("OCR sample interval must be between 0.2 and 600 seconds")
        if not 1 <= self.maximum_frames <= 10_000:
            raise ValueError("OCR maximum frames must be between 1 and 10000")
        if not 160 <= self.maximum_width <= 7680:
            raise ValueError("OCR maximum width must be between 160 and 7680")
        if not math.isfinite(self.minimum_confidence) or not 0 <= self.minimum_confidence <= 1:
            raise ValueError("OCR confidence threshold must be between 0 and 1")
        if not 1 <= self.cpu_threads <= 32:
            raise ValueError("OCR CPU thread count must be between 1 and 32")


class PaddleOcrAdapter:
    def __init__(
        self,
        config: PaddleOcrConfig | None = None,
        *,
        ffmpeg_executable: str | Path = "ffmpeg",
        runner: ProcessRunner | None = None,
        probe_service: MediaProbeService | None = None,
        timeout_seconds: float = 3_600.0,
    ) -> None:
        self.config = config or PaddleOcrConfig()
        self.ffmpeg_executable = ffmpeg_executable
        self.runner = runner or ProcessRunner(default_timeout_seconds=timeout_seconds)
        self.probe_service = probe_service or MediaProbeService(runner=self.runner)
        self.timeout_seconds = timeout_seconds
        self._engine: Any | None = None
        self._engine_lock = threading.Lock()

    def capability(self) -> CapabilityStatus:
        try:
            apply_current_process_thread_limit(self.config.cpu_threads)
            paddle_module = importlib.import_module("paddle")
            ocr_module = importlib.import_module("paddleocr")
            if not callable(ocr_module.PaddleOCR):
                raise AttributeError("PaddleOCR is unavailable")
            paddle_version = getattr(paddle_module, "__version__", "unknown")
            ocr_version = importlib.metadata.version("paddleocr")
        except Exception as exc:
            logger.debug(
                "PaddleOCR capability check failed",
                extra={"error": type(exc).__name__},
            )
            return CapabilityStatus(
                component="paddleocr",
                available=False,
                version=None,
                reason_code="PADDLEOCR_NOT_INSTALLED",
                message="PaddleOCR 或 PaddlePaddle 未安装，画面文字识别当前不可用",
                action="安装与运行设备匹配的 paddlepaddle 和 paddleocr 后重新检查模型状态",
            )
        return CapabilityStatus(
            component="paddleocr",
            available=True,
            version=f"paddleocr {ocr_version}; paddle {paddle_version}",
            reason_code=None,
            message="PaddleOCR 画面文字识别可用",
            action=None,
        )

    def recognize(
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
                    action=capability.action or "安装 OCR 依赖后重试",
                )
            )
        if cancellation_event is not None and cancellation_event.is_set():
            raise _canceled_error()
        duration = self._duration(path)
        logger.info(
            "Starting local OCR analysis",
            extra={
                "language": self.config.language,
                "sample_interval_seconds": self.config.sample_interval_seconds,
            },
        )
        with tempfile.TemporaryDirectory(prefix="bilibili-ocr-") as temporary_directory:
            frame_directory = Path(temporary_directory)
            self._extract_frames(path, frame_directory, cancellation_event)
            frame_paths = sorted(frame_directory.glob("ocr_*.jpg"))
            engine = self._load_engine()
            segments: list[SubtitleSegment] = []
            failed_frames = 0
            for frame_index, frame_path in enumerate(frame_paths):
                if cancellation_event is not None and cancellation_event.is_set():
                    raise _canceled_error()
                try:
                    items = self._run_ocr(engine, frame_path)
                except Exception:
                    failed_frames += 1
                    continue
                accepted = [
                    (text, confidence)
                    for text, confidence in items
                    if confidence >= self.config.minimum_confidence and text.strip()
                ]
                if not accepted:
                    continue
                start = frame_index * self.config.sample_interval_seconds
                end = min(duration, start + self.config.sample_interval_seconds)
                if end <= start:
                    end = start + min(0.1, self.config.sample_interval_seconds)
                text = "\n".join(dict.fromkeys(item[0].strip() for item in accepted))
                confidence = sum(item[1] for item in accepted) / len(accepted)
                segments.append(
                    SubtitleSegment(
                        start_seconds=start,
                        end_seconds=end,
                        text=text,
                        source=TranscriptSource.OCR,
                        language=self.config.language,
                        confidence=confidence,
                        evidence_id=frame_path.name,
                    )
                )
        if failed_frames and failed_frames == len(frame_paths):
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.MODEL_FAILED,
                    message="PaddleOCR 无法处理采样画面",
                    action="检查 OCR 模型、运行设备和可用内存后重试",
                    diagnostic=f"all {failed_frames} OCR frames failed",
                )
            )
        warnings = ["OCR 结果受画面清晰度、字体、遮挡和采样间隔影响"]
        if failed_frames:
            warnings.append(f"有 {failed_frames} 个采样画面识别失败，其余结果已保留")
        if not segments:
            warnings.append("采样画面中未识别到达到置信度阈值的文字")
        logger.info(
            "Local OCR analysis completed",
            extra={"segment_count": len(segments), "failed_frames": failed_frames},
        )
        return subtitle_document(
            segments,
            language=self.config.language,
            source=TranscriptSource.OCR,
            model_name=f"paddleocr:{_paddle_language(self.config.language)}",
            model_version=capability.version,
            generated_at=generated_at or datetime.now(UTC),
            warnings=warnings,
        )

    def _duration(self, path: Path) -> float:
        report = self.probe_service.probe(path, include_keyframes=False)
        duration = report.container.duration_seconds
        if duration is None:
            durations = [
                stream.duration_seconds
                for stream in report.video_streams
                if stream.duration_seconds is not None
            ]
            duration = max(durations) if durations else None
        if duration is None or duration <= 0:
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.INVALID_MEDIA,
                    message="无法确定视频时长，不能执行 OCR",
                    action="确认文件包含完整可解码的视频轨",
                )
            )
        return duration

    def _extract_frames(
        self,
        path: Path,
        output_directory: Path,
        cancellation_event: threading.Event | None,
    ) -> None:
        frame_rate = 1.0 / self.config.sample_interval_seconds
        video_filter = (
            f"fps={_decimal(frame_rate)},"
            f"scale=w=min({_decimal(float(self.config.maximum_width))}\\,iw):h=-2"
        )
        self.runner.run(
            self.ffmpeg_executable,
            [
                "-hide_banner",
                "-nostdin",
                "-v",
                "error",
                "-threads",
                str(self.config.cpu_threads),
                "-i",
                path,
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                video_filter,
                "-frames:v",
                str(self.config.maximum_frames),
                "-q:v",
                "3",
                "-y",
                output_directory / "ocr_%06d.jpg",
            ],
            timeout_seconds=self.timeout_seconds,
            cancellation_event=cancellation_event,
        )

    def _load_engine(self) -> Any:
        with self._engine_lock:
            if self._engine is not None:
                return self._engine
            apply_current_process_thread_limit(self.config.cpu_threads)
            paddle_module = importlib.import_module("paddle")
            set_num_threads = getattr(paddle_module, "set_num_threads", None)
            if callable(set_num_threads):
                set_num_threads(self.config.cpu_threads)
            module = importlib.import_module("paddleocr")
            engine_class = module.PaddleOCR
            parameters = inspect.signature(engine_class).parameters
            accepts_keyword_arguments = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
            )
            try:
                major_version = int(importlib.metadata.version("paddleocr").split(".", 1)[0])
            except (ValueError, importlib.metadata.PackageNotFoundError):
                major_version = 0
            kwargs: dict[str, Any] = {"lang": _paddle_language(self.config.language)}
            if "device" in parameters or (major_version >= 3 and accepts_keyword_arguments):
                kwargs["device"] = "gpu:0" if self.config.device == "gpu" else "cpu"
            elif "use_gpu" in parameters or accepts_keyword_arguments:
                kwargs["use_gpu"] = self.config.device == "gpu"
            if "use_textline_orientation" in parameters or (
                major_version >= 3 and accepts_keyword_arguments
            ):
                kwargs["use_textline_orientation"] = True
            elif "use_angle_cls" in parameters or accepts_keyword_arguments:
                kwargs["use_angle_cls"] = True
            self._engine = engine_class(**kwargs)
            return self._engine

    @staticmethod
    def _run_ocr(engine: Any, frame_path: Path) -> list[tuple[str, float]]:
        predict = getattr(engine, "predict", None)
        if callable(predict):
            raw_result = predict(str(frame_path))
        else:
            ocr = engine.ocr
            raw_result = ocr(str(frame_path), cls=True)
        return _extract_ocr_items(raw_result)


def _extract_ocr_items(value: object) -> list[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    visited: set[int] = set()

    def visit(current: object, depth: int) -> None:
        if current is None or depth > 12:
            return
        if isinstance(current, str):
            stripped = current.strip()
            if len(stripped) <= 10_000_000 and stripped.startswith(("{", "[")):
                try:
                    visit(json.loads(stripped), depth + 1)
                except json.JSONDecodeError:
                    return
            return
        if isinstance(current, (bytes, int, float, bool)):
            return
        identity = id(current)
        if identity in visited:
            return
        visited.add(identity)
        if isinstance(current, Mapping):
            texts = current.get("rec_texts")
            scores = current.get("rec_scores")
            if isinstance(texts, Sequence) and not isinstance(texts, (str, bytes)):
                score_values = (
                    scores
                    if isinstance(scores, Sequence) and not isinstance(scores, (str, bytes))
                    else []
                )
                for index, text in enumerate(texts):
                    score = score_values[index] if index < len(score_values) else 1.0
                    _append_item(items, text, score)
            text = current.get("text", current.get("transcription"))
            score = current.get("score", current.get("confidence", 1.0))
            _append_item(items, text, score)
            for nested in current.values():
                visit(nested, depth + 1)
            return
        if isinstance(current, Sequence):
            if len(current) == 2 and isinstance(current[1], Sequence):
                possible = current[1]
                if len(possible) >= 2:
                    _append_item(items, possible[0], possible[1])
            for nested in current:
                visit(nested, depth + 1)
            return
        for attribute in ("json", "res", "to_dict"):
            nested = getattr(current, attribute, None)
            if callable(nested):
                try:
                    nested = nested()
                except Exception as exc:
                    logger.debug(
                        "Ignoring unsupported PaddleOCR result adapter",
                        extra={"result_type": type(current).__name__, "error": type(exc).__name__},
                    )
                    continue
            if nested is not None:
                visit(nested, depth + 1)

    visit(value, 0)
    deduplicated: dict[tuple[str, int], tuple[str, float]] = {}
    for text, confidence in items:
        key = (text, round(confidence * 10_000))
        deduplicated[key] = (text, confidence)
    return list(deduplicated.values())


def _append_item(items: list[tuple[str, float]], text: object, score: object) -> None:
    if not isinstance(text, str) or not text.strip():
        return
    try:
        confidence = float(score)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return
    if math.isfinite(confidence):
        items.append((text.strip(), min(1.0, max(0.0, confidence))))


def _paddle_language(language: str) -> str:
    normalized = language.strip().lower().replace("_", "-")
    aliases = {
        "zh": "ch",
        "zh-cn": "ch",
        "zh-hans": "ch",
        "zh-tw": "chinese_cht",
        "zh-hant": "chinese_cht",
        "en-us": "en",
        "en-gb": "en",
        "ja": "japan",
        "ja-jp": "japan",
        "ko": "korean",
        "ko-kr": "korean",
    }
    return aliases.get(normalized, normalized.split("-", 1)[0])


def _decimal(value: float) -> str:
    return format(value, ".8f").rstrip("0").rstrip(".")


def _canceled_error() -> AnalysisError:
    return AnalysisError(
        AnalysisFailure(
            code=AnalysisErrorCode.CANCELED,
            message="OCR 任务已取消",
            action="可按需重新创建分析任务",
        )
    )
