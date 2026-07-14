from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app.analysis import AnalysisError, AnalysisErrorCode, ProcessRunner
from app.core.process_limits import MINIMUM_PROCESS_MEMORY_LIMIT_BYTES


def test_process_runner_preserves_argument_boundaries(tmp_path: Path) -> None:
    marker = tmp_path / "command-injection-marker"
    dangerous_argument = f"; open(r'{marker}', 'w').write('unsafe')"
    result = ProcessRunner(default_timeout_seconds=5).run(
        sys.executable,
        ["-c", "import sys; print(sys.argv[1])", dangerous_argument],
    )
    assert result.stdout_text().strip() == dangerous_argument
    assert not marker.exists()


def test_process_runner_enforces_timeout() -> None:
    runner = ProcessRunner(default_timeout_seconds=5)
    with pytest.raises(AnalysisError) as caught:
        runner.run(
            sys.executable,
            ["-c", "import time; time.sleep(2)"],
            timeout_seconds=0.1,
        )
    assert caught.value.failure.code == AnalysisErrorCode.PROCESS_TIMEOUT


def test_process_runner_enforces_bounded_output() -> None:
    runner = ProcessRunner(
        default_timeout_seconds=5,
        stdout_limit_bytes=65_536,
        stderr_limit_bytes=65_536,
    )
    with pytest.raises(AnalysisError) as caught:
        runner.run(sys.executable, ["-c", "print('x' * 70000)"])
    assert caught.value.failure.code == AnalysisErrorCode.PROCESS_OUTPUT_LIMIT


def test_process_runner_observes_cancellation() -> None:
    event = threading.Event()
    event.set()
    with pytest.raises(AnalysisError) as caught:
        ProcessRunner(default_timeout_seconds=5).run(
            sys.executable,
            ["-c", "import time; time.sleep(2)"],
            cancellation_event=event,
        )
    assert caught.value.failure.code == AnalysisErrorCode.CANCELED


def test_process_runner_applies_native_thread_cap() -> None:
    result = ProcessRunner(default_timeout_seconds=5, max_threads=3).run(
        sys.executable,
        ["-c", "import os; print(os.environ['OMP_NUM_THREADS'])"],
    )
    assert result.stdout_text().strip() == "3"


def test_process_runner_memory_watchdog_stops_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = MINIMUM_PROCESS_MEMORY_LIMIT_BYTES
    monkeypatch.setattr(
        "app.analysis.process.process_resident_memory_bytes",
        lambda _: limit + 1,
    )
    with pytest.raises(AnalysisError) as caught:
        ProcessRunner(default_timeout_seconds=5, memory_limit_bytes=limit).run(
            sys.executable,
            ["-c", "import time; time.sleep(2)"],
        )
    assert caught.value.failure.code == AnalysisErrorCode.PROCESS_RESOURCE_LIMIT


def test_process_runner_rejects_unbounded_resource_configuration() -> None:
    with pytest.raises(AnalysisError):
        ProcessRunner(max_threads=0)
    with pytest.raises(AnalysisError):
        ProcessRunner(memory_limit_bytes=MINIMUM_PROCESS_MEMORY_LIMIT_BYTES - 1)


def test_process_runner_applies_limits_from_parent_without_preexec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_popen = subprocess.Popen
    spawn_keywords: dict[str, object] = {}
    applied: list[tuple[int, int]] = []

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        spawn_keywords.update(kwargs)
        return real_popen(*args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr("app.analysis.process.subprocess.Popen", recording_popen)
    monkeypatch.setattr(
        "app.analysis.process.apply_process_resource_limits",
        lambda process_id, memory: applied.append((process_id, memory)) or True,
    )
    runner = ProcessRunner(default_timeout_seconds=5)
    runner.run(sys.executable, ["-c", "print('bounded')"])
    assert "preexec_fn" not in spawn_keywords
    assert spawn_keywords["start_new_session"] is (os.name != "nt")
    if os.name == "nt":
        assert int(spawn_keywords["creationflags"]) & subprocess.CREATE_NEW_PROCESS_GROUP
    assert applied and applied[0][1] == runner.memory_limit_bytes


def test_process_diagnostic_redacts_absolute_path(tmp_path: Path) -> None:
    secret_path = (tmp_path / "private-input.mp4").resolve()
    secret_path.write_bytes(b"invalid")
    with pytest.raises(AnalysisError) as caught:
        ProcessRunner(default_timeout_seconds=5).run(
            sys.executable,
            [
                "-c",
                "import sys; sys.stderr.write(sys.argv[1]); raise SystemExit(4)",
                secret_path,
            ],
        )
    diagnostic = caught.value.failure.diagnostic or ""
    assert str(secret_path) not in diagnostic
    assert secret_path.name in diagnostic
