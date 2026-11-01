"""Add admin quota-control storage.

Revision ID: company_20260709_quota_control
Revises: 0002_runs_token_usage
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import (
    safe_add_column,
    safe_create_index,
    safe_create_table,
    safe_drop_column,
    safe_drop_index,
    safe_drop_table,
)

revision: str = "company_20260709_quota_control"
down_revision: str | Sequence[str] | None = "0002_runs_token_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column(
        "runs",
        sa.Column("image_generation_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    safe_create_table(
        "user_quota_periods",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_tokens_limit", sa.Integer(), nullable=True),
        sa.Column("model_tokens_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("model_tokens_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("model_requests_limit", sa.Integer(), nullable=True),
        sa.Column("model_requests_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("model_requests_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("image_generations_limit", sa.Integer(), nullable=True),
        sa.Column("image_generations_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("image_generations_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "period", "period_start", name="uq_user_quota_period_user_period_start"),
    )
    safe_create_index("ix_user_quota_periods_user_id", "user_quota_periods", ["user_id"])
    safe_create_index("ix_user_quota_periods_period_start", "user_quota_periods", ["period_start"])
    safe_create_index("ix_runs_created_at", "runs", ["created_at"])
    safe_create_index("ix_runs_user_created_at", "runs", ["user_id", "created_at"])


def downgrade() -> None:
    safe_drop_index("ix_runs_user_created_at", "runs")
    safe_drop_index("ix_runs_created_at", "runs")
    safe_drop_index("ix_user_quota_periods_period_start", "user_quota_periods")
    safe_drop_index("ix_user_quota_periods_user_id", "user_quota_periods")
    safe_drop_table("user_quota_periods")
    safe_drop_column("runs", "image_generation_count")
