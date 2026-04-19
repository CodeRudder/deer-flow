#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["textual>=3.0", "httpx>=0.28"]
# ///
"""DeerFlow TUI — Interactive terminal UI for managing DeerFlow threads and tasks.

Usage:
    uv run scripts/deerflow-tui.py
    uv run scripts/deerflow-tui.py --gateway http://host:8001 --langgraph http://host:2024
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Header, Label, Static
from textual.widgets.data_table import CellType

# ── Constants ────────────────────────────────────────────────────────────

CST = timezone(timedelta(hours=8))
STATUS_COLORS = {
    "completed": "green",
    "running": "cyan",
    "pending": "yellow",
    "interrupted": "magenta",
    "failed": "red",
    "cancelled": "red",
    "timed_out": "red",
    "idle": "dim",
    "busy": "cyan",
    "error": "red",
    "unknown": "dim",
}
TODO_ICONS = {"completed": "+", "in_progress": ">", "pending": " "}
ROLE_STYLES = {
    "human": ("bold cyan", "Human"),
    "ai": ("bold green", "AI"),
    "tool": ("bold yellow", "Tool"),
}


# ── Helpers ──────────────────────────────────────────────────────────────


def _format_ts(ts: str | None) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts)
        local = dt.astimezone(CST)
        return local.strftime("%m-%d %H:%M")
    except (ValueError, OSError):
        return ts[:16] if len(ts) >= 16 else ts


def _truncate(text: str, max_len: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _status_text(status: str) -> str:
    color = STATUS_COLORS.get(status, "dim")
    return f"[{color}]{status}[/]"


# ── API Client ───────────────────────────────────────────────────────────


class DeerFlowClient:
    """Async HTTP client for DeerFlow APIs."""

    def __init__(self, gateway: str = "http://localhost:8001", langgraph: str = "http://localhost:2024"):
        self.gateway = gateway
        self.langgraph = langgraph
        self._http: httpx.AsyncClient | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=15)
        return self._http

    async def search_threads(self) -> list[dict]:
        r = await self.http.post(f"{self.gateway}/api/threads/search", json={"limit": 100})
        r.raise_for_status()
        return r.json()

    async def get_thread_state(self, thread_id: str) -> dict:
        r = await self.http.get(f"{self.gateway}/api/threads/{thread_id}/state")
        r.raise_for_status()
        return r.json()

    async def get_thread_status(self, thread_id: str) -> dict:
        r = await self.http.get(f"{self.gateway}/api/threads/{thread_id}/status")
        r.raise_for_status()
        return r.json()

    async def list_subagents(self, thread_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        r = await self.http.get(f"{self.gateway}/api/threads/{thread_id}/subagents", params={"limit": limit, "offset": offset})
        r.raise_for_status()
        return r.json()

    async def get_subagent_detail(self, thread_id: str, task_id: str) -> dict:
        r = await self.http.get(f"{self.gateway}/api/threads/{thread_id}/subagents/{task_id}")
        r.raise_for_status()
        return r.json()

    async def get_messages(self, thread_id: str, limit: int = 100, offset: int = 0) -> dict:
        r = await self.http.get(f"{self.gateway}/api/threads/{thread_id}/messages", params={"limit": limit, "offset": offset})
        r.raise_for_status()
        return r.json()

    async def cancel_run(self, thread_id: str, run_id: str) -> bool:
        r = await self.http.post(f"{self.langgraph}/threads/{thread_id}/runs/{run_id}/cancel")
        return r.status_code in (200, 202, 204)

    async def cancel_subtask(self, task_id: str) -> dict:
        r = await self.http.post(f"{self.gateway}/api/runs/subtasks/{task_id}/cancel")
        r.raise_for_status()
        return r.json()

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()


# ── Confirm Dialog ───────────────────────────────────────────────────────


class ConfirmDialog(ModalScreen[bool]):
    """Yes/No confirmation overlay."""

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "No"),
    ]

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self._title = title
        self._message = message

    def compose(self) -> ComposeResult:
        with Container(classes="confirm-dialog"):
            yield Label(f"[bold]{self._title}[/]", classes="confirm-title")
            yield Label(self._message, classes="confirm-message")
            yield Label("[dim]Y: 确认  N/Escape: 取消[/]", classes="confirm-hint")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


# ── Thread List Screen ───────────────────────────────────────────────────


class ThreadListScreen(Screen):
    """Browse all DeerFlow threads."""

    BINDINGS = [
        Binding("enter", "open_thread", "Open"),
        Binding("r", "refresh", "Refresh"),
        Binding("/", "filter", "Filter"),
        Binding("q", "quit", "Quit"),
    ]

    CSS = """
    ThreadListScreen {
        layout: vertical;
    }
    .filter-bar {
        height: 1;
        dock: top;
        padding: 0 1;
    }
    """

    def __init__(self, client: DeerFlowClient) -> None:
        super().__init__()
        self.client = client
        self._all_threads: list[dict] = []
        self._filter_text = ""

    def compose(self) -> ComposeResult:
        yield Label("[dim]Filter: (press / to filter)[/]", classes="filter-bar", id="filter-label")
        table: DataTable = DataTable(id="thread-table")
        table.add_columns("#", "Title", "Status", "Updated", "Thread ID")
        table.cursor_type = "row"
        yield table

    def on_mount(self) -> None:
        self._load_threads()

    @work(exclusive=True)
    async def _load_threads(self) -> None:
        table = self.query_one("#thread-table", DataTable)
        table.loading = True
        try:
            self._all_threads = await self.client.search_threads()
            self._apply_filter()
        except Exception as e:
            self.notify(f"Failed to load threads: {e}", severity="error")
        finally:
            table.loading = False

    def _apply_filter(self) -> None:
        table = self.query_one("#thread-table", DataTable)
        table.clear()
        filtered = self._all_threads
        if self._filter_text:
            ft = self._filter_text.lower()
            filtered = [t for t in self._all_threads if ft in (t.get("values", {}).get("title", "") or "").lower() or ft in t.get("thread_id", "")]

        for i, t in enumerate(filtered, 1):
            title = _truncate(t.get("values", {}).get("title", "") or "Untitled", 40)
            status = t.get("status", "unknown")
            updated = _format_ts(t.get("updated_at"))
            tid = t.get("thread_id", "")[:16]
            table.add_row(i, title, _status_text(status), updated, tid, key=t.get("thread_id"))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_open_thread()

    def action_open_thread(self) -> None:
        table = self.query_one("#thread-table", DataTable)
        try:
            row_data = table.get_row_at(table.cursor_row)
        except Exception:
            return
        tid_cell = row_data[-1] if isinstance(row_data, (list, tuple)) else str(row_data)
        thread_id = tid_cell
        for t in self._all_threads:
            if t.get("thread_id", "").startswith(tid_cell):
                thread_id = t["thread_id"]
                break
        title = ""
        for t in self._all_threads:
            if t.get("thread_id") == thread_id:
                title = t.get("values", {}).get("title", "") or "Untitled"
                break
        self.app.push_screen(ThreadDetailScreen(self.client, thread_id, title))

    def action_refresh(self) -> None:
        self._load_threads()

    def action_filter(self) -> None:
        # Simple inline filter: toggle between filtered and unfiltered
        # A full Input widget would overlay the table; for simplicity, we cycle through empty -> typed
        # In a future iteration, use an Input overlay
        self.notify("Filter: edit the script to add interactive input. Showing all threads.", severity="information")
        self._filter_text = ""
        self._apply_filter()


# ── Thread Detail Screen ────────────────────────────────────────────────


class ThreadDetailScreen(Screen):
    """Thread detail: session status, todos, subtask list."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("enter", "open_subtask", "Messages"),
        Binding("m", "open_session_messages", "Session Msgs"),
        Binding("s", "stop_session", "Stop"),
        Binding("c", "cancel_subtask", "Cancel"),
        Binding("r", "refresh", "Refresh"),
        Binding("t", "toggle_todos", "Todos"),
    ]

    CSS = """
    ThreadDetailScreen { layout: vertical; }
    .breadcrumb { dock: top; height: 1; padding: 0 1; background: $surface; }
    .session-bar { dock: top; height: auto; padding: 0 1; background: $surface; }
    .todos-panel { dock: top; height: auto; max-height: 10; padding: 0 1; border-bottom: solid $primary; }
    .todos-panel.hidden { display: none; }
    """

    def __init__(self, client: DeerFlowClient, thread_id: str, title: str = "") -> None:
        super().__init__()
        self.client = client
        self.thread_id = thread_id
        self.title = title
        self._todos_visible = True
        self._session_status: dict = {}
        self._state: dict = {}
        self._subagents: list[dict] = []
        self._auto_refresh_interval: Any | None = None

    def compose(self) -> ComposeResult:
        yield Label(f"[bold]Threads > {self.title[:40]}[/]  [dim]{self.thread_id[:16]}[/]", classes="breadcrumb")
        yield Label("Session: loading...", classes="session-bar", id="session-label")
        yield Label("Todos: loading...", classes="todos-panel", id="todos-label")
        table: DataTable = DataTable(id="subtask-table")
        table.add_columns("#", "Task ID", "Agent", "Description", "Status", "Started", "Updated", "Msgs")
        table.cursor_type = "row"
        yield table

    def on_mount(self) -> None:
        self._load_all()

    def on_unmount(self) -> None:
        self._stop_auto_refresh()

    @work(exclusive=True)
    async def _load_all(self) -> None:
        try:
            state, status, subagents = await asyncio.gather(
                self.client.get_thread_state(self.thread_id),
                self.client.get_thread_status(self.thread_id),
                self.client.list_subagents(self.thread_id),
            )
            self._state = state
            self._session_status = status
            self._subagents = subagents if isinstance(subagents, list) else []
            self._render_all()
            self._maybe_start_auto_refresh()
        except Exception as e:
            self.notify(f"Failed to load: {e}", severity="error")

    def _render_all(self) -> None:
        self._render_session()
        self._render_todos()
        self._render_subagents()

    def _render_session(self) -> None:
        label = self.query_one("#session-label", Label)
        main = self._session_status.get("main_session", {})
        status = main.get("status", "unknown")
        run_id = (main.get("run_id") or "")[:12]
        started = _format_ts(main.get("started_at"))
        label.update(f"Session: {_status_text(status)}  Run: [dim]{run_id}[/]  Started: {started}")

    def _render_todos(self) -> None:
        label = self.query_one("#todos-label", Label)
        todos = self._state.get("values", {}).get("todos", [])
        if not todos:
            label.update("[dim]No todos[/]")
            return
        completed = sum(1 for t in todos if t.get("status") == "completed")
        lines = [f"[bold]Todos ({completed}/{len(todos)}):[/]"]
        for todo in todos:
            icon = TODO_ICONS.get(todo.get("status", ""), " ")
            color = STATUS_COLORS.get(todo.get("status", ""), "dim")
            lines.append(f"  [{color}]{icon}[/{color}]  {todo.get('content', '')}")
        label.update("\n".join(lines))

    def _render_subagents(self) -> None:
        table = self.query_one("#subtask-table", DataTable)
        table.clear()
        # Sort: running first, then by started_at descending
        running = [t for t in self._subagents if t.get("status") == "running"]
        others = [t for t in self._subagents if t.get("status") != "running"]
        others.sort(key=lambda t: t.get("started_at", ""), reverse=True)
        ordered = running + others

        for i, t in enumerate(ordered, 1):
            tid = t.get("task_id", "")[:16]
            agent = t.get("subagent_name", "")[:12]
            desc = _truncate(t.get("description", ""), 30)
            status = _status_text(t.get("status", "unknown"))
            started = _format_ts(t.get("started_at"))
            updated = _format_ts(t.get("completed_at")) or "-"
            msgs = str(t.get("message_count", 0))
            table.add_row(i, tid, agent, desc, status, started, updated, msgs, key=t.get("task_id"))

    def _maybe_start_auto_refresh(self) -> None:
        main = self._session_status.get("main_session", {})
        if main.get("status") == "running" or any(t.get("status") == "running" for t in self._subagents):
            self._start_auto_refresh()
        else:
            self._stop_auto_refresh()

    def _start_auto_refresh(self) -> None:
        if self._auto_refresh_interval is not None:
            return
        self._auto_refresh_interval = self.set_interval(5, self._auto_refresh_tick)

    def _stop_auto_refresh(self) -> None:
        if self._auto_refresh_interval is not None:
            self._auto_refresh_interval.stop()
            self._auto_refresh_interval = None

    @work(exclusive=True)
    async def _auto_refresh_tick(self) -> None:
        try:
            self._session_status = await self.client.get_thread_status(self.thread_id)
            self._subagents = await self.client.list_subagents(self.thread_id)
            self._render_all()
            self._maybe_start_auto_refresh()
        except Exception:
            pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table = self.query_one("#subtask-table", DataTable)
        if event.data_table is table:
            self.action_open_subtask()

    def action_open_subtask(self) -> None:
        table = self.query_one("#subtask-table", DataTable)
        try:
            row_data = table.get_row_at(table.cursor_row)
        except Exception:
            return
        tid_cell = row_data[1] if isinstance(row_data, (list, tuple)) and len(row_data) > 1 else str(row_data)
        task_id = tid_cell
        for t in self._subagents:
            if t.get("task_id", "").startswith(tid_cell):
                task_id = t["task_id"]
                break
        desc = ""
        for t in self._subagents:
            if t.get("task_id") == task_id:
                desc = t.get("description", "")
                break
        self.app.push_screen(MessageViewerScreen(self.client, self.thread_id, mode="subtask", task_id=task_id, description=desc))

    def action_open_session_messages(self) -> None:
        self.app.push_screen(MessageViewerScreen(self.client, self.thread_id, mode="session"))

    def action_stop_session(self) -> None:
        main = self._session_status.get("main_session", {})
        run_id = main.get("run_id")
        if not run_id:
            self.notify("No active run to stop", severity="warning")
            return
        self.app.push_screen(
            ConfirmDialog("停止会话", f"确定停止主会话运行？\nRun: {run_id[:16]}"),
            lambda confirmed: confirmed and self._do_stop(run_id),
        )

    @work(exclusive=True)
    async def _do_stop(self, run_id: str) -> None:
        try:
            ok = await self.client.cancel_run(self.thread_id, run_id)
            if ok:
                self.notify("Session stopped", severity="information")
                self._load_all()
            else:
                self.notify("Failed to stop session", severity="error")
        except Exception as e:
            self.notify(f"Stop failed: {e}", severity="error")

    def action_cancel_subtask(self) -> None:
        table = self.query_one("#subtask-table", DataTable)
        try:
            row_data = table.get_row_at(table.cursor_row)
        except Exception:
            return
        tid_cell = row_data[1] if isinstance(row_data, (list, tuple)) and len(row_data) > 1 else str(row_data)
        task_id = tid_cell
        for t in self._subagents:
            if t.get("task_id", "").startswith(tid_cell):
                task_id = t["task_id"]
                break

        self.app.push_screen(
            ConfirmDialog("取消子任务", f"确定取消子任务？\nTask: {task_id[:16]}"),
            lambda confirmed: confirmed and self._do_cancel(task_id),
        )

    @work(exclusive=True)
    async def _do_cancel(self, task_id: str) -> None:
        try:
            result = await self.client.cancel_subtask(task_id)
            if result.get("cancelled"):
                self.notify(f"Task cancelled: {task_id[:16]}", severity="information")
                self._load_all()
            else:
                self.notify(f"Cancel failed: {result.get('error', 'unknown')}", severity="error")
        except Exception as e:
            self.notify(f"Cancel failed: {e}", severity="error")

    def action_refresh(self) -> None:
        self._load_all()

    def action_toggle_todos(self) -> None:
        todos = self.query_one("#todos-label")
        self._todos_visible = not self._todos_visible
        todos.set_class(not self._todos_visible, "hidden")

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ── Message Viewer Screen ───────────────────────────────────────────────


class MessageViewerScreen(Screen):
    """View messages for a subtask or main session."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("n", "next_page", "Next"),
        Binding("p", "prev_page", "Prev"),
        Binding("c", "cancel_subtask", "Cancel"),
    ]

    CSS = """
    MessageViewerScreen { layout: vertical; }
    .msg-header { dock: top; height: auto; padding: 0 1; background: $surface; border-bottom: solid $primary; }
    .msg-footer-bar { dock: bottom; height: 1; padding: 0 1; background: $surface; }
    #msg-scroll { padding: 0 1; }
    """

    def __init__(
        self,
        client: DeerFlowClient,
        thread_id: str,
        mode: str = "subtask",  # "subtask" or "session"
        task_id: str = "",
        description: str = "",
    ) -> None:
        super().__init__()
        self.client = client
        self.thread_id = thread_id
        self.mode = mode
        self.task_id = task_id
        self.description = description
        self._offset = 0
        self._total = 0
        self._has_more = False
        self._page_size = 50
        self._last_msg_count = 0
        self._auto_refresh_interval: Any | None = None

    def compose(self) -> ComposeResult:
        if self.mode == "subtask":
            header_text = f"[bold]Subtask: {self.description[:30]}[/]  [dim]{self.task_id[:16]}[/]"
        else:
            header_text = f"[bold]Session Messages[/]  [dim]{self.thread_id[:16]}[/]"
        yield Label(header_text, classes="msg-header", id="msg-title")
        yield VerticalScroll(id="msg-scroll")
        yield Label("[dim]n:Next  p:Prev  c:Cancel  Esc:Back[/]", classes="msg-footer-bar")

    def on_mount(self) -> None:
        self._load_messages()

    def on_unmount(self) -> None:
        self._stop_auto_refresh()

    @work(exclusive=True)
    async def _load_messages(self) -> None:
        scroll = self.query_one("#msg-scroll", VerticalScroll)
        try:
            if self.mode == "subtask":
                data = await self.client.get_subagent_detail(self.thread_id, self.task_id)
                messages = data.get("messages", [])
                status = data.get("status", "unknown")
                self._render_messages(messages, status)
                if status == "running":
                    self._start_auto_refresh()
            else:
                data = await self.client.get_messages(self.thread_id, limit=self._page_size, offset=self._offset)
                messages = data.get("messages", [])
                self._total = data.get("total", 0)
                self._has_more = data.get("has_more", False)
                self._render_messages(messages)
                self._update_page_info()
        except Exception as e:
            self.notify(f"Failed to load messages: {e}", severity="error")

    def _render_messages(self, messages: list[dict], status: str = "") -> None:
        scroll = self.query_one("#msg-scroll", VerticalScroll)
        # Remove old message widgets
        for child in list(scroll.children):
            child.remove()

        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            ts = _format_ts(msg.get("ts"))

            # Handle list content (multi-part)
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, str):
                        parts.append(part)
                    elif isinstance(part, dict):
                        parts.append(part.get("text", str(part)))
                content = " ".join(parts)

            style, label = ROLE_STYLES.get(role, ("dim", role))
            tool_name = msg.get("name", "")
            if role == "tool" and tool_name:
                label = f"Tool: {tool_name}"

            # Tool calls in AI messages
            tool_calls = msg.get("tool_calls", [])
            tool_text = ""
            if tool_calls:
                tc_parts = []
                for tc in tool_calls:
                    fn = tc.get("name", "?")
                    args = tc.get("args", {})
                    args_str = str(args)[:80] if args else ""
                    tc_parts.append(f"  → {fn}({args_str})")
                tool_text = "\n" + "\n".join(tc_parts)

            text = f"[{style}][{label}][/] [dim]{ts}[/]\n{content}{tool_text}"
            scroll.mount(Static(text, classes="msg-item"))

        if status:
            title = self.query_one("#msg-title", Label)
            extra = f"  {_status_text(status)}"
            base = f"[bold]Subtask: {self.description[:30]}[/]  [dim]{self.task_id[:16]}[/]"
            title.update(base + extra)

    def _update_page_info(self) -> None:
        footer = self.query_one(".msg-footer-bar", Label)
        page = self._offset // self._page_size + 1
        total_pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        has_more = "[LIVE]" if self._has_more else ""
        footer.update(f"[dim]Page {page}/{total_pages} ({self._total} msgs) {has_more}  n:Next  p:Prev  Esc:Back[/]")

    def _start_auto_refresh(self) -> None:
        if self._auto_refresh_interval is not None:
            return
        self._auto_refresh_interval = self.set_interval(5, self._auto_refresh_tick)

    def _stop_auto_refresh(self) -> None:
        if self._auto_refresh_interval is not None:
            self._auto_refresh_interval.stop()
            self._auto_refresh_interval = None

    @work(exclusive=True)
    async def _auto_refresh_tick(self) -> None:
        try:
            data = await self.client.get_subagent_detail(self.thread_id, self.task_id)
            messages = data.get("messages", [])
            status = data.get("status", "unknown")
            if len(messages) != self._last_msg_count:
                self._last_msg_count = len(messages)
                self._render_messages(messages, status)
            if status != "running":
                self._stop_auto_refresh()
                self._render_messages(messages, status)
        except Exception:
            pass

    def action_next_page(self) -> None:
        if self.mode == "session" and self._has_more:
            self._offset += self._page_size
            self._load_messages()

    def action_prev_page(self) -> None:
        if self.mode == "session" and self._offset > 0:
            self._offset = max(0, self._offset - self._page_size)
            self._load_messages()

    def action_cancel_subtask(self) -> None:
        if self.mode != "subtask":
            return
        self.app.push_screen(
            ConfirmDialog("取消子任务", f"确定取消子任务？\nTask: {self.task_id[:16]}"),
            lambda confirmed: confirmed and self._do_cancel(),
        )

    @work(exclusive=True)
    async def _do_cancel(self) -> None:
        try:
            result = await self.client.cancel_subtask(self.task_id)
            if result.get("cancelled"):
                self.notify("Task cancelled", severity="information")
                self._stop_auto_refresh()
            else:
                self.notify(f"Cancel failed: {result.get('error', 'unknown')}", severity="error")
        except Exception as e:
            self.notify(f"Cancel failed: {e}", severity="error")

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ── App ──────────────────────────────────────────────────────────────────


class DeerFlowTUI(App):
    """DeerFlow Terminal UI."""

    TITLE = "DeerFlow TUI"

    # Set to True to suppress auto-mounting the default screen (for testing)
    _test_mode: bool = False
    CSS = """
    Screen { background: $surface; }
    DataTable { height: 1fr; }
    .confirm-dialog {
        align: center middle;
        width: 50;
        height: auto;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    .confirm-title { text-align: center; margin-bottom: 1; }
    .confirm-message { text-align: center; margin-bottom: 1; }
    .confirm-hint { text-align: center; }
    .msg-item { margin-bottom: 1; }
    .hidden { display: none; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "show_help", "Help", key_display="?"),
    ]

    def __init__(self, gateway: str = "http://localhost:8001", langgraph: str = "http://localhost:2024") -> None:
        super().__init__()
        self.client = DeerFlowClient(gateway=gateway, langgraph=langgraph)

    def on_mount(self) -> None:
        if not self._test_mode:
            self.push_screen(ThreadListScreen(self.client))

    async def action_quit(self) -> None:
        await self.client.close()
        self.exit()

    def action_show_help(self) -> None:
        self.notify("Enter:Open  r:Refresh  s:Stop  c:Cancel  m:Messages  Esc:Back  q:Quit", severity="information")


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DeerFlow TUI")
    parser.add_argument("--gateway", default="http://localhost:8001", help="Gateway URL")
    parser.add_argument("--langgraph", default="http://localhost:2024", help="LangGraph URL")
    args = parser.parse_args()
    app = DeerFlowTUI(gateway=args.gateway, langgraph=args.langgraph)
    app.run()
