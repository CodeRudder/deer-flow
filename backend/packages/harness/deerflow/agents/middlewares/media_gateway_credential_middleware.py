"""Per-user Media Gateway credential injection into sandbox sessions.

Goal: inside an agent session, any bash/python execution (including skill
scripts) automatically sees that user's Media Gateway (MGW) credentials as
environment variables — skills never handle credentials themselves.

Injection chain (this middleware is the orchestrator only)::

    wrap/awrap_tool_call
      → get_runtime_mgw_env(user_id)             deerflow.media_gateway.runtime_env
      → runtime.context["media_gateway_env"]     (for in-process Python callers)
      → runtime.context["__mgw_pending_sandbox_env"]  (pending marker)
      → sandbox.set_session_env(env)             deerflow.sandbox (applied by
        this middleware when the sandbox already exists, or by
        ensure_sandbox_initialized* right after lazy acquisition so the very
        first bash command of a thread already carries the env)

Design notes:

- Registered in the shared runtime base (``_build_runtime_middlewares``)
  immediately after ``SandboxMiddleware``, so both the lead agent and every
  subagent get identical injection.
- Unconfigured MGW (``MEDIA_GATEWAY_BASE_URL`` unset) → one cheap settings
  read, then a permanent zero-overhead passthrough for this instance.
- ``get_token`` failure / no mapping → env not injected, warning/debug logged,
  session continues (never raises into the graph).
- The token never appears in logs; only env key names are logged, at debug.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from deerflow.agents.thread_state import SandboxStateField
from deerflow.media_gateway.runtime_env import get_runtime_mgw_env
from deerflow.media_gateway.settings import get_media_gateway_settings
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.sandbox import get_sandbox_provider
from deerflow.sandbox.sandbox import Sandbox

logger = logging.getLogger(__name__)

# Runtime-context key holding the resolved env dict for in-process Python
# callers (code that constructs a MediaGatewayClient directly).
MEDIA_GATEWAY_ENV_CONTEXT_KEY = "media_gateway_env"
# Internal marker: env resolved but not yet applied to a sandbox (the sandbox
# may still be lazily acquired inside the wrapped tool call).
PENDING_MGW_ENV_CONTEXT_KEY = "__mgw_pending_sandbox_env"


def apply_pending_mgw_env(runtime: Runtime | None, sandbox: Sandbox) -> None:
    """Apply the pending Media Gateway env to a freshly acquired ``sandbox``.

    Called from ``ensure_sandbox_initialized`` / ``ensure_sandbox_initialized_async``
    right after lazy acquisition so the very first bash command of a thread
    already runs with the injected environment. Idempotent and a no-op when no
    env is pending.
    """
    if runtime is None or runtime.context is None:
        return
    env = runtime.context.get(PENDING_MGW_ENV_CONTEXT_KEY)
    if isinstance(env, dict) and env:
        sandbox.set_session_env(env)
        runtime.context.pop(PENDING_MGW_ENV_CONTEXT_KEY, None)
        logger.debug("media gateway env: injected keys %s into sandbox %s", sorted(env), sandbox.id)


class MediaGatewayCredentialMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    sandbox: SandboxStateField
    media_gateway_env: NotRequired[dict[str, str] | None]


class MediaGatewayCredentialMiddleware(AgentMiddleware[MediaGatewayCredentialMiddlewareState]):
    """Inject per-user Media Gateway env into the thread's sandbox session.

    Registered after ``SandboxMiddleware`` in the shared runtime base so
    sandbox lifecycle bookkeeping stays outermost; this middleware only ever
    *adds* env to an already-acquired (or lazily acquired) sandbox.
    """

    state_schema = MediaGatewayCredentialMiddlewareState

    def __init__(self) -> None:
        super().__init__()
        # Tri-state config cache: None = not yet probed. Unconfigured MGW makes
        # every later tool call a single bool check (zero-overhead passthrough).
        self._configured: bool | None = None

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _is_configured(self) -> bool:
        if self._configured is None:
            try:
                self._configured = bool(get_media_gateway_settings().base_url)
            except Exception:  # noqa: BLE001 - settings failure must not break the session
                logger.warning("media gateway credential middleware: settings unavailable; disabling injection")
                self._configured = False
        return self._configured

    def _get_sandbox(self, runtime: Runtime | None) -> Sandbox | None:
        """Resolve the live sandbox object for the current thread, or None."""
        if runtime is None or runtime.state is None:
            return None
        sandbox_state = runtime.state.get("sandbox")
        if not isinstance(sandbox_state, dict):
            return None
        sandbox_id = sandbox_state.get("sandbox_id")
        if not isinstance(sandbox_id, str):
            return None
        return get_sandbox_provider().get(sandbox_id)

    def _apply_env(self, runtime: Runtime, env: dict[str, str]) -> None:
        """Record env on the runtime context and push it into the sandbox (if present)."""
        context = runtime.context
        if context is None:
            return
        context[MEDIA_GATEWAY_ENV_CONTEXT_KEY] = env
        # Mark pending so ensure_sandbox_initialized* applies the env when the
        # sandbox is lazily acquired inside the wrapped tool call.
        context[PENDING_MGW_ENV_CONTEXT_KEY] = env
        sandbox = self._get_sandbox(runtime)
        if sandbox is not None:
            sandbox.set_session_env(env)
            context.pop(PENDING_MGW_ENV_CONTEXT_KEY, None)
            logger.debug("media gateway env: injected keys %s into sandbox %s", sorted(env), sandbox.id)

    async def _resolve_env(self, runtime: Runtime) -> dict[str, str] | None:
        """Resolve the per-user env dict; None means "leave everything untouched"."""
        user_id = resolve_runtime_user_id(runtime)
        try:
            env = await get_runtime_mgw_env(user_id)
        except Exception as exc:  # noqa: BLE001 - degraded, never fatal
            logger.warning("media gateway credential middleware: env resolution failed for user %s: %s", user_id, exc)
            return None
        if not env:
            return None
        return env

    # ------------------------------------------------------------------
    # tool-call wrappers
    # ------------------------------------------------------------------
    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        if self._is_configured():
            runtime = request.runtime
            context = runtime.context if runtime is not None else None
            if context is not None:
                env = context.get(MEDIA_GATEWAY_ENV_CONTEXT_KEY)
                if isinstance(env, dict) and env:
                    # Already resolved for this run — (re)apply in case the
                    # sandbox was rebuilt between tool calls. Never blocks.
                    self._apply_env(runtime, env)
                elif not _loop_running():
                    # No async loop on this (sync) execution path: resolve
                    # inline. When a loop IS running, resolution is deferred to
                    # ``awrap_tool_call`` — never block the event loop here.
                    result = _run_coro_sync(self._resolve_env(runtime))
                    if result is not None:
                        self._apply_env(runtime, result)
                else:
                    logger.debug("media gateway credential middleware: async loop running; deferring env resolution to awrap_tool_call")
        return handler(request)  # type: ignore[return-value]

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if self._is_configured():
            runtime = request.runtime
            context = runtime.context if runtime is not None else None
            if context is not None:
                env = context.get(MEDIA_GATEWAY_ENV_CONTEXT_KEY)
                if isinstance(env, dict) and env:
                    # Re-apply (idempotent) in case the sandbox was rebuilt.
                    self._apply_env(runtime, env)
                else:
                    resolved = await self._resolve_env(runtime)
                    if resolved is not None:
                        self._apply_env(runtime, resolved)
        return await handler(request)


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _run_coro_sync(coro):
    """Run a coroutine when no event loop is running; None on failure."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        return None
