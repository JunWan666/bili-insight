"""store non-sensitive DASH metadata used by browser previews

Revision ID: 0005_stream_preview_metadata
Revises: 0004_retained_files
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_stream_preview_metadata"
down_revision: str | None = "0004_retained_files"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_streams", schema=None) as batch_op:
        batch_op.add_column(sa.Column("mime_type", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("codec_string", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("init_range_start", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("init_range_end", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("index_range_start", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("index_range_end", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media_streams", schema=None) as batch_op:
        batch_op.drop_column("index_range_end")
        batch_op.drop_column("index_range_start")
        batch_op.drop_column("init_range_end")
        batch_op.drop_column("init_range_start")
        batch_op.drop_column("codec_string")
        batch_op.drop_column("mime_type")
