from __future__ import annotations

from pathlib import Path

import pytest

from app.analysis import AnalysisError, SceneAnalyzer, to_json_compatible


def test_scene_detection_and_keyframe_extraction_use_real_ffmpeg(
    sample_media: Path, tmp_path: Path
) -> None:
    analyzer = SceneAnalyzer(timeout_seconds=30)
    scenes = analyzer.analyze(
        sample_media,
        threshold=0.1,
        minimum_scene_seconds=0.2,
    )
    assert scenes.duration_seconds >= 2.9
    assert len(scenes.scenes) >= 3
    assert scenes.scenes[0].start_seconds == 0
    assert scenes.scenes[-1].end_seconds == scenes.duration_seconds
    assert scenes.average_scene_length_seconds > 0
    assert scenes.scene_density_per_minute > 0

    keyframes = analyzer.extract_keyframes(
        sample_media,
        scenes,
        tmp_path / "keyframes",
        maximum_keyframes=3,
        maximum_width=320,
    )
    assert len(keyframes.artifacts) == 3
    for artifact in keyframes.artifacts:
        assert artifact.path.is_file()
        assert artifact.size_bytes == artifact.path.stat().st_size
        assert len(artifact.sha256) == 64
    serialized = to_json_compatible(keyframes)
    serialized_artifacts = serialized["artifacts"]
    assert all(item["path"] == item["filename"] for item in serialized_artifacts)
    assert str(tmp_path.resolve()) not in str(serialized)


def test_scene_and_keyframe_limits_are_enforced(sample_media: Path, tmp_path: Path) -> None:
    analyzer = SceneAnalyzer(timeout_seconds=30)
    truncated = analyzer.analyze(
        sample_media,
        threshold=0.1,
        minimum_scene_seconds=0.2,
        maximum_scenes=1,
    )
    assert truncated.truncated is True
    assert len(truncated.scenes) == 1

    full = analyzer.analyze(sample_media, threshold=0.1, minimum_scene_seconds=0.2)
    single = analyzer.extract_keyframes(
        sample_media,
        full,
        tmp_path / "single",
        maximum_keyframes=1,
    )
    assert len(single.artifacts) == 1

    for options in (
        {"maximum_keyframes": 0},
        {"maximum_width": 100},
        {"jpeg_quality": 32},
    ):
        with pytest.raises(AnalysisError):
            analyzer.extract_keyframes(
                sample_media,
                full,
                tmp_path / "invalid",
                **options,  # type: ignore[arg-type]
            )
