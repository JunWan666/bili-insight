from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass

DEFAULT_PROCESS_MAX_THREADS = 4
DEFAULT_PROCESS_MEMORY_LIMIT_BYTES = 4 * 1024**3
DEFAULT_CHILD_PROCESS_CONCURRENCY = 3
MINIMUM_PROCESS_MEMORY_LIMIT_BYTES = 64 * 1024**2

_NATIVE_THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
)
_PROCESS_SLOTS = threading.BoundedSemaphore(DEFAULT_CHILD_PROCESS_CONCURRENCY)


@dataclass(slots=True)
class ChildProcessSlot:
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        _PROCESS_SLOTS.release()


@dataclass(frozen=True, slots=True)
class BoundedChildProcessResult:
    return_code: int
    stdout: bytes
    stderr: bytes
    output_exceeded: bool


def acquire_child_process_slot(
    *,
    cancellation_requested: Callable[[], bool] | None = None,
) -> ChildProcessSlot:
    while True:
        if cancellation_requested is not None and cancellation_requested():
            raise InterruptedError("child process launch was canceled")
        if _PROCESS_SLOTS.acquire(timeout=0.1):
            return ChildProcessSlot()


async def acquire_child_process_slot_async() -> ChildProcessSlot:
    while True:
        attempt = asyncio.create_task(asyncio.to_thread(_PROCESS_SLOTS.acquire, True, 0.1))
        try:
            acquired = await asyncio.shield(attempt)
        except asyncio.CancelledError:
            acquired = await attempt
            if acquired:
                _PROCESS_SLOTS.release()
            raise
        if acquired:
            return ChildProcessSlot()


async def run_bounded_child_process(
    executable: str,
    *arguments: str,
    timeout_seconds: float,
    output_limit_bytes: int = 64 * 1024,
    max_threads: int = DEFAULT_PROCESS_MAX_THREADS,
    memory_limit_bytes: int = DEFAULT_PROCESS_MEMORY_LIMIT_BYTES,
) -> BoundedChildProcessResult | None:
    """Run a trusted executable with the global child limits and bounded output."""

    if not 0 < timeout_seconds <= 300:
        raise ValueError("child process timeout is outside the safe range")
    if not 1_024 <= output_limit_bytes <= 16 * 1024 * 1024:
        raise ValueError("child process output limit is outside the safe range")
    if memory_limit_bytes < MINIMUM_PROCESS_MEMORY_LIMIT_BYTES:
        raise ValueError("child process memory limit is below the safe minimum")
    environment = bounded_process_environment(max_threads)
    creation_flags = (
        subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    )
    slot = await acquire_child_process_slot_async()
    process: asyncio.subprocess.Process | None = None
    tasks: list[asyncio.Task[object]] = []
    try:
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                *arguments,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creation_flags,
                start_new_session=os.name != "nt",
                env=environment,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        apply_process_resource_limits(process.pid, memory_limit_bytes)
        stdout_task = asyncio.create_task(
            _read_bounded_process_stream(process.stdout, output_limit_bytes)
        )
        stderr_task = asyncio.create_task(
            _read_bounded_process_stream(process.stderr, output_limit_bytes)
        )
        wait_task = asyncio.create_task(process.wait())
        tasks.extend((stdout_task, stderr_task, wait_task))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while not wait_task.done():
            await asyncio.wait({wait_task}, timeout=0.05)
            resident_memory = process_resident_memory_bytes(process.pid)
            if resident_memory is not None and resident_memory > memory_limit_bytes:
                await _terminate_async_process(process)
                return None
            if loop.time() >= deadline:
                await _terminate_async_process(process)
                return None
        return_code = await wait_task
        stdout, stdout_exceeded = await stdout_task
        stderr, stderr_exceeded = await stderr_task
        return BoundedChildProcessResult(
            return_code=return_code,
            stdout=stdout,
            stderr=stderr,
            output_exceeded=stdout_exceeded or stderr_exceeded,
        )
    finally:
        if process is not None and process.returncode is None:
            await _terminate_async_process(process)
        pending = [task for task in tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        slot.release()


async def _read_bounded_process_stream(
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
            return bytes(captured), exceeded
        available = max(0, maximum - len(captured))
        captured.extend(chunk[:available])
        exceeded = exceeded or len(chunk) > available


async def _terminate_async_process(process: asyncio.subprocess.Process) -> None:
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
        await asyncio.wait_for(process.wait(), timeout=1.0)
    except TimeoutError:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.kill(-process.pid, signal.Signals(9))
            except ProcessLookupError:
                return
        await process.wait()


def bounded_process_environment(
    max_threads: int = DEFAULT_PROCESS_MAX_THREADS,
    *,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    if not 1 <= max_threads <= 32:
        raise ValueError("native process thread limit is outside the safe range")
    environment = dict(os.environ if base is None else base)
    for name in _NATIVE_THREAD_VARIABLES:
        environment[name] = str(max_threads)
    return environment


def apply_current_process_thread_limit(
    max_threads: int = DEFAULT_PROCESS_MAX_THREADS,
) -> None:
    environment = bounded_process_environment(max_threads)
    for name in _NATIVE_THREAD_VARIABLES:
        os.environ[name] = environment[name]


def apply_process_resource_limits(process_id: int, memory_limit_bytes: int) -> bool:
    """Apply Linux child limits from the parent; watchdogs cover unsupported platforms."""

    if memory_limit_bytes < MINIMUM_PROCESS_MEMORY_LIMIT_BYTES:
        raise ValueError("child process memory limit is below the safe minimum")
    if os.name == "nt" or process_id <= 0:
        return False
    try:
        resource_module = __import__("resource")
        resource_values = vars(resource_module)
        prlimit = resource_values.get("prlimit")
        if not callable(prlimit):
            return False
        rlimit_as = resource_values["RLIMIT_AS"]
        rlimit_core = resource_values["RLIMIT_CORE"]
        _, hard = prlimit(process_id, rlimit_as)
        bounded = memory_limit_bytes if hard < 0 else min(memory_limit_bytes, hard)
        prlimit(process_id, rlimit_as, (bounded, bounded))
        prlimit(process_id, rlimit_core, (0, 0))
    except (KeyError, OSError, TypeError, ValueError):
        return False
    return True


def process_resident_memory_bytes(process_id: int) -> int | None:
    if process_id <= 0:
        return None
    if os.name == "nt":
        return _windows_process_resident_memory_bytes(process_id)
    status_path = f"/proc/{process_id}/status"
    try:
        with open(status_path, encoding="ascii") as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    fields = line.split()
                    if len(fields) >= 2:
                        return int(fields[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


def _windows_process_resident_memory_bytes(process_id: int) -> int | None:
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        process_query_limited_information = 0x1000
        process_vm_read = 0x0010
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information | process_vm_read,
            False,
            process_id,
        )
        if not handle:
            return None
        try:
            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            succeeded = ctypes.windll.psapi.GetProcessMemoryInfo(
                handle,
                ctypes.byref(counters),
                counters.cb,
            )
            return int(counters.WorkingSetSize) if succeeded else None
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
