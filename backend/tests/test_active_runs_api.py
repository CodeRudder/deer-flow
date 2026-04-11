"""Tests for the active runs API endpoints.

Covers:
- GET /api/runs/active — list active runs
- POST /api/runs/cancel-all — cancel all active runs
- RunManager.list_active() method
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus


# ── RunManager.list_active Tests ────────────────────────────────────────


class TestListActive:
    """Test RunManager.list_active method."""

    @pytest.mark.anyio
    async def test_returns_empty_when_no_runs(self):
        mgr = RunManager()
        active = await mgr.list_active()
        assert active == []

    @pytest.mark.anyio
    async def test_returns_only_active_runs(self):
        mgr = RunManager()
        r1 = await mgr.create("thread-1")
        await mgr.set_status(r1.run_id, RunStatus.running)

        r2 = await mgr.create("thread-2")
        # r2 stays pending

        r3 = await mgr.create("thread-3")
        await mgr.set_status(r3.run_id, RunStatus.success)

        active = await mgr.list_active()
        ids = {r.run_id for r in active}
        assert r1.run_id in ids
        assert r2.run_id in ids
        assert r3.run_id not in ids

    @pytest.mark.anyio
    async def test_returns_newest_first(self):
        mgr = RunManager()
        r1 = await mgr.create("thread-1")
        await mgr.set_status(r1.run_id, RunStatus.running)
        r2 = await mgr.create("thread-2")
        await mgr.set_status(r2.run_id, RunStatus.running)

        active = await mgr.list_active()
        assert active[0].run_id == r2.run_id
        assert active[1].run_id == r1.run_id

    @pytest.mark.anyio
    async def test_excludes_interrupted_and_cancelled(self):
        mgr = RunManager()
        r1 = await mgr.create("thread-1")
        await mgr.set_status(r1.run_id, RunStatus.running)
        await mgr.cancel(r1.run_id)

        r2 = await mgr.create("thread-2")
        await mgr.set_status(r2.run_id, RunStatus.error)

        r3 = await mgr.create("thread-3")
        await mgr.set_status(r3.run_id, RunStatus.running)

        active = await mgr.list_active()
        assert len(active) == 1
        assert active[0].run_id == r3.run_id


# ── Cancel All Integration Tests ────────────────────────────────────────


class TestCancelAll:
    """Test cancel-all logic via RunManager."""

    @pytest.mark.anyio
    async def test_cancel_all_active_runs(self):
        mgr = RunManager()
        r1 = await mgr.create("thread-1")
        await mgr.set_status(r1.run_id, RunStatus.running)
        r2 = await mgr.create("thread-2")
        await mgr.set_status(r2.run_id, RunStatus.running)

        active = await mgr.list_active()
        assert len(active) == 2

        cancelled = []
        failed = []
        for record in active:
            ok = await mgr.cancel(record.run_id)
            if ok:
                cancelled.append(record.run_id)
            else:
                failed.append(record.run_id)

        assert len(cancelled) == 2
        assert len(failed) == 0

        # Verify all are now interrupted
        active_after = await mgr.list_active()
        assert len(active_after) == 0

    @pytest.mark.anyio
    async def test_cancel_all_with_already_stopped(self):
        mgr = RunManager()
        r1 = await mgr.create("thread-1")
        await mgr.set_status(r1.run_id, RunStatus.running)
        # Already cancel one
        await mgr.cancel(r1.run_id)

        active = await mgr.list_active()
        assert len(active) == 0
