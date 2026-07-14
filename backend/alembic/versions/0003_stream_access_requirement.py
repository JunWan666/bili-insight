"""record the entitlement required by each media stream

Revision ID: 0003_stream_access_requirement
Revises: 0002_app_settings
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_stream_access_requirement"
down_revision: str | None = "0002_app_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_requirement_type = sa.Enum(
    "NONE",
    "LOGIN",
    "PREMIUM",
    "SPECIAL",
    name="streamaccessrequirement",
    native_enum=False,
    length=24,
)


def upgrade() -> None:
    op.add_column(
        "media_streams",
        sa.Column("access_requirement", _requirement_type, nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE media_streams "
            "SET access_requirement = CASE "
            "WHEN auth_required = 1 THEN 'LOGIN' ELSE 'NONE' END"
        )
    )
    with op.batch_alter_table("media_streams", schema=None) as batch_op:
        batch_op.alter_column(
            "access_requirement",
            existing_type=_requirement_type,
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("media_streams", schema=None) as batch_op:
        batch_op.drop_column("access_requirement")
