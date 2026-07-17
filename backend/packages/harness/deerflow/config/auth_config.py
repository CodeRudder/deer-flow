"""OIDC / SSO authentication configuration models."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

_EMAIL_LOCAL_PART_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")


def _normalize_email_domain(domain: str) -> str:
    candidate = domain.strip()
    if candidate.startswith("@"):
        candidate = candidate[1:]
    candidate = candidate.lower()

    if not candidate or "@" in candidate:
        raise ValueError("allowed_email_domains entries must be a valid email domain")

    try:
        ascii_domain = candidate.encode("ascii").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("allowed_email_domains entries must be a valid email domain") from exc

    labels = ascii_domain.split(".")
    if len(ascii_domain) > 253 or any(label.startswith("xn--") or not _is_valid_dns_label(label) for label in labels):
        raise ValueError("allowed_email_domains entries must be a valid email domain")

    return ascii_domain


def _is_valid_dns_label(label: str) -> bool:
    if not 1 <= len(label) <= 63 or not label[0].isalnum() or not label[-1].isalnum():
        return False
    return all(character.isalnum() or character == "-" for character in label)


class OIDCProviderConfig(BaseModel):
    """Configuration for a single OIDC identity provider (Keycloak, Google, Azure AD, etc.)."""

    display_name: str = Field(description="Human-readable name shown on the login button")
    issuer: str = Field(description="OIDC issuer URL (e.g. https://keycloak.example.com/realms/deerflow)")
    client_id: str = Field(description="OAuth2 client ID assigned by the provider")
    client_secret: str | None = Field(default=None, description="OAuth2 client secret ($ENV_VAR references supported)")
    redirect_uri: str | None = Field(default=None, description="Callback URL the provider will redirect to after auth")
    scopes: list[str] = Field(
        default_factory=lambda: ["openid", "email", "profile"],
        description="OIDC scopes to request (must include openid)",
    )
    token_endpoint_auth_method: Literal["client_secret_post", "client_secret_basic", "none"] = Field(
        default="client_secret_post",
        description="How the client authenticates at the token endpoint",
    )

    # ── User provisioning ─────────────────────────────────────────────
    auto_create_users: bool = Field(
        default=True,
        description="Automatically create a DeerFlow user on first SSO login",
    )
    require_verified_email: bool = Field(
        default=True,
        description="Reject authentication if the provider does not report the email as verified",
    )
    allowed_email_domains: list[str] = Field(
        default_factory=list,
        description="If non-empty, only allow users whose email domain is in this list (e.g. ['example.com'])",
    )
    admin_emails: list[str] = Field(
        default_factory=list,
        description="Users with these email addresses are automatically granted the admin role on first login",
    )

    # ── PKCE / nonce ──────────────────────────────────────────────────
    pkce_enabled: bool = Field(default=True, description="Enable PKCE (S256) for the authorization code flow")
    nonce_enabled: bool = Field(default=True, description="Include and validate the nonce claim in ID tokens")

    # ── Endpoint overrides (for providers with non-standard discovery) ─
    authorization_endpoint: str | None = Field(default=None)
    token_endpoint: str | None = Field(default=None)
    userinfo_endpoint: str | None = Field(default=None)
    jwks_uri: str | None = Field(default=None)


class OIDCAuthConfig(BaseModel):
    """Top-level OIDC authentication configuration."""

    enabled: bool = Field(default=False, description="Enable OIDC SSO authentication")
    frontend_base_url: str | None = Field(
        default=None,
        description="Base URL of the frontend (used for callback redirects when behind a reverse proxy)",
    )
    providers: dict[str, OIDCProviderConfig] = Field(
        default_factory=dict,
        description="Map of provider IDs to their configuration (e.g. keycloak, google, azure)",
    )


class SMTPConfig(BaseModel):
    """Implicit-TLS SMTP connection settings for registration notifications."""

    host: str = Field(description="SMTP server hostname")
    port: int = Field(default=465, ge=1, le=65535)
    security: Literal["ssl"] = "ssl"
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    timeout_seconds: int = Field(default=10, ge=1, le=30)

    @field_validator("host", "username")
    @classmethod
    def _validate_nonempty_connection_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "\r" in normalized or "\n" in normalized:
            raise ValueError("SMTP host and username must be non-empty single-line values")
        return normalized


class ApprovalEmailConfig(BaseModel):
    """Email notification settings used after an administrator approves a user."""

    enabled: bool = False
    login_url: str | None = None
    from_name: str = "DeerFlow"
    from_address: str | None = None
    smtp: SMTPConfig | None = None

    @field_validator("from_name")
    @classmethod
    def _validate_from_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "\r" in value or "\n" in value:
            raise ValueError("approval email from_name must be a non-empty single-line value")
        return normalized

    @field_validator("from_address")
    @classmethod
    def _validate_from_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip()
        if "\r" in candidate or "\n" in candidate or candidate.count("@") != 1:
            raise ValueError("approval email from_address must be a valid email address")
        local_part, domain = candidate.rsplit("@", 1)
        if not local_part or len(local_part) > 64 or local_part.startswith(".") or local_part.endswith(".") or ".." in local_part or _EMAIL_LOCAL_PART_RE.fullmatch(local_part) is None:
            raise ValueError("approval email from_address must be a valid email address")
        try:
            normalized_domain = _normalize_email_domain(domain)
        except ValueError as exc:
            raise ValueError("approval email from_address must be a valid email address") from exc
        return f"{local_part}@{normalized_domain}"

    @field_validator("login_url")
    @classmethod
    def _validate_login_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if "\r" in normalized or "\n" in normalized:
            raise ValueError("approval email login_url must be a single-line value")
        return normalized or None

    @model_validator(mode="after")
    def _require_delivery_settings_when_enabled(self) -> ApprovalEmailConfig:
        if self.enabled and (self.from_address is None or self.smtp is None):
            raise ValueError("enabled approval_email requires from_address and smtp settings")
        return self


class LocalRegistrationConfig(BaseModel):
    """Local email/password registration workflow settings."""

    require_admin_approval: bool = False
    approval_email: ApprovalEmailConfig = Field(default_factory=ApprovalEmailConfig)

    @model_validator(mode="after")
    def _require_email_when_approval_enabled(self) -> LocalRegistrationConfig:
        if self.require_admin_approval and not self.approval_email.enabled:
            raise ValueError("require_admin_approval requires approval_email.enabled=true")
        return self


class AuthAppConfig(BaseModel):
    """Authentication configuration section for the DeerFlow app config."""

    allowed_email_domains: list[str] = Field(
        default_factory=lambda: ["sz-jlc.com"],
        description="Email domains allowed for local registration and email changes",
    )
    enforce_email_domain_on_login: bool = Field(
        default=False,
        description="Enforce allowed_email_domains after successful local email/password authentication",
    )
    local_registration: LocalRegistrationConfig = Field(
        default_factory=LocalRegistrationConfig,
        description="Local registration approval and notification settings",
    )
    oidc: OIDCAuthConfig = Field(default_factory=OIDCAuthConfig, description="OIDC SSO authentication settings")

    @field_validator("allowed_email_domains")
    @classmethod
    def _validate_allowed_email_domains(cls, domains: list[str]) -> list[str]:
        if not domains:
            raise ValueError("allowed_email_domains must contain at least one domain")

        normalized_domains: list[str] = []
        seen: set[str] = set()
        for domain in domains:
            normalized = _normalize_email_domain(domain)
            if normalized not in seen:
                normalized_domains.append(normalized)
                seen.add(normalized)
        return normalized_domains
