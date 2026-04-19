#!/usr/bin/env python3
"""View subtask list and todos for a DeerFlow thread.

Usage:
    # By thread ID
    python scripts/thread-tasks.py <thread_id>

    # By thread URL (extracts thread_id automatically)
    python scripts/thread-tasks.py http://localhost:2026/workspace/chats/<thread_id>

    # Pagination
    python scripts/thread-tasks.py <thread_id> --page 2 --page-size 10

    # Filter by status
    python scripts/thread-tasks.py <thread_id> --status interrupted

    # Show last message of each task
    python scripts/thread-tasks.py <thread_id> --last-message

    # Only show todos
    python scripts/thread-tasks.py <thread_id> --todos-only

    # Stop the main session
    python scripts/thread-tasks.py <thread_id> stop
    python scripts/thread-tasks.py <thread_id> stop --with-subtasks

    # Cancel subtasks
    python scripts/thread-tasks.py <thread_id> cancel call_abc123
    python scripts/thread-tasks.py <thread_id> cancel --all-running

    # Custom backend URL
    python scripts/thread-tasks.py <thread_id> --gateway http://host:8001 --langgraph http://host:2024
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from urllib.request import urlopen
from urllib.error import URLError
import json


# ── CJK-aware string padding ────────────────────────────────────────────


def _display_width(s: str) -> int:
    """Return the terminal display width of *s*, counting CJK chars as 2."""
    w = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        w += 2 if eaw in ("W", "F") else 1
    return w


def _pad(s: str, width: int) -> str:
    """Left-align *s* in *width* terminal columns (CJK-aware)."""
    dw = _display_width(s)
    return s + " " * max(0, width - dw)


def _rpad(s: str, width: int) -> str:
    """Right-align *s* in *width* terminal columns (CJK-aware)."""
    dw = _display_width(s)
    return " " * max(0, width - dw) + s


# ── ANSI Colors ──────────────────────────────────────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
RESET = "\033[0m"

STATUS_COLORS = {
    "completed": GREEN,
    "running": CYAN,
    "pending": YELLOW,
    "interrupted": MAGENTA,
    "failed": RED,
    "cancelled": RED,
    "timed_out": RED,
    "unknown": DIM,
}

TODO_COLORS = {
    "completed": GREEN,
    "in_progress": CYAN,
    "pending": YELLOW,
}


def _fetch_json(url: str) -> object:
    try:
        with urlopen(url, timeout=30) as resp:
            return json.loads(resp.read())
    except URLError as e:
        print(f"{RED}Failed to fetch {url}: {e}{RESET}", file=sys.stderr)
        sys.exit(1)


def _post_json(url: str) -> object:
    from urllib.request import Request

    req = Request(url, method="POST", data=b"", headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except URLError as e:
        print(f"{RED}POST {url} failed: {e}{RESET}", file=sys.stderr)
        sys.exit(1)


def _extract_thread_id(arg: str) -> str:
    """Extract thread_id from URL or return as-is."""
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", arg)
    if m:
        return m.group(1)
    return arg


def _truncate(text: str, max_width: int) -> str:
    """Truncate text to fit *max_width* terminal columns (CJK-aware)."""
    text = text.replace("\n", " ").strip()
    w = _display_width(text)
    if w <= max_width:
        return text
    # Trim characters until we fit (leave room for "...")
    result = []
    cur = 0
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cur + cw > max_width - 3:
            break
        result.append(ch)
        cur += cw
    return "".join(result) + "..."


def _format_ts(ts: str) -> str:
    """Format ISO timestamp to a shorter form, converted to CST (UTC+8)."""
    if not ts:
        return "-"
    # Parse ISO timestamp and convert to CST
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", ts)
    if not m:
        return ts[:16]
    from datetime import datetime, timezone, timedelta

    CST = timezone(timedelta(hours=8))
    try:
        # Try parsing with timezone info
        dt = datetime.fromisoformat(ts)
        dt_cst = dt.astimezone(CST)
    except (ValueError, OSError):
        return ts[:16]
    return dt_cst.strftime("%m-%d %H:%M")


def _ts_sort_key(ts: str) -> str:
    """Return raw ISO string for sorting (empty → smallest)."""
    return ts or ""


def _status_label(status: str) -> str:
    color = STATUS_COLORS.get(status, "")
    return f"{color}{status:<12}{RESET}"


def _todo_status_label(status: str) -> str:
    color = TODO_COLORS.get(status, DIM)
    return f"{color}{status:<12}{RESET}"


def _get_last_message(gateway: str, thread_id: str, task_id: str) -> str:
    """Fetch last AI message from subagent session detail."""
    detail = _fetch_json(f"{gateway}/api/threads/{thread_id}/subagents/{task_id}")
    messages = detail.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") == "ai" and msg.get("content"):
            return _truncate(msg["content"], 80)
    return ""


# ── Display ──────────────────────────────────────────────────────────────


def show_subtasks(
    gateway: str,
    thread_id: str,
    page: int,
    page_size: int,
    status_filter: str | None,
    show_last_msg: bool,
) -> int:
    """Show paginated subtask list. Returns total count."""
    offset = (page - 1) * page_size
    url = f"{gateway}/api/threads/{thread_id}/subagents?limit={page_size * 3}&offset=0"
    all_tasks = _fetch_json(url)

    if not isinstance(all_tasks, list):
        print(f"{RED}Unexpected response{RESET}", file=sys.stderr)
        return 0

    # Filter
    if status_filter:
        all_tasks = [t for t in all_tasks if t.get("status") == status_filter]

    # Sort: running first, then by started_at descending
    def _sort_key(t):
        is_running = 0 if t.get("status") == "running" else 1
        started = _ts_sort_key(t.get("started_at", ""))
        return (is_running, "", started) if is_running == 0 else (is_running, started, "")

    all_tasks.sort(key=_sort_key, reverse=True)
    # Re-sort to keep running tasks at top (reverse flips running group too)
    all_tasks.sort(key=lambda t: 0 if t.get("status") == "running" else 1)

    total = len(all_tasks)
    tasks = all_tasks[offset : offset + page_size]

    if not tasks:
        if total == 0:
            print(f"{DIM}No subtasks found.{RESET}")
        else:
            print(f"{DIM}Page {page} is out of range ({total} tasks total).{RESET}")
        return total

    # Column widths
    id_w = 28
    agent_w = 14
    desc_w = 36
    msg_w = show_last_msg and 42 or 0
    time_w = 11

    # Header
    header = (
        f"{'#':>3}  "
        f"{_pad('Task ID', id_w)}  "
        f"{_pad('Agent', agent_w)}  "
        f"{_pad('Description', desc_w)}  "
        f"{'Status':12}  "
        f"{_pad('Started', time_w)}  "
        f"{_pad('Last Update', time_w)}  "
        f"{'Msgs':>4}"
    )
    if show_last_msg:
        header += f"  {_pad('Last Message', msg_w)}"
    print(f"{BOLD}{header}{RESET}")
    print("─" * 120)

    for i, t in enumerate(tasks, start=offset + 1):
        task_id = t.get("task_id", "")[:id_w]
        agent = t.get("subagent_name", "")[:agent_w]
        desc = _truncate(t.get("description", ""), desc_w)
        status = t.get("status", "unknown")
        started = _format_ts(t.get("started_at", ""))
        last_update = _format_ts(t.get("completed_at", "")) or "-"
        msgs = t.get("message_count", 0)

        row = (
            f"{i:>3}  "
            f"{DIM}{_pad(task_id, id_w)}{RESET}  "
            f"{CYAN}{_pad(agent, agent_w)}{RESET}  "
            f"{_pad(desc, desc_w)}  "
            f"{_status_label(status)}  "
            f"{_pad(started, time_w)}  "
            f"{_pad(last_update, time_w)}  "
            f"{msgs:>4}"
        )

        if show_last_msg:
            last_msg = _get_last_message(gateway, thread_id, t["task_id"])
            row += f"  {DIM}{_pad(last_msg, msg_w)}{RESET}"

        print(row)

    # Pagination footer
    total_pages = max(1, (total + page_size - 1) // page_size)
    print()
    print(
        f"{DIM}Showing {len(tasks)} of {total} tasks "
        f"(page {page}/{total_pages}, size {page_size}){RESET}"
    )
    if page < total_pages:
        print(f"{DIM}Next page: --page {page + 1}{RESET}")

    return total


def show_todos(langgraph: str, thread_id: str) -> None:
    """Show todos from thread state."""
    state = _fetch_json(f"{langgraph}/threads/{thread_id}/state")
    values = state.get("values", {})
    todos = values.get("todos", [])
    title = values.get("title", "")

    if title:
        print(f"\n{BOLD}Thread:{RESET} {title}")
    print(f"{BOLD}Thread ID:{RESET} {thread_id}")

    if not todos:
        print(f"\n{DIM}No todos found.{RESET}")
        return

    print(f"\n{BOLD}Todos ({len(todos)}):{RESET}")
    print("─" * 60)

    for i, todo in enumerate(todos, 1):
        content = todo.get("content", "")
        status = todo.get("status", "pending")
        icon = {"completed": "+", "in_progress": ">", "pending": " "}.get(status, "?")
        print(f"  {BOLD}{icon}{RESET}  {_todo_status_label(status)} {content}")

    completed = sum(1 for t in todos if t.get("status") == "completed")
    print(f"\n{DIM}{completed}/{len(todos)} completed{RESET}")


# ── Cancel ──────────────────────────────────────────────────────────────


def stop_session(langgraph: str, gateway: str, thread_id: str, cancel_subtasks: bool) -> None:
    """Stop the main session by cancelling all active runs."""
    cancelled_runs = 0
    failed_runs = 0

    # Step 1: Cancel active LangGraph runs
    runs = _fetch_json(f"{langgraph}/threads/{thread_id}/runs?limit=10")
    if isinstance(runs, list):
        for run in runs:
            if run.get("status") in ("running", "pending"):
                run_id = run.get("run_id", "")
                try:
                    _post_json(f"{langgraph}/threads/{thread_id}/runs/{run_id}/cancel")
                    print(f"  {GREEN}✓{RESET} Run {run_id[:20]}... cancelled")
                    cancelled_runs += 1
                except SystemExit:
                    failed_runs += 1

    if cancelled_runs == 0 and failed_runs == 0:
        print(f"{DIM}No active runs found.{RESET}")

    # Step 2: Optionally cancel running subtasks
    if cancel_subtasks:
        print()
        subagents = _fetch_json(f"{gateway}/api/threads/{thread_id}/subagents?limit=200&offset=0")
        if isinstance(subagents, list):
            active = [t for t in subagents if t.get("status") in ("running", "pending")]
            if active:
                print(f"{BOLD}Cancelling {len(active)} active subtask(s)...{RESET}")
                cancel_tasks(gateway, thread_id, [t["task_id"] for t in active], None)
            else:
                print(f"{DIM}No active subtasks.{RESET}")

    print(f"\n{BOLD}{cancelled_runs} run(s) cancelled, {failed_runs} failed{RESET}")


def cancel_tasks(gateway: str, thread_id: str, task_ids: list[str], status_filter: str | None) -> None:
    """Cancel one or more subtasks."""
    if not task_ids and not status_filter:
        print(f"{RED}Specify task IDs or --status to select tasks to cancel.{RESET}", file=sys.stderr)
        sys.exit(1)

    # Resolve task IDs
    targets: list[str] = list(task_ids)

    if status_filter:
        all_tasks = _fetch_json(f"{gateway}/api/threads/{thread_id}/subagents?limit=200&offset=0")
        if isinstance(all_tasks, list):
            filtered = [t["task_id"] for t in all_tasks if t.get("status") == status_filter]
            if not filtered:
                print(f"{DIM}No tasks with status '{status_filter}' found.{RESET}")
                return
            print(f"{DIM}Found {len(filtered)} task(s) with status '{status_filter}'.{RESET}")
            targets.extend(filtered)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for tid in targets:
        if tid not in seen:
            seen.add(tid)
            unique.append(tid)

    cancelled = 0
    failed = 0

    for tid in unique:
        result = _post_json(f"{gateway}/api/runs/subtasks/{tid}/cancel")
        if result.get("cancelled"):
            print(f"  {GREEN}✓{RESET} {tid} cancelled")
            cancelled += 1
        else:
            error = result.get("error", "unknown")
            print(f"  {RED}✗{RESET} {tid} — {error}")
            failed += 1

    print(f"\n{BOLD}{cancelled} cancelled, {failed} failed{RESET}")


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="View and manage subtasks for a DeerFlow thread",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s df886916-d106-4944-ba66-5161ec715bb3
  %(prog)s http://localhost:2026/workspace/chats/df886916-...
  %(prog)s <id> --page 2 --page-size 10
  %(prog)s <id> --status interrupted --last-message
  %(prog)s <id> --todos-only
  %(prog)s <id> cancel call_abc123 call_def456
  %(prog)s <id> cancel --status interrupted
  %(prog)s <id> cancel --all-running
  %(prog)s <id> stop
  %(prog)s <id> stop --with-subtasks
        """,
    )
    parser.add_argument("thread", help="Thread ID or URL")
    parser.add_argument("--gateway", default="http://localhost:8001", help="Gateway URL")
    parser.add_argument("--langgraph", default="http://localhost:2024", help="LangGraph URL")

    sub = parser.add_subparsers(dest="command")

    # cancel subcommand
    cancel_parser = sub.add_parser("cancel", help="Cancel subtask(s)")
    cancel_parser.add_argument("task_ids", nargs="*", help="Task IDs to cancel")
    cancel_parser.add_argument("--status", help="Cancel all tasks with this status (e.g. interrupted)")
    cancel_parser.add_argument("--all-running", action="store_true", help="Cancel all running/pending tasks")

    # stop subcommand
    stop_parser = sub.add_parser("stop", help="Stop the main session (cancel active runs)")
    stop_parser.add_argument("--with-subtasks", action="store_true", help="Also cancel all running subtasks")

    # List options (when no subcommand)
    parser.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    parser.add_argument("--page-size", type=int, default=15, help="Items per page (default: 15)")
    parser.add_argument("--status", help="Filter by status (running/interrupted/completed/failed/cancelled)")
    parser.add_argument("--last-message", action="store_true", help="Show last AI message per task (slower)")
    parser.add_argument("--todos-only", action="store_true", help="Only show todos, skip subtask list")

    args = parser.parse_args()
    thread_id = _extract_thread_id(args.thread)

    if args.command == "cancel":
        status_filter = args.status
        if args.all_running:
            # Cancel both running and pending
            cancel_tasks(args.gateway, thread_id, args.task_ids, "running")
            cancel_tasks(args.gateway, thread_id, [], "pending")
        else:
            cancel_tasks(args.gateway, thread_id, args.task_ids, status_filter)
        return

    if args.command == "stop":
        stop_session(args.langgraph, args.gateway, thread_id, args.with_subtasks)
        return

    if args.todos_only:
        show_todos(args.langgraph, thread_id)
        return

    # Show todos first
    show_todos(args.langgraph, thread_id)

    # Then subtasks
    print(f"\n{BOLD}Subtasks:{RESET}")
    show_subtasks(
        gateway=args.gateway,
        thread_id=thread_id,
        page=args.page,
        page_size=args.page_size,
        status_filter=args.status,
        show_last_msg=args.last_message,
    )


if __name__ == "__main__":
    main()
