from __future__ import annotations

import shutil

from app.core.process_limits import run_bounded_child_process


async def probe_executable_version(
    name: str,
    *,
    timeout_seconds: float = 3.0,
) -> tuple[bool, str | None]:
    """Return one bounded, path-free version line for a trusted executable name."""

    executable = shutil.which(name)
    if executable is None:
        return False, None
    result = await run_bounded_child_process(
        executable,
        "-version",
        timeout_seconds=timeout_seconds,
    )
    if result is None or result.return_code != 0 or result.output_exceeded:
        return False, None
    output = result.stdout or result.stderr
    first_line = output.splitlines()[0].decode("utf-8", errors="replace") if output else ""
    return bool(first_line), first_line or None


async def probe_media_executable(name: str, *, timeout_seconds: float = 3.0) -> bool:
    """Execute a bounded version probe without exposing the resolved executable path."""

    available, first_line = await probe_executable_version(
        name,
        timeout_seconds=timeout_seconds,
    )
    return available and name.lower() in (first_line or "").lower()
