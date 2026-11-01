"""Tests for idempotent run cancellation (issue #3055).

RunManager.cancel() returns True when a run is already interrupted so that
a second cancel request from the same worker is treated as a no-op success
(202) rather than a conflict (409).  Both the POST cancel endpoint and the
POST stream endpoint share this behaviour through the same cancel() call.

Cancelling a run that already reached a non-interrupted terminal state
(success/error/timeout) is also treated as a no-op success: the SDK stop
button can race with run completion (the SSE stream may still be draining
when the user clicks stop), and a 409 there surfaces as an unhandled
promise rejection in the browser.  The rule extends to hydrated store-only
records so a stop click after a worker restart stays a no-op for terminal
runs while a still-running record keeps its worker-ownership 409.
"""

from __future__ import annotations

import asyncio

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.routers import thread_runs
from deerflow.runtime import RunManager, RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore

THREAD_ID = "thread-cancel-test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(mgr: RunManager) -> TestClient:
    app = make_authed_test_app()
    app.include_router(thread_runs.router)
    app.state.run_manager = mgr
    return TestClient(app, raise_server_exceptions=False)


def _create_interrupted_run(mgr: RunManager) -> str:
    """Create a run and cancel it, returning its run_id."""

    async def _setup():
        record = await mgr.create(THREAD_ID)
        await mgr.set_status(record.run_id, RunStatus.running)
        await mgr.cancel(record.run_id)
        return record.run_id

    return asyncio.run(_setup())


# ---------------------------------------------------------------------------
# RunManager.cancel() unit tests
# ---------------------------------------------------------------------------


class TestRunManagerCancelIdempotency:
    def test_cancel_returns_true_for_already_interrupted_run(self):
        """cancel() must return True when the run is already interrupted."""

        async def run():
            mgr = RunManager()
            record = await mgr.create(THREAD_ID)
            await mgr.set_status(record.run_id, RunStatus.running)
            first = await mgr.cancel(record.run_id)
            assert first is True
            second = await mgr.cancel(record.run_id)
            assert second is True  # idempotent

        asyncio.run(run())

    def test_cancel_returns_false_for_successful_run(self):
        """cancel() must still return False for runs that completed successfully."""

        async def run():
            mgr = RunManager()
            record = await mgr.create(THREAD_ID)
            await mgr.set_status(record.run_id, RunStatus.running)
            await mgr.set_status(record.run_id, RunStatus.success)
            result = await mgr.cancel(record.run_id)
            assert result is False

        asyncio.run(run())

    def test_cancel_returns_false_for_unknown_run(self):
        async def run():
            mgr = RunManager()
            result = await mgr.cancel("nonexistent-run-id")
            assert result is False

        asyncio.run(run())


# ---------------------------------------------------------------------------
# POST /cancel endpoint — idempotent 202
# ---------------------------------------------------------------------------


class TestCancelRunEndpointIdempotency:
    def test_double_cancel_returns_202_not_409(self):
        """Second cancel on an already-interrupted run must return 202, not 409."""
        mgr = RunManager()
        run_id = _create_interrupted_run(mgr)
        client = _make_app(mgr)

        resp = client.post(f"/api/threads/{THREAD_ID}/runs/{run_id}/cancel")
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"

    def test_cancel_unknown_run_returns_404(self):
        mgr = RunManager()
        client = _make_app(mgr)
        resp = client.post(f"/api/threads/{THREAD_ID}/runs/no-such-run/cancel")
        assert resp.status_code == 404

    def test_cancel_successful_run_returns_idempotent_202(self):
        """Cancelling a successfully-completed run is a no-op success (202).

        The SDK stop button can race with run completion; the endpoint must
        not answer 409 because the browser surfaces the rejected promise as
        a runtime error.
        """

        async def _setup():
            mgr = RunManager()
            record = await mgr.create(THREAD_ID)
            await mgr.set_status(record.run_id, RunStatus.running)
            await mgr.set_status(record.run_id, RunStatus.success)
            return mgr, record.run_id

        mgr, run_id = asyncio.run(_setup())
        client = _make_app(mgr)
        resp = client.post(f"/api/threads/{THREAD_ID}/runs/{run_id}/cancel")
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"

    def test_cancel_errored_run_returns_idempotent_202(self):
        async def _setup():
            mgr = RunManager()
            record = await mgr.create(THREAD_ID)
            await mgr.set_status(record.run_id, RunStatus.running)
            await mgr.set_status(record.run_id, RunStatus.error, error="boom")
            return mgr, record.run_id

        mgr, run_id = asyncio.run(_setup())
        client = _make_app(mgr)
        resp = client.post(f"/api/threads/{THREAD_ID}/runs/{run_id}/cancel")
        assert resp.status_code == 202

    def test_cancel_wait_on_successful_run_returns_204(self):
        async def _setup():
            mgr = RunManager()
            record = await mgr.create(THREAD_ID)
            await mgr.set_status(record.run_id, RunStatus.running)
            await mgr.set_status(record.run_id, RunStatus.success)
            return mgr, record.run_id

        mgr, run_id = asyncio.run(_setup())
        client = _make_app(mgr)
        resp = client.post(f"/api/threads/{THREAD_ID}/runs/{run_id}/cancel", params={"wait": "true"})
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# POST /{thread_id}/runs/{run_id}/join (stream_existing_run) — idempotent cancel
# ---------------------------------------------------------------------------


class TestStreamExistingRunIdempotentCancel:
    def test_stream_cancel_successful_run_returns_204(self):
        """POST stream (stop-button path) on a finished run returns 204, not 409."""
        mgr = RunManager()

        async def _setup():
            record = await mgr.create(THREAD_ID)
            await mgr.set_status(record.run_id, RunStatus.running)
            await mgr.set_status(record.run_id, RunStatus.success)
            return record.run_id

        run_id = asyncio.run(_setup())
        client = _make_app(mgr)
        resp = client.post(
            f"/api/threads/{THREAD_ID}/runs/{run_id}/stream",
            params={"action": "interrupt", "wait": "1"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"


class TestCancelStoreOnlyTerminalRun:
    def test_cancel_store_only_success_run_returns_202(self):
        """Cancelling a hydrated terminal run (worker restarted, user clicks
        stop before reload) is a no-op success, not a worker-ownership 409."""
        store = MemoryRunStore()
        asyncio.run(
            store.put(
                "store-only-success-run",
                thread_id=THREAD_ID,
                assistant_id="lead_agent",
                status="success",
                multitask_strategy="reject",
                metadata={},
                kwargs={},
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        client = _make_app(RunManager(store=store))

        resp = client.post(f"/api/threads/{THREAD_ID}/runs/store-only-success-run/cancel")
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"

    def test_cancel_store_only_running_run_still_409(self):
        """A hydrated running record has no worker state to stop — stays 409."""
        store = MemoryRunStore()
        asyncio.run(
            store.put(
                "store-only-running-run",
                thread_id=THREAD_ID,
                assistant_id="lead_agent",
                status="running",
                multitask_strategy="reject",
                metadata={},
                kwargs={},
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        client = _make_app(RunManager(store=store))

        resp = client.post(f"/api/threads/{THREAD_ID}/runs/store-only-running-run/cancel")
        assert resp.status_code == 409
        assert "not active on this worker" in resp.json()["detail"]
