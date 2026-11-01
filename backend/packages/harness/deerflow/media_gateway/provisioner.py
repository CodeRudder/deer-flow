"""Just-in-time provisioning of Media Gateway (MGW) accounts for DeerFlow users.

On first contact with a media feature a DeerFlow user gets:

1. an MGW account (``POST /v1/users``) with a random password — 409 means the
   username already exists on MGW, in which case we claim it by username;
2. a per-user long-lived API token (``POST /v1/users/{uid}/tokens``, label
   ``"deerflow"``) whose plaintext is shown exactly once and stored encrypted;
3. a row in ``media_gateway_users`` mapping DeerFlow user → MGW account.

Concurrency safety: two concurrent first calls for the same user race on the
mapping table's primary key — the loser gets an ``IntegrityError`` and
re-reads the winner's row instead of creating a second MGW account.

This module contains provisioning/identity logic only; generation calls are
made by providers/skills (M2) using the token returned here.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.media_gateway.client import MediaGatewayClient, MediaGatewayError
from deerflow.media_gateway.crypto import MediaGatewayCipher
from deerflow.persistence.media_gateway import MediaGatewayUserRow

logger = logging.getLogger(__name__)

_TOKEN_LABEL = "deerflow"

# MGW username constraints: ≤32 chars, no whitespace/control chars, no '/' or ':'.
_MGW_USERNAME_MAX_LEN = 32
_USERNAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MGW_USERNAME_FALLBACK = "deerflow-user"


@dataclass(frozen=True)
class MediaGatewayAccount:
    """A provisioned (or already-mapped) MGW account for one DeerFlow user."""

    deerflow_user_id: str
    mgw_user_id: str
    mgw_username: str
    token: str
    token_sha8: str
    disabled: bool = False


def slugify_username(raw: str) -> str:
    """Normalise a DeerFlow username/email into a valid MGW username.

    - keeps ``[A-Za-z0-9._-]`` only; every other run collapses to ``-``
    - strips whitespace/control characters entirely
    - hard-truncates to 32 chars and strips trailing separators
    - falls back to a random-suffixed name when nothing usable remains
    """
    # ``isprintable()`` is False for whitespace and control characters alike.
    candidate = "".join(ch for ch in raw if ch.isprintable())
    candidate = _USERNAME_SANITIZE_RE.sub("-", candidate).strip("-._")
    if len(candidate) > _MGW_USERNAME_MAX_LEN:
        candidate = candidate[:_MGW_USERNAME_MAX_LEN].rstrip("-._")
    if not candidate:
        candidate = f"{_MGW_USERNAME_FALLBACK}-{secrets.token_hex(4)}"
        candidate = candidate[:_MGW_USERNAME_MAX_LEN]
    return candidate


def resolve_mgw_role(system_role: str | None) -> str:
    """One-way role mapping: DeerFlow admin → MGW admin, everyone else → user."""
    return "admin" if (system_role or "").lower() == "admin" else "user"


class MediaGatewayProvisioner:
    """JIT account/token provisioning with an idempotent local mapping table."""

    def __init__(
        self,
        client: MediaGatewayClient,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: MediaGatewayCipher,
    ) -> None:
        self._client = client
        self.session_factory = session_factory
        self._cipher = cipher

    # ── public API ────────────────────────────────────────────────────
    async def ensure_user(self, user: object) -> MediaGatewayAccount:
        """Idempotently provision an MGW account + token for ``user``.

        ``user`` is any object exposing ``id``, ``username`` or ``email``, and
        ``system_role`` (e.g. ``deerflow.persistence.user.model.UserRow``).
        """
        user_id = str(getattr(user, "id"))
        async with self.session_factory() as session:
            row = await self._get_row(session, user_id)
            if row is not None:
                return self._account_from_row(row)
        return await self._provision_new(user, user_id)

    async def disable_user(self, user_id: str) -> bool:
        """Disable the mapped MGW account (revokes its tokens/sessions). No-op when unmapped."""
        async with self.session_factory() as session:
            row = await self._get_row(session, user_id)
            if row is None:
                logger.warning("media gateway disable: no mapping for user %s", user_id)
                return False
            await self._client.patch(f"/v1/users/{row.mgw_user_id}", json={"disabled": True})
            row.mgw_disabled = True
            row.updated_at = datetime.now(UTC)
            await session.commit()
        return True

    async def update_role(self, user_id: str, system_role: str) -> bool:
        """Sync a DeerFlow role change (promote/demote admin) to the mapped MGW account.

        No-op when the user has no MGW mapping yet — a later JIT provisioning
        picks up the current role anyway.
        """
        async with self.session_factory() as session:
            row = await self._get_row(session, user_id)
            if row is None:
                logger.warning("media gateway role sync: no mapping for user %s", user_id)
                return False
            await self._client.patch(f"/v1/users/{row.mgw_user_id}", json={"role": resolve_mgw_role(system_role)})
            row.updated_at = datetime.now(UTC)
            await session.commit()
        return True

    async def reset_token(self, user_id: str) -> MediaGatewayAccount:
        """Revoke the current MGW token and issue a fresh one (token rotation)."""
        async with self.session_factory() as session:
            row = await self._get_row(session, user_id)
            if row is None:
                raise MediaGatewayError(status=404, code="not_provisioned", message=f"no Media Gateway mapping for user {user_id}")

            old_sha8 = row.mgw_token_sha8
            created = await self._create_token(row.mgw_user_id)
            if old_sha8:
                try:
                    await self._client.delete(f"/v1/users/{row.mgw_user_id}/tokens/{old_sha8}")
                except MediaGatewayError as exc:
                    # Revocation is best-effort: the new token is already the
                    # mapping table's source of truth.
                    logger.warning("media gateway token revoke failed for user %s: %s", user_id, exc)

            row.mgw_token_enc = self._cipher.encrypt_text(created["token"])
            row.mgw_token_sha8 = created["sha8"]
            row.updated_at = datetime.now(UTC)
            await session.commit()
            return MediaGatewayAccount(
                deerflow_user_id=user_id,
                mgw_user_id=row.mgw_user_id,
                mgw_username=row.mgw_username,
                token=created["token"],
                token_sha8=created["sha8"],
                disabled=row.mgw_disabled,
            )

    async def get_token(self, user_id: str) -> str:
        """Decrypt and return the stored per-user MGW token."""
        async with self.session_factory() as session:
            row = await self._get_row(session, user_id)
        if row is None:
            raise MediaGatewayError(status=404, code="not_provisioned", message=f"no Media Gateway mapping for user {user_id}")
        return self._cipher.decrypt_text(row.mgw_token_enc)

    # ── provisioning internals ────────────────────────────────────────
    async def _provision_new(self, user: object, user_id: str) -> MediaGatewayAccount:
        raw_name = str(getattr(user, "username", None) or getattr(user, "email", None) or user_id)
        username = slugify_username(raw_name)
        role = resolve_mgw_role(getattr(user, "system_role", None))
        password = secrets.token_urlsafe(32)  # ~32 bytes of entropy; console-fallback only

        try:
            body = await self._client.post(
                "/v1/users",
                json={"username": username, "password": password, "role": role, "display": raw_name},
            )
            mgw_user_id = str(body.get("id") or (body.get("user") or {}).get("id") or "")
        except MediaGatewayError as exc:
            if exc.status != 409:
                raise
            # Username already exists on MGW (e.g. provisioned by another
            # DeerFlow instance) — claim it instead of failing.
            mgw_user_id = await self._claim_existing_user(username)
            logger.info("claimed existing media gateway user %r for deerflow user %s", username, user_id)

        created = await self._create_token(mgw_user_id)

        row = MediaGatewayUserRow(
            deerflow_user_id=user_id,
            mgw_user_id=mgw_user_id,
            mgw_username=username,
            mgw_token_enc=self._cipher.encrypt_text(created["token"]),
            mgw_token_sha8=created["sha8"],
            mgw_password_enc=self._cipher.encrypt_text(password),
        )
        async with self.session_factory() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # A concurrent first call for the same user won the PK race —
                # return their (now committed) account.
                await session.rollback()
                existing = await self._get_row(session, user_id)
                if existing is None:
                    raise
                return self._account_from_row(existing)
        return self._account_from_row(row)

    async def _create_token(self, mgw_user_id: str) -> dict[str, str]:
        body = await self._client.post(f"/v1/users/{mgw_user_id}/tokens", json={"label": _TOKEN_LABEL})
        token = str(body.get("token") or "")
        info = body.get("token_info") or {}
        sha8 = str(info.get("sha8") or "")
        if not token or not sha8:
            raise MediaGatewayError(status=0, code="bad_response", message="token creation response missing token/sha8")
        return {"token": token, "sha8": sha8}

    async def _claim_existing_user(self, username: str) -> str:
        # MGW contract (docs/integration/api/auth.md, GET /v1/users): the admin
        # listing does NOT support a username filter (any params are ignored and
        # the full list is returned) and user objects are keyed by ``name`` —
        # there is no ``username`` field. The gateway is internal with a small
        # user count, so a full fetch plus a client-side match is acceptable.
        listing = await self._client.get("/v1/users")
        for item in listing.get("users") or []:
            if str(item.get("name")) == username:
                if item.get("disabled"):
                    logger.warning("media gateway user %r exists but is disabled; refusing to claim", username)
                    break
                return str(item["id"])
        raise MediaGatewayError(
            status=409,
            code="username_conflict",
            message=f"media gateway user {username!r} already exists but could not be claimed",
        )

    async def _get_row(self, session: AsyncSession, user_id: str) -> MediaGatewayUserRow | None:
        result = await session.execute(select(MediaGatewayUserRow).where(MediaGatewayUserRow.deerflow_user_id == user_id))
        return result.scalar_one_or_none()

    def _account_from_row(self, row: MediaGatewayUserRow) -> MediaGatewayAccount:
        return MediaGatewayAccount(
            deerflow_user_id=row.deerflow_user_id,
            mgw_user_id=row.mgw_user_id,
            mgw_username=row.mgw_username,
            token=self._cipher.decrypt_text(row.mgw_token_enc),
            token_sha8=row.mgw_token_sha8,
            disabled=row.mgw_disabled,
        )
