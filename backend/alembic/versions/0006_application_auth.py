"""add local application administrator and sessions

Revision ID: 0006_application_auth
Revises: 0005_stream_preview_metadata
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_application_auth"
down_revision: str | None = "0005_stream_preview_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_users_username", "app_users", ["username"], unique=True)
    op.create_table(
        "app_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_sessions_user_id", "app_sessions", ["user_id"], unique=False)
    op.create_index(
        "ix_app_sessions_token_hash", "app_sessions", ["token_hash"], unique=True
    )
    op.create_index(
        "ix_app_sessions_expires_at", "app_sessions", ["expires_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_app_sessions_expires_at", table_name="app_sessions")
    op.drop_index("ix_app_sessions_token_hash", table_name="app_sessions")
    op.drop_index("ix_app_sessions_user_id", table_name="app_sessions")
    op.drop_table("app_sessions")
    op.drop_index("ix_app_users_username", table_name="app_users")
    op.drop_table("app_users")
