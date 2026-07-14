from __future__ import annotations

import json
import logging
from pathlib import Path

from app.core.logging import JsonFormatter, redact_text, sanitize


def test_redaction_removes_cookie_authorization_and_signed_query() -> None:
    message = (
        "SESSDATA=secret-value; bili_jct:csrf-value Authorization: Bearer bearer-secret "
        "https://cdn.bilivideo.com/a.m4s?deadline=1&token=signed"
    )
    redacted = redact_text(message)
    assert "secret-value" not in redacted
    assert "csrf-value" not in redacted
    assert "bearer-secret" not in redacted
    assert "token=signed" not in redacted
    assert "<redacted>" in redacted


def test_recursive_sanitizer_redacts_sensitive_keys() -> None:
    output = sanitize(
        {
            "headers": {"Cookie": "SESSDATA=secret"},
            "authorization": "Bearer secret",
            "safe": ["https://example.test/path?secret=1"],
        }
    )
    assert output["authorization"] == "<redacted>"
    assert output["headers"]["Cookie"] == "<redacted>"
    assert output["safe"] == ["https://example.test/path?<redacted>"]


def test_redaction_removes_cross_platform_paths_but_keeps_api_routes() -> None:
    message = (
        r'File "C:\Users\private\project\worker.py", line 8; '
        r"UNC \\server\private-share\reports\result.json; "
        "forward UNC //server/private-share/report.json; "
        "POSIX /srv/bili/private/job.json; "
        'quoted POSIX "/srv/bili/My Private Project/job.json"; '
        "request GET /api/v1/jobs/123?limit=2"
    )
    redacted = redact_text(message)
    assert "C:\\Users\\private" not in redacted
    assert "private-share" not in redacted
    assert "/srv/bili/private" not in redacted
    assert "My Private Project" not in redacted
    assert redacted.count("<path>") >= 5
    assert "/api/v1/jobs/123?<redacted>" in redacted


def test_sanitize_redacts_pathlike_values() -> None:
    assert sanitize(Path("/srv/private/report.json")) == "<path>"


def test_json_formatter_redacts_paths_in_exception_traceback() -> None:
    try:
        raise RuntimeError(r"failed at C:\private\service\worker.py and /srv/private/data")
    except RuntimeError:
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "request failed on %s",
            (r"\\server\share\private.txt",),
            exc_info=__import__("sys").exc_info(),
        )
    payload = json.loads(JsonFormatter().format(record))
    serialized = json.dumps(payload)
    assert "C:\\\\private" not in serialized
    assert "/srv/private" not in serialized
    assert "server\\\\share" not in serialized
    assert "<path>" in payload["exception"]
