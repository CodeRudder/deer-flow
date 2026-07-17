"""Add user account status and registration-approval metadata.

Revision ID: company_20260716_user_mgmt
Revises: company_20260715_group_quotas
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import (
    safe_add_column,
    safe_create_index,
    safe_drop_column,
    safe_drop_index,
)

revision: str = "company_20260716_user_mgmt"
down_revision: str | Sequence[str] | None = "company_20260715_group_quotas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column(
        "users",
        sa.Column(
            "account_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
    )
    safe_add_column(
        "users",
        sa.Column("registration_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    safe_add_column(
        "users",
        sa.Column("registration_approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    safe_add_column(
        "users",
        sa.Column("registration_approved_by", sa.String(length=36), nullable=True),
    )
    safe_add_column(
        "users",
        sa.Column("approval_email_status", sa.String(length=16), nullable=True),
    )
    safe_add_column(
        "users",
        sa.Column("approval_email_last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    safe_create_index(
        "idx_users_account_status_created",
        "users",
        ["account_status", "created_at", "id"],
    )
    safe_create_index(
        "idx_users_pending_queue",
        "users",
        ["account_status", "registration_requested_at", "id"],
    )


def downgrade() -> None:
    safe_drop_index("idx_users_pending_queue", "users")
    safe_drop_index("idx_users_account_status_created", "users")
    safe_drop_column("users", "approval_email_last_attempt_at")
    safe_drop_column("users", "approval_email_status")
    safe_drop_column("users", "registration_approved_by")
    safe_drop_column("users", "registration_approved_at")
    safe_drop_column("users", "registration_requested_at")
    safe_drop_column("users", "account_status")
