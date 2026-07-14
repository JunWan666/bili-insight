from __future__ import annotations

import json
import math
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any


def to_json_compatible(value: object) -> Any:
    """Converts analysis dataclasses to JSON values without exposing absolute paths."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        timestamp = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return timestamp.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.name
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_json_compatible(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, dict):
        return {_json_key(key): to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        serialized = [to_json_compatible(item) for item in value]
        return sorted(serialized, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [to_json_compatible(item) for item in value]
    raise TypeError(f"Unsupported analysis value: {type(value).__name__}")


def _json_key(value: object) -> str:
    if isinstance(value, Path):
        return value.name
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    raise TypeError(f"Unsupported analysis mapping key: {type(value).__name__}")


def analysis_json_bytes(value: object, *, pretty: bool = True) -> bytes:
    return (
        json.dumps(
            to_json_compatible(value),
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
