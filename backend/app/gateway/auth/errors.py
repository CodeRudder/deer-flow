"""Typed error definitions for auth module.

AuthErrorCode: exhaustive enum of all auth failure conditions.
TokenError: exhaustive enum of JWT decode failures.
AuthErrorResponse: structured error payload for HTTP responses.
"""

from enum import StrEnum

from pydantic import BaseModel


class AuthErrorCode(StrEnum):
    """Exhaustive list of auth error conditions."""

    INVALID_CREDENTIALS = "invalid_credentials"
    TOKEN_EXPIRED = "token_expired"
    TOKEN_INVALID = "token_invalid"
    USER_NOT_FOUND = "user_not_found"
    EMAIL_ALREADY_EXISTS = "email_already_exists"
    EMAIL_DOMAIN_NOT_ALLOWED = "email_domain_not_allowed"
    PROVIDER_NOT_FOUND = "provider_not_found"
    NOT_AUTHENTICATED = "not_authenticated"
    SYSTEM_ALREADY_INITIALIZED = "system_already_initialized"
    REGISTRATION_PENDING = "registration_pending"
    ACCOUNT_DISABLED = "account_disabled"
    INVALID_ACCOUNT_STATUS_TRANSITION = "invalid_account_status_transition"
    ADMIN_STATUS_CHANGE_NOT_ALLOWED = "admin_status_change_not_allowed"
    ADMIN_PROFILE_EDIT_NOT_ALLOWED = "admin_profile_edit_not_allowed"
    EXTERNAL_IDENTITY_EMAIL_NOT_EDITABLE = "external_identity_email_not_editable"
    APPROVAL_EMAIL_NOT_RETRYABLE = "approval_email_not_retryable"
    APPROVAL_EMAIL_IN_PROGRESS = "approval_email_in_progress"
    USER_MANAGEMENT_UNAVAILABLE = "user_management_unavailable"


class TokenError(StrEnum):
    """Exhaustive list of JWT decode failure reasons."""

    EXPIRED = "expired"
    INVALID_SIGNATURE = "invalid_signature"
    MALFORMED = "malformed"


class AuthErrorResponse(BaseModel):
    """Structured error response — replaces bare `detail` strings."""

    code: AuthErrorCode
    message: str


def token_error_to_code(err: TokenError) -> AuthErrorCode:
    """Map TokenError to AuthErrorCode — single source of truth."""
    if err == TokenError.EXPIRED:
        return AuthErrorCode.TOKEN_EXPIRED
    return AuthErrorCode.TOKEN_INVALID
