from __future__ import annotations

from pathlib import Path

import pytest

from app.analysis import (
    AnalysisError,
    AnalysisErrorCode,
    MediaProbeService,
    analysis_json_bytes,
)


def test_ffprobe_reads_real_short_media(sample_media: Path) -> None:
    service = MediaProbeService(timeout_seconds=30)
    capability = service.capability()
    assert capability.available is True
    assert capability.version

    report = service.probe(sample_media, include_keyframes=True)
    assert report.probe_name == "ffprobe"
    assert report.container.duration_seconds == pytest.approx(3.0, abs=0.15)
    assert report.container.size_bytes == sample_media.stat().st_size
    assert report.container.tags.get("title") == "analysis-test"
    assert len(report.video_streams) == 1
    assert len(report.audio_streams) == 1
    video = report.video_streams[0]
    assert (video.width, video.height) == (320, 180)
    assert video.average_frame_rate == pytest.approx(25.0, abs=0.01)
    assert video.hdr_type in {"SDR", "UNKNOWN"}
    assert video.keyframes is not None
    assert video.keyframes.count >= 3
    assert video.keyframes.average_interval_seconds == pytest.approx(1.0, abs=0.2)
    audio = report.audio_streams[0]
    assert audio.sample_rate_hz == 48_000
    assert audio.channels == 1

    encoded = analysis_json_bytes(report)
    assert b'"probe_name": "ffprobe"' in encoded


def test_ffprobe_rejects_missing_media(tmp_path: Path) -> None:
    with pytest.raises(AnalysisError) as caught:
        MediaProbeService(timeout_seconds=5).probe(tmp_path / "missing.mp4")
    assert caught.value.failure.code == AnalysisErrorCode.INVALID_MEDIA


def test_keyframe_timestamps_are_bounded_without_losing_total_count(sample_media: Path) -> None:
    report = MediaProbeService(timeout_seconds=30).probe(
        sample_media,
        max_keyframes_per_stream=1,
    )
    statistics = report.video_streams[0].keyframes
    assert statistics is not None
    assert statistics.count >= 3
    assert len(statistics.timestamps_seconds) == 1
    assert statistics.truncated is True
