"""Reject credential-shaped files and high-confidence secrets tracked by Git."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_SCAN_BYTES = 5 * 1024 * 1024

FORBIDDEN_NAMES = (
    re.compile(r"(?:^|/)cookies?\.json$", re.IGNORECASE),
    re.compile(r"(?:^|/).*\.cookies\.(?:json|txt)$", re.IGNORECASE),
    re.compile(r"(?:^|/)\.env(?:\..+)?$", re.IGNORECASE),
)

SECRET_PATTERNS = (
    re.compile(r"APP_COOKIE_ENCRYPTION_KEY\s*=\s*[A-Za-z0-9_-]{43}=", re.IGNORECASE),
    re.compile(r"SESSDATA\s*[=:]\s*[\"']?[A-Za-z0-9%_-]{20,}", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

ALLOWED_NAMES = {".env.example"}
SCANNER_PATH = "scripts/check_repository_secrets.py"


def repository_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [item for item in result.stdout.decode("utf-8").split("\0") if item]


def main() -> int:
    violations: list[str] = []
    for relative_name in repository_files():
        normalized = relative_name.replace("\\", "/")
        path = ROOT / relative_name
        if path.name.lower() not in ALLOWED_NAMES and any(
            pattern.search(normalized) for pattern in FORBIDDEN_NAMES
        ):
            violations.append(f"forbidden credential filename: {normalized}")
            continue

        if normalized == SCANNER_PATH or not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            violations.append(f"credential-shaped content: {normalized}")

    if violations:
        print("Repository credential safety check failed:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1

    print("Repository credential safety check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
