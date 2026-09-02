"""Add daily video-quota policy and per-date usage buckets (feat30).

Daily buckets live in the parent usage row's ``daily_usage`` JSON keyed by the
reservation's Asia/Shanghai local date; the scope carries the generic daily
policy (``daily_enforced`` / ``daily_limit``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "company_20260902_quota_order"
down_revision: str | Sequence[str] | None = "company_20260831_video_points"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCOPE_DAILY_LIMIT_CHECK = "daily_limit IS NULL OR daily_limit >= 0"


def _check_exists(table: str, name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return any(constraint.get("name") == name for constraint in inspector.get_check_constraints(table))


def _ensure_scope_daily_limit_check() -> None:
    if _check_exists("quota_scopes", "ck_quota_scope_daily_limit"):
        return
    if "quota_scopes" not in sa.inspect(op.get_bind()).get_table_names():
        return
    with op.batch_alter_table("quota_scopes") as batch:
        batch.create_check_constraint("ck_quota_scope_daily_limit", _SCOPE_DAILY_LIMIT_CHECK)


def upgrade() -> None:
    safe_add_column(
        "quota_scopes",
        sa.Column("daily_enforced", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    safe_add_column(
        "quota_scopes",
        sa.Column("daily_limit", sa.BigInteger(), nullable=True),
    )
    safe_add_column(
        "user_quota_usage_periods",
        sa.Column("daily_enforced_snapshot", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    safe_add_column(
        "user_quota_usage_periods",
        sa.Column("daily_limit_snapshot", sa.BigInteger(), nullable=True),
    )
    safe_add_column(
        "user_quota_usage_periods",
        sa.Column("daily_usage", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    _ensure_scope_daily_limit_check()


def downgrade() -> None:
    # Drop checks before their referenced columns for SQLite batch rebuilds.
    if _check_exists("quota_scopes", "ck_quota_scope_daily_limit"):
        with op.batch_alter_table("quota_scopes") as batch:
            batch.drop_constraint("ck_quota_scope_daily_limit", type_="check")
    safe_drop_column("user_quota_usage_periods", "daily_usage")
    safe_drop_column("user_quota_usage_periods", "daily_limit_snapshot")
    safe_drop_column("user_quota_usage_periods", "daily_enforced_snapshot")
    safe_drop_column("quota_scopes", "daily_limit")
    safe_drop_column("quota_scopes", "daily_enforced")
