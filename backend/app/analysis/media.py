from __future__ import annotations

import json
import math
import threading
from collections.abc import Mapping
from itertools import pairwise
from pathlib import Path
from typing import Any

from app.analysis.errors import AnalysisError, AnalysisErrorCode, AnalysisFailure
from app.analysis.models import (
    AudioTechnicalInfo,
    CapabilityStatus,
    ChapterTechnicalInfo,
    ContainerTechnicalInfo,
    KeyframeStatistics,
    MediaTechnicalReport,
    SubtitleTechnicalInfo,
    VideoTechnicalInfo,
)
from app.analysis.process import ProcessRunner


class MediaProbeService:
    def __init__(
        self,
        *,
        ffprobe_executable: str | Path = "ffprobe",
        runner: ProcessRunner | None = None,
        timeout_seconds: float = 600.0,
    ) -> None:
        self.ffprobe_executable = ffprobe_executable
        self.runner = runner or ProcessRunner(default_timeout_seconds=timeout_seconds)
        self.timeout_seconds = timeout_seconds
        self._version: str | None = None
        self._version_lock = threading.Lock()

    def capability(self) -> CapabilityStatus:
        try:
            version = self.version()
        except AnalysisError:
            return CapabilityStatus(
                component="ffprobe",
                available=False,
                version=None,
                reason_code="FFPROBE_NOT_FOUND",
                message="FFprobe 未安装，媒体技术分析不可用",
                action="安装 FFmpeg 并确认 ffprobe 已加入 PATH",
            )
        return CapabilityStatus(
            component="ffprobe",
            available=True,
            version=version,
            reason_code=None,
            message="FFprobe 媒体探测可用",
            action=None,
        )

    def version(self) -> str:
        with self._version_lock:
            if self._version is not None:
                return self._version
            result = self.runner.run(
                self.ffprobe_executable,
                ["-version"],
                timeout_seconds=min(self.timeout_seconds, 15.0),
            )
            first_line = result.stdout_text().splitlines()[0] if result.stdout else ""
            parts = first_line.split()
            self._version = parts[2] if len(parts) >= 3 else "unknown"
            return self._version

    def probe(
        self,
        media_path: str | Path,
        *,
        include_keyframes: bool = True,
        max_keyframes_per_stream: int = 20_000,
        cancellation_event: threading.Event | None = None,
    ) -> MediaTechnicalReport:
        path = _validated_media_path(media_path)
        if not 1 <= max_keyframes_per_stream <= 100_000:
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.INVALID_CONFIGURATION,
                    message="关键帧统计上限必须在 1 到 100000 之间",
                    action="调整媒体分析参数后重试",
                )
            )
        result = self.runner.run(
            self.ffprobe_executable,
            [
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-show_chapters",
                "-of",
                "json",
                "-i",
                path,
            ],
            timeout_seconds=self.timeout_seconds,
            cancellation_event=cancellation_event,
        )
        payload = _load_probe_json(result.stdout)
        raw_format = _mapping(payload.get("format"))
        raw_streams = payload.get("streams")
        streams = raw_streams if isinstance(raw_streams, list) else []
        warnings: list[str] = []

        video_streams: list[VideoTechnicalInfo] = []
        audio_streams: list[AudioTechnicalInfo] = []
        subtitle_streams: list[SubtitleTechnicalInfo] = []
        video_ordinal = 0
        for raw_stream in streams:
            stream = _mapping(raw_stream)
            codec_type = _text(stream.get("codec_type"))
            if codec_type == "video":
                keyframes: KeyframeStatistics | None = None
                if include_keyframes:
                    try:
                        keyframes = self._probe_keyframes(
                            path,
                            video_ordinal,
                            max_keyframes_per_stream,
                            cancellation_event,
                        )
                    except AnalysisError:
                        warnings.append(
                            f"视频流 {video_ordinal} 的关键帧间隔未能读取，其他技术参数不受影响"
                        )
                video_streams.append(_parse_video_stream(stream, keyframes))
                video_ordinal += 1
            elif codec_type == "audio":
                audio_streams.append(_parse_audio_stream(stream))
            elif codec_type == "subtitle":
                subtitle_streams.append(_parse_subtitle_stream(stream))

        if not video_streams:
            warnings.append("媒体中未检测到视频轨")
        if not audio_streams:
            warnings.append("媒体中未检测到音频轨")

        raw_chapters = payload.get("chapters")
        chapter_values = raw_chapters if isinstance(raw_chapters, list) else []
        chapters = tuple(
            _parse_chapter(index, _mapping(chapter)) for index, chapter in enumerate(chapter_values)
        )
        return MediaTechnicalReport(
            probe_name="ffprobe",
            probe_version=self.version(),
            container=_parse_container(raw_format, path),
            video_streams=tuple(video_streams),
            audio_streams=tuple(audio_streams),
            subtitle_streams=tuple(subtitle_streams),
            chapters=chapters,
            warnings=tuple(warnings),
        )

    def _probe_keyframes(
        self,
        path: Path,
        video_ordinal: int,
        maximum: int,
        cancellation_event: threading.Event | None,
    ) -> KeyframeStatistics:
        result = self.runner.run(
            self.ffprobe_executable,
            [
                "-v",
                "error",
                "-select_streams",
                f"v:{video_ordinal}",
                "-skip_frame",
                "nokey",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp_time,pkt_pts_time",
                "-of",
                "json",
                "-i",
                path,
            ],
            timeout_seconds=self.timeout_seconds,
            cancellation_event=cancellation_event,
        )
        payload = _load_probe_json(result.stdout)
        raw_frames = payload.get("frames")
        frames = raw_frames if isinstance(raw_frames, list) else []
        timestamps: list[float] = []
        for raw_frame in frames:
            frame = _mapping(raw_frame)
            timestamp = _float(frame.get("best_effort_timestamp_time", frame.get("pkt_pts_time")))
            if timestamp is not None and timestamp >= 0:
                timestamps.append(timestamp)
        timestamps = sorted(set(timestamps))
        intervals = [
            current - previous for previous, current in pairwise(timestamps) if current > previous
        ]
        return KeyframeStatistics(
            count=len(timestamps),
            timestamps_seconds=tuple(timestamps[:maximum]),
            average_interval_seconds=(sum(intervals) / len(intervals)) if intervals else None,
            minimum_interval_seconds=min(intervals) if intervals else None,
            maximum_interval_seconds=max(intervals) if intervals else None,
            truncated=len(timestamps) > maximum,
        )


def _validated_media_path(media_path: str | Path) -> Path:
    try:
        path = Path(media_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.INVALID_MEDIA,
                message="待分析的媒体文件不存在或不可访问",
                action="重新下载媒体文件或选择有效产物后重试",
            )
        ) from exc
    if not path.is_file():
        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.INVALID_MEDIA,
                message="待分析路径不是可读取的媒体文件",
                action="选择有效媒体产物后重试",
            )
        )
    return path


def _load_probe_json(value: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.INVALID_MEDIA,
                message="FFprobe 未返回可识别的媒体信息",
                action="确认文件完整且格式受 FFmpeg 支持后重试",
                diagnostic=f"invalid ffprobe json: {type(exc).__name__}",
            )
        ) from exc
    if not isinstance(payload, dict):
        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.INVALID_MEDIA,
                message="媒体技术信息结构无效",
                action="确认文件完整后重新分析",
            )
        )
    return payload


def _parse_container(raw: Mapping[str, Any], path: Path) -> ContainerTechnicalInfo:
    format_name = _text(raw.get("format_name")) or ""
    size = _integer(raw.get("size"))
    if size is None:
        try:
            size = path.stat().st_size
        except OSError:
            size = None
    return ContainerTechnicalInfo(
        format_names=tuple(item for item in format_name.split(",") if item),
        format_long_name=_text(raw.get("format_long_name")),
        duration_seconds=_float(raw.get("duration")),
        size_bytes=size,
        bit_rate=_integer(raw.get("bit_rate")),
        start_time_seconds=_float(raw.get("start_time")),
        tags=_tags(raw.get("tags")),
    )


def _parse_video_stream(
    raw: Mapping[str, Any], keyframes: KeyframeStatistics | None
) -> VideoTechnicalInfo:
    return VideoTechnicalInfo(
        index=_integer(raw.get("index")) or 0,
        codec_name=_text(raw.get("codec_name")),
        codec_long_name=_text(raw.get("codec_long_name")),
        profile=_text(raw.get("profile")),
        level=_integer(raw.get("level")),
        width=_integer(raw.get("width")),
        height=_integer(raw.get("height")),
        pixel_format=_text(raw.get("pix_fmt")),
        average_frame_rate=_ratio(raw.get("avg_frame_rate")),
        real_frame_rate=_ratio(raw.get("r_frame_rate")),
        duration_seconds=_float(raw.get("duration")),
        bit_rate=_integer(raw.get("bit_rate")),
        frame_count=_integer(raw.get("nb_frames")),
        color_range=_text(raw.get("color_range")),
        color_space=_text(raw.get("color_space")),
        color_transfer=_text(raw.get("color_transfer")),
        color_primaries=_text(raw.get("color_primaries")),
        hdr_type=_detect_hdr_type(raw),
        keyframes=keyframes,
        tags=_tags(raw.get("tags")),
    )


def _parse_audio_stream(raw: Mapping[str, Any]) -> AudioTechnicalInfo:
    return AudioTechnicalInfo(
        index=_integer(raw.get("index")) or 0,
        codec_name=_text(raw.get("codec_name")),
        codec_long_name=_text(raw.get("codec_long_name")),
        profile=_text(raw.get("profile")),
        sample_format=_text(raw.get("sample_fmt")),
        sample_rate_hz=_integer(raw.get("sample_rate")),
        channels=_integer(raw.get("channels")),
        channel_layout=_text(raw.get("channel_layout")),
        duration_seconds=_float(raw.get("duration")),
        bit_rate=_integer(raw.get("bit_rate")),
        bits_per_sample=_integer(raw.get("bits_per_sample")),
        tags=_tags(raw.get("tags")),
    )


def _parse_subtitle_stream(raw: Mapping[str, Any]) -> SubtitleTechnicalInfo:
    tags = _tags(raw.get("tags"))
    return SubtitleTechnicalInfo(
        index=_integer(raw.get("index")) or 0,
        codec_name=_text(raw.get("codec_name")),
        language=tags.get("language"),
        title=tags.get("title"),
    )


def _parse_chapter(index: int, raw: Mapping[str, Any]) -> ChapterTechnicalInfo:
    tags = _tags(raw.get("tags"))
    return ChapterTechnicalInfo(
        index=_integer(raw.get("id")) or index,
        start_seconds=_float(raw.get("start_time")) or 0.0,
        end_seconds=_float(raw.get("end_time")) or 0.0,
        title=tags.get("title"),
    )


def _detect_hdr_type(raw: Mapping[str, Any]) -> str:
    transfer = (_text(raw.get("color_transfer")) or "").lower()
    primaries = (_text(raw.get("color_primaries")) or "").lower()
    side_data = raw.get("side_data_list")
    entries = side_data if isinstance(side_data, list) else []
    side_types = " ".join(
        (_text(_mapping(entry).get("side_data_type")) or "").lower() for entry in entries
    )
    if "dovi" in side_types or "dolby vision" in side_types:
        return "DOLBY_VISION"
    if transfer in {"smpte2084", "pq"}:
        return "HDR10" if primaries == "bt2020" else "PQ_HDR"
    if transfer in {"arib-std-b67", "hlg"}:
        return "HLG"
    if transfer:
        return "SDR"
    return "UNKNOWN"


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed


def _float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _ratio(value: object) -> float | None:
    text = _text(value)
    if text is None:
        return None
    if "/" not in text:
        return _float(text)
    numerator, denominator = text.split("/", 1)
    top = _float(numerator)
    bottom = _float(denominator)
    if top is None or bottom is None or bottom == 0:
        return None
    return top / bottom


def _tags(value: object) -> dict[str, str]:
    raw = _mapping(value)
    return {str(key): str(item) for key, item in raw.items() if isinstance(item, (str, int, float))}
