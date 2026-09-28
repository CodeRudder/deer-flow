#!/usr/bin/env python3
"""Offline migration from DeerFlow SQLite persistence to PostgreSQL.

This migrates LangGraph checkpoints and DeerFlow's lightweight thread Store
records. It intentionally does not move thread-local files such as
``conversation.jsonl`` or artifacts under ``.deer-flow/threads``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from langgraph.store.sqlite.aio import AsyncSqliteStore

from deerflow.config.app_config import AppConfig
from deerflow.runtime.store._sqlite_utils import resolve_sqlite_conn_str

THREADS_NS = ("threads",)


def _repo_backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate DeerFlow SQLite checkpoints/store records to PostgreSQL.",
    )
    parser.add_argument(
        "--sqlite",
        default="checkpoints.db",
        help=("Source SQLite DB path. Relative paths are resolved like DeerFlow config paths. Default: checkpoints.db"),
    )
    parser.add_argument(
        "--postgres-dsn",
        default=None,
        help=("Destination PostgreSQL DSN. Defaults to config.yaml checkpointer.connection_string after env var resolution."),
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml used to resolve the PostgreSQL DSN.",
    )
    parser.add_argument(
        "--thread-id",
        action="append",
        default=[],
        help="Only migrate this thread ID. Can be provided multiple times.",
    )
    parser.add_argument(
        "--skip-store",
        action="store_true",
        help="Skip migrating DeerFlow Store records.",
    )
    parser.add_argument(
        "--skip-checkpoints",
        action="store_true",
        help="Skip migrating checkpoints.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Delete destination thread checkpoints before writing them.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only count what would be migrated.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of checkpoints to migrate, for testing.",
    )
    return parser.parse_args()


def _load_postgres_dsn(config_path: str | None) -> str:
    config = AppConfig.from_file(config_path)
    if config.checkpointer is None:
        raise SystemExit("config.yaml does not define a checkpointer section.")
    if config.checkpointer.type != "postgres":
        raise SystemExit(
            "config.yaml checkpointer.type is not 'postgres'. Pass --postgres-dsn explicitly if you still want to migrate.",
        )
    if not config.checkpointer.connection_string:
        raise SystemExit("config.yaml checkpointer.connection_string is empty.")
    return config.checkpointer.connection_string


def _resolve_sqlite_path(raw: str) -> str:
    # Match existing provider behavior for source paths.
    conn_str = resolve_sqlite_conn_str(raw)
    if conn_str == ":memory:" or conn_str.startswith("file:"):
        return conn_str
    return str(Path(conn_str).resolve())


def _checkpoint_thread_id(checkpoint_tuple: Any) -> str:
    return str(checkpoint_tuple.config["configurable"]["thread_id"])


def _checkpoint_id(checkpoint_tuple: Any) -> str:
    return str(checkpoint_tuple.config["configurable"]["checkpoint_id"])


def _config_for_parent(checkpoint_tuple: Any) -> dict[str, Any]:
    """Return config whose checkpoint_id points at the parent checkpoint.

    LangGraph saver APIs store the parent checkpoint id from the input config,
    while the checkpoint payload itself carries the current checkpoint id.
    """
    configurable = checkpoint_tuple.config["configurable"].copy()
    if checkpoint_tuple.parent_config is not None:
        parent_id = checkpoint_tuple.parent_config["configurable"].get("checkpoint_id")
        if parent_id:
            configurable["checkpoint_id"] = parent_id
        else:
            configurable.pop("checkpoint_id", None)
    else:
        configurable.pop("checkpoint_id", None)
    return {"configurable": configurable}


async def _collect_checkpoint_counts(
    sqlite_cp: AsyncSqliteSaver,
    selected_threads: set[str],
    limit: int | None,
) -> tuple[int, set[str]]:
    count = 0
    threads: set[str] = set()
    async for checkpoint_tuple in sqlite_cp.alist(None, limit=limit):
        thread_id = _checkpoint_thread_id(checkpoint_tuple)
        if selected_threads and thread_id not in selected_threads:
            continue
        count += 1
        threads.add(thread_id)
    return count, threads


async def _migrate_checkpoints(
    sqlite_cp: AsyncSqliteSaver,
    postgres_cp: AsyncPostgresSaver,
    *,
    selected_threads: set[str],
    overwrite: bool,
    dry_run: bool,
    limit: int | None,
) -> tuple[int, int, int]:
    """Migrate checkpoints.

    Returns ``(written, skipped, threads_seen)``.
    """
    count, threads = await _collect_checkpoint_counts(sqlite_cp, selected_threads, limit)
    if dry_run:
        print(f"dry-run: checkpoints matched={count}, threads={len(threads)}")
        return 0, count, len(threads)

    if overwrite:
        for thread_id in sorted(threads):
            await postgres_cp.adelete_thread(thread_id)
            print(f"deleted destination checkpoints for thread {thread_id}")

    by_thread: dict[str, list[Any]] = defaultdict(list)
    async for checkpoint_tuple in sqlite_cp.alist(None, limit=limit):
        thread_id = _checkpoint_thread_id(checkpoint_tuple)
        if selected_threads and thread_id not in selected_threads:
            continue
        by_thread[thread_id].append(checkpoint_tuple)

    written = 0
    skipped = 0
    for thread_id in sorted(by_thread):
        checkpoints = by_thread[thread_id]
        # Source lists newest first. Write oldest first so parent links resolve
        # naturally and the resulting history remains coherent.
        for checkpoint_tuple in reversed(checkpoints):
            current_config = checkpoint_tuple.config
            existing = await postgres_cp.aget_tuple(current_config)
            if existing is not None and not overwrite:
                skipped += 1
                continue

            await postgres_cp.aput(
                _config_for_parent(checkpoint_tuple),
                checkpoint_tuple.checkpoint,
                checkpoint_tuple.metadata,
                checkpoint_tuple.checkpoint.get("channel_versions", {}),
            )

            if checkpoint_tuple.pending_writes:
                writes_by_task: dict[str, list[tuple[str, Any]]] = defaultdict(list)
                for task_id, channel, value in checkpoint_tuple.pending_writes:
                    writes_by_task[str(task_id)].append((channel, value))
                for task_id, writes in writes_by_task.items():
                    await postgres_cp.aput_writes(current_config, writes, task_id)

            written += 1

        print(f"checkpoints: thread={thread_id} total={len(checkpoints)} written_so_far={written} skipped_so_far={skipped}")

    return written, skipped, len(by_thread)


async def _migrate_store(
    sqlite_store: AsyncSqliteStore,
    postgres_store: AsyncPostgresStore,
    *,
    selected_threads: set[str],
    dry_run: bool,
) -> tuple[int, int]:
    namespaces = await sqlite_store.alist_namespaces(limit=1000)
    matched = 0
    written = 0
    for namespace in namespaces:
        offset = 0
        while True:
            items = await sqlite_store.asearch(namespace, limit=100, offset=offset)
            if not items:
                break
            offset += len(items)
            for item in items:
                if namespace == THREADS_NS and selected_threads and item.key not in selected_threads:
                    continue
                matched += 1
                if dry_run:
                    continue
                await postgres_store.aput(item.namespace, item.key, item.value)
                written += 1

    if dry_run:
        print(f"dry-run: store records matched={matched}, namespaces={len(namespaces)}")
    return written, matched


async def _main() -> None:
    args = _parse_args()
    selected_threads = set(args.thread_id)

    sqlite_path = _resolve_sqlite_path(args.sqlite)
    if sqlite_path != ":memory:" and not sqlite_path.startswith("file:") and not Path(sqlite_path).exists():
        raise SystemExit(f"SQLite DB not found: {sqlite_path}")

    postgres_dsn = args.postgres_dsn or _load_postgres_dsn(args.config)
    print(f"source sqlite: {sqlite_path}")
    print(f"destination postgres: {postgres_dsn.split('@')[-1] if '@' in postgres_dsn else postgres_dsn}")
    if selected_threads:
        print(f"selected threads: {', '.join(sorted(selected_threads))}")
    if args.dry_run:
        print("dry-run enabled: no destination writes will be performed")

    async with (
        AsyncSqliteSaver.from_conn_string(sqlite_path) as sqlite_cp,
        AsyncPostgresSaver.from_conn_string(postgres_dsn) as postgres_cp,
        AsyncSqliteStore.from_conn_string(sqlite_path) as sqlite_store,
        AsyncPostgresStore.from_conn_string(postgres_dsn) as postgres_store,
    ):
        await sqlite_cp.setup()
        await postgres_cp.setup()
        await sqlite_store.setup()
        await postgres_store.setup()

        store_written = store_matched = 0
        if not args.skip_store:
            store_written, store_matched = await _migrate_store(
                sqlite_store,
                postgres_store,
                selected_threads=selected_threads,
                dry_run=args.dry_run,
            )

        checkpoint_written = checkpoint_skipped = checkpoint_threads = 0
        if not args.skip_checkpoints:
            checkpoint_written, checkpoint_skipped, checkpoint_threads = await _migrate_checkpoints(
                sqlite_cp,
                postgres_cp,
                selected_threads=selected_threads,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
                limit=args.limit,
            )

    print("migration summary:")
    print(f"  store_records_matched: {store_matched}")
    print(f"  store_records_written: {store_written}")
    print(f"  checkpoint_threads: {checkpoint_threads}")
    print(f"  checkpoints_written: {checkpoint_written}")
    print(f"  checkpoints_skipped: {checkpoint_skipped}")


if __name__ == "__main__":
    # Make relative config path resolution stable when users run from repo root
    # or backend/.
    os.chdir(_repo_backend_dir())
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
