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

Drama series (M2 batch 2, contract ``minimax-h3-gateway/docs/integration/api/drama.md``):

- ``POST /v1/drama``                 → create drama (201, owner = caller)
- ``GET  /v1/drama`` / ``GET /v1/drama/inflight`` / ``GET /v1/drama/{did}``
- ``POST /v1/drama/{did}/chapters``  → chapter (idx unique per drama)
- ``POST …/chapters/{ch}/storylines`` / ``…/plots``
- ``POST …/chapters/{ch}/characters`` (asset registry) + ``GET``
- ``POST …/chapters/{ch}/clips`` / ``GET`` / ``PATCH`` (ref_tray full-replace)
- ``POST …/clips/{cl}/budget`` / ``GET …/prompt_preview`` (compose + checks + valid)
- ``POST/GET …/clips/{cl}/generate`` → single in-flight lease; ``GET …/qc`` reads the
  scripted QC result (``script_qc``); ``POST …/archive`` appends the archive record
- ``POST …/chapters/{ch}/shots`` (save-chain checks: negative wording → dialogue
  speaker → empty English column → clip budget) / ``GET`` / ``PATCH`` / ``DELETE`` (soft)
- ``GET/POST …/chapters/{ch}/partition/preview|commit`` + ``GET …/episodes``

Error envelope everywhere: ``{"error": {code, message, retryable}}``; 429/503
carry ``Retry-After``. Drama routes use the drama contract envelope instead:
``{"detail": {"error": {code, …}}}`` for structured errors (shot save-chain,
budget, in-flight) and ``{"detail": "…"}`` for plain-string errors. Non-admin callers of admin endpoints get 404 (the
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
import re
import secrets
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from deerflow.media_gateway.client import MediaGatewayClient

_DEFAULT_ADMIN_TOKEN = "h3t_mock-admin-token"

# ── drama contract constants (drama.md) ───────────────────────────────
DRAMA_BUDGET_TIERS = (1, 5, 10, 15)
DRAMA_NEGATIVE_WORDS = ("不要", "别让")
_PICTURE_TOKEN_RE = re.compile(r"<(?:picture|图片)\s*(\d+)>", re.IGNORECASE)
_ID_HEX = "0123456789abcdef"

_QC_PASS_BODY = {
    "state": "qc_pass",
    "scores": {"identity": 0.92, "ball": None, "seam": None, "motion": 0.9, "space": 0.9},
    "issues": [],
    "must_fix_storyboard": False,
}


def _drama_detail_error(status: int, code: str, message: str, **payload: Any) -> httpx.Response:
    """Drama structured error: ``{"detail": {"error": {code, …}}}`` (drama.md §1)."""
    body: dict[str, Any] = {"code": code, "message": message}
    body.update(payload)
    return httpx.Response(status, json={"detail": {"error": body}})


def _drama_string_error(status: int, message: str) -> httpx.Response:
    return httpx.Response(status, json={"detail": message})


def _drama_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def _fmt_s(value: float) -> str:
    return f"{value:g}"


def _token_numbers(shot: dict[str, Any]) -> list[int]:
    """Derived ``tray_tokens_used``: ``<Picture N>`` numbers across both prompt columns."""
    text = f"{shot.get('prompt_en') or ''}{shot.get('prompt_zh') or ''}"
    return sorted({int(n) for n in _PICTURE_TOKEN_RE.findall(text)})

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
        self.media: dict[str, dict[str, Any]] = {}  # INSERT-GROUPVID: 素材库 media（groupvid.md / media.md）
        self.file_bytes = file_bytes
        self.file_content_type = file_content_type

        # Drama domain (contract: drama.md). All state lives in-process; owner
        # isolation follows the job model (admin sees all, others see their own).
        self.dramas: dict[str, dict[str, Any]] = {}
        self.qc_script: dict[str, list[dict[str, Any]]] = {}  # clip_id → queued QC bodies

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

    def script_qc(self, clip_id: str, results: list[dict[str, Any]]) -> None:
        """Queue QC bodies for ``clip_id``: the n-th ``GET …/qc`` returns the n-th
        entry; when the queue is empty a clean ``qc_pass`` body is served."""
        self.qc_script[clip_id] = list(results)

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
        if path.startswith("/v1/media"):
            return self._handle_media(request, user_id)  # INSERT-GROUPVID routing
        if path.startswith("/v1/drama"):
            return self._handle_drama(request, user_id)

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

    # ── drama (contract: minimax-h3-gateway docs/integration/api/drama.md) ──
    def _new_id(self, prefix: str) -> str:
        return prefix + secrets.token_hex(4)

    def _drama_owned(self, drama: dict[str, Any] | None, user_id: str) -> dict[str, Any] | None:
        """Owner isolation: foreign dramas read as 404 (merged, anti-probing)."""
        if drama is None:
            return None
        if self._is_admin(user_id) or drama["owner"] == user_id:
            return drama
        return None

    def _find_chapter(self, drama: dict[str, Any], ch_id: str) -> dict[str, Any] | None:
        return next((c for c in drama["chapters"] if c["id"] == ch_id), None)

    def _clip_shots(self, drama: dict[str, Any], clip_id: str) -> list[dict[str, Any]]:
        return [s for s in drama["shots"] if s["clip_id"] == clip_id and not s["deleted"]]

    def _budget_used(self, drama: dict[str, Any], clip_id: str) -> float:
        return round(sum(s["duration_s"] for s in self._clip_shots(drama, clip_id)), 2)

    def _handle_drama(self, request: httpx.Request, user_id: str) -> httpx.Response:
        path = request.url.path
        method = request.method
        body = self._json(request)
        parts = path.strip("/").split("/")  # ["v1","drama", ...]

        if method == "POST" and len(parts) == 2:
            return self._drama_create(body, user_id)
        if method == "GET" and len(parts) == 2:
            return self._drama_list(user_id)
        if len(parts) == 3 and parts[2] == "inflight":
            items = []
            for drama in self.dramas.values():
                if not self._drama_owned(drama, user_id):
                    continue
                for clip in drama["clips"]:
                    if clip["state"] in ("generating", "qc_pending", "qc_running"):
                        items.append({"drama_id": drama["id"], "drama_title": drama["title"], "ch_id": clip["chapter_id"], "clip_id": clip["id"], "state": clip["state"], "version": clip["version"]})
            return httpx.Response(200, json={"items": items, "total": len(items)})

        if len(parts) < 3:
            return _drama_string_error(404, "drama not found")
        drama = self._drama_owned(self.dramas.get(parts[2]), user_id)
        if drama is None:
            return _drama_string_error(404, "drama not found")

        # ── drama level ───────────────────────────────────────────────
        if method == "GET" and len(parts) == 3:
            return httpx.Response(200, json=drama["doc"])
        if method == "POST" and len(parts) == 4 and parts[3] == "chapters":
            return self._chapter_create(drama, body)
        if method == "POST" and len(parts) == 4 and parts[3] == "characters":
            return self._character_create(drama, body)
        if method == "GET" and len(parts) == 4 and parts[3] == "characters":
            items = [c for c in drama["characters"] if not c["deleted"]]
            return httpx.Response(200, json={"items": items, "total": len(items)})

        if len(parts) < 5 or parts[3] != "chapters":
            return _drama_string_error(404, "drama not found")
        chapter = self._find_chapter(drama, parts[4])
        if chapter is None:
            return _drama_string_error(404, "drama not found")

        # ── chapter level ─────────────────────────────────────────────
        if method == "POST" and len(parts) == 6 and parts[5] == "storylines":
            return self._storyline_create(drama, chapter, body)
        if method == "GET" and len(parts) == 6 and parts[5] == "storylines":
            items = sorted([s for s in drama["storylines"] if s["chapter_id"] == chapter["id"]], key=lambda s: s["order"])
            return httpx.Response(200, json={"items": items, "total": len(items)})
        if method == "POST" and len(parts) == 6 and parts[5] == "plots":
            return self._plot_create(drama, chapter, body)
        if method == "GET" and len(parts) == 6 and parts[5] == "plots":
            items = sorted([p for p in drama["plots"] if p["chapter_id"] == chapter["id"]], key=lambda p: p["order"])
            return httpx.Response(200, json={"items": items, "total": len(items)})
        if method == "GET" and len(parts) == 6 and parts[5] == "clips":
            return self._clip_list(drama, chapter, request.url.params.get("plot_id"))
        if method == "POST" and len(parts) == 6 and parts[5] == "clips":
            return self._clip_create(drama, chapter, body)
        if method == "GET" and len(parts) == 6 and parts[5] == "shots":
            clip_filter = request.url.params.get("clip_id")
            shots = [s for s in drama["shots"] if not s["deleted"] and (clip_filter is None or s["clip_id"] == clip_filter)]
            return httpx.Response(200, json={"items": shots, "total": len(shots)})
        if method == "POST" and len(parts) == 6 and parts[5] == "shots":
            return self._shot_create(drama, chapter, body)
        if parts[5] == "partition" and len(parts) == 7:
            if method == "GET" and parts[6] == "preview":
                return self._partition_preview(drama, chapter, float(request.url.params.get("target_episode_s") or 0))
            if method == "POST" and parts[6] == "commit":
                return self._partition_commit(drama, chapter, body)
        if method == "GET" and len(parts) == 6 and parts[5] == "episodes":
            eps = chapter.get("episodes_snapshot") or []
            items = [{**e, "id": f"ep{e['idx']:02d}"} for e in eps]
            return httpx.Response(200, json={"items": items, "total": len(items)})

        if len(parts) < 7:
            return _drama_string_error(404, "drama not found")
        target = parts[6]

        # ── clip level ────────────────────────────────────────────────
        if parts[5] == "clips":
            clip = next((c for c in drama["clips"] if c["id"] == target and c["chapter_id"] == chapter["id"]), None)
            if clip is None:
                return _drama_string_error(404, "drama not found")
            if method == "GET" and len(parts) == 7:
                return httpx.Response(200, json=clip)
            if method == "PATCH" and len(parts) == 7:
                return self._clip_patch(drama, chapter, clip, body)
            if len(parts) == 8:
                sub = parts[7]
                if method == "POST" and sub == "budget":
                    used = self._budget_used(drama, clip["id"])
                    nearest = next((t for t in DRAMA_BUDGET_TIERS if used <= t), None)
                    return httpx.Response(200, json={"tier": clip["budget_tier"], "used_s": used, "ok": nearest is not None and nearest <= clip["budget_tier"], "nearest_tier": nearest})
                if method == "GET" and sub == "prompt_preview":
                    return self._prompt_preview(drama, clip)
                if method == "POST" and sub == "generate":
                    return self._generate_submit(drama, chapter, clip)
                if method == "GET" and sub == "generate":
                    return self._generate_status(drama, clip)
                if method == "GET" and sub == "qc":
                    return self._qc_read(drama, clip)
                if method == "POST" and sub == "archive":
                    record = {"version": int(body.get("version") or clip["version"]), "reason": str(body.get("reason") or ""), "hash": secrets.token_hex(4), "at": _drama_now(), "by": user_id}
                    clip["archives"].append(record)
                    return httpx.Response(200, json=record)

        # ── shot level ────────────────────────────────────────────────
        if parts[5] == "shots" and len(parts) == 7:
            shot = next((s for s in drama["shots"] if s["id"] == target), None)
            if shot is None:
                return _drama_string_error(404, "drama not found")
            if method == "GET":
                return httpx.Response(200, json=shot)
            if method == "PATCH":
                target_clip = next((c for c in drama["clips"] if c["id"] == shot["clip_id"]), None)
                if target_clip is None:
                    return _drama_string_error(404, "drama not found")
                return self._shot_patch(drama, target_clip, shot, body)
            if method == "DELETE":
                shot["deleted"] = True
                return httpx.Response(200, json={"deleted": True, "shot_id": shot["id"]})

        return _drama_string_error(404, "drama not found")

    # ── drama/chapter/asset creation ──────────────────────────────────
    def _drama_create(self, body: dict[str, Any], user_id: str) -> httpx.Response:
        title = str(body.get("title") or "").strip()
        if not title:
            return _drama_string_error(400, "title is required")
        did = self._new_id("dr_")
        doc = {"id": did, "title": title, "synopsis": str(body.get("synopsis") or ""), "rules": list(body.get("rules") or []), "owner": user_id, "archived": False, "rule_fingerprint": self._sha8(title), "created_at": _drama_now()}
        self.dramas[did] = {"id": did, "doc": doc, "owner": user_id, "chapters": [], "characters": [], "plots": [], "storylines": [], "clips": [], "shots": [], "gen_inflight": None}
        return httpx.Response(201, json=doc)

    def _drama_list(self, user_id: str) -> httpx.Response:
        items = [d["doc"] for d in self.dramas.values() if self._drama_owned(d, user_id)]
        return httpx.Response(200, json={"items": items, "total": len(items)})

    def _chapter_create(self, drama: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        idx = int(body.get("idx") or 0)
        if any(c["idx"] == idx for c in drama["chapters"]):
            return _drama_string_error(400, f"章节序号 {idx} 已存在")
        chapter = {
            "id": self._new_id("ch_"),
            "drama_id": drama["id"],
            "idx": idx,
            "title": str(body.get("title") or ""),
            "outline": str(body.get("outline") or ""),
            "storyline_count": 0,
            "shot_count": 0,
            "episode_count": 0,
            "script_versions": [],
            "episodes_snapshot": None,
            "created_at": _drama_now(),
        }
        drama["chapters"].append(chapter)
        return httpx.Response(201, json=chapter)

    def _character_create(self, drama: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        name = str(body.get("name") or "").strip()
        if any(c["name"] == name and not c["deleted"] for c in drama["characters"]):
            return _drama_string_error(400, f"characters 重名：{name}")
        character = {"name": name, "subject_id": int(body.get("subject_id") or 0), "refs": list(body.get("refs") or []), "costume_variant": list(body.get("costume_variant") or []), "deleted": False, "images": [], "created_at": _drama_now()}
        drama["characters"].append(character)
        return httpx.Response(201, json=character)

    def _storyline_create(self, drama: dict[str, Any], chapter: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        order = int(body.get("order") or 0)
        if any(s["order"] == order for s in drama["storylines"] if s["chapter_id"] == chapter["id"]):
            return _drama_string_error(400, f"故事线序号 {order} 已存在")
        storyline = {"id": self._new_id("sl_"), "chapter_id": chapter["id"], "name": str(body.get("name") or ""), "order": order}
        drama["storylines"].append(storyline)
        return httpx.Response(201, json=storyline)

    def _plot_create(self, drama: dict[str, Any], chapter: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        order = int(body.get("order") or 0)
        storyline_id = str(body.get("storyline_id") or "")
        if not any(s["id"] == storyline_id for s in drama["storylines"]):
            return _drama_string_error(404, "drama not found")
        if any(p["order"] == order for p in drama["plots"] if p["chapter_id"] == chapter["id"]):
            return _drama_string_error(400, f"情节序号 {order} 已存在")
        plot = {"id": self._new_id("pl_"), "chapter_id": chapter["id"], "storyline_id": storyline_id, "name": str(body.get("name") or ""), "order": order}
        drama["plots"].append(plot)
        return httpx.Response(201, json=plot)

    # ── clips ─────────────────────────────────────────────────────────
    def _clip_create(self, drama: dict[str, Any], chapter: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        tier = int(body.get("budget_tier") or 0)
        if tier not in DRAMA_BUDGET_TIERS:
            return _drama_string_error(400, f"budget_tier 须为 {DRAMA_BUDGET_TIERS} 之一，收到 {tier}")
        plot_id = str(body.get("plot_id") or "")
        if not any(p["id"] == plot_id for p in drama["plots"]):
            return _drama_string_error(404, "drama not found")
        clip = {
            "id": self._new_id("cl_"),
            "chapter_id": chapter["id"],
            "plot_id": plot_id,
            "order": int(body.get("order") or 1),
            "budget_tier": tier,
            "version": 1,
            "state": "pending",
            "stale": False,
            "archives": [],
            "prompt_opening": str(body.get("prompt_opening") or ""),
            "prompt_ending": str(body.get("prompt_ending") or ""),
            "ref_tray": [dict(t) for t in body.get("ref_tray") or []],
            "scene_slot": body.get("scene_slot"),
            "created_at": _drama_now(),
        }
        drama["clips"].append(clip)
        return httpx.Response(201, json=clip)

    def _clip_list(self, drama: dict[str, Any], chapter: dict[str, Any], plot_id: str | None) -> httpx.Response:
        plots = {p["id"]: p["order"] for p in drama["plots"] if p["chapter_id"] == chapter["id"]}
        clips = [c for c in drama["clips"] if c["chapter_id"] == chapter["id"] and (plot_id is None or c["plot_id"] == plot_id)]
        clips = sorted(clips, key=lambda c: (plots.get(c["plot_id"], 0), c["order"]))
        items = []
        for clip in clips:
            shots = self._clip_shots(drama, clip["id"])
            tray = [dict(t, desc=None) for t in clip["ref_tray"]]
            items.append({
                **clip,
                "ref_tray": tray,
                "shot_ids": [s["id"] for s in shots],
                "budget_used_s": self._budget_used(drama, clip["id"]),
                "shots": {
                    s["id"]: {
                        "order": s["order"], "duration_s": s["duration_s"], "camera": s["camera"], "size": s["size"],
                        "state": clip["state"], "prompt_en": s["prompt_en"], "prompt_zh": s["prompt_zh"],
                        "dialogue": s["dialogue"], "tray_tokens_used": _token_numbers(s), "scene_id": s["scene_id"], "time": s["time"],
                    }
                    for s in shots
                },
            })
        return httpx.Response(200, json={"items": items, "total": len(items)})

    def _clip_patch(self, drama: dict[str, Any], chapter: dict[str, Any], clip: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        allowed = ("plot_id", "order", "budget_tier", "prompt_opening", "prompt_ending", "ref_tray", "scene_slot")
        if not any(k in body for k in allowed):
            return _drama_string_error(400, "nothing to patch")
        if "budget_tier" in body:
            tier = int(body["budget_tier"])
            if tier not in DRAMA_BUDGET_TIERS:
                return _drama_string_error(400, f"budget_tier 非法：{tier}")
            clip["budget_tier"] = tier
        if "plot_id" in body and not any(p["id"] == body["plot_id"] for p in drama["plots"]):
            return _drama_string_error(404, "drama not found")
        for key in ("plot_id", "order", "prompt_opening", "prompt_ending", "scene_slot"):
            if key in body:
                clip[key] = body[key]
        if "ref_tray" in body:
            old_numbers = {str(t.get("media_id")): i + 1 for i, t in enumerate(clip["ref_tray"]) if t.get("media_id")}
            new_numbers = {str(t.get("media_id")): i + 1 for i, t in enumerate(body["ref_tray"]) if t.get("media_id")}
            clip["ref_tray"] = [dict(t) for t in body["ref_tray"]]
            for shot in self._clip_shots(drama, clip["id"]):
                for column in ("prompt_en", "prompt_zh"):
                    text = shot[column]
                    if text.startswith("IMPORTED (see prompt_zh)"):
                        continue
                    for media_id, old_n in old_numbers.items():
                        token = f"<Picture {old_n}>"
                        if media_id not in new_numbers:
                            text = text.replace(token + " ", "").replace(token, "")  # removed → strip token
                        elif new_numbers[media_id] != old_n:
                            text = text.replace(token, f"<Picture {new_numbers[media_id]}>")
                    shot[column] = text
        # Budget re-check after any change (contract: enforced, not exusable).
        used = self._budget_used(drama, clip["id"])
        nearest = next((t for t in DRAMA_BUDGET_TIERS if used <= t), None)
        if nearest is None or nearest > clip["budget_tier"]:
            return _drama_detail_error(400, "clip_over_budget", "片段时长超档", used_s=used, budget_tier=clip["budget_tier"], nearest_tier=nearest)
        return httpx.Response(200, json=clip)

    # ── shots (save-chain checks, drama.md §1/§10) ────────────────────
    def _shot_check(self, drama: dict[str, Any], clip: dict[str, Any], shot: dict[str, Any]) -> httpx.Response | None:
        """Return the first save-chain violation, or None when clean."""
        text = f"{shot['prompt_en']}\n{shot['prompt_zh']}"
        for word in DRAMA_NEGATIVE_WORDS:
            if word in text:
                return _drama_detail_error(400, "negative_wording", "负向措辞", violations=[{"match": word, "rule": word, "suggest": f"写『是什么/在哪』，避免『{word}』"}])
        for line in shot["dialogue"]:
            if not str(line.get("speaker") or "").strip():
                return _drama_detail_error(400, "dialogue_unassigned", "dialogue 条目缺 speaker")
        if not shot["prompt_en"].strip():
            return _drama_detail_error(400, "prompt_en_empty", "英文列为空")
        return None

    def _budget_check(self, drama: dict[str, Any], clip: dict[str, Any]) -> httpx.Response | None:
        used = self._budget_used(drama, clip["id"])
        nearest = next((t for t in DRAMA_BUDGET_TIERS if used <= t), None)
        if nearest is None or nearest > clip["budget_tier"]:
            return _drama_detail_error(400, "clip_over_budget", "片段时长超档", used_s=used, budget_tier=clip["budget_tier"], nearest_tier=nearest)
        return None

    def _shot_create(self, drama: dict[str, Any], chapter: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        clip_id = str(body.get("clip_id") or "")
        clip = next((c for c in drama["clips"] if c["id"] == clip_id), None)
        if clip is None:
            return _drama_string_error(404, "drama not found")
        shot = {
            "id": self._new_id("sh_"),
            "chapter_id": chapter["id"],
            "clip_id": clip_id,
            "order": int(body.get("order") or 1),
            "camera": str(body.get("camera") or ""),
            "size": str(body.get("size") or ""),
            "duration_s": float(body.get("duration_s") or 0),
            "prompt_en": str(body.get("prompt_en") or ""),
            "prompt_zh": str(body.get("prompt_zh") or ""),
            "dialogue": list(body.get("dialogue") or []),
            "scene_id": body.get("scene_id"),
            "time": body.get("time"),
            "deleted": False,
            "warnings_confirmed": [],
            "created_at": _drama_now(),
        }
        if not body.get("force"):
            violation = self._shot_check(drama, clip, shot)
            if violation is not None:
                return violation
        drama["shots"].append(shot)
        return self._budget_check(drama, clip) or httpx.Response(201, json=shot)

    def _shot_patch(self, drama: dict[str, Any], clip: dict[str, Any], shot: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        allowed = ("clip_id", "order", "camera", "size", "duration_s", "prompt_en", "prompt_zh", "dialogue", "scene_id", "time")
        if not any(k in body for k in allowed):
            return _drama_string_error(400, "nothing to patch")
        for key in allowed:
            if key in body:
                shot[key] = body[key]
        if not body.get("force"):
            violation = self._shot_check(drama, clip, shot)
            if violation is not None:
                return violation
        return self._budget_check(drama, clip) or httpx.Response(200, json=shot)

    # ── prompt preview (compose, drama.md §9.6) ───────────────────────
    def _prompt_preview(self, drama: dict[str, Any], clip: dict[str, Any]) -> httpx.Response:
        segments: list[dict[str, Any]] = []
        prompt_parts: list[str] = []
        pictures = []
        n = 0
        for entry in clip["ref_tray"]:
            if str(entry.get("media_id") or "").strip():
                n += 1
                pictures.append({"n": n, "media_id": entry["media_id"], "kind": entry.get("kind") or "", "name": entry.get("name") or "", "source": f"tray[{n - 1}]"})
        slot = clip.get("scene_slot") or {}
        if slot.get("note"):
            segments.append({"kind": "scene_note", "ref": "scene_slot", "text": f"（场景说明）{slot['note']}"})
            prompt_parts.append(segments[-1]["text"])
        if clip["prompt_opening"]:
            segments.append({"kind": "opening", "ref": "clip", "text": clip["prompt_opening"]})
            prompt_parts.append(clip["prompt_opening"])
        shots = sorted(self._clip_shots(drama, clip["id"]), key=lambda s: s["order"])
        violations: list[dict[str, str]] = []
        negative_hits: list[dict[str, str]] = []
        used_tokens: set[int] = set()
        for i, shot in enumerate(shots, start=1):
            text = shot["prompt_zh"].strip() or shot["prompt_en"].strip()
            full = f"[Shot {i} · {_fmt_s(shot['duration_s'])}] {text}"
            segments.append({"kind": "shot", "ref": shot["id"], "duration_s": shot["duration_s"], "text": full if text else ""})
            if text:
                prompt_parts.append(full)
            for token in _PICTURE_TOKEN_RE.findall(full):
                num = int(token)
                used_tokens.add(num)
                if num > n:
                    violations.append({"shot_id": shot["id"], "token": f"<Picture {num}>"})
            for word in DRAMA_NEGATIVE_WORDS:
                if word in full:
                    negative_hits.append({"match": word, "rule": word, "suggest": f"写『是什么/在哪』，避免『{word}』"})
        if clip["prompt_ending"]:
            segments.append({"kind": "ending", "ref": "clip", "text": clip["prompt_ending"]})
            prompt_parts.append(clip["prompt_ending"])
        prompt = "\n".join(prompt_parts)
        unused = [p["name"] or p["media_id"] for p in pictures if p["n"] not in used_tokens]
        token_match = not violations
        checks = {
            "negative_wording": "fail" if negative_hits else "pass",
            "negative_hits": negative_hits,
            "token_manifest_match": token_match,
            "violations": violations,
            "unused_tray_refs": unused,
            "scene_slot_missing": bool(slot) and not slot.get("note"),
            "scene_slot_absent": not slot,
            "scene_link_dangling": bool(slot.get("media_id")) and slot["media_id"] not in {p["media_id"] for p in pictures},
            "warnings": [],
        }
        return httpx.Response(200, json={"prompt": prompt, "segments": segments, "pictures": pictures, "scene_slot": slot or {}, "checks": checks, "valid": token_match and not negative_hits})

    # ── generate / qc ─────────────────────────────────────────────────
    def _generate_submit(self, drama: dict[str, Any], chapter: dict[str, Any], clip: dict[str, Any]) -> httpx.Response:
        inflight = drama["gen_inflight"]
        if inflight is not None and inflight["clip_id"] != clip["id"]:
            return _drama_detail_error(409, "single_job_in_flight", f"已有在途作业 {inflight['job_id']}（片段 {inflight['clip_id']}）；一次只排一笔", job_id=inflight["job_id"], clip_id=inflight["clip_id"])
        if clip["stale"]:
            return _drama_detail_error(409, "stale_partition", "先确认重算（partition/commit）")
        if clip["state"] != "pending" and all(a["version"] != clip["version"] for a in clip["archives"]):
            return _drama_detail_error(409, "previous_version_not_archived", f"重出前须先归档 v{clip['version']}")
        if clip["state"] not in ("pending", "qc_pass", "qc_ml", "qc_sl", "failed"):
            return _drama_detail_error(400, "clip_state_not_submittable", f"state={clip['state']} 不可提交")
        job_id = "job_" + secrets.token_hex(4)
        drama["gen_inflight"] = {"job_id": job_id, "clip_id": clip["id"], "submitted_at": time.time()}
        clip["state"] = "generating"
        return httpx.Response(202, json={"job_id": job_id, "clip_id": clip["id"], "state": "generating"})

    def _generate_status(self, drama: dict[str, Any], clip: dict[str, Any]) -> httpx.Response:
        inflight = drama["gen_inflight"]
        if inflight is None or inflight["clip_id"] != clip["id"]:
            return _drama_string_error(404, "no in-flight job")
        # One poll moves generating → qc_pending (the engine "finished").
        if clip["state"] == "generating":
            clip["state"] = "qc_pending"
        return httpx.Response(200, json={"state": clip["state"], "drama_id": drama["id"], "ch_id": clip["chapter_id"], "clip_id": clip["id"], "job_id": inflight["job_id"], "submitted_at": inflight["submitted_at"]})

    def _qc_read(self, drama: dict[str, Any], clip: dict[str, Any]) -> httpx.Response:
        scripted = self.qc_script.get(clip["id"])
        body = scripted.pop(0) if scripted else dict(_QC_PASS_BODY)
        body = json.loads(json.dumps(body))  # deep copy so scripted dicts stay pristine
        clip["state"] = body["state"]
        if body["state"] in ("qc_pass", "qc_ml", "qc_sl"):
            drama["gen_inflight"] = None  # lease released when QC concludes
        return httpx.Response(200, json=body)

    # ── partition (drama.md §14) ──────────────────────────────────────
    def _partition(self, drama: dict[str, Any], chapter: dict[str, Any], target_s: float) -> list[dict[str, Any]]:
        plots = {p["id"]: p["order"] for p in drama["plots"] if p["chapter_id"] == chapter["id"]}
        chapter_clips = [c for c in drama["clips"] if c["chapter_id"] == chapter["id"]]
        clips_by_id = {c["id"]: c for c in chapter_clips}
        episodes: list[dict[str, Any]] = []
        for clip in sorted(chapter_clips, key=lambda c: (plots.get(c["plot_id"], 0), c["order"])):
            used = self._budget_used(drama, clip["id"])
            prev = clips_by_id.get(episodes[-1]["clip_ids"][-1]) if episodes else None
            boundary_break = prev is not None and plots.get(prev["plot_id"]) != plots.get(clip["plot_id"])
            if prev is not None and not boundary_break and episodes[-1]["duration_s"] + used <= target_s:
                episodes[-1]["duration_s"] = round(episodes[-1]["duration_s"] + used, 2)
                episodes[-1]["clip_ids"].append(clip["id"])
            else:
                episodes.append({"idx": len(episodes) + 1, "duration_s": round(used, 2), "clip_ids": [clip["id"]], "break_reason": "plot_boundary" if prev is not None else "duration"})
        return episodes

    def _partition_preview(self, drama: dict[str, Any], chapter: dict[str, Any], target: float) -> httpx.Response:
        return httpx.Response(200, json={"episodes": self._partition(drama, chapter, target), "stale": False, "diff": None})

    def _partition_commit(self, drama: dict[str, Any], chapter: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        episodes = self._partition(drama, chapter, float(body.get("target_episode_s") or 60))
        chapter["episodes_snapshot"] = episodes
        return httpx.Response(200, json={"episodes": len(episodes), "unchanged_from": 1})

    # ── INSERT-GROUPVID: 组图生视频 media 端点（groupvid.md 流水线 A）──────
    # 建模：POST /v1/media（multipart 上传 kind=images）→ POST/GET …/analyze →
    # POST/GET …/storyboard、PATCH …/storyboard/shots/{index} →
    # POST/GET …/generate（逐镜 ref2va 作业，复用上面的 jobs 状态机）。
    # 逐镜作业本体走既有 /v1/images/jobs/{id}(/file)（jobs.md：产物统一
    # /file?kind=video）。
    def _handle_media(self, request: httpx.Request, user_id: str) -> httpx.Response:
        path = request.url.path
        method = request.method

        if method == "POST" and path == "/v1/media":
            return self._media_upload(request, user_id)

        parts = path.strip("/").split("/")  # ["v1","media", media_id, ...]
        if len(parts) < 3:
            return _error(404, "not_found", f"mock has no route for {method} {path}")
        media = self.media.get(parts[2])
        if media is None or not (self._is_admin(user_id) or media["owner"] == user_id):
            # Contract: foreign media reads as 404 (media.md 归属与可见性).
            return _error(404, "not_found", "media 不存在")
        rest = parts[3:]

        if not rest and method == "GET":
            return httpx.Response(200, json=self._media_public(media, detail=True))
        if rest == ["analyze"] and method == "POST":
            media["analyzed"] = True
            return httpx.Response(200, json={"stage": "completed", "progress": 100, "shots": [], "media_id": media["id"]})
        if rest == ["analysis"] and method == "GET":
            if not media["analyzed"]:
                return _error(400, "invalid_request", "还没有组图分析结果")
            return httpx.Response(200, json={"stage": "completed", "progress": 100, "shots": [], "media_id": media["id"]})
        if rest == ["storyboard"] and method == "POST":
            return self._storyboard_design(media, self._json(request))
        if rest == ["storyboard"] and method == "GET":
            storyboard = media.get("storyboard")
            if storyboard is None:
                return _error(404, "not_found", "还没有组图分镜")
            return httpx.Response(200, json=storyboard)
        if len(rest) == 3 and rest[0] == "storyboard" and rest[1] == "shots" and method == "PATCH":
            return self._storyboard_patch(media, int(rest[2]), self._json(request))
        if rest == ["generate"] and method == "POST":
            return self._groupvid_generate(request, media, user_id)
        if rest == ["generate"] and method == "GET":
            record = media.get("generate")
            if record is None:
                return _error(404, "not_found", "还没有组图生视频提交记录")
            return httpx.Response(200, json=record)
        return _error(404, "not_found", f"mock has no route for {method} {path}")

    def _media_upload(self, request: httpx.Request, user_id: str) -> httpx.Response:
        fields, uploads = self._parse_multipart(request)
        if not uploads:
            return _error(400, "invalid_request", "file is required")
        kinds = {ext for _, filename, _ in uploads if (ext := filename.rsplit(".", 1)[-1].lower()) not in ("png", "jpg", "jpeg", "webp")}
        kind = str(fields.get("kind") or ("images" if not kinds else "video"))
        if kind not in ("images", "video"):
            return _error(400, "invalid_request", "kind must be video or images")
        media_id = "med_" + secrets.token_hex(6)
        probe_images = [{"width": 1024, "height": 1024, "index": i + 1} for i in range(len(uploads))]
        media: dict[str, Any] = {
            "id": media_id,
            "kind": kind,
            "owner": user_id,
            "created": int(time.time()),
            "stage": "uploaded",
            "title": str(fields.get("title") or ""),
            "description": str(fields.get("description") or "") or None,
            "count": len(uploads),
            "probe": {"images": probe_images} if kind == "images" else {"duration_s": 10.0},
            "analyzed": False,
            "storyboard": None,
            "generate": None,
        }
        self.media[media_id] = media
        return httpx.Response(201, json=self._media_public(media))

    def _media_public(self, media: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
        body = {
            "id": media["id"],
            "kind": media["kind"],
            "owner": media["owner"],
            "created": media["created"],
            "stage": media["stage"],
            "probe": media["probe"],
            "title": media["title"],
            "description": media["description"],
            "thumb_url": f"/v1/media/{media['id']}/file?kind=thumb",
            "original_url": f"/v1/media/{media['id']}/file?kind=original",
        }
        if detail:
            body["has_plan"] = False
            body["image_count"] = media["count"]
        return body

    @staticmethod
    def _parse_multipart(request: httpx.Request) -> tuple[dict[str, str], list[tuple[str, str, bytes]]]:
        """Crude multipart/form-data parse: (form fields, file uploads)."""
        boundary = ""
        for part in (request.headers.get("content-type") or "").split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary=") :].strip('"')
        fields: dict[str, str] = {}
        uploads: list[tuple[str, str, bytes]] = []
        if not boundary:
            return fields, uploads
        for section in request.content.split(f"--{boundary}".encode()):
            section = section.strip(b"\r\n")
            if not section or section == b"--":
                continue
            head, _, body = section.partition(b"\r\n\r\n")
            headers = head.decode("utf-8", "replace")
            name = ""
            filename = ""
            for line in headers.split("\r\n"):
                if line.lower().startswith("content-disposition"):
                    for token in line.split(";"):
                        token = token.strip()
                        if token.startswith("name="):
                            name = token[5:].strip('"')
                        elif token.startswith("filename="):
                            filename = token[9:].strip('"')
            if filename:
                uploads.append((name, filename, body))
            elif name:
                fields[name] = body.decode("utf-8", "replace")
        return fields, uploads

    def _storyboard_design(self, media: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        if media["kind"] != "images":
            return _error(400, "invalid_request", "组图功能只用于图片类上传物（kind=images）")
        if not media["analyzed"]:
            return _error(400, "invalid_request", "还没有组图分析结果；先 POST /analyze（图片类）")
        shot_count = min(max(int(body.get("shot_count") or 3), 1), 10)
        duration_tier = int(body.get("duration_tier") or 5)
        notes = body.get("notes")
        shots = []
        for index in range(1, shot_count + 1):
            ref = min(index, media["count"])
            shots.append(
                {
                    "index": index,
                    "subject": f"1位与参考图{ref}一致的主体",
                    "action": f"主体从参考图{ref}的姿势开始，完成第{index}个分镜动作",
                    "camera": "固定机位" if index % 2 else "缓推",
                    "soundscape": "环境声",
                    "music": "轻配乐",
                    "duration_tier": duration_tier if duration_tier in (1, 5, 10, 15) else None,
                    "refs": [ref],
                }
            )
        storyboard = {"version": 1, "created": int(time.time()), "params": {"shot_count": shot_count, "duration_tier": duration_tier, "notes": notes}, "shots": shots}
        media["storyboard"] = storyboard
        return httpx.Response(200, json=storyboard)

    def _storyboard_patch(self, media: dict[str, Any], index: int, body: dict[str, Any]) -> httpx.Response:
        storyboard = media.get("storyboard")
        if storyboard is None:
            return _error(404, "not_found", "还没有组图分镜")
        shot = next((s for s in storyboard["shots"] if s["index"] == index), None)
        if shot is None:
            return _error(404, "not_found", f"没有第 {index} 个分镜")
        count = int(media["count"])
        refs = body.get("refs")
        if refs is not None:
            if not refs:
                return _error(400, "invalid_request", "refs 不能为空")
            for ref in refs:
                if not 1 <= int(ref) <= count:
                    return _error(400, "invalid_request", f"参考图编号越界：{ref}（共 {count} 张）")
        for key in ("subject", "action", "camera", "soundscape", "music", "refs", "duration_tier"):
            if key in body and body[key] is not None:
                shot[key] = body[key]
        shot["edited"] = True
        return httpx.Response(200, json={"shot": shot})

    def _groupvid_generate(self, request: httpx.Request, media: dict[str, Any], user_id: str) -> httpx.Response:
        if media["kind"] != "images":
            return _error(400, "invalid_request", "组图功能只用于图片类上传物（kind=images）")
        storyboard = media.get("storyboard")
        if storyboard is None:
            return _error(400, "invalid_request", "还没有分镜设计（先 POST /storyboard）")
        body_bytes = request.content
        body = self._json_bytes(body_bytes)
        key = request.headers.get("Idempotency-Key")
        idem = getattr(self, "_groupvid_idempotency", None)
        if idem is None:
            idem = self._groupvid_idempotency = {}
        if key:
            prior = idem.get(key)
            if prior is not None:
                if prior["body_sha"] != hashlib.sha256(body_bytes).hexdigest():
                    return _error(409, "conflict", "same Idempotency-Key with a different request body")
                return httpx.Response(200, json=prior["response"])

        wanted = body.get("shots")
        shots = [s for s in storyboard["shots"] if wanted is None or s["index"] in set(int(i) for i in wanted)]
        if not shots:
            return _error(400, "invalid_request", "没有这些分镜")
        candidates = min(max(int(body.get("candidates") or 1), 1), 3)
        duration_tier = int(body.get("duration_tier") or (storyboard["params"].get("duration_tier") or 5))
        jobs = []
        for shot in shots:
            for candidate in range(candidates):
                job_id = "ref2va_" + secrets.token_hex(6)
                self.jobs[job_id] = {
                    "id": job_id,
                    "kind": "ref2va",
                    "owner": user_id,
                    "request": {"prompt": f"{shot['subject']}：{shot['action']}（{shot['camera']}）", "mode": "ref2va-max", "output": "video"},
                    "output": "video",
                    "status": "queued",
                    "polls_in_stage": self.polls_per_stage,
                    "fail": None,
                    "created": int(time.time()),
                }
                jobs.append({"shot": shot["index"], "job_id": job_id, "candidate": candidate, "refs": shot["refs"], "duration_tier": shot.get("duration_tier") or duration_tier})
        response = {
            "jobs": jobs,
            "count": len(jobs),
            "params": {"mode": "ref2va-max", "aspect": "9:16", "short_edge": int(body.get("short_edge") or 720), "seed": body.get("seed"), "duration_tier": duration_tier, "candidates": candidates},
        }
        media["generate"] = response
        if key:
            idem[key] = {"response": response, "body_sha": hashlib.sha256(body_bytes).hexdigest()}
        return httpx.Response(200, json=response)

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


# ═══ INSERT-IMGTEXTPOST ═══════════════════════════════════════════════════
# 图文帖子（image-text-post）所需端点（M2-10）。素材上传复用既有
# POST /v1/media（INSERT-GROUPVID 段），这里只补 VLM 看图问答端点——按
# minimax-h3-gateway/docs/integration/api/vlm.md 建模最小问答：
#
# - ``POST /v1/vlm/chat`` → 同步返回 ``{"model", "text", "elapsed_s", "usage"}``；
#   ``text`` 来自 ``mock.vlm_responses`` 队列（test-facing knob，逐次出队，
#   队列空时返回相符结论 ``{"match": true, "reason": ""}`` 的 JSON 文本）。
#   认证与权限口径与其它业务端点一致（所有已认证用户可调）；``prompt`` 为空
#   或 ``images`` 超过 8 张 → 400 ``invalid_request``（vlm.md 请求体约束）。
#
# 通过在类外包装 :meth:`MockMediaGateway.handle` 追加路由，不改既有任何行；
# 包装器把未命中的请求原样委托给原 handler（与并发追加的其它分区兼容）。

_VLM_DEFAULT_TEXT = '{"match": true, "reason": ""}'


def _handle_vlm_chat(mg: MockMediaGateway, request: httpx.Request, user_id: str) -> httpx.Response:
    body = mg._json(request)
    prompt = str(body.get("prompt") or "")
    images = body.get("images")
    if not prompt.strip() or not isinstance(images, list) or len(images) > 8:
        return _error(400, "invalid_request", "prompt 必填（1–8000 字符）；images ≤8 张")
    queue = getattr(mg, "_vlm_responses", None)
    if queue:
        text = str(queue.pop(0))
    else:
        text = _VLM_DEFAULT_TEXT
    return httpx.Response(
        200,
        json={"model": "mock-vlm", "text": text, "elapsed_s": 0.1, "usage": {"prompt_tokens": 64, "output_tokens": 16}},
    )


_original_imgtextpost_handle = MockMediaGateway.handle


def _handle_with_vlm(self: MockMediaGateway, request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/vlm/chat" and request.method == "POST":
        self.requests.append((request.method, request.url.path))
        user_id = self._identify(request)
        if user_id is None:
            return _error(401, "unauthorized", "missing or invalid bearer token")
        return _handle_vlm_chat(self, request, user_id)
    return _original_imgtextpost_handle(self, request)


MockMediaGateway.handle = _handle_with_vlm  # type: ignore[method-assign]


def script_vlm(self: MockMediaGateway, responses: list[str]) -> None:
    """Queue ``/v1/vlm/chat`` answer texts: the n-th call returns the n-th entry;
    when the queue is empty a pass-through ``{"match": true}`` verdict is served."""
    self._vlm_responses = list(responses)


MockMediaGateway.script_vlm = script_vlm  # type: ignore[attr-defined]
# ═══ END INSERT-IMGTEXTPOST ═══════════════════════════════════════════════


# ═══ INSERT-T2ISERIES ═══════════════════════════════════════════════════
# 文生组图（t2i-series / M2-9，images.md 语义最小建模）。组图 = 多次单图提交
# （POST /v1/images/generations，每张独立 Idempotency-Key，复用上面的 jobs
# 状态机）+ 一本"组图账本"（series registry）让批状态在 MGW 可查：
#
# - ``POST /v1/images/series``        → 登记组图（theme/style/items[index,job_id]）
#   → 201 ``{"id": "ser_…", "count": n}``；items 为空或 job_id 未知 → 400
# - ``GET  /v1/images/series``        → 本用户组图列表
# - ``GET  /v1/images/series/{sid}``  → 组图详情（逐张回填作业 status/url）
#
# 归属与可见性沿用 jobs 模型：外来用户的组图读作 404。实现以类外包装
# :meth:`MockMediaGateway._handle_images` 追加路由，不改上方任何已有行；
# 包装器把未命中的请求原样委托给原处理器（与并发追加的其它分区兼容）。


def _t2i_series_routes(mg: MockMediaGateway, request: httpx.Request, user_id: str) -> httpx.Response | None:
    path = request.url.path
    method = request.method

    if path != "/v1/images/series" and not path.startswith("/v1/images/series/"):
        return None

    registry: dict[str, dict[str, Any]] = getattr(mg, "_t2i_series_registry", None) or {}
    mg._t2i_series_registry = registry  # lazy store（不改 __init__）

    if method == "POST" and path == "/v1/images/series":
        body = mg._json(request)
        items = [i for i in body.get("items") or [] if isinstance(i, dict) and i.get("job_id")]
        if not items:
            return _error(400, "invalid_request", "items 不能为空（每张图须带 job_id）")
        unknown = [i["job_id"] for i in items if i["job_id"] not in mg.jobs]
        if unknown:
            return _error(400, "invalid_request", f"作业不存在：{unknown}")
        sid = "ser_" + secrets.token_hex(4)
        registry[sid] = {
            "id": sid,
            "owner": user_id,
            "created": int(time.time()),
            "theme": str(body.get("theme") or ""),
            "style": dict(body.get("style") or {}),
            "items": [{"index": int(i.get("index") or 0), "job_id": i["job_id"], "idempotency_key": i.get("idempotency_key")} for i in items],
        }
        return httpx.Response(201, json={"id": sid, "theme": registry[sid]["theme"], "count": len(items)})

    if method == "GET" and path == "/v1/images/series":
        items = [s for s in registry.values() if mg._can_access({"owner": s["owner"]}, user_id)]
        return httpx.Response(200, json={"items": items, "total": len(items)})

    if method == "GET" and path.startswith("/v1/images/series/"):
        sid = path.rstrip("/").rsplit("/", 1)[-1]
        series = registry.get(sid)
        if series is None or not mg._can_access({"owner": series["owner"]}, user_id):
            return _error(404, "not_found", "series 不存在")
        return httpx.Response(200, json=_t2i_series_body(mg, series))

    return _error(404, "not_found", f"mock has no route for {method} {path}")


def _t2i_series_body(mg: MockMediaGateway, series: dict[str, Any]) -> dict[str, Any]:
    """组图详情：逐张回填作业状态与产物 URL（复用 jobs 状态机，不复制状态）。"""
    items = []
    for item in series["items"]:
        job = mg.jobs.get(item["job_id"]) or {}
        entry: dict[str, Any] = {"index": item["index"], "job_id": item["job_id"], "status": job.get("status")}
        if job.get("status") == "completed":
            entry["url"] = f"/v1/images/jobs/{item['job_id']}/file?kind=image"
        if job.get("error"):
            entry["error"] = job["error"]
        items.append(entry)
    return {"id": series["id"], "theme": series["theme"], "style": series["style"], "created": series["created"], "items": sorted(items, key=lambda i: i["index"])}


_handle_images_before_t2i_series = MockMediaGateway._handle_images


def _handle_images_with_t2i_series(self: MockMediaGateway, request: httpx.Request, user_id: str) -> httpx.Response:
    routed = _t2i_series_routes(self, request, user_id)
    if routed is not None:
        return routed
    return _handle_images_before_t2i_series(self, request, user_id)


MockMediaGateway._handle_images = _handle_images_with_t2i_series  # type: ignore[method-assign]
# ═══ END INSERT-T2ISERIES ═══════════════════════════════════════════════


# ═══ INSERT-SERIES ═══════════════════════════════════════════════════════
# 内容创作矩阵（content-series / M2-11，design.md §8 最小建模）。矩阵（垂类
# 定位 + 风格指南 + 选题清单 + 矩阵项状态）落 MGW 可查，deer-flow 不建内容
# 领域表（§8.3）。生成侧全部复用既有端点（images / media / drama），本分区
# 只补"矩阵账本"：
#
# - ``POST  /v1/series/plans``        → 201 ``{"id": "csplan_…", …}``
#   （positioning/style_guide/topics 必填；items 带初始状态）
# - ``GET   /v1/series/plans``        → 本用户矩阵列表
# - ``GET   /v1/series/plans/{sid}``  → 矩阵详情
# - ``PATCH /v1/series/plans/{sid}``  → 按 item id 合并更新状态（完成度回写）
#
# 归属与可见性沿用 jobs/series 模型：外来用户的矩阵读作 404。文本类输入
# （现有文章）**不入库**——POST /v1/media 契约只收 images/video（media.md），
# 文本随矩阵 source 字段携带，无需新增端点。实现以类外包装
# :meth:`MockMediaGateway.handle` 追加路由，不改上方任何已有行；包装器把
# 未命中的请求原样委托给原处理器（与并发追加的其它分区兼容）。


def _series_plan_routes(mg: MockMediaGateway, request: httpx.Request, user_id: str) -> httpx.Response | None:
    path = request.url.path
    method = request.method

    if path != "/v1/series/plans" and not path.startswith("/v1/series/plans/"):
        return None

    registry: dict[str, dict[str, Any]] = getattr(mg, "_series_plan_registry", None) or {}
    mg._series_plan_registry = registry  # lazy store（不改 __init__）

    if method == "POST" and path == "/v1/series/plans":
        body = mg._json(request)
        topics = [t for t in body.get("topics") or [] if isinstance(t, dict) and t.get("id")]
        if not str(body.get("positioning") or "").strip():
            return _error(400, "invalid_request", "positioning 不能为空")
        if not topics:
            return _error(400, "invalid_request", "topics 不能为空（每选题须带 id）")
        sid = "csplan_" + secrets.token_hex(4)
        registry[sid] = {
            "id": sid,
            "owner": user_id,
            "created": int(time.time()),
            "positioning": str(body.get("positioning")),
            "style_guide": dict(body.get("style_guide") or {}),
            "topics": topics,
            "source": dict(body.get("source") or {}),
            "items": [{"id": str(i.get("id")), "topic_id": i.get("topic_id"), "format": i.get("format"), "status": str(i.get("status") or "pending")} for i in body.get("items") or []],
        }
        return httpx.Response(201, json={k: registry[sid][k] for k in ("id", "positioning", "style_guide", "topics", "items")})

    if method == "GET" and path == "/v1/series/plans":
        items = [{k: s[k] for k in ("id", "positioning", "created")} for s in registry.values() if mg._can_access({"owner": s["owner"]}, user_id)]
        return httpx.Response(200, json={"items": items, "total": len(items)})

    if path.startswith("/v1/series/plans/"):
        sid = path.rstrip("/").rsplit("/", 1)[-1]
        plan = registry.get(sid)
        if plan is None or not mg._can_access({"owner": plan["owner"]}, user_id):
            return _error(404, "not_found", "series plan 不存在")
        if method == "GET":
            return httpx.Response(200, json={k: plan[k] for k in ("id", "positioning", "style_guide", "topics", "source", "items", "created")})
        if method == "PATCH":
            body = mg._json(request)
            statuses = {str(i.get("id")): str(i.get("status") or "") for i in body.get("items") or [] if i.get("id")}
            for item in plan["items"]:
                new_status = statuses.get(item["id"])
                if new_status:
                    item["status"] = new_status
            if isinstance(body.get("style_guide"), dict):
                plan["style_guide"] = body["style_guide"]
            return httpx.Response(200, json={k: plan[k] for k in ("id", "positioning", "style_guide", "items")})

    return _error(404, "not_found", f"mock has no route for {method} {path}")


_handle_before_series = MockMediaGateway.handle


def _handle_with_series(self: MockMediaGateway, request: httpx.Request) -> httpx.Response:
    user_id = self._identify(request)
    routed = _series_plan_routes(self, request, user_id) if user_id is not None else None
    if routed is not None:
        return routed
    return _handle_before_series(self, request)


MockMediaGateway.handle = _handle_with_series  # type: ignore[method-assign]
# ═══ END INSERT-SERIES ═══════════════════════════════════════════════════
