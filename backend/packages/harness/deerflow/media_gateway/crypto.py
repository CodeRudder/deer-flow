"""Secret-at-rest encryption for Media Gateway mapping rows.

MGW per-user tokens (and the generated console-fallback passwords) must never
be persisted in cleartext. We reuse the exact Fernet scheme already used for
IM channel credentials (``deerflow.persistence.channel_connections.sql``):
SHA-256 of a textual key material → urlsafe-base64 → ``cryptography.Fernet``,
values stored with a ``fernet:v1:`` prefix.

Key material resolution order (see ``MediaGatewaySettings``):

1. ``MEDIA_GATEWAY_ENCRYPTION_KEY`` env var (dedicated secret)
2. ``AUTH_JWT_SECRET`` env var (falls back to the persisted ``.jwt_secret``
   via ``app.gateway.auth.config`` semantics — but we only read the env var
   here to keep the harness free of ``app.*`` imports)
3. empty → encryption unavailable; the provisioner raises instead of
   silently storing plaintext.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

_PREFIX = "fernet:v1:"


class MediaGatewaySecretError(RuntimeError):
    """Raised when encrypted values cannot be decrypted (key mismatch/corruption)."""


class MediaGatewayCipher:
    """Encrypts/decrypts MGW tokens and passwords before they hit the database."""

    def __init__(self, fernet: Fernet) -> None:
        self._fernet = fernet

    @classmethod
    def from_key(cls, key: str) -> MediaGatewayCipher:
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        return cls(Fernet(base64.urlsafe_b64encode(digest)))

    def encrypt_text(self, value: str) -> str:
        return _PREFIX + self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt_text(self, value: str) -> str:
        token = value.removeprefix(_PREFIX)
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeEncodeError) as exc:
            raise MediaGatewaySecretError("Failed to decrypt Media Gateway secret (wrong encryption key?)") from exc
