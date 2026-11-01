"""Interrupted-run message visibility across refresh.

Regression coverage for lost last-message-after-refresh on interrupted
runs:

1. Worker side — ``run_agent`` persists streamed ``messages``-mode chunks
   as pending writes on abort/cancel (interrupt action), so the partial
   assistant reply is not silently discarded with the graph state.
2. Read side — ``/history``, ``/state`` and ``GET /threads/{id}`` merge
   pending ``messages`` writes into the latest checkpoint's committed
   messages, deduplicated by message id (a completed run must not show
   duplicates).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from app.gateway.routers import threads
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.worker import RunContext, run_agent

THREAD_ID = "interrupted-visibility"


def _build_thread_app(checkpointer: InMemorySaver) -> FastAPI:
    app = make_authed_test_app()
    store = InMemoryStore()
    app.state.store = store
    app.state.checkpointer = checkpointer
    app.state.thread_store = MemoryThreadMetaStore(store)
    app.include_router(threads.router)
    return app


async def _seed_committed_checkpoint(checkpointer: InMemorySaver, thread_id: str) -> None:
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"messages": [HumanMessage(content="你好", id="human-1")]}
    checkpoint["channel_versions"] = {"messages": "1"}
    await checkpointer.aput(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        checkpoint,
        {"step": 0, "source": "input", "writes": None, "parents": {}},
        {"messages": "1"},
    )


# ---------------------------------------------------------------------------
# Read side: /history + /state merge pending messages writes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_history_merges_pending_message_write_from_interrupted_run():
    checkpointer = InMemorySaver()
    await _seed_committed_checkpoint(checkpointer, THREAD_ID)
    config = {"configurable": {"thread_id": THREAD_ID, "checkpoint_ns": ""}}
    latest = await checkpointer.aget_tuple(config)
    await checkpointer.aput_writes(
        latest.config,
        [("messages", AIMessage(content="被中断的部分回复", id="ai-aborted"))],
        task_id="aborted-run-1",
    )

    app = _build_thread_app(checkpointer)
    with TestClient(app) as client:
        body = client.post(f"/api/threads/{THREAD_ID}/history", json={"limit": 10}).json()

    latest_entry = body[0]
    ids = [m["id"] for m in latest_entry["values"]["messages"]]
    assert ids == ["human-1", "ai-aborted"]
    assert latest_entry["values"]["messages"][-1]["content"] == "被中断的部分回复"


@pytest.mark.anyio
async def test_state_and_thread_endpoints_merge_pending_messages():
    checkpointer = InMemorySaver()
    await _seed_committed_checkpoint(checkpointer, THREAD_ID)
    config = {"configurable": {"thread_id": THREAD_ID, "checkpoint_ns": ""}}
    latest = await checkpointer.aget_tuple(config)
    await checkpointer.aput_writes(
        latest.config,
        [("messages", AIMessage(content="中断回复", id="ai-aborted"))],
        task_id="aborted-run-1",
    )

    app = _build_thread_app(checkpointer)
    with TestClient(app) as client:
        state = client.get(f"/api/threads/{THREAD_ID}/state").json()
        thread = client.get(f"/api/threads/{THREAD_ID}").json()

    for body in (state, thread):
        ids = [m["id"] for m in body["values"]["messages"]]
        assert ids == ["human-1", "ai-aborted"]


@pytest.mark.anyio
async def test_history_does_not_duplicate_messages_already_committed():
    checkpointer = InMemorySaver()
    await _seed_committed_checkpoint(checkpointer, THREAD_ID)
    config = {"configurable": {"thread_id": THREAD_ID, "checkpoint_ns": ""}}
    latest = await checkpointer.aget_tuple(config)
    # Same id as the committed human message — must not appear twice.
    await checkpointer.aput_writes(
        latest.config,
        [("messages", HumanMessage(content="你好", id="human-1"))],
        task_id="duplicate-write",
    )
    # Non-message pending writes (__error__, __interrupt__) are ignored.
    await checkpointer.aput_writes(latest.config, [("__error__", "CancelledError()")], task_id="err-task")

    app = _build_thread_app(checkpointer)
    with TestClient(app) as client:
        body = client.post(f"/api/threads/{THREAD_ID}/history", json={"limit": 10}).json()

    ids = [m["id"] for m in body[0]["values"]["messages"]]
    assert ids == ["human-1"]


# ---------------------------------------------------------------------------
# Worker side: abort persists the streamed partial reply
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_agent_interrupt_persists_streamed_message_as_pending_write():
    checkpointer = InMemorySaver()
    await _seed_committed_checkpoint(checkpointer, THREAD_ID)

    run_manager = RunManager()
    record = await run_manager.create(THREAD_ID)

    chunks = [
        AIMessageChunk(content="部分", id="ai-streamed"),
        AIMessageChunk(content="回复", id="ai-streamed"),
    ]

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            for chunk in chunks:
                yield ("messages", (chunk, {"langgraph_step": 1}))
            record.abort_event.set()

    def factory(*, config):
        return DummyAgent()

    bridge = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())
    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=checkpointer),
        agent_factory=factory,
        graph_input={},
        stream_modes=["values", "messages"],
        config={},
    )
    await asyncio.sleep(0)

    fetched = await run_manager.get(record.run_id)
    assert fetched is not None and fetched.status == RunStatus.interrupted

    # The streamed reply must now be readable: merged into /history.
    app = _build_thread_app(checkpointer)
    with TestClient(app) as client:
        body = client.post(f"/api/threads/{THREAD_ID}/history", json={"limit": 10}).json()

    ids = [m["id"] for m in body[0]["values"]["messages"]]
    assert "ai-streamed" in ids
    merged = next(m for m in body[0]["values"]["messages"] if m["id"] == "ai-streamed")
    assert merged["content"] == "部分回复"


@pytest.mark.anyio
async def test_run_agent_success_does_not_add_pending_messages():
    """Normal completion must not leave pending message writes behind."""
    checkpointer = InMemorySaver()
    await _seed_committed_checkpoint(checkpointer, THREAD_ID)

    run_manager = RunManager()
    record = await run_manager.create(THREAD_ID)

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield ("messages", (AIMessageChunk(content="完整回复", id="ai-done"), {"langgraph_step": 1}))
            yield ("values", {"messages": [AIMessage(content="完整回复", id="ai-done")]})

    def factory(*, config):
        return DummyAgent()

    bridge = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())
    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=checkpointer),
        agent_factory=factory,
        graph_input={},
        stream_modes=["values", "messages"],
        config={},
    )
    await asyncio.sleep(0)

    fetched = await run_manager.get(record.run_id)
    assert fetched is not None and fetched.status == RunStatus.success

    latest = await checkpointer.aget_tuple({"configurable": {"thread_id": THREAD_ID, "checkpoint_ns": ""}})
    message_writes = [w for w in (latest.pending_writes or []) if w[1] == "messages"]
    assert message_writes == []
