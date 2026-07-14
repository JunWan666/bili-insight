from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.api.dependencies import get_container
from app.container import ApplicationContainer
from app.core.runtime import probe_media_executable
from app.db.session import check_database

router = APIRouter(prefix="/health", tags=["health"])


def _probe_storage_directories(
    directories: tuple[Path, ...],
    *,
    min_free_bytes: int,
) -> None:
    for directory in directories:
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or (hasattr(directory, "is_junction") and directory.is_junction())
        ):
            raise OSError("required storage directory is unavailable")
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".bili-insight-readiness-",
                suffix=".tmp",
                dir=directory,
            ) as probe:
                probe.write(b"ready")
                probe.flush()
        except OSError as exc:
            raise OSError("required storage directory is not writable") from exc
        if shutil.disk_usage(directory).free < min_free_bytes:
            raise OSError("required storage volume has insufficient free space")


@router.get("")
async def health(container: ApplicationContainer = Depends(get_container)) -> dict[str, str]:
    return {"status": "ok", "version": container.settings.version}


@router.get("/ready", response_model=None)
async def readiness(
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, Any] | JSONResponse:
    checks: dict[str, str] = {}
    try:
        await check_database(container.engine)
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"

    try:
        (
            artifact_directory,
            temporary_directory,
        ) = await container.settings_service.storage_directories()
        directories = (
            container.settings_service.storage_root,
            artifact_directory,
            temporary_directory,
        )
        await asyncio.to_thread(
            _probe_storage_directories,
            directories,
            min_free_bytes=container.download_executor.runtime.min_free_bytes,
        )
        if (
            container.artifact_service.root != artifact_directory
            or container.download_executor.artifact_root != artifact_directory
            or container.download_executor.temp_root != temporary_directory
            or container.analysis_service.artifact_root != artifact_directory
            or container.subtitle_service.temp_root != temporary_directory
        ):
            raise OSError("runtime storage configuration is inconsistent")
        checks["storage"] = "ok"
    except Exception:
        checks["storage"] = "unavailable"

    try:
        worker_health = container.job_service.health()
        checks["worker"] = "ok" if worker_health.status == "healthy" else worker_health.status
    except Exception:
        checks["worker"] = "unavailable"

    async def media_probe(name: str) -> bool:
        try:
            return await probe_media_executable(name)
        except Exception:
            return False

    ffmpeg_ready, ffprobe_ready = await asyncio.gather(
        media_probe("ffmpeg"),
        media_probe("ffprobe"),
    )
    checks["ffmpeg"] = "ok" if ffmpeg_ready else "unavailable"
    checks["ffprobe"] = "ok" if ffprobe_ready else "unavailable"

    if any(value != "ok" for value in checks.values()):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "checks": checks},
        )
    return {"status": "ready", "checks": checks}
