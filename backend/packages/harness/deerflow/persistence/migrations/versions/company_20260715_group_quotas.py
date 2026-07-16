"""Add model-group quota policies and per-user usage periods.

Revision ID: company_20260715_group_quotas
Revises: company_20260709_quota_control
Create Date: 2026-07-15
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

revision: str = "company_20260715_group_quotas"
down_revision: str | Sequence[str] | None = "company_20260709_quota_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep the legacy quota table untouched; any data backfill is operator-run.
    safe_create_table(
        "quota_scopes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("match_rules", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("period_type", sa.String(length=16), nullable=False),
        sa.Column("request_enforced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("request_limit", sa.BigInteger(), nullable=True),
        sa.Column("image_enforced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("image_limit", sa.BigInteger(), nullable=True),
        sa.Column("policy_version", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.CheckConstraint("resource_type IN ('model', 'image_generation')", name="ck_quota_scope_resource_type"),
        sa.CheckConstraint("period_type IN ('weekly', 'monthly')", name="ck_quota_scope_period_type"),
        sa.CheckConstraint("request_limit IS NULL OR request_limit >= 0", name="ck_quota_scope_request_limit"),
        sa.CheckConstraint("image_limit IS NULL OR image_limit >= 0", name="ck_quota_scope_image_limit"),
        sa.CheckConstraint("policy_version >= 1", name="ck_quota_scope_policy_version"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    safe_create_table(
        "user_quota_usage_periods",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("quota_scope_id", sa.String(length=36), nullable=False),
        sa.Column("period_type", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope_policy_version_snapshot", sa.BigInteger(), nullable=False),
        sa.Column("token_used", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("request_enforced_snapshot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("request_limit_snapshot", sa.BigInteger(), nullable=True),
        sa.Column("request_used", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("image_enforced_snapshot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("image_limit_snapshot", sa.BigInteger(), nullable=True),
        sa.Column("image_used", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_overridden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("overridden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("overridden_by", sa.String(length=64), nullable=True),
        sa.Column("override_reason", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.CheckConstraint("period_type IN ('weekly', 'monthly')", name="ck_user_quota_usage_period_type"),
        sa.CheckConstraint("token_used >= 0 AND request_used >= 0 AND image_used >= 0", name="ck_user_quota_usage_nonnegative"),
        sa.ForeignKeyConstraint(["quota_scope_id"], ["quota_scopes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "quota_scope_id",
            "period_type",
            "period_start",
            name="uq_user_quota_usage_period",
        ),
    )
    safe_create_index("ix_user_quota_usage_periods_user_id", "user_quota_usage_periods", ["user_id"])
    safe_create_index("ix_user_quota_usage_user_period", "user_quota_usage_periods", ["user_id", "period_start"])
    safe_create_index("ix_user_quota_usage_scope_period", "user_quota_usage_periods", ["quota_scope_id", "period_start"])
    safe_create_index(
        "ix_user_quota_usage_policy_sync",
        "user_quota_usage_periods",
        ["quota_scope_id", "period_start", "is_overridden"],
    )


def downgrade() -> None:
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
        sa.UniqueConstraint(
            "user_id",
            "period",
            "period_start",
            name="uq_user_quota_period_user_period_start",
        ),
    )
    safe_create_index("ix_user_quota_periods_user_id", "user_quota_periods", ["user_id"])
    safe_create_index("ix_user_quota_periods_period_start", "user_quota_periods", ["period_start"])

    safe_drop_index("ix_user_quota_usage_policy_sync", "user_quota_usage_periods")
    safe_drop_index("ix_user_quota_usage_scope_period", "user_quota_usage_periods")
    safe_drop_index("ix_user_quota_usage_user_period", "user_quota_usage_periods")
    safe_drop_index("ix_user_quota_usage_periods_user_id", "user_quota_usage_periods")
    safe_drop_table("user_quota_usage_periods")
    safe_drop_table("quota_scopes")
