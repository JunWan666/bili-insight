from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.artifacts import router as artifacts_router
from app.core.config import Settings
from app.core.exceptions import AppError, install_exception_handlers
from app.db.models import Artifact, Job, JobStatus, JobType, RetainedFile
from app.db.session import create_engine, create_schema, create_session_factory
from app.services.artifacts import ArtifactService, RangeNotSatisfiable


@pytest.fixture
async def artifact_environment(
    settings: Settings,
) -> tuple[ArtifactService, object, object]:
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    service = ArtifactService(settings, factory)
    yield service, factory, engine
    await engine.dispose()


async def create_job(factory: object, *, status: JobStatus = JobStatus.COMPLETED) -> Job:
    job = Job(
        type=JobType.DOWNLOAD,
        status=status,
        phase=status.value,
        progress=100 if status == JobStatus.COMPLETED else 25,
        input_json={
            "video_id": "video-id",
            "part_id": "part-id",
            "video_title": "可搜索的视频标题",
            "part_title": "第一集",
        },
        retry_count=0,
        cancel_requested=False,
    )
    async with factory() as session:  # type: ignore[operator]
        session.add(job)
        await session.commit()
        await session.refresh(job)
        session.expunge(job)
    return job


async def publish_bytes(
    service: ArtifactService,
    job: Job,
    payload: bytes = b"0123456789",
    *,
    artifact_type: str = "video",
    filename: str = "成品.mp4",
    expires_at: datetime | None = None,
) -> Artifact:
    directory = service.root / job.id
    directory.mkdir(parents=True, exist_ok=True)
    staging = directory / f".{job.id}.{artifact_type}.partial"
    staging.write_bytes(payload)
    return await service.publish(
        job_id=job.id,
        artifact_type=artifact_type,
        staging_path=staging,
        final_path=directory / filename,
        filename=filename,
        mime_type="video/mp4" if artifact_type == "video" else "application/json",
        media_info={"duration": 1.0} if artifact_type == "video" else None,
        expires_at=expires_at,
    )


async def test_publish_list_search_date_status_and_delivery(
    artifact_environment: tuple[ArtifactService, object, object],
) -> None:
    service, factory, _ = artifact_environment
    job = await create_job(factory)
    record = await publish_bytes(service, job)

    read = await service.get(record.id)
    assert read.video_id == "video-id"
    assert read.video_title == "可搜索的视频标题"
    assert read.part_title == "第一集"
    assert read.job_status == JobStatus.COMPLETED
    assert read.content_url.endswith(f"/{record.id}/content")
    assert read.checksum.startswith("sha256:")

    by_filename = await service.list(limit=10, offset=0, search="成品")
    by_title = await service.list(limit=10, offset=0, search="视频标题")
    by_status = await service.list(
        limit=10,
        offset=0,
        job_status=JobStatus.COMPLETED,
        created_from=read.created_at - timedelta(seconds=1),
        created_to=read.created_at + timedelta(seconds=1),
    )
    assert [item.id for item in by_filename.items] == [record.id]
    assert [item.id for item in by_title.items] == [record.id]
    assert [item.id for item in by_status.items] == [record.id]
    assert (await service.list(limit=10, offset=0, artifact_type="metadata")).total == 0
    assert (await service.list(limit=10, offset=0, job_id=job.id)).total == 1

    full = await service.delivery(record.id, None)
    partial = await service.delivery(record.id, "bytes=2-5")
    suffix = await service.delivery(record.id, "bytes=-3")
    open_end = await service.delivery(record.id, "bytes=7-")
    assert b"".join([chunk async for chunk in full.stream()]) == b"0123456789"
    assert b"".join([chunk async for chunk in partial.stream()]) == b"2345"
    assert b"".join([chunk async for chunk in suffix.stream()]) == b"789"
    assert b"".join([chunk async for chunk in open_end.stream()]) == b"789"
    assert partial.status_code == 206 and partial.length == 4

    with pytest.raises(AppError) as dates:
        await service.list(
            limit=10,
            offset=0,
            created_from=datetime.now(UTC),
            created_to=datetime.now(UTC) - timedelta(days=1),
        )
    assert dates.value.status_code == 422

    mixed_timezones = await service.list(
        limit=10,
        offset=0,
        created_from=(read.created_at - timedelta(seconds=1)).replace(tzinfo=None),
        created_to=read.created_at + timedelta(seconds=1),
    )
    assert [item.id for item in mixed_timezones.items] == [record.id]


@pytest.mark.parametrize(
    "value",
    [
        "items=0-1",
        "bytes=0-1,3-4",
        "bytes=",
        "bytes=9-1",
        "bytes=99-",
        "bytes=-0",
        f"bytes={'9' * 30}-",
    ],
)
def test_invalid_ranges_are_rejected(value: str) -> None:
    with pytest.raises(RangeNotSatisfiable):
        ArtifactService._parse_range(value, 10)


async def test_api_range_filters_storage_and_delete(
    artifact_environment: tuple[ArtifactService, object, object],
) -> None:
    service, factory, _ = artifact_environment
    job = await create_job(factory)
    record = await publish_bytes(service, job)
    app = FastAPI()
    app.state.container = SimpleNamespace(artifact_service=service)
    install_exception_handlers(app)
    app.include_router(artifacts_router, prefix="/api/v1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        listed = await client.get(
            "/api/v1/artifacts",
            params={
                "search": "视频标题",
                "jobStatus": "completed",
                "from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                "to": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        )
        assert listed.status_code == 200
        assert listed.json()["items"][0]["videoTitle"] == "可搜索的视频标题"

        detail = await client.get(f"/api/v1/artifacts/{record.id}")
        assert detail.status_code == 200
        ranged = await client.get(
            f"/api/v1/artifacts/{record.id}/content",
            headers={"Range": "bytes=1-3"},
        )
        assert ranged.status_code == 206
        assert ranged.content == b"123"
        assert ranged.headers["content-range"] == "bytes 1-3/10"
        assert "filename*=UTF-8''" in ranged.headers["content-disposition"]

        invalid = await client.get(
            f"/api/v1/artifacts/{record.id}/content",
            headers={"Range": "bytes=50-"},
        )
        assert invalid.status_code == 416
        assert invalid.headers["content-range"] == "bytes */10"

        storage = await client.get("/api/v1/artifacts/storage")
        assert storage.status_code == 200
        assert storage.json()["artifactBytes"] >= 10

        deleted = await client.delete(
            f"/api/v1/artifacts/{record.id}",
            params={"deleteFile": True},
        )
        assert deleted.status_code == 200
        assert deleted.json() == {
            "id": record.id,
            "recordDeleted": True,
            "fileDeleted": True,
            "retained": False,
        }


async def test_record_only_delete_moves_file_to_retained_namespace(
    artifact_environment: tuple[ArtifactService, object, object],
) -> None:
    service, factory, _ = artifact_environment
    job = await create_job(factory)
    record = await publish_bytes(service, job)
    original = service.root / record.storage_key

    result = await service.delete(record.id, delete_file=False)
    retained = service.root / ".retained" / record.id / record.filename
    assert result.record_deleted is True and result.file_deleted is False
    assert result.retained is True
    assert not original.exists()
    assert retained.read_bytes() == b"0123456789"

    old = datetime.now(UTC) - timedelta(days=30)
    os.utime(retained, (old.timestamp(), old.timestamp()))
    assert await service.cleanup_untracked(older_than=datetime.now(UTC)) == 0
    assert retained.exists()
    managed = await service.get(record.id)
    assert managed.retained is True
    assert managed.protected is True
    assert managed.job_id is None
    assert managed.video_title is None
    assert managed.media_info is None

    with pytest.raises(AppError) as unmanaged:
        await service.delete(record.id, delete_file=False)
    assert unmanaged.value.status_code == 409
    deleted = await service.delete(record.id, delete_file=True)
    assert deleted.retained is True
    assert deleted.file_deleted is True
    assert not retained.exists()


async def test_retained_file_remains_manageable_through_artifact_api(
    artifact_environment: tuple[ArtifactService, object, object],
) -> None:
    service, factory, _ = artifact_environment
    job = await create_job(factory)
    record = await publish_bytes(service, job, filename="managed-retained.mp4")
    app = FastAPI()
    app.state.container = SimpleNamespace(artifact_service=service)
    install_exception_handlers(app)
    app.include_router(artifacts_router, prefix="/api/v1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        retained = await client.delete(
            f"/api/v1/artifacts/{record.id}", params={"deleteFile": False}
        )
        assert retained.status_code == 200
        assert retained.json()["retained"] is True

        listed = await client.get("/api/v1/artifacts", params={"search": "managed-retained"})
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        item = listed.json()["items"][0]
        assert item["id"] == record.id
        assert item["retained"] is True
        assert item["protected"] is True
        assert item["jobId"] is None
        assert item["videoTitle"] is None
        assert item["mediaInfo"] is None

        content = await client.get(
            f"/api/v1/artifacts/{record.id}/content",
            headers={"Range": "bytes=2-4"},
        )
        assert content.status_code == 206
        assert content.content == b"234"
        rejected = await client.delete(
            f"/api/v1/artifacts/{record.id}", params={"deleteFile": False}
        )
        assert rejected.status_code == 409
        removed = await client.delete(f"/api/v1/artifacts/{record.id}", params={"deleteFile": True})
        assert removed.status_code == 200
        assert removed.json()["fileDeleted"] is True
        assert (await client.get(f"/api/v1/artifacts/{record.id}")).status_code == 404


async def test_startup_reconciles_legacy_and_interrupted_retained_moves(
    artifact_environment: tuple[ArtifactService, object, object],
) -> None:
    service, factory, _ = artifact_environment
    job = await create_job(factory)
    record = await publish_bytes(service, job, filename="recover.mp4")
    original = service.root / record.storage_key
    interrupted = service.root / ".retained" / record.id / record.filename
    interrupted.parent.mkdir(parents=True, exist_ok=True)
    os.replace(original, interrupted)

    legacy_id = str(uuid.uuid4())
    legacy = service.root / ".retained" / legacy_id / "legacy.flac"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"legacy-user-retained")
    result = await service.reconcile_retained_files()
    assert result == {"recovered": 1, "restoredArtifacts": 1}
    assert original.read_bytes() == b"0123456789"
    assert not interrupted.exists()
    retained = await service.get(legacy_id)
    assert retained.retained is True
    assert retained.protected is True
    assert retained.type == "audio"
    assert retained.retention_reason == "legacy_recovered"
    async with factory() as session:  # type: ignore[operator]
        assert await session.get(RetainedFile, legacy_id) is not None
    future = datetime.now(UTC) + timedelta(days=1)
    assert await service.cleanup_untracked(older_than=future) == 0
    assert legacy.exists()


async def test_reconciliation_does_not_shadow_a_live_artifact_identifier(
    artifact_environment: tuple[ArtifactService, object, object],
) -> None:
    service, factory, _ = artifact_environment
    job = await create_job(factory)
    record = await publish_bytes(service, job, filename="live.mp4")
    duplicate = service.root / ".retained" / record.id / "legacy-copy.mp4"
    duplicate.parent.mkdir(parents=True, exist_ok=True)
    duplicate.write_bytes(b"independently retained copy")

    result = await service.reconcile_retained_files()

    assert result == {"recovered": 1, "restoredArtifacts": 0}
    listed = await service.list(limit=10, offset=0)
    assert listed.total == 2
    assert len({item.id for item in listed.items}) == 2
    recovered = next(item for item in listed.items if item.retained)
    assert recovered.id != record.id
    assert (await service.delivery(record.id, None)).path.name == "live.mp4"
    assert (await service.delivery(recovered.id, None)).path.read_bytes() == (
        b"independently retained copy"
    )


async def test_record_only_retention_restores_file_and_record_when_commit_fails(
    artifact_environment: tuple[ArtifactService, object, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, factory, _ = artifact_environment
    job = await create_job(factory)
    record = await publish_bytes(service, job, filename="transactional.mp4")
    original = service.root / record.storage_key

    async def fail_commit(_: AsyncSession) -> None:
        raise RuntimeError("retained transaction failed")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="retained transaction failed"):
        await service.delete(record.id, delete_file=False)
    assert original.read_bytes() == b"0123456789"
    assert not (service.root / ".retained" / record.id / record.filename).exists()
    async with factory() as session:  # type: ignore[operator]
        assert await session.get(Artifact, record.id) is not None
        assert await session.get(RetainedFile, record.id) is None


async def test_cleanup_expired_untracked_and_active_files(
    artifact_environment: tuple[ArtifactService, object, object],
) -> None:
    service, factory, _ = artifact_environment
    completed = await create_job(factory)
    expired = await publish_bytes(
        service,
        completed,
        artifact_type="metadata",
        filename="expired.json",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert await service.cleanup_expired() == 1
    assert not (service.root / expired.storage_key).exists()

    active = await create_job(factory, status=JobStatus.RUNNING)
    active_file = service.root / active.id / "working.partial"
    active_file.parent.mkdir(parents=True, exist_ok=True)
    active_file.write_bytes(b"active")
    orphan = service.root / "orphan" / "stale.bin"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")
    old = datetime.now(UTC) - timedelta(days=2)
    os.utime(active_file, (old.timestamp(), old.timestamp()))
    os.utime(orphan, (old.timestamp(), old.timestamp()))

    assert await service.cleanup_untracked(older_than=datetime.now(UTC) - timedelta(days=1)) == 1
    assert active_file.exists()
    assert not orphan.exists()


async def test_stale_records_missing_files_and_publish_rollback(
    artifact_environment: tuple[ArtifactService, object, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, factory, _ = artifact_environment
    job = await create_job(factory)
    record = await publish_bytes(service, job)
    (service.root / record.storage_key).unlink()
    assert await service.existing_for_job(job.id, "video") is None
    async with factory() as session:  # type: ignore[operator]
        assert await session.get(Artifact, record.id) is None

    directory = service.root / job.id
    staging = directory / ".rollback.partial"
    final = directory / "rollback.bin"
    staging.write_bytes(b"data")

    async def fail_create(**_kwargs: object) -> Artifact:
        raise RuntimeError("fixed database failure")

    monkeypatch.setattr(service, "_create_from_file_unlocked", fail_create)
    with pytest.raises(RuntimeError):
        await service.publish(
            job_id=job.id,
            artifact_type="video",
            staging_path=staging,
            final_path=final,
            filename=final.name,
            mime_type="application/octet-stream",
            media_info=None,
        )
    assert not staging.exists() and not final.exists()


async def test_missing_and_corrupt_artifacts_are_actionable(
    artifact_environment: tuple[ArtifactService, object, object],
) -> None:
    service, factory, _ = artifact_environment
    with pytest.raises(AppError) as missing:
        await service.get("missing")
    assert missing.value.status_code == 404

    job = await create_job(factory)
    record = await publish_bytes(service, job)
    path = service.root / record.storage_key
    path.write_bytes(b"changed-size")
    with pytest.raises(AppError) as corrupt:
        await service.delivery(record.id, None)
    assert corrupt.value.status_code == 409

    path.unlink()
    with pytest.raises(AppError) as gone:
        await service.delivery(record.id, None)
    assert gone.value.status_code == 404


async def test_create_rejects_outside_and_symlink_files(
    artifact_environment: tuple[ArtifactService, object, object],
    tmp_path: Path,
) -> None:
    service, factory, _ = artifact_environment
    job = await create_job(factory)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"data")
    with pytest.raises(ValueError):
        await service.create_from_file(
            job_id=job.id,
            artifact_type="video",
            path=outside,
            filename="outside.bin",
            mime_type="application/octet-stream",
            media_info=None,
        )

    target = service.root / "target.bin"
    target.write_bytes(b"data")
    link = service.root / "link.bin"
    try:
        link.symlink_to(target)
    except OSError:
        return
    with pytest.raises(FileNotFoundError):
        await service.create_from_file(
            job_id=job.id,
            artifact_type="video",
            path=link,
            filename="link.bin",
            mime_type="application/octet-stream",
            media_info=None,
        )


async def test_list_order_and_existing_all_removes_stale(
    artifact_environment: tuple[ArtifactService, object, object],
) -> None:
    service, factory, _ = artifact_environment
    job = await create_job(factory)
    first = await publish_bytes(service, job, artifact_type="video", filename="first.mp4")
    second = await publish_bytes(
        service,
        job,
        payload=b"{}",
        artifact_type="metadata",
        filename="second.json",
    )
    (service.root / second.storage_key).unlink()
    existing = await service.existing_all_for_job(job.id)
    assert [item.id for item in existing] == [first.id]
    listed = await service.list(limit=1, offset=0)
    assert listed.total == 1 and len(listed.items) == 1
    async with factory() as session:  # type: ignore[operator]
        identifiers = set((await session.scalars(select(Artifact.id))).all())
    assert identifiers == {first.id}


async def test_reconfigure_root_only_when_storage_is_empty(
    artifact_environment: tuple[ArtifactService, object, object],
    tmp_path: Path,
) -> None:
    service, factory, _ = artifact_environment
    new_root = tmp_path / "new-artifacts"
    await service.reconfigure_root(new_root)
    assert service.root == new_root.resolve()

    job = await create_job(factory)
    record = await publish_bytes(service, job)
    with pytest.raises(AppError) as blocked:
        await service.reconfigure_root(tmp_path / "third-root")
    assert blocked.value.status_code == 409

    startup_service = object.__new__(ArtifactService)
    startup_service.root = tmp_path / "old-core-root"
    startup_service.session_factory = factory
    startup_service._mutation_lock = asyncio.Lock()
    startup_service._mutation_owner = None
    startup_service._mutation_depth = 0
    startup_service.root.mkdir(parents=True, exist_ok=True)
    await startup_service.reconfigure_root(new_root, startup=True)
    assert (await startup_service.get(record.id)).id == record.id
