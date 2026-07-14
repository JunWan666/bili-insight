from __future__ import annotations

import importlib.metadata
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from app.analysis import (
    AnalysisError,
    FasterWhisperAdapter,
    FasterWhisperConfig,
    LocalAnalysisEngine,
    PaddleOcrAdapter,
    PaddleOcrConfig,
)
from app.analysis.ocr import _extract_ocr_items


def test_optional_model_capabilities_are_explicit() -> None:
    for capability in (
        FasterWhisperAdapter().capability(),
        PaddleOcrAdapter().capability(),
    ):
        assert capability.component
        assert capability.message
        if capability.available:
            assert capability.version
            assert capability.reason_code is None
        else:
            assert capability.reason_code
            assert capability.action


def test_engine_reports_independent_capabilities() -> None:
    capabilities = LocalAnalysisEngine(process_timeout_seconds=30).capabilities()
    by_name = {item.component: item for item in capabilities}
    assert by_name["ffprobe"].available
    assert by_name["ffmpeg-audio"].available
    assert by_name["ffmpeg-scenes"].available
    assert by_name["local-content-summary"].available
    assert "faster-whisper" in by_name
    assert "paddleocr" in by_name


def test_adapter_configuration_bounds() -> None:
    invalid_asr_configurations = [
        {"model_size_or_path": ""},
        {"compute_type": ""},
        {"beam_size": 0},
        {"cpu_threads": -1},
        {"num_workers": 0},
    ]
    for values in invalid_asr_configurations:
        with pytest.raises(ValueError):
            FasterWhisperConfig(**values)  # type: ignore[arg-type]

    invalid_ocr_configurations = [
        {"language": ""},
        {"sample_interval_seconds": 0.01},
        {"maximum_frames": 0},
        {"maximum_width": 100},
        {"minimum_confidence": 2},
        {"cpu_threads": 0},
    ]
    for values in invalid_ocr_configurations:
        with pytest.raises(ValueError):
            PaddleOcrConfig(**values)  # type: ignore[arg-type]


def test_paddle_result_adapter_supports_legacy_and_current_shapes() -> None:
    legacy = [[[[[0, 0], [1, 0], [1, 1], [0, 1]], ("硬字幕", 0.91)]]]
    current = {"res": {"rec_texts": ["片头", "标题"], "rec_scores": [0.8, 0.95]}}
    assert ("硬字幕", 0.91) in _extract_ocr_items(legacy)
    assert ("片头", 0.8) in _extract_ocr_items(current)
    assert ("标题", 0.95) in _extract_ocr_items(current)
    encoded = '{"rec_texts":["JSON 结果"],"rec_scores":[0.88]}'
    assert ("JSON 结果", 0.88) in _extract_ocr_items(encoded)


def test_unavailable_adapters_raise_safe_dependency_errors(
    monkeypatch: pytest.MonkeyPatch, sample_media: Path
) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(AnalysisError) as asr_error:
        FasterWhisperAdapter().transcribe(sample_media)
    assert "未安装" in str(asr_error.value)

    monkeypatch.setitem(sys.modules, "paddle", None)
    monkeypatch.setitem(sys.modules, "paddleocr", None)
    with pytest.raises(AnalysisError) as ocr_error:
        PaddleOcrAdapter().recognize(sample_media)
    assert "未安装" in str(ocr_error.value)


def test_installed_faster_whisper_adapter_executes_transcription(
    monkeypatch: pytest.MonkeyPatch,
    sample_media: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FakeWhisperModel:
        initialization: tuple[str, dict[str, Any]] | None = None

        def __init__(self, model_name: str, **kwargs: Any) -> None:
            FakeWhisperModel.initialization = (model_name, kwargs)

        def transcribe(self, _: str, **__: Any) -> tuple[Iterator[object], object]:
            segments = iter(
                [
                    SimpleNamespace(
                        start=0.25,
                        end=1.5,
                        text="真实适配路径",
                        avg_logprob=-0.1,
                        no_speech_prob=0.05,
                    )
                ]
            )
            return segments, SimpleNamespace(language="zh")

    module = ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    real_version = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: "9.9.0" if name == "faster-whisper" else real_version(name),
    )

    private_model_path = (tmp_path / "private" / "whisper-model").resolve()
    caplog.set_level(logging.INFO, logger="app.analysis.asr")
    adapter = FasterWhisperAdapter(FasterWhisperConfig(model_size_or_path=str(private_model_path)))
    result = adapter.transcribe(sample_media)
    assert result.model_version == "9.9.0"
    assert result.model_name is not None
    assert result.model_name.startswith("faster-whisper:local-model-")
    assert str(private_model_path) not in result.model_name
    assert result.language == "zh"
    assert result.segments[0].text == "真实适配路径"
    assert result.segments[0].confidence is not None
    assert FakeWhisperModel.initialization is not None
    assert FakeWhisperModel.initialization[0] == str(private_model_path)
    assert FakeWhisperModel.initialization[1]["cpu_threads"] == 4
    assert all(str(private_model_path) not in record.getMessage() for record in caplog.records)
    assert all(
        getattr(record, "model", None) != str(private_model_path) for record in caplog.records
    )


def test_installed_paddleocr_adapter_executes_sampled_frames(
    monkeypatch: pytest.MonkeyPatch, sample_media: Path
) -> None:
    class FakePaddleOcr:
        initialization: dict[str, Any] | None = None

        def __init__(self, **kwargs: Any) -> None:
            FakePaddleOcr.initialization = kwargs

        def predict(self, _: str) -> list[dict[str, object]]:
            return [{"res": {"rec_texts": ["画面标题"], "rec_scores": [0.93]}}]

    paddle_module = ModuleType("paddle")
    paddle_module.__dict__["__version__"] = "3.1.0"
    configured_threads: list[int] = []
    paddle_module.__dict__["set_num_threads"] = configured_threads.append
    ocr_module = ModuleType("paddleocr")
    ocr_module.PaddleOCR = FakePaddleOcr  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "paddle", paddle_module)
    monkeypatch.setitem(sys.modules, "paddleocr", ocr_module)
    real_version = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: "3.2.0" if name == "paddleocr" else real_version(name),
    )

    adapter = PaddleOcrAdapter(
        PaddleOcrConfig(
            sample_interval_seconds=1,
            maximum_frames=3,
            maximum_width=320,
            minimum_confidence=0.5,
        ),
        timeout_seconds=30,
    )
    result = adapter.recognize(sample_media)
    assert result.segments
    assert all(segment.text == "画面标题" for segment in result.segments)
    assert all(segment.confidence == pytest.approx(0.93) for segment in result.segments)
    assert all(
        segment.evidence_id and segment.evidence_id.endswith(".jpg") for segment in result.segments
    )
    assert FakePaddleOcr.initialization is not None
    assert FakePaddleOcr.initialization["device"] == "cpu"
    assert configured_threads == [4]
