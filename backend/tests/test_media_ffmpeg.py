from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.core.process_limits import MINIMUM_PROCESS_MEMORY_LIMIT_BYTES
from app.media.download import DownloadCanceled
from app.media.ffmpeg import FFmpegError, FFmpegProcessor, MediaProbe, MediaValidationError


class Checkpoint:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def checkpoint(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


def run_command(arguments: Sequence[str]) -> None:
    subprocess.run(  # noqa: S603
        list(arguments),
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )


@pytest.fixture
def short_tracks(tmp_path: Path) -> tuple[Path, Path]:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg is not installed")
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    run_command(
        (
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-y",
            str(video),
        )
    )
    run_command(
        (
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=44100",
            "-t",
            "1",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-vn",
            "-y",
            str(audio),
        )
    )
    return video, audio


async def test_real_ffmpeg_merge_and_probe(short_tracks: tuple[Path, Path], tmp_path: Path) -> None:
    video, audio = short_tracks
    output = tmp_path / "merged.partial.mp4"
    processor = FFmpegProcessor(process_timeout_seconds=30, probe_timeout_seconds=10)
    progress: list[float] = []
    checkpoint = Checkpoint()
    probe = await processor.process(
        video_path=video,
        audio_path=audio,
        output_path=output,
        container="mp4",
        processing_mode="copy",
        expected_duration=1.0,
        checkpoint=checkpoint,
        progress=lambda value: _append(progress, value),
    )

    assert output.is_file()
    assert {item["type"] for item in probe.streams} == {"video", "audio"}
    assert probe.size == output.stat().st_size
    assert probe.as_dict()["formatName"]
    assert progress[-1] == 1.0
    assert checkpoint.calls > 0

    video_probe = await processor.validate_input(video, expected_kind="video")
    audio_probe = await processor.validate_input(audio, expected_kind="audio")
    assert video_probe.duration > 0
    assert audio_probe.duration > 0
    with pytest.raises(MediaValidationError) as mismatch:
        await processor.validate_input(video, expected_kind="audio")
    assert mismatch.value.code == "MEDIA_STREAM_MISSING"


async def _append(values: list[float], value: float) -> None:
    values.append(value)


@pytest.mark.parametrize(
    ("container", "processing_mode"),
    [("m4a", "copy"), ("mp3", "transcode"), ("flac", "transcode")],
)
async def test_real_audio_outputs(
    short_tracks: tuple[Path, Path],
    tmp_path: Path,
    container: str,
    processing_mode: str,
) -> None:
    _, audio = short_tracks
    processor = FFmpegProcessor(process_timeout_seconds=30, probe_timeout_seconds=10)
    output = tmp_path / f"audio.partial.{container}"
    probe = await processor.process(
        video_path=None,
        audio_path=audio,
        output_path=output,
        container=container,
        processing_mode=processing_mode,
        expected_duration=1.0,
        checkpoint=Checkpoint(),
        progress=lambda _value: _append([], _value),
    )
    assert {item["type"] for item in probe.streams} == {"audio"}
    assert output.stat().st_size > 0


def test_ffmpeg_argument_builder_is_shell_free_and_bounded(tmp_path: Path) -> None:
    processor = FFmpegProcessor(max_threads=3)
    video = tmp_path / "video;touch injected.mp4"
    audio = tmp_path / "audio.m4a"
    output = tmp_path / "output.mp4"
    arguments = processor._build_arguments(
        video_path=video,
        audio_path=audio,
        output_path=output,
        container="mp4",
        processing_mode="transcode",
    )
    assert arguments[0] == "ffmpeg"
    assert str(video) in arguments
    assert "file,pipe" in arguments
    assert arguments.count("3") == 2
    assert "libx264" in arguments
    assert "aac" in arguments
    assert "shell=True" not in arguments

    mkv = processor._build_arguments(
        video_path=video,
        audio_path=None,
        output_path=tmp_path / "out.mkv",
        container="mkv",
        processing_mode="copy",
    )
    assert "matroska" in mkv


@pytest.mark.parametrize(
    ("container", "mode", "video", "audio", "code"),
    [
        ("avi", "copy", True, True, "CONTAINER_UNSUPPORTED"),
        ("mp4", "invalid", True, True, "PROCESSING_MODE_UNSUPPORTED"),
        ("m4a", "copy", False, False, "AUDIO_INPUT_MISSING"),
        ("flac", "transcode", False, False, "AUDIO_INPUT_MISSING"),
    ],
)
def test_argument_builder_rejects_invalid_combinations(
    tmp_path: Path,
    container: str,
    mode: str,
    video: bool,
    audio: bool,
    code: str,
) -> None:
    processor = FFmpegProcessor()
    with pytest.raises(FFmpegError) as caught:
        processor._build_arguments(
            video_path=tmp_path / "v" if video else None,
            audio_path=tmp_path / "a" if audio else None,
            output_path=tmp_path / "o",
            container=container,
            processing_mode=mode,
        )
    assert caught.value.code == code


def test_probe_parser_handles_timestamps_ticks_and_tags() -> None:
    payload = {
        "format": {"duration": "10.0", "format_name": "mov,mp4", "bit_rate": "1000"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "duration_ts": 250,
                "time_base": "1/25",
                "start_time": "-0.04",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "tags": {"DURATION": "00:00:10.000000"},
                "start_time": "0",
            },
        ],
    }
    probe = FFmpegProcessor._parse_probe(payload, actual_size=1234)
    assert probe == MediaProbe(
        duration=10.0,
        size=1234,
        bitrate=1000,
        format_name="mov,mp4",
        streams=(
            {
                "type": "video",
                "codec": "h264",
                "width": 1920,
                "height": 1080,
                "duration": 10.0,
                "startTime": -0.04,
            },
            {
                "type": "audio",
                "codec": "aac",
                "channels": 2,
                "sampleRate": 48000,
                "duration": 10.0,
                "startTime": 0.0,
            },
        ),
    )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"format": {}, "streams": []},
        {"format": {"duration": "0"}, "streams": []},
        {"format": {"duration": "1"}, "streams": [{"codec_type": "data"}]},
    ],
)
def test_probe_parser_rejects_invalid_documents(payload: object) -> None:
    with pytest.raises(ValueError):
        FFmpegProcessor._parse_probe(payload, actual_size=1)


def test_numeric_and_duration_parsers_reject_nonfinite_values() -> None:
    assert FFmpegProcessor._float_value("1.5") == 1.5
    assert FFmpegProcessor._float_value(float("inf")) is None
    assert FFmpegProcessor._float_value(True) is None
    assert FFmpegProcessor._int_value("12") == 12
    assert FFmpegProcessor._int_value(float("inf")) is None
    assert FFmpegProcessor._signed_float_value("-1.25") == -1.25
    assert FFmpegProcessor._duration_from_ticks("250", "1/25") == 10
    assert FFmpegProcessor._duration_from_ticks("x", "1/25") is None
    assert FFmpegProcessor._duration_from_ticks("1", "broken") is None
    assert FFmpegProcessor._timestamp_duration("01:02:03.5") == 3723.5
    assert FFmpegProcessor._timestamp_duration("00:99:00") is None
    assert FFmpegProcessor._timestamp_duration(object()) is None


async def test_bounded_stream_readers() -> None:
    stream = asyncio.StreamReader()
    stream.feed_data(b"0123456789")
    stream.feed_eof()
    value, exceeded = await FFmpegProcessor._read_with_limit(stream, 5)
    assert value == b"01234"
    assert exceeded is True

    stream = asyncio.StreamReader()
    stream.feed_data(b"0123456789")
    stream.feed_eof()
    assert await FFmpegProcessor._read_bounded(stream, 4) == b"6789"
    assert await FFmpegProcessor._read_bounded(None, 4) == b""


async def test_process_rejects_missing_inputs_and_control_cancel(tmp_path: Path) -> None:
    processor = FFmpegProcessor(ffmpeg="missing-ffmpeg", ffprobe="missing-ffprobe")
    with pytest.raises(FFmpegError) as unavailable:
        processor.check_available()
    assert unavailable.value.code == "MEDIA_TOOL_UNAVAILABLE"

    real = FFmpegProcessor()
    with pytest.raises(FFmpegError) as missing:
        await real.process(
            video_path=None,
            audio_path=None,
            output_path=tmp_path / "out.mp4",
            container="mp4",
            processing_mode="copy",
            expected_duration=1,
            checkpoint=Checkpoint(),
            progress=lambda _value: _append([], _value),
        )
    assert missing.value.code == "MEDIA_INPUT_MISSING"

    with pytest.raises(ValueError):
        FFmpegProcessor(max_threads=0)
    with pytest.raises(ValueError):
        FFmpegProcessor(memory_limit_bytes=MINIMUM_PROCESS_MEMORY_LIMIT_BYTES - 1)


async def test_async_media_spawn_uses_parent_limits_without_preexec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_spawn = asyncio.create_subprocess_exec
    spawn_keywords: dict[str, object] = {}
    applied: list[tuple[int, int]] = []

    async def recording_spawn(*arguments: object, **kwargs: object) -> asyncio.subprocess.Process:
        spawn_keywords.update(kwargs)
        return await real_spawn(*arguments, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("app.media.ffmpeg.asyncio.create_subprocess_exec", recording_spawn)
    monkeypatch.setattr(
        "app.media.ffmpeg.apply_process_resource_limits",
        lambda process_id, memory: applied.append((process_id, memory)) or True,
    )
    processor = FFmpegProcessor()
    process = await processor._spawn([sys.executable, "-c", "print('bounded')"])
    await process.communicate()
    assert "preexec_fn" not in spawn_keywords
    assert applied and applied[0][1] == processor.memory_limit_bytes


def test_validate_final_timeline_and_track_errors() -> None:
    missing_video = MediaProbe(10, 1, None, "mp4", ({"type": "audio", "codec": "aac"},))
    synchronized = MediaProbe(
        10,
        1,
        None,
        "mp4",
        (
            {"type": "video", "codec": "h264", "duration": 10.0, "startTime": -0.1},
            {"type": "audio", "codec": "aac", "duration": 10.0, "startTime": 0.0},
        ),
    )
    shifted = MediaProbe(
        3600,
        1,
        None,
        "mp4",
        (
            {"type": "video", "codec": "h264", "duration": 3600.0, "startTime": 0.0},
            {"type": "audio", "codec": "aac", "duration": 3590.0, "startTime": 3.0},
        ),
    )
    assert synchronized.streams[0]["startTime"] == -0.1
    assert missing_video.streams[0]["type"] == "audio"
    assert shifted.duration == 3600


async def test_validate_final_uses_probe_and_enforces_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    processor = FFmpegProcessor()
    probe = MediaProbe(
        3600,
        1,
        None,
        "mp4",
        (
            {"type": "video", "codec": "h264", "duration": 3600.0, "startTime": 0.0},
            {"type": "audio", "codec": "aac", "duration": 3595.0, "startTime": 3.0},
        ),
    )

    async def fake_probe(_path: Path) -> MediaProbe:
        return probe

    monkeypatch.setattr(processor, "probe", fake_probe)
    with pytest.raises(MediaValidationError) as caught:
        await processor.validate_final(
            Path("unused"),
            expected_duration=3600,
            require_video=True,
            require_audio=True,
        )
    assert caught.value.code == "MEDIA_SYNC_MISMATCH"

    no_timeline = MediaProbe(
        10,
        1,
        None,
        "mp4",
        ({"type": "video", "codec": "h264"}, {"type": "audio", "codec": "aac"}),
    )

    async def missing_timeline(_path: Path) -> MediaProbe:
        return no_timeline

    monkeypatch.setattr(processor, "probe", missing_timeline)
    with pytest.raises(MediaValidationError) as timeline:
        await processor.validate_final(
            Path("unused"), expected_duration=10, require_video=True, require_audio=True
        )
    assert timeline.value.code == "MEDIA_TIMELINE_UNAVAILABLE"


async def test_cancellation_type_is_preserved() -> None:
    checkpoint = Checkpoint(DownloadCanceled())
    with pytest.raises(DownloadCanceled):
        await checkpoint.checkpoint()
