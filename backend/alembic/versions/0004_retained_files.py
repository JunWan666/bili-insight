"""manage files retained after privacy history removal

Revision ID: 0004_retained_files
Revises: 0003_stream_access_requirement
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_retained_files"
down_revision: str | None = "0003_stream_access_requirement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retained_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("storage_key", sa.String(length=768), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.Column("protected", sa.Boolean(), nullable=False),
        sa.Column("retention_reason", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retained_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retained_files")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_retained_files_storage_key")),
    )
    with op.batch_alter_table("retained_files", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_retained_files_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_retained_files_protected"), ["protected"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_retained_files_type"), ["type"], unique=False)


def downgrade() -> None:
    connection = op.get_bind()
    retained_count = int(
        connection.execute(sa.text("SELECT COUNT(*) FROM retained_files")).scalar_one()
    )
    if retained_count:
        raise RuntimeError(
            "cannot downgrade while managed retained files exist; delete or migrate them first"
        )
    with op.batch_alter_table("retained_files", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_retained_files_type"))
        batch_op.drop_index(batch_op.f("ix_retained_files_protected"))
        batch_op.drop_index(batch_op.f("ix_retained_files_created_at"))
    op.drop_table("retained_files")
