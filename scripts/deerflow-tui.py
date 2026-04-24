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
from rich.markup import escape as _markup_escape
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Header, Label, Markdown, Static
from textual.widgets.data_table import CellType

# ── Constants ────────────────────────────────────────────────────────────

CST = timezone(timedelta(hours=8))
_LOG_PATH = "/tmp/tui_debug.log"
_DEBUG = False


def _log(msg: str) -> None:
    if _DEBUG:
        with open(_LOG_PATH, "a") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S.%f')[:12]} {msg}\n")


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
    # Try Unix epoch (float)
    try:
        dt = datetime.fromtimestamp(float(ts), tz=CST)
        return dt.strftime("%m-%d %H:%M")
    except (ValueError, OSError):
        pass
    # Try ISO format
    try:
        dt = datetime.fromisoformat(ts)
        local = dt.astimezone(CST)
        return local.strftime("%m-%d %H:%M")
    except (ValueError, OSError):
        return ts[:16] if len(ts) >= 16 else ts


def _truncate(text: str | None, max_len: int) -> str:
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _status_text(status: str) -> str:
    color = STATUS_COLORS.get(status, "dim")
    return f"[{color}]{status}[/]"


class FocusableStatic(Static):
    """Static widget that can receive keyboard focus."""
    can_focus = True


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
        data = r.json()
        if not data.get("messages"):
            agents = await self.list_subagents(thread_id, limit=200)
            for a in agents:
                if a.get("task_id", "").startswith(task_id) and a["task_id"] != task_id:
                    r2 = await self.http.get(f"{self.gateway}/api/threads/{thread_id}/subagents/{a['task_id']}")
                    r2.raise_for_status()
                    return r2.json()
        return data

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
        Binding("enter", "confirm", "Yes"),
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
            yield Label("[dim]Y/Enter: 确认  N/Escape: 取消[/]", classes="confirm-hint")

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
        Binding("q", "confirm_quit", "Quit"),
        Binding("escape", "confirm_quit", "Quit", priority=True),
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
        self._auto_refresh_interval: Any | None = None

    def compose(self) -> ComposeResult:
        yield Label("[dim]Filter: (press / to filter)[/]", classes="filter-bar", id="filter-label")
        table: DataTable = DataTable(id="thread-table")
        table.add_columns("#", "Title", "Status", "Updated", "Thread ID")
        table.cursor_type = "row"
        yield table

    def on_mount(self) -> None:
        self._load_threads()

    def on_unmount(self) -> None:
        self._stop_auto_refresh()

    @work(exclusive=True)
    async def _load_threads(self) -> None:
        table = self.query_one("#thread-table", DataTable)
        table.loading = True
        try:
            self._all_threads = await self.client.search_threads()
            self._apply_filter()
            self._maybe_start_auto_refresh()
        except Exception as e:
            self.notify(f"Failed to load threads: {e}", severity="error")
        finally:
            table.loading = False

    def _apply_filter(self) -> None:
        table = self.query_one("#thread-table", DataTable)
        saved_cursor = table.cursor_row
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

        if saved_cursor and saved_cursor < len(filtered):
            table.move_cursor(row=saved_cursor, animate=False)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_open_thread()

    def action_open_thread(self) -> None:
        table = self.query_one("#thread-table", DataTable)
        try:
            row_data = table.get_row_at(table.cursor_row)
        except Exception:
            return
        tid_cell = str(row_data[-1]) if isinstance(row_data, (list, tuple)) else str(row_data)
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
        self.app.push_screen(ThreadDetailScreen(self.client, str(thread_id), str(title)))

    def action_refresh(self) -> None:
        self._load_threads()

    def action_confirm_quit(self) -> None:
        self.app.push_screen(
            ConfirmDialog("退出", "确定退出 DeerFlow TUI？"),
            lambda confirmed: confirmed and self.app.action_quit(),
        )

    def action_filter(self) -> None:
        # Simple inline filter: toggle between filtered and unfiltered
        # A full Input widget would overlay the table; for simplicity, we cycle through empty -> typed
        # In a future iteration, use an Input overlay
        self.notify("Filter: edit the script to add interactive input. Showing all threads.", severity="information")
        self._filter_text = ""
        self._apply_filter()

    def _maybe_start_auto_refresh(self) -> None:
        has_running = any(t.get("status") == "running" for t in self._all_threads)
        if has_running:
            self._start_auto_refresh()
        else:
            self._stop_auto_refresh()

    def _start_auto_refresh(self) -> None:
        if self._auto_refresh_interval is not None:
            return
        self._auto_refresh_interval = self.set_interval(10, self._auto_refresh_tick)

    def _stop_auto_refresh(self) -> None:
        if self._auto_refresh_interval is not None:
            self._auto_refresh_interval.stop()
            self._auto_refresh_interval = None

    @work(exclusive=True)
    async def _auto_refresh_tick(self) -> None:
        try:
            self._all_threads = await self.client.search_threads()
            self._apply_filter()
            self._maybe_start_auto_refresh()
        except Exception:
            pass


# ── Thread Detail Screen ────────────────────────────────────────────────


class ThreadDetailScreen(Screen):
    """Thread detail: session status, todos, subtask list."""

    BINDINGS = [
        Binding("escape", "handle_escape", "Back/Close", priority=True),
        Binding("enter", "open_subtask", "Messages"),
        Binding("tab", "focus_next_area", "Jump", priority=True),
        Binding("m", "open_session_messages", "Session Msgs"),
        Binding("s", "stop_session", "Stop"),
        Binding("c", "cancel_subtask", "Cancel"),
        Binding("r", "refresh", "Refresh"),
        Binding("t", "toggle_todos", "Todos"),
        Binding("n", "next_page", "Next Pg", priority=True),
        Binding("p", "prev_page", "Prev Pg", priority=True),
        Binding("slash", "toggle_filter", "Filter"),
    ]

    CSS = """
    ThreadDetailScreen { layout: vertical; }
    .breadcrumb { dock: top; height: 1; padding: 0 1; background: $surface; }
    .session-bar { dock: top; height: auto; padding: 0 1; background: $surface; }
    #todos-table { dock: top; max-height: 12; border-bottom: solid $primary; }
    #todos-table.hidden { display: none; }
    .page-bar {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: $surface;
    }
    .page-bar.filtering { background: $warning-darken-3; }
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
        self._filter_text = ""
        self._filtering = False
        self._page = 1
        self._page_size = 20

    def compose(self) -> ComposeResult:
        title = str(self.title or "")[:40]
        tid = str(self.thread_id or "")[:16]
        yield Label(f"[bold]Threads > {title}[/]  [dim]{tid}[/]", classes="breadcrumb")
        yield Label("Session: loading...", classes="session-bar", id="session-label")
        todos_table = DataTable(id="todos-table")
        todos_table.add_columns("Status", "Content")
        todos_table.cursor_type = "row"
        yield todos_table
        table: DataTable = DataTable(id="subtask-table")
        table.add_columns("#", "Task ID", "Agent", "Description", "Status", "Started", "Updated", "Msgs")
        table.cursor_type = "row"
        yield table
        yield Label("", classes="page-bar", id="page-label")

    def on_mount(self) -> None:
        self._load_all()
        self.query_one("#subtask-table", DataTable).focus()

    def on_unmount(self) -> None:
        self._stop_auto_refresh()

    @work(exclusive=True)
    async def _load_all(self) -> None:
        try:
            state, status, subagents = await asyncio.gather(
                self.client.get_thread_state(self.thread_id),
                self.client.get_thread_status(self.thread_id),
                self.client.list_subagents(self.thread_id, limit=500),
            )
            self._state = state
            self._session_status = status
            self._subagents = subagents if isinstance(subagents, list) else []
            self._render_all()
            self._maybe_start_auto_refresh()
        except Exception as e:
            self.notify(f"Failed to load: {e}", severity="error")

    def _render_all(self) -> None:
        for name, fn in [
            ("session", self._render_session),
            ("todos", self._render_todos),
            ("subagents", self._render_subagents),
        ]:
            try:
                fn()
            except Exception as e:
                self.notify(f"Render {name} error: {e}", severity="error")

    def _render_session(self) -> None:
        label = self.query_one("#session-label", Label)
        main = self._session_status.get("main_session", {})
        status = main.get("status", "unknown")
        run_id = (main.get("run_id") or "")[:12]
        started = _format_ts(main.get("started_at"))
        label.update(f"Session: {_status_text(status)}  Run: [dim]{run_id}[/]  Started: {started}  [bold cyan]m:查看消息[/]")

    def _render_todos(self) -> None:
        table = self.query_one("#todos-table", DataTable)
        table.clear()
        todos = self._state.get("values", {}).get("todos", [])
        if not todos:
            table.add_row("", "[dim]No todos[/]")
            return
        priority = {"in_progress": 0, "pending": 1, "completed": 2}
        sorted_todos = sorted(todos, key=lambda t: priority.get(t.get("status", ""), 3))
        for idx, todo in enumerate(sorted_todos):
            icon = TODO_ICONS.get(todo.get("status", ""), " ")
            color = STATUS_COLORS.get(todo.get("status", ""), "dim")
            status_text = f"[{color}]{icon}[/{color}]"
            content = todo.get("content", "")
            table.add_row(status_text, content, key=f"todo-{idx}")

    def _render_subagents(self) -> None:
        table = self.query_one("#subtask-table", DataTable)
        # Preserve selected task across refreshes
        saved_task_id = None
        try:
            if table.row_count > 0:
                row_data = table.get_row_at(table.cursor_row)
                if isinstance(row_data, (list, tuple)) and len(row_data) > 1:
                    saved_task_id = str(row_data[1])
        except Exception:
            pass

        table.clear()
        running = [t for t in self._subagents if t.get("status") == "running"]
        others = [t for t in self._subagents if t.get("status") != "running"]
        others.sort(key=lambda t: t.get("started_at", ""), reverse=True)
        ordered = running + others

        # Apply keyword filter
        if self._filter_text:
            ft = self._filter_text.lower()
            ordered = [
                t for t in ordered
                if ft in (t.get("task_id", "") or "").lower()
                or ft in (t.get("subagent_name", "") or "").lower()
                or ft in (t.get("description", "") or "").lower()
            ]

        # Apply pagination
        total = len(ordered)
        total_pages = max(1, (total + self._page_size - 1) // self._page_size)
        self._page = max(1, min(self._page, total_pages))
        offset = (self._page - 1) * self._page_size
        page_items = ordered[offset:offset + self._page_size]

        for i, t in enumerate(page_items, start=offset + 1):
            tid = t.get("task_id", "")
            agent = t.get("subagent_name", "")[:12]
            desc = _truncate(t.get("description", ""), 30)
            status = _status_text(t.get("status", "unknown"))
            started = _format_ts(t.get("started_at"))
            updated = _format_ts(t.get("completed_at")) or "-"
            msgs = str(t.get("message_count", 0))
            table.add_row(i, tid, agent, desc, status, started, updated, msgs, key=t.get("task_id"))

        # Restore cursor to previously selected task
        if saved_task_id:
            for row_idx in range(table.row_count):
                try:
                    rd = table.get_row_at(row_idx)
                    if isinstance(rd, (list, tuple)) and len(rd) > 1 and str(rd[1]) == saved_task_id:
                        table.move_cursor(row=row_idx, animate=False)
                        break
                except Exception:
                    break

        # Update page info bar
        page_label = self.query_one("#page-label", Label)
        page_label.set_class(self._filtering, "filtering")
        if self._filtering:
            display = self._filter_text + "▏" if self._filter_text else "▏"
            page_label.update(
                f"[bold yellow]FILTER:[/] [white]{_markup_escape(display)}[/] "
                f"[dim]({total} matched, p{self._page}/{total_pages}) Esc:Cancel Enter:Done[/]"
            )
        else:
            page_label.update(
                f"[dim]Tasks {offset + 1}-{offset + len(page_items)} of {total}  "
                f"p{self._page}/{total_pages}  n:Next p:Prev /:Filter[/]"
            )

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
        task_id = str(row_data[1]) if isinstance(row_data, (list, tuple)) and len(row_data) > 1 else ""
        if not task_id:
            return
        desc = ""
        for t in self._subagents:
            if t.get("task_id") == task_id:
                desc = t.get("description") or ""
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
        todos = self.query_one("#todos-table", DataTable)
        self._todos_visible = not self._todos_visible
        todos.set_class(not self._todos_visible, "hidden")

    def action_focus_next_area(self) -> None:
        """Toggle focus between todos panel and subtask table."""
        subtask_table = self.query_one("#subtask-table", DataTable)
        todos_table = self.query_one("#todos-table", DataTable)
        if self.focused is subtask_table:
            todos_table.focus()
        else:
            subtask_table.focus()

    def action_next_page(self) -> None:
        self._page += 1
        self._render_subagents()

    def action_prev_page(self) -> None:
        if self._page > 1:
            self._page -= 1
        self._render_subagents()

    def action_toggle_filter(self) -> None:
        """Enter filter mode — bottom bar becomes filter prompt."""
        if not self._filtering:
            self._filtering = True
            self._filter_text = ""
            self._page = 1
            self._render_subagents()

    def _close_filter(self) -> None:
        """Exit filter mode and restore view."""
        self._filtering = False
        self._filter_text = ""
        self._page = 1
        self._render_subagents()

    def on_key(self, event) -> None:
        """Handle keys in filter mode: type to filter, Esc to exit, Enter to confirm."""
        if not self._filtering:
            return
        key = event.key
        if key == "escape":
            self._close_filter()
            event.prevent_default()
        elif key == "enter":
            self._filtering = False
            self._render_subagents()
        elif key == "backspace":
            self._filter_text = self._filter_text[:-1]
            self._page = 1
            self._render_subagents()
            event.prevent_default()
        elif len(key) == 1 and key.isprintable():
            self._filter_text += key
            self._page = 1
            self._render_subagents()
            event.prevent_default()

    def action_handle_escape(self) -> None:
        """Escape: close filter if active, otherwise go back."""
        if self._filtering:
            self._close_filter()
        else:
            self.app.pop_screen()

    def action_go_back(self) -> None:
        self.app.pop_screen()


# ── Message Viewer Screen ───────────────────────────────────────────────


class MessageViewerScreen(Screen):
    """View messages for a subtask or main session."""

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("r", "refresh", "Refresh"),
        Binding("g", "scroll_top", "Top"),
        Binding("G", "scroll_bottom", "Bottom"),
        Binding("n", "next_page", "PgDown", priority=True),
        Binding("p", "prev_page", "PgUp", priority=True),
        Binding("c", "cancel_subtask", "Cancel"),
    ]

    CSS = """
    MessageViewerScreen { layout: vertical; }
    .msg-header { dock: top; height: auto; padding: 0 1; background: $surface; border-bottom: solid $primary; }
    .msg-footer-bar { dock: bottom; height: 1; padding: 0 1; background: $surface; }
    #msg-scroll { height: 1fr; padding: 0 1; }
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
        self.task_id = task_id or ""
        self.description = description or ""
        _log(f"MsgViewer.init: thread={thread_id!r}, task={task_id!r}, mode={mode}")
        self._offset = 0
        self._total = 0
        self._has_more = False
        self._page_size = 50
        self._last_msg_count = 0
        self._auto_refresh_interval: Any | None = None

    def compose(self) -> ComposeResult:
        if self.mode == "subtask":
            header_text = f"[bold]Subtask: {(self.description or '')[:30]}[/]  [dim]{self.task_id[:16]}[/]"
        else:
            header_text = f"[bold]Session Messages[/]  [dim]{self.thread_id[:16]}[/]"
        yield Label(header_text, classes="msg-header", id="msg-title")
        yield VerticalScroll(id="msg-scroll")
        yield Label("[dim]r:Refresh  g:Top  G:Bottom  n:Next  p:Prev  c:Cancel  Esc:Back[/]", classes="msg-footer-bar")

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
                _log(f"_load_messages: fetched {len(messages)} msgs, status={status}")
                self._render_messages(messages, status)
                if status == "running":
                    self._start_auto_refresh()
            else:
                data = await self.client.get_messages(self.thread_id, limit=self._page_size, offset=self._offset)
                messages = data.get("messages", [])
                self._total = data.get("total", 0)
                self._has_more = data.get("has_more", False)
                self._last_msg_count = len(messages)
                _log(f"_load_messages: session fetched {len(messages)} msgs, total={self._total}")
                self._render_messages(messages)
                self._update_page_info()
                # Check if session is running for auto-refresh
                try:
                    status_data = await self.client.get_thread_status(self.thread_id)
                    main = status_data.get("main_session", {})
                    if main.get("status") == "running":
                        self._start_auto_refresh()
                except Exception:
                    pass
        except Exception as e:
            _log(f"_load_messages FAILED: {e}")
            self.notify(f"Failed to load messages: {e}", severity="error")

    def _render_messages(self, messages: list[dict], status: str = "") -> None:
        scroll = self.query_one("#msg-scroll", VerticalScroll)
        _log(f"_render_messages: {len(messages)} msgs, children before={len(scroll.children)}")
        # Remove old message widgets
        for child in list(scroll.children):
            child.remove()

        for i, msg in enumerate(messages):
            try:
                self._mount_message(msg)
            except Exception as e:
                _log(f"_render_messages: mount msg[{i}] FAILED: {e}")

        _log(f"_render_messages: children after={len(scroll.children)}, "
             f"virtual_size={scroll.virtual_size}, size={scroll.size}")

        if status:
            title = self.query_one("#msg-title", Label)
            extra = f"  {_status_text(status)}"
            base = f"[bold]Subtask: {(self.description or '')[:30]}[/]  [dim]{self.task_id[:16]}[/]"
            title.update(base + extra)

        # Auto-scroll to bottom on initial load
        if messages:
            scroll.call_after_refresh(lambda: scroll.scroll_end(animate=False))

    def _mount_message(self, msg: dict) -> None:
        scroll = self.query_one("#msg-scroll", VerticalScroll)
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
        elif content is None:
            content = ""

        content_str = str(content)
        # Truncate long messages
        if len(content_str) > 2000:
            content_str = content_str[:2000] + f"\n[dim]... (truncated, {len(content_str)} chars total)[/dim]"

        style, label = ROLE_STYLES.get(role, ("dim", role))
        tool_name = msg.get("name", "")
        if role == "tool" and tool_name:
            label = f"Tool: {_markup_escape(tool_name)}"

        # Tool calls in AI messages
        tool_calls = msg.get("tool_calls", [])
        tool_text = ""
        if tool_calls:
            tc_parts = []
            for tc in tool_calls:
                fn = _markup_escape(tc.get("name", "?"))
                args = tc.get("args", {})
                if args:
                    args_str = str(args)
                    if len(args_str) > 2048:
                        args_display = args_str[:1024] + f"\n  ... ({len(args_str)} chars, truncated)\n" + args_str[-1024:]
                    else:
                        args_display = args_str
                    args_escaped = _markup_escape(args_display)
                else:
                    args_escaped = ""
                tc_parts.append(f"  → {fn}({args_escaped})")
            tool_text = "\n" + "\n".join(tc_parts)

        escaped = _markup_escape(content_str)
        text = f"[{style}][{label}][/] [dim]{ts}[/]\n{escaped}{tool_text}"
        scroll.mount(Static(text, classes="msg-item"))

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
            if self.mode == "subtask":
                data = await self.client.get_subagent_detail(self.thread_id, self.task_id)
                messages = data.get("messages", [])
                status = data.get("status", "unknown")
                new_count = len(messages)
                if new_count != self._last_msg_count:
                    old_count = self._last_msg_count
                    self._last_msg_count = new_count
                    # Append only new messages
                    scroll = self.query_one("#msg-scroll", VerticalScroll)
                    at_bottom = scroll.is_vertical_scroll_end
                    for msg in messages[old_count:]:
                        self._mount_message(msg)
                    if at_bottom:
                        scroll.call_after_refresh(scroll.scroll_end)
                if status != "running":
                    self._stop_auto_refresh()
                    if new_count != self._last_msg_count:
                        self._render_messages(messages, status)
            else:
                # Session mode: reload and append new messages
                data = await self.client.get_messages(self.thread_id, limit=self._page_size, offset=self._offset)
                messages = data.get("messages", [])
                self._total = data.get("total", 0)
                self._has_more = data.get("has_more", False)
                new_count = len(messages)
                if new_count != self._last_msg_count:
                    scroll = self.query_one("#msg-scroll", VerticalScroll)
                    at_bottom = scroll.is_vertical_scroll_end
                    for msg in messages[self._last_msg_count:]:
                        self._mount_message(msg)
                    self._last_msg_count = new_count
                    if at_bottom:
                        scroll.call_after_refresh(scroll.scroll_end)
                self._update_page_info()
                # Check if still running
                try:
                    status_data = await self.client.get_thread_status(self.thread_id)
                    main = status_data.get("main_session", {})
                    if main.get("status") != "running":
                        self._stop_auto_refresh()
                except Exception:
                    pass
        except Exception:
            pass

    def action_refresh(self) -> None:
        self._load_messages()

    def action_scroll_top(self) -> None:
        scroll = self.query_one("#msg-scroll", VerticalScroll)
        scroll.scroll_home(animate=False)

    def action_scroll_bottom(self) -> None:
        scroll = self.query_one("#msg-scroll", VerticalScroll)
        scroll.scroll_end(animate=False)

    def action_next_page(self) -> None:
        scroll = self.query_one("#msg-scroll", VerticalScroll)
        page_height = scroll.window_region.height
        scroll.scroll_to(0, min(scroll.scroll_y + page_height, scroll.max_scroll_y), animate=False)

    def action_prev_page(self) -> None:
        scroll = self.query_one("#msg-scroll", VerticalScroll)
        page_height = scroll.window_region.height
        scroll.scroll_to(0, max(scroll.scroll_y - page_height, 0), animate=False)

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
    VerticalScroll > .scrollbar { background: $primary-darken-2; }
    VerticalScroll > .scrollbar:hover { background: $primary; }
    VerticalScroll > .scrollbar-thumb { background: $primary-lighten-1; min-height: 1; }
    VerticalScroll > .scrollbar-thumb:hover { background: $primary-lighten-2; }
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
        Binding("ctrl+c", "handle_ctrl_c", "Quit"),
        Binding("question_mark", "show_help", "Help", key_display="?"),
    ]

    def __init__(
        self,
        gateway: str = "http://localhost:8001",
        langgraph: str = "http://localhost:2024",
        thread_id: str = "",
        task_id: str = "",
        session_mode: bool = False,
    ) -> None:
        super().__init__()
        self.client = DeerFlowClient(gateway=gateway, langgraph=langgraph)
        self._df_thread_id = thread_id
        self._df_task_id = task_id
        self._df_session_mode = session_mode
        self._ctrl_c_count = 0

    def on_mount(self) -> None:
        if self._test_mode:
            return
        _log(f"on_mount: thread={self._df_thread_id!r}, task={self._df_task_id!r}")
        self.push_screen(ThreadListScreen(self.client))
        if self._df_thread_id:
            if self._df_session_mode:
                self.set_timer(0.2, lambda: self.push_screen(
                    MessageViewerScreen(self.client, self._df_thread_id, mode="session")))
            elif self._df_task_id:
                self.set_timer(0.2, lambda: self.push_screen(
                    MessageViewerScreen(self.client, self._df_thread_id, mode="subtask", task_id=self._df_task_id)))
            else:
                self.set_timer(0.2, lambda: self.push_screen(
                    ThreadDetailScreen(self.client, self._df_thread_id)))

    async def action_quit(self) -> None:
        await self.client.close()
        self.exit()

    def action_handle_ctrl_c(self) -> None:
        self._ctrl_c_count += 1
        if self._ctrl_c_count >= 2:
            self.exit()
        else:
            self.notify("再按一次 Ctrl+C 退出", severity="warning")
            self.set_timer(3, self._reset_ctrl_c)

    def _reset_ctrl_c(self) -> None:
        self._ctrl_c_count = 0

    def action_show_help(self) -> None:
        self.notify("Enter:Open  r:Refresh  s:Stop  c:Cancel  m:Messages  Esc:Back  q:Quit", severity="information")


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DeerFlow TUI")
    parser.add_argument("--gateway", default="http://localhost:8001", help="Gateway URL")
    parser.add_argument("--langgraph", default="http://localhost:2024", help="LangGraph URL")
    parser.add_argument("--thread", default="", help="Thread ID — skip to thread detail or messages")
    parser.add_argument("--task", default="", help="Task ID — open subtask messages directly (requires --thread)")
    parser.add_argument("--session", action="store_true", help="Open session messages (requires --thread)")
    parser.add_argument("--debug", action="store_true", help=f"Enable debug logging to {_LOG_PATH}")
    args = parser.parse_args()
    if args.debug:
        _DEBUG = True  # noqa: PLW0603 — module-level global
        with open(_LOG_PATH, "w") as f:
            f.write(f"--- TUI debug log {datetime.now().isoformat()} ---\n")
    if args.task and not args.thread:
        parser.error("--task requires --thread")
    if args.session and not args.thread:
        parser.error("--session requires --thread")
    app = DeerFlowTUI(
        gateway=args.gateway,
        langgraph=args.langgraph,
        thread_id=args.thread,
        task_id=args.task,
        session_mode=args.session,
    )
    app.run()
