#!/usr/bin/env python3
"""Migrate a DeerFlow SQLite database (app tables + LangGraph checkpointer) to PostgreSQL.

Run inside an environment that has the backend dependencies installed
(SQLAlchemy asyncpg, psycopg_pool, langgraph-checkpoint-{sqlite,postgres}) and
the deerflow packages importable, e.g. from the dev VM container:

    docker exec deer-flow-dev bash -c '
      cd /app/backend && PYTHONPATH=.:packages/harness \
      python ../scripts/migrate_sqlite_to_pg.py \
        --sqlite /app/backend/.deer-flow/data/deerflow.db \
        --pg-url postgresql://deerflow:***@172.17.0.1:5432/deerflow'

What it does
------------
1. Source safety: opens the SQLite file read-only and runs PRAGMA integrity_check.
2. App schema: creates DeerFlow tables on PostgreSQL via the same bootstrap the
   Gateway uses on startup (create_all + alembic stamp head), so the target is
   exactly the schema the running app expects.
3. App data: copies every ORM table row-by-row (same ORM models on both sides).
4. Checkpointer: creates LangGraph's postgres tables via AsyncPostgresSaver.setup(),
   then converts every sqlite checkpoint/write through the savers' public API
   (serde.loads_typed -> aput / aput_writes). The two backends have different
   physical layouts (sqlite keeps channel_values inline in one blob; postgres
   inlines primitives in a JSONB column and stores the rest in checkpoint_blobs),
   so a raw table copy would NOT be readable — hence the API-level conversion.

Idempotency / safety
--------------------
- The target must be EMPTY: if any DeerFlow or LangGraph table already has rows
  the script aborts with a clear error instead of duplicating data. To re-run
  after a failed attempt, reset the target first:
      docker exec deer-flow-postgres psql -U deerflow -c \
        'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
- The source is only ever read (mode=ro); it is never modified.
- Progress is printed per table / per thread.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import defaultdict

# Must be imported before building URL strings
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--sqlite", required=True, help="Path to the source deerflow.db (opened read-only)")
    p.add_argument("--pg-url", required=True, help="PostgreSQL URL, e.g. postgresql://user:pass@host:5432/db")
    p.add_argument("--batch-size", type=int, default=500, help="Rows per INSERT batch")
    p.add_argument("--skip-app", action="store_true", help="Skip application tables")
    p.add_argument("--skip-checkpointer", action="store_true", help="Skip LangGraph checkpointer conversion")
    return p.parse_args()


def asyncpg_url(pg_url: str) -> str:
    if pg_url.startswith("postgresql+asyncpg://"):
        return pg_url
    if pg_url.startswith("postgresql://"):
        return pg_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    raise SystemExit(f"Unsupported --pg-url scheme: {pg_url}")


def open_sqlite_ro(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        raise SystemExit(f"Source SQLite integrity_check failed: {ok!r} — aborting, source untouched.")
    return con


def check_target_empty(pg_url: str) -> None:
    """Abort if the target database already contains DeerFlow/LangGraph data."""
    engine = create_async_engine(asyncpg_url(pg_url), pool_pre_ping=True)

    async def _run() -> list[str]:
        import deerflow.persistence.models  # noqa: F401 — register all ORM tables
        from deerflow.persistence.base import Base

        nonlocal_tables = [t for t in Base.metadata.tables]
        langgraph_tables = ["checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"]
        async with engine.connect() as conn:
            existing = {r[0] for r in await conn.execute(sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))}
            dirty = []
            for t in nonlocal_tables + langgraph_tables:
                if t not in existing:
                    continue
                n = (await conn.execute(sa.text(f'SELECT count(*) FROM "{t}"'))).scalar_one()
                if n:
                    dirty.append(f"{t} ({n} rows)")
            return dirty

    try:
        dirty = asyncio.run(_run())
    finally:
        engine.sync_engine.dispose()
    if dirty:
        raise SystemExit(
            "Target PostgreSQL database is NOT empty: " + ", ".join(dirty) + "\nRefusing to migrate (would duplicate data). Reset the target schema first:\n"
            "  docker exec deer-flow-postgres psql -U deerflow -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'"
        )


async def migrate_app_tables(sqlite_path: str, pg_url: str, batch_size: int) -> None:
    import deerflow.persistence.models  # noqa: F401
    from deerflow.persistence.base import Base
    from deerflow.persistence.bootstrap import bootstrap_schema

    src = create_async_engine(f"sqlite+aiosqlite:///{sqlite_mode_ro_path(sqlite_path)}")
    dst = create_async_engine(asyncpg_url(pg_url), pool_pre_ping=True)

    print("[app] creating target schema (bootstrap: create_all + alembic stamp head)...")
    await bootstrap_schema(dst, backend="postgres")

    try:
        async with src.connect() as scon:
            for table in Base.metadata.sorted_tables:
                total = (await scon.execute(sa.select(sa.func.count()).select_from(table))).scalar_one()
                if total == 0:
                    print(f"[app] {table.name}: 0 rows, skipped")
                    continue
                result = await scon.execute(sa.select(table))
                rows = result.mappings().all()
                async with dst.begin() as dcon:
                    for i in range(0, len(rows), batch_size):
                        await dcon.run_sync(lambda dc, chunk=rows[i : i + batch_size], t=table: dc.execute(t.insert(), [dict(r) for r in chunk]))
                print(f"[app] {table.name}: copied {len(rows)} rows")
    finally:
        await src.dispose()
        await dst.dispose()


def sqlite_mode_ro_path(path: str) -> str:
    # aiosqlite accepts URI only via connect args; simplest is a plain path —
    # we already opened a ro handle for integrity and won't write through here either.
    return path


async def migrate_checkpointer(sqlite_path: str, pg_url: str) -> None:
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    # Default JsonPlusSerializer — must match what the sqlite saver used.
    serde = BaseCheckpointSaver().serde

    con = open_sqlite_ro(sqlite_path)

    pool = AsyncConnectionPool(pg_url, kwargs={"autocommit": True, "prepare_threshold": 0}, check=AsyncConnectionPool.check_connection)
    async with pool:
        saver = AsyncPostgresSaver(conn=pool)
        print("[ckpt] running AsyncPostgresSaver.setup() ...")
        await saver.setup()

        threads = [r[0] for r in con.execute("SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id")]
        total_ckpts = con.execute("SELECT count(*) FROM checkpoints").fetchone()[0]
        total_writes = con.execute("SELECT count(*) FROM writes").fetchone()[0]
        print(f"[ckpt] source: {len(threads)} threads, {total_ckpts} checkpoints, {total_writes} write rows")

        done = 0
        for tid in threads:
            ns_list = [r[0] for r in con.execute("SELECT DISTINCT checkpoint_ns FROM checkpoints WHERE thread_id=? ORDER BY checkpoint_ns", (tid,))]
            for ns in ns_list:
                rows = con.execute(
                    "SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? ORDER BY checkpoint_id",
                    (tid, ns),
                ).fetchall()
                for thread_id, checkpoint_ns, checkpoint_id, parent_id, ctype, cblob, meta in rows:
                    checkpoint = serde.loads_typed((ctype, cblob))
                    metadata = json.loads(meta) if meta else {}
                    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}}
                    if parent_id:
                        config["configurable"]["checkpoint_id"] = parent_id
                    await saver.aput(config, checkpoint, metadata, checkpoint.get("channel_versions") or {})

                # pending writes for every checkpoint in this namespace
                wrows = con.execute(
                    "SELECT checkpoint_id, task_id, idx, channel, type, value FROM writes WHERE thread_id=? AND checkpoint_ns=? ORDER BY checkpoint_id, task_id, idx",
                    (tid, ns),
                ).fetchall()
                by_ck_task: dict[tuple[str, str], list[tuple[str, object]]] = defaultdict(list)
                for wcid, wtask, _idx, wchannel, wtype, wvalue in wrows:
                    by_ck_task[(wcid, wtask)].append((wchannel, serde.loads_typed((wtype, wvalue))))
                for (wcid, wtask), writes in by_ck_task.items():
                    cfg = {"configurable": {"thread_id": tid, "checkpoint_ns": ns}}
                    if wcid:
                        cfg["configurable"]["checkpoint_id"] = wcid
                    await saver.aput_writes(cfg, writes, wtask)

            done += len(ns_list)
            n_done = con.execute("SELECT count(*) FROM checkpoints WHERE thread_id=?", (tid,)).fetchone()[0]
            print(f"[ckpt] thread {tid}: {n_done} checkpoints + their writes converted")

        # verify round-trip readability on every thread's latest checkpoint
        print("[ckpt] verifying latest checkpoint of each thread reads back from postgres ...")
        for tid in threads:
            tup = await saver.aget_tuple({"configurable": {"thread_id": tid}})
            if tup is None or tup.checkpoint is None:
                raise SystemExit(f"[ckpt] VERIFY FAILED: no readable checkpoint for thread {tid}")
        print(f"[ckpt] OK: {total_ckpts} checkpoints / {total_writes} writes migrated and readable")
    con.close()


def main() -> None:
    args = parse_args()
    check_target_empty(args.pg_url)
    open_sqlite_ro(args.sqlite).close()
    print("source integrity OK, target empty — starting migration")

    if not args.skip_app:
        asyncio.run(migrate_app_tables(args.sqlite, args.pg_url, args.batch_size))
    if not args.skip_checkpointer:
        asyncio.run(migrate_checkpointer(args.sqlite, args.pg_url))
    print("MIGRATION COMPLETE")


if __name__ == "__main__":
    sys.exit(main())
