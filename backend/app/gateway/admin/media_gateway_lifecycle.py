"""Best-effort Media Gateway (MGW) lifecycle hooks for DeerFlow user changes.

When a DeerFlow user is disabled (or their role changes), the mapped MGW
account should be disabled / re-roleled too. The local database change is the
source of truth and MUST succeed regardless; these hooks only propagate the
change outward:

- MGW not configured (``MEDIA_GATEWAY_BASE_URL`` unset) → silent no-op;
- any MGW / construction failure → logged as a warning, never raised.

Trade-off note: :class:`~app.gateway.admin.user_management_service.UserManagementService`
is constructed per request without a provisioner dependency, so instead of
threading a provisioner through the service/repository constructors we expose
module-level functions the service calls in one line each. The provisioner is
built lazily once from :func:`deerflow.media_gateway.settings.get_media_gateway_settings`
plus the shared persistence engine and cached for the process lifetime.

There is currently no runtime admin promote/demote flow in the app layer
(roles are fixed at account creation; OIDC admin emails are resolved at
provisioning time), so :func:`on_user_role_changed` has no live caller yet —
:meth:`MediaGatewayProvisioner.update_role` exists and the hook is wired for
when such an admin action lands.
"""

from __future__ import annotations

import logging

from deerflow.media_gateway.client import MediaGatewayClient
from deerflow.media_gateway.crypto import MediaGatewayCipher
from deerflow.media_gateway.provisioner import MediaGatewayProvisioner

logger = logging.getLogger(__name__)

_provisioner: MediaGatewayProvisioner | None = None
_provisioner_resolved = False


def _get_provisioner() -> MediaGatewayProvisioner | None:
    """Lazily build and cache the process-wide provisioner; None when MGW is unset."""
    global _provisioner, _provisioner_resolved
    if _provisioner_resolved:
        return _provisioner
    _provisioner_resolved = True
    try:
        from deerflow.media_gateway.settings import get_media_gateway_settings
        from deerflow.persistence.engine import get_session_factory

        settings = get_media_gateway_settings()
        if not settings.base_url:
            return None
        encryption_key = settings.encryption_key or "deerflow-media-gateway"
        client = MediaGatewayClient(settings.base_url, settings.admin_token)
        _provisioner = MediaGatewayProvisioner(client, get_session_factory(), MediaGatewayCipher.from_key(encryption_key))
    except Exception as exc:  # noqa: BLE001 - hooks must never break the caller
        logger.warning("media gateway lifecycle: provisioner unavailable (%s)", exc)
        _provisioner = None
    return _provisioner


def reset_provisioner_cache() -> None:
    """Drop the cached provisioner (testing / settings override)."""
    global _provisioner, _provisioner_resolved
    _provisioner = None
    _provisioner_resolved = False


async def on_user_disabled(user_id: str) -> None:
    """Best-effort: disable the mapped MGW account after a local disable."""
    try:
        provisioner = _get_provisioner()
        if provisioner is None:
            return
        disabled = await provisioner.disable_user(user_id)
        if not disabled:
            logger.info("media gateway lifecycle: user %s has no MGW mapping; nothing to disable", user_id)
    except Exception as exc:  # noqa: BLE001 - local disable already succeeded
        logger.warning("media gateway lifecycle: disable sync failed for user %s: %s", user_id, exc)


async def on_user_role_changed(user_id: str, system_role: str) -> None:
    """Best-effort: propagate a DeerFlow role change (admin/user) to MGW."""
    try:
        provisioner = _get_provisioner()
        if provisioner is None:
            return
        updated = await provisioner.update_role(user_id, system_role)
        if not updated:
            logger.info("media gateway lifecycle: user %s has no MGW mapping; role not synced", user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("media gateway lifecycle: role sync failed for user %s: %s", user_id, exc)
