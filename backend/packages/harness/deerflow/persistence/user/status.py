"""Persisted user lifecycle status values shared across backend layers."""

from enum import StrEnum


class AccountStatus(StrEnum):
    """Whether a persisted user may authenticate and use the platform."""

    ACTIVE = "active"
    PENDING = "pending"
    DISABLED = "disabled"


class ApprovalEmailStatus(StrEnum):
    """Delivery state for the registration-approval notification."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


__all__ = ["AccountStatus", "ApprovalEmailStatus"]
