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
        self.media: dict[str, dict[str, Any]] = {}  # INSERT-GROUPVID: 素材库 media（groupvid.md / media.md）
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
        if path.startswith("/v1/media"):
            return self._handle_media(request, user_id)  # INSERT-GROUPVID routing

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
