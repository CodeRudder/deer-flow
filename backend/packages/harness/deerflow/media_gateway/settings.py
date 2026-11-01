"""Configuration for the external Media Gateway (MGW).

All fields are optional: the Media Gateway integration degrades gracefully
when MGW is not configured (providers/skills simply should not be offered).
Loaded from environment once and cached, mirroring ``app.gateway.config``.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field


class MediaGatewaySettings(BaseModel):
    """Media Gateway connection settings (see docs/plans/2026-11-01-media-gateway-integration)."""

    base_url: str | None = Field(
        default=None,
        description="MGW API base URL; /v1/... paths are appended to this",
    )
    admin_token: str | None = Field(
        default=None,
        description="Long-lived admin token used only for user provisioning",
    )
    console_url: str | None = Field(
        default=None,
        description="MGW console page URL used to build user-facing links",
    )
    image_model: str | None = Field(
        default=None,
        description="Optional default MGW image model name",
    )
    video_model: str | None = Field(
        default=None,
        description="Optional default MGW video model name",
    )
    # Secret used to encrypt per-user MGW tokens/passwords at rest. Falls back
    # to the auth JWT secret when unset so deployments get non-trivial
    # encryption without extra configuration.
    encryption_key: str | None = Field(
        default=None,
        description="Fernet key material for encrypting MGW tokens at rest",
    )


_media_gateway_settings: MediaGatewaySettings | None = None


def get_media_gateway_settings() -> MediaGatewaySettings:
    """Get Media Gateway settings, loading from environment on first call."""
    global _media_gateway_settings
    if _media_gateway_settings is None:
        from dotenv import load_dotenv

        load_dotenv()
        _media_gateway_settings = MediaGatewaySettings(
            base_url=os.getenv("MEDIA_GATEWAY_BASE_URL") or None,
            admin_token=os.getenv("MEDIA_GATEWAY_ADMIN_TOKEN") or None,
            console_url=os.getenv("MEDIA_GATEWAY_CONSOLE_URL") or None,
            image_model=os.getenv("MEDIA_GATEWAY_IMAGE_MODEL") or None,
            video_model=os.getenv("MEDIA_GATEWAY_VIDEO_MODEL") or None,
            encryption_key=os.getenv("MEDIA_GATEWAY_ENCRYPTION_KEY") or None,
        )
    return _media_gateway_settings


def set_media_gateway_settings(settings: MediaGatewaySettings | None) -> None:
    """Override the cached settings singleton (for testing)."""
    global _media_gateway_settings
    _media_gateway_settings = settings
