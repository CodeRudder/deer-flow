from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.admin.session_trace_service import SessionTraceNotFoundError, SessionTraceService
from deerflow.persistence.base import Base
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow
from deerflow.runtime.events.store.db import DbRunEventStore
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.user_context import reset_current_user, set_current_user


@pytest_asyncio.fixture
async def trace_service(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'traces.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    events = MemoryRunEventStore()
    async with sf() as session:
        session.add_all(
            [
                UserRow(id="user-a", email="alice@example.com", system_role="user"),
                UserRow(id="user-b", email="ops@example.com", system_role="user"),
                ThreadMetaRow(thread_id="thread-a", user_id="user-a", display_name="Alice thread"),
                ThreadMetaRow(thread_id="thread-b", user_id="user-b", display_name="Ops thread"),
                RunRow(
                    run_id="run-a-new",
                    thread_id="thread-a",
                    user_id="user-a",
                    status="success",
                    model_name="model-a",
                    created_at=datetime(2026, 7, 10, tzinfo=UTC),
                    updated_at=datetime(2026, 7, 10, 0, 1, tzinfo=UTC),
                    total_tokens=100,
                    llm_call_count=2,
                    image_generation_count=1,
                    token_usage_by_model={"model-a": {"total_tokens": 100}},
                    first_human_message="--- BEGIN USER INPUT ---\nhello\n--- END USER INPUT ---",
                    last_ai_message="world\nresult",
                ),
                RunRow(
                    run_id="run-a-old",
                    thread_id="thread-a",
                    user_id="user-a",
                    status="error",
                    model_name="model-b",
                    created_at=datetime(2026, 5, 1, tzinfo=UTC),
                    total_tokens=999,
                    llm_call_count=9,
                ),
                RunRow(
                    run_id="run-b",
                    thread_id="thread-b",
                    user_id="user-b",
                    status="running",
                    model_name="model-b",
                    created_at=datetime(2026, 7, 12, tzinfo=UTC),
                    total_tokens=50,
                    llm_call_count=1,
                ),
            ]
        )
        await session.commit()
    service = SessionTraceService(sf, events, now=lambda: datetime(2026, 7, 13, 8, tzinfo=UTC))
    try:
        yield service, events
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_users_are_recent_first_and_searchable(trace_service):
    service, _ = trace_service
    result = await service.list_users(keyword=None, limit=20, cursor=None)
    assert [item["user_id"] for item in result["items"]] == ["user-b", "user-a"]

    searched = await service.list_users(keyword="ALICE", limit=20, cursor=None)
    assert [item["user_id"] for item in searched["items"]] == ["user-a"]


@pytest.mark.asyncio
async def test_user_overview_is_limited_to_last_30_days(trace_service):
    service, _ = trace_service
    result = await service.user_overview("user-a")

    assert result["summary"] == {
        "thread_count": 1,
        "run_count": 1,
        "total_tokens": 100,
        "model_requests": 2,
        "image_generations": 1,
    }
    assert len(result["trends"]) == 30
    assert result["trends"][-4] == {
        "date": "2026-07-10",
        "tokens": 100,
        "model_requests": 2,
        "image_generations": 1,
    }
    assert result["models"][0]["model"] == "model-a"
    assert result["models"][0]["share"] == 1.0


@pytest.mark.asyncio
async def test_run_filters_use_and_and_events_validate_ownership(trace_service):
    service, events = trace_service
    result = await service.list_runs(user_id="user-a", thread_id="thread-a", run_id=None, page=1, page_size=20)
    assert [item["run_id"] for item in result["items"]] == ["run-a-new", "run-a-old"]
    assert result["items"][0]["thread_title"] == "Alice thread"
    assert result["items"][0]["duration_ms"] == 60_000
    assert result["items"][0]["input_preview"] == "hello"
    assert result["items"][0]["output_preview"] == "world result"

    mismatch = await service.list_runs(user_id="user-b", thread_id="thread-a", run_id=None, page=1, page_size=20)
    assert mismatch["items"] == []

    await events.put(
        thread_id="thread-a",
        run_id="run-a-new",
        event_type="llm.ai.response",
        category="message",
        content={
            "type": "ai",
            "tool_calls": [
                {"id": "call-1", "name": "web_search", "args": {}},
                {"id": "call-2", "name": "web_search", "args": {}},
                {"id": "call-3", "name": "view_image", "args": {}},
            ],
        },
    )
    await events.put(
        thread_id="thread-a",
        run_id="run-a-new",
        event_type="llm.tool.result",
        category="message",
        content={"type": "tool", "name": "web_search", "tool_call_id": "call-1"},
    )
    await events.put(thread_id="thread-a", run_id="run-a-new", event_type="run.end", category="trace")
    detail = await service.run_events("run-a-new", thread_id="thread-a", limit=2)
    assert [item["seq"] for item in detail["items"]] == [1, 2]
    assert detail["truncated"] is True
    assert detail["tool_summary"] == {
        "total_calls": 3,
        "tools": [
            {"name": "web_search", "call_count": 2},
            {"name": "view_image", "call_count": 1},
        ],
        "complete": False,
    }

    await events.put(
        thread_id="thread-a",
        run_id="run-a-old",
        event_type="llm.tool.result",
        category="message",
        content={"type": "tool", "name": "legacy_tool", "tool_call_id": "legacy-1"},
    )
    legacy_detail = await service.run_events("run-a-old", thread_id="thread-a", limit=10)
    assert legacy_detail["tool_summary"] == {
        "total_calls": 1,
        "tools": [{"name": "legacy_tool", "call_count": 1}],
        "complete": True,
    }

    with pytest.raises(SessionTraceNotFoundError):
        await service.run_events("run-a-new", thread_id="thread-b", limit=2)


@pytest.mark.asyncio
@pytest.mark.no_auto_user
async def test_admin_run_events_use_trusted_unscoped_db_read(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'admin-traces.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    events = DbRunEventStore(sf)

    async with sf() as session:
        session.add_all(
            [
                UserRow(id="user-a", email="alice@example.com", system_role="user"),
                UserRow(id="admin-a", email="admin@example.com", system_role="admin"),
                ThreadMetaRow(thread_id="thread-a", user_id="user-a", display_name="Alice thread"),
                RunRow(run_id="run-a", thread_id="thread-a", user_id="user-a", status="success"),
            ]
        )
        await session.commit()

    owner_token = set_current_user(SimpleNamespace(id="user-a"))
    try:
        await events.put(
            thread_id="thread-a",
            run_id="run-a",
            event_type="llm.ai.response",
            category="message",
            content={"type": "ai", "tool_calls": [{"id": "call-1", "name": "web_search", "args": {}}]},
        )
    finally:
        reset_current_user(owner_token)

    # Background and legacy writes can legitimately have no ambient owner.
    await events.put(
        thread_id="thread-a",
        run_id="run-a",
        event_type="run.end",
        category="trace",
    )

    service = SessionTraceService(sf, events)
    admin_token = set_current_user(SimpleNamespace(id="admin-a"))
    try:
        assert await events.list_events("thread-a", "run-a") == []
        detail = await service.run_events("run-a", thread_id="thread-a", limit=500)
    finally:
        reset_current_user(admin_token)
        await engine.dispose()

    assert detail["returned"] == 2
    assert detail["items"][0]["event_type"] == "llm.ai.response"
    assert detail["items"][1]["event_type"] == "run.end"
    assert detail["tool_summary"]["total_calls"] == 1
