#!/usr/bin/env python3
"""Rebuild LangGraph dev's local thread catalog from PostgreSQL.

LangGraph ``dev`` keeps thread/run API metadata in ``backend/.langgraph_api`` even
when DeerFlow's graph checkpointer and store are backed by PostgreSQL. If that
local catalog is deleted, old PG checkpoints still exist but LangGraph API
returns "Thread not found". This script restores missing thread catalog entries
through the public LangGraph API.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
import psycopg
from psycopg.rows import dict_row

from deerflow.config.app_config import AppConfig

DEFAULT_LANGGRAPH_URL = "http://localhost:2024"
DEFAULT_GRAPH_ID = "lead_agent"


@dataclass
class ThreadCatalogRecord:
    thread_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SyncStats:
    discovered: int = 0
    checked: int = 0
    skipped_existing: int = 0
    repaired: int = 0
    patched: int = 0
    failed: int = 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync LangGraph dev local thread catalog from DeerFlow PostgreSQL persistence.",
    )
    parser.add_argument(
        "--langgraph-url",
        default=DEFAULT_LANGGRAPH_URL,
        help=f"LangGraph API base URL. Default: {DEFAULT_LANGGRAPH_URL}",
    )
    parser.add_argument(
        "--postgres-dsn",
        default=None,
        help="PostgreSQL DSN. Defaults to config.yaml checkpointer.connection_string.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml. Defaults to DeerFlow config resolution.",
    )
    parser.add_argument(
        "--graph-id",
        default=DEFAULT_GRAPH_ID,
        help=f"Fallback graph_id when thread metadata does not contain one. Default: {DEFAULT_GRAPH_ID}",
    )
    parser.add_argument(
        "--thread-id",
        action="append",
        default=[],
        help="Only sync this thread ID. Can be provided multiple times.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of discovered threads to process.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover and check threads without creating or patching LangGraph catalog entries.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP timeout for LangGraph API requests in seconds. Default: 10.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of thread IDs to check per /threads/search call. Default: 100.",
    )
    return parser.parse_args()


def _load_postgres_dsn(config_path: str | None, explicit_dsn: str | None) -> str | None:
    if explicit_dsn:
        return explicit_dsn
    config = AppConfig.from_file(config_path)
    if config.checkpointer is None:
        print("LangGraph catalog sync skipped: config.yaml does not define a checkpointer section.")
        return None
    if config.checkpointer.type != "postgres":
        print("LangGraph catalog sync skipped: checkpointer.type is not 'postgres'.")
        return None
    if not config.checkpointer.connection_string:
        raise SystemExit("config.yaml checkpointer.connection_string is empty.")
    return config.checkpointer.connection_string


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _valid_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _merge_record(
    records: dict[str, ThreadCatalogRecord],
    thread_id: str,
    *,
    value: Any = None,
    metadata_value: Any = None,
    fallback_graph_id: str,
) -> None:
    if not _valid_uuid(thread_id):
        return

    payload = _as_dict(value)
    metadata = _as_dict(metadata_value) or _as_dict(payload.get("metadata"))
    if not metadata.get("graph_id"):
        metadata["graph_id"] = fallback_graph_id

    existing = records.get(thread_id)
    if existing is None:
        records[thread_id] = ThreadCatalogRecord(
            thread_id=thread_id,
            metadata=metadata,
        )
        return

    if metadata:
        existing.metadata = {**existing.metadata, **metadata}


def discover_threads(
    dsn: str,
    *,
    fallback_graph_id: str,
    thread_ids: list[str],
    limit: int | None,
) -> list[ThreadCatalogRecord]:
    records: dict[str, ThreadCatalogRecord] = {}
    only = {tid for tid in thread_ids if _valid_uuid(tid)}

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            if only:
                cur.execute(
                    "select key, value->'metadata' as metadata from store where prefix = 'threads' and key = any(%s)",
                    (list(only),),
                )
            else:
                cur.execute(
                    "select key, value->'metadata' as metadata from store where prefix = 'threads' order by updated_at desc nulls last, key",
                )
            for row in cur.fetchall():
                _merge_record(
                    records,
                    str(row["key"]),
                    metadata_value=row["metadata"],
                    fallback_graph_id=fallback_graph_id,
                )

            if only:
                cur.execute(
                    "select distinct thread_id from checkpoints where thread_id = any(%s)",
                    (list(only),),
                )
            else:
                cur.execute(
                    "select distinct thread_id from checkpoints where thread_id is not null order by thread_id",
                )
            for row in cur.fetchall():
                _merge_record(
                    records,
                    str(row["thread_id"]),
                    fallback_graph_id=fallback_graph_id,
                )

    selected = list(records.values())
    if limit is not None:
        selected = selected[: max(limit, 0)]
    return selected


def _chunks(records: list[ThreadCatalogRecord], batch_size: int) -> list[list[ThreadCatalogRecord]]:
    size = max(1, batch_size)
    return [records[index : index + size] for index in range(0, len(records), size)]


async def _search_existing_threads(
    client: httpx.AsyncClient,
    batch: list[ThreadCatalogRecord],
) -> dict[str, dict[str, Any]]:
    response = await client.post(
        "/threads/search",
        json={
            "ids": [record.thread_id for record in batch],
            "limit": len(batch),
            "offset": 0,
        },
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        return {}
    existing: dict[str, dict[str, Any]] = {}
    for item in data:
        if isinstance(item, dict) and item.get("thread_id"):
            existing[str(item["thread_id"])] = item
    return existing


async def _create_thread(client: httpx.AsyncClient, record: ThreadCatalogRecord) -> None:
    response = await client.post(
        "/threads",
        json={
            "thread_id": record.thread_id,
            "metadata": record.metadata,
            "if_exists": "do_nothing",
        },
    )
    if response.status_code == 409:
        return
    response.raise_for_status()


async def _patch_thread(client: httpx.AsyncClient, record: ThreadCatalogRecord) -> None:
    response = await client.patch(
        f"/threads/{record.thread_id}",
        json={"metadata": record.metadata},
    )
    response.raise_for_status()


async def sync_catalog(
    records: list[ThreadCatalogRecord],
    *,
    langgraph_url: str,
    dry_run: bool,
    timeout: float,
    batch_size: int,
) -> SyncStats:
    stats = SyncStats(discovered=len(records))
    async with httpx.AsyncClient(base_url=langgraph_url.rstrip("/"), timeout=timeout) as client:
        for batch in _chunks(records, batch_size):
            try:
                existing_by_id = await _search_existing_threads(client, batch)
            except Exception as exc:
                stats.failed += len(batch)
                ids = ", ".join(record.thread_id for record in batch[:5])
                suffix = "..." if len(batch) > 5 else ""
                print(f"FAILED batch search {ids}{suffix}: {exc}", file=sys.stderr)
                continue

            for record in batch:
                stats.checked += 1
                try:
                    existing = existing_by_id.get(record.thread_id)
                    if existing is None:
                        if dry_run:
                            print(f"WOULD REPAIR {record.thread_id} graph_id={record.metadata.get('graph_id')}")
                            continue
                        await _create_thread(client, record)
                        stats.repaired += 1
                        print(f"REPAIRED {record.thread_id} graph_id={record.metadata.get('graph_id')}")
                        continue

                    metadata = _as_dict(existing.get("metadata"))
                    if not metadata.get("graph_id") and record.metadata.get("graph_id"):
                        if dry_run:
                            print(f"WOULD PATCH {record.thread_id} graph_id={record.metadata.get('graph_id')}")
                            continue
                        await _patch_thread(client, record)
                        stats.patched += 1
                        print(f"PATCHED {record.thread_id} graph_id={record.metadata.get('graph_id')}")
                        continue

                    stats.skipped_existing += 1
                except Exception as exc:
                    stats.failed += 1
                    print(f"FAILED {record.thread_id}: {exc}", file=sys.stderr)
    return stats


async def async_main() -> int:
    args = _parse_args()
    dsn = _load_postgres_dsn(args.config, args.postgres_dsn)
    if dsn is None:
        return 0
    records = discover_threads(
        dsn,
        fallback_graph_id=args.graph_id,
        thread_ids=args.thread_id,
        limit=args.limit,
    )
    stats = await sync_catalog(
        records,
        langgraph_url=args.langgraph_url,
        dry_run=args.dry_run,
        timeout=args.timeout,
        batch_size=args.batch_size,
    )
    print(f"LangGraph catalog sync complete: discovered={stats.discovered} checked={stats.checked} repaired={stats.repaired} patched={stats.patched} skipped_existing={stats.skipped_existing} failed={stats.failed}")
    return 1 if stats.failed else 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
