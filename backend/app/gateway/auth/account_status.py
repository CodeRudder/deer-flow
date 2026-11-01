"""Helpers for enforcing the persisted user account state."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.gateway.auth.errors import AuthErrorCode, AuthErrorResponse
from app.gateway.auth.models import AccountStatus


def account_status_error_code(account_status: AccountStatus | str | None) -> AuthErrorCode | None:
    """Return the public auth error for a non-active account state."""
    if account_status == AccountStatus.PENDING:
        return AuthErrorCode.REGISTRATION_PENDING
    if account_status == AccountStatus.DISABLED:
        return AuthErrorCode.ACCOUNT_DISABLED
    return None


def ensure_account_active(user) -> None:
    """Raise a structured 403 unless *user* is active.

    Synthetic users used by auth-disabled/internal modes do not carry an
    ``account_status`` attribute and are intentionally treated as active.
    """
    error_code = account_status_error_code(getattr(user, "account_status", AccountStatus.ACTIVE))
    if error_code is None:
        return

    messages = {
        AuthErrorCode.REGISTRATION_PENDING: "Registration is waiting for administrator approval.",
        AuthErrorCode.ACCOUNT_DISABLED: "This account has been disabled.",
    }
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=AuthErrorResponse(code=error_code, message=messages[error_code]).model_dump(),
    )


def account_status_error_detail(user) -> dict | None:
    """Return the structured error detail without raising.

    This is useful for middleware/redirect adapters which need to translate a
    status failure into their own transport-level response.
    """
    error_code = account_status_error_code(getattr(user, "account_status", AccountStatus.ACTIVE))
    if error_code is None:
        return None
    message = "Registration is waiting for administrator approval." if error_code == AuthErrorCode.REGISTRATION_PENDING else "This account has been disabled."
    return AuthErrorResponse(code=error_code, message=message).model_dump()
