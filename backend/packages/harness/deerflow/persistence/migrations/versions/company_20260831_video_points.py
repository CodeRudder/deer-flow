"""Add video point-billing fields.

Video generation is billed in integer RMB fen (one point equals one yuan).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "company_20260831_video_points"
down_revision: str | Sequence[str] | None = "company_20260820_video_quota"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USAGE_NONNEGATIVE_CHECK = "token_used >= 0 AND request_used >= 0 AND image_used >= 0 AND video_used >= 0 AND video_reserved >= 0"
_USAGE_NONNEGATIVE_CHECK_PREVIOUS = "token_used >= 0 AND request_used >= 0 AND image_used >= 0 AND video_used >= 0"


def _check_sqltext(table: str, name: str) -> str | None:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return None
    for constraint in inspector.get_check_constraints(table):
        if constraint.get("name") == name:
            return " ".join(str(constraint.get("sqltext") or "").split())
    return None


def _ensure_check(table: str, name: str, expression: str, marker: str) -> None:
    """Create or replace a named CHECK, while tolerating repeated upgrades."""
    if _check_sqltext(table, name) is None:
        if table not in sa.inspect(op.get_bind()).get_table_names():
            return
        with op.batch_alter_table(table) as batch:
            batch.create_check_constraint(name, expression)
        return
    if expression in (_check_sqltext(table, name) or ""):
        return
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(name, expression)


def _drop_check(table: str, name: str) -> None:
    if _check_sqltext(table, name) is None:
        return
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(name, type_="check")


def _restore_nonnegative_check() -> None:
    sqltext = _check_sqltext("user_quota_usage_periods", "ck_user_quota_usage_nonnegative")
    if sqltext is None or "video_reserved" not in sqltext:
        return
    with op.batch_alter_table("user_quota_usage_periods") as batch:
        batch.drop_constraint("ck_user_quota_usage_nonnegative", type_="check")
        batch.create_check_constraint(
            "ck_user_quota_usage_nonnegative",
            _USAGE_NONNEGATIVE_CHECK_PREVIOUS,
        )


def upgrade() -> None:
    safe_add_column(
        "quota_scopes",
        sa.Column("video_billing_rules", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    safe_add_column(
        "user_quota_usage_periods",
        sa.Column("video_reserved", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    safe_add_column(
        "user_quota_usage_periods",
        sa.Column("video_billing_records", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    # Legacy values represented generation counts and cannot be converted to
    # money.  Disable those limits and reset their period aggregates so an
    # administrator can configure point limits and rates explicitly.
    op.execute(sa.text("UPDATE quota_scopes SET video_enforced = false, video_limit = NULL WHERE resource_type = 'video_generation'"))
    op.execute(
        sa.text(
            "UPDATE user_quota_usage_periods SET video_enforced_snapshot = false, "
            "video_limit_snapshot = NULL, video_used = 0 WHERE EXISTS ("
            "SELECT 1 FROM quota_scopes WHERE quota_scopes.id = "
            "user_quota_usage_periods.quota_scope_id AND quota_scopes.resource_type = 'video_generation')"
        )
    )
    _ensure_check(
        "user_quota_usage_periods",
        "ck_user_quota_usage_nonnegative",
        _USAGE_NONNEGATIVE_CHECK,
        "video_reserved",
    )


def downgrade() -> None:
    _restore_nonnegative_check()
    # Drop checks before their referenced columns for SQLite batch rebuilds.
    safe_drop_column("user_quota_usage_periods", "video_billing_records")
    safe_drop_column("user_quota_usage_periods", "video_reserved")
    safe_drop_column("quota_scopes", "video_billing_rules")
