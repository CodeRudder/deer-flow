"""HTTP client for the external Media Gateway (MGW).

Thin ``httpx.AsyncClient`` wrapper implementing the cross-cutting concerns of
the MGW HTTP contract (see ``docs/plans/2026-11-01-media-gateway-integration``):

- Bearer token injection — per-request token overrides the default admin token
- Unified timeouts (connect 5s / read 30s)
- MGW error envelope ``{"error": {"code", "message", "retryable"}}`` mapped to
  :class:`MediaGatewayError`, including the ``Retry-After`` response header
- ``Idempotency-Key`` header support for safe retries of mutating requests

The client holds zero business logic; provisioning lives in
:mod:`deerflow.media_gateway.provisioner`.
"""

from __future__ import annotations

import logging
from types import TracebackType

import httpx

logger = logging.getLogger(__name__)

# connect / read / write / pool
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)

_ERROR_RETRIABLE_STATUSES = {429, 502, 503, 504}


class MediaGatewayError(Exception):
    """Normalised MGW error.

    Raised for any non-2xx MGW response (or transport-level failure surfaced
    by the caller wrapping ``httpx.HTTPError``). Carries the parsed error
    envelope so callers can branch on ``code`` / ``retryable`` without
    touching HTTP details.
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"media gateway error {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after


class MediaGatewayClient:
    """Async HTTP client for MGW. Zero business logic."""

    def __init__(
        self,
        base_url: str,
        admin_token: str | None = None,
        *,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._admin_token = admin_token
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout, transport=transport)

    @property
    def base_url(self) -> str:
        return self._base_url

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> MediaGatewayClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json: object | None = None,
        params: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """Perform one MGW request and return the decoded JSON body.

        ``token=None`` means "use the admin token"; pass an explicit token
        (e.g. a per-user ``h3t_`` token) to act as that user. Pass an empty
        string to send no Authorization header at all (e.g. ``/health``).
        """
        headers: dict[str, str] = {}
        bearer = self._admin_token if token is None else token
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        try:
            response = await self._client.request(method, path, json=json, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise MediaGatewayError(status=0, code="transport_error", message=str(exc), retryable=True) from exc

        return self._handle_response(response)

    async def get(self, path: str, **kwargs: object) -> dict:
        return await self.request("GET", path, **kwargs)  # type: ignore[arg-type]

    async def post(self, path: str, **kwargs: object) -> dict:
        return await self.request("POST", path, **kwargs)  # type: ignore[arg-type]

    async def patch(self, path: str, **kwargs: object) -> dict:
        return await self.request("PATCH", path, **kwargs)  # type: ignore[arg-type]

    async def delete(self, path: str, **kwargs: object) -> dict:
        return await self.request("DELETE", path, **kwargs)  # type: ignore[arg-type]

    async def health(self) -> dict:
        """Unauthenticated liveness probe (``GET /health``)."""
        return await self.request("GET", "/health", token="")

    # ── response handling ─────────────────────────────────────────────
    def _handle_response(self, response: httpx.Response) -> dict:
        if response.is_success:
            if not response.content:
                return {}
            try:
                body = response.json()
            except ValueError:
                return {}
            return body if isinstance(body, dict) else {"data": body}
        raise self._error_from_response(response)

    def _error_from_response(self, response: httpx.Response) -> MediaGatewayError:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        code = "http_error"
        message = response.text[:500] if response.text else f"HTTP {response.status_code}"
        retryable = response.status_code in _ERROR_RETRIABLE_STATUSES

        try:
            envelope = response.json()
        except ValueError:
            envelope = None
        if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
            err = envelope["error"]
            code = str(err.get("code") or code)
            message = str(err.get("message") or message)
            retryable = bool(err.get("retryable", retryable))

        return MediaGatewayError(
            status=response.status_code,
            code=code,
            message=message,
            retryable=retryable,
            retry_after=retry_after,
        )


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        # HTTP-date form is not worth a full parser here; treat as unknown.
        return None
