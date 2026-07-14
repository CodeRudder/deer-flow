"""OIDC / SSO authentication configuration models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
