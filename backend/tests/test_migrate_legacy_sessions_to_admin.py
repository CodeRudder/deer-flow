"""Tests for the legacy session-to-admin migration script."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from deerflow.config.paths import Paths
from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow


@dataclass
class _StoreItem:
    key: str
    value: dict
    namespace: tuple[str, ...] = ("threads",)


class _FakeStore:
    def __init__(self, items: dict[str, dict] | None = None):
        self.items = items or {}
        self.puts: list[tuple[tuple[str, ...], str, dict]] = []

    async def asearch(self, namespace, *, limit=500, offset=0):
        rows = [_StoreItem(key, value) for key, value in sorted(self.items.items())]
        return rows[offset : offset + limit]

    async def aput(self, namespace, key, value):
        self.items[key] = value
        self.puts.append((namespace, key, value))


class _FakeCheckpointer:
    def __init__(self, tuples: dict[str, object] | None = None):
        self.tuples = tuples or {}

    async def aget_tuple(self, config):
        thread_id = config["configurable"]["thread_id"]
        return self.tuples.get(thread_id)


class _Msg:
    def __init__(self, typ: str, content: str, *, msg_id: str):
        self.type = typ
        self.content = content
        self.id = msg_id

    def model_dump(self):
        return {"type": self.type, "content": self.content, "id": self.id}


@pytest.fixture
async def session_factory(tmp_path):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}", sqlite_dir=str(tmp_path))
    sf = get_session_factory()
    assert sf is not None
    try:
        yield sf
    finally:
        await close_engine()


async def _seed_admin(sf, *, admin_id="admin-1", email="admin@example.com"):
    async with sf() as session:
        session.add(
            UserRow(
                id=admin_id,
                email=email,
                password_hash=None,
                system_role="admin",
            )
        )
        await session.commit()


def _checkpoint_tuple(*messages, title="Legacy title"):
    return SimpleNamespace(
        checkpoint={"channel_values": {"messages": list(messages), "title": title}},
        metadata={"created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:01:00+00:00"},
    )


@pytest.mark.anyio
async def test_migrates_store_checkpoint_and_legacy_dir_to_admin(tmp_path, session_factory):
    from scripts.migrate_legacy_sessions_to_admin import legacy_run_id, migrate_legacy_sessions

    await _seed_admin(session_factory)
    paths = Paths(tmp_path)
    legacy_file = tmp_path / "threads" / "thread-1" / "user-data" / "workspace" / "old.txt"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text("old")
    store = _FakeStore({"thread-1": {"metadata": {}, "values": {"title": "Store title"}}})
    checkpointer = _FakeCheckpointer(
        {
            "thread-1": _checkpoint_tuple(
                _Msg("human", "hello", msg_id="h1"),
                _Msg("ai", "answer", msg_id="a1"),
                title="Checkpoint title",
            )
        }
    )

    stats = await migrate_legacy_sessions(
        sf=session_factory,
        paths=paths,
        admin_id="admin-1",
        admin_email="admin@example.com",
        store=store,
        checkpointer=checkpointer,
        dry_run=False,
    )

    assert stats.threads_meta_created == 1
    assert stats.runs_created == 1
    assert stats.run_events_created == 2
    assert stats.event_sources["checkpoint"] == 2
    assert stats.store_owner_updated == 1
    assert stats.legacy_dirs_moved == 1
    assert store.items["thread-1"]["metadata"]["user_id"] == "admin-1"
    assert (tmp_path / "users" / "admin-1" / "threads" / "thread-1" / "user-data" / "workspace" / "old.txt").read_text() == "old"

    async with session_factory() as session:
        thread = await session.get(ThreadMetaRow, "thread-1")
        assert thread is not None
        assert thread.user_id == "admin-1"
        assert thread.display_name == "Store title"

        run = await session.get(RunRow, legacy_run_id("thread-1"))
        assert run is not None
        assert run.user_id == "admin-1"
        assert run.first_human_message == "hello"
        assert run.last_ai_message == "answer"
        assert run.message_count == 2

        rows = list((await session.execute(select(RunEventRow).order_by(RunEventRow.seq))).scalars())
        assert [row.event_type for row in rows] == ["llm.human.input", "llm.ai.response"]
        assert [row.user_id for row in rows] == ["admin-1", "admin-1"]


@pytest.mark.anyio
async def test_jsonl_is_lossy_fallback_when_checkpoint_has_no_messages(tmp_path, session_factory):
    from scripts.migrate_legacy_sessions_to_admin import migrate_legacy_sessions

    await _seed_admin(session_factory)
    paths = Paths(tmp_path)
    jsonl = tmp_path / "threads" / "thread-jsonl" / "conversation.jsonl"
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text(
        '{"ts":"2026-01-01T00:00:00+00:00","role":"human","content":"from jsonl","id":"j1"}\n{"ts":"2026-01-01T00:01:00+00:00","role":"ai","content":"jsonl answer","id":"j2"}\n',
        encoding="utf-8",
    )

    stats = await migrate_legacy_sessions(
        sf=session_factory,
        paths=paths,
        admin_id="admin-1",
        store=_FakeStore(),
        checkpointer=_FakeCheckpointer(),
        dry_run=False,
    )

    assert stats.run_events_created == 2
    assert stats.event_sources["conversation_jsonl"] == 2
    async with session_factory() as session:
        rows = list((await session.execute(select(RunEventRow).order_by(RunEventRow.seq))).scalars())
        assert [row.event_type for row in rows] == ["llm.human.input", "llm.ai.response"]
        assert all(row.event_metadata["lossy"] is True for row in rows)
        assert all(row.event_metadata["legacy_source"] == "conversation_jsonl" for row in rows)


@pytest.mark.anyio
async def test_dry_run_does_not_write_or_move(tmp_path, session_factory):
    from scripts.migrate_legacy_sessions_to_admin import migrate_legacy_sessions

    await _seed_admin(session_factory)
    paths = Paths(tmp_path)
    legacy_dir = tmp_path / "threads" / "thread-1"
    legacy_dir.mkdir(parents=True)
    store = _FakeStore({"thread-1": {"metadata": {}, "values": {}}})

    stats = await migrate_legacy_sessions(
        sf=session_factory,
        paths=paths,
        admin_id="admin-1",
        store=store,
        checkpointer=_FakeCheckpointer({"thread-1": _checkpoint_tuple(_Msg("human", "hello", msg_id="h1"))}),
        dry_run=True,
    )

    assert stats.threads_meta_created == 1
    assert stats.runs_created == 1
    assert stats.run_events_created == 1
    assert stats.store_owner_updated == 1
    assert stats.legacy_dirs_moved == 1
    assert store.items["thread-1"]["metadata"] == {}
    assert legacy_dir.exists()
    assert not (tmp_path / "users" / "admin-1" / "threads" / "thread-1").exists()

    async with session_factory() as session:
        assert await session.get(ThreadMetaRow, "thread-1") is None
        assert list((await session.execute(select(RunRow))).scalars()) == []
        assert list((await session.execute(select(RunEventRow))).scalars()) == []


@pytest.mark.anyio
async def test_existing_other_owner_is_reported_not_overwritten(tmp_path, session_factory):
    from scripts.migrate_legacy_sessions_to_admin import migrate_legacy_sessions

    await _seed_admin(session_factory)
    async with session_factory() as session:
        session.add(
            ThreadMetaRow(
                thread_id="thread-1",
                assistant_id=None,
                user_id="other-user",
                display_name="Other",
                status="idle",
                metadata_json={},
            )
        )
        await session.commit()

    stats = await migrate_legacy_sessions(
        sf=session_factory,
        paths=Paths(tmp_path),
        admin_id="admin-1",
        store=_FakeStore({"thread-1": {"metadata": {}, "values": {}}}),
        checkpointer=_FakeCheckpointer(),
        dry_run=False,
    )

    assert any("already owned by other-user" in item for item in stats.conflicts)
    async with session_factory() as session:
        thread = await session.get(ThreadMetaRow, "thread-1")
        assert thread.user_id == "other-user"
