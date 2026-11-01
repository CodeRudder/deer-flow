"""Company platform JWT authentication configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class PlatformAuthConfig(BaseModel):
    """Configuration for JWTs injected by the company gateway."""

    enabled: bool = Field(default=False, description="Enable company gateway JWT authentication.")
    header: str = Field(default="JLC-WEB-API-AccessToken", description="Header carrying the gateway-signed JWT.")
    algorithm: str = Field(default="RS256", description="Expected JWT signing algorithm.")
    issuer: str = Field(default="ai-code-base-platform", description="Expected JWT issuer.")
    audience: str = Field(default="ai-code-base-platform", description="Expected JWT audience.")
    provider: str = Field(default="company_gateway", description="Provider key stored in users.oauth_provider.")
    auto_provision: bool = Field(default=True, description="Create or bind local shadow users on first JWT access.")
    default_password: str = Field(default="jlc@123456", min_length=1, description="Default password for shadow users.")
    public_key: str | None = Field(default=None, description="PEM public key used to verify gateway JWTs.")

    @model_validator(mode="after")
    def _validate_enabled_config(self) -> PlatformAuthConfig:
        if self.enabled and not self.public_key:
            raise ValueError("platform_auth.public_key is required when platform_auth.enabled=true")
        return self

    @property
    def normalized_public_key(self) -> str:
        """Return PEM public key with escaped newlines restored."""
        return (self.public_key or "").replace("\\n", "\n")
