"""Tests for the Postgres backend of CheckpointCleaner (P2 session-data-slimming).

Hermetic parts: pure keep-set computation + keep-set row shapes.
Database parts: skipped unless DATABASE_URL points at a reachable Postgres
with the LangGraph checkpointer tables (dev environment). The DB tests build
a synthetic chain directly via SQL (matching the cleaner's access pattern),
run dry-run and real cycles, and clean up after themselves.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

from app.gateway.checkpoint_cleaner import CheckpointCleaner, compute_keep_sets


class TestComputeKeepSets:
    def test_linear_chain_keeps_last_n(self):
        rows = [(f"({i})", "", f"cp{i}", f"cp{i - 1}" if i else None) for i in range(30)]
        all_ids, keep_ids, leaf_ids = compute_keep_sets(rows, 20)
        assert all_ids == {f"cp{i}" for i in range(30)}
        assert keep_ids == {f"cp{i}" for i in range(10, 30)}
        assert leaf_ids == {"cp29"}

    def test_all_leaves_survive_even_shorter_than_n(self):
        rows = [("t", "", "a", None), ("t", "", "b", "a")]
        _, keep_ids, leaf_ids = compute_keep_sets(rows, 5)
        assert keep_ids == {"a", "b"}
        assert leaf_ids == {"b"}

    def test_recent_leaf_policy_prunes_old_branches(self):
        rows = [("t", "", "root", None)]
        rows += [("t", "", f"m{i}", f"m{i - 1}" if i else "root") for i in range(30)]
        rows += [("t", "", f"f{i}", f"f{i - 1}" if i else "m25") for i in range(2)]
        all_ids, keep_ids, leaf_ids = compute_keep_sets(rows, 20, leaf_policy="recent")
        assert leaf_ids == {"m29", "f1"}  # all leaves still reported
        # only the newest leaf's chain is walked: fork + its ancestors are prunable
        assert {f"m{i}" for i in range(10, 30)} <= keep_ids
        assert not ({"f0", "f1"} & keep_ids)
        assert all_ids - keep_ids == {f"m{i}" for i in range(0, 10)} | {f"f{i}" for i in range(2)} | {"root"}

    def test_branch_leaves_are_never_deleted(self):
        rows = [("t", "", "root", None)]
        rows += [("t", "", f"m{i}", f"m{i - 1}" if i else "root") for i in range(30)]
        rows += [("t", "", f"f{i}", f"f{i - 1}" if i else "m25") for i in range(2)]
        all_ids, keep_ids, leaf_ids = compute_keep_sets(rows, 20)
        assert leaf_ids == {"m29", "f1"}
        assert leaf_ids <= keep_ids
        # both branches keep their last 20 (walking up the parent chain)
        assert {f"m{i}" for i in range(10, 30)} <= keep_ids
        assert {f"f{i}" for i in range(2)} <= keep_ids
        assert "m25" in keep_ids  # fork ancestor
        # main-chain checkpoints covered by neither walk are deletable
        assert all_ids - keep_ids == {f"m{i}" for i in range(0, 8)} | {"root"}

    def test_empty_input_is_safe(self):
        assert compute_keep_sets([], 20) == (set(), set(), set())


class FakeCursor:
    """Cursor whose results are keyed by a distinctive SQL substring."""

    def __init__(self, mapping: dict[str, list]):
        self._mapping = mapping
        self._pending: list = []
        self.executed: list[str] = []

    async def execute(self, query, params=None):
        self.executed.append(query)
        self._pending = []
        for key, value in self._mapping.items():
            if key in query:
                self._pending = value
                return

    async def fetchone(self):
        return self._pending.pop(0) if self._pending else None

    async def fetchall(self):
        out = self._pending
        self._pending = []
        return out

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, mapping: dict[str, list]):
        self._mapping = mapping
        self.cursors: list[FakeCursor] = []

    def cursor(self):
        cur = FakeCursor(self._mapping)
        self.cursors.append(cur)
        return cur

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn: FakeConn):
        self._conn = conn

    def connection(self):
        return self._conn


def _make_cleaner(pool, keep=20, dry_run=False) -> CheckpointCleaner:
    saver = type("FakeSaver", (), {"pool": pool})()
    return CheckpointCleaner(saver, keep=keep, dry_run=dry_run)


@pytest.mark.asyncio
async def test_postgres_aborts_when_keep_set_empty():
    conn = FakeConn({"pg_database_size": [{"sz": 0}], "FROM checkpoints": []})
    cleaner = _make_cleaner(FakePool(conn))
    await cleaner.run_once()  # must not raise; aborted safely


@pytest.mark.asyncio
async def test_postgres_dry_run_executes_no_deletes():
    """dry_run must run the read/plan path and emit zero DELETE statements."""
    chain = [{"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": f"cp{i}", "parent_checkpoint_id": f"cp{i - 1}" if i else None} for i in range(25)]
    kept_checkpoint = {"channel_versions": {"messages": "v24", "__pregel_tasks": "t24"}}
    conn = FakeConn(
        {
            "pg_database_size": [{"sz": 1024}],
            "parent_checkpoint_id FROM checkpoints": chain,  # keep-set snapshot
            "to_regclass": [{"present": True}],
            "FROM threads_meta": [{"thread_id": "t1"}],
            "SELECT DISTINCT thread_id FROM checkpoints": [{"thread_id": "t1"}],
            "SUM(length(checkpoint::text))": [{"n": 5, "b": 5000}],
            "FROM checkpoint_writes WHERE checkpoint_id": [{"n": 3, "b": 300}],
            "checkpoint FROM checkpoints WHERE checkpoint_id = ANY": [{"thread_id": "t1", "checkpoint_ns": "", "checkpoint": kept_checkpoint}],
            "FROM checkpoint_blobs": [{"thread_id": "t1", "checkpoint_ns": "", "channel": "messages", "version": f"v{i}", "b": 100} for i in range(25)],
        }
    )
    cleaner = _make_cleaner(FakePool(conn), keep=20, dry_run=True)
    await cleaner.run_once()
    executed = [q for c in conn.cursors for q in c.executed]
    assert not any(q.strip().upper().startswith("DELETE") for q in executed), "dry run must not delete"
    # the cycle reached the blob-purge planning step (all reads happened)
    assert any("FROM checkpoint_blobs" in q for q in executed)


def _has_database_url() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


requires_postgres = pytest.mark.skipif(not _has_database_url(), reason="DATABASE_URL not set")


@requires_postgres
class TestCheckpointCleanerPostgresLive:
    """End-to-end against the configured Postgres (dev). Self-cleaning.

    Runs the cleaner's Postgres path through a thin sync adapter over a real
    psycopg connection — the cleaner only issues SQL through the pool's
    cursor protocol, so the adapter exercises the production SQL verbatim.
    """

    def setup_method(self):
        import psycopg

        self.TID = f"cleaner-test-{uuid.uuid4().hex[:12]}"
        self.ROOT = f"cproot{uuid.uuid4().hex[:8]}"
        self._conn = psycopg.connect(os.environ["DATABASE_URL"])
        self._conn.autocommit = True

    def teardown_method(self):
        cur = self._conn.cursor()
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            cur.execute(f"DELETE FROM {table} WHERE thread_id = %s", (self.TID,))
        cur.execute("DELETE FROM threads_meta WHERE thread_id = %s", (self.TID,))
        self._conn.commit()
        self._conn.close()

    def _insert_chain(self, cur, n: int):
        """Linear chain of n checkpoints, each with one messages blob and one write."""
        for i in range(n):
            cp_id = f"{self.ROOT}-{i}"
            parent = f"{self.ROOT}-{i - 1}" if i else None
            version = f"0000000000000000000000000000{i:04d}"
            cur.execute(
                "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata) VALUES (%s, '', %s, %s, 'msgpack', %s::jsonb, '{}'::jsonb)",
                (self.TID, cp_id, parent, json.dumps({"channel_versions": {"messages": version}})),
            )
            cur.execute(
                "INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob) VALUES (%s, '', 'messages', %s, 'msgpack', %s)",
                (self.TID, version, b"x" * 100),
            )
        # pending writes: one on the newest (leaf, must survive) and one on the oldest (must go)
        for cp_suffix, task_id, payload in ((n - 1, "task-keep", b"keep-me"), (0, "task-old", b"drop-me")):
            cur.execute(
                "INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob) VALUES (%s, '', %s, %s, 0, 'messages', 'msgpack', %s)",
                (self.TID, f"{self.ROOT}-{cp_suffix}", task_id, payload),
            )

    def _count(self, cur, table):
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE thread_id = %s", (self.TID,))
        return cur.fetchone()[0]

    class _SyncCursor:
        def __init__(self, cur):
            self._cur = cur

        async def execute(self, query, params=None):
            self._cur.execute(query, params)

        async def fetchone(self):
            return self._cur.fetchone()

        async def fetchall(self):
            return self._cur.fetchall()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _SyncConn:
        def __init__(self, conn):
            self._conn = conn

        def cursor(self):
            return TestCheckpointCleanerPostgresLive._SyncCursor(self._conn.cursor())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _SyncPool:
        def __init__(self, conn):
            self._conn = conn

        def connection(self):
            return TestCheckpointCleanerPostgresLive._SyncConn(self._conn)

    def _run_cleaner(self, keep: int, dry_run: bool):
        cleaner = CheckpointCleaner(checkpointer=None, keep=keep, dry_run=dry_run)
        cleaner._checkpointer = type("S", (), {"pool": self._SyncPool(self._conn)})()
        asyncio.run(cleaner.run_once())

    def test_keep_n_dry_run_then_real(self):
        cur = self._conn.cursor()
        n = 25
        self._insert_chain(cur, n)
        cur.execute(
            "INSERT INTO threads_meta (thread_id, assistant_id, user_id, display_name, status, metadata_json, created_at, updated_at) VALUES (%s, 'agent', 'u1', 't', 'busy', '{}', now(), now())",
            (self.TID,),
        )

        # dry run: nothing deleted
        self._run_cleaner(keep=20, dry_run=True)
        assert self._count(cur, "checkpoints") == n
        assert self._count(cur, "checkpoint_blobs") == n
        assert self._count(cur, "checkpoint_writes") == 2

        # real run: keep the latest 20 of the chain
        self._run_cleaner(keep=20, dry_run=False)
        cur.execute("SELECT checkpoint_id FROM checkpoints WHERE thread_id = %s", (self.TID,))
        remaining = {r[0] for r in cur.fetchall()}
        assert len(remaining) == 20
        assert f"{self.ROOT}-0" not in remaining
        assert f"{self.ROOT}-24" in remaining  # leaf kept

        assert self._count(cur, "checkpoint_blobs") == 20  # unreferenced blobs purged

        cur.execute("SELECT task_id FROM checkpoint_writes WHERE thread_id = %s", (self.TID,))
        assert {r[0] for r in cur.fetchall()} == {"task-keep"}

    def test_orphan_thread_fully_removed(self):
        cur = self._conn.cursor()
        self._insert_chain(cur, 3)
        # our thread is intentionally NOT registered in threads_meta → orphan
        self._run_cleaner(keep=20, dry_run=False)
        assert self._count(cur, "checkpoints") == 0
        assert self._count(cur, "checkpoint_blobs") == 0
        assert self._count(cur, "checkpoint_writes") == 0
