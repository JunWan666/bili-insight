from __future__ import annotations

import asyncio
import builtins
import hashlib
import logging
import os
import shutil
import stat
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi import status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.exceptions import AppError, ErrorCode
from app.db.models import Artifact, Job, JobStatus, RetainedFile
from app.media.download import iter_file
from app.media.security import safe_child_path, sanitize_filename
from app.schemas.artifacts import (
    ArtifactDeleteResponse,
    ArtifactList,
    ArtifactRead,
    StorageStatus,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FileDelivery:
    path: Path
    filename: str
    mime_type: str
    size: int
    start: int
    end: int
    status_code: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    def stream(self) -> AsyncIterator[bytes]:
        return iter_file(self.path, start=self.start, length=self.length)


@dataclass(frozen=True, slots=True)
class QuarantinedArtifactFile:
    original: Path
    quarantine: Path


@dataclass(frozen=True, slots=True)
class RetainedFileStage:
    files: tuple[QuarantinedArtifactFile, ...]
    records: tuple[RetainedFile, ...]


class RangeNotSatisfiable(ValueError):
    def __init__(self, size: int) -> None:
        super().__init__("requested byte range is not satisfiable")
        self.size = size


class ArtifactService:
    """Persist and deliver files strictly contained by the configured artifact root."""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.root = settings.artifact_dir.expanduser().resolve()
        self.session_factory = session_factory
        self._mutation_lock = asyncio.Lock()
        self._mutation_owner: asyncio.Task[object] | None = None
        self._mutation_depth = 0
        self.root.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def mutation_guard(self) -> AsyncIterator[None]:
        """Serialize Artifact DB and filesystem mutations across service workflows."""

        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("artifact mutation guard requires an asyncio task")
        if self._mutation_owner is task:
            self._mutation_depth += 1
            try:
                yield
            finally:
                self._mutation_depth -= 1
            return

        await self._mutation_lock.acquire()
        self._mutation_owner = task
        self._mutation_depth = 1
        try:
            yield
        finally:
            self._mutation_depth = 0
            self._mutation_owner = None
            self._mutation_lock.release()

    async def reconfigure_root(self, root: Path, *, startup: bool = False) -> None:
        async with self.mutation_guard():
            await self._reconfigure_root_unlocked(root, startup=startup)

    async def _reconfigure_root_unlocked(self, root: Path, *, startup: bool) -> None:
        candidate = await asyncio.to_thread(lambda: root.expanduser().resolve())
        if candidate == self.root:
            return
        async with self.session_factory() as session:
            artifact_count = int(await session.scalar(select(func.count(Artifact.id))) or 0)
            retained_count = int(await session.scalar(select(func.count(RetainedFile.id))) or 0)
            count = artifact_count + retained_count
            storage_keys: list[str] = []
            if startup:
                storage_keys.extend((await session.scalars(select(Artifact.storage_key))).all())
                storage_keys.extend((await session.scalars(select(RetainedFile.storage_key))).all())
        if startup:
            await asyncio.to_thread(candidate.mkdir, parents=True, exist_ok=True)
            for storage_key in storage_keys:
                safe_child_path(candidate, *Path(storage_key).parts)
            self.root = candidate
            return
        has_retained_or_untracked = await asyncio.to_thread(
            lambda: any(path.is_file() for path in self.root.rglob("*"))
        )
        if count or has_retained_or_untracked:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "已有产物或保留文件时不能直接切换产物目录",
                action="清理产物并迁移保留文件后再更改目录",
                status_code=status.HTTP_409_CONFLICT,
            )
        await asyncio.to_thread(candidate.mkdir, parents=True, exist_ok=True)
        self.root = candidate

    async def reconcile_retained_files(self) -> dict[str, int]:
        """Recover legacy and interrupted retained-file moves into managed state."""

        async with self.mutation_guard():
            return await self._reconcile_retained_files_unlocked()

    async def _reconcile_retained_files_unlocked(self) -> dict[str, int]:

        retained_root = safe_child_path(self.root, ".retained")
        await asyncio.to_thread(retained_root.mkdir, parents=True, exist_ok=True)
        async with self.session_factory() as session:
            tracked = set((await session.scalars(select(RetainedFile.storage_key))).all())
            artifacts = {
                record.id: record for record in (await session.scalars(select(Artifact))).all()
            }
            retained_ids = set((await session.scalars(select(RetainedFile.id))).all())
            # Artifact and retained-file identifiers share one public API
            # namespace even though they live in separate tables.  Never
            # recover an unrelated retained file under a live artifact ID or
            # the retained record would be shadowed and could not be managed.
            occupied_ids = retained_ids | set(artifacts)
            recovered: list[RetainedFile] = []
            moved: list[tuple[Path, Path]] = []
            restored_artifacts = 0
            try:
                paths = await asyncio.to_thread(
                    lambda: tuple(path for path in retained_root.rglob("*") if path.is_file())
                )
                for path in paths:
                    if path.is_symlink():
                        await asyncio.to_thread(path.unlink, missing_ok=True)
                        continue
                    try:
                        self._contained_file(path)
                    except (FileNotFoundError, ValueError):
                        logger.warning(
                            "Unsafe retained-file entry was ignored during reconciliation",
                            extra={"event": "unsafe_retained_entry_ignored"},
                        )
                        continue
                    relative = path.relative_to(self.root).as_posix()
                    if relative in tracked:
                        continue
                    parts = path.relative_to(retained_root).parts
                    candidate_id = parts[0] if len(parts) >= 2 else ""
                    source_artifact = artifacts.get(candidate_id)
                    if source_artifact is not None:
                        original = self._artifact_path(source_artifact)
                        if not await asyncio.to_thread(original.exists):
                            await asyncio.to_thread(
                                original.parent.mkdir, parents=True, exist_ok=True
                            )
                            await asyncio.to_thread(os.replace, path, original)
                            restored_artifacts += 1
                            continue
                    retained_id = self._safe_retained_identifier(candidate_id, occupied_ids)
                    occupied_ids.add(retained_id)
                    target = safe_child_path(
                        self.root,
                        ".retained",
                        retained_id,
                        sanitize_filename(path.name),
                    )
                    if target != path:
                        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
                        await asyncio.to_thread(os.replace, path, target)
                        moved.append((target, path))
                    file_stat = await asyncio.to_thread(target.stat)
                    checksum = await asyncio.to_thread(self._sha256, target)
                    recovered.append(
                        RetainedFile(
                            id=retained_id,
                            type=self._retained_type(target.name),
                            filename=sanitize_filename(target.name),
                            storage_key=target.relative_to(self.root).as_posix(),
                            mime_type="application/octet-stream",
                            size=file_stat.st_size,
                            checksum=f"sha256:{checksum}",
                            protected=True,
                            retention_reason="legacy_recovered",
                            expires_at=None,
                            created_at=datetime.fromtimestamp(file_stat.st_mtime, UTC),
                        )
                    )
                session.add_all(recovered)
                await session.commit()
            except Exception:
                await session.rollback()
                for target, original in reversed(moved):
                    try:
                        if await asyncio.to_thread(target.exists):
                            await asyncio.to_thread(
                                original.parent.mkdir, parents=True, exist_ok=True
                            )
                            await asyncio.to_thread(os.replace, target, original)
                    except OSError:
                        logger.critical(
                            "Retained-file reconciliation rollback failed",
                            extra={"event": "retained_reconciliation_restore_failed"},
                        )
                raise
        return {"recovered": len(recovered), "restoredArtifacts": restored_artifacts}

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        artifact_type: str | None = None,
        job_id: str | None = None,
        search: str | None = None,
        job_status: JobStatus | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> ArtifactList:
        created_from = self._as_utc(created_from)
        created_to = self._as_utc(created_to)
        if created_from is not None and created_to is not None and created_from > created_to:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "产物筛选的开始时间不能晚于结束时间",
                action="调整日期范围后重试",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        filters = []
        retained_filters = []
        if artifact_type:
            filters.append(Artifact.type == artifact_type)
            retained_filters.append(RetainedFile.type == artifact_type)
        if job_id:
            filters.append(Artifact.job_id == job_id)
        if job_status is not None:
            filters.append(Artifact.job.has(Job.status == job_status))
        if created_from is not None:
            filters.append(Artifact.created_at >= created_from)
            retained_filters.append(RetainedFile.created_at >= created_from)
        if created_to is not None:
            filters.append(Artifact.created_at <= created_to)
            retained_filters.append(RetainedFile.created_at <= created_to)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            filters.append(
                or_(
                    Artifact.filename.ilike(pattern, escape="\\"),
                    Artifact.job.has(
                        Job.input_json["video_title"]
                        .as_string()
                        .ilike(
                            pattern,
                            escape="\\",
                        )
                    ),
                )
            )
            retained_filters.append(RetainedFile.filename.ilike(pattern, escape="\\"))
        async with self.session_factory() as session:
            records = list(
                (
                    await session.scalars(
                        select(Artifact)
                        .where(*filters)
                        .options(selectinload(Artifact.job))
                        .order_by(Artifact.created_at.desc())
                    )
                ).all()
            )
            retained_records: list[RetainedFile] = []
            if job_id is None and job_status is None:
                retained_records = list(
                    (
                        await session.scalars(
                            select(RetainedFile)
                            .where(*retained_filters)
                            .order_by(RetainedFile.created_at.desc())
                        )
                    ).all()
                )
        combined = [self.to_read(item) for item in records]
        combined.extend(self.to_read_retained(item) for item in retained_records)
        combined.sort(key=lambda item: item.created_at, reverse=True)
        return ArtifactList(
            items=combined[offset : offset + limit],
            total=len(combined),
            limit=limit,
            offset=offset,
        )

    async def get(self, artifact_id: str) -> ArtifactRead:
        try:
            return self.to_read(await self._record(artifact_id))
        except AppError:
            retained = await self._retained_record(artifact_id)
            return self.to_read_retained(retained)

    async def existing_for_job(self, job_id: str, artifact_type: str) -> Artifact | None:
        async with self.mutation_guard():
            return await self._existing_for_job_unlocked(job_id, artifact_type)

    async def _existing_for_job_unlocked(self, job_id: str, artifact_type: str) -> Artifact | None:
        async with self.session_factory() as session:
            record = await session.scalar(
                select(Artifact)
                .where(Artifact.job_id == job_id, Artifact.type == artifact_type)
                .order_by(Artifact.created_at.desc())
            )
            if record is None:
                return None
            path = self._artifact_path(record)
            valid = await asyncio.to_thread(self._file_has_size, path, record.size)
            if valid:
                session.expunge(record)
                return record
            await session.delete(record)
            await session.commit()
            return None

    async def existing_all_for_job(
        self,
        job_id: str,
        artifact_types: set[str] | None = None,
    ) -> builtins.list[Artifact]:
        async with self.mutation_guard():
            return await self._existing_all_for_job_unlocked(job_id, artifact_types)

    async def _existing_all_for_job_unlocked(
        self,
        job_id: str,
        artifact_types: set[str] | None,
    ) -> builtins.list[Artifact]:
        filters = [Artifact.job_id == job_id]
        if artifact_types:
            filters.append(Artifact.type.in_(artifact_types))
        async with self.session_factory() as session:
            records = list(
                (
                    await session.scalars(
                        select(Artifact).where(*filters).order_by(Artifact.created_at.asc())
                    )
                ).all()
            )
            valid: builtins.list[Artifact] = []
            removed_stale = False
            for record in records:
                path = self._artifact_path(record)
                if await asyncio.to_thread(self._file_has_size, path, record.size):
                    session.expunge(record)
                    valid.append(record)
                else:
                    await session.delete(record)
                    removed_stale = True
            if removed_stale:
                await session.commit()
            return valid

    async def create_from_file(
        self,
        *,
        job_id: str,
        artifact_type: str,
        path: Path,
        filename: str,
        mime_type: str,
        media_info: dict[str, object] | None,
        expires_at: datetime | None = None,
    ) -> Artifact:
        async with self.mutation_guard():
            return await self._create_from_file_unlocked(
                job_id=job_id,
                artifact_type=artifact_type,
                path=path,
                filename=filename,
                mime_type=mime_type,
                media_info=media_info,
                expires_at=expires_at,
            )

    async def _create_from_file_unlocked(
        self,
        *,
        job_id: str,
        artifact_type: str,
        path: Path,
        filename: str,
        mime_type: str,
        media_info: dict[str, object] | None,
        expires_at: datetime | None,
    ) -> Artifact:
        resolved = self._contained_file(path)
        relative = resolved.relative_to(self.root).as_posix()
        size = resolved.stat().st_size
        checksum = await asyncio.to_thread(self._sha256, resolved)
        record = Artifact(
            job_id=job_id,
            type=artifact_type,
            filename=sanitize_filename(filename),
            storage_key=relative,
            mime_type=mime_type,
            size=size,
            checksum=f"sha256:{checksum}",
            media_info=media_info,
            expires_at=expires_at,
        )
        async with self.session_factory() as session:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            session.expunge(record)
        return record

    async def publish(
        self,
        *,
        job_id: str,
        artifact_type: str,
        staging_path: Path,
        final_path: Path,
        filename: str,
        mime_type: str,
        media_info: dict[str, object] | None,
        expires_at: datetime | None = None,
    ) -> Artifact:
        """Atomically expose a completed file and roll it back if DB persistence fails."""

        async with self.mutation_guard():
            return await self._publish_unlocked(
                job_id=job_id,
                artifact_type=artifact_type,
                staging_path=staging_path,
                final_path=final_path,
                filename=filename,
                mime_type=mime_type,
                media_info=media_info,
                expires_at=expires_at,
            )

    async def _publish_unlocked(
        self,
        *,
        job_id: str,
        artifact_type: str,
        staging_path: Path,
        final_path: Path,
        filename: str,
        mime_type: str,
        media_info: dict[str, object] | None,
        expires_at: datetime | None,
    ) -> Artifact:

        staging = self._contained_file(staging_path)
        final = self._contained_target(final_path)
        await asyncio.to_thread(self._atomic_replace, staging, final)
        try:
            return await self._create_from_file_unlocked(
                job_id=job_id,
                artifact_type=artifact_type,
                path=final,
                filename=filename,
                mime_type=mime_type,
                media_info=media_info,
                expires_at=expires_at,
            )
        except Exception:
            await asyncio.to_thread(final.unlink, missing_ok=True)
            raise

    async def delete(self, artifact_id: str, *, delete_file: bool) -> ArtifactDeleteResponse:
        async with self.mutation_guard():
            return await self._delete_unlocked(artifact_id, delete_file=delete_file)

    async def _delete_unlocked(
        self, artifact_id: str, *, delete_file: bool
    ) -> ArtifactDeleteResponse:
        quarantine: Path | None = None
        original: Path | None = None
        async with self.session_factory() as session:
            record = await session.get(Artifact, artifact_id)
            if record is None:
                retained = await session.get(RetainedFile, artifact_id)
                if retained is None:
                    raise self._not_found()
                if not delete_file:
                    raise AppError(
                        ErrorCode.VALIDATION_ERROR,
                        "保留文件必须保留管理记录",
                        action="如需释放空间，请选择记录与文件一起删除",
                        status_code=status.HTTP_409_CONFLICT,
                    )
                original = safe_child_path(self.root, *Path(retained.storage_key).parts)
                if await asyncio.to_thread(original.exists):
                    quarantine = safe_child_path(
                        self.root,
                        f".deleting-{artifact_id}-{uuid.uuid4().hex}",
                    )
                    try:
                        await asyncio.to_thread(os.replace, original, quarantine)
                    except OSError as exc:
                        raise AppError(
                            ErrorCode.INTERNAL_ERROR,
                            "保留文件暂时无法安全删除",
                            action="确认文件未被其他程序占用后重试",
                            status_code=status.HTTP_409_CONFLICT,
                        ) from exc
                try:
                    await session.delete(retained)
                    await session.commit()
                except Exception:
                    if quarantine is not None and original is not None:
                        await asyncio.to_thread(os.replace, quarantine, original)
                    raise
                file_deleted = False
                if quarantine is not None and delete_file:
                    try:
                        await asyncio.to_thread(quarantine.unlink)
                        file_deleted = True
                    except OSError:
                        logger.error(
                            "Retained file was quarantined but removal failed",
                            extra={"event": "retained_file_delete_failed"},
                        )
                return ArtifactDeleteResponse(
                    id=artifact_id,
                    record_deleted=True,
                    file_deleted=file_deleted,
                    retained=True,
                )

            if not delete_file:
                stage = await self._retain_records_for_privacy_cleanup_unlocked(
                    [record],
                    reason="user_retained",
                    protected=True,
                )
                try:
                    session.add_all(stage.records)
                    await session.delete(record)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    await self._restore_without_masking(stage.files)
                    raise
                for item in stage.files:
                    await asyncio.to_thread(self._remove_empty_parents, item.original.parent)
                return ArtifactDeleteResponse(
                    id=artifact_id,
                    record_deleted=True,
                    file_deleted=False,
                    retained=bool(stage.records),
                )

            original = self._artifact_path(record)
            if await asyncio.to_thread(original.exists):
                quarantine = safe_child_path(
                    self.root,
                    f".deleting-{artifact_id}-{uuid.uuid4().hex}",
                )
                try:
                    await asyncio.to_thread(os.replace, original, quarantine)
                except OSError as exc:
                    raise AppError(
                        ErrorCode.INTERNAL_ERROR,
                        "产物文件暂时无法安全删除",
                        action="确认文件未被其他程序占用后重试",
                        status_code=status.HTTP_409_CONFLICT,
                    ) from exc
            try:
                await session.delete(record)
                await session.commit()
            except Exception:
                if quarantine is not None and original is not None:
                    await asyncio.to_thread(os.replace, quarantine, original)
                raise

        file_deleted = False
        if quarantine is not None and delete_file:
            try:
                await asyncio.to_thread(quarantine.unlink)
                file_deleted = True
                if original is not None:
                    await asyncio.to_thread(self._remove_empty_parents, original.parent)
            except OSError:
                logger.error(
                    "Artifact was quarantined but final file removal failed",
                    extra={"event": "artifact_file_delete_failed", "artifact_id": artifact_id},
                )
        return ArtifactDeleteResponse(
            id=artifact_id,
            record_deleted=True,
            file_deleted=file_deleted,
            retained=False,
        )

    async def retain_records_for_privacy_cleanup(
        self,
        records: Sequence[Artifact],
        *,
        reason: str = "history_retention",
        protected: bool = False,
    ) -> RetainedFileStage:
        """Move files into managed retention and return DB records for the same transaction."""

        async with self.mutation_guard():
            return await self._retain_records_for_privacy_cleanup_unlocked(
                records,
                reason=reason,
                protected=protected,
            )

    async def _retain_records_for_privacy_cleanup_unlocked(
        self,
        records: Sequence[Artifact],
        *,
        reason: str,
        protected: bool,
    ) -> RetainedFileStage:

        if reason not in {"history_retention", "user_retained"}:
            raise ValueError("retention reason is invalid")
        retained: list[RetainedFile] = []
        staged: list[QuarantinedArtifactFile] = []
        try:
            for record in records:
                original = self._artifact_path(record)
                if not await asyncio.to_thread(original.exists):
                    continue
                self._contained_file(original)
                retained_id = record.id
                target = safe_child_path(self.root, ".retained", retained_id, record.filename)
                if await asyncio.to_thread(target.exists):
                    raise FileExistsError("managed retained target already exists")
                await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
                await asyncio.to_thread(os.replace, original, target)
                staged.append(QuarantinedArtifactFile(original=original, quarantine=target))
                relative = target.relative_to(self.root).as_posix()
                retained.append(
                    RetainedFile(
                        id=retained_id,
                        type=record.type,
                        filename=record.filename,
                        storage_key=relative,
                        mime_type=record.mime_type,
                        size=record.size,
                        checksum=record.checksum,
                        protected=protected,
                        retention_reason=reason,
                        expires_at=None if protected else record.expires_at,
                        created_at=record.created_at,
                    )
                )
        except Exception:
            await self._restore_without_masking(staged)
            raise
        return RetainedFileStage(files=tuple(staged), records=tuple(retained))

    async def _restore_without_masking(
        self,
        staged: Sequence[QuarantinedArtifactFile],
    ) -> None:
        try:
            await self._restore_quarantined_cleanup_unlocked(staged)
        except OSError:
            logger.critical(
                "Artifact transaction rollback could not restore retained files",
                extra={"event": "retained_file_restore_failed"},
            )

    async def rollback_retained_stage(self, stage: RetainedFileStage) -> None:
        async with self.mutation_guard():
            await self._restore_without_masking(stage.files)

    async def complete_retained_stage(self, stage: RetainedFileStage) -> None:
        async with self.mutation_guard():
            await self._complete_retained_stage_unlocked(stage)

    async def _complete_retained_stage_unlocked(self, stage: RetainedFileStage) -> None:
        for item in stage.files:
            await asyncio.to_thread(self._remove_empty_parents, item.original.parent)

    async def restore_quarantined_cleanup(
        self,
        staged: Sequence[QuarantinedArtifactFile],
    ) -> None:
        async with self.mutation_guard():
            await self._restore_quarantined_cleanup_unlocked(staged)

    async def _restore_quarantined_cleanup_unlocked(
        self,
        staged: Sequence[QuarantinedArtifactFile],
    ) -> None:
        failures = 0
        for item in reversed(staged):
            try:
                if not await asyncio.to_thread(item.quarantine.exists):
                    continue
                await asyncio.to_thread(item.original.parent.mkdir, parents=True, exist_ok=True)
                await asyncio.to_thread(os.replace, item.quarantine, item.original)
            except OSError:
                failures += 1
        for item in staged:
            await asyncio.to_thread(self._remove_empty_parents, item.quarantine.parent)
        if failures:
            raise OSError("one or more quarantined artifact files could not be restored")

    async def delivery(self, artifact_id: str, range_header: str | None) -> FileDelivery:
        try:
            record: Artifact | RetainedFile = await self._record(artifact_id)
        except AppError:
            record = await self._retained_record(artifact_id)
        path = self._artifact_path(record)
        actual_size = await asyncio.to_thread(self._validated_size, path)
        if actual_size is None:
            raise AppError(
                ErrorCode.RESOURCE_NOT_FOUND,
                "产物文件不存在或已被清理",
                action="删除失效记录，或重新创建任务",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if actual_size != record.size or actual_size <= 0:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "产物文件完整性状态异常",
                action="重新创建下载任务",
                status_code=status.HTTP_409_CONFLICT,
            )
        start, end, response_status = self._parse_range(range_header, actual_size)
        return FileDelivery(
            path=path,
            filename=record.filename,
            mime_type=record.mime_type,
            size=actual_size,
            start=start,
            end=end,
            status_code=response_status,
        )

    async def storage_status(self) -> StorageStatus:
        artifact_bytes, free_bytes, total_bytes = await asyncio.to_thread(self._storage_numbers)
        return StorageStatus(
            artifact_bytes=artifact_bytes,
            free_bytes=free_bytes,
            total_bytes=total_bytes,
        )

    async def cleanup_expired(self, *, now: datetime | None = None) -> int:
        async with self.mutation_guard():
            return await self._cleanup_expired_unlocked(now=now)

    async def _cleanup_expired_unlocked(self, *, now: datetime | None) -> int:
        cutoff = now or datetime.now(UTC)
        async with self.session_factory() as session:
            protected_ids = await self._active_artifact_references(session)
            artifact_filters = [
                Artifact.expires_at.is_not(None),
                Artifact.expires_at <= cutoff,
            ]
            if protected_ids:
                artifact_filters.append(Artifact.id.not_in(protected_ids))
            identifiers = list(
                (await session.scalars(select(Artifact.id).where(*artifact_filters))).all()
            )
            identifiers.extend(
                (
                    await session.scalars(
                        select(RetainedFile.id).where(
                            RetainedFile.expires_at.is_not(None),
                            RetainedFile.expires_at <= cutoff,
                        )
                    )
                ).all()
            )
        removed = 0
        for artifact_id in identifiers:
            result = await self._delete_unlocked(artifact_id, delete_file=True)
            if result.record_deleted:
                removed += 1
        return removed

    async def cleanup_older_than(self, *, older_than: datetime) -> int:
        async with self.mutation_guard():
            return await self._cleanup_older_than_unlocked(older_than=older_than)

    async def _cleanup_older_than_unlocked(self, *, older_than: datetime) -> int:
        terminal = {JobStatus.COMPLETED, JobStatus.CANCELED, JobStatus.FAILED}
        async with self.session_factory() as session:
            protected_ids = await self._active_artifact_references(session)
            artifact_filters = [
                Artifact.created_at < older_than,
                Artifact.job.has(Job.status.in_(terminal)),
            ]
            if protected_ids:
                artifact_filters.append(Artifact.id.not_in(protected_ids))
            identifiers = list(
                (await session.scalars(select(Artifact.id).where(*artifact_filters))).all()
            )
            identifiers.extend(
                (
                    await session.scalars(
                        select(RetainedFile.id).where(
                            RetainedFile.created_at < older_than,
                            RetainedFile.protected.is_(False),
                        )
                    )
                ).all()
            )
        removed = 0
        for artifact_id in identifiers:
            result = await self._delete_unlocked(artifact_id, delete_file=True)
            if result.record_deleted:
                removed += 1
        return removed

    async def cleanup_untracked(self, *, older_than: datetime) -> int:
        """Remove stale crash remnants while preserving files of active jobs."""

        async with self.mutation_guard():
            return await self._cleanup_untracked_unlocked(older_than=older_than)

    async def _cleanup_untracked_unlocked(self, *, older_than: datetime) -> int:

        active_statuses = {
            JobStatus.QUEUED,
            JobStatus.PREPARING,
            JobStatus.RUNNING,
            JobStatus.POST_PROCESSING,
            JobStatus.PAUSED,
        }
        async with self.session_factory() as session:
            tracked = set((await session.scalars(select(Artifact.storage_key))).all())
            tracked.update((await session.scalars(select(RetainedFile.storage_key))).all())
            active_jobs = set(
                (await session.scalars(select(Job.id).where(Job.status.in_(active_statuses)))).all()
            )
        return await asyncio.to_thread(
            self._cleanup_untracked_files,
            tracked,
            active_jobs,
            older_than.timestamp(),
        )

    def to_read(
        self,
        record: Artifact,
        *,
        job_input: dict[str, object] | None = None,
        job_status: JobStatus | None = None,
    ) -> ArtifactRead:
        payload = job_input
        if payload is None:
            loaded_job = record.__dict__.get("job")
            if isinstance(loaded_job, Job):
                payload = cast(dict[str, object], loaded_job.input_json)
                job_status = loaded_job.status
        payload = payload or {}
        return ArtifactRead(
            id=record.id,
            job_id=record.job_id,
            video_id=self._optional_display_value(payload.get("video_id")),
            video_title=self._optional_display_value(payload.get("video_title")),
            part_id=self._optional_display_value(payload.get("part_id")),
            part_title=self._optional_display_value(payload.get("part_title")),
            job_status=job_status,
            type=record.type,
            filename=record.filename,
            mime_type=record.mime_type,
            size=record.size,
            checksum=record.checksum,
            media_info=cast(dict[str, object] | None, record.media_info),
            expires_at=self._as_utc(record.expires_at),
            created_at=self._as_utc(record.created_at) or datetime.now(UTC),
            content_url=f"/api/v1/artifacts/{record.id}/content",
        )

    def to_read_retained(self, record: RetainedFile) -> ArtifactRead:
        return ArtifactRead(
            id=record.id,
            job_id=None,
            video_id=None,
            video_title=None,
            part_id=None,
            part_title=None,
            job_status=None,
            type=record.type,
            filename=record.filename,
            mime_type=record.mime_type,
            size=record.size,
            checksum=record.checksum,
            media_info=None,
            expires_at=self._as_utc(record.expires_at),
            created_at=self._as_utc(record.created_at) or datetime.now(UTC),
            content_url=f"/api/v1/artifacts/{record.id}/content",
            retained=True,
            protected=record.protected,
            retention_reason=record.retention_reason,
            retained_at=self._as_utc(record.retained_at),
        )

    async def _record(self, artifact_id: str) -> Artifact:
        async with self.session_factory() as session:
            record = await session.scalar(
                select(Artifact)
                .where(Artifact.id == artifact_id)
                .options(selectinload(Artifact.job))
            )
            if record is None:
                raise self._not_found()
            session.expunge(record)
            return record

    async def _retained_record(self, artifact_id: str) -> RetainedFile:
        async with self.session_factory() as session:
            record = await session.get(RetainedFile, artifact_id)
            if record is None:
                raise self._not_found()
            session.expunge(record)
            return record

    @staticmethod
    async def _active_artifact_references(session: AsyncSession) -> set[str]:
        active_statuses = {
            JobStatus.QUEUED,
            JobStatus.PREPARING,
            JobStatus.RUNNING,
            JobStatus.POST_PROCESSING,
            JobStatus.PAUSED,
        }
        payloads = (
            await session.scalars(select(Job.input_json).where(Job.status.in_(active_statuses)))
        ).all()
        identifiers: set[str] = set()
        for payload in payloads:
            if not isinstance(payload, Mapping):
                continue
            direct = payload.get("artifact_id")
            if isinstance(direct, str) and direct:
                identifiers.add(direct)
            many = payload.get("artifact_ids")
            if isinstance(many, list):
                identifiers.update(item for item in many if isinstance(item, str) and item)
            sources = payload.get("source_artifact_ids")
            if isinstance(sources, Mapping):
                identifiers.update(
                    item for item in sources.values() if isinstance(item, str) and item
                )
        return identifiers

    def _artifact_path(self, record: Artifact | RetainedFile) -> Path:
        return safe_child_path(self.root, *Path(record.storage_key).parts)

    def _contained_file(self, path: Path) -> Path:
        expanded = path.expanduser()
        if expanded.is_symlink():
            raise FileNotFoundError("Artifact file does not exist or is not a regular file")
        resolved = expanded.resolve()
        if self.root != resolved and self.root not in resolved.parents:
            raise ValueError("Artifact path is outside the storage root")
        if not resolved.is_file() or resolved.is_symlink():
            raise FileNotFoundError("Artifact file does not exist or is not a regular file")
        return resolved

    def _contained_target(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if self.root != resolved and self.root not in resolved.parents:
            raise ValueError("Artifact target is outside the storage root")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    @staticmethod
    def _parse_range(value: str | None, size: int) -> tuple[int, int, int]:
        if value is None:
            return 0, size - 1, status.HTTP_200_OK
        normalized = value.strip()
        if not normalized.lower().startswith("bytes=") or "," in normalized:
            raise RangeNotSatisfiable(size)
        specification = normalized[6:].strip()
        if "-" not in specification:
            raise RangeNotSatisfiable(size)
        raw_start, raw_end = specification.split("-", maxsplit=1)
        try:
            if len(raw_start) > 20 or len(raw_end) > 20:
                raise ValueError("range integer is too long")
            if raw_start:
                start = int(raw_start)
                end = int(raw_end) if raw_end else size - 1
                if start < 0 or end < start or start >= size:
                    raise RangeNotSatisfiable(size)
                end = min(end, size - 1)
            else:
                suffix = int(raw_end)
                if suffix <= 0:
                    raise RangeNotSatisfiable(size)
                suffix = min(suffix, size)
                start = size - suffix
                end = size - 1
        except ValueError as exc:
            raise RangeNotSatisfiable(size) from exc
        return start, end, status.HTTP_206_PARTIAL_CONTENT

    def _storage_numbers(self) -> tuple[int, int, int]:
        usage = shutil.disk_usage(self.root)
        artifact_bytes = 0
        for path in self.root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    artifact_bytes += path.stat().st_size
            except OSError:
                logger.warning(
                    "Artifact storage entry could not be inspected",
                    extra={"event": "artifact_storage_inspection_failed"},
                )
        return artifact_bytes, usage.free, usage.total

    def _cleanup_untracked_files(
        self,
        tracked: set[str],
        active_jobs: set[str],
        older_than_timestamp: float,
    ) -> int:
        removed = 0
        for path in self.root.rglob("*"):
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                resolved = path.resolve()
                if self.root != resolved and self.root not in resolved.parents:
                    continue
                relative = path.relative_to(self.root).as_posix()
                first_component = Path(relative).parts[0]
                if relative in tracked or first_component in active_jobs:
                    continue
                if path.stat().st_mtime >= older_than_timestamp:
                    continue
                path.unlink()
                self._remove_empty_parents(path.parent)
                removed += 1
            except OSError:
                logger.warning(
                    "Stale artifact file could not be removed",
                    extra={"event": "artifact_orphan_cleanup_failed"},
                )
        return removed

    def _remove_empty_parents(self, directory: Path) -> None:
        current = directory
        while current != self.root and self.root in current.parents:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    @staticmethod
    def _atomic_replace(staging: Path, final: Path) -> None:
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(staging, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("artifact staging path is not a regular file")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(staging, final)

    @staticmethod
    def _safe_retained_identifier(candidate: str, used: set[str]) -> str:
        try:
            parsed = str(uuid.UUID(candidate))
        except (ValueError, AttributeError):
            parsed = ""
        if parsed == candidate.lower() and candidate not in used:
            return candidate
        generated = str(uuid.uuid4())
        while generated in used:
            generated = str(uuid.uuid4())
        return generated

    @staticmethod
    def _retained_type(filename: str) -> str:
        suffix = Path(filename).suffix.lower()
        if suffix in {".mp4", ".mkv", ".webm", ".mov"}:
            return "video"
        if suffix in {".m4a", ".mp3", ".flac", ".wav", ".aac", ".ogg"}:
            return "audio"
        if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            return "cover"
        if suffix in {".srt", ".vtt", ".txt"}:
            return "transcript"
        if suffix in {".json", ".jsonl"}:
            return "metadata"
        if suffix in {".zip", ".tar", ".gz", ".7z"}:
            return "archive"
        return "report"

    @staticmethod
    def _validated_size(path: Path) -> int | None:
        if not path.is_file() or path.is_symlink():
            return None
        return path.stat().st_size

    @staticmethod
    def _file_has_size(path: Path, expected: int) -> bool:
        return path.is_file() and not path.is_symlink() and path.stat().st_size == expected

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            ErrorCode.RESOURCE_NOT_FOUND,
            "产物记录不存在",
            action="刷新产物列表后重试",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _optional_display_value(value: object) -> str | None:
        return value if isinstance(value, str) and value else None
