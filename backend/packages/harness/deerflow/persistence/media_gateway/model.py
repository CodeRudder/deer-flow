"""ORM model mapping DeerFlow users to Media Gateway (MGW) accounts.

One row per DeerFlow user who has touched a media feature. MGW tokens and
the generated fallback password are stored encrypted (Fernet, see
``deerflow.media_gateway.crypto``) — never in cleartext.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MediaGatewayUserRow(Base):
    __tablename__ = "media_gateway_users"

    # DeerFlow user id (UUID string) — primary key, one MGW account per user.
    deerflow_user_id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # MGW-side identifiers returned by POST /v1/users.
    mgw_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mgw_username: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)

    # Encrypted per-user API token (h3t_, plaintext shown exactly once by MGW)
    # and the generated console-fallback password.
    mgw_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    mgw_token_sha8: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    mgw_password_enc: Mapped[str] = mapped_column(Text, nullable=False)

    # Whether the MGW account has been disabled via the DeerFlow disable flow.
    mgw_disabled: Mapped[bool] = mapped_column(nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)
