"""Tests for rejecting streaming joins on unreplayable terminal runs."""

from __future__ import annotations

import asyncio

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.routers import thread_runs
from deerflow.runtime import RunManager, RunStatus
from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge

THREAD_ID = "thread-join-terminal-test"


def _make_app(mgr: RunManager, bridge: MemoryStreamBridge) -> TestClient:
    app = make_authed_test_app()
    app.include_router(thread_runs.router)
    app.state.run_manager = mgr
    app.state.stream_bridge = bridge
    return TestClient(app, raise_server_exceptions=False)


def _create_terminal_run(mgr: RunManager, bridge: MemoryStreamBridge, status: RunStatus) -> str:
    """Create a run, publish a couple of buffered events, end it, set status."""

    async def _setup():
        record = await mgr.create(THREAD_ID)
        await mgr.set_status(record.run_id, RunStatus.running)
        await bridge.publish(record.run_id, "updates", {"seq": 1})
        await bridge.publish(record.run_id, "updates", {"seq": 2})
        await bridge.publish_end(record.run_id)
        await mgr.set_status(record.run_id, status)
        return record.run_id

    return asyncio.run(_setup())


def _drop_bridge_buffer(bridge: MemoryStreamBridge, run_id: str) -> None:
    """Simulate the 60s bridge cleanup."""

    async def _drop():
        await bridge.cleanup(run_id)

    asyncio.run(_drop())


class TestJoinUnreplayableTerminalRun:
    def test_join_terminal_run_without_buffer_returns_409(self):
        mgr = RunManager()
        bridge = MemoryStreamBridge()
        run_id = _create_terminal_run(mgr, bridge, RunStatus.success)
        _drop_bridge_buffer(bridge, run_id)
        client = _make_app(mgr, bridge)

        resp = client.get(f"/api/threads/{THREAD_ID}/runs/{run_id}/join")
        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"

    def test_join_terminal_run_with_buffer_still_streams(self):
        """Within the replay window a terminal run still replays events + end."""
        mgr = RunManager()
        bridge = MemoryStreamBridge()
        run_id = _create_terminal_run(mgr, bridge, RunStatus.success)
        client = _make_app(mgr, bridge)

        with client.stream("GET", f"/api/threads/{THREAD_ID}/runs/{run_id}/join") as resp:
            assert resp.status_code == 200
            body = b"".join(resp.iter_bytes())
        assert b"event: updates" in body
        assert b"event: end" in body

    def test_join_running_run_passes_the_guard(self):
        """A running run is not rejected by the guard."""
        from app.gateway.routers.thread_runs import _raise_if_unreplayable

        async def run():
            mgr = RunManager()
            bridge = MemoryStreamBridge()
            record = await mgr.create(THREAD_ID)
            await mgr.set_status(record.run_id, RunStatus.running)
            await bridge.publish(record.run_id, "updates", {"seq": 1})
            await _raise_if_unreplayable(record, bridge)

        asyncio.run(run())

    @pytest.mark.parametrize("status", [RunStatus.error, RunStatus.timeout, RunStatus.interrupted])
    def test_join_every_terminal_status_without_buffer_returns_409(self, status):
        mgr = RunManager()
        bridge = MemoryStreamBridge()
        run_id = _create_terminal_run(mgr, bridge, status)
        _drop_bridge_buffer(bridge, run_id)
        client = _make_app(mgr, bridge)

        resp = client.get(f"/api/threads/{THREAD_ID}/runs/{run_id}/join")
        assert resp.status_code == 409


class TestStreamExistingRunUnreplayableTerminalRun:
    def test_stream_terminal_run_without_buffer_returns_409(self):
        mgr = RunManager()
        bridge = MemoryStreamBridge()
        run_id = _create_terminal_run(mgr, bridge, RunStatus.success)
        _drop_bridge_buffer(bridge, run_id)
        client = _make_app(mgr, bridge)

        resp = client.post(f"/api/threads/{THREAD_ID}/runs/{run_id}/stream")
        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
