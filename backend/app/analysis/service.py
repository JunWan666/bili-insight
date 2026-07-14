from __future__ import annotations

from pathlib import Path

from app.analysis.asr import FasterWhisperAdapter, FasterWhisperConfig
from app.analysis.audio import AudioAnalyzer
from app.analysis.media import MediaProbeService
from app.analysis.models import CapabilityStatus
from app.analysis.ocr import PaddleOcrAdapter, PaddleOcrConfig
from app.analysis.process import ProcessRunner
from app.analysis.scenes import SceneAnalyzer
from app.analysis.summary import LocalContentAnalyzer, LocalSummaryConfig


class LocalAnalysisEngine:
    """Composition root for local, independently executable analysis capabilities."""

    def __init__(
        self,
        *,
        ffmpeg_executable: str | Path = "ffmpeg",
        ffprobe_executable: str | Path = "ffprobe",
        process_timeout_seconds: float = 3_600.0,
        asr_config: FasterWhisperConfig | None = None,
        ocr_config: PaddleOcrConfig | None = None,
        summary_config: LocalSummaryConfig | None = None,
    ) -> None:
        runner = ProcessRunner(default_timeout_seconds=process_timeout_seconds)
        probe = MediaProbeService(
            ffprobe_executable=ffprobe_executable,
            runner=runner,
            timeout_seconds=process_timeout_seconds,
        )
        self.media = probe
        self.audio = AudioAnalyzer(
            ffmpeg_executable=ffmpeg_executable,
            runner=runner,
            probe_service=probe,
            timeout_seconds=process_timeout_seconds,
        )
        self.scenes = SceneAnalyzer(
            ffmpeg_executable=ffmpeg_executable,
            runner=runner,
            probe_service=probe,
            timeout_seconds=process_timeout_seconds,
        )
        self.asr = FasterWhisperAdapter(asr_config)
        self.ocr = PaddleOcrAdapter(
            ocr_config,
            ffmpeg_executable=ffmpeg_executable,
            runner=runner,
            probe_service=probe,
            timeout_seconds=process_timeout_seconds,
        )
        self.summary = LocalContentAnalyzer(summary_config)

    def capabilities(self) -> tuple[CapabilityStatus, ...]:
        return (
            self.media.capability(),
            self.audio.capability(),
            self.scenes.capability(),
            self.asr.capability(),
            self.ocr.capability(),
            CapabilityStatus(
                component="local-content-summary",
                available=True,
                version="2.0.0",
                reason_code=None,
                message="本地多来源、可复现且带证据的内容摘要可用",
                action=None,
            ),
        )
