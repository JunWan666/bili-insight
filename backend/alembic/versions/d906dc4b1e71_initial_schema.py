"""initial_schema

Revision ID: d906dc4b1e71
Revises:
Create Date: 2026-07-14 04:38:54.255318
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d906dc4b1e71"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("encrypted_cookies", sa.LargeBinary(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "LOGGED_OUT",
                "VALIDATING",
                "AUTHENTICATED",
                "PREMIUM",
                "EXPIRED",
                "ERROR",
                name="authstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("masked_account_name", sa.String(length=128), nullable=True),
        sa.Column("membership_type", sa.String(length=64), nullable=False),
        sa.Column("cookie_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_profiles")),
    )
    with op.batch_alter_table("auth_profiles", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_auth_profiles_provider"), ["provider"], unique=True)

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "DOWNLOAD",
                "ANALYSIS",
                "PACKAGE",
                "CLEANUP",
                name="jobtype",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "QUEUED",
                "PREPARING",
                "RUNNING",
                "POST_PROCESSING",
                "PAUSED",
                "COMPLETED",
                "CANCELED",
                "FAILED",
                name="jobstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_jobs_created_at"), ["created_at"], unique=False)
        batch_op.create_index("ix_jobs_status_created", ["status", "created_at"], unique=False)

    op.create_table(
        "videos",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("bvid", sa.String(length=16), nullable=False),
        sa.Column("aid", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("cover_url", sa.Text(), nullable=False),
        sa.Column("owner_name", sa.String(length=256), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("rights", sa.JSON(), nullable=False),
        sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_videos")),
        sa.UniqueConstraint("provider", "aid", name="uq_videos_provider_aid"),
        sa.UniqueConstraint("provider", "bvid", name="uq_videos_provider_bvid"),
    )
    with op.batch_alter_table("videos", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_videos_aid"), ["aid"], unique=False)
        batch_op.create_index(batch_op.f("ix_videos_bvid"), ["bvid"], unique=False)
        batch_op.create_index(batch_op.f("ix_videos_parsed_at"), ["parsed_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_videos_provider"), ["provider"], unique=False)

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("storage_key", sa.String(length=768), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.Column("media_info", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_artifacts_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifacts")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_artifacts_storage_key")),
    )
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_artifacts_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_artifacts_job_id"), ["job_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_artifacts_type"), ["type"], unique=False)

    op.create_table(
        "video_parts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("video_id", sa.String(length=36), nullable=False),
        sa.Column("cid", sa.BigInteger(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["videos.id"],
            name=op.f("fk_video_parts_video_id_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_parts")),
        sa.UniqueConstraint("video_id", "cid", name="uq_video_parts_video_cid"),
        sa.UniqueConstraint("video_id", "page_number", name="uq_video_parts_video_page"),
    )
    with op.batch_alter_table("video_parts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_video_parts_video_id"), ["video_id"], unique=False)

    op.create_table(
        "analyses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("video_id", sa.String(length=36), nullable=False),
        sa.Column("part_id", sa.String(length=36), nullable=True),
        sa.Column("analysis_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("model_version", sa.String(length=128), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["part_id"],
            ["video_parts.id"],
            name=op.f("fk_analyses_part_id_video_parts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["videos.id"],
            name=op.f("fk_analyses_video_id_videos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analyses")),
    )
    with op.batch_alter_table("analyses", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_analyses_created_at"), ["created_at"], unique=False)
        batch_op.create_index(
            "ix_analyses_video_part_type", ["video_id", "part_id", "analysis_type"], unique=False
        )

    op.create_table(
        "media_streams",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("part_id", sa.String(length=36), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("VIDEO", "AUDIO", name="streamkind", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column(
            "access_context",
            sa.Enum(
                "ANONYMOUS", "AUTHENTICATED", name="accesscontext", native_enum=False, length=24
            ),
            nullable=False,
        ),
        sa.Column("source_key", sa.String(length=128), nullable=False),
        sa.Column("quality_code", sa.Integer(), nullable=False),
        sa.Column("quality_label", sa.String(length=64), nullable=False),
        sa.Column("codec", sa.String(length=64), nullable=False),
        sa.Column("container", sa.String(length=24), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("fps", sa.Float(), nullable=True),
        sa.Column("bitrate", sa.Integer(), nullable=True),
        sa.Column("hdr_type", sa.String(length=32), nullable=True),
        sa.Column("audio_channels", sa.Integer(), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column("estimated_size", sa.BigInteger(), nullable=True),
        sa.Column("auth_required", sa.Boolean(), nullable=False),
        sa.Column("compatibility", sa.String(length=256), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["part_id"],
            ["video_parts.id"],
            name=op.f("fk_media_streams_part_id_video_parts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_streams")),
        sa.UniqueConstraint(
            "part_id",
            "access_context",
            "kind",
            "source_key",
            name="uq_media_streams_part_context_source",
        ),
    )
    with op.batch_alter_table("media_streams", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_media_streams_fetched_at"), ["fetched_at"], unique=False
        )
        batch_op.create_index(
            "ix_media_streams_part_context_kind",
            ["part_id", "access_context", "kind"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("media_streams", schema=None) as batch_op:
        batch_op.drop_index("ix_media_streams_part_context_kind")
        batch_op.drop_index(batch_op.f("ix_media_streams_fetched_at"))

    op.drop_table("media_streams")
    with op.batch_alter_table("analyses", schema=None) as batch_op:
        batch_op.drop_index("ix_analyses_video_part_type")
        batch_op.drop_index(batch_op.f("ix_analyses_created_at"))

    op.drop_table("analyses")
    with op.batch_alter_table("video_parts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_video_parts_video_id"))

    op.drop_table("video_parts")
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artifacts_type"))
        batch_op.drop_index(batch_op.f("ix_artifacts_job_id"))
        batch_op.drop_index(batch_op.f("ix_artifacts_created_at"))

    op.drop_table("artifacts")
    with op.batch_alter_table("videos", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_videos_provider"))
        batch_op.drop_index(batch_op.f("ix_videos_parsed_at"))
        batch_op.drop_index(batch_op.f("ix_videos_bvid"))
        batch_op.drop_index(batch_op.f("ix_videos_aid"))

    op.drop_table("videos")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_jobs_status_created")
        batch_op.drop_index(batch_op.f("ix_jobs_created_at"))

    op.drop_table("jobs")
    with op.batch_alter_table("auth_profiles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_auth_profiles_provider"))

    op.drop_table("auth_profiles")
