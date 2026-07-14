from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import signal
import subprocess
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.process_limits import (
    DEFAULT_PROCESS_MEMORY_LIMIT_BYTES,
    MINIMUM_PROCESS_MEMORY_LIMIT_BYTES,
    ChildProcessSlot,
    acquire_child_process_slot_async,
    apply_process_resource_limits,
    bounded_process_environment,
    process_resident_memory_bytes,
)
from app.media.download import DownloadCanceled, DownloadCheckpoint, DownloadPaused


class FFmpegError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class MediaValidationError(FFmpegError):
    """Raised when FFprobe proves that a media input or output is invalid."""


@dataclass(frozen=True, slots=True)
class MediaProbe:
    duration: float
    size: int
    bitrate: int | None
    format_name: str
    streams: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "duration": self.duration,
            "size": self.size,
            "bitrate": self.bitrate,
            "formatName": self.format_name,
            "streams": list(self.streams),
        }


ProcessProgress = Callable[[float], Awaitable[None]]


class FFmpegProcessor:
    """Safe FFmpeg/FFprobe process adapter using argument arrays exclusively."""

    def __init__(
        self,
        *,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        process_timeout_seconds: float = 14_400.0,
        probe_timeout_seconds: float = 30.0,
        max_threads: int = 4,
        memory_limit_bytes: int = DEFAULT_PROCESS_MEMORY_LIMIT_BYTES,
    ) -> None:
        if not 1 <= max_threads <= 32:
            raise ValueError("FFmpeg thread limit is outside the safe range")
        if memory_limit_bytes < MINIMUM_PROCESS_MEMORY_LIMIT_BYTES:
            raise ValueError("FFmpeg memory limit is below the safe minimum")
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.process_timeout_seconds = process_timeout_seconds
        self.probe_timeout_seconds = probe_timeout_seconds
        self.max_threads = max_threads
        self.memory_limit_bytes = memory_limit_bytes
        self._slot_tasks: set[asyncio.Task[None]] = set()

    def check_available(self) -> None:
        if shutil.which(self.ffmpeg) is None or shutil.which(self.ffprobe) is None:
            raise FFmpegError(
                "MEDIA_TOOL_UNAVAILABLE",
                "FFmpeg 或 FFprobe 未安装，无法处理媒体文件",
            )

    async def process(
        self,
        *,
        video_path: Path | None,
        audio_path: Path | None,
        output_path: Path,
        container: str,
        processing_mode: str,
        expected_duration: float,
        checkpoint: DownloadCheckpoint,
        progress: ProcessProgress,
    ) -> MediaProbe:
        self.check_available()
        if video_path is None and audio_path is None:
            raise FFmpegError("MEDIA_INPUT_MISSING", "没有可处理的媒体输入")
        await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(output_path.unlink, missing_ok=True)

        arguments = self._build_arguments(
            video_path=video_path,
            audio_path=audio_path,
            output_path=output_path,
            container=container,
            processing_mode=processing_mode,
        )
        await self._run_ffmpeg(
            arguments,
            expected_duration=expected_duration,
            checkpoint=checkpoint,
            progress=progress,
        )
        return await self.validate_final(
            output_path,
            expected_duration=expected_duration,
            require_video=video_path is not None and container not in {"m4a", "mp3", "flac"},
            require_audio=audio_path is not None,
        )

    async def validate_input(self, path: Path, *, expected_kind: str) -> MediaProbe:
        probe = await self.probe(path)
        if not any(stream.get("type") == expected_kind for stream in probe.streams):
            label = "视频" if expected_kind == "video" else "音频"
            raise MediaValidationError(
                "MEDIA_STREAM_MISSING",
                f"下载的媒体文件未包含预期的{label}轨道",
            )
        return probe

    async def validate_final(
        self,
        path: Path,
        *,
        expected_duration: float,
        require_video: bool,
        require_audio: bool,
    ) -> MediaProbe:
        probe = await self.probe(path)
        stream_types = {stream.get("type") for stream in probe.streams}
        if require_video and "video" not in stream_types:
            raise MediaValidationError("VIDEO_TRACK_MISSING", "最终文件缺少视频轨道")
        if require_audio and "audio" not in stream_types:
            raise MediaValidationError("AUDIO_TRACK_MISSING", "最终文件缺少音频轨道")

        if expected_duration > 0:
            tolerance = max(2.0, min(10.0, expected_duration * 0.01))
            if abs(probe.duration - expected_duration) > tolerance:
                raise MediaValidationError(
                    "MEDIA_DURATION_MISMATCH",
                    "最终文件时长与视频分 P 时长不一致",
                )

        timelines: dict[str, tuple[float, float]] = {}
        for stream in probe.streams:
            kind = stream.get("type")
            duration = self._float_value(stream.get("duration"))
            if kind not in {"video", "audio"} or duration is None or kind in timelines:
                continue
            start = self._signed_float_value(stream.get("startTime")) or 0.0
            timelines[str(kind)] = (start, start + duration)
        if require_video and require_audio and not {"video", "audio"} <= timelines.keys():
            raise MediaValidationError(
                "MEDIA_TIMELINE_UNAVAILABLE",
                "最终文件缺少可校验的音视频时间轴",
            )
        if require_video and require_audio:
            video_start, video_end = timelines["video"]
            audio_start, audio_end = timelines["audio"]
            sync_tolerance = max(0.75, min(2.0, expected_duration * 0.005))
            if (
                abs(video_start - audio_start) > sync_tolerance
                or abs(video_end - audio_end) > sync_tolerance
            ):
                raise MediaValidationError(
                    "MEDIA_SYNC_MISMATCH",
                    "最终文件的音视频时长差异过大",
                )
        return probe

    async def probe(self, path: Path) -> MediaProbe:
        self.check_available()
        initial_size = await asyncio.to_thread(self._validated_file_size, path)
        if initial_size is None:
            raise MediaValidationError("MEDIA_FILE_EMPTY", "媒体文件不存在或内容为空")
        arguments = [
            self.ffprobe,
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-show_entries",
            (
                "format=format_name,duration,size,bit_rate:"
                "stream=index,codec_type,codec_name,width,height,sample_rate,channels,"
                "start_time,duration,duration_ts,time_base:stream_tags=DURATION"
            ),
            "-of",
            "json",
            str(path),
        ]
        process = await self._spawn(arguments)
        try:
            stdout, exceeded = await asyncio.wait_for(
                self._communicate_probe(process),
                timeout=self.probe_timeout_seconds,
            )
        except TimeoutError as exc:
            await self._terminate(process)
            raise MediaValidationError("FFPROBE_TIMEOUT", "媒体完整性校验超时") from exc
        except FFmpegError as exc:
            await self._terminate(process)
            raise MediaValidationError(
                "FFPROBE_RESOURCE_LIMIT",
                "媒体完整性校验超过内存资源上限",
            ) from exc
        except asyncio.CancelledError:
            await self._terminate(process)
            raise
        if process.returncode != 0 or exceeded:
            raise MediaValidationError("FFPROBE_FAILED", "媒体文件无法通过完整性校验")
        try:
            payload = json.loads(stdout)
            actual_size = await asyncio.to_thread(self._validated_file_size, path)
            if actual_size is None:
                raise ValueError("media disappeared during validation")
            return self._parse_probe(payload, actual_size=actual_size)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise MediaValidationError("FFPROBE_INVALID", "媒体校验结果格式无效") from exc

    def _build_arguments(
        self,
        *,
        video_path: Path | None,
        audio_path: Path | None,
        output_path: Path,
        container: str,
        processing_mode: str,
    ) -> list[str]:
        if container not in {"mp4", "mkv", "m4a", "mp3", "flac"}:
            raise FFmpegError("CONTAINER_UNSUPPORTED", "不支持所选输出容器")
        if processing_mode not in {"copy", "transcode"}:
            raise FFmpegError("PROCESSING_MODE_UNSUPPORTED", "不支持所选处理方式")
        if container in {"m4a", "mp3", "flac"} and audio_path is None:
            raise FFmpegError("AUDIO_INPUT_MISSING", "仅音频输出需要选择音频流")

        arguments = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-protocol_whitelist",
            "file,pipe",
            "-threads",
            str(self.max_threads),
        ]
        if video_path is not None and container not in {"m4a", "mp3", "flac"}:
            arguments.extend(["-i", str(video_path)])
        if audio_path is not None:
            arguments.extend(["-i", str(audio_path)])
        arguments.extend(["-map_metadata", "-1"])

        if container in {"m4a", "mp3", "flac"}:
            arguments.extend(["-vn", "-map", "0:a:0"])
        elif video_path is not None:
            arguments.extend(["-map", "0:v:0"])
            if audio_path is not None:
                audio_input_index = 1 if video_path is not None else 0
                arguments.extend(["-map", f"{audio_input_index}:a:0"])

        if container == "mp3":
            arguments.extend(["-c:a", "libmp3lame", "-q:a", "2"])
        elif container == "flac":
            arguments.extend(["-c:a", "flac", "-compression_level", "5"])
        elif processing_mode == "copy":
            arguments.extend(["-c", "copy"])
        else:
            if video_path is not None and container not in {"m4a", "mp3", "flac"}:
                arguments.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "20"])
            if audio_path is not None:
                arguments.extend(["-c:a", "aac", "-b:a", "192k"])

        if container in {"mp4", "m4a"}:
            arguments.extend(["-movflags", "+faststart"])
        arguments.extend(
            [
                "-threads",
                str(self.max_threads),
                "-progress",
                "pipe:1",
                "-nostats",
                "-f",
                self._muxer(container),
                str(output_path),
            ]
        )
        return arguments

    async def _run_ffmpeg(
        self,
        arguments: Sequence[str],
        *,
        expected_duration: float,
        checkpoint: DownloadCheckpoint,
        progress: ProcessProgress,
    ) -> None:
        process = await self._spawn(arguments)
        stderr_task = asyncio.create_task(self._read_bounded(process.stderr, 64 * 1024))
        started = asyncio.get_running_loop().time()
        try:
            while True:
                await checkpoint.checkpoint()
                if asyncio.get_running_loop().time() - started > self.process_timeout_seconds:
                    raise FFmpegError("FFMPEG_TIMEOUT", "音视频处理超时")
                resident_memory = process_resident_memory_bytes(process.pid)
                if resident_memory is not None and resident_memory > self.memory_limit_bytes:
                    raise FFmpegError("FFMPEG_RESOURCE_LIMIT", "音视频处理超过内存资源上限")
                stdout = process.stdout
                if stdout is None:
                    raise FFmpegError("FFMPEG_FAILED", "音视频处理程序没有进度输出")
                try:
                    line = await asyncio.wait_for(stdout.readline(), timeout=0.5)
                except TimeoutError:
                    if process.returncode is not None:
                        break
                    continue
                if not line:
                    break
                key, separator, raw_value = line.decode("utf-8", errors="replace").partition("=")
                if separator and key in {"out_time_us", "out_time_ms"}:
                    try:
                        microseconds = int(raw_value.strip())
                    except ValueError:
                        continue
                    if expected_duration > 0:
                        await progress(min(1.0, microseconds / 1_000_000 / expected_duration))
            return_code = await process.wait()
            await stderr_task
            if return_code != 0:
                raise FFmpegError("FFMPEG_FAILED", "音视频处理失败")
            await progress(1.0)
        except (DownloadCanceled, DownloadPaused, FFmpegError, asyncio.CancelledError):
            await self._terminate(process)
            if not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
            raise
        except Exception:
            await self._terminate(process)
            if not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
            raise

    async def _spawn(self, arguments: Sequence[str]) -> asyncio.subprocess.Process:
        creation_flags = (
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            if os.name == "nt"
            else 0
        )
        slot = await acquire_child_process_slot_async()
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creation_flags,
                start_new_session=os.name != "nt",
                env=bounded_process_environment(self.max_threads),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            slot.release()
            raise FFmpegError("MEDIA_PROCESS_START_FAILED", "无法启动媒体处理程序") from exc
        apply_process_resource_limits(process.pid, self.memory_limit_bytes)
        release_task = asyncio.create_task(
            self._release_slot_when_done(process, slot),
            name=f"media-process-{process.pid}-resource-slot",
        )
        self._slot_tasks.add(release_task)
        release_task.add_done_callback(self._slot_tasks.discard)
        return process

    @staticmethod
    async def _release_slot_when_done(
        process: asyncio.subprocess.Process,
        slot: ChildProcessSlot,
    ) -> None:
        try:
            await process.wait()
        finally:
            slot.release()

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if os.name == "nt":
            process.terminate()
        else:
            try:
                os.kill(-process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except TimeoutError:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.kill(-process.pid, signal.Signals(9))
                except ProcessLookupError:
                    return
            await process.wait()

    @staticmethod
    async def _read_bounded(stream: asyncio.StreamReader | None, maximum: int) -> bytes:
        if stream is None:
            return b""
        captured = bytearray()
        while True:
            chunk = await stream.read(8_192)
            if not chunk:
                break
            captured.extend(chunk)
            if len(captured) > maximum:
                del captured[: len(captured) - maximum]
        return bytes(captured)

    async def _communicate_probe(
        self,
        process: asyncio.subprocess.Process,
    ) -> tuple[bytes, bool]:
        stdout_task = asyncio.create_task(self._read_with_limit(process.stdout, 2 * 1024 * 1024))
        stderr_task = asyncio.create_task(self._read_bounded(process.stderr, 64 * 1024))
        wait_task = asyncio.create_task(process.wait())
        try:
            while not wait_task.done():
                await asyncio.wait({wait_task}, timeout=0.1)
                resident_memory = process_resident_memory_bytes(process.pid)
                if resident_memory is not None and resident_memory > self.memory_limit_bytes:
                    raise FFmpegError(
                        "FFPROBE_RESOURCE_LIMIT",
                        "媒体完整性校验超过内存资源上限",
                    )
            await wait_task
            stdout, exceeded = await stdout_task
            await stderr_task
            return stdout, exceeded
        finally:
            pending = [task for task in (stdout_task, stderr_task, wait_task) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    @staticmethod
    async def _read_with_limit(
        stream: asyncio.StreamReader | None,
        maximum: int,
    ) -> tuple[bytes, bool]:
        if stream is None:
            return b"", False
        captured = bytearray()
        exceeded = False
        while True:
            chunk = await stream.read(8_192)
            if not chunk:
                break
            available = max(0, maximum - len(captured))
            captured.extend(chunk[:available])
            if len(chunk) > available:
                exceeded = True
        return bytes(captured), exceeded

    @staticmethod
    def _parse_probe(payload: object, *, actual_size: int) -> MediaProbe:
        if not isinstance(payload, Mapping):
            raise ValueError("probe payload is not an object")
        raw_format = payload.get("format")
        raw_streams = payload.get("streams")
        if not isinstance(raw_format, Mapping) or not isinstance(raw_streams, list):
            raise ValueError("probe payload is incomplete")
        duration = FFmpegProcessor._float_value(raw_format.get("duration"))
        if duration is None or duration <= 0:
            stream_durations = [
                value
                for item in raw_streams
                if isinstance(item, Mapping)
                and (value := FFmpegProcessor._float_value(item.get("duration"))) is not None
            ]
            duration = max(stream_durations, default=0.0)
        if duration <= 0:
            raise ValueError("media duration is unavailable")

        streams: list[dict[str, object]] = []
        for item in raw_streams:
            if not isinstance(item, Mapping):
                continue
            kind = item.get("codec_type")
            codec = item.get("codec_name")
            if kind not in {"video", "audio", "subtitle"} or not isinstance(codec, str):
                continue
            entry: dict[str, object] = {"type": kind, "codec": codec}
            for key in ("width", "height", "channels"):
                value = FFmpegProcessor._int_value(item.get(key))
                if value is not None:
                    entry[key] = value
            sample_rate = FFmpegProcessor._int_value(item.get("sample_rate"))
            if sample_rate is not None:
                entry["sampleRate"] = sample_rate
            stream_duration = FFmpegProcessor._float_value(item.get("duration"))
            if stream_duration is None:
                stream_duration = FFmpegProcessor._duration_from_ticks(
                    item.get("duration_ts"),
                    item.get("time_base"),
                )
            if stream_duration is None:
                tags = item.get("tags")
                if isinstance(tags, Mapping):
                    stream_duration = FFmpegProcessor._timestamp_duration(tags.get("DURATION"))
            if stream_duration is not None:
                entry["duration"] = stream_duration
            start_time = FFmpegProcessor._signed_float_value(item.get("start_time"))
            if start_time is not None:
                entry["startTime"] = start_time
            streams.append(entry)
        if not streams:
            raise ValueError("probe did not return playable streams")
        format_name = raw_format.get("format_name")
        bitrate = FFmpegProcessor._int_value(raw_format.get("bit_rate"))
        return MediaProbe(
            duration=duration,
            size=actual_size,
            bitrate=bitrate,
            format_name=format_name if isinstance(format_name, str) else "unknown",
            streams=tuple(streams),
        )

    @staticmethod
    def _float_value(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) and parsed >= 0 else None

    @staticmethod
    def _int_value(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _signed_float_value(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    @classmethod
    def _duration_from_ticks(cls, ticks: object, time_base: object) -> float | None:
        parsed_ticks = cls._int_value(ticks)
        if parsed_ticks is None or not isinstance(time_base, str):
            return None
        numerator, separator, denominator = time_base.partition("/")
        if not separator:
            return None
        parsed_numerator = cls._int_value(numerator)
        parsed_denominator = cls._int_value(denominator)
        if parsed_numerator is None or not parsed_denominator:
            return None
        return parsed_ticks * parsed_numerator / parsed_denominator

    @classmethod
    def _timestamp_duration(cls, value: object) -> float | None:
        if not isinstance(value, str):
            return None
        hours, separator, remainder = value.partition(":")
        minutes, second_separator, seconds = remainder.partition(":")
        if not separator or not second_separator:
            return None
        parsed_hours = cls._int_value(hours)
        parsed_minutes = cls._int_value(minutes)
        parsed_seconds = cls._float_value(seconds)
        if (
            parsed_hours is None
            or parsed_minutes is None
            or parsed_minutes >= 60
            or parsed_seconds is None
            or parsed_seconds >= 60
        ):
            return None
        return parsed_hours * 3600 + parsed_minutes * 60 + parsed_seconds

    @staticmethod
    def _muxer(container: str) -> str:
        return {
            "mp4": "mp4",
            "mkv": "matroska",
            "m4a": "ipod",
            "mp3": "mp3",
            "flac": "flac",
        }[container]

    @staticmethod
    def _validated_file_size(path: Path) -> int | None:
        if not path.is_file() or path.is_symlink():
            return None
        size = path.stat().st_size
        return size if size > 0 else None
