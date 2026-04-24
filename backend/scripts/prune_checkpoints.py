"""Prune old checkpoints from the SQLite checkpointer database.

Keeps the latest N checkpoints per (thread_id, checkpoint_ns) chain and deletes
everything else, then VACUUMs to reclaim disk space.

Also optionally deletes entire threads not tracked in the store (orphaned).

Usage:
    # Dry run - show what would be deleted (keeps latest 1 by default)
    cd backend && python scripts/prune_checkpoints.py --dry-run

    # Keep latest 3 checkpoints per chain
    cd backend && python scripts/prune_checkpoints.py --keep 3

    # Keep latest 10 per chain, also remove orphaned threads
    cd backend && python scripts/prune_checkpoints.py --keep 10 --remove-orphans

    # Nuclear: delete ALL checkpoints and writes
    cd backend && python scripts/prune_checkpoints.py --all

    # Specify a custom database path
    cd backend && python scripts/prune_checkpoints.py --db /path/to/checkpoints.db
"""

from __future__ import annotations

import argparse
import collections
import os
import sqlite3
import sys


def get_db_path(args_db: str | None = None) -> str:
    if args_db:
        return args_db
    default = os.path.expanduser("~/.deer-flow/checkpoints.db")
    if os.path.exists(default):
        return default
    local = os.path.join(os.path.dirname(__file__), "..", ".deer-flow", "checkpoints.db")
    local = os.path.abspath(local)
    if os.path.exists(local):
        return local
    print(f"Database not found at {default} or {local}", file=sys.stderr)
    sys.exit(1)


def get_store_threads(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT key FROM store WHERE prefix = 'threads'")
    return {row[0] for row in cur.fetchall()}


def get_all_thread_ids(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT DISTINCT thread_id FROM checkpoints")
    return {row[0] for row in cur.fetchall()}


def find_keep_checkpoint_ids(conn: sqlite3.Connection, keep: int) -> set[str]:
    """Find checkpoint_ids to KEEP: the latest `keep` in each (thread_id, checkpoint_ns) chain.

    Checkpoints form a tree via parent_checkpoint_id. Starting from each leaf (node
    with no children), we walk back `keep` steps along the parent chain.
    """
    # Build parent -> children map to find leaves (nodes with no children)
    all_rows = conn.execute(
        "SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id FROM checkpoints"
    ).fetchall()

    has_child: set[str] = set()
    id_to_parent: dict[str, str | None] = {}
    groups: dict[tuple[str, str], list[str]] = collections.defaultdict(list)

    for thread_id, ns, cp_id, parent_id in all_rows:
        id_to_parent[cp_id] = parent_id
        groups[(thread_id, ns)].append(cp_id)
        if parent_id:
            has_child.add(parent_id)

    keep_ids: set[str] = set()
    for (thread_id, ns), cp_ids in groups.items():
        # Find leaves in this group
        leaves = [cp_id for cp_id in cp_ids if cp_id not in has_child]
        if not leaves:
            continue

        # Walk back from each leaf, collecting `keep` ancestors
        chain: set[str] = set()
        for leaf in leaves:
            current: str | None = leaf
            steps = 0
            while current and steps < keep:
                chain.add(current)
                current = id_to_parent.get(current)
                steps += 1
        keep_ids.update(chain)

    return keep_ids


def get_stats(conn: sqlite3.Connection) -> dict:
    cp_count = conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    wr_count = conn.execute("SELECT COUNT(*) FROM writes").fetchone()[0]
    cp_size = conn.execute("SELECT COALESCE(SUM(LENGTH(checkpoint)), 0) FROM checkpoints").fetchone()[0]
    wr_size = conn.execute("SELECT COALESCE(SUM(LENGTH(value)), 0) FROM writes").fetchone()[0]
    thread_count = conn.execute("SELECT COUNT(DISTINCT thread_id) FROM checkpoints").fetchone()[0]
    return {
        "checkpoints": cp_count,
        "writes": wr_count,
        "checkpoint_bytes": cp_size,
        "write_bytes": wr_size,
        "threads": thread_count,
    }


def fmt_size(n: int) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GB"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def _chunked_not_in_delete(conn: sqlite3.Connection, table: str, id_col: str, keep_ids: set[str]) -> int:
    """DELETE rows from table whose id_col is NOT in keep_ids. Uses temp table for correctness."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _keep_ids (id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM _keep_ids")
    conn.executemany("INSERT OR IGNORE INTO _keep_ids VALUES (?)", [(cid,) for cid in keep_ids])
    conn.execute(f"DELETE FROM {table} WHERE {id_col} NOT IN (SELECT id FROM _keep_ids)")
    return conn.execute("SELECT changes()").fetchone()[0]


def _measure_not_in(conn: sqlite3.Connection, table: str, id_col: str, size_col: str, keep_ids: set[str]) -> tuple[int, int]:
    """Count rows and total size of rows NOT in keep_ids, using temp table."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _keep_ids (id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM _keep_ids")
    conn.executemany("INSERT OR IGNORE INTO _keep_ids VALUES (?)", [(cid,) for cid in keep_ids])
    r = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(LENGTH({size_col})), 0) FROM {table} WHERE {id_col} NOT IN (SELECT id FROM _keep_ids)"
    ).fetchone()
    return r[0], r[1]


def prune_old_checkpoints(conn: sqlite3.Connection, *, keep: int = 1, dry_run: bool = False) -> int:
    """Delete old checkpoints, keeping the latest `keep` per chain."""
    print(f"Finding latest {keep} checkpoint(s) per (thread, namespace) chain...")
    keep_ids = find_keep_checkpoint_ids(conn, keep)
    print(f"Keeping {len(keep_ids)} checkpoints")

    if dry_run:
        cp_count, cp_size = _measure_not_in(conn, "checkpoints", "checkpoint_id", "checkpoint", keep_ids)
        wr_count, wr_size = _measure_not_in(conn, "writes", "checkpoint_id", "value", keep_ids)
        freed = cp_size + wr_size
        print(f"Would delete {cp_count} checkpoints ({fmt_size(cp_size)}) and {wr_count} writes ({fmt_size(wr_size)})")
        print(f"Total space to reclaim: {fmt_size(freed)}")
        return freed

    _chunked_not_in_delete(conn, "writes", "checkpoint_id", keep_ids)
    _chunked_not_in_delete(conn, "checkpoints", "checkpoint_id", keep_ids)
    conn.execute("DROP TABLE IF EXISTS _keep_ids")
    print("Deleted old checkpoints.")
    return 0


def remove_orphan_threads(
    conn: sqlite3.Connection, store_threads: set[str], *, dry_run: bool = False
) -> int:
    """Remove threads not tracked in the store."""
    all_threads = get_all_thread_ids(conn)
    orphaned = all_threads - store_threads
    if not orphaned:
        print("No orphaned threads found.")
        return 0

    ph = ",".join(["?"] * len(orphaned))
    orphaned_list = list(orphaned)
    cp_size = conn.execute(
        f"SELECT COALESCE(SUM(LENGTH(checkpoint)), 0) FROM checkpoints WHERE thread_id IN ({ph})",
        orphaned_list,
    ).fetchone()[0]
    wr_size = conn.execute(
        f"SELECT COALESCE(SUM(LENGTH(value)), 0) FROM writes WHERE thread_id IN ({ph})",
        orphaned_list,
    ).fetchone()[0]

    print(f"Orphaned threads: {len(orphaned)} ({fmt_size(cp_size + wr_size)})")

    if dry_run:
        print(f"Would delete {len(orphaned)} orphaned threads ({fmt_size(cp_size + wr_size)})")
        return cp_size + wr_size

    conn.execute(f"DELETE FROM writes WHERE thread_id IN ({ph})", orphaned_list)
    conn.execute(f"DELETE FROM checkpoints WHERE thread_id IN ({ph})", orphaned_list)
    print(f"Deleted {len(orphaned)} orphaned threads ({fmt_size(cp_size + wr_size)})")
    return cp_size + wr_size


def remove_all_threads(conn: sqlite3.Connection, *, dry_run: bool = False) -> int:
    """Delete ALL checkpoints and writes (keeps store metadata)."""
    stats = get_stats(conn)
    total = stats["checkpoint_bytes"] + stats["write_bytes"]
    print(f"Would delete ALL {stats['checkpoints']} checkpoints and {stats['writes']} writes ({fmt_size(total)})")
    if dry_run:
        return total

    conn.execute("DELETE FROM writes")
    conn.execute("DELETE FROM checkpoints")
    print(f"Deleted all checkpoints and writes ({fmt_size(total)})")
    return total


def vacuum(conn: sqlite3.Connection) -> None:
    print("Running VACUUM to reclaim disk space (this may take a few minutes)...")
    conn.execute("VACUUM")
    print("VACUUM complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune old checkpoints from the database")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")
    parser.add_argument("--keep", type=int, default=1, help="Number of latest checkpoints to keep per chain (default: 1)")
    parser.add_argument("--remove-orphans", action="store_true", help="Also remove threads not in the store")
    parser.add_argument("--all", action="store_true", help="Delete ALL checkpoints and writes (nuclear option)")
    parser.add_argument("--db", help="Path to checkpoints.db")
    parser.add_argument("--no-vacuum", action="store_true", help="Skip VACUUM after cleanup")
    args = parser.parse_args()

    db_path = get_db_path(args.db)
    file_size = os.path.getsize(db_path)
    print(f"Database: {db_path}")
    print(f"File size: {fmt_size(file_size)}")

    conn = sqlite3.connect(db_path)

    before = get_stats(conn)
    print(f"Before: {before['threads']} threads, {before['checkpoints']} checkpoints, {before['writes']} writes")
    print(f"Data size: {fmt_size(before['checkpoint_bytes'] + before['write_bytes'])}")
    print()

    if args.all:
        remove_all_threads(conn, dry_run=args.dry_run)
    else:
        prune_old_checkpoints(conn, keep=args.keep, dry_run=args.dry_run)
        if args.remove_orphans:
            print()
            store_threads = get_store_threads(conn)
            remove_orphan_threads(conn, store_threads, dry_run=args.dry_run)

    if not args.dry_run:
        conn.commit()
        if not args.no_vacuum:
            print()
            vacuum(conn)

    conn.close()

    if not args.dry_run:
        new_size = os.path.getsize(db_path)
        print(f"\nFile size: {fmt_size(file_size)} -> {fmt_size(new_size)} (saved {fmt_size(file_size - new_size)})")


if __name__ == "__main__":
    main()
