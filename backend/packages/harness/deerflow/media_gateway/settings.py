"""Configuration for the external Media Gateway (MGW).

All fields are optional: the Media Gateway integration degrades gracefully
when MGW is not configured (providers/skills simply should not be offered).
Loaded from environment once and cached, mirroring ``app.gateway.config``.
"""

from __future__ import annotations

import json
import logging
import os

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _load_additional_env(raw: str | None) -> dict[str, str] | None:
    """Parse the optional ``MEDIA_GATEWAY_ADDITIONAL_ENV`` JSON object.

    Malformed payloads are logged as a warning and dropped (never fatal — this
    is an operator convenience for extra sandbox env, not required config).
    """
    if not raw or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("MEDIA_GATEWAY_ADDITIONAL_ENV is not valid JSON; ignoring")
        return None
    if not isinstance(parsed, dict):
        logger.warning("MEDIA_GATEWAY_ADDITIONAL_ENV must be a JSON object; ignoring")
        return None
    return {str(k): str(v) for k, v in parsed.items()}


def parse_candidate_base_urls(raw: str | None) -> list[str]:
    """Split a (possibly comma-separated) ``MEDIA_GATEWAY_BASE_URL`` into candidates.

    Rules: split on ``,``, trim whitespace, strip trailing ``/``, drop empty
    segments, deduplicate keeping first occurrence. A single-URL value yields
    exactly that URL — behaviour is unchanged for existing single-address
    deployments. Example: ``http://172.17.0.1:8000,http://192.168.1.10:8000``.
    """
    candidates: list[str] = []
    for part in (raw or "").split(","):
        url = part.strip().rstrip("/")
        if url and url not in candidates:
            candidates.append(url)
    return candidates


class MediaGatewaySettings(BaseModel):
    """Media Gateway connection settings (see docs/plans/2026-11-01-media-gateway-integration)."""

    base_url: str | None = Field(
        default=None,
        description=("MGW API base URL; /v1/... paths are appended to this. May be a comma-separated list of candidate URLs — the first reachable one wins"),
    )
    admin_token: str | None = Field(
        default=None,
        description="Long-lived admin token used only for user provisioning",
    )
    console_url: str | None = Field(
        default=None,
        description="MGW console page URL used to build user-facing links",
    )
    # External (user-reachable) addresses. The server-side ``base_url`` /
    # ``console_url`` may be internal-only (e.g. http://172.17.0.1:8000) while
    # session-facing links must resolve from the user's browser. Unset → the
    # internal addresses are used as before (full backward compatibility).
    external_base_url: str | None = Field(
        default=None,
        description="External (user-reachable) MGW base address, e.g. https://mgw.example.com or http://100.108.144.120:8000; console links default to external_base_url + /ui",
    )
    external_console_url: str | None = Field(
        default=None,
        description="External MGW console page URL; overrides external_base_url + /ui when set",
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
    # Reserved: extra env vars appended to the per-user sandbox session env by
    # the credential middleware (JSON object, e.g. MEDIA_GATEWAY_ADDITIONAL_ENV
    # ='{"MEDIA_GATEWAY_TIMEOUT":"30"}'). Values must not be secrets — they are
    # readable by any command running inside the user's sandbox.
    additional_env: dict[str, str] | None = Field(
        default=None,
        description="Optional extra env vars injected into sandbox sessions alongside MGW credentials",
    )
    # JIT per-user provisioning (design §5.1): when a signed-in user has no
    # media_gateway_users mapping yet, the credential-injection path provisions
    # an MGW account + token on the fly instead of skipping injection. Disable
    # with MEDIA_GATEWAY_JIT_PROVISIONING=false.
    jit_provisioning: bool = Field(
        default=True,
        description="Provision an MGW account on first credential resolution for users without a mapping (design §5.1)",
    )

    def display_console_url(self) -> str | None:
        """Console URL shown to end users, preferring external addresses.

        Priority: ``external_console_url`` → ``external_base_url`` + ``/ui`` →
        ``console_url`` (internal; legacy behaviour). ``None`` when nothing is
        configured.
        """
        if self.external_console_url and self.external_console_url.strip():
            return self.external_console_url.strip().rstrip("/")
        if self.external_base_url and self.external_base_url.strip():
            return self.external_base_url.strip().rstrip("/") + "/ui"
        return self.console_url

    def candidate_base_urls(self) -> list[str]:
        """Ordered, deduplicated candidate base URLs (comma-separated support)."""
        return parse_candidate_base_urls(self.base_url)

    def primary_base_url(self) -> str | None:
        """First candidate base URL, or ``None`` when unset."""
        candidates = self.candidate_base_urls()
        return candidates[0] if candidates else None


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
            external_base_url=os.getenv("MEDIA_GATEWAY_EXTERNAL_BASE_URL") or None,
            external_console_url=os.getenv("MEDIA_GATEWAY_EXTERNAL_CONSOLE_URL") or None,
            image_model=os.getenv("MEDIA_GATEWAY_IMAGE_MODEL") or None,
            video_model=os.getenv("MEDIA_GATEWAY_VIDEO_MODEL") or None,
            encryption_key=os.getenv("MEDIA_GATEWAY_ENCRYPTION_KEY") or None,
            additional_env=_load_additional_env(os.getenv("MEDIA_GATEWAY_ADDITIONAL_ENV")),
            jit_provisioning=os.getenv("MEDIA_GATEWAY_JIT_PROVISIONING", "true").strip().lower() not in {"0", "false", "no", "off"},
        )
    return _media_gateway_settings


def set_media_gateway_settings(settings: MediaGatewaySettings | None) -> None:
    """Override the cached settings singleton (for testing)."""
    global _media_gateway_settings
    _media_gateway_settings = settings
