"""Per-user Media Gateway environment for sandbox sessions and in-process clients.

Central helper behind the credential-injection chain:

    MediaGatewayCredentialMiddleware (agents/middlewares)
        → get_runtime_mgw_env(user_id)          ← this module
            → provisioner.get_token(user_id)    (deerflow.media_gateway.provisioner)
            → get_media_gateway_settings()      (deerflow.media_gateway.settings)

Returns the env dict that gets (a) written into the user's sandbox session so
every bash/python command (including skill scripts) sees the credentials via
``os.environ``, and (b) stored on the run's runtime context so Python-layer
code that builds a :class:`~deerflow.media_gateway.client.MediaGatewayClient`
can construct one with the caller's own per-user token directly:

    env = await get_runtime_mgw_env(user_id)
    if env:
        client = MediaGatewayClient(env["MEDIA_GATEWAY_BASE_URL"], env["MEDIA_GATEWAY_TOKEN"])

Degradation contract (never raises, never blocks a session):

- MGW not configured (``MEDIA_GATEWAY_BASE_URL`` unset) → ``{}`` (debug log);
- no provisioning mapping for the user → ``{}`` (debug log);
- token decryption / lookup failure → ``{}`` (warning log);
- the token itself is never logged.
"""

from __future__ import annotations

import logging

from deerflow.media_gateway.client import MediaGatewayError
from deerflow.media_gateway.provisioner import MediaGatewayProvisioner
from deerflow.media_gateway.settings import get_media_gateway_settings

logger = logging.getLogger(__name__)

__all__ = ["get_runtime_mgw_env", "reset_runtime_mgw_cache"]

_provisioner: MediaGatewayProvisioner | None = None
_provisioner_resolved = False


def _get_provisioner() -> MediaGatewayProvisioner | None:
    """Lazily build and cache the process-wide provisioner; None when MGW is unset.

    Mirrors ``app.gateway.admin.media_gateway_lifecycle._get_provisioner`` but
    lives in the harness so middleware (which must not import ``app.*``) can
    use it. Built once from :func:`get_media_gateway_settings` plus the shared
    persistence engine and cached for the process lifetime.
    """
    global _provisioner, _provisioner_resolved
    if _provisioner_resolved:
        return _provisioner
    _provisioner_resolved = True
    try:
        from deerflow.persistence.engine import get_session_factory

        settings = get_media_gateway_settings()
        if not settings.base_url:
            return None
        from deerflow.media_gateway.client import MediaGatewayClient
        from deerflow.media_gateway.crypto import MediaGatewayCipher

        encryption_key = settings.encryption_key or "deerflow-media-gateway"
        client = MediaGatewayClient(settings.base_url, settings.admin_token)
        session_factory = get_session_factory()
        if session_factory is None:
            logger.warning("media gateway runtime env: persistence engine not initialized; MGW env disabled")
            return None
        _provisioner = MediaGatewayProvisioner(client, session_factory, MediaGatewayCipher.from_key(encryption_key))
    except Exception as exc:  # noqa: BLE001 - env resolution must never break the caller
        logger.warning("media gateway runtime env: provisioner unavailable (%s)", exc)
        _provisioner = None
    return _provisioner


def reset_runtime_mgw_cache() -> None:
    """Drop the cached provisioner (testing / settings override)."""
    global _provisioner, _provisioner_resolved
    _provisioner = None
    _provisioner_resolved = False


async def get_runtime_mgw_env(user_id: str | None) -> dict[str, str]:
    """Build the per-user Media Gateway env dict for sandbox injection.

    Empty dict when MGW is unconfigured or the user has no provisioned
    mapping — callers treat that as "do not inject" and continue normally.
    """
    settings = get_media_gateway_settings()
    if not settings.base_url:
        logger.debug("media gateway env: not configured (no MEDIA_GATEWAY_BASE_URL); skipping injection")
        return {}
    if not user_id:
        logger.debug("media gateway env: no runtime user id; skipping injection")
        return {}

    provisioner = _get_provisioner()
    if provisioner is None:
        return {}
    try:
        token = await provisioner.get_token(user_id)
    except MediaGatewayError as exc:
        if exc.status == 404 and exc.code == "not_provisioned":
            logger.debug("media gateway env: user %s has no MGW mapping; skipping injection", user_id)
        else:
            logger.warning("media gateway env: token lookup failed for user %s: %s", user_id, exc)
        return {}
    except Exception as exc:  # noqa: BLE001 - degraded, never fatal
        logger.warning("media gateway env: unexpected token lookup failure for user %s: %s", user_id, exc)
        return {}

    env = {
        "MEDIA_GATEWAY_BASE_URL": settings.base_url,
        "MEDIA_GATEWAY_TOKEN": token,
    }
    if settings.image_model:
        env["MEDIA_GATEWAY_IMAGE_MODEL"] = settings.image_model
    if settings.video_model:
        env["MEDIA_GATEWAY_VIDEO_MODEL"] = settings.video_model
    if settings.console_url:
        env["MEDIA_GATEWAY_CONSOLE_URL"] = settings.console_url
    if settings.additional_env:
        env.update(settings.additional_env)
    return env
