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

    # Custom backend URL
    python scripts/thread-tasks.py <thread_id> --gateway http://host:8001 --langgraph http://host:2024
"""

from __future__ import annotations

import argparse
import re
import sys
from urllib.request import urlopen
from urllib.error import URLError
import json


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


def _extract_thread_id(arg: str) -> str:
    """Extract thread_id from URL or return as-is."""
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", arg)
    if m:
        return m.group(1)
    return arg


def _truncate(text: str, max_len: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _format_ts(ts: str) -> str:
    """Format ISO timestamp to a shorter form."""
    if not ts:
        return "-"
    # "2026-04-14T12:10:52+00:00" → "04-14 12:10"
    m = re.match(r"\d{4}-(\d{2}-\d{2})T(\d{2}:\d{2})", ts)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return ts[:16]


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

    # Sort by most recent activity (completed_at > started_at), descending
    all_tasks.sort(key=lambda t: _ts_sort_key(t.get("completed_at") or t.get("started_at")), reverse=True)

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
        f"{'Task ID':<{id_w}}  "
        f"{'Agent':<{agent_w}}  "
        f"{'Description':<{desc_w}}  "
        f"{'Status'}  "
        f"{'Last Active':<{time_w}}  "
        f"{'Started':<{time_w}}  "
        f"{'Msgs':>4}"
    )
    if show_last_msg:
        header += f"  {'Last Message':<{msg_w}}"
    print(f"{BOLD}{header}{RESET}")
    print("─" * len(header))

    for i, t in enumerate(tasks, start=offset + 1):
        task_id = t.get("task_id", "")[:id_w]
        agent = t.get("subagent_name", "")[:agent_w]
        desc = _truncate(t.get("description", ""), desc_w)
        status = t.get("status", "unknown")
        started = _format_ts(t.get("started_at", ""))
        last_active = _format_ts(t.get("completed_at", "")) or started
        msgs = t.get("message_count", 0)

        row = (
            f"{i:>3}  "
            f"{DIM}{task_id:<{id_w}}{RESET}  "
            f"{CYAN}{agent:<{agent_w}}{RESET}  "
            f"{desc:<{desc_w}}  "
            f"{_status_label(status)}  "
            f"{last_active:<{time_w}}  "
            f"{started:<{time_w}}  "
            f"{msgs:>4}"
        )

        if show_last_msg:
            last_msg = _get_last_message(gateway, thread_id, t["task_id"])
            row += f"  {DIM}{last_msg:<{msg_w}}{RESET}"

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


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="View subtask list and todos for a DeerFlow thread",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s df886916-d106-4944-ba66-5161ec715bb3
  %(prog)s http://localhost:2026/workspace/chats/df886916-...
  %(prog)s <id> --page 2 --page-size 10
  %(prog)s <id> --status interrupted --last-message
  %(prog)s <id> --todos-only
        """,
    )
    parser.add_argument("thread", help="Thread ID or URL")
    parser.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    parser.add_argument("--page-size", type=int, default=15, help="Items per page (default: 15)")
    parser.add_argument("--status", help="Filter by status (running/interrupted/completed/failed/cancelled)")
    parser.add_argument("--last-message", action="store_true", help="Show last AI message per task (slower)")
    parser.add_argument("--todos-only", action="store_true", help="Only show todos, skip subtask list")
    parser.add_argument("--gateway", default="http://localhost:8001", help="Gateway URL (default: http://localhost:8001)")
    parser.add_argument("--langgraph", default="http://localhost:2024", help="LangGraph URL (default: http://localhost:2024)")

    args = parser.parse_args()
    thread_id = _extract_thread_id(args.thread)

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
