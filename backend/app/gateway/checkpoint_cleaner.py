"""Periodic checkpoint cleanup for SQLite-backed checkpointer.

Runs as a background ``threading.Timer`` task in the Gateway process.
Uses the checkpointer's own ``aiosqlite`` connection to avoid lock conflicts.

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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop

    from langgraph.types import Checkpointer

logger = logging.getLogger(__name__)


def _get_aiosqlite_conn(checkpointer: Checkpointer):
    """Extract the aiosqlite connection from an AsyncSqliteSaver."""
    return getattr(checkpointer, "conn", None)


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
        keep: int = 50,
        interval: int = 3600,
    ) -> None:
        self._checkpointer = checkpointer
        self._keep = max(keep, 1)  # must keep at least 1
        self._interval = interval
        self._timer: threading.Timer | None = None
        self._running = False
        self._loop: AbstractEventLoop | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────

    def start(self, loop: AbstractEventLoop) -> None:
        self._running = True
        self._loop = loop
        self._schedule_next()
        logger.info(
            "Checkpoint cleaner started (keep=%d, interval=%ds)",
            self._keep, self._interval,
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
                    self._cleanup_all(), self._loop,
                )
                future.result(timeout=600)
        except Exception:
            logger.exception("Checkpoint cleaner cycle failed")
        self._schedule_next()

    # ── Main cleanup logic ─────────────────────────────────────────────

    async def _cleanup_all(self) -> None:
        conn = _get_aiosqlite_conn(self._checkpointer)
        if conn is None:
            logger.debug("Checkpointer is not SQLite, skipping cleanup")
            return

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
            logger.warning(
                "Keep set is empty — aborting cleanup to prevent data loss"
            )
            return

        # 2. Find and delete orphaned threads
        orphan_deleted = await self._delete_orphan_threads(conn)

        # 3. Delete old checkpoints and writes (not in keep set)
        cp_deleted, cp_freed, wr_deleted, wr_freed = await self._delete_old(
            conn, all_ids, keep_ids, leaf_ids
        )

        # 4. Run checkpoint to flush WAL
        try:
            async with conn.cursor() as cur:
                await cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            logger.debug("WAL checkpoint failed (non-critical)", exc_info=True)

        after = await self._db_size(conn)
        logger.info(
            "Checkpoint cleaner: deleted %d checkpoints (%.0f MB) + %d writes (%.0f MB) + %d orphan threads, "
            "DB %.1f -> %.1f GB",
            cp_deleted, cp_freed / 1024**2,
            wr_deleted, wr_freed / 1024**2,
            orphan_deleted,
            before / 1024**3, after / 1024**3,
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
            await cur.execute(
                "SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id FROM checkpoints"
            )
            rows = await cur.fetchall()

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
            for leaf in leaves:
                leaf_ids.add(leaf)
                current = leaf
                steps = 0
                while current and steps < self._keep:
                    keep_ids.add(current)
                    current = id_to_parent.get(current)
                    steps += 1

        # Safety: ensure every leaf is always in keep set
        keep_ids |= leaf_ids

        logger.info(
            "Keep computation: %d total checkpoints, %d leaf nodes, %d to keep",
            len(all_ids), len(leaf_ids), len(keep_ids),
        )
        return all_ids, keep_ids, leaf_ids

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

    async def _delete_old(
        self, conn, all_ids: set[str], keep_ids: set[str], leaf_ids: set[str]
    ) -> tuple[int, int, int, int]:
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
                    f"SELECT COUNT(*), COALESCE(SUM(LENGTH(checkpoint)), 0) "
                    f"FROM checkpoints WHERE checkpoint_id IN ({ph})",
                    batch,
                )
                row = await cur.fetchone()
                cp_count += row[0]
                cp_bytes += row[1]

                await cur.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(LENGTH(value)), 0) "
                    f"FROM writes WHERE checkpoint_id IN ({ph})",
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
                await cur.execute(
                    f"DELETE FROM writes WHERE checkpoint_id IN ({ph})", batch
                )
                await cur.execute(
                    f"DELETE FROM checkpoints WHERE checkpoint_id IN ({ph})", batch
                )
            await conn.commit()

        return cp_count, cp_bytes, wr_count, wr_bytes
