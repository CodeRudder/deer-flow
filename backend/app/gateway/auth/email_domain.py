"""Email-domain authorization policy for local authentication flows."""

from collections.abc import Collection

from fastapi import HTTPException, status

from app.gateway.auth.errors import AuthErrorCode, AuthErrorResponse


def is_email_domain_allowed(email: str, allowed_domains: Collection[str]) -> bool:
    """Return whether an email's complete normalized domain is allowed."""
    if email != email.strip() or email.count("@") != 1:
        return False

    local_part, domain = email.rsplit("@", maxsplit=1)
    if not local_part or not domain:
        return False

    try:
        normalized_domain = domain.lower().encode("ascii").decode("ascii")
    except UnicodeError:
        return False

    if any(label.startswith("xn--") for label in normalized_domain.split(".")):
        return False

    return normalized_domain in allowed_domains


def enforce_email_domain_allowed(email: str, allowed_domains: Collection[str]) -> None:
    """Raise a structured HTTP 403 when an email domain is not allowed."""
    if is_email_domain_allowed(email, allowed_domains):
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=AuthErrorResponse(
            code=AuthErrorCode.EMAIL_DOMAIN_NOT_ALLOWED,
            message="请使用公司邮箱注册",
        ).model_dump(),
    )
