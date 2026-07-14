from __future__ import annotations

import hashlib
import math
import os
import re
import threading
from itertools import pairwise
from pathlib import Path

from app.analysis.errors import AnalysisError, AnalysisErrorCode, AnalysisFailure
from app.analysis.media import MediaProbeService, _validated_media_path
from app.analysis.models import (
    CapabilityStatus,
    KeyframeAnalysis,
    KeyframeArtifact,
    SceneAnalysis,
    SceneSegment,
)
from app.analysis.process import ProcessRunner

_NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"
_SCENE_METADATA = re.compile(
    rf"frame:\s*\d+\s+pts:\s*\d+\s+pts_time:\s*(?P<time>{_NUMBER})"
    rf"(?:(?!frame:).)*?lavfi\.scene_score=(?P<score>{_NUMBER})",
    re.DOTALL,
)


class SceneAnalyzer:
    def __init__(
        self,
        *,
        ffmpeg_executable: str | Path = "ffmpeg",
        runner: ProcessRunner | None = None,
        probe_service: MediaProbeService | None = None,
        timeout_seconds: float = 3_600.0,
    ) -> None:
        self.ffmpeg_executable = ffmpeg_executable
        self.runner = runner or ProcessRunner(default_timeout_seconds=timeout_seconds)
        self.probe_service = probe_service or MediaProbeService(runner=self.runner)
        self.timeout_seconds = timeout_seconds
        self._version: str | None = None
        self._version_lock = threading.Lock()

    def capability(self) -> CapabilityStatus:
        try:
            version = self.version()
        except AnalysisError:
            return CapabilityStatus(
                component="ffmpeg-scenes",
                available=False,
                version=None,
                reason_code="FFMPEG_NOT_FOUND",
                message="FFmpeg 未安装，镜头切分与关键帧不可用",
                action="安装 FFmpeg 并确认 ffmpeg 已加入 PATH",
            )
        return CapabilityStatus(
            component="ffmpeg-scenes",
            available=True,
            version=version,
            reason_code=None,
            message="FFmpeg 镜头切分与关键帧提取可用",
            action=None,
        )

    def version(self) -> str:
        with self._version_lock:
            if self._version is not None:
                return self._version
            result = self.runner.run(
                self.ffmpeg_executable,
                ["-version"],
                timeout_seconds=min(self.timeout_seconds, 15.0),
            )
            first_line = result.stdout_text().splitlines()[0] if result.stdout else ""
            parts = first_line.split()
            self._version = parts[2] if len(parts) >= 3 else "unknown"
            return self._version

    def analyze(
        self,
        media_path: str | Path,
        *,
        threshold: float = 0.3,
        minimum_scene_seconds: float = 0.4,
        maximum_scenes: int = 10_000,
        cancellation_event: threading.Event | None = None,
    ) -> SceneAnalysis:
        path = _validated_media_path(media_path)
        _validate_scene_options(threshold, minimum_scene_seconds, maximum_scenes)
        duration = self._duration(path)
        select_filter = (
            f"select='gt(scene,{_decimal(threshold)})',metadata=print:key=lavfi.scene_score"
        )
        result = self.runner.run(
            self.ffmpeg_executable,
            [
                "-hide_banner",
                "-nostdin",
                "-v",
                "info",
                "-i",
                path,
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                select_filter,
                "-fps_mode",
                "vfr",
                "-f",
                "null",
                "-",
            ],
            timeout_seconds=self.timeout_seconds,
            cancellation_event=cancellation_event,
        )
        transitions = _parse_transitions(result.stderr_text(), duration, minimum_scene_seconds)
        truncated = len(transitions) + 1 > maximum_scenes
        if truncated:
            transitions = transitions[: maximum_scenes - 1]
        scenes = _build_scenes(duration, transitions)
        warnings: list[str] = []
        if truncated:
            warnings.append("镜头数量超过配置上限，报告仅保留前段镜头")
        if len(scenes) == 1:
            warnings.append("按当前阈值未检测到明显镜头切换")
        average = sum(scene.duration_seconds for scene in scenes) / len(scenes)
        density = len(scenes) / (duration / 60.0) if duration > 0 else 0.0
        return SceneAnalysis(
            analyzer_name="ffmpeg-scene-detect",
            analyzer_version=self.version(),
            threshold=threshold,
            duration_seconds=duration,
            scenes=scenes,
            average_scene_length_seconds=average,
            scene_density_per_minute=density,
            truncated=truncated,
            warnings=tuple(warnings),
        )

    def extract_keyframes(
        self,
        media_path: str | Path,
        scene_analysis: SceneAnalysis,
        output_directory: str | Path,
        *,
        maximum_keyframes: int = 24,
        maximum_width: int = 1280,
        jpeg_quality: int = 2,
        cancellation_event: threading.Event | None = None,
    ) -> KeyframeAnalysis:
        path = _validated_media_path(media_path)
        if not 1 <= maximum_keyframes <= 200:
            raise _invalid_scene_configuration("关键帧数量必须在 1 到 200 之间")
        if not 160 <= maximum_width <= 7680:
            raise _invalid_scene_configuration("关键帧宽度必须在 160 到 7680 之间")
        if not 2 <= jpeg_quality <= 31:
            raise _invalid_scene_configuration("JPEG 质量参数必须在 2 到 31 之间")
        try:
            output_dir = Path(output_directory).expanduser().resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.EXPORT_FAILED,
                    message="无法创建关键帧产物目录",
                    action="检查存储目录权限与磁盘空间后重试",
                    diagnostic=f"output directory failed: {type(exc).__name__}",
                )
            ) from exc
        if not output_dir.is_dir():
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.EXPORT_FAILED,
                    message="关键帧产物路径不是目录",
                    action="选择有效产物目录后重试",
                )
            )

        selected = _select_scene_representatives(scene_analysis.scenes, maximum_keyframes)
        artifacts: list[KeyframeArtifact] = []
        warnings: list[str] = []
        scale_filter = f"scale=w=min({_decimal(float(maximum_width))}\\,iw):h=-2"
        for output_index, (scene, timestamp) in enumerate(selected, start=1):
            timestamp_ms = round(timestamp * 1000)
            filename = f"keyframe_{output_index:04d}_{timestamp_ms:012d}.jpg"
            final_path = output_dir / filename
            temporary_path = output_dir / f".{filename}.partial.jpg"
            try:
                self.runner.run(
                    self.ffmpeg_executable,
                    [
                        "-hide_banner",
                        "-nostdin",
                        "-v",
                        "error",
                        "-ss",
                        _decimal(timestamp),
                        "-i",
                        path,
                        "-map",
                        "0:v:0",
                        "-frames:v",
                        "1",
                        "-vf",
                        scale_filter,
                        "-q:v",
                        str(jpeg_quality),
                        "-f",
                        "image2",
                        "-y",
                        temporary_path,
                    ],
                    timeout_seconds=min(self.timeout_seconds, 120.0),
                    cancellation_event=cancellation_event,
                )
                size = temporary_path.stat().st_size
                if size <= 0:
                    raise OSError("empty keyframe")
                digest = _sha256_file(temporary_path)
                os.replace(temporary_path, final_path)
                artifacts.append(
                    KeyframeArtifact(
                        index=output_index,
                        timestamp_seconds=timestamp,
                        scene_index=scene.index,
                        filename=filename,
                        path=final_path,
                        size_bytes=size,
                        sha256=digest,
                    )
                )
            except AnalysisError as exc:
                if exc.failure.code == AnalysisErrorCode.CANCELED:
                    _unlink_if_present(temporary_path)
                    raise
                _unlink_if_present(temporary_path)
                warnings.append(f"镜头 {scene.index} 的代表帧提取失败，其他关键帧不受影响")
            except OSError:
                _unlink_if_present(temporary_path)
                warnings.append(f"镜头 {scene.index} 的关键帧写入失败，其他关键帧不受影响")
        if not artifacts:
            warnings.append("未生成可用关键帧，请检查媒体视频轨与存储空间")
        return KeyframeAnalysis(
            extractor_name="ffmpeg-scene-keyframes",
            extractor_version=self.version(),
            artifacts=tuple(artifacts),
            warnings=tuple(warnings),
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
        if duration is None or not math.isfinite(duration) or duration <= 0:
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.INVALID_MEDIA,
                    message="无法确定视频时长，不能执行镜头切分",
                    action="确认视频文件完整且包含可解码的视频轨",
                )
            )
        return duration


def _parse_transitions(
    log: str, duration: float, minimum_scene_seconds: float
) -> list[tuple[float, float]]:
    candidates: list[tuple[float, float]] = []
    for match in _SCENE_METADATA.finditer(log):
        timestamp = float(match.group("time"))
        score = float(match.group("score"))
        if minimum_scene_seconds <= timestamp < duration - 0.001:
            candidates.append((timestamp, score))
    candidates.sort(key=lambda item: (item[0], -item[1]))
    kept: list[tuple[float, float]] = []
    for timestamp, score in candidates:
        if not kept or timestamp - kept[-1][0] >= minimum_scene_seconds:
            kept.append((timestamp, score))
        elif score > kept[-1][1]:
            kept[-1] = (timestamp, score)
    return kept


def _build_scenes(
    duration: float, transitions: list[tuple[float, float]]
) -> tuple[SceneSegment, ...]:
    boundaries = [0.0, *(timestamp for timestamp, _ in transitions), duration]
    scores = [None, *(score for _, score in transitions)]
    return tuple(
        SceneSegment(
            index=index,
            start_seconds=start,
            end_seconds=end,
            duration_seconds=end - start,
            transition_score=scores[index - 1],
        )
        for index, (start, end) in enumerate(pairwise(boundaries), start=1)
        if end > start
    )


def _select_scene_representatives(
    scenes: tuple[SceneSegment, ...], maximum: int
) -> list[tuple[SceneSegment, float]]:
    if not scenes:
        return []
    if len(scenes) <= maximum:
        selected = list(scenes)
    elif maximum == 1:
        selected = [max(scenes, key=lambda item: (item.duration_seconds, -item.index))]
    else:
        indexes = {
            round(position * (len(scenes) - 1) / (maximum - 1)) for position in range(maximum)
        }
        selected = [scenes[index] for index in sorted(indexes)]
    return [(scene, scene.start_seconds + scene.duration_seconds / 2.0) for scene in selected]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def _validate_scene_options(
    threshold: float, minimum_scene_seconds: float, maximum_scenes: int
) -> None:
    if not math.isfinite(threshold) or not 0.01 <= threshold <= 1.0:
        raise _invalid_scene_configuration("镜头阈值必须在 0.01 到 1.0 之间")
    if not math.isfinite(minimum_scene_seconds) or not 0.05 <= minimum_scene_seconds <= 60:
        raise _invalid_scene_configuration("最短镜头时长必须在 0.05 到 60 秒之间")
    if not 1 <= maximum_scenes <= 100_000:
        raise _invalid_scene_configuration("镜头数量上限必须在 1 到 100000 之间")


def _invalid_scene_configuration(message: str) -> AnalysisError:
    return AnalysisError(
        AnalysisFailure(
            code=AnalysisErrorCode.INVALID_CONFIGURATION,
            message=message,
            action="调整镜头分析参数后重试",
        )
    )


def _decimal(value: float) -> str:
    return format(value, ".6f").rstrip("0").rstrip(".")
