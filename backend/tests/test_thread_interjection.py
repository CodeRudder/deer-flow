"""Thread interjection (plan-b) — queue, worker injection, manager lookup, API.

Design: docs/research/2026-11-01-thread-interjection/plan-b.md
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

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
    INTERJECTION_AUTO_RUN_EVENT,
    InterjectionAlreadyAppliedError,
    InterjectionApplier,
    InterjectionNotFoundError,
    InterjectionQueueFullError,
    merge_interjection_texts,
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


class _RecordingAgent:
    """Fake agent: streams two chunks and records finalize-time aupdate_state."""

    def __init__(self):
        self.update_calls: list[tuple[dict, str | None]] = []

    async def aupdate_state(self, config, values, as_node=None):
        self.update_calls.append((values, as_node))
        return None

    async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
        yield {"messages": []}
        yield {"messages": []}


def _factory(agent):
    def factory(*, config):
        return agent

    return factory


class _FakeContext:
    def __init__(self, values: dict):
        self.context = values


class _FakeRequest:
    def __init__(self, context: dict, messages: list):
        self.runtime = _FakeContext(context)
        self.messages = messages

    def override(self, *, messages):
        return _FakeRequest(self.runtime.context, messages)


class _FakeJournal:
    def __init__(self):
        self.interjections: list[dict] = []
        self.messages: list = []

    def record_interjection(self, **kwargs):
        self.interjections.append(kwargs)

    def record_interjection_message(self, message, **kwargs):
        self.messages.append((message, kwargs))


async def _make_applier_with_items(texts: list[str]) -> InterjectionApplier:
    """Applier with items still queued (not yet drained/applied)."""
    applier = InterjectionApplier("t1", "r1", max_pending=8)
    for text in texts:
        await applier.enqueue(text, source="web")
    return applier


# ---------------------------------------------------------------------------
# InterjectionMiddleware (model-call boundary injection)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_appends_interjections_to_model_call():
    from deerflow.agents.middlewares.interjection_middleware import InterjectionMiddleware

    applier = await _make_applier_with_items(["first", "second"])
    journal = _FakeJournal()
    bridge = _make_bridge()
    context = {
        "__run_interjections": applier,
        "__run_journal": journal,
        "__run_bridge": bridge,
    }
    request = _FakeRequest(context, [HumanMessage(content="original question")])
    seen: list[list] = []

    async def handler(req):
        seen.append(list(req.messages))
        return "response"

    result = await InterjectionMiddleware().awrap_model_call(request, handler)
    assert result == "response"
    assert len(seen) == 1
    contents = [m.content for m in seen[0]]
    assert contents == ["original question", "first", "second"]
    # Applied + announced + journalled (trace + displayable message).
    assert all(item.applied for item in applier.applied_items)
    applied = [data for (event, data) in _published_events(bridge) if event == "custom" and data.get("type") == INTERJECTION_APPLIED_EVENT]
    assert len(applied) == 1
    assert len(applied[0]["interjections"]) == 2
    assert len(journal.interjections) == 2
    assert len(journal.messages) == 2


@pytest.mark.asyncio
async def test_middleware_noop_without_applier_or_queue():
    from deerflow.agents.middlewares.interjection_middleware import InterjectionMiddleware

    request = _FakeRequest({}, [HumanMessage(content="q")])
    seen: list[list] = []

    async def handler(req):
        seen.append(list(req.messages))
        return "ok"

    assert await InterjectionMiddleware().awrap_model_call(request, handler) == "ok"
    assert [m.content for m in seen[0]] == ["q"]

    applier = InterjectionApplier("t1", "r1")
    context = {"__run_interjections": applier}
    request2 = _FakeRequest(context, [HumanMessage(content="q2")])
    assert await InterjectionMiddleware().awrap_model_call(request2, handler) == "ok"
    assert len(seen) == 2


# ---------------------------------------------------------------------------
# Worker finalize: applied -> final checkpoint, leftover -> discarded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_persists_applied_interjections_into_final_checkpoint():
    agent = _RecordingAgent()
    bridge = _make_bridge()
    mgr = RunManager()
    record = await mgr.create("t1")
    record.ensure_interjections(max_pending=4)
    item = await record.interjections.enqueue("remember this", source="web")
    batch = await record.interjections.drain_batch(first_wait_timeout=0)
    await record.interjections.apply_batch(batch)
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
    await asyncio.wait_for(task, timeout=5)

    assert len(agent.update_calls) == 1
    values, as_node = agent.update_calls[0]
    assert as_node == INTERJECTION_AS_NODE
    assert [m.content for m in values["messages"]] == ["remember this"]
    assert values["messages"][0].additional_kwargs.get("interjection") is True
    assert item.applied is True


@pytest.mark.asyncio
async def test_worker_discards_unapplied_interjections_at_run_end():
    agent = _RecordingAgent()
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
    assert agent.update_calls == []


# ---------------------------------------------------------------------------
# Worker finalize: leftover -> merged follow-up run (auto-run)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_interjection_texts_joins_in_order():
    applier = await _make_applier_with_items(["first", "second", "third"])
    items = await applier.take_pending_for_auto_run()
    assert merge_interjection_texts(items) == "first\n\nsecond\n\nthird"
    assert merge_interjection_texts(items, separator=" | ") == "first | second | third"


@pytest.mark.asyncio
async def test_take_pending_for_auto_run_is_idempotent_and_unregisters():
    applier = await _make_applier_with_items(["only"])
    item = applier.pending_snapshot()[0]
    first = await applier.take_pending_for_auto_run()
    assert [i.text for i in first] == ["only"]
    # Second take: nothing left — the batch can never spawn two follow-ups.
    assert await applier.take_pending_for_auto_run() == []
    # Removed from the pending registry: snapshot and cancel both forget it.
    assert applier.pending_snapshot() == []
    with pytest.raises(InterjectionNotFoundError):
        applier.cancel(item["id"])


@pytest.mark.asyncio
async def test_worker_auto_starts_followup_run_for_leftover_interjections():
    agent = _RecordingAgent()
    bridge = _make_bridge()
    mgr = RunManager()
    record = await mgr.create("t1")
    record.ensure_interjections(max_pending=4)
    spawned: list[str] = []

    async def followup_runner(merged_text: str):
        spawned.append(merged_text)
        return SimpleNamespace(run_id="r-followup")

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
            followup_runner=followup_runner,
        )
    )
    record.task = task
    first = await record.interjections.enqueue("first ask", source="web")
    second = await record.interjections.enqueue("second ask", source="web")
    await asyncio.wait_for(task, timeout=5)
    await asyncio.sleep(0.05)

    # Merged into ONE follow-up run with one user message.
    assert spawned == ["first ask\n\nsecond ask"]
    events = _published_events(bridge)
    auto_events = [data for (event, data) in events if event == "custom" and data.get("type") == INTERJECTION_AUTO_RUN_EVENT]
    assert len(auto_events) == 1
    payload = auto_events[0]
    assert payload["new_run_id"] == "r-followup"
    assert payload["thread_id"] == "t1"
    assert payload["input"] == "first ask\n\nsecond ask"
    assert [item["id"] for item in payload["interjections"]] == [first.id, second.id]
    # Applied-checkpoint persistence untouched: leftovers were never injected.
    assert agent.update_calls == []
    # Queue fully consumed — no double execution.
    assert record.interjections.pending_snapshot() == []
    assert await record.interjections.take_pending_for_auto_run() == []


@pytest.mark.asyncio
async def test_worker_followup_failure_falls_back_to_discard():
    agent = _RecordingAgent()
    bridge = _make_bridge()
    mgr = RunManager()
    record = await mgr.create("t1")
    record.ensure_interjections(max_pending=4)

    async def failing_runner(merged_text: str):
        raise RuntimeError("thread deleted / concurrency limit")

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
            followup_runner=failing_runner,
        )
    )
    record.task = task
    await record.interjections.enqueue("too late", source="web")
    await asyncio.wait_for(task, timeout=5)
    await asyncio.sleep(0.05)

    # Run finished normally despite the spawner failing; nothing announced.
    assert record.status.value in {"success", "error"}
    events = _published_events(bridge)
    assert not [data for (event, data) in events if event == "custom" and data.get("type") == INTERJECTION_AUTO_RUN_EVENT]
    # Queue consumed exactly once (no retry loop, no duplicate run).
    assert record.interjections.pending_snapshot() == []


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


# ---------------------------------------------------------------------------
# Journal: interjection trace + displayable message records
# ---------------------------------------------------------------------------


async def _flush(journal):
    await journal.flush()


@pytest.mark.asyncio
async def test_journal_records_interjection_trace_and_message():
    from deerflow.runtime.events.store.memory import MemoryRunEventStore
    from deerflow.runtime.journal import RunJournal

    store = MemoryRunEventStore()
    journal = RunJournal("r1", "t1", store, flush_threshold=100)
    journal.record_interjection(interjection_id="int-1", text="hello", source="web", seq=1)
    message = HumanMessage(id="m-1", content="hello", additional_kwargs={"interjection": True})
    journal.record_interjection_message(message, interjection_id="int-1", seq=1, source="web")
    # Deduped by message id on repeat calls.
    journal.record_interjection_message(message, interjection_id="int-1", seq=1, source="web")
    await journal.flush()

    rows = list(store._events.get("t1", []))
    trace = [r for r in rows if r["event_type"] == "interjection.applied"]
    display = [r for r in rows if r["event_type"] == "llm.human.input"]
    assert len(trace) == 1
    assert trace[0]["category"] == "trace"
    assert trace[0]["content"]["interjection_id"] == "int-1"
    assert len(display) == 1
    assert display[0]["category"] == "message"
    assert display[0]["metadata"]["interjection_id"] == "int-1"


# ---------------------------------------------------------------------------
# End-to-end run wiring: enqueue mid-run -> middleware applies -> finalize persists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_wiring_enqueue_midrun_applied_and_persisted():
    """Full worker path: the middleware sees the applier through the runtime
    context installed by the worker; a mid-run enqueue is applied at the
    (simulated) next model call and persisted into the final checkpoint."""
    from deerflow.agents.middlewares.interjection_middleware import (
        RUN_BRIDGE_CONTEXT_KEY,
        RUN_INTERJECTIONS_CONTEXT_KEY,
        InterjectionMiddleware,
    )

    class _WiredAgent(_RecordingAgent):
        """Simulates one model call, a gap (enqueue window), a second call."""

        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            runtime = (config or {}).get("configurable", {}).get("__pregel_runtime")
            context = getattr(runtime, "context", {}) or {}
            middleware = InterjectionMiddleware()
            request = _FakeRequest(context, [HumanMessage(content="turn question")])

            async def handler(req):
                return "call-1"

            await middleware.awrap_model_call(request, handler)
            yield {"messages": []}
            await asyncio.sleep(0.2)  # window for the API-side enqueue
            await middleware.awrap_model_call(request, handler)
            yield {"messages": []}

    agent = _WiredAgent()
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
    await asyncio.sleep(0.05)
    item = await record.interjections.enqueue("mid-run input", source="web")
    await asyncio.wait_for(item.applied_event.wait(), timeout=5)
    await asyncio.wait_for(task, timeout=5)

    assert item.applied is True
    assert [item.text for item in record.interjections.applied_items] == ["mid-run input"]
    # Finalize persisted the applied interjection into the checkpoint.
    assert len(agent.update_calls) == 1
    values, as_node = agent.update_calls[0]
    assert as_node == INTERJECTION_AS_NODE
    assert [m.content for m in values["messages"]] == ["mid-run input"]
    # Applied event published on the run's bridge.
    applied = [data for (event, data) in _published_events(bridge) if event == "custom" and data.get("type") == INTERJECTION_APPLIED_EVENT]
    assert len(applied) == 1
    assert applied[0]["interjections"][0]["id"] == item.id
    # Context keys were installed by the worker.
    assert RUN_INTERJECTIONS_CONTEXT_KEY and RUN_BRIDGE_CONTEXT_KEY
