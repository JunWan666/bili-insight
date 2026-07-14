from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.analysis import KeyframeArtifact, analysis_json_bytes, to_json_compatible


def test_generic_serialization_never_exposes_absolute_path(tmp_path: Path) -> None:
    absolute_path = (tmp_path / "frame.jpg").resolve()
    value = KeyframeArtifact(
        index=1,
        timestamp_seconds=1.25,
        scene_index=1,
        filename="frame.jpg",
        path=absolute_path,
        size_bytes=12,
        sha256="a" * 64,
    )
    compatible = to_json_compatible(value)
    assert compatible["path"] == "frame.jpg"
    encoded = analysis_json_bytes(
        {"artifact": value, "generated": datetime(2026, 7, 14, tzinfo=UTC)}
    )
    assert str(absolute_path).encode() not in encoded
    payload = json.loads(encoded)
    assert payload["generated"] == "2026-07-14T00:00:00+00:00"


def test_mapping_path_keys_are_reduced_to_basename(tmp_path: Path) -> None:
    absolute_path = (tmp_path / "internal-key").resolve()
    encoded = analysis_json_bytes({absolute_path: "value"})
    assert str(absolute_path).encode() not in encoded
    assert json.loads(encoded) == {"internal-key": "value"}
