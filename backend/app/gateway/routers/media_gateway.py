"""Media Gateway (MGW) proxy routes for deer-flow users.

Two endpoints, both scoped to the *current session user* — neither accepts
any user/token parameter (acceptance A5: identity always comes from the
session, never from the request):

- ``GET /api/media-gateway/file/{path}`` — streams an MGW media file with the
  user's own MGW token attached server-side. Exists because ``<img src>``
  requests carry no ``Authorization`` header, so the browser cannot call MGW
  directly (design.md §4 "产物代理路由"). MGW error statuses are mapped
  semantically (404 → 404, 401 → 401, transport failure → 503).

- ``POST /api/media-gateway/console-link`` — returns a console URL, optionally
  carrying a one-time ticket minted from the user's MGW token
  (design.md §5.3). Degrades to a plain console URL (manual login) when the
  ticket call fails, so the link always works.

The MGW runtime (JSON client + streaming HTTP client) is built lazily from
:func:`deerflow.media_gateway.settings.get_media_gateway_settings` and cached
for the process lifetime; tests swap it via :func:`set_runtime`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response

from app.gateway.deps import get_current_user_from_request
from deerflow.media_gateway.client import MediaGatewayClient, MediaGatewayError
from deerflow.media_gateway.provisioner import MediaGatewayProvisioner
from deerflow.media_gateway.settings import get_media_gateway_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/media-gateway", tags=["media-gateway"])

_FILE_CHUNK_SIZE = 64 * 1024
_STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0)


@dataclass
class MediaGatewayRuntime:
    """Process-wide MGW access: JSON client + streaming file client."""

    client: MediaGatewayClient
    file_client: httpx.AsyncClient
    provisioner: MediaGatewayProvisioner

    async def aclose(self) -> None:
        await self.client.aclose()
        await self.file_client.aclose()


_runtime: MediaGatewayRuntime | None = None
_runtime_resolved = False


def _build_runtime() -> MediaGatewayRuntime:
    """Build the runtime from settings; raises when MGW is not configured."""
    from deerflow.media_gateway.crypto import MediaGatewayCipher
    from deerflow.persistence.engine import get_session_factory

    settings = get_media_gateway_settings()
    if not settings.base_url:
        raise RuntimeError("MEDIA_GATEWAY_BASE_URL is not configured")
    encryption_key = settings.encryption_key or "deerflow-media-gateway"
    session_factory = get_session_factory()
    client = MediaGatewayClient(settings.base_url, settings.admin_token)
    provisioner = MediaGatewayProvisioner(client, session_factory, MediaGatewayCipher.from_key(encryption_key))
    file_client = httpx.AsyncClient(base_url=settings.base_url, timeout=_STREAM_TIMEOUT)
    return MediaGatewayRuntime(client=client, file_client=file_client, provisioner=provisioner)


def _get_runtime() -> MediaGatewayRuntime | None:
    global _runtime, _runtime_resolved
    if _runtime_resolved:
        return _runtime
    _runtime_resolved = True
    try:
        _runtime = _build_runtime()
    except Exception as exc:  # noqa: BLE001 - unconfigured MGW must not 500 the gateway
        logger.info("media gateway routes: runtime unavailable (%s)", exc)
        _runtime = None
    return _runtime


def set_runtime(runtime: MediaGatewayRuntime | None) -> None:
    """Replace the cached runtime (testing). Pass None to force a lazy rebuild."""
    global _runtime, _runtime_resolved
    _runtime = runtime
    _runtime_resolved = runtime is not None


def reset_runtime() -> None:
    """Drop the cached runtime so the next request rebuilds it (testing)."""
    set_runtime(None)


async def _require_mgw_user(request: Request) -> tuple[object, MediaGatewayRuntime, str]:
    """Common auth + provisioning preamble shared by both routes."""
    user = await get_current_user_from_request(request)
    runtime = _get_runtime()
    if runtime is None:
        raise HTTPException(status_code=404, detail="Media Gateway 未配置，媒体功能不可用")
    try:
        token = await runtime.provisioner.get_token(str(user.id))
    except MediaGatewayError as exc:
        if exc.status == 404:
            raise HTTPException(
                status_code=404,
                detail="当前用户尚未开通媒体网关账号（未置备）；请先使用一次生图/生视频功能完成开通",
            ) from exc
        raise HTTPException(status_code=502, detail=f"媒体网关令牌获取失败: {exc.message}") from exc
    return user, runtime, token


@router.get("/file/{file_path:path}")
async def proxy_media_file(file_path: str, request: Request) -> Response:
    """Stream ``GET MGW /file/{path}`` on behalf of the current user.

    The caller's identity is *always* the session user — any attempt to pass a
    user/token parameter is ignored by design; the route signature accepts no
    such parameter (acceptance A5).
    """
    _, runtime, token = await _require_mgw_user(request)

    upstream_path = f"/file/{file_path}"
    try:
        upstream = await runtime.file_client.get(
            upstream_path,
            headers={"Authorization": f"Bearer {token}"},
        )
    except httpx.HTTPError as exc:
        logger.warning("media gateway file proxy: transport error for %s: %s", upstream_path, exc)
        raise HTTPException(status_code=503, detail="媒体网关不可达，请稍后重试") from exc

    if upstream.status_code == 404:
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    if upstream.status_code == 401:
        raise HTTPException(status_code=401, detail="媒体网关令牌无效，请重新触发一次生成以刷新")
    if upstream.status_code >= 400:
        raise HTTPException(status_code=upstream.status_code, detail=f"媒体网关返回 {upstream.status_code}")

    headers: dict[str, str] = {}
    if upstream.headers.get("content-disposition"):
        headers["Content-Disposition"] = upstream.headers["content-disposition"]
    if upstream.headers.get("content-length"):
        headers["Content-Length"] = upstream.headers["content-length"]

    return Response(
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
        content=upstream.content,
    )


@router.post("/console-link")
async def console_link(request: Request) -> dict[str, str]:
    """Return the MGW console URL for the current user, with a one-time ticket when possible.

    Always returns a usable URL: if MGW is configured but the ticket mint
    fails, the bare console URL is returned (the user falls back to manual
    login; design.md §5.3 "ticket 过期/已用 → 控制台回落到手动登录页").
    """
    _, runtime, token = await _require_mgw_user(request)

    settings = get_media_gateway_settings()
    console_url = settings.console_url
    if not console_url:
        raise HTTPException(status_code=400, detail="媒体控制台未配置（缺少 MEDIA_GATEWAY_CONSOLE_URL），无法生成跳转链接")

    url = console_url
    try:
        body = await runtime.client.post("/v1/auth/ticket", token=token)
        ticket = str(body.get("ticket") or "")
        if ticket:
            separator = "&" if "?" in console_url else "?"
            url = f"{console_url}{separator}ticket={ticket}"
    except MediaGatewayError as exc:
        logger.warning("media gateway console-link: ticket mint failed (%s); degrading to plain console URL", exc)
        url = console_url

    return {"url": url}
