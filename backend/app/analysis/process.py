from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.analysis.errors import AnalysisError, AnalysisErrorCode, AnalysisFailure
from app.core.process_limits import (
    DEFAULT_PROCESS_MAX_THREADS,
    DEFAULT_PROCESS_MEMORY_LIMIT_BYTES,
    MINIMUM_PROCESS_MEMORY_LIMIT_BYTES,
    ChildProcessSlot,
    acquire_child_process_slot,
    apply_process_resource_limits,
    bounded_process_environment,
    process_resident_memory_bytes,
)

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)(?<![a-z0-9_])[a-z]:[\\/][^\r\n]*")
_POSIX_ABSOLUTE_PATH = re.compile(r"(?<![\w:])/(?:[^/\s]+/)*[^/\s]+")


@dataclass(frozen=True, slots=True)
class ProcessResult:
    executable: str
    return_code: int
    stdout: bytes
    stderr: bytes
    elapsed_seconds: float

    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")


class _BoundedCapture:
    def __init__(self, limit_bytes: int) -> None:
        self._limit_bytes = limit_bytes
        self._chunks: list[bytes] = []
        self._size = 0
        self._lock = threading.Lock()
        self.exceeded = False

    def read_stream(self, stream: BinaryIO) -> None:
        while True:
            chunk = stream.read(65_536)
            if not chunk:
                return
            with self._lock:
                remaining = self._limit_bytes - self._size
                if remaining > 0:
                    kept = chunk[:remaining]
                    self._chunks.append(kept)
                    self._size += len(kept)
                if len(chunk) > remaining:
                    self.exceeded = True

    def value(self) -> bytes:
        with self._lock:
            return b"".join(self._chunks)


class ProcessRunner:
    """Runs trusted binaries with fixed argument arrays and bounded captured output."""

    def __init__(
        self,
        *,
        default_timeout_seconds: float = 600.0,
        stdout_limit_bytes: int = 64 * 1024 * 1024,
        stderr_limit_bytes: int = 16 * 1024 * 1024,
        max_threads: int = DEFAULT_PROCESS_MAX_THREADS,
        memory_limit_bytes: int = DEFAULT_PROCESS_MEMORY_LIMIT_BYTES,
    ) -> None:
        if not 0 < default_timeout_seconds <= 86_400:
            raise invalid_runner_configuration("进程超时时间必须在 0 到 86400 秒之间")
        if stdout_limit_bytes < 65_536 or stderr_limit_bytes < 65_536:
            raise invalid_runner_configuration("进程输出上限不能小于 65536 字节")
        if not 1 <= max_threads <= 32:
            raise invalid_runner_configuration("进程线程上限必须在 1 到 32 之间")
        if memory_limit_bytes < MINIMUM_PROCESS_MEMORY_LIMIT_BYTES:
            raise invalid_runner_configuration("进程内存上限不能小于 64 MiB")
        self.default_timeout_seconds = default_timeout_seconds
        self.stdout_limit_bytes = stdout_limit_bytes
        self.stderr_limit_bytes = stderr_limit_bytes
        self.max_threads = max_threads
        self.memory_limit_bytes = memory_limit_bytes

    @staticmethod
    def resolve_executable(executable: str | Path) -> Path:
        raw = os.fspath(executable)
        candidate = shutil.which(raw)
        if candidate is None:
            path = Path(raw).expanduser()
            if path.is_absolute() and path.is_file():
                candidate = os.fspath(path)
        if candidate is None:
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.DEPENDENCY_UNAVAILABLE,
                    message=f"本地分析组件 {Path(raw).name} 未安装或不可执行",
                    action=f"安装 {Path(raw).name} 并确认其已加入 PATH",
                )
            )
        resolved = Path(candidate).resolve()
        if not resolved.is_file():
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.DEPENDENCY_UNAVAILABLE,
                    message=f"本地分析组件 {resolved.name} 不可用",
                    action="检查组件安装路径与执行权限",
                )
            )
        return resolved

    def run(
        self,
        executable: str | Path,
        arguments: Sequence[str | os.PathLike[str]],
        *,
        timeout_seconds: float | None = None,
        cancellation_event: threading.Event | None = None,
        check: bool = True,
    ) -> ProcessResult:
        if cancellation_event is not None and cancellation_event.is_set():
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.CANCELED,
                    message="分析任务已取消",
                    action="可按需重新创建分析任务",
                )
            )
        resolved = self.resolve_executable(executable)
        args = self._validate_arguments(arguments)
        command = [os.fspath(resolved), *args]
        timeout = self.default_timeout_seconds if timeout_seconds is None else timeout_seconds
        if not 0 < timeout <= 86_400:
            raise invalid_runner_configuration("进程超时时间必须在 0 到 86400 秒之间")

        stdout_capture = _BoundedCapture(self.stdout_limit_bytes)
        stderr_capture = _BoundedCapture(self.stderr_limit_bytes)
        creation_flags = 0
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        started = time.monotonic()
        logger.debug("Starting local analysis process", extra={"executable": resolved.name})
        try:
            slot = acquire_child_process_slot(
                cancellation_requested=(
                    cancellation_event.is_set if cancellation_event is not None else None
                )
            )
        except InterruptedError as exc:
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.CANCELED,
                    message="分析任务已取消",
                    action="可按需重新创建分析任务",
                )
            ) from exc
        try:
            process: subprocess.Popen[bytes] = subprocess.Popen(  # noqa: S603
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=creation_flags,
                start_new_session=os.name != "nt",
                env=bounded_process_environment(self.max_threads),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            slot.release()
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.PROCESS_FAILED,
                    message=f"无法启动本地分析组件 {resolved.name}",
                    action="检查组件安装与系统资源后重试",
                    diagnostic=f"spawn failed: {type(exc).__name__}",
                )
            ) from exc
        apply_process_resource_limits(process.pid, self.memory_limit_bytes)
        threading.Thread(
            target=self._release_slot_when_done,
            args=(process, slot),
            name=f"{resolved.name}-resource-slot",
            daemon=True,
        ).start()

        if process.stdout is None or process.stderr is None:
            self._stop_process(process)
            raise AnalysisError(
                AnalysisFailure(
                    code=AnalysisErrorCode.PROCESS_FAILED,
                    message="本地分析进程未能建立输出通道",
                    action="检查运行环境后重试",
                )
            )

        readers = (
            threading.Thread(
                target=stdout_capture.read_stream,
                args=(process.stdout,),
                name=f"{resolved.name}-stdout",
                daemon=True,
            ),
            threading.Thread(
                target=stderr_capture.read_stream,
                args=(process.stderr,),
                name=f"{resolved.name}-stderr",
                daemon=True,
            ),
        )
        for reader in readers:
            reader.start()

        failure_code: AnalysisErrorCode | None = None
        while process.poll() is None:
            if cancellation_event is not None and cancellation_event.is_set():
                failure_code = AnalysisErrorCode.CANCELED
                break
            if stdout_capture.exceeded or stderr_capture.exceeded:
                failure_code = AnalysisErrorCode.PROCESS_OUTPUT_LIMIT
                break
            resident_memory = process_resident_memory_bytes(process.pid)
            if resident_memory is not None and resident_memory > self.memory_limit_bytes:
                failure_code = AnalysisErrorCode.PROCESS_RESOURCE_LIMIT
                break
            if time.monotonic() - started > timeout:
                failure_code = AnalysisErrorCode.PROCESS_TIMEOUT
                break
            time.sleep(0.05)

        if failure_code is not None:
            self._stop_process(process)
        for reader in readers:
            reader.join(timeout=5.0)
        if any(reader.is_alive() for reader in readers):
            self._stop_process(process)
            for reader in readers:
                reader.join(timeout=1.0)
        if failure_code is None and (stdout_capture.exceeded or stderr_capture.exceeded):
            failure_code = AnalysisErrorCode.PROCESS_OUTPUT_LIMIT

        elapsed = time.monotonic() - started
        stdout = stdout_capture.value()
        stderr = stderr_capture.value()
        return_code = process.returncode if process.returncode is not None else -1
        if failure_code is not None:
            raise self._process_failure(
                failure_code,
                resolved.name,
                return_code,
                stderr,
                args,
            )
        if check and return_code != 0:
            failure = AnalysisErrorCode.PROCESS_FAILED
            if b"MemoryError" in stderr or b"bad_alloc" in stderr:
                failure = AnalysisErrorCode.PROCESS_RESOURCE_LIMIT
            raise self._process_failure(
                failure,
                resolved.name,
                return_code,
                stderr,
                args,
            )
        logger.debug(
            "Local analysis process completed",
            extra={"executable": resolved.name, "elapsed_seconds": round(elapsed, 3)},
        )
        return ProcessResult(
            executable=resolved.name,
            return_code=return_code,
            stdout=stdout,
            stderr=stderr,
            elapsed_seconds=elapsed,
        )

    @staticmethod
    def _validate_arguments(
        arguments: Sequence[str | os.PathLike[str]],
    ) -> list[str]:
        if len(arguments) > 4096:
            raise invalid_runner_configuration("本地分析参数数量超过安全上限")
        validated: list[str] = []
        for argument in arguments:
            value = os.fspath(argument)
            if "\x00" in value:
                raise invalid_runner_configuration("本地分析参数包含非法空字符")
            if len(value) > 32_768:
                raise invalid_runner_configuration("本地分析参数长度超过安全上限")
            validated.append(value)
        return validated

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.kill(-process.pid, signal.SIGTERM)
            process.wait(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.kill(-process.pid, signal.Signals(9))
                process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                logger.warning("Unable to terminate local analysis process")

    @staticmethod
    def _release_slot_when_done(
        process: subprocess.Popen[bytes],
        slot: ChildProcessSlot,
    ) -> None:
        try:
            process.wait()
        finally:
            slot.release()

    @staticmethod
    def _process_failure(
        code: AnalysisErrorCode,
        executable: str,
        return_code: int,
        stderr: bytes,
        arguments: Sequence[str],
    ) -> AnalysisError:
        messages = {
            AnalysisErrorCode.CANCELED: ("分析任务已取消", "可按需重新创建分析任务"),
            AnalysisErrorCode.PROCESS_TIMEOUT: (
                "本地媒体分析超过时间限制",
                "缩短分析时长、调整模型配置或提高超时限制后重试",
            ),
            AnalysisErrorCode.PROCESS_OUTPUT_LIMIT: (
                "本地媒体分析输出超过安全上限",
                "检查媒体文件是否损坏，或提高受控输出限制后重试",
            ),
            AnalysisErrorCode.PROCESS_RESOURCE_LIMIT: (
                "本地媒体分析超过内存资源上限",
                "降低模型或采样规格后重试",
            ),
            AnalysisErrorCode.PROCESS_FAILED: (
                "本地媒体处理失败",
                "确认媒体文件可读、FFmpeg 可用后重试",
            ),
        }
        message, action = messages[code]
        diagnostic = ProcessRunner._safe_diagnostic(stderr, arguments, executable, return_code)
        return AnalysisError(
            AnalysisFailure(
                code=code,
                message=message,
                action=action,
                diagnostic=diagnostic,
            )
        )

    @staticmethod
    def _safe_diagnostic(
        stderr: bytes,
        arguments: Sequence[str],
        executable: str,
        return_code: int,
    ) -> str:
        text = stderr[-2048:].decode("utf-8", errors="replace")
        for argument in arguments:
            path = Path(argument).expanduser()
            try:
                is_path = path.is_absolute() or path.exists()
            except OSError:
                is_path = False
            if is_path:
                text = text.replace(argument, f"<{path.name or 'path'}>")
        text = _URL_PATTERN.sub("<url>", text)
        text = _WINDOWS_ABSOLUTE_PATH.sub("<path>", text)
        text = _POSIX_ABSOLUTE_PATH.sub("<path>", text)
        text = " ".join(text.split())[-1000:]
        return (
            f"{executable} exited {return_code}: {text}"
            if text
            else (f"{executable} exited {return_code}")
        )


def invalid_runner_configuration(message: str) -> AnalysisError:
    return AnalysisError(
        AnalysisFailure(
            code=AnalysisErrorCode.INVALID_CONFIGURATION,
            message=message,
            action="检查本地分析进程配置后重试",
        )
    )
