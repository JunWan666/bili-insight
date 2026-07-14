from __future__ import annotations

import math
import string
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.schemas.base import CamelModel

GIBIBYTE = 1024**3
MEBIBYTE = 1024**2
MAX_STORAGE_QUOTA_BYTES = 100_000 * GIBIBYTE
MAX_RATE_LIMIT_BYTES_PER_SECOND = 10_000 * MEBIBYTE
_ALLOWED_TEMPLATE_FIELDS = {"title", "bvid", "page", "part", "quality"}
_WINDOWS_FORBIDDEN_CHARACTERS = set('<>:"|?*')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class DownloadPreset(StrEnum):
    BEST_QUALITY = "best_quality"
    BEST_COMPATIBILITY = "best_compatibility"
    SMALLEST = "smallest"
    AUDIO_ONLY = "audio_only"
    CUSTOM = "custom"


class DefaultContainer(StrEnum):
    MP4 = "mp4"
    MKV = "mkv"


class AnalysisLanguage(StrEnum):
    CHINESE_SIMPLIFIED = "zh-CN"
    AUTO = "auto"
    ENGLISH = "en"
    JAPANESE = "ja"


class AsrModel(StrEnum):
    TINY = "tiny"
    BASE = "base"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE_V3 = "large-v3"


class AnalysisDevice(StrEnum):
    AUTO = "auto"
    CPU = "cpu"
    GPU = "gpu"


class DownloadSettings(CamelModel):
    default_preset: DownloadPreset = DownloadPreset.BEST_QUALITY
    concurrency: int = Field(default=2, ge=1, le=4)
    retry_limit: int = Field(default=2, ge=0, le=5)
    filename_template: str = Field(default="{title} - P{page} - {quality}", max_length=180)
    default_container: DefaultContainer = DefaultContainer.MP4
    minimum_resolution_height: Literal[360, 480, 720, 1080] | None = None

    @field_validator("filename_template")
    @classmethod
    def validate_filename_template(cls, value: str) -> str:
        template = value.strip()
        if not template:
            raise ValueError("filename template cannot be empty")
        if any(ord(character) < 32 for character in template):
            raise ValueError("filename template contains control characters")
        if any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in template):
            raise ValueError("filename template contains reserved filename characters")
        if "/" in template or "\\" in template:
            raise ValueError("filename template cannot contain path separators")
        try:
            parsed = tuple(string.Formatter().parse(template))
        except ValueError as exc:
            raise ValueError("filename template has invalid braces") from exc
        for _, field_name, format_spec, conversion in parsed:
            if field_name is None:
                continue
            if field_name not in _ALLOWED_TEMPLATE_FIELDS:
                raise ValueError("filename template contains an unsupported field")
            if format_spec or conversion:
                raise ValueError("filename template formatting modifiers are not supported")
        return template


class StorageSettings(CamelModel):
    artifact_directory: str = "artifacts"
    temporary_directory: str = "tmp"
    quota_bytes: int | None = Field(
        default=None,
        ge=GIBIBYTE,
        le=MAX_STORAGE_QUOTA_BYTES,
    )
    cleanup_after_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("artifact_directory", "temporary_directory")
    @classmethod
    def validate_relative_directory(cls, value: str) -> str:
        return normalize_storage_relative_path(value)

    @model_validator(mode="after")
    def validate_directories_are_separate(self) -> StorageSettings:
        artifact = tuple(part.casefold() for part in PurePosixPath(self.artifact_directory).parts)
        temporary = tuple(part.casefold() for part in PurePosixPath(self.temporary_directory).parts)
        minimum_length = min(len(artifact), len(temporary))
        if artifact[:minimum_length] == temporary[:minimum_length]:
            raise ValueError("artifact and temporary directories must not overlap")
        return self


class AnalysisSettings(CamelModel):
    model_config = ConfigDict(allow_inf_nan=False)

    language: AnalysisLanguage = AnalysisLanguage.CHINESE_SIMPLIFIED
    asr_model: AsrModel = AsrModel.SMALL
    ocr_enabled: bool = False
    device: AnalysisDevice = AnalysisDevice.AUTO
    sample_interval_seconds: float = Field(default=2.0, ge=0.2, le=60.0)
    maximum_duration_seconds: int | None = Field(default=3600, ge=60, le=86_400)

    @field_validator("sample_interval_seconds")
    @classmethod
    def validate_finite_interval(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("sample interval must be finite")
        return value


class NetworkSettings(CamelModel):
    timeout_seconds: int = Field(default=30, ge=5, le=300)
    rate_limit_bytes_per_second: int | None = Field(
        default=None,
        ge=104_858,
        le=MAX_RATE_LIMIT_BYTES_PER_SECOND,
    )
    upstream_interval_milliseconds: int = Field(default=250, ge=0, le=60_000)


class PrivacySettings(CamelModel):
    history_retention_days: int | None = Field(default=None, ge=1, le=3650)
    diagnostics_enabled: bool = True


class AppSettings(CamelModel):
    download: DownloadSettings = Field(default_factory=DownloadSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    network: NetworkSettings = Field(default_factory=NetworkSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)


def normalize_storage_relative_path(value: str) -> str:
    raw = value.strip()
    if value != raw or not raw or len(raw) > 240 or "\x00" in raw:
        raise ValueError("storage directory is invalid")
    windows_path = PureWindowsPath(raw)
    posix_path = PurePosixPath(raw)
    if windows_path.is_absolute() or windows_path.drive or posix_path.is_absolute():
        raise ValueError("storage directory must be relative")
    normalized = raw.replace("\\", "/")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("storage directory contains unsafe path segments")
    for part in parts:
        if len(part) > 128 or part.endswith((" ", ".")):
            raise ValueError("storage directory contains an invalid segment")
        if any(ord(character) < 32 for character in part):
            raise ValueError("storage directory contains control characters")
        if any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in part):
            raise ValueError("storage directory contains reserved characters")
        if part.split(".", maxsplit=1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            raise ValueError("storage directory contains a reserved name")
    return "/".join(parts)
