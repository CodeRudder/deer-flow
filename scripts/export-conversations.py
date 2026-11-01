#!/usr/bin/env python3
"""Export conversations from LangGraph checkpointer database to JSONL files.

Reads all threads from the SQLite checkpointer, extracts messages from the
latest checkpoint per thread, and writes them using the same ``serialize_message``
format as ``MainSessionMiddleware`` — so the exported files are immediately
compatible with the real-time JSONL session persistence.

Usage:
    # Export all threads
    python scripts/export-conversations.py

    # Export a specific thread
    python scripts/export-conversations.py --thread-id <thread_id>

    # Preview without writing files
    python scripts/export-conversations.py --dry-run

    # Truncate large tool output (> N chars)
    python scripts/export-conversations.py --max-content-len 50000

Output:
    {base_dir}/threads/{thread_id}/conversation.jsonl

Format (same as MainSessionMiddleware / SubagentSession):
    {"ts": "...", "role": "human|ai|tool", "content": "...", ...}
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: ensure we can import deerflow from the backend directory
# ---------------------------------------------------------------------------

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if _BACKEND_DIR.exists():
    sys.path.insert(0, str(_BACKEND_DIR / "packages" / "harness"))
    sys.path.insert(0, str(_BACKEND_DIR))


def _resolve_db_path() -> Path:
    """Find the checkpointer SQLite database."""
    # Try standard locations
    candidates = [
        _BACKEND_DIR / ".deer-flow" / "checkpoints.db",
        _BACKEND_DIR / "checkpoints.db",
        Path(".deer-flow") / "checkpoints.db",
        Path("checkpoints.db"),
    ]
    for p in candidates:
        if p.exists():
            return p

    # Try reading from config
    try:
        from deerflow.config.app_config import get_app_config
        from deerflow.runtime.store._sqlite_utils import resolve_sqlite_conn_str

        app_config = get_app_config()
        if app_config.checkpointer and app_config.checkpointer.type == "sqlite":
            conn_str = resolve_sqlite_conn_str(app_config.checkpointer.connection_string or "checkpoints.db")
            p = Path(conn_str)
            if p.exists():
                return p
    except Exception:
        pass

    raise FileNotFoundError("Cannot find checkpoints.db. Use --db-path to specify the location.")


def _resolve_base_dir() -> Path:
    """Find the DeerFlow base data directory."""
    candidates = [
        _BACKEND_DIR / ".deer-flow",
        Path(".deer-flow"),
    ]
    for p in candidates:
        if p.exists():
            return p

    # Fallback: try config (may trigger circular imports, so last)
    try:
        from deerflow.config.paths import get_paths
        return get_paths().base_dir
    except Exception:
        pass

    return _BACKEND_DIR / ".deer-flow"


def _get_serializer():
    """Get the LangGraph checkpoint serializer."""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    return JsonPlusSerializer()


def _get_threads(db: sqlite3.Connection) -> list[str]:
    """Get all thread IDs from the database."""
    cur = db.cursor()
    cur.execute("SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id")
    return [row[0] for row in cur.fetchall()]


def _get_latest_checkpoint_messages(db: sqlite3.Connection, thread_id: str, serde) -> list:
    """Extract messages from the latest checkpoint for a thread.

    Returns a list of LangChain message objects.
    """
    cur = db.cursor()

    # Get latest checkpoint
    cur.execute(
        "SELECT checkpoint_id, type, checkpoint FROM checkpoints "
        "WHERE thread_id = ? ORDER BY checkpoint_id DESC LIMIT 1",
        (thread_id,),
    )
    row = cur.fetchone()
    if not row:
        return []

    _cp_id, cp_type, cp_data = row
    checkpoint = serde.loads_typed((cp_type, cp_data))
    channel_values = checkpoint.get("channel_values", {})
    messages = channel_values.get("messages", [])

    # Also check for pending writes to the messages channel
    # (writes that happened after the checkpoint was created)
    cur.execute(
        "SELECT task_id, idx, channel, type, value FROM writes "
        "WHERE thread_id = ? AND checkpoint_id = ? AND channel = 'messages' "
        "ORDER BY task_id, idx",
        (thread_id, _cp_id),
    )
    seen_ids = {getattr(m, "id", None) for m in messages}
    for wrow in cur.fetchall():
        _task_id, _idx, _channel, wtype, wvalue = wrow
        try:
            data = serde.loads_typed((wtype, wvalue))
            if isinstance(data, list):
                for msg in data:
                    msg_id = getattr(msg, "id", None)
                    if msg_id and msg_id not in seen_ids:
                        messages.append(msg)
                        seen_ids.add(msg_id)
        except Exception:
            pass

    return messages


def _serialize_for_export(msg, *, max_content_len: int | None = None) -> dict:
    """Serialize a LangChain message to JSONL-compatible dict.

    Same format as ``deerflow.subagents.session.serialize_message``.
    Inlined to avoid circular import when running outside the server context.
    """
    from datetime import datetime, timezone

    entry: dict = {"ts": datetime.now(tz=timezone.utc).isoformat(timespec="seconds")}

    # Include message ID for deduplication on restart
    msg_id = getattr(msg, "id", None)
    if msg_id:
        entry["id"] = msg_id

    # Import message types lazily (they're already loaded via checkpointer serde)
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    content = msg.content
    if max_content_len and isinstance(content, str) and len(content) > max_content_len:
        content = content[:max_content_len] + f"\n...[TRUNCATED: original {len(msg.content)} chars]"

    if isinstance(msg, HumanMessage):
        entry["role"] = "human"
        entry["content"] = content
    elif isinstance(msg, AIMessage):
        entry["role"] = "ai"
        entry["content"] = content
        if msg.tool_calls:
            entry["tool_calls"] = [
                {"id": tc.get("id"), "name": tc.get("name"), "args": tc.get("args")}
                for tc in msg.tool_calls
            ]
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            reasoning = msg.reasoning_content
            if max_content_len and isinstance(reasoning, str) and len(reasoning) > max_content_len:
                reasoning = reasoning[:max_content_len] + f"\n...[TRUNCATED: original {len(msg.reasoning_content)} chars]"
            entry["reasoning"] = reasoning
    elif isinstance(msg, ToolMessage):
        entry["role"] = "tool"
        entry["tool_call_id"] = msg.tool_call_id
        entry["content"] = content
        entry["name"] = msg.name
    else:
        entry["role"] = getattr(msg, "type", "unknown")
        entry["content"] = content

    return entry


def export_thread(
    db: sqlite3.Connection,
    serde,
    thread_id: str,
    output_dir: Path,
    *,
    max_content_len: int | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Export a single thread's conversation to JSONL.

    Returns stats dict.
    """
    messages = _get_latest_checkpoint_messages(db, thread_id, serde)

    stats = {
        "thread_id": thread_id,
        "message_count": len(messages),
        "types": {},
        "output_path": None,
        "skipped": False,
    }

    for m in messages:
        t = type(m).__name__
        stats["types"][t] = stats["types"].get(t, 0) + 1

    if not messages:
        stats["skipped"] = True
        return stats

    jsonl_path = output_dir / "threads" / thread_id / "conversation.jsonl"
    stats["output_path"] = str(jsonl_path)

    if dry_run:
        return stats

    # Check if file already exists
    if jsonl_path.exists() and not force:
        existing_lines = sum(1 for _ in open(jsonl_path))
        stats["existing_lines"] = existing_lines
        stats["skipped"] = True
        return stats

    # Write JSONL
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for msg in messages:
        entry = _serialize_for_export(msg, max_content_len=max_content_len)
        lines.append(json.dumps(entry, ensure_ascii=False))

    with open(jsonl_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    stats["written_lines"] = len(lines)
    return stats


def verify_thread(thread_id: str, output_dir: Path, expected_count: int) -> dict:
    """Verify exported JSONL file integrity.

    Returns verification result dict.
    """
    jsonl_path = output_dir / "threads" / thread_id / "conversation.jsonl"
    result = {"thread_id": thread_id, "valid": False, "errors": []}

    if not jsonl_path.exists():
        result["errors"].append("File does not exist")
        return result

    lines = []
    roles = {}
    errors = []
    with open(jsonl_path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                role = entry.get("role", "unknown")
                roles[role] = roles.get(role, 0) + 1

                # Validate required fields
                if "ts" not in entry:
                    errors.append(f"Line {i}: missing 'ts'")
                if "role" not in entry:
                    errors.append(f"Line {i}: missing 'role'")
                if "content" not in entry:
                    errors.append(f"Line {i}: missing 'content'")

                lines.append(entry)
            except json.JSONDecodeError as e:
                errors.append(f"Line {i}: invalid JSON: {e}")

    result["total_lines"] = len(lines)
    result["roles"] = roles
    result["errors"] = errors

    if expected_count and len(lines) != expected_count:
        result["errors"].append(f"Line count mismatch: expected {expected_count}, got {len(lines)}")

    result["valid"] = len(errors) == 0
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Export LangGraph checkpointer conversations to JSONL files"
    )
    parser.add_argument("--db-path", type=Path, help="Path to checkpoints.db")
    parser.add_argument("--output-dir", type=Path, help="Output base directory (default: auto-detect)")
    parser.add_argument("--thread-id", help="Export only this specific thread")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    parser.add_argument("--force", action="store_true", help="Overwrite existing JSONL files")
    parser.add_argument("--max-content-len", type=int, default=50000, help="Truncate content longer than N chars (default: 50000)")
    parser.add_argument("--verify", action="store_true", help="Verify exported files after writing")
    parser.add_argument("--list", action="store_true", help="List threads with message counts")

    args = parser.parse_args()

    # Resolve paths
    db_path = args.db_path or _resolve_db_path()
    output_dir = args.output_dir or _resolve_base_dir()

    print(f"Database: {db_path}")
    print(f"Output:   {output_dir}")
    print()

    # Open database
    db = sqlite3.connect(str(db_path))
    serde = _get_serializer()

    # Get threads
    if args.thread_id:
        thread_ids = [args.thread_id]
    else:
        thread_ids = _get_threads(db)

    if not thread_ids:
        print("No threads found in database.")
        db.close()
        return

    # List mode
    if args.list:
        print(f"{'Thread ID':<42} {'Messages':>8} {'Types'}")
        print("-" * 80)
        for tid in thread_ids:
            messages = _get_latest_checkpoint_messages(db, tid, serde)
            type_counts = _count_types(messages)
            types_str = ", ".join(f"{k}:{v}" for k, v in sorted(type_counts.items()))
            print(f"{tid:<42} {len(messages):>8} {types_str}")
        db.close()
        return

    # Export
    print(f"Exporting {len(thread_ids)} thread(s)...")
    total_messages = 0
    total_files = 0
    skipped = 0

    for tid in thread_ids:
        stats = export_thread(
            db, serde, tid, output_dir,
            max_content_len=args.max_content_len,
            dry_run=args.dry_run,
            force=args.force,
        )
        mc = stats["message_count"]
        total_messages += mc

        status = ""
        if stats["skipped"]:
            skipped += 1
            if stats.get("existing_lines"):
                status = f"(exists: {stats['existing_lines']} lines, use --force to overwrite)"
            elif mc == 0:
                status = "(empty)"
        else:
            total_files += 1
            status = f"→ {stats.get('written_lines', 0)} lines"

        types_str = ", ".join(f"{k}:{v}" for k, v in sorted(stats["types"].items()))
        print(f"  {tid}: {mc} messages {types_str} {status}")

    print()
    action = "Would write" if args.dry_run else "Wrote"
    print(f"{action} {total_files} file(s), {total_messages} total messages")
    if skipped:
        print(f"Skipped {skipped} thread(s) (empty or existing files)")

    # Verify
    if args.verify and not args.dry_run:
        print()
        print("Verifying exported files...")
        all_valid = True
        for tid in thread_ids:
            messages = _get_latest_checkpoint_messages(db, tid, serde)
            if not messages:
                continue
            result = verify_thread(tid, output_dir, len(messages))
            status = "OK" if result["valid"] else "FAILED"
            print(f"  {tid}: {status} ({result.get('total_lines', 0)} lines)")
            if result["errors"]:
                for err in result["errors"]:
                    print(f"    ERROR: {err}")
                all_valid = False

        if all_valid:
            print("\nAll files verified successfully.")
        else:
            print("\nSome files had verification errors!")
            sys.exit(1)

    db.close()


def _count_types(messages):
    """Count message types in a list."""
    counts = {}
    for m in messages:
        name = type(m).__name__
        counts[name] = counts.get(name, 0) + 1
    return counts


if __name__ == "__main__":
    main()
