"""Thread interjection (plan-b) — queue, worker injection, manager lookup, API.

Design: docs/research/2026-11-01-thread-interjection/plan-b.md
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.gateway.routers.thread_runs import (
    InterjectionCreateRequest,
    RunCreateRequest,
    _try_enqueue_interjection,
    cancel_interjection,
    create_interjection,
)
from deerflow.config.app_config import AppConfig
from deerflow.runtime.runs.interjection import (
    INTERJECTION_APPLIED_EVENT,
    INTERJECTION_AS_NODE,
    InterjectionAlreadyAppliedError,
    InterjectionApplier,
    InterjectionNotFoundError,
    InterjectionQueueFullError,
)
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.worker import RunContext, run_agent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _app_config(enabled: bool = True, max_pending: int = 8) -> AppConfig:
    cfg = AppConfig(
        sandbox={"use": "deerflow.sandbox.local.local_sandbox:LocalSandboxProvider"},
        models=[],
    )
    cfg.interjection.enabled = enabled
    cfg.interjection.max_pending = max_pending
    return cfg


def _make_bridge():
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    return bridge


def _published_events(bridge) -> list[tuple[str, dict]]:
    return [(call.args[1], call.args[2]) for call in bridge.publish.await_args_list]


# ---------------------------------------------------------------------------
# InterjectionApplier unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_applier_enqueue_preserves_order_and_metadata():
    applier = InterjectionApplier("t1", "r1", max_pending=4)
    a = await applier.enqueue("first", source="web")
    b = await applier.enqueue("second", source="api")
    assert a.seq < b.seq
    assert a.applied is False
    batch = await applier.drain_batch(first_wait_timeout=0.1)
    assert [i.text for i in batch] == ["first", "second"]


@pytest.mark.asyncio
async def test_applier_rejects_empty_text():
    applier = InterjectionApplier("t1", "r1")
    with pytest.raises(ValueError):
        await applier.enqueue("   ")


@pytest.mark.asyncio
async def test_applier_queue_full():
    applier = InterjectionApplier("t1", "r1", max_pending=1)
    await applier.enqueue("one")
    with pytest.raises(InterjectionQueueFullError):
        await applier.enqueue("two")


@pytest.mark.asyncio
async def test_applier_cancel_pending_vs_applied():
    applier = InterjectionApplier("t1", "r1")
    a = await applier.enqueue("one")
    b = await applier.enqueue("two")
    cancelled = applier.cancel(a.id)
    assert cancelled.text == "one"
    # Cancelled items are skipped by drain.
    batch = await applier.drain_batch(first_wait_timeout=0.1)
    assert [i.text for i in batch] == ["two"]
    await applier.apply_batch(batch)
    with pytest.raises(InterjectionAlreadyAppliedError):
        applier.cancel(b.id)
    with pytest.raises(InterjectionNotFoundError):
        applier.cancel("no-such-id")


@pytest.mark.asyncio
async def test_applier_cancel_while_queued_skips_item():
    applier = InterjectionApplier("t1", "r1")
    a = await applier.enqueue("one")
    b = await applier.enqueue("two")
    applier.cancel(b.id)
    batch = await applier.drain_batch(first_wait_timeout=0.1)
    # b was cancelled after being queued: drain must not return it.
    assert [i.id for i in batch] == [a.id]


@pytest.mark.asyncio
async def test_applier_drain_zero_timeout_returns_nowait_batch():
    applier = InterjectionApplier("t1", "r1")
    assert await applier.drain_batch(first_wait_timeout=0) == []
    await applier.enqueue("one")
    batch = await applier.drain_batch(first_wait_timeout=0)
    assert [i.text for i in batch] == ["one"]


# ---------------------------------------------------------------------------
# RunManager.find_inflight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_inflight_returns_live_record():
    mgr = RunManager()
    record = await mgr.create("t1")
    record.task = asyncio.create_task(asyncio.sleep(0))
    found = await mgr.find_inflight("t1")
    assert found is not None and found.run_id == record.run_id
    record.task.cancel()


@pytest.mark.asyncio
async def test_find_inflight_none_without_task_or_for_terminal():
    mgr = RunManager()
    record = await mgr.create("t1")  # no task attached
    assert await mgr.find_inflight("t1") is None
    record.task = asyncio.create_task(asyncio.sleep(0))
    from deerflow.runtime.runs.schemas import RunStatus

    await mgr.set_status(record.run_id, RunStatus.success)
    assert await mgr.find_inflight("t1") is None
    record.task.cancel()


# ---------------------------------------------------------------------------
# Worker injection
# ---------------------------------------------------------------------------


class _InterjectableAgent:
    """Fake agent: yields one chunk, waits for the interjection, yields again."""

    def __init__(self, applied_event: asyncio.Event):
        self.applied_event = applied_event
        self.update_calls: list[tuple[dict, str | None]] = []
        self.stream_closed = asyncio.Event()

    async def update_state(self, config, values, as_node=None):
        self.update_calls.append((values, as_node))
        self.applied_event.set()
        return None

    async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
        yield {"messages": []}
        await self.applied_event.wait()
        yield {"messages": []}
        self.stream_closed.set()


def _factory(agent):
    def factory(*, config):
        return agent

    return factory


@pytest.mark.asyncio
async def test_worker_injects_interjection_at_step_boundary():
    applied_event = asyncio.Event()
    agent = _InterjectableAgent(applied_event)
    bridge = _make_bridge()
    mgr = RunManager()
    record = await mgr.create("t1")
    record.ensure_interjections(max_pending=4)
    task = asyncio.create_task(
        run_agent(
            bridge,
            mgr,
            record,
            ctx=RunContext(checkpointer=None, app_config=_app_config()),
            agent_factory=_factory(agent),
            graph_input={"messages": []},
            config={},
            stream_modes=["values"],
        )
    )
    record.task = task

    item = await record.interjections.enqueue("remember this", source="web")
    await asyncio.wait_for(applied_event.wait(), timeout=5)

    assert len(agent.update_calls) == 1
    values, as_node = agent.update_calls[0]
    assert as_node == INTERJECTION_AS_NODE
    assert [m.content for m in values["messages"]] == ["remember this"]
    assert item.applied is True

    # SSE/custom event announces the applied interjections.
    events = _published_events(bridge)
    applied = [data for (event, data) in events if event == "custom" and data.get("type") == INTERJECTION_APPLIED_EVENT]
    assert len(applied) == 1
    assert applied[0]["interjections"][0]["id"] == item.id

    await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_worker_discards_unapplied_interjections_at_run_end():
    applied_event = asyncio.Event()
    agent = _InterjectableAgent(applied_event)
    # Agent never waits: run finishes before the consumer drains.
    agent.applied_event.set()
    bridge = _make_bridge()
    mgr = RunManager()
    record = await mgr.create("t1")
    record.ensure_interjections(max_pending=4)
    task = asyncio.create_task(
        run_agent(
            bridge,
            mgr,
            record,
            ctx=RunContext(checkpointer=None, app_config=_app_config()),
            agent_factory=_factory(agent),
            graph_input={"messages": []},
            config={},
            stream_modes=["values"],
        )
    )
    record.task = task
    await record.interjections.enqueue("too late", source="web")
    await asyncio.wait_for(task, timeout=5)
    await asyncio.sleep(0.05)
    events = _published_events(bridge)
    assert not [data for (event, data) in events if event == "custom" and data.get("type") == INTERJECTION_APPLIED_EVENT]


# ---------------------------------------------------------------------------
# API helpers / endpoints
# ---------------------------------------------------------------------------


def _make_request(app, run_manager):
    request = SimpleNamespace(app=app)
    app.state.run_manager = run_manager
    return request


@pytest.mark.asyncio
async def test_try_enqueue_returns_202_payload_when_inflight():
    mgr = RunManager()
    record = await mgr.create("t1")
    record.task = asyncio.create_task(asyncio.sleep(0))
    record.ensure_interjections()
    app = SimpleNamespace(state=SimpleNamespace(run_manager=mgr))
    request = _make_request(app, mgr)

    body = RunCreateRequest(input={"messages": [{"type": "human", "content": "hello there"}]})
    import app.gateway.routers.thread_runs as tr

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tr, "get_app_config", lambda: _app_config(enabled=True))
        payload = await _try_enqueue_interjection(body, "t1", request)
    assert payload is not None
    assert payload["status"] == "interject_queued"
    assert payload["run_id"] == record.run_id
    assert payload["interjection"]["text"] == "hello there"
    record.task.cancel()


@pytest.mark.asyncio
async def test_try_enqueue_returns_none_when_disabled():
    mgr = RunManager()
    record = await mgr.create("t1")
    record.task = asyncio.create_task(asyncio.sleep(0))
    app = SimpleNamespace(state=SimpleNamespace(run_manager=mgr))
    request = _make_request(app, mgr)
    body = RunCreateRequest(input={"messages": [{"type": "human", "content": "hi"}]})
    import app.gateway.routers.thread_runs as tr

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tr, "get_app_config", lambda: _app_config(enabled=False))
        assert await _try_enqueue_interjection(body, "t1", request) is None
    record.task.cancel()


@pytest.mark.asyncio
async def test_try_enqueue_explicit_action_409_when_no_inflight():
    mgr = RunManager()
    app = SimpleNamespace(state=SimpleNamespace(run_manager=mgr))
    request = _make_request(app, mgr)
    body = RunCreateRequest(action="interject", input={"messages": [{"type": "human", "content": "hi"}]})
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await _try_enqueue_interjection(body, "t-idle", request)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_try_enqueue_429_when_queue_full():
    mgr = RunManager()
    record = await mgr.create("t1")
    record.task = asyncio.create_task(asyncio.sleep(0))
    record.ensure_interjections(max_pending=1)
    await record.interjections.enqueue("full")
    app = SimpleNamespace(state=SimpleNamespace(run_manager=mgr))
    request = _make_request(app, mgr)
    body = RunCreateRequest(input={"messages": [{"type": "human", "content": "hi"}]})
    from fastapi import HTTPException

    import app.gateway.routers.thread_runs as tr

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tr, "get_app_config", lambda: _app_config(enabled=True, max_pending=1))
        with pytest.raises(HTTPException) as exc_info:
            await _try_enqueue_interjection(body, "t1", request)
    assert exc_info.value.status_code == 429
    record.task.cancel()


@pytest.mark.asyncio
async def test_try_enqueue_skips_resume_command_and_non_reject_strategy():
    mgr = RunManager()
    record = await mgr.create("t1")
    record.task = asyncio.create_task(asyncio.sleep(0))
    record.ensure_interjections()
    app = SimpleNamespace(state=SimpleNamespace(run_manager=mgr))
    request = _make_request(app, mgr)
    import app.gateway.routers.thread_runs as tr

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tr, "get_app_config", lambda: _app_config(enabled=True))
        resume_body = RunCreateRequest(command={"resume": {"answer": 1}})
        assert await _try_enqueue_interjection(resume_body, "t1", request) is None
        interrupt_body = RunCreateRequest(multitask_strategy="interrupt", input={"messages": [{"type": "human", "content": "hi"}]})
        assert await _try_enqueue_interjection(interrupt_body, "t1", request) is None
    record.task.cancel()


@pytest.mark.asyncio
async def test_explicit_interjection_endpoint_and_cancel():
    mgr = RunManager()
    record = await mgr.create("t1")
    record.task = asyncio.create_task(asyncio.sleep(0))
    record.ensure_interjections()
    app = SimpleNamespace(state=SimpleNamespace(run_manager=mgr))
    request = _make_request(app, mgr)
    from _router_auth_helpers import call_unwrapped

    import app.gateway.routers.thread_runs as tr

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tr, "get_app_config", lambda: _app_config(enabled=True))
        created = await call_unwrapped(create_interjection, "t1", InterjectionCreateRequest(text="inject me"), request)
        assert created.applied is False
        assert created.text == "inject me"
        from fastapi import HTTPException, Response

        cancelled = await call_unwrapped(cancel_interjection, "t1", created.id, request)
        assert isinstance(cancelled, Response) and cancelled.status_code == 204
        with pytest.raises(HTTPException) as e404:
            await call_unwrapped(cancel_interjection, "t1", created.id, request)
        assert e404.value.status_code == 404
    record.task.cancel()


# ---------------------------------------------------------------------------
# Router over TestClient (auth decorator path)
# ---------------------------------------------------------------------------


def test_delete_interjection_route_404_for_unknown_thread_run():
    from _router_auth_helpers import make_authed_test_app

    from app.gateway.routers import thread_runs as tr_module

    app = make_authed_test_app()
    app.include_router(tr_module.router)
    app.state.run_manager = RunManager()
    with TestClient(app) as client:
        resp = client.delete("/api/threads/t-none/interjections/nope")
    assert resp.status_code == 404


def test_list_interjections_route_returns_empty_when_idle():
    from _router_auth_helpers import make_authed_test_app

    from app.gateway.routers import thread_runs as tr_module

    app = make_authed_test_app()
    app.include_router(tr_module.router)
    app.state.run_manager = RunManager()
    with TestClient(app) as client:
        resp = client.get("/api/threads/t-none/interjections")
    assert resp.status_code == 200
    assert resp.json() == []
