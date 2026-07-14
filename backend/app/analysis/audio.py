from __future__ import annotations

import math
import re
import threading
from pathlib import Path

from app.analysis.errors import AnalysisError, AnalysisErrorCode, AnalysisFailure
from app.analysis.media import MediaProbeService, _validated_media_path
from app.analysis.models import (
    AudioAnalysis,
    AudioContentClassification,
    AudioContentLabel,
    AudioContentSegment,
    CapabilityStatus,
    LoudnessPoint,
    SilenceInterval,
    SpectrumBand,
    SpectrumOverview,
)
from app.analysis.process import ProcessRunner

_NUMBER = r"(?:[-+]?(?:\d+(?:\.\d+)?|\.\d+)|[-+]?inf)"
_SILENCE_START = re.compile(rf"silence_start:\s*(?P<value>{_NUMBER})", re.IGNORECASE)
_SILENCE_END = re.compile(
    rf"silence_end:\s*(?P<end>{_NUMBER})\s*\|\s*silence_duration:\s*(?P<duration>{_NUMBER})",
    re.IGNORECASE,
)
_CURVE_POINT = re.compile(
    rf"\bt:\s*(?P<time>{_NUMBER}).*?\bM:\s*(?P<m>{_NUMBER})"
    rf".*?\bS:\s*(?P<s>{_NUMBER}).*?\bI:\s*(?P<i>{_NUMBER})\s*LUFS"
    rf".*?\bLRA:\s*(?P<lra>{_NUMBER})\s*LU",
    re.IGNORECASE,
)
_INTEGRATED = re.compile(rf"\bI:\s*(?P<value>{_NUMBER})\s*LUFS", re.IGNORECASE)
_LOUDNESS_RANGE = re.compile(rf"\bLRA:\s*(?P<value>{_NUMBER})\s*LU\b", re.IGNORECASE)
_TRUE_PEAK = re.compile(rf"^\s*Peak:\s*(?P<value>{_NUMBER})\s*dBFS", re.MULTILINE)
_MEAN_VOLUME = re.compile(rf"mean_volume:\s*(?P<value>{_NUMBER})\s*dB", re.IGNORECASE)
_MAX_VOLUME = re.compile(rf"max_volume:\s*(?P<value>{_NUMBER})\s*dB", re.IGNORECASE)
_SPECTRUM_DISCLAIMER = (
    "频带强度由 FFmpeg 生成的有界频谱图归一化计算，只能比较本音频内各频段，"
    "不是校准声压、无损频谱或音频指纹。"
)
_CLASSIFICATION_DISCLAIMER = (
    "语音/音乐区段为频谱与静音特征的粗粒度启发式估计，置信度不是识别正确率；"
    "不得用于版权、曲目、说话人或内容事实认定。"
)
_CLASSIFICATION_LIMITATIONS = (
    "歌声、乐器独奏、环境声和多人重叠说话可能互相混淆。",
    "压缩失真、低码率、极短片段和异常采样率会降低判断稳定性。",
    "该方法不查询版权曲库，也不执行精确音乐或说话人识别。",
)
_SPECTRUM_BANDS = (
    ("sub_bass", "次低频", 20.0, 60.0),
    ("bass", "低频", 60.0, 250.0),
    ("low_mid", "中低频", 250.0, 500.0),
    ("mid", "中频", 500.0, 2_000.0),
    ("presence", "存在感", 2_000.0, 6_000.0),
    ("brilliance", "高频", 6_000.0, 20_000.0),
)


class AudioAnalyzer:
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
                component="ffmpeg-audio",
                available=False,
                version=None,
                reason_code="FFMPEG_NOT_FOUND",
                message="FFmpeg 未安装，响度与静音分析不可用",
                action="安装 FFmpeg 并确认 ffmpeg 已加入 PATH",
            )
        return CapabilityStatus(
            component="ffmpeg-audio",
            available=True,
            version=version,
            reason_code=None,
            message="FFmpeg 响度、峰值、静音、频谱与启发式音频区段分析可用",
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
        audio_stream_ordinal: int = 0,
        silence_threshold_db: float = -50.0,
        minimum_silence_seconds: float = 0.5,
        maximum_analysis_seconds: float | None = None,
        curve_interval_seconds: float = 0.5,
        cancellation_event: threading.Event | None = None,
    ) -> AudioAnalysis:
        path = _validated_media_path(media_path)
        self._validate_options(
            audio_stream_ordinal,
            silence_threshold_db,
            minimum_silence_seconds,
            maximum_analysis_seconds,
            curve_interval_seconds,
        )
        duration, sample_rate = self._media_metadata(path, audio_stream_ordinal)
        analyzed_duration = (
            min(duration, maximum_analysis_seconds)
            if duration is not None and maximum_analysis_seconds is not None
            else maximum_analysis_seconds or duration
        )
        duration_args = (
            ["-t", _decimal(maximum_analysis_seconds)]
            if maximum_analysis_seconds is not None
            else []
        )
        audio_filter = (
            f"silencedetect=noise={_decimal(silence_threshold_db)}dB:"
            f"d={_decimal(minimum_silence_seconds)},"
            "ebur128=peak=true:framelog=verbose"
        )
        loudness_result = self.runner.run(
            self.ffmpeg_executable,
            [
                "-hide_banner",
                "-nostdin",
                "-v",
                "verbose",
                "-i",
                path,
                *duration_args,
                "-map",
                f"0:a:{audio_stream_ordinal}",
                "-vn",
                "-sn",
                "-dn",
                "-af",
                audio_filter,
                "-f",
                "null",
                "-",
            ],
            timeout_seconds=self.timeout_seconds,
            cancellation_event=cancellation_event,
        )
        volume_result = self.runner.run(
            self.ffmpeg_executable,
            [
                "-hide_banner",
                "-nostdin",
                "-v",
                "info",
                "-i",
                path,
                *duration_args,
                "-map",
                f"0:a:{audio_stream_ordinal}",
                "-vn",
                "-sn",
                "-dn",
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            timeout_seconds=self.timeout_seconds,
            cancellation_event=cancellation_event,
        )
        loudness_log = loudness_result.stderr_text()
        volume_log = volume_result.stderr_text()
        curve = _parse_curve(loudness_log, curve_interval_seconds)
        integrated = _last_metric(_INTEGRATED, loudness_log)
        loudness_range = _last_metric(_LOUDNESS_RANGE, loudness_log)
        true_peak = _last_metric(_TRUE_PEAK, loudness_log)
        sample_peak = _last_metric(_MAX_VOLUME, volume_log)
        mean_volume = _last_metric(_MEAN_VOLUME, volume_log)
        silence_intervals = _parse_silences(loudness_log, analyzed_duration)
        warnings: list[str] = []
        spectrum: SpectrumOverview | None = None
        classification: AudioContentClassification | None = None
        try:
            spectrum, classification = self._spectrum_and_classification(
                path,
                audio_stream_ordinal=audio_stream_ordinal,
                analyzed_duration=analyzed_duration,
                sample_rate=sample_rate,
                maximum_analysis_seconds=maximum_analysis_seconds,
                silence_intervals=silence_intervals,
                cancellation_event=cancellation_event,
            )
        except AnalysisError as exc:
            if exc.failure.code == AnalysisErrorCode.CANCELED:
                raise
            warnings.append("频谱与启发式语音/音乐区段未能生成；响度和静音结果仍可使用")
        except ValueError:
            warnings.append("频谱与启发式语音/音乐区段未能生成；响度和静音结果仍可使用")
        if integrated is None:
            warnings.append("未获得有效综合响度；纯静音或极短音频可能出现此情况")
        if (
            maximum_analysis_seconds is not None
            and duration is not None
            and duration > maximum_analysis_seconds
        ):
            warnings.append("音频仅分析了配置的前段时长，结果不代表完整媒体")
        if not curve:
            warnings.append("未获得响度曲线，但综合响度与峰值结果仍可使用")
        return AudioAnalysis(
            analyzer_name="ffmpeg-ebur128",
            analyzer_version=self.version(),
            stream_index=audio_stream_ordinal,
            integrated_loudness_lufs=integrated,
            loudness_range_lu=loudness_range,
            sample_peak_dbfs=sample_peak,
            true_peak_dbfs=true_peak,
            mean_volume_db=mean_volume,
            silence_threshold_db=silence_threshold_db,
            minimum_silence_seconds=minimum_silence_seconds,
            silence_intervals=silence_intervals,
            loudness_curve=curve,
            spectrum_overview=spectrum,
            content_classification=classification,
            warnings=tuple(warnings),
        )

    def _media_metadata(
        self, path: Path, audio_stream_ordinal: int
    ) -> tuple[float | None, int | None]:
        try:
            report = self.probe_service.probe(path, include_keyframes=False)
        except AnalysisError:
            return None, None
        duration = report.container.duration_seconds
        durations = [
            stream.duration_seconds
            for stream in report.audio_streams
            if stream.duration_seconds is not None
        ]
        if duration is None:
            duration = max(durations) if durations else None
        selected = (
            report.audio_streams[audio_stream_ordinal]
            if audio_stream_ordinal < len(report.audio_streams)
            else None
        )
        return duration, selected.sample_rate_hz if selected is not None else None

    def _spectrum_and_classification(
        self,
        path: Path,
        *,
        audio_stream_ordinal: int,
        analyzed_duration: float | None,
        sample_rate: int | None,
        maximum_analysis_seconds: float | None,
        silence_intervals: tuple[SilenceInterval, ...],
        cancellation_event: threading.Event | None,
    ) -> tuple[SpectrumOverview, AudioContentClassification | None]:
        width = 512
        height = 192
        minimum_frequency = 20.0
        nyquist = float(sample_rate) / 2 if sample_rate is not None else 20_000.0
        maximum_frequency = max(minimum_frequency + 1.0, min(20_000.0, nyquist))
        trim = (
            f"atrim=end={_decimal(maximum_analysis_seconds)},"
            if maximum_analysis_seconds is not None
            else ""
        )
        filter_graph = (
            f"[0:a:{audio_stream_ordinal}]"
            f"{trim}aformat=channel_layouts=mono,"
            f"showspectrumpic=s={width}x{height}:legend=0:orientation=vertical:"
            "color=intensity:saturation=0:scale=sqrt:fscale=log:win_func=hann:"
            f"start={int(minimum_frequency)}:stop={int(maximum_frequency)}:drange=80,"
            "format=gray[spectrum]"
        )
        result = self.runner.run(
            self.ffmpeg_executable,
            [
                "-hide_banner",
                "-nostdin",
                "-v",
                "error",
                "-i",
                path,
                "-filter_complex",
                filter_graph,
                "-map",
                "[spectrum]",
                "-frames:v",
                "1",
                "-c:v",
                "pgm",
                "-pix_fmt",
                "gray",
                "-f",
                "image2pipe",
                "-",
            ],
            timeout_seconds=self.timeout_seconds,
            cancellation_event=cancellation_event,
        )
        image_width, image_height, pixels = _parse_pgm(result.stdout)
        frequencies = _spectrum_frequencies(
            image_height,
            minimum_frequency,
            maximum_frequency,
        )
        row_magnitudes = _row_magnitudes(pixels, image_width, image_height)
        bands = _spectrum_bands(frequencies, row_magnitudes, maximum_frequency)
        total = sum(row_magnitudes)
        dominant = frequencies[max(range(len(row_magnitudes)), key=row_magnitudes.__getitem__)]
        centroid = (
            sum(
                frequency * magnitude
                for frequency, magnitude in zip(frequencies, row_magnitudes, strict=True)
            )
            / total
            if total > 0
            else None
        )
        overview = SpectrumOverview(
            analyzer_name="ffmpeg-showspectrumpic-relative",
            analyzer_version=self.version(),
            frequency_scale="logarithmic",
            minimum_frequency_hz=minimum_frequency,
            maximum_frequency_hz=maximum_frequency,
            analyzed_duration_seconds=analyzed_duration,
            time_bins=image_width,
            frequency_bins=image_height,
            dominant_frequency_hz=round(dominant, 2) if total > 0 else None,
            spectral_centroid_hz=round(centroid, 2) if centroid is not None else None,
            bands=bands,
            disclaimer=_SPECTRUM_DISCLAIMER,
        )
        classification = (
            _classify_audio_content(
                pixels,
                width=image_width,
                height=image_height,
                frequencies=frequencies,
                duration_seconds=analyzed_duration,
                silence_intervals=silence_intervals,
            )
            if analyzed_duration is not None and analyzed_duration > 0
            else None
        )
        return overview, classification

    @staticmethod
    def _validate_options(
        audio_stream_ordinal: int,
        silence_threshold_db: float,
        minimum_silence_seconds: float,
        maximum_analysis_seconds: float | None,
        curve_interval_seconds: float,
    ) -> None:
        if not 0 <= audio_stream_ordinal <= 255:
            raise _invalid_audio_configuration("音频轨序号必须在 0 到 255 之间")
        if not math.isfinite(silence_threshold_db) or not -120 <= silence_threshold_db <= 0:
            raise _invalid_audio_configuration("静音阈值必须在 -120 dB 到 0 dB 之间")
        if not math.isfinite(minimum_silence_seconds) or not 0.05 <= minimum_silence_seconds <= 600:
            raise _invalid_audio_configuration("最短静音时长必须在 0.05 到 600 秒之间")
        if maximum_analysis_seconds is not None and (
            not math.isfinite(maximum_analysis_seconds)
            or not 0.1 <= maximum_analysis_seconds <= 86_400
        ):
            raise _invalid_audio_configuration("最大分析时长必须在 0.1 到 86400 秒之间")
        if not math.isfinite(curve_interval_seconds) or not 0.1 <= curve_interval_seconds <= 60:
            raise _invalid_audio_configuration("响度曲线采样间隔必须在 0.1 到 60 秒之间")


def _parse_pgm(payload: bytes) -> tuple[int, int, bytes]:
    position = 0

    def token() -> bytes:
        nonlocal position
        while position < len(payload):
            if payload[position] == ord("#"):
                newline = payload.find(b"\n", position)
                position = len(payload) if newline < 0 else newline + 1
                continue
            if payload[position] in b" \t\r\n":
                position += 1
                continue
            break
        start = position
        while position < len(payload) and payload[position] not in b" \t\r\n#":
            position += 1
        if start == position:
            raise ValueError("spectrum image header is incomplete")
        return payload[start:position]

    if token() != b"P5":
        raise ValueError("spectrum image is not a binary PGM")
    try:
        width = int(token())
        height = int(token())
        maximum = int(token())
    except ValueError as exc:
        raise ValueError("spectrum image dimensions are invalid") from exc
    if not 1 <= width <= 2_048 or not 1 <= height <= 1_024 or maximum != 255:
        raise ValueError("spectrum image exceeds the bounded format")
    if position >= len(payload) or payload[position] not in b" \t\r\n":
        raise ValueError("spectrum image is missing its data separator")
    if payload[position : position + 2] == b"\r\n":
        position += 2
    else:
        position += 1
    expected = width * height
    pixels = payload[position : position + expected]
    if len(pixels) != expected:
        raise ValueError("spectrum image pixel data is incomplete")
    return width, height, pixels


def _spectrum_frequencies(
    height: int,
    minimum_frequency: float,
    maximum_frequency: float,
) -> tuple[float, ...]:
    if height == 1:
        return (minimum_frequency,)
    ratio = maximum_frequency / minimum_frequency
    return tuple(
        minimum_frequency * ratio ** ((height - 1 - row) / (height - 1)) for row in range(height)
    )


def _row_magnitudes(pixels: bytes, width: int, height: int) -> tuple[float, ...]:
    return tuple(
        sum(pixels[row * width : (row + 1) * width]) / (width * 255.0) for row in range(height)
    )


def _spectrum_bands(
    frequencies: tuple[float, ...],
    magnitudes: tuple[float, ...],
    maximum_frequency: float,
) -> tuple[SpectrumBand, ...]:
    raw: list[tuple[str, str, float, float, float, float, float]] = []
    total = sum(magnitudes)
    for key, label, configured_minimum, configured_maximum in _SPECTRUM_BANDS:
        minimum = max(20.0, configured_minimum)
        maximum = min(maximum_frequency, configured_maximum)
        if maximum <= minimum:
            continue
        values = [
            magnitude
            for frequency, magnitude in zip(frequencies, magnitudes, strict=True)
            if minimum <= frequency <= maximum
        ]
        if not values:
            continue
        raw.append(
            (
                key,
                label,
                minimum,
                maximum,
                sum(values) / len(values),
                sum(values) / total if total > 0 else 0.0,
                max(values),
            )
        )
    reference = max((item[4] for item in raw), default=0.0)
    return tuple(
        SpectrumBand(
            key=key,
            label=label,
            minimum_frequency_hz=minimum,
            maximum_frequency_hz=maximum,
            relative_magnitude=round(mean / reference, 4) if reference > 0 else 0.0,
            magnitude_share=round(share, 4),
            peak_magnitude=round(peak, 4),
        )
        for key, label, minimum, maximum, mean, share, peak in raw
    )


def _classify_audio_content(
    pixels: bytes,
    *,
    width: int,
    height: int,
    frequencies: tuple[float, ...],
    duration_seconds: float,
    silence_intervals: tuple[SilenceInterval, ...],
) -> AudioContentClassification:
    target_window = min(5.0, max(0.5, duration_seconds / 40.0))
    bucket_count = min(width, 120, max(1, math.ceil(duration_seconds / target_window)))
    global_strength = sum(pixels) / (len(pixels) * 255.0)
    raw_segments: list[AudioContentSegment] = []
    for bucket in range(bucket_count):
        column_start = bucket * width // bucket_count
        column_end = max(column_start + 1, (bucket + 1) * width // bucket_count)
        start_seconds = duration_seconds * bucket / bucket_count
        end_seconds = duration_seconds * (bucket + 1) / bucket_count
        magnitudes = tuple(
            sum(pixels[row * width + column] for column in range(column_start, column_end))
            / ((column_end - column_start) * 255.0)
            for row in range(height)
        )
        total = sum(magnitudes)
        speech_ratio = _frequency_ratio(frequencies, magnitudes, 120.0, 4_000.0, total)
        low_ratio = _frequency_ratio(frequencies, magnitudes, 20.0, 120.0, total)
        high_ratio = _frequency_ratio(
            frequencies,
            magnitudes,
            4_000.0,
            max(frequencies),
            total,
        )
        flatness = _spectral_flatness(magnitudes)
        peak = max(magnitudes, default=0.0)
        active_fraction = (
            sum(value >= peak * 0.18 for value in magnitudes) / len(magnitudes) if peak > 0 else 0.0
        )
        silence_overlap = _interval_overlap_ratio(
            start_seconds,
            end_seconds,
            silence_intervals,
        )
        label, confidence = _content_label(
            strength=(total / height if height else 0.0),
            global_strength=global_strength,
            silence_overlap=silence_overlap,
            speech_ratio=speech_ratio,
            low_ratio=low_ratio,
            high_ratio=high_ratio,
            flatness=flatness,
            active_fraction=active_fraction,
        )
        raw_segments.append(
            AudioContentSegment(
                index=bucket,
                start_seconds=round(start_seconds, 3),
                end_seconds=round(end_seconds, 3),
                label=label,
                confidence=round(confidence, 3),
                speech_band_ratio=round(speech_ratio, 3),
                spectral_flatness=round(flatness, 3),
                explanation=_classification_explanation(
                    label,
                    speech_ratio=speech_ratio,
                    flatness=flatness,
                    silence_overlap=silence_overlap,
                ),
            )
        )
    merged = _merge_content_segments(raw_segments)
    return AudioContentClassification(
        classifier_name="bounded-spectrum-heuristic",
        classifier_version="1.0.0",
        heuristic=True,
        segments=merged,
        disclaimer=_CLASSIFICATION_DISCLAIMER,
        limitations=_CLASSIFICATION_LIMITATIONS,
    )


def _frequency_ratio(
    frequencies: tuple[float, ...],
    magnitudes: tuple[float, ...],
    minimum: float,
    maximum: float,
    total: float,
) -> float:
    if total <= 0:
        return 0.0
    return (
        sum(
            magnitude
            for frequency, magnitude in zip(frequencies, magnitudes, strict=True)
            if minimum <= frequency <= maximum
        )
        / total
    )


def _spectral_flatness(magnitudes: tuple[float, ...]) -> float:
    if not magnitudes:
        return 0.0
    arithmetic = sum(magnitudes) / len(magnitudes)
    if arithmetic <= 1e-9:
        return 0.0
    geometric = math.exp(sum(math.log(max(value, 1e-6)) for value in magnitudes) / len(magnitudes))
    return min(1.0, geometric / arithmetic)


def _interval_overlap_ratio(
    start: float,
    end: float,
    intervals: tuple[SilenceInterval, ...],
) -> float:
    duration = end - start
    if duration <= 0:
        return 0.0
    overlap = sum(
        max(0.0, min(end, interval.end_seconds) - max(start, interval.start_seconds))
        for interval in intervals
    )
    return min(1.0, overlap / duration)


def _content_label(
    *,
    strength: float,
    global_strength: float,
    silence_overlap: float,
    speech_ratio: float,
    low_ratio: float,
    high_ratio: float,
    flatness: float,
    active_fraction: float,
) -> tuple[AudioContentLabel, float]:
    if silence_overlap >= 0.5:
        return AudioContentLabel.SILENCE, min(0.98, 0.72 + silence_overlap * 0.25)
    if strength < max(0.006, global_strength * 0.12) or active_fraction < 0.025:
        return AudioContentLabel.MIXED_OR_UNCERTAIN, 0.46

    speech_score = (
        0.48 * speech_ratio
        + 0.18 * max(0.0, 1.0 - high_ratio / 0.3)
        + 0.12 * max(0.0, 1.0 - low_ratio / 0.25)
        + 0.22 * max(0.0, 1.0 - abs(active_fraction - 0.18) / 0.25)
    )
    music_score = (
        0.32 * min(1.0, (low_ratio + high_ratio) / 0.35)
        + 0.28 * min(1.0, active_fraction / 0.4)
        + 0.22 * min(1.0, flatness / 0.35)
        + 0.18 * min(1.0, high_ratio / 0.22)
    )
    if speech_score >= 0.68 and speech_score - music_score >= 0.1:
        confidence = min(0.82, 0.58 + (speech_score - music_score) * 0.35)
        return AudioContentLabel.SPEECH_LIKELY, confidence
    if music_score >= 0.58 and music_score - speech_score >= 0.07:
        confidence = min(0.82, 0.57 + (music_score - speech_score) * 0.35)
        return AudioContentLabel.MUSIC_LIKELY, confidence
    confidence = min(0.65, 0.5 + abs(speech_score - music_score) * 0.2)
    return AudioContentLabel.MIXED_OR_UNCERTAIN, confidence


def _classification_explanation(
    label: AudioContentLabel,
    *,
    speech_ratio: float,
    flatness: float,
    silence_overlap: float,
) -> str:
    if label == AudioContentLabel.SILENCE:
        return f"与静音检测区间重合约 {silence_overlap:.0%}。"
    evidence = f"语音常见频段占比约 {speech_ratio:.0%}，频谱平坦度约 {flatness:.2f}。"
    messages = {
        AudioContentLabel.SPEECH_LIKELY: "特征更接近语音，但歌声或窄带乐器可能被混淆。",
        AudioContentLabel.MUSIC_LIKELY: "特征更接近宽频音乐，但环境声或复杂噪声可能被混淆。",
        AudioContentLabel.MIXED_OR_UNCERTAIN: "语音与音乐证据不足或相互冲突，保持不确定。",
    }
    return f"{evidence}{messages[label]}"


def _merge_content_segments(
    segments: list[AudioContentSegment],
) -> tuple[AudioContentSegment, ...]:
    merged: list[AudioContentSegment] = []
    for segment in segments:
        previous = merged[-1] if merged else None
        if previous is None or previous.label != segment.label:
            merged.append(segment)
            continue
        previous_duration = previous.end_seconds - previous.start_seconds
        segment_duration = segment.end_seconds - segment.start_seconds
        combined_duration = previous_duration + segment_duration
        confidence = (
            previous.confidence * previous_duration + segment.confidence * segment_duration
        ) / combined_duration
        speech_ratio = (
            previous.speech_band_ratio * previous_duration
            + segment.speech_band_ratio * segment_duration
        ) / combined_duration
        flatness = (
            previous.spectral_flatness * previous_duration
            + segment.spectral_flatness * segment_duration
        ) / combined_duration
        merged[-1] = AudioContentSegment(
            index=previous.index,
            start_seconds=previous.start_seconds,
            end_seconds=segment.end_seconds,
            label=previous.label,
            confidence=round(confidence, 3),
            speech_band_ratio=round(speech_ratio, 3),
            spectral_flatness=round(flatness, 3),
            explanation=_classification_explanation(
                previous.label,
                speech_ratio=speech_ratio,
                flatness=flatness,
                silence_overlap=1.0 if previous.label == AudioContentLabel.SILENCE else 0.0,
            ),
        )
    return tuple(
        AudioContentSegment(
            index=index,
            start_seconds=segment.start_seconds,
            end_seconds=segment.end_seconds,
            label=segment.label,
            confidence=segment.confidence,
            speech_band_ratio=segment.speech_band_ratio,
            spectral_flatness=segment.spectral_flatness,
            explanation=segment.explanation,
        )
        for index, segment in enumerate(merged)
    )


def _parse_curve(log: str, interval_seconds: float) -> tuple[LoudnessPoint, ...]:
    points: list[LoudnessPoint] = []
    last_kept = -math.inf
    for match in _CURVE_POINT.finditer(log):
        timestamp = _metric(match.group("time"))
        if timestamp is None or timestamp + 1e-6 < last_kept + interval_seconds:
            continue
        points.append(
            LoudnessPoint(
                timestamp_seconds=timestamp,
                momentary_lufs=_metric(match.group("m")),
                short_term_lufs=_metric(match.group("s")),
                integrated_lufs=_metric(match.group("i")),
                loudness_range_lu=_metric(match.group("lra")),
            )
        )
        last_kept = timestamp
    return tuple(points)


def _parse_silences(log: str, media_duration: float | None) -> tuple[SilenceInterval, ...]:
    events: list[tuple[int, float, float | None]] = []
    for match in _SILENCE_START.finditer(log):
        value = _metric(match.group("value"))
        if value is not None:
            events.append((match.start(), value, None))
    for match in _SILENCE_END.finditer(log):
        end = _metric(match.group("end"))
        duration = _metric(match.group("duration"))
        if end is not None:
            events.append((match.start(), end, duration))
    events.sort(key=lambda item: item[0])
    open_start: float | None = None
    intervals: list[SilenceInterval] = []
    for _, timestamp, duration in events:
        if duration is None:
            open_start = max(0.0, timestamp)
        elif open_start is not None:
            end = max(open_start, timestamp)
            intervals.append(
                SilenceInterval(
                    start_seconds=open_start,
                    end_seconds=end,
                    duration_seconds=max(0.0, duration),
                )
            )
            open_start = None
    if open_start is not None and media_duration is not None and media_duration > open_start:
        intervals.append(
            SilenceInterval(
                start_seconds=open_start,
                end_seconds=media_duration,
                duration_seconds=media_duration - open_start,
            )
        )
    return tuple(intervals)


def _last_metric(pattern: re.Pattern[str], log: str) -> float | None:
    values = [_metric(match.group("value")) for match in pattern.finditer(log)]
    valid = [value for value in values if value is not None]
    return valid[-1] if valid else None


def _metric(value: str) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _decimal(value: float) -> str:
    return format(value, ".6f").rstrip("0").rstrip(".")


def _invalid_audio_configuration(message: str) -> AnalysisError:
    return AnalysisError(
        AnalysisFailure(
            code=AnalysisErrorCode.INVALID_CONFIGURATION,
            message=message,
            action="调整音频分析参数后重试",
        )
    )
