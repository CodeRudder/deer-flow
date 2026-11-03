"""Periodic checkpoint cleanup for SQLite- and Postgres-backed checkpointers.

Runs as a background ``threading.Timer`` task in the Gateway process.
SQLite uses the checkpointer's own ``aiosqlite`` connection to avoid lock
conflicts; Postgres talks to the saver's ``AsyncConnectionPool`` (``.pool``)
with the same keep-set semantics.

For each thread, keeps only the latest N checkpoints (walking the
parent-checkpoint chain from each leaf) and deletes the rest.

**Safety guarantees**:
- Leaf checkpoints (latest state of any branch) are NEVER deleted.
- The most recent checkpoint of each (thread, namespace) is always preserved.
- If the keep-set computation yields 0 IDs, cleanup aborts without touching data.
- Before deleting, cross-checks that no leaf checkpoint is in the delete-set.
- No persistent helper tables — all work is done in-memory with batched DELETE.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop

    from langgraph.types import Checkpointer

logger = logging.getLogger(__name__)


def _get_aiosqlite_conn(checkpointer: Checkpointer):
    """Extract the aiosqlite connection from an AsyncSqliteSaver."""
    return getattr(checkpointer, "conn", None)


def _get_postgres_pool(checkpointer: Checkpointer):
    """Extract the AsyncConnectionPool from an AsyncPostgresSaver.

    ``AsyncPostgresSaver`` stores its pool as ``conn`` (and some versions
    also expose ``pool``) — detect it structurally: a connection factory
    without a DB-API ``cursor`` method.
    """
    for attr in ("pool", "conn"):
        candidate = getattr(checkpointer, attr, None)
        if candidate is not None and hasattr(candidate, "connection") and not hasattr(candidate, "cursor"):
            return candidate
    return None


def compute_keep_sets(
    rows: list[tuple[str, str, str, str | None]],
    keep_n: int,
    *,
    leaf_policy: str = "all",
) -> tuple[set[str], set[str], set[str]]:
    """Pure keep-set computation shared by the SQLite and Postgres backends.

    Args:
        rows: ``(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id)``
            tuples from a single snapshot read of the checkpoints table.
        keep_n: Number of latest checkpoints to keep per (thread, namespace)
            chain, walking the parent chain back from each leaf.
        leaf_policy: ``"all"`` (default) protects every branch leaf — heavily
            branched threads therefore retain up to ``leaves × keep_n``
            checkpoints. ``"recent"`` walks only from each chain's newest
            leaf, pruned branches may become unreachable for time travel but
            heavily branched threads actually shrink (measured on dev: 41
            leaves made ``"all"`` retain 726/726 checkpoints; ``"recent"``
            marks 84 deletable).

    Returns:
        (all_ids, keep_ids, leaf_ids) — ``leaf_ids`` lists the leaves that
        were walked; under the default policy every leaf is preserved.
    """
    all_ids: set[str] = set()
    has_child: set[str] = set()
    id_to_parent: dict[str, str | None] = {}
    groups: dict[tuple[str, str], list[str]] = collections.defaultdict(list)

    for thread_id, ns, cp_id, parent_id in rows:
        all_ids.add(cp_id)
        id_to_parent[cp_id] = parent_id
        groups[(thread_id, ns)].append(cp_id)
        if parent_id:
            has_child.add(parent_id)

    keep_ids: set[str] = set()
    leaf_ids: set[str] = set()

    for (_, _), cp_ids in groups.items():
        leaves = [c for c in cp_ids if c not in has_child]
        leaf_ids.update(leaves)
        walked = leaves if leaf_policy == "all" else [max(leaves)]
        for leaf in walked:
            current = leaf
            steps = 0
            while current and steps < keep_n:
                keep_ids.add(current)
                current = id_to_parent.get(current)
                steps += 1

    if leaf_policy == "all":
        # Safety: ensure every leaf is always in keep set
        keep_ids |= leaf_ids
    return all_ids, keep_ids, leaf_ids


class CheckpointCleaner:
    """Periodic background cleaner for old checkpoints.

    Uses the checkpointer's own aiosqlite connection so there are no
    lock conflicts with the running Gateway process.

    Args:
        checkpointer: The async checkpointer instance (from app.state).
        keep: Number of latest checkpoints to keep per (thread, namespace) chain.
        interval: Seconds between cleanup cycles.
    """

    def __init__(
        self,
        checkpointer: Checkpointer,
        keep: int = 20,
        interval: int = 3600,
        dry_run: bool = False,
        leaf_policy: str = "all",
    ) -> None:
        self._checkpointer = checkpointer
        self._keep = max(keep, 1)  # must keep at least 1
        self._leaf_policy = leaf_policy if leaf_policy in ("all", "recent") else "all"
        self._interval = interval
        self._dry_run = dry_run
        self._timer: threading.Timer | None = None
        self._running = False
        self._loop: AbstractEventLoop | None = None

    async def run_once(self, *, dry_run: bool | None = None) -> None:
        """Run one cleanup cycle immediately (for one-shot / dry-run invocations)."""
        previous = self._dry_run
        if dry_run is not None:
            self._dry_run = dry_run
        try:
            await self._cleanup_all()
        finally:
            self._dry_run = previous

    # ── Lifecycle ──────────────────────────────────────────────────────

    def start(self, loop: AbstractEventLoop) -> None:
        self._running = True
        self._loop = loop
        self._schedule_next()
        logger.info(
            "Checkpoint cleaner started (keep=%d, interval=%ds)",
            self._keep,
            self._interval,
        )

    def stop(self) -> None:
        self._running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        logger.info("Checkpoint cleaner stopped")

    # ── Scheduling ─────────────────────────────────────────────────────

    def _schedule_next(self) -> None:
        if not self._running:
            return
        self._timer = threading.Timer(self._interval, self._clean_cycle)
        self._timer.daemon = True
        self._timer.start()

    def _clean_cycle(self) -> None:
        logger.info("Checkpoint cleaner cycle started")
        try:
            if self._loop and not self._loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(
                    self._cleanup_all(),
                    self._loop,
                )
                future.result(timeout=600)
        except Exception:
            logger.exception("Checkpoint cleaner cycle failed")
        self._schedule_next()

    # ── Main cleanup logic ─────────────────────────────────────────────

    async def _cleanup_all(self) -> None:
        # NOTE: AsyncPostgresSaver stores its pool as ``conn`` too, so the
        # Postgres check must come first.
        pool = _get_postgres_pool(self._checkpointer)
        if pool is not None:
            await self._cleanup_all_postgres(pool)
            return
        conn = _get_aiosqlite_conn(self._checkpointer)
        if conn is not None:
            await self._cleanup_all_sqlite(conn)
            return
        logger.debug("Checkpointer is neither SQLite nor Postgres, skipping cleanup")

    async def _cleanup_all_sqlite(self, conn) -> None:
        # Drop legacy helper table from previous (buggy) cleaner versions
        try:
            async with conn.cursor() as cur:
                await cur.execute("DROP TABLE IF EXISTS _cleaner_keep")
            await conn.commit()
        except Exception:
            logger.debug("Failed to drop legacy _cleaner_keep table", exc_info=True)

        before = await self._db_size(conn)

        # 1. Find which checkpoint IDs to keep (single DB snapshot)
        all_ids, keep_ids, leaf_ids = await self._find_keep_ids(conn)

        # Safety: abort if keep set is empty (something went wrong)
        if not keep_ids:
            logger.warning("Keep set is empty — aborting cleanup to prevent data loss")
            return

        # 2. Find and delete orphaned threads
        orphan_deleted = await self._delete_orphan_threads(conn)

        # 3. Delete old checkpoints and writes (not in keep set)
        cp_deleted, cp_freed, wr_deleted, wr_freed = await self._delete_old(conn, all_ids, keep_ids, leaf_ids)

        # 4. Run checkpoint to flush WAL
        try:
            async with conn.cursor() as cur:
                await cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            logger.debug("WAL checkpoint failed (non-critical)", exc_info=True)

        after = await self._db_size(conn)
        logger.info(
            "Checkpoint cleaner: deleted %d checkpoints (%.0f MB) + %d writes (%.0f MB) + %d orphan threads, DB %.1f -> %.1f GB",
            cp_deleted,
            cp_freed / 1024**2,
            wr_deleted,
            wr_freed / 1024**2,
            orphan_deleted,
            before / 1024**3,
            after / 1024**3,
        )

    async def _db_size(self, conn) -> float:
        """Get the DB file size via page_count * page_size."""
        async with conn.cursor() as cur:
            await cur.execute("PRAGMA page_count")
            pages = (await cur.fetchone())[0]
            await cur.execute("PRAGMA page_size")
            page_size = (await cur.fetchone())[0]
        return pages * page_size

    async def _find_keep_ids(self, conn) -> tuple[set[str], set[str], set[str]]:
        """Walk the parent chain from each leaf, keep the latest N.

        Returns:
            (all_ids, keep_ids, leaf_ids) — snapshot from a single DB read.
            - all_ids: every checkpoint_id in the DB at read time
            - keep_ids: IDs to preserve (includes all leaves)
            - leaf_ids: leaf nodes that must never be deleted

        All three sets come from the same SELECT, so no race with new checkpoints.
        """
        async with conn.cursor() as cur:
            await cur.execute("SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id FROM checkpoints")
            rows = await cur.fetchall()

        rows = [(thread_id, ns, cp_id, parent_id) for thread_id, ns, cp_id, parent_id in rows]
        all_ids, keep_ids, leaf_ids = compute_keep_sets(rows, self._keep, leaf_policy=self._leaf_policy)

    async def _delete_orphan_threads(self, conn) -> int:
        """Delete checkpoints/writes for threads not in the store table."""
        async with conn.cursor() as cur:
            await cur.execute("SELECT key FROM store WHERE prefix = 'threads'")
            store_threads = {r[0] for r in await cur.fetchall()}

        if not store_threads:
            return 0

        async with conn.cursor() as cur:
            await cur.execute("SELECT DISTINCT thread_id FROM checkpoints")
            all_threads = {r[0] for r in await cur.fetchall()}

        orphaned = all_threads - store_threads
        if not orphaned:
            return 0

        deleted = 0
        for tid in orphaned:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM writes WHERE thread_id = ?", (tid,))
                await cur.execute("DELETE FROM checkpoints WHERE thread_id = ?", (tid,))
            deleted += 1

        await conn.commit()
        logger.info("Deleted %d orphaned threads", deleted)
        return deleted

    async def _delete_old(self, conn, all_ids: set[str], keep_ids: set[str], leaf_ids: set[str]) -> tuple[int, int, int, int]:
        """Delete checkpoints and writes not in keep_ids. Returns (cp_del, cp_bytes, wr_del, wr_bytes).

        Uses batched parameterized DELETE queries — no helper tables.
        ``all_ids`` is the snapshot from _find_keep_ids (no re-read from DB),
        so new checkpoints created between snapshot and delete are safe.
        Cross-checks that no leaf checkpoint is in the delete-set.
        """
        # delete_ids computed from the same snapshot as keep_ids/leaf_ids
        delete_ids = all_ids - keep_ids

        # Safety: if any leaf would be deleted, abort
        if delete_ids & leaf_ids:
            logger.error(
                "SAFETY ABORT: %d leaf checkpoints would be deleted — skipping cleanup",
                len(delete_ids & leaf_ids),
            )
            return 0, 0, 0, 0

        if not delete_ids:
            logger.info("No old checkpoints to delete")
            return 0, 0, 0, 0

        # Count what will be deleted
        delete_list = list(delete_ids)
        cp_count = 0
        cp_bytes = 0
        wr_count = 0
        wr_bytes = 0

        # Count in batches
        batch_size = 500
        for i in range(0, len(delete_list), batch_size):
            batch = delete_list[i : i + batch_size]
            ph = ",".join(["?"] * len(batch))
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(LENGTH(checkpoint)), 0) FROM checkpoints WHERE checkpoint_id IN ({ph})",
                    batch,
                )
                row = await cur.fetchone()
                cp_count += row[0]
                cp_bytes += row[1]

                await cur.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(LENGTH(value)), 0) FROM writes WHERE checkpoint_id IN ({ph})",
                    batch,
                )
                row = await cur.fetchone()
                wr_count += row[0]
                wr_bytes += row[1]

        # Delete writes first, then checkpoints — batched
        for i in range(0, len(delete_list), batch_size):
            batch = delete_list[i : i + batch_size]
            ph = ",".join(["?"] * len(batch))
            async with conn.cursor() as cur:
                await cur.execute(f"DELETE FROM writes WHERE checkpoint_id IN ({ph})", batch)
                await cur.execute(f"DELETE FROM checkpoints WHERE checkpoint_id IN ({ph})", batch)
            await conn.commit()

        return cp_count, cp_bytes, wr_count, wr_bytes

    # ── Postgres backend ────────────────────────────────────────────────

    async def _cleanup_all_postgres(self, pool) -> None:
        """Cleanup cycle against the Postgres checkpointer tables.

        Uses the saver's own ``AsyncConnectionPool`` (autocommit connections),
        so there are no lock conflicts with the running Gateway. Semantics
        match the SQLite path: keep the latest N checkpoints per
        (thread, namespace) chain plus every leaf; delete the rest together
        with their pending writes; purge checkpoint_blobs whose
        (channel, version) is no longer referenced by any kept checkpoint.
        """
        async with pool.connection() as conn:
            before = await self._pg_db_size(conn)

            all_ids, keep_ids, leaf_ids = await self._pg_find_keep_ids(conn)
            if not keep_ids:
                logger.warning("Keep set is empty — aborting cleanup to prevent data loss")
                return

            delete_ids = all_ids - keep_ids
            if delete_ids & leaf_ids:
                logger.error(
                    "SAFETY ABORT: %d leaf checkpoints would be deleted — skipping cleanup",
                    len(delete_ids & leaf_ids),
                )
                return

            orphan_deleted = await self._pg_delete_orphan_threads(conn, keep_ids)

            cp_deleted, cp_bytes, wr_deleted, wr_bytes, blob_deleted, blob_bytes = await self._pg_delete_old(conn, all_ids, keep_ids, leaf_ids)
            after = await self._pg_db_size(conn)

        logger.info(
            "Checkpoint cleaner (postgres)%s: deleted %d checkpoints (%.0f MB) + %d writes (%.0f MB) + %d blobs (%.0f MB) + %d orphan threads; DB %.2f -> %.2f GB",
            " [DRY RUN]" if self._dry_run else "",
            cp_deleted,
            cp_bytes / 1024**2,
            wr_deleted,
            wr_bytes / 1024**2,
            blob_deleted,
            blob_bytes / 1024**2,
            orphan_deleted,
            before / 1024**3,
            after / 1024**3,
        )

    @staticmethod
    def _row(row: Any, keys: list[str]) -> dict:
        """Normalize a psycopg row (dict with dict_row factory, or a tuple)."""
        if isinstance(row, dict):
            return row
        return dict(zip(keys, row))

    @staticmethod
    def _rows(rows: list, keys: list[str]) -> list[dict]:
        return [CheckpointCleaner._row(r, keys) for r in rows]

    async def _pg_find_keep_ids(self, conn) -> tuple[set[str], set[str], set[str]]:
        """Snapshot the checkpoints table and compute the keep set."""
        async with conn.cursor() as cur:
            await cur.execute("SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id FROM checkpoints")
            rows = await cur.fetchall()

        normalized = [(r["thread_id"], r["checkpoint_ns"], r["checkpoint_id"], r["parent_checkpoint_id"]) for r in self._rows(rows, ["thread_id", "checkpoint_ns", "checkpoint_id", "parent_checkpoint_id"])]
        all_ids, keep_ids, leaf_ids = compute_keep_sets(normalized, self._keep, leaf_policy=self._leaf_policy)
        logger.info(
            "Keep computation (postgres): %d total checkpoints, %d leaf nodes, %d to keep%s",
            len(all_ids),
            len(leaf_ids),
            len(keep_ids),
            " [DRY RUN]" if self._dry_run else "",
        )
        return all_ids, keep_ids, leaf_ids

    async def _pg_delete_orphan_threads(self, conn, keep_ids: set[str]) -> int:
        """Delete checkpointer rows for threads absent from ``threads_meta``.

        DeerFlow stores thread records in the app-owned ``threads_meta`` table
        (not LangGraph's legacy ``store``). Orphan deletion is skipped when the
        table does not exist. Never touches checkpoints in the keep set.
        """
        async with conn.cursor() as cur:
            await cur.execute("SELECT to_regclass('public.threads_meta') IS NOT NULL AS present")
            row = await cur.fetchone()
            row = self._row(row, ["present"]) if row else {}
            if not row.get("present", row.get("?column?", False)):
                logger.debug("threads_meta table not present — skipping orphan cleanup")
                return 0
            await cur.execute("SELECT thread_id FROM threads_meta")
            meta_rows = await cur.fetchall()
        meta_ids = {r["thread_id"] for r in self._rows(meta_rows, ["thread_id"])}

        async with conn.cursor() as cur:
            await cur.execute("SELECT DISTINCT thread_id FROM checkpoints")
            cp_rows = await cur.fetchall()
        checkpoint_threads = {r["thread_id"] for r in self._rows(cp_rows, ["thread_id"])}
        orphans = checkpoint_threads - meta_ids
        if not orphans:
            return 0

        # Safety: threads whose latest checkpoints were just kept are live.
        async with conn.cursor() as cur:
            deleted = 0
            for tid in orphans:
                if self._dry_run:
                    deleted += 1
                    continue
                await cur.execute("DELETE FROM checkpoint_writes WHERE thread_id = %s", (tid,))
                await cur.execute("DELETE FROM checkpoint_blobs WHERE thread_id = %s", (tid,))
                await cur.execute("DELETE FROM checkpoints WHERE thread_id = %s", (tid,))
                deleted += 1
        return deleted

    async def _pg_delete_old(self, conn, all_ids: set[str], keep_ids: set[str], leaf_ids: set[str]) -> tuple[int, int, int, int, int, int]:
        """Delete non-kept checkpoints + their writes + unreferenced blobs.

        Returns (cp_del, cp_bytes, wr_del, wr_bytes, blob_del, blob_bytes).
        With ``dry_run`` enabled nothing is deleted — only counts are reported.
        """
        delete_ids = all_ids - keep_ids
        if not delete_ids:
            return 0, 0, 0, 0, 0, 0
        if delete_ids & leaf_ids:  # defensive re-check
            return 0, 0, 0, 0, 0, 0

        delete_list = list(delete_ids)
        cp_count = cp_bytes = wr_count = wr_bytes = 0
        batch_size = 500
        async with conn.cursor() as cur:
            for i in range(0, len(delete_list), batch_size):
                batch = delete_list[i : i + batch_size]
                await cur.execute(
                    "SELECT COUNT(*) AS n, COALESCE(SUM(length(checkpoint::text)), 0) AS b FROM checkpoints WHERE checkpoint_id = ANY(%s)",
                    (batch,),
                )
                row = self._row(await cur.fetchone() or {"n": 0, "b": 0}, ["n", "b"])
                cp_count += int(row["n"] or 0)
                cp_bytes += int(row["b"] or 0)
                await cur.execute(
                    "SELECT COUNT(*) AS n, COALESCE(SUM(length(blob)), 0) AS b FROM checkpoint_writes WHERE checkpoint_id = ANY(%s)",
                    (batch,),
                )
                row = self._row(await cur.fetchone() or {"n": 0, "b": 0}, ["n", "b"])
                wr_count += int(row["n"] or 0)
                wr_bytes += int(row["b"] or 0)

        # Blobs: purge (thread, ns, channel, version) rows no longer referenced
        # by any kept checkpoint's channel_versions.
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT thread_id, checkpoint_ns, checkpoint FROM checkpoints WHERE checkpoint_id = ANY(%s)",
                (list(keep_ids),),
            )
            kept_rows = self._rows(await cur.fetchall(), ["thread_id", "checkpoint_ns", "checkpoint"])
        referenced: set[tuple[str, str, str, str]] = set()
        for r in kept_rows:
            checkpoint = r["checkpoint"]
            versions = checkpoint.get("channel_versions", {}) if isinstance(checkpoint, dict) else {}
            for channel, version in (versions or {}).items():
                referenced.add((r["thread_id"], r["checkpoint_ns"], channel, version))

        blob_del = blob_bytes = 0
        stale: list[tuple[str, str, str, str]] = []
        async with conn.cursor() as cur:
            await cur.execute("SELECT thread_id, checkpoint_ns, channel, version, length(blob) AS b FROM checkpoint_blobs")
            for r in self._rows(await cur.fetchall(), ["thread_id", "checkpoint_ns", "channel", "version", "b"]):
                key = (r["thread_id"], r["checkpoint_ns"], r["channel"], r["version"])
                if key not in referenced:
                    stale.append(key)
                    blob_bytes += int(r["b"] or 0)
        blob_del = len(stale)

        if self._dry_run:
            return cp_count, cp_bytes, wr_count, wr_bytes, blob_del, blob_bytes

        async with conn.cursor() as cur:
            for i in range(0, len(delete_list), batch_size):
                batch = delete_list[i : i + batch_size]
                await cur.execute("DELETE FROM checkpoint_writes WHERE checkpoint_id = ANY(%s)", (batch,))
                await cur.execute("DELETE FROM checkpoints WHERE checkpoint_id = ANY(%s)", (batch,))
            # per-key delete: the blob table is modest and this avoids
            # fragile array-join SQL; each statement is a PK delete.
            for thread_id, ns, channel, version in stale:
                await cur.execute(
                    "DELETE FROM checkpoint_blobs WHERE thread_id = %s AND checkpoint_ns = %s AND channel = %s AND version = %s",
                    (thread_id, ns, channel, version),
                )
        return cp_count, cp_bytes, wr_count, wr_bytes, blob_del, blob_bytes

    async def _pg_db_size(self, conn) -> float:
        """Current database size in bytes (used for before/after logging)."""
        async with conn.cursor() as cur:
            await cur.execute("SELECT pg_database_size(current_database()) AS sz")
            row = self._row(await cur.fetchone() or {"sz": 0}, ["sz"])
        return float(row["sz"])
