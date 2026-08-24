"""Add video-generation quota dimension.

Revision ID: company_20260820_video_quota
Revises: company_20260716_user_mgmt
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "company_20260820_video_quota"
down_revision: str | Sequence[str] | None = "company_20260716_user_mgmt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESOURCE_TYPE_CHECK = "resource_type IN ('model', 'image_generation', 'video_generation')"
_RESOURCE_TYPE_CHECK_PREVIOUS = "resource_type IN ('model', 'image_generation')"
_USAGE_NONNEGATIVE_CHECK = "token_used >= 0 AND request_used >= 0 AND image_used >= 0 AND video_used >= 0"
_USAGE_NONNEGATIVE_CHECK_PREVIOUS = "token_used >= 0 AND request_used >= 0 AND image_used >= 0"


def _check_sqltext(table: str, name: str) -> str | None:
    """Reflected, whitespace-normalized SQL of a named CHECK constraint."""
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return None
    for constraint in inspector.get_check_constraints(table):
        if constraint.get("name") == name:
            return " ".join(str(constraint.get("sqltext") or "").split())
    return None


def _upgrade_check_constraint(table: str, name: str, expression: str, new_marker: str) -> None:
    """Swap a named CHECK so it contains *new_marker*; create when absent; no-op when current."""
    sqltext = _check_sqltext(table, name)
    if sqltext is not None and new_marker in sqltext:
        return
    with op.batch_alter_table(table) as batch:
        if sqltext is not None:
            batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(name, expression)


def _downgrade_check_constraint(table: str, name: str, expression: str, removed_marker: str) -> None:
    """Swap a named CHECK back only while its SQL still contains *removed_marker*."""
    sqltext = _check_sqltext(table, name)
    if sqltext is None or removed_marker not in sqltext:
        return
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(name, expression)


def _drop_check_constraint(table: str, name: str) -> None:
    """Drop a named CHECK when it exists."""
    if _check_sqltext(table, name) is None:
        return
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(name, type_="check")


def upgrade() -> None:
    safe_add_column("quota_scopes", sa.Column("video_enforced", sa.Boolean(), nullable=False, server_default=sa.false()))
    safe_add_column("quota_scopes", sa.Column("video_limit", sa.BigInteger(), nullable=True))
    safe_add_column("user_quota_usage_periods", sa.Column("video_enforced_snapshot", sa.Boolean(), nullable=False, server_default=sa.false()))
    safe_add_column("user_quota_usage_periods", sa.Column("video_limit_snapshot", sa.BigInteger(), nullable=True))
    safe_add_column("user_quota_usage_periods", sa.Column("video_used", sa.BigInteger(), nullable=False, server_default=sa.text("0")))
    safe_add_column("runs", sa.Column("video_generation_count", sa.Integer(), nullable=False, server_default=sa.text("0")))
    _upgrade_check_constraint("quota_scopes", "ck_quota_scope_resource_type", _RESOURCE_TYPE_CHECK, "video_generation")
    _upgrade_check_constraint("quota_scopes", "ck_quota_scope_video_limit", "video_limit IS NULL OR video_limit >= 0", "video_limit")
    _upgrade_check_constraint("user_quota_usage_periods", "ck_user_quota_usage_nonnegative", _USAGE_NONNEGATIVE_CHECK, "video_used")


def downgrade() -> None:
    _downgrade_check_constraint("user_quota_usage_periods", "ck_user_quota_usage_nonnegative", _USAGE_NONNEGATIVE_CHECK_PREVIOUS, "video_used")
    # Must drop before the column it references, or the SQLite batch rebuild
    # renders a CHECK against a deleted column.
    _drop_check_constraint("quota_scopes", "ck_quota_scope_video_limit")
    _downgrade_check_constraint("quota_scopes", "ck_quota_scope_resource_type", _RESOURCE_TYPE_CHECK_PREVIOUS, "video_generation")
    safe_drop_column("runs", "video_generation_count")
    safe_drop_column("user_quota_usage_periods", "video_used")
    safe_drop_column("user_quota_usage_periods", "video_limit_snapshot")
    safe_drop_column("user_quota_usage_periods", "video_enforced_snapshot")
    safe_drop_column("quota_scopes", "video_limit")
    safe_drop_column("quota_scopes", "video_enforced")
