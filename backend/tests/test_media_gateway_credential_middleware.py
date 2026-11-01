"""Unit tests for MediaGatewayCredentialMiddleware and the runtime-env helper.

Covers:
- injection success (env written to sandbox session + runtime context);
- unconfigured MGW → zero-overhead passthrough (provisioner never touched);
- get_token failure → degraded to no injection, session continues;
- subagent runtime base registers the middleware after SandboxMiddleware;
- LocalSandbox session env precedence over inherited environment.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from deerflow.agents.middlewares import media_gateway_credential_middleware as mw_module
from deerflow.agents.middlewares.media_gateway_credential_middleware import (
    MEDIA_GATEWAY_ENV_CONTEXT_KEY,
    PENDING_MGW_ENV_CONTEXT_KEY,
    MediaGatewayCredentialMiddleware,
    apply_pending_mgw_env,
)
from deerflow.media_gateway.client import MediaGatewayError
from deerflow.media_gateway.settings import MediaGatewaySettings, set_media_gateway_settings
from deerflow.sandbox.local.local_sandbox import LocalSandbox
from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.search import GrepMatch


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------
class _FakeSandbox(Sandbox):
    def execute_command(self, command: str) -> str:
        return "OK"

    def read_file(self, path: str) -> str:
        return ""

    def download_file(self, path: str) -> bytes:
        return b""

    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        return []

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        return None

    def glob(self, path: str, pattern: str, *, include_dirs: bool = False, max_results: int = 200) -> tuple[list[str], bool]:
        return [], False

    def grep(self, path: str, pattern: str, *, glob: str | None = None, literal: bool = False, case_sensitive: bool = False, max_results: int = 100) -> tuple[list[GrepMatch], bool]:
        return [], False

    def update_file(self, path: str, content: bytes) -> None:
        return None


class _FakeProvider:
    """Duck-typed sandbox provider returning one pre-built sandbox."""

    def __init__(self, sandbox: Sandbox | None):
        self.sandbox = sandbox

    def get(self, sandbox_id: str) -> Sandbox | None:
        return self.sandbox

    def acquire(self, thread_id=None, *, user_id=None) -> str:  # pragma: no cover
        return "sbx"

    def release(self, sandbox_id: str) -> None:  # pragma: no cover
        return None


def _runtime(user_id: str | None = "user-1", sandbox: Sandbox | None = None, *, with_sandbox_state: bool = True) -> SimpleNamespace:
    state: dict = {}
    if with_sandbox_state and sandbox is not None:
        state["sandbox"] = {"sandbox_id": sandbox.id}
    return SimpleNamespace(state=state, context={"user_id": user_id, "thread_id": "thread-1"})


def _request(runtime) -> SimpleNamespace:
    return SimpleNamespace(runtime=runtime, tool_call={"name": "bash", "id": "tc-1", "args": {}, "type": "tool_call"})


def _install_provider(monkeypatch, sandbox: Sandbox | None) -> None:
    """Point the middleware's sandbox lookup at a duck-typed fake provider."""
    monkeypatch.setattr(mw_module, "get_sandbox_provider", lambda: _FakeProvider(sandbox))


@pytest.fixture
def configured_settings(monkeypatch):
    """MGW configured via the settings singleton (as .env loading would)."""
    set_media_gateway_settings(
        MediaGatewaySettings(
            base_url="http://mgw.internal",
            image_model="flux-schnell",
            video_model="wan2.2",
            console_url="http://mgw.console",
        )
    )
    yield
    set_media_gateway_settings(None)


@pytest.fixture
def unconfigured_settings():
    set_media_gateway_settings(MediaGatewaySettings())
    yield
    set_media_gateway_settings(None)


@pytest.fixture
def fake_env(monkeypatch):
    """Replace the async runtime-env resolution with a scripted fake."""
    calls: list[str | None] = []

    async def _resolve(user_id):
        calls.append(user_id)
        return fake_env.result

    fake_env.result: dict[str, str] = {}
    fake_env.calls = calls
    monkeypatch.setattr(mw_module, "get_runtime_mgw_env", _resolve)
    return fake_env


# ---------------------------------------------------------------------------
# middleware behaviour
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_injection_success_writes_sandbox_and_context(configured_settings, fake_env, monkeypatch):
    sandbox = _FakeSandbox("sbx-1")
    runtime = _runtime(sandbox=sandbox)
    fake_env.result = {
        "MEDIA_GATEWAY_BASE_URL": "http://mgw.internal",
        "MEDIA_GATEWAY_TOKEN": "h3t_secret",
    }

    middleware = MediaGatewayCredentialMiddleware()
    _install_provider(monkeypatch, sandbox)

    async def handler(request):
        return "tool-ok"

    result = await middleware.awrap_tool_call(_request(runtime), handler)
    assert result == "tool-ok"
    assert sandbox.session_env["MEDIA_GATEWAY_TOKEN"] == "h3t_secret"
    assert sandbox.session_env["MEDIA_GATEWAY_BASE_URL"] == "http://mgw.internal"
    # Admin token explicitly blanked in the sandbox session (per-user isolation).
    assert sandbox.session_env["MEDIA_GATEWAY_ADMIN_TOKEN"] == ""
    assert runtime.context[MEDIA_GATEWAY_ENV_CONTEXT_KEY]["MEDIA_GATEWAY_ADMIN_TOKEN"] == ""
    # Applied to an existing sandbox → pending marker cleared.
    assert PENDING_MGW_ENV_CONTEXT_KEY not in runtime.context
    assert fake_env.calls == ["user-1"]


@pytest.mark.asyncio
async def test_lazy_sandbox_gets_env_via_pending_marker(configured_settings, fake_env):
    """Env resolved before the sandbox exists stays pending; apply_pending applies it."""
    runtime = _runtime(sandbox=None, with_sandbox_state=False)
    fake_env.result = {"MEDIA_GATEWAY_TOKEN": "h3t_secret"}

    middleware = MediaGatewayCredentialMiddleware()

    async def handler(request):
        # Sandbox lazily acquired inside the tool call — apply the pending env
        # exactly like ensure_sandbox_initialized does.
        apply_pending_mgw_env(runtime, sandbox)
        return "tool-ok"

    sandbox = _FakeSandbox("sbx-lazy")
    runtime.state["sandbox"] = {"sandbox_id": "sbx-lazy"}
    runtime2 = runtime  # context shared
    await middleware.awrap_tool_call(_request(runtime2), handler)
    assert sandbox.session_env["MEDIA_GATEWAY_TOKEN"] == "h3t_secret"
    assert runtime2.context[MEDIA_GATEWAY_ENV_CONTEXT_KEY]["MEDIA_GATEWAY_TOKEN"] == "h3t_secret"


@pytest.mark.asyncio
async def test_unconfigured_passthrough_never_touches_provisioner(unconfigured_settings, monkeypatch):
    async def _explode(user_id):  # pragma: no cover - must not be called
        raise AssertionError("provisioner must not be consulted when MGW is unconfigured")

    monkeypatch.setattr(mw_module, "get_runtime_mgw_env", _explode)
    sandbox = _FakeSandbox("sbx-2")
    runtime = _runtime(sandbox=sandbox)
    middleware = MediaGatewayCredentialMiddleware()

    async def handler(request):
        return "tool-ok"

    assert await middleware.awrap_tool_call(_request(runtime), handler) == "tool-ok"
    assert sandbox.session_env == {}
    assert MEDIA_GATEWAY_ENV_CONTEXT_KEY not in runtime.context


@pytest.mark.asyncio
async def test_no_user_id_skips_injection(configured_settings, fake_env):
    sandbox = _FakeSandbox("sbx-3")
    runtime = _runtime(user_id=None, sandbox=sandbox)
    fake_env.result = {}  # helper returns {} for missing user
    middleware = MediaGatewayCredentialMiddleware()

    async def handler(request):
        return "tool-ok"

    await middleware.awrap_tool_call(_request(runtime), handler)
    assert sandbox.session_env == {}


@pytest.mark.asyncio
async def test_get_token_failure_degrades_to_no_injection(configured_settings, fake_env, caplog):
    """Helper-level degradation: get_token raises → env is {} → nothing injected."""
    from deerflow.media_gateway.runtime_env import get_runtime_mgw_env as real_helper

    class _FailingProvisioner:
        async def get_token(self, user_id):
            raise MediaGatewayError(status=500, code="boom", message="boom")

    monkey = pytest.MonkeyPatch()
    monkey.setattr("deerflow.media_gateway.runtime_env._get_provisioner", lambda: _FailingProvisioner())
    try:
        env = await real_helper("user-9")
    finally:
        monkey.undo()
    assert env == {}

    # And the middleware treats an empty env as "do not inject".
    sandbox = _FakeSandbox("sbx-4")
    runtime = _runtime(user_id="user-9", sandbox=sandbox)
    fake_env.result = {}
    middleware = MediaGatewayCredentialMiddleware()

    async def handler(request):
        return "tool-ok"

    await middleware.awrap_tool_call(_request(runtime), handler)
    assert sandbox.session_env == {}


def test_sync_wrap_resolves_when_no_loop(configured_settings, fake_env, monkeypatch):
    sandbox = _FakeSandbox("sbx-5")
    runtime = _runtime(sandbox=sandbox)
    fake_env.result = {"MEDIA_GATEWAY_TOKEN": "h3t_sync"}
    middleware = MediaGatewayCredentialMiddleware()
    _install_provider(monkeypatch, sandbox)
    middleware.wrap_tool_call(_request(runtime), lambda request: "tool-ok")
    assert sandbox.session_env["MEDIA_GATEWAY_TOKEN"] == "h3t_sync"


@pytest.mark.asyncio
async def test_subagent_and_lead_bases_register_middleware_after_sandbox(monkeypatch):
    """Shared runtime base includes the middleware; SandboxMiddleware stays outermost."""
    from deerflow.agents.middlewares.tool_error_handling_middleware import (
        build_lead_runtime_middlewares,
        build_subagent_runtime_middlewares,
    )
    from deerflow.config.app_config import AppConfig
    from deerflow.config.sandbox_config import SandboxConfig

    def _check(middlewares):
        names = [type(m).__name__ for m in middlewares]
        assert "MediaGatewayCredentialMiddleware" in names
        assert names.index("SandboxMiddleware") < names.index("MediaGatewayCredentialMiddleware")

    config = AppConfig(sandbox=SandboxConfig(use="deerflow.sandbox.local.local_sandbox_provider.LocalSandboxProvider"), log_level="warning")
    _check(build_lead_runtime_middlewares(app_config=config, lazy_init=True))
    _check(build_subagent_runtime_middlewares(app_config=config))


# ---------------------------------------------------------------------------
# runtime-env helper
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_runtime_env_unconfigured_returns_empty(unconfigured_settings):
    from deerflow.media_gateway.runtime_env import get_runtime_mgw_env

    assert await get_runtime_mgw_env("user-1") == {}


@pytest.mark.asyncio
async def test_runtime_env_builds_expected_keys(configured_settings):
    from deerflow.media_gateway.runtime_env import reset_runtime_mgw_cache

    class _Provisioner:
        async def get_token(self, user_id):
            return f"h3t_{user_id}"

    reset_runtime_mgw_cache()
    monkey = pytest.MonkeyPatch()
    monkey.setattr("deerflow.media_gateway.runtime_env._get_provisioner", lambda: _Provisioner())
    try:
        from deerflow.media_gateway.runtime_env import get_runtime_mgw_env

        env = await get_runtime_mgw_env("user-1")
    finally:
        monkey.undo()
        reset_runtime_mgw_cache()
    assert env["MEDIA_GATEWAY_BASE_URL"] == "http://mgw.internal"
    assert env["MEDIA_GATEWAY_TOKEN"] == "h3t_user-1"
    assert env["MEDIA_GATEWAY_IMAGE_MODEL"] == "flux-schnell"
    assert env["MEDIA_GATEWAY_VIDEO_MODEL"] == "wan2.2"
    assert env["MEDIA_GATEWAY_CONSOLE_URL"] == "http://mgw.console"


@pytest.mark.asyncio
async def test_runtime_env_not_provisioned_returns_empty_debug(configured_settings, caplog):
    from deerflow.media_gateway.runtime_env import get_runtime_mgw_env, reset_runtime_mgw_cache

    class _Unmapped:
        async def get_token(self, user_id):
            raise MediaGatewayError(status=404, code="not_provisioned", message=f"no mapping for {user_id}")

    reset_runtime_mgw_cache()
    monkey = pytest.MonkeyPatch()
    monkey.setattr("deerflow.media_gateway.runtime_env._get_provisioner", lambda: _Unmapped())
    try:
        with caplog.at_level("DEBUG", logger="deerflow.media_gateway.runtime_env"):
            env = await get_runtime_mgw_env("user-404")
    finally:
        monkey.undo()
        reset_runtime_mgw_cache()
    assert env == {}
    assert any("no MGW mapping" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# LocalSandbox session env
# ---------------------------------------------------------------------------
def test_local_sandbox_empty_session_value_overrides_inherited(tmp_path):
    """An empty-string session env value must override a non-empty inherited value.

    Regression: MEDIA_GATEWAY_ADMIN_TOKEN="" injected by the middleware must
    blank an admin token leaked into the backend process environment.
    """
    sandbox = LocalSandbox("local:test:mgw3")
    sentinel = "MGW_LOCAL_SENTINEL_admin"
    os.environ[sentinel] = "leaked-admin-token"
    try:
        assert sandbox.execute_command(f"echo ${sentinel}").strip() == "leaked-admin-token"
        sandbox.set_session_env({sentinel: ""})
        output = sandbox.execute_command('echo "[$' + sentinel + ']"').strip()
        assert output == "[]"
    finally:
        os.environ.pop(sentinel, None)


def test_local_sandbox_session_env_overrides_inherited(tmp_path):
    sandbox = LocalSandbox("local:test:mgw")
    sentinel = "MGW_LOCAL_SENTINEL_9f2"
    os.environ[sentinel] = "inherited"
    try:
        assert sandbox.execute_command(f"echo ${sentinel}").strip() == "inherited"
        sandbox.set_session_env({sentinel: "session", "MEDIA_GATEWAY_TOKEN": "h3t_xyz"})
        output = sandbox.execute_command(f"echo ${sentinel},$MEDIA_GATEWAY_TOKEN")
        assert "session" in output
        assert "h3t_xyz" in output
    finally:
        os.environ.pop(sentinel, None)


def test_local_sandbox_without_session_env_inherits(tmp_path):
    sandbox = LocalSandbox("local:test:mgw2")
    sentinel = "MGW_LOCAL_SENTINEL_ab3"
    os.environ[sentinel] = "plain"
    try:
        assert sandbox.execute_command(f"echo ${sentinel}").strip() == "plain"
    finally:
        os.environ.pop(sentinel, None)


def test_apply_pending_mgw_env_is_idempotent():
    sandbox = _FakeSandbox("sbx-pending")
    runtime = _runtime(user_id="u", with_sandbox_state=False)
    runtime.context[PENDING_MGW_ENV_CONTEXT_KEY] = {"MEDIA_GATEWAY_TOKEN": "h3t_p"}
    apply_pending_mgw_env(runtime, sandbox)
    assert sandbox.session_env["MEDIA_GATEWAY_TOKEN"] == "h3t_p"
    assert PENDING_MGW_ENV_CONTEXT_KEY not in runtime.context
    # Second application is a no-op.
    apply_pending_mgw_env(runtime, sandbox)
    assert sandbox.session_env["MEDIA_GATEWAY_TOKEN"] == "h3t_p"


def test_injection_blanks_inherited_admin_token(configured_settings, fake_env, monkeypatch):
    """End-to-end: admin token inherited from the process env is blanked in the sandbox."""
    monkeypatch.setenv("MEDIA_GATEWAY_ADMIN_TOKEN", "h3t_leaked_admin")
    sandbox = LocalSandbox("local:test:mgw-admin")
    runtime = _runtime(sandbox=sandbox)
    fake_env.result = {"MEDIA_GATEWAY_BASE_URL": "http://mgw.internal", "MEDIA_GATEWAY_TOKEN": "h3t_user"}
    middleware = MediaGatewayCredentialMiddleware()
    _install_provider(monkeypatch, sandbox)

    async def handler(request):
        return "tool-ok"

    asyncio.run(middleware.awrap_tool_call(_request(runtime), handler))
    output = sandbox.execute_command("echo \"[$MEDIA_GATEWAY_ADMIN_TOKEN][$MEDIA_GATEWAY_TOKEN]\"").strip()
    assert output == "[][h3t_user]"


def test_no_token_in_logs(configured_settings, fake_env, caplog):
    """The h3t_ token must never appear in middleware log records."""
    sandbox = _FakeSandbox("sbx-log")
    runtime = _runtime(sandbox=sandbox)
    fake_env.result = {"MEDIA_GATEWAY_TOKEN": "h3t_supersecret"}
    middleware = MediaGatewayCredentialMiddleware()

    async def handler(request):
        return "tool-ok"

    with caplog.at_level("DEBUG"):
        asyncio.run(middleware.awrap_tool_call(_request(runtime), handler))
    for record in caplog.records:
        assert "h3t_supersecret" not in record.getMessage()
