"""In-process mock of the Media Gateway (MGW) HTTP contract.

Contract source of truth: ``minimax-h3-gateway/docs/integration/api/auth.md`` +
``images.md`` (+ the ``/v1/auth/ticket`` addition in
``docs/integration/deerflow-integration.md``). This mock implements enough of
that contract for the E2E-mock tier: the client, the JIT provisioner and — in
later batches — every media provider/skill test.

It is an ``httpx.MockTransport`` handler (same style as the existing
``test_media_gateway_client.py`` tests), so no port is bound and no sockets are
involved: :class:`~deerflow.media_gateway.client.MediaGatewayClient` is simply
constructed with ``transport=mock.transport``.

Covered endpoints:

- ``POST /v1/auth/login``            → session token (``h3s_…``)
- ``POST /v1/auth/ticket``           → one-shot console ticket (60 s TTL)
- ``POST /v1/auth/login-ticket``     → redeem ticket → session token (single use)
- ``GET  /v1/users``                 → ``{"users":[{id,name,role,disabled,…}]}`` (admin)
- ``POST /v1/users``                 → create (409 ``username_exists`` on dup)
- ``PATCH /v1/users/{uid}``          → ``disabled`` / ``role``
- ``POST /v1/users/{uid}/tokens``    → ``{"token","token_info":{label,sha8}}`` (plaintext once)
- ``DELETE /v1/users/{uid}/tokens/{sha8}``
- ``POST /v1/images/generations``    → 202 job brief; honours ``Idempotency-Key``
  (same key + same body → same job, different body → 409 ``conflict``)
- ``GET  /v1/images/jobs/{id}``      → state machine queued → running → completed
- ``GET  /v1/images/jobs/{id}/file`` → configurable bytes + Content-Type
- ``GET  /health``                   → unauthenticated liveness

Error envelope everywhere: ``{"error": {code, message, retryable}}``; 429/503
carry ``Retry-After``. Non-admin callers of admin endpoints get 404 (the
contract deliberately hides existence), bad/missing tokens get 401.

Usage (pytest)::

    from deerflow.media_gateway.mock_server import media_gateway_mock

    @pytest.mark.anyio
    async def test_something(media_gateway_mock):
        mg = media_gateway_mock
        assert await mg.client.get("/health")["status"] == "ok"
        mg.fail_next(status=429, code="rate_limited", retry_after=7)
        with pytest.raises(MediaGatewayError):
            await mg.client.get("/v1/users")

Scriptable failure knobs on :class:`MockMediaGateway`:

- ``fail_next(status=…, code=…, retryable=…, retry_after=…, times=n)`` — the
  next *n* requests fail with a contract-shaped error envelope
- ``rate_limit_next(retry_after=…, times=n)`` — sugar for 429 + Retry-After
- ``timeout_next(times=n)`` — the next n requests raise ``httpx.ReadTimeout``
  (the client maps that to a retryable ``transport_error``)
- ``fail_job(job_id, code=…, message=…)`` — inject a generation failure; the
  next poll returns ``failed`` with the error attached
- ``polls_per_stage`` — how many GET polls each job stays in a given state
  (default 1: first poll → ``running``, second → ``completed``)
- ``set_file(data, content_type)`` — what ``/file`` returns
- ``requests`` — log of ``(method, path)`` pairs for assertions

Every test batch (providers, skills, E2E-mock) builds on this class — extend it
rather than re-rolling ad-hoc handlers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from deerflow.media_gateway.client import MediaGatewayClient

_DEFAULT_ADMIN_TOKEN = "h3t_mock-admin-token"

# Console ticket semantics (deerflow-integration.md): 32 random bytes, 60 s TTL.
_TICKET_TTL_S = 60
_TICKET_TTL_WARNING_MARGIN = 5


def _error(status: int, code: str, message: str, *, retryable: bool = False, retry_after: float | None = None) -> httpx.Response:
    headers = {"Retry-After": str(int(retry_after))} if retry_after is not None else {}
    return httpx.Response(status, json={"error": {"code": code, "message": message, "retryable": retryable}}, headers=headers)


class MockMediaGateway:
    """Scriptable in-process MGW; hand :attr:`transport` to an MGW client."""

    def __init__(
        self,
        *,
        admin_username: str = "legacy",
        admin_password: str = "mock-admin-password",
        admin_token: str = _DEFAULT_ADMIN_TOKEN,
        file_bytes: bytes = b"\x89PNG-mock-image-bytes",
        file_content_type: str = "image/png",
        polls_per_stage: int = 1,
    ) -> None:
        self.admin_username = admin_username
        self.admin_password = admin_password
        self.admin_token = admin_token
        self.polls_per_stage = polls_per_stage

        self.requests: list[tuple[str, str]] = []
        self.users: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, dict[str, Any]] = {}  # plaintext -> {user_id, sha8, label}
        self.tickets: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, dict[str, Any]] = {}
        self.file_bytes = file_bytes
        self.file_content_type = file_content_type

        self._pending_failures: list[dict[str, Any]] = []
        self._pending_timeouts = 0

        # Seed the legacy admin bootstrap identity (mirrors H3IMG_AUTH_TOKEN):
        # a built-in admin whose token predates the users table.
        self.users[""] = {"id": "", "name": "legacy", "display": "内置管理员", "role": "admin", "disabled": False, "legacy": True, "password": admin_password}
        self.tokens[admin_token] = {"user_id": "", "sha8": self._sha8(admin_token), "label": "bootstrap"}

    # ── test-facing wiring ────────────────────────────────────────────
    @property
    def base_url(self) -> str:
        return "http://mock-mgw"

    @property
    def transport(self) -> httpx.AsyncBaseTransport:
        return httpx.MockTransport(self.handle)

    def client(self, *, token: str | None = None) -> MediaGatewayClient:
        """A ready-to-use :class:`MediaGatewayClient` pointed at this mock."""
        return MediaGatewayClient(self.base_url, token if token is not None else self.admin_token, transport=self.transport)

    # ── scriptable behaviour knobs ────────────────────────────────────
    def fail_next(
        self,
        *,
        status: int = 503,
        code: str = "unavailable",
        message: str = "mock failure",
        retryable: bool = True,
        retry_after: float | None = None,
        times: int = 1,
    ) -> None:
        """Make the next ``times`` requests fail with a contract-shaped envelope."""
        for _ in range(times):
            self._pending_failures.append({"status": status, "code": code, "message": message, "retryable": retryable, "retry_after": retry_after})

    def rate_limit_next(self, *, retry_after: float = 30, times: int = 1) -> None:
        """Sugar: the next requests get 429 ``too_many_requests`` + Retry-After."""
        self.fail_next(status=429, code="too_many_requests", message="rate limited (mock)", retryable=True, retry_after=retry_after, times=times)

    def timeout_next(self, *, times: int = 1) -> None:
        """Make the next requests die at the transport layer (read timeout)."""
        self._pending_timeouts += times

    def fail_job(self, job_id: str, *, code: str = "engine_error", message: str = "mock generation failure") -> None:
        """Schedule ``failed`` for this job on its next poll."""
        job = self.jobs[job_id]
        job["fail"] = {"code": code, "message": message}
        job["polls_in_stage"] = self.polls_per_stage  # finish the current stage first

    def set_file(self, data: bytes, content_type: str = "image/png") -> None:
        self.file_bytes = data
        self.file_content_type = content_type

    # ── the handler ───────────────────────────────────────────────────
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))

        if self._pending_timeouts:
            self._pending_timeouts -= 1
            raise httpx.ReadTimeout("mock read timeout", request=request)
        if self._pending_failures:
            f = self._pending_failures.pop(0)
            return _error(f["status"], f["code"], f["message"], retryable=f["retryable"], retry_after=f["retry_after"])

        if request.method == "GET" and path == "/health":
            return httpx.Response(200, json={"status": "ok", "auth_mode": "users"})

        # ── unauthenticated auth flows ────────────────────────────────
        if request.method == "POST" and path == "/v1/auth/login":
            return self._login(request)
        if request.method == "POST" and path == "/v1/auth/login-ticket":
            return self._login_ticket(request)

        # ── everything below requires a valid bearer token ────────────
        user_id = self._identify(request)
        if user_id is None:
            return _error(401, "unauthorized", "missing or invalid bearer token")

        if request.method == "POST" and path == "/v1/auth/ticket":
            return self._issue_ticket(user_id)

        if path.startswith("/v1/users"):
            return self._handle_users(request, user_id)
        if path.startswith("/v1/images"):
            return self._handle_images(request, user_id)

        return _error(404, "not_found", f"mock has no route for {request.method} {path}")

    # ── auth ──────────────────────────────────────────────────────────
    def _login(self, request: httpx.Request) -> httpx.Response:
        body = self._json(request)
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        for user in self.users.values():
            if user["name"] == username and user.get("password") == password and not user["disabled"]:
                return self._session_response(user["id"])
        return _error(401, "invalid_credentials", "unknown user or wrong password")

    def _issue_ticket(self, user_id: str) -> httpx.Response:
        user = self.users[user_id]
        if user["disabled"]:
            return _error(401, "unauthorized", "user disabled")
        ticket = "mgt_" + secrets.token_hex(16)
        expires = int(time.time()) + _TICKET_TTL_S
        self.tickets[ticket] = {"user_id": user_id, "expires": expires, "used": False}
        return httpx.Response(200, json={"ticket": ticket, "expires": expires})

    def _login_ticket(self, request: httpx.Request) -> httpx.Response:
        ticket = str(self._json(request).get("ticket") or "")
        record = self.tickets.get(ticket)
        if record is None or record["used"] or record["expires"] < time.time():
            return _error(401, "invalid_ticket", "ticket unknown, already used or expired")
        record["used"] = True  # single-use: a second redemption always fails
        return self._session_response(record["user_id"])

    def _session_response(self, user_id: str) -> httpx.Response:
        token = "h3s_" + secrets.token_hex(16)
        self.tokens[token] = {"user_id": user_id, "sha8": self._sha8(token), "label": "session"}
        user = self.users[user_id]
        return httpx.Response(200, json={"token": token, "token_type": "session", "user": {"id": user["id"], "name": user["name"], "role": user["role"]}})

    def expire_ticket(self, ticket: str) -> None:
        """Test hook: force a previously issued ticket to look expired."""
        self.tickets[ticket]["expires"] = int(time.time()) - 1

    # ── users / tokens ────────────────────────────────────────────────
    def _handle_users(self, request: httpx.Request, user_id: str) -> httpx.Response:
        path = request.url.path
        method = request.method

        if method == "GET" and path == "/v1/users":
            if not self._is_admin(user_id):
                return _error(404, "not_found", "admin only (existence hidden)")
            users = [{"id": u["id"], "name": u["name"], "role": u["role"], "disabled": u["disabled"], "legacy": u.get("legacy", False)} for u in self.users.values() if u["id"]]
            return httpx.Response(200, json={"users": users})

        if method == "POST" and path == "/v1/users":
            if not self._is_admin(user_id):
                return _error(404, "not_found", "admin only")
            body = self._json(request)
            name = str(body.get("username") or "")
            for existing in self.users.values():
                if existing["name"] == name:
                    return _error(409, "username_exists", f"username {name!r} already exists")
            uid = "mgw-" + secrets.token_hex(4)
            self.users[uid] = {"id": uid, "name": name, "display": str(body.get("display") or name), "role": str(body.get("role") or "user"), "disabled": False, "password": str(body.get("password") or "")}
            return httpx.Response(201, json={"id": uid, "name": name, "role": self.users[uid]["role"]})

        parts = path.strip("/").split("/")  # ["v1", "users", uid, ...]
        if len(parts) >= 3:
            uid = parts[2]
            if uid not in self.users:
                return _error(404, "not_found", f"no such user {uid!r}")
            if not self._is_admin(user_id):
                return _error(404, "not_found", "admin only")

            if method == "PATCH" and len(parts) == 3:
                body = self._json(request)
                user = self.users[uid]
                if "disabled" in body:
                    user["disabled"] = bool(body["disabled"])
                if "role" in body:
                    user["role"] = str(body["role"])
                return httpx.Response(200, json={"id": uid, "name": user["name"], "role": user["role"], "disabled": user["disabled"]})

            if method == "POST" and len(parts) == 4 and parts[3] == "tokens":
                body = self._json(request)
                label = str(body.get("label") or "")[:64]
                token = "h3t_" + secrets.token_hex(16)
                sha8 = self._sha8(token)
                self.tokens[token] = {"user_id": uid, "sha8": sha8, "label": label}
                # Plaintext appears exactly once, in this response body.
                return httpx.Response(201, json={"token": token, "token_info": {"label": label, "sha8": sha8}})

            if method == "DELETE" and len(parts) == 5 and parts[3] == "tokens":
                sha8 = parts[4]
                for plaintext, record in list(self.tokens.items()):
                    if record["sha8"] == sha8 and record["user_id"] == uid:
                        del self.tokens[plaintext]
                        return httpx.Response(204)
                return _error(404, "not_found", f"no token {sha8!r} for user {uid!r}")

        return _error(404, "not_found", f"mock has no route for {method} {path}")

    # ── images / jobs / files ─────────────────────────────────────────
    def _handle_images(self, request: httpx.Request, user_id: str) -> httpx.Response:
        path = request.url.path
        method = request.method

        if method == "POST" and path == "/v1/images/generations":
            return self._submit_generation(request, user_id)

        parts = path.strip("/").split("/")  # ["v1","images","jobs", job_id, ...]
        if len(parts) >= 4 and parts[2] == "jobs":
            job_id = parts[3]
            job = self.jobs.get(job_id)
            if job is None:
                return _error(404, "not_found", "no such job")
            if not self._can_access(job, user_id):
                # Contract: foreign jobs read as 404, never 403.
                return _error(404, "not_found", "no such job")

            if method == "GET" and len(parts) == 4:
                return httpx.Response(200, json=self._job_body(job))

            if method == "GET" and len(parts) == 5 and parts[4] == "file":
                return self._file_response(request, job)

        return _error(404, "not_found", f"mock has no route for {method} {path}")

    def _submit_generation(self, request: httpx.Request, user_id: str) -> httpx.Response:
        body_bytes = request.content
        key = request.headers.get("Idempotency-Key")

        if key:
            prior = self.idempotency.get(key)
            if prior is not None:
                if prior["body_sha"] != hashlib.sha256(body_bytes).hexdigest():
                    return _error(409, "conflict", "same Idempotency-Key with a different request body")
                prior["job"]["polls_in_stage"] = 0  # re-submitting must not advance the job
                return httpx.Response(202, json=self._job_brief(prior["job"]))

        body = self._json_bytes(body_bytes)
        if not str(body.get("prompt") or "").strip():
            return _error(400, "invalid_request", "prompt is required")

        job_id = "img_" + secrets.token_hex(6)
        job = {
            "id": job_id,
            "kind": "generate",
            "owner": user_id,
            "request": body,
            "output": str(body.get("output") or "image"),
            # Status machine: queued → running → completed (per images.md §4.1).
            # polls_in_stage counts GET polls until the next transition.
            "status": "queued",
            "polls_in_stage": self.polls_per_stage,
            "fail": None,
            "created": int(time.time()),
        }
        self.jobs[job_id] = job
        if key:
            self.idempotency[key] = {"job": job, "body_sha": hashlib.sha256(body_bytes).hexdigest()}
        return httpx.Response(202, json=self._job_brief(job))

    def _advance(self, job: dict[str, Any]) -> None:
        if job["status"] in ("completed", "failed", "cancelled"):
            return
        job["polls_in_stage"] -= 1
        if job["polls_in_stage"] > 0:
            return
        if job["status"] == "queued":
            job["status"] = "running"
            job["polls_in_stage"] = self.polls_per_stage
        elif job["status"] == "running":
            if job["fail"]:
                job["status"] = "failed"
                job["error"] = {"code": job["fail"]["code"], "message": job["fail"]["message"]}
            else:
                job["status"] = "completed"
                job["finished"] = int(time.time())

    def _job_brief(self, job: dict[str, Any]) -> dict[str, Any]:
        return {"id": job["id"], "kind": job["kind"], "status": job["status"], "queue_position": 0, "eta_s": 45.0, "progress": 100 if job["status"] == "completed" else 0}

    def _job_body(self, job: dict[str, Any]) -> dict[str, Any]:
        self._advance(job)
        body = self._job_brief(job)
        body.update({"created": job["created"], "owner": job["owner"], "request": job["request"], "mode": job["request"].get("mode"), "data": []})
        if job["status"] == "completed":
            b64 = base64.b64encode(self.file_bytes).decode("ascii")
            url = f"/v1/images/jobs/{job['id']}/file?kind={'video' if job['output'] == 'video' else 'image'}"
            body["data"] = [{"b64_json": None if job["output"] == "video" else b64, "url": url, "v": "1", "meta": {"output": job["output"], "mode": job["request"].get("mode")}}]
        if job["status"] == "failed":
            body["error"] = job["error"]
        return body

    def _file_response(self, request: httpx.Request, job: dict[str, Any]) -> httpx.Response:
        kind = request.url.params.get("kind", "image")
        if kind not in ("image", "video", "thumb", "frame", "ref"):
            return _error(400, "invalid_request", "unknown kind (valid: image, video, thumb, frame, ref)")
        if job["status"] != "completed":
            return _error(404, "not_found", "job has no artifacts yet")
        return httpx.Response(
            200,
            content=self.file_bytes,
            headers={"Content-Type": self.file_content_type, "Cache-Control": "private, max-age=259200, immutable"},
        )

    # ── auth helpers ──────────────────────────────────────────────────
    def _identify(self, request: httpx.Request) -> str | None:
        auth = request.headers.get("Authorization") or ""
        if not auth.startswith("Bearer "):
            return None
        record = self.tokens.get(auth.removeprefix("Bearer ").strip())
        if record is None:
            return None
        user = self.users.get(record["user_id"])
        if user is not None and user["disabled"]:
            return None  # disabled users' tokens stop validating
        return record["user_id"]

    def _is_admin(self, user_id: str) -> bool:
        return self.users[user_id]["role"] == "admin"

    def _can_access(self, job: dict[str, Any], user_id: str) -> bool:
        return self._is_admin(user_id) or job["owner"] == user_id

    @staticmethod
    def _json(request: httpx.Request) -> dict[str, Any]:
        return MockMediaGateway._json_bytes(request.content)

    @staticmethod
    def _json_bytes(raw: bytes) -> dict[str, Any]:
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _sha8(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


# TTL minus a margin, so tests that assert "expires is in the near future" stay stable.
TICKET_TTL_S = _TICKET_TTL_S
TICKET_EXPIRY_MARGIN_S = _TICKET_TTL_WARNING_MARGIN


@pytest.fixture
async def media_gateway_mock():
    """Ready-made mock + admin-configured client.

    Yields a namespace with ``mock`` (:class:`MockMediaGateway`), a ``client``
    already wired to ``mock.transport`` with the admin token, and ``base_url`` /
    ``admin_token`` for building extra clients (e.g. per-user ones).
    """
    mock = MockMediaGateway()
    client = mock.client()
    try:
        yield SimpleNamespace(mock=mock, client=client, base_url=mock.base_url, admin_token=mock.admin_token)
    finally:
        await client.aclose()
