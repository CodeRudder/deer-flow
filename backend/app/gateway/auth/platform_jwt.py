"""JWT parsing for tokens injected by the company gateway."""

from __future__ import annotations

from typing import Any

import jwt
from pydantic import BaseModel, Field

from deerflow.config.platform_auth_config import PlatformAuthConfig


class PlatformTokenError(Exception):
    """Raised when the gateway JWT is missing or invalid."""


class PlatformUserClaims(BaseModel):
    """Normalized user identity extracted from a company gateway JWT."""

    sub: str
    email: str
    preferred_username: str = ""
    name: str = ""
    org_id: str = ""
    org_name: str = ""
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    auth_source: str = ""
    session_id: str = ""


def _list_claim(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _string_claim(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return "" if value is None else str(value).strip()


def parse_platform_user_claims(token: str, config: PlatformAuthConfig) -> PlatformUserClaims:
    """Verify a company gateway JWT and return normalized user claims."""
    if not token:
        raise PlatformTokenError("Missing platform JWT")
    try:
        payload = jwt.decode(
            token,
            config.normalized_public_key,
            algorithms=[config.algorithm],
            issuer=config.issuer,
            audience=config.audience,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise PlatformTokenError("Token expired") from exc
    except jwt.InvalidIssuerError as exc:
        raise PlatformTokenError("Token issuer mismatch") from exc
    except jwt.InvalidAudienceError as exc:
        raise PlatformTokenError("Token audience mismatch") from exc
    except jwt.PyJWTError as exc:
        raise PlatformTokenError("Token invalid") from exc

    sub = _string_claim(payload, "sub")
    if not sub:
        raise PlatformTokenError("Token missing sub")
    name = _string_claim(payload, "name")
    if not name:
        raise PlatformTokenError("Token missing name")
    email = _string_claim(payload, "email")
    if not email:
        email = f"{name}@sz-jlc.com"

    return PlatformUserClaims(
        sub=sub,
        email=email,
        preferred_username=_string_claim(payload, "preferred_username"),
        name=name,
        org_id=_string_claim(payload, "org_id"),
        org_name=_string_claim(payload, "org_name"),
        roles=_list_claim(payload, "roles"),
        permissions=_list_claim(payload, "permissions"),
        auth_source=_string_claim(payload, "auth_source"),
        session_id=_string_claim(payload, "session_id"),
    )
