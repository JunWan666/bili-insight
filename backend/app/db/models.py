from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now


def new_id() -> str:
    return str(uuid.uuid4())


class AuthStatus(StrEnum):
    LOGGED_OUT = "logged_out"
    VALIDATING = "validating"
    AUTHENTICATED = "authenticated"
    PREMIUM = "premium"
    EXPIRED = "expired"
    ERROR = "error"


class AuthPersistence(StrEnum):
    SESSION = "session"
    LOCAL = "local"


class AppSetting(Base):
    """Single-row persisted preferences document.

    The database constraint deliberately makes the singleton invariant independent
    from application-process locking.
    """

    __tablename__ = "app_settings"
    __table_args__ = (CheckConstraint("id = 1", name="singleton_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class StreamKind(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"


class AccessContext(StrEnum):
    ANONYMOUS = "anonymous"
    AUTHENTICATED = "authenticated"


class StreamAccessRequirement(StrEnum):
    NONE = "none"
    LOGIN = "login"
    PREMIUM = "premium"
    SPECIAL = "special"


class JobType(StrEnum):
    DOWNLOAD = "download"
    ANALYSIS = "analysis"
    PACKAGE = "package"
    CLEANUP = "cleanup"


class JobStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    POST_PROCESSING = "post_processing"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"


class AuthProfile(Base):
    __tablename__ = "auth_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    encrypted_cookies: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[AuthStatus] = mapped_column(
        Enum(AuthStatus, native_enum=False, length=32), nullable=False
    )
    masked_account_name: Mapped[str | None] = mapped_column(String(128))
    membership_type: Mapped[str] = mapped_column(String(64), nullable=False, default="none")
    cookie_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        UniqueConstraint("provider", "bvid", name="uq_videos_provider_bvid"),
        UniqueConstraint("provider", "aid", name="uq_videos_provider_aid"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    bvid: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    aid: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cover_url: Mapped[str] = mapped_column(Text, nullable=False)
    owner_name: Mapped[str] = mapped_column(String(256), nullable=False)
    duration: Mapped[int] = mapped_column(Integer, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict[str, int | None]] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    rights: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    parts: Mapped[list[VideoPart]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
        order_by="VideoPart.page_number",
        lazy="selectin",
    )
    analyses: Mapped[list[Analysis]] = relationship(back_populates="video")


class VideoPart(Base):
    __tablename__ = "video_parts"
    __table_args__ = (
        UniqueConstraint("video_id", "cid", name="uq_video_parts_video_cid"),
        UniqueConstraint("video_id", "page_number", name="uq_video_parts_video_page"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    duration: Mapped[int] = mapped_column(Integer, nullable=False)

    video: Mapped[Video] = relationship(back_populates="parts")
    streams: Mapped[list[MediaStream]] = relationship(
        back_populates="part", cascade="all, delete-orphan", lazy="selectin"
    )
    analyses: Mapped[list[Analysis]] = relationship(back_populates="part")


class MediaStream(Base):
    __tablename__ = "media_streams"
    __table_args__ = (
        UniqueConstraint(
            "part_id",
            "access_context",
            "kind",
            "source_key",
            name="uq_media_streams_part_context_source",
        ),
        Index("ix_media_streams_part_context_kind", "part_id", "access_context", "kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    part_id: Mapped[str] = mapped_column(
        ForeignKey("video_parts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[StreamKind] = mapped_column(
        Enum(StreamKind, native_enum=False, length=16), nullable=False
    )
    access_context: Mapped[AccessContext] = mapped_column(
        Enum(AccessContext, native_enum=False, length=24), nullable=False
    )
    source_key: Mapped[str] = mapped_column(String(128), nullable=False)
    quality_code: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_label: Mapped[str] = mapped_column(String(64), nullable=False)
    codec: Mapped[str] = mapped_column(String(64), nullable=False)
    container: Mapped[str] = mapped_column(String(24), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    bitrate: Mapped[int | None] = mapped_column(Integer)
    hdr_type: Mapped[str | None] = mapped_column(String(32))
    audio_channels: Mapped[int | None] = mapped_column(Integer)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    estimated_size: Mapped[int | None] = mapped_column(BigInteger)
    auth_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    access_requirement: Mapped[StreamAccessRequirement] = mapped_column(
        Enum(StreamAccessRequirement, native_enum=False, length=24),
        nullable=False,
        default=StreamAccessRequirement.NONE,
    )
    compatibility: Mapped[str] = mapped_column(String(256), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )

    part: Mapped[VideoPart] = relationship(back_populates="streams")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    type: Mapped[JobType] = mapped_column(Enum(JobType, native_enum=False, length=24))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, native_enum=False, length=32))
    phase: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(512))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(768), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    media_info: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )

    job: Mapped[Job] = relationship(back_populates="artifacts")


class RetainedFile(Base):
    """A managed file whose privacy-bearing job metadata has been removed."""

    __tablename__ = "retained_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(768), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    retention_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    retained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (Index("ix_analyses_video_part_type", "video_id", "part_id", "analysis_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
    )
    part_id: Mapped[str | None] = mapped_column(ForeignKey("video_parts.id", ondelete="CASCADE"))
    analysis_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    model_name: Mapped[str | None] = mapped_column(String(128))
    model_version: Mapped[str | None] = mapped_column(String(128))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    video: Mapped[Video] = relationship(back_populates="analyses")
    part: Mapped[VideoPart | None] = relationship(back_populates="analyses")
