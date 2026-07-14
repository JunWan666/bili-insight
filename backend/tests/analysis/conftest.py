from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.analysis import ProcessRunner


@pytest.fixture(scope="session")
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(scope="session")
def sample_media(tmp_path_factory: pytest.TempPathFactory, ffmpeg_available: bool) -> Path:
    if not ffmpeg_available:
        pytest.skip("FFmpeg and FFprobe are required for media analysis tests")
    output_directory = tmp_path_factory.mktemp("analysis-media")
    output_path = output_directory / "short-analysis-sample.mp4"
    audio_source = "aevalsrc=if(lt(t\\,0.5)+gt(t\\,2.5)\\,0\\,0.2*sin(2*PI*440*t)):s=48000:d=3"
    ProcessRunner(default_timeout_seconds=30).run(
        "ffmpeg",
        [
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x180:r=25:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=25:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=320x180:r=25:d=1",
            "-f",
            "lavfi",
            "-i",
            audio_source,
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "3:a:0",
            "-c:v",
            "mpeg4",
            "-q:v",
            "3",
            "-g",
            "25",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-metadata",
            "title=analysis-test",
            "-movflags",
            "+faststart",
            "-y",
            output_path,
        ],
        timeout_seconds=30,
    )
    assert output_path.is_file()
    assert output_path.stat().st_size > 0
    return output_path
