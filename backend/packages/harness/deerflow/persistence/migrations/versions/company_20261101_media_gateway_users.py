"""Add media_gateway_users mapping table (Media Gateway integration M1).

Revision ID: company_20261101_media_gw
Revises: company_20260902_quota_order
Create Date: 2026-11-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import (
    safe_create_index,
    safe_create_table,
    safe_drop_index,
    safe_drop_table,
)

revision: str = "company_20261101_media_gw"
down_revision: str | Sequence[str] | None = "company_20260902_quota_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_create_table(
        "media_gateway_users",
        sa.Column("deerflow_user_id", sa.String(length=36), nullable=False),
        sa.Column("mgw_user_id", sa.String(length=64), nullable=False),
        sa.Column("mgw_username", sa.String(length=32), nullable=False),
        sa.Column("mgw_token_enc", sa.Text(), nullable=False),
        sa.Column("mgw_token_sha8", sa.String(length=16), nullable=False),
        sa.Column("mgw_password_enc", sa.Text(), nullable=False),
        sa.Column("mgw_disabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("deerflow_user_id"),
        sa.UniqueConstraint("mgw_username", name="uq_media_gateway_users_username"),
    )
    safe_create_index("ix_media_gateway_users_username", "media_gateway_users", ["mgw_username"], unique=True)


def downgrade() -> None:
    safe_drop_index("ix_media_gateway_users_username")
    safe_drop_table("media_gateway_users")
