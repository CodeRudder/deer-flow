"""ORM models for model-group quota policies and per-user period usage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, false, text, true
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class QuotaScopeRow(Base):
    __tablename__ = "quota_scopes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    match_rules: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    period_type: Mapped[str] = mapped_column(String(16), nullable=False, default="weekly")
    request_enforced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    request_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    image_enforced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    image_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    policy_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint("resource_type IN ('model', 'image_generation')", name="ck_quota_scope_resource_type"),
        CheckConstraint("period_type IN ('weekly', 'monthly')", name="ck_quota_scope_period_type"),
        CheckConstraint("request_limit IS NULL OR request_limit >= 0", name="ck_quota_scope_request_limit"),
        CheckConstraint("image_limit IS NULL OR image_limit >= 0", name="ck_quota_scope_image_limit"),
        CheckConstraint("policy_version >= 1", name="ck_quota_scope_policy_version"),
    )


class UserQuotaUsagePeriodRow(Base):
    __tablename__ = "user_quota_usage_periods"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    quota_scope_id: Mapped[str] = mapped_column(String(36), ForeignKey("quota_scopes.id"), nullable=False)
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope_policy_version_snapshot: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    request_enforced_snapshot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    request_limit_snapshot: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    request_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    image_enforced_snapshot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    image_limit_snapshot: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    image_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    is_overridden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    overridden_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "quota_scope_id", "period_type", "period_start", name="uq_user_quota_usage_period"),
        Index("ix_user_quota_usage_user_period", "user_id", "period_start"),
        Index("ix_user_quota_usage_scope_period", "quota_scope_id", "period_start"),
        Index("ix_user_quota_usage_policy_sync", "quota_scope_id", "period_start", "is_overridden"),
        CheckConstraint("period_type IN ('weekly', 'monthly')", name="ck_user_quota_usage_period_type"),
        CheckConstraint("token_used >= 0 AND request_used >= 0 AND image_used >= 0", name="ck_user_quota_usage_nonnegative"),
    )
