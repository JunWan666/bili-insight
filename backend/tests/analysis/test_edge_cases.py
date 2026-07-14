from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.analysis import (
    AnalysisError,
    AnalysisErrorCode,
    AudioAnalyzer,
    MediaProbeService,
    ProcessRunner,
    SceneAnalyzer,
    SubtitleDocument,
    TranscriptSource,
    analysis_json_bytes,
)
from app.analysis.media import _detect_hdr_type, _ratio


def test_missing_ffmpeg_capabilities_are_reported_without_raising(tmp_path: Path) -> None:
    missing = tmp_path / "definitely-missing-binary"
    assert not MediaProbeService(ffprobe_executable=missing).capability().available
    assert not AudioAnalyzer(ffmpeg_executable=missing).capability().available
    assert not SceneAnalyzer(ffmpeg_executable=missing).capability().available


def test_process_configuration_and_arguments_are_validated() -> None:
    with pytest.raises(AnalysisError) as timeout_configuration:
        ProcessRunner(default_timeout_seconds=0)
    assert timeout_configuration.value.failure.code == AnalysisErrorCode.INVALID_CONFIGURATION

    runner = ProcessRunner(default_timeout_seconds=5)
    with pytest.raises(AnalysisError) as null_argument:
        runner.run(sys.executable, ["bad\x00argument"])
    assert null_argument.value.failure.code == AnalysisErrorCode.INVALID_CONFIGURATION

    with pytest.raises(AnalysisError) as zero_timeout:
        runner.run(sys.executable, ["-c", "print('ok')"], timeout_seconds=0)
    assert zero_timeout.value.failure.code == AnalysisErrorCode.INVALID_CONFIGURATION


def test_process_check_false_returns_nonzero_result() -> None:
    result = ProcessRunner(default_timeout_seconds=5).run(
        sys.executable,
        ["-c", "raise SystemExit(7)"],
        check=False,
    )
    assert result.return_code != 0


def test_media_option_validation_uses_actionable_error(sample_media: Path) -> None:
    with pytest.raises(AnalysisError) as caught:
        MediaProbeService(timeout_seconds=30).probe(
            sample_media,
            max_keyframes_per_stream=0,
        )
    assert caught.value.failure.code == AnalysisErrorCode.INVALID_CONFIGURATION


def test_media_helpers_detect_hdr_and_invalid_ratios() -> None:
    assert _detect_hdr_type({"color_transfer": "smpte2084", "color_primaries": "bt2020"}) == (
        "HDR10"
    )
    assert _detect_hdr_type({"color_transfer": "arib-std-b67"}) == "HLG"
    assert _detect_hdr_type({"side_data_list": [{"side_data_type": "DOVI configuration"}]}) == (
        "DOLBY_VISION"
    )
    assert _ratio("30000/1001") == pytest.approx(29.97003)
    assert _ratio("1/0") is None
    assert _ratio("invalid") is None


@pytest.mark.parametrize(
    ("options", "message_fragment"),
    [
        ({"audio_stream_ordinal": -1}, "音频轨"),
        ({"silence_threshold_db": 2}, "静音阈值"),
        ({"minimum_silence_seconds": 0.001}, "最短静音"),
        ({"maximum_analysis_seconds": 0.01}, "最大分析时长"),
        ({"curve_interval_seconds": 0.01}, "采样间隔"),
    ],
)
def test_audio_options_are_bounded(
    sample_media: Path,
    options: dict[str, int | float],
    message_fragment: str,
) -> None:
    with pytest.raises(AnalysisError) as caught:
        AudioAnalyzer(timeout_seconds=30).analyze(sample_media, **options)  # type: ignore[arg-type]
    assert message_fragment in caught.value.failure.message


def test_scene_no_cut_warning_and_option_bounds(sample_media: Path) -> None:
    result = SceneAnalyzer(timeout_seconds=30).analyze(sample_media, threshold=1.0)
    assert len(result.scenes) == 1
    assert any("未检测到" in warning for warning in result.warnings)

    with pytest.raises(AnalysisError):
        SceneAnalyzer(timeout_seconds=30).analyze(sample_media, threshold=0)


def test_keyframe_output_must_be_directory(sample_media: Path, tmp_path: Path) -> None:
    analyzer = SceneAnalyzer(timeout_seconds=30)
    scenes = analyzer.analyze(sample_media, threshold=1.0)
    output_file = tmp_path / "not-a-directory"
    output_file.write_text("occupied", encoding="utf-8")
    with pytest.raises(AnalysisError) as caught:
        analyzer.extract_keyframes(sample_media, scenes, output_file)
    assert caught.value.failure.code == AnalysisErrorCode.EXPORT_FAILED


def test_serialization_sorts_sets_and_rejects_unknown_types() -> None:
    assert analysis_json_bytes({"values": {"b", "a"}}, pretty=False) == (b'{"values":["a","b"]}\n')
    with pytest.raises(TypeError):
        analysis_json_bytes({"unsupported": object()})


def test_empty_subtitle_document_is_valid_and_utc_normalized() -> None:
    document = SubtitleDocument(
        language="zh-CN",
        source=TranscriptSource.OCR,
        segments=(),
        model_name=None,
        model_version=None,
        generated_at=datetime(2026, 7, 14, tzinfo=UTC),
    )
    assert document.generated_at.tzinfo == UTC
