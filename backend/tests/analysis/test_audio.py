from __future__ import annotations

from pathlib import Path

import pytest

from app.analysis import (
    AnalysisError,
    AnalysisErrorCode,
    AnalysisFailure,
    AudioAnalyzer,
    AudioContentLabel,
)
from app.analysis.audio import _content_label


def test_ffmpeg_analyzes_loudness_peak_and_silence(sample_media: Path) -> None:
    analyzer = AudioAnalyzer(timeout_seconds=30)
    result = analyzer.analyze(
        sample_media,
        silence_threshold_db=-45,
        minimum_silence_seconds=0.3,
        curve_interval_seconds=0.2,
    )
    assert result.analyzer_name == "ffmpeg-ebur128"
    assert result.integrated_loudness_lufs is not None
    assert -30 < result.integrated_loudness_lufs < -10
    assert result.sample_peak_dbfs is not None
    assert result.true_peak_dbfs is not None
    assert result.mean_volume_db is not None
    assert result.loudness_range_lu is not None
    assert len(result.loudness_curve) >= 5
    assert len(result.silence_intervals) >= 2
    assert result.silence_intervals[0].start_seconds == pytest.approx(0, abs=0.05)
    assert result.silence_intervals[0].end_seconds == pytest.approx(0.5, abs=0.1)
    assert result.silence_intervals[-1].end_seconds == pytest.approx(3.0, abs=0.15)
    assert result.spectrum_overview is not None
    assert result.spectrum_overview.bands
    assert result.spectrum_overview.time_bins <= 512
    assert result.spectrum_overview.frequency_bins <= 192
    assert result.spectrum_overview.dominant_frequency_hz == pytest.approx(440, rel=0.2)
    assert "音频指纹" in result.spectrum_overview.disclaimer
    assert result.content_classification is not None
    assert result.content_classification.heuristic is True
    assert result.content_classification.segments
    assert all(0 <= item.confidence <= 1 for item in result.content_classification.segments)
    assert "版权" in result.content_classification.disclaimer
    assert any("精确音乐" in item for item in result.content_classification.limitations)


def test_audio_analysis_can_limit_duration(sample_media: Path) -> None:
    result = AudioAnalyzer(timeout_seconds=30).analyze(
        sample_media,
        maximum_analysis_seconds=1.0,
    )
    assert any("前段时长" in warning for warning in result.warnings)


def test_audio_content_classifier_keeps_likely_labels_conservative() -> None:
    speech, speech_confidence = _content_label(
        strength=0.4,
        global_strength=0.4,
        silence_overlap=0,
        speech_ratio=0.9,
        low_ratio=0.03,
        high_ratio=0.05,
        flatness=0.15,
        active_fraction=0.15,
    )
    music, music_confidence = _content_label(
        strength=0.5,
        global_strength=0.4,
        silence_overlap=0,
        speech_ratio=0.45,
        low_ratio=0.25,
        high_ratio=0.3,
        flatness=0.45,
        active_fraction=0.5,
    )
    uncertain, uncertain_confidence = _content_label(
        strength=0.001,
        global_strength=0.4,
        silence_overlap=0,
        speech_ratio=0.8,
        low_ratio=0.05,
        high_ratio=0.05,
        flatness=0.1,
        active_fraction=0.01,
    )

    assert speech == AudioContentLabel.SPEECH_LIKELY
    assert music == AudioContentLabel.MUSIC_LIKELY
    assert uncertain == AudioContentLabel.MIXED_OR_UNCERTAIN
    assert speech_confidence <= 0.82
    assert music_confidence <= 0.82
    assert uncertain_confidence < speech_confidence


def test_audio_spectrum_cancellation_is_not_downgraded_to_a_warning(
    sample_media: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = AudioAnalyzer(timeout_seconds=30)

    def canceled(*_: object, **__: object) -> object:
        raise AnalysisError(
            AnalysisFailure(
                code=AnalysisErrorCode.CANCELED,
                message="分析任务已取消",
                action="可按需重新创建分析任务",
            )
        )

    monkeypatch.setattr(analyzer, "_spectrum_and_classification", canceled)
    with pytest.raises(AnalysisError) as captured:
        analyzer.analyze(sample_media)
    assert captured.value.failure.code == AnalysisErrorCode.CANCELED
