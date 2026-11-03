"""One-time backlog cleanup: replace persisted base64 images with file references.

Implements P3 of docs/plans/2026-11-03-session-data-slimming.md. Rewrites the
historical LangGraph checkpointer rows of a Postgres database in place:

- ``checkpoint_blobs`` rows for the ``messages`` and ``viewed_images`` channels
- ``checkpoint_writes`` rows for the same channels

using exactly the same replacement rules as the write-side
``SlimmingCheckpointer`` (P1): ``data:``-URL image parts become placeholder
text carrying the ``/mnt/user-data`` archive path (which the artifacts router
resolves). Serde compatibility is guaranteed by round-tripping every payload
through the saver's own ``serde.loads_typed`` / ``serde.dumps_typed``.

Idempotent: blobs without base64 payloads (or already replaced) are skipped,
so the script can be interrupted and re-run at any time — per-thread progress
is implicit in the data.

Usage::

    cd backend
    export DATABASE_URL=postgresql://...   # target database

    # 1. Report what would be cleaned (no writes)
    PYTHONPATH=. python scripts/slim_checkpoints_postgres.py --dry-run

    # 2. Clean a single thread (small-batch verification)
    PYTHONPATH=. python scripts/slim_checkpoints_postgres.py --threads 71bafe95

    # 3. Clean everything
    PYTHONPATH=. python scripts/slim_checkpoints_postgres.py --all

    # 4. Reclaim disk afterwards (locks each table; run in a maintenance window)
    #    docker exec <pg> psql -U deerflow -d deerflow \\
    #      -c "VACUUM (FULL, ANALYZE) checkpoint_blobs" \\
    #      -c "VACUUM (FULL, ANALYZE) checkpoint_writes"

BACKUP SOP (run before any non-dry-run invocation — the script refuses to
write unless ``--i-have-backup`` is passed)::

    docker exec <pg-container> pg_dump -U deerflow -Fc -d deerflow \\
        -t checkpoint_blobs -t checkpoint_writes -t checkpoints \\
        -f /var/lib/postgresql/data/backup/pre-slim-$(date +%Y%m%d-%H%M).dump
    docker cp <pg-container>:/var/lib/postgresql/data/backup/pre-slim-*.dump ~/backups/deerflow/

    # roll back everything:
    docker exec -i <pg-container> pg_restore -U deerflow -d deerflow \\
        --clean --if-exists ~/backups/deerflow/pre-slim-*.dump

Constraints honoured: per-thread batches; any failure aborts the current
thread only (earlier threads stay rewritten — that is safe because every
blob replacement is independent and idempotent); the running Gateway should
be stopped (or idle) during a real run so it does not race the same rows.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from dataclasses import dataclass, field

# Minimum base64 run length that counts as an image payload (mirrors P1).
_MIN_B64 = 4000
_B64_RE = re.compile(rb"[A-Za-z0-9+/=_-]{%d,}" % _MIN_B64)
_CHANNELS = ("messages", "viewed_images")


@dataclass
class ThreadResult:
    thread_id: str
    blobs_scanned: int = 0
    blobs_rewritten: int = 0
    blobs_bytes_before: int = 0
    blobs_bytes_after: int = 0
    writes_scanned: int = 0
    writes_rewritten: int = 0
    writes_bytes_before: int = 0
    writes_bytes_after: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def saved(self) -> int:
        return (self.blobs_bytes_before - self.blobs_bytes_after) + (self.writes_bytes_before - self.writes_bytes_after)


def _has_base64(blob: bytes) -> bool:
    return _B64_RE.search(blob) is not None


async def process_thread(conn, serde, thread_id: str, dry_run: bool, min_b64: int) -> ThreadResult:
    from deerflow.runtime.checkpointer.slimming import slim_channel_value

    result = ThreadResult(thread_id=thread_id)
    b64_re = re.compile(rb"[A-Za-z0-9+/=_-]{%d,}" % min_b64)

    # --- checkpoint_blobs -------------------------------------------------
    cur = conn.cursor()
    cur.execute(
        "SELECT channel, version, type, blob FROM checkpoint_blobs WHERE thread_id = %s AND channel = ANY(%s)",
        (thread_id, list(_CHANNELS)),
    )
    for channel, version, type_, blob in cur.fetchall():
        result.blobs_scanned += 1
        blob = bytes(blob)
        if not b64_re.search(blob):
            continue
        try:
            value = serde.loads_typed((type_, blob))
        except Exception as exc:
            result.errors.append(f"blob {channel}/{version}: deserialize failed: {exc}")
            continue
        slimmed = slim_channel_value(channel, value, thread_id=thread_id)
        new_type, new_blob = serde.dumps_typed(slimmed)
        new_blob = bytes(new_blob)
        result.blobs_bytes_before += len(blob)
        result.blobs_bytes_after += len(new_blob)
        if len(new_blob) >= len(blob):
            continue  # nothing to reclaim
        result.blobs_rewritten += 1
        if not dry_run:
            cur.execute(
                "UPDATE checkpoint_blobs SET type = %s, blob = %s WHERE thread_id = %s AND checkpoint_ns = '' AND channel = %s AND version = %s",
                (new_type, new_blob, thread_id, channel, version),
            )

    # --- checkpoint_writes -------------------------------------------------
    cur.execute(
        "SELECT checkpoint_id, task_id, idx, channel, type, blob FROM checkpoint_writes WHERE thread_id = %s AND channel = ANY(%s)",
        (thread_id, list(_CHANNELS)),
    )
    for checkpoint_id, task_id, idx, channel, type_, blob in cur.fetchall():
        result.writes_scanned += 1
        blob = bytes(blob)
        if not b64_re.search(blob):
            continue
        try:
            value = serde.loads_typed((type_, blob))
        except Exception as exc:
            result.errors.append(f"write {checkpoint_id}/{task_id}/{channel}: deserialize failed: {exc}")
            continue
        slimmed = slim_channel_value(channel, value, thread_id=thread_id)
        new_type, new_blob = serde.dumps_typed(slimmed)
        new_blob = bytes(new_blob)
        result.writes_bytes_before += len(blob)
        result.writes_bytes_after += len(new_blob)
        if len(new_blob) >= len(blob):
            continue
        result.writes_rewritten += 1
        if not dry_run:
            cur.execute(
                "UPDATE checkpoint_writes SET type = %s, blob = %s WHERE thread_id = %s AND checkpoint_ns = '' AND checkpoint_id = %s AND task_id = %s AND idx = %s AND channel = %s",
                (new_type, new_blob, thread_id, checkpoint_id, task_id, idx, channel),
            )

    if not dry_run:
        conn.commit()
    return result


async def main_async(args: argparse.Namespace) -> int:
    import psycopg
    from psycopg.rows import tuple_row

    dsn = args.database_url or os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set — pass --database-url or export it", file=sys.stderr)
        return 2
    if not args.dry_run and not args.i_have_backup:
        print("Refusing to write without --i-have-backup — execute the BACKUP SOP in the script header first (or use --dry-run)", file=sys.stderr)
        return 2

    # Serde must match what the saver wrote: take it from a real saver instance.
    from deerflow.config import get_app_config
    from deerflow.runtime.checkpointer.async_provider import make_checkpointer

    async with make_checkpointer(get_app_config()) as cp:
        inner = getattr(cp, "delegate", cp)
        serde = inner.serde

    with psycopg.connect(dsn, row_factory=tuple_row) as conn:
        cur = conn.cursor()
        if args.threads:
            patterns = [f"{p}%" for p in args.threads]
            cur.execute(
                "SELECT DISTINCT thread_id FROM checkpoint_blobs WHERE thread_id LIKE ANY(%s) UNION SELECT DISTINCT thread_id FROM checkpoint_writes WHERE thread_id LIKE ANY(%s)",
                (patterns, patterns),
            )
        else:
            cur.execute("SELECT DISTINCT thread_id FROM checkpoint_blobs UNION SELECT DISTINCT thread_id FROM checkpoint_writes")
        thread_ids = sorted({r[0] for r in cur.fetchall()})
        print(f"threads to process: {len(thread_ids)} (dry_run={args.dry_run})")

        total_saved = 0
        processed = 0
        for thread_id in thread_ids:
            result = await process_thread(conn, serde, thread_id, args.dry_run, args.min_b64)
            processed += 1
            total_saved += result.saved
            if result.blobs_rewritten or result.writes_rewritten or result.errors:
                print(
                    f"[{thread_id}] blobs {result.blobs_rewritten}/{result.blobs_scanned} rewritten "
                    f"({result.blobs_bytes_before / 1024**2:.1f} -> {result.blobs_bytes_after / 1024**2:.1f} MB), "
                    f"writes {result.writes_rewritten}/{result.writes_scanned} "
                    f"({result.writes_bytes_before / 1024**2:.1f} -> {result.writes_bytes_after / 1024**2:.1f} MB)" + (f" ERRORS: {result.errors}" if result.errors else "")
                )
            if args.limit and processed >= args.limit:
                print(f"--limit {args.limit} reached; stopping")
                break

        size_row = None
        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        size_row = cur.fetchone()
        print(f"\n{'would reclaim' if args.dry_run else 'reclaimed'}: {total_saved / 1024**2:.1f} MB across {processed} threads")
        print(f"database size now: {size_row[0]}")
        if not args.dry_run:
            print("reclaim disk: VACUUM (FULL, ANALYZE) checkpoint_blobs / checkpoint_writes in a maintenance window")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report only; no writes")
    parser.add_argument("--threads", nargs="*", help="thread-id prefixes to clean (default: all)")
    parser.add_argument("--all", action="store_true", help="process every thread (default behaviour without --threads; flag exists to force explicit intent)")
    parser.add_argument("--limit", type=int, default=0, help="stop after N threads (small-batch verification)")
    parser.add_argument("--min-b64", type=int, default=_MIN_B64, help="minimum base64 run length to treat as a payload")
    parser.add_argument("--database-url", default=None, help="DSN (defaults to $DATABASE_URL)")
    parser.add_argument("--i-have-backup", action="store_true", help="confirm the BACKUP SOP (header) has been executed")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
