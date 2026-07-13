"""ORM model for monthly user quota periods."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, String, UniqueConstraint, text, true
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class UserQuotaPeriodRow(Base):
    __tablename__ = "user_quota_periods"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    model_tokens_limit: Mapped[int | None] = mapped_column(nullable=True)
    model_tokens_used: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))
    model_tokens_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())

    model_requests_limit: Mapped[int | None] = mapped_column(nullable=True)
    model_requests_used: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))
    model_requests_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())

    image_generations_limit: Mapped[int | None] = mapped_column(nullable=True)
    image_generations_used: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))
    image_generations_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "period", "period_start", name="uq_user_quota_period_user_period_start"),
        Index("ix_user_quota_periods_period_start", "period_start"),
    )
