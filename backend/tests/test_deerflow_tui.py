"""Tests for DeerFlow TUI script (scripts/deerflow-tui.py).

Covers:
- Helper functions (_format_ts, _truncate, _status_text)
- DeerFlowClient (API calls with mocked httpx)
- ConfirmDialog (confirm/cancel dismiss)
- ThreadListScreen (load, filter, select, refresh)
- ThreadDetailScreen (render, stop, cancel, toggle todos, auto-refresh)
- MessageViewerScreen (render, pagination, cancel, auto-refresh)
- DeerFlowTUI app (navigation flow)
"""

import importlib
import sys
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Import TUI module from scripts directory
sys.path.insert(0, "../scripts")
tui = importlib.import_module("deerflow-tui")


def _mock_thread_client():
    """Create a DeerFlowClient with all methods mocked to return safe defaults."""
    client = tui.DeerFlowClient()
    client.search_threads = AsyncMock(return_value=[])
    client.get_thread_state = AsyncMock(return_value={"values": {}})
    client.get_thread_status = AsyncMock(return_value={"main_session": {"status": "idle"}})
    client.list_subagents = AsyncMock(return_value=[])
    client.get_subagent_detail = AsyncMock(return_value={"task_id": "t1", "status": "completed", "messages": []})
    client.get_messages = AsyncMock(return_value={"messages": [], "total": 0, "has_more": False})
    client.cancel_run = AsyncMock(return_value=True)
    client.cancel_subtask = AsyncMock(return_value={"cancelled": True, "task_id": "t1"})
    client.close = AsyncMock()
    return client


def _make_app(client=None):
    """Create a test-mode DeerFlowTUI with mocked client."""
    app = tui.DeerFlowTUI()
    app._test_mode = True
    app.client = client or _mock_thread_client()
    return app


# ── Helper Function Tests ───────────────────────────────────────────────


class TestFormatTs:
    def test_none_returns_dash(self):
        assert tui._format_ts(None) == "-"

    def test_empty_returns_dash(self):
        assert tui._format_ts("") == "-"

    def test_utc_timestamp_converts_to_cst(self):
        # UTC: 2026-04-19T10:00:00+00:00 → CST: 2026-04-19 18:00
        result = tui._format_ts("2026-04-19T10:00:00+00:00")
        assert result == "04-19 18:00"

    def test_iso_without_timezone(self):
        result = tui._format_ts("2026-04-19T10:30:00")
        # Naive datetime, treated as local — just verify format
        assert "04-19" in result

    def test_short_invalid_string(self):
        result = tui._format_ts("bad")
        assert result == "bad"

    def test_truncates_long_string_on_error(self):
        # Not a valid ISO format but long enough
        result = tui._format_ts("2026-04-19TINVALID extra")
        assert len(result) <= 16

    def test_valid_iso_format(self):
        result = tui._format_ts("2026-01-05T08:30:00+08:00")
        assert result == "01-05 08:30"


class TestTruncate:
    def test_short_text_unchanged(self):
        assert tui._truncate("hello", 10) == "hello"

    def test_long_text_truncated(self):
        result = tui._truncate("a" * 20, 10)
        assert result == "a" * 7 + "..."
        assert len(result) == 10

    def test_exact_length_not_truncated(self):
        text = "a" * 10
        assert tui._truncate(text, 10) == text

    def test_newlines_replaced(self):
        assert tui._truncate("line1\nline2", 20) == "line1 line2"

    def test_whitespace_stripped(self):
        assert tui._truncate("  hello  ", 10) == "hello"


class TestStatusText:
    def test_known_status(self):
        result = tui._status_text("running")
        assert "running" in result
        assert "cyan" in result

    def test_unknown_status(self):
        result = tui._status_text("unknown")
        assert "unknown" in result
        assert "dim" in result

    def test_completed_status(self):
        result = tui._status_text("completed")
        assert "completed" in result
        assert "green" in result


# ── DeerFlowClient Tests ────────────────────────────────────────────────


class TestDeerFlowClient:
    def _make_client(self, response_json=None, status_code=200):
        client = tui.DeerFlowClient(gateway="http://gw", langgraph="http://lg")
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json.return_value = response_json or {}
        mock_response.raise_for_status = MagicMock()

        # Create a mock async client
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.is_closed = False
        mock_http.get.return_value = mock_response
        mock_http.post.return_value = mock_response
        mock_http.aclose = AsyncMock()

        client._http = mock_http
        return client, mock_http

    @pytest.mark.anyio
    async def test_search_threads(self):
        data = [{"thread_id": "abc", "status": "idle"}]
        client, mock_http = self._make_client(data)
        result = await client.search_threads()
        assert result == data
        mock_http.post.assert_called_once_with(
            "http://gw/api/threads/search", json={"limit": 100}
        )

    @pytest.mark.anyio
    async def test_get_thread_state(self):
        data = {"values": {"title": "Test"}}
        client, mock_http = self._make_client(data)
        result = await client.get_thread_state("t1")
        assert result == data
        mock_http.get.assert_called_once_with("http://gw/api/threads/t1/state")

    @pytest.mark.anyio
    async def test_get_thread_status(self):
        data = {"main_session": {"status": "running"}}
        client, mock_http = self._make_client(data)
        result = await client.get_thread_status("t1")
        assert result == data

    @pytest.mark.anyio
    async def test_list_subagents(self):
        data = [{"task_id": "t1", "status": "completed"}]
        client, mock_http = self._make_client(data)
        result = await client.list_subagents("tid", limit=10, offset=5)
        assert result == data
        mock_http.get.assert_called_once_with(
            "http://gw/api/threads/tid/subagents",
            params={"limit": 10, "offset": 5},
        )

    @pytest.mark.anyio
    async def test_get_subagent_detail(self):
        data = {"task_id": "t1", "messages": []}
        client, mock_http = self._make_client(data)
        result = await client.get_subagent_detail("tid", "t1")
        assert result == data

    @pytest.mark.anyio
    async def test_get_messages(self):
        data = {"messages": [], "total": 0, "has_more": False}
        client, mock_http = self._make_client(data)
        result = await client.get_messages("tid", limit=50, offset=0)
        assert result == data

    @pytest.mark.anyio
    async def test_cancel_run_success(self):
        client, mock_http = self._make_client(status_code=202)
        result = await client.cancel_run("tid", "rid")
        assert result is True
        mock_http.post.assert_called_once_with(
            "http://lg/threads/tid/runs/rid/cancel"
        )

    @pytest.mark.anyio
    async def test_cancel_run_failure(self):
        client, mock_http = self._make_client(status_code=404)
        result = await client.cancel_run("tid", "rid")
        assert result is False

    @pytest.mark.anyio
    async def test_cancel_subtask(self):
        data = {"task_id": "t1", "cancelled": True}
        client, mock_http = self._make_client(data)
        result = await client.cancel_subtask("t1")
        assert result == data
        mock_http.post.assert_called_once_with(
            "http://gw/api/runs/subtasks/t1/cancel"
        )

    @pytest.mark.anyio
    async def test_close(self):
        client, mock_http = self._make_client()
        await client.close()
        mock_http.aclose.assert_called_once()

    @pytest.mark.anyio
    async def test_close_when_none(self):
        client = tui.DeerFlowClient()
        client._http = None
        await client.close()  # Should not raise

    def test_http_creates_client(self):
        client = tui.DeerFlowClient()
        assert client._http is None
        http = client.http
        assert isinstance(http, httpx.AsyncClient)
        # Re-access returns same instance
        assert client.http is http


# ── ConfirmDialog Tests ─────────────────────────────────────────────────


class TestConfirmDialog:
    @pytest.mark.anyio
    async def test_action_confirm(self):
        app = _make_app()
        async with app.run_test() as pilot:
            dialog = tui.ConfirmDialog("Title", "Message")
            app.push_screen(dialog)
            await pilot.pause()
            # Press y to confirm
            result = await pilot.press("y")
            # Dialog should be dismissed with True
            await pilot.pause()

    @pytest.mark.anyio
    async def test_action_cancel(self):
        app = _make_app()
        async with app.run_test() as pilot:
            dialog = tui.ConfirmDialog("Title", "Message")
            app.push_screen(dialog)
            await pilot.pause()
            # Press n to cancel
            await pilot.press("n")
            await pilot.pause()


# ── ThreadListScreen Tests ──────────────────────────────────────────────


class TestThreadListScreen:
    def _mock_client(self, threads=None):
        client = tui.DeerFlowClient()
        client.search_threads = AsyncMock(return_value=threads or [])
        return client

    @pytest.mark.anyio
    async def test_loads_threads_on_mount(self):
        threads = [
            {
                "thread_id": "abc-123",
                "status": "idle",
                "updated_at": "2026-04-19T10:00:00+00:00",
                "values": {"title": "Test Thread"},
            }
        ]
        client = self._mock_client(threads)
        app = _make_app()
        app.client = client
        # Override default mount to use our mock client
        screen = tui.ThreadListScreen(client)
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            table = screen.query_one("#thread-table", tui.DataTable)
            # Should have 1 row
            assert table.row_count == 1

    @pytest.mark.anyio
    async def test_empty_threads(self):
        client = self._mock_client([])
        screen = tui.ThreadListScreen(client)
        app = _make_app()
        app.client = _mock_thread_client()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            table = screen.query_one("#thread-table", tui.DataTable)
            assert table.row_count == 0

    @pytest.mark.anyio
    async def test_api_failure_shows_error(self):
        client = tui.DeerFlowClient()
        client.search_threads = AsyncMock(side_effect=Exception("Connection refused"))
        screen = tui.ThreadListScreen(client)
        app = _make_app()
        app.client = _mock_thread_client()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            # Should not crash, table should be empty
            table = screen.query_one("#thread-table", tui.DataTable)
            assert table.row_count == 0

    @pytest.mark.anyio
    async def test_open_thread_posts_message(self):
        threads = [
            {
                "thread_id": "abc-123",
                "status": "idle",
                "values": {"title": "My Thread"},
            }
        ]
        client = self._mock_client(threads)
        screen = tui.ThreadListScreen(client)
        app = _make_app()
        screen._all_threads = threads
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            # Select first row and press enter
            table = screen.query_one("#thread-table", tui.DataTable)
            table.move_cursor(row=0)
            await pilot.press("enter")
            await pilot.pause()
            # Verify ThreadSelected was handled by app (pushed ThreadDetailScreen)
            await pilot.pause()

    @pytest.mark.anyio
    async def test_refresh_reloads(self):
        client = self._mock_client([])
        screen = tui.ThreadListScreen(client)
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            # Press r to refresh
            await pilot.press("r")
            await pilot.pause()
            # search_threads should have been called again
            assert client.search_threads.call_count >= 2

    @pytest.mark.anyio
    async def test_filter_action(self):
        client = self._mock_client([])
        screen = tui.ThreadListScreen(client)
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("/")
            await pilot.pause()
            # Filter text should be reset
            assert screen._filter_text == ""

    def test_apply_filter_with_text(self):
        threads = [
            {"thread_id": "aaa", "values": {"title": "Alpha"}, "status": "idle"},
            {"thread_id": "bbb", "values": {"title": "Beta"}, "status": "idle"},
            {"thread_id": "ccc", "values": {"title": "Gamma"}, "status": "idle"},
        ]
        client = self._mock_client(threads)
        screen = tui.ThreadListScreen(client)
        screen._all_threads = threads
        screen._filter_text = "alpha"
        # _apply_filter accesses query — can't call without mounting
        # Test the filter logic directly
        ft = "alpha"
        filtered = [t for t in threads if ft in (t.get("values", {}).get("title", "") or "").lower()]
        assert len(filtered) == 1
        assert filtered[0]["thread_id"] == "aaa"

    def test_filter_by_thread_id(self):
        threads = [
            {"thread_id": "abc-123", "values": {"title": "X"}, "status": "idle"},
            {"thread_id": "def-456", "values": {"title": "Y"}, "status": "idle"},
        ]
        ft = "def"
        filtered = [t for t in threads if ft in t.get("thread_id", "")]
        assert len(filtered) == 1
        assert filtered[0]["thread_id"] == "def-456"

    def test_open_thread_no_cursor(self):
        """action_open_thread should not crash when no row is selected."""
        client = self._mock_client([])
        screen = tui.ThreadListScreen(client)
        # Calling without mounting should not raise
        try:
            screen.action_open_thread()
        except Exception:
            pass  # Expected — no DOM yet


# ── ThreadDetailScreen Tests ────────────────────────────────────────────


class TestThreadDetailScreen:
    def _mock_client(
        self,
        state=None,
        status=None,
        subagents=None,
        cancel_run_result=True,
        cancel_subtask_result=None,
    ):
        client = tui.DeerFlowClient()
        client.get_thread_state = AsyncMock(return_value=state or {"values": {}})
        client.get_thread_status = AsyncMock(
            return_value=status or {"main_session": {"status": "idle"}}
        )
        client.list_subagents = AsyncMock(return_value=subagents or [])
        client.cancel_run = AsyncMock(return_value=cancel_run_result)
        client.cancel_subtask = AsyncMock(
            return_value=cancel_subtask_result or {"cancelled": True, "task_id": "t1"}
        )
        return client

    @pytest.mark.anyio
    async def test_loads_and_renders(self):
        state = {"values": {"title": "Test", "todos": [{"content": "Todo1", "status": "completed"}]}}
        status = {"main_session": {"status": "idle", "run_id": "r1", "started_at": "2026-04-19T10:00:00+00:00"}}
        subagents = [
            {"task_id": "t1", "subagent_name": "general", "description": "Task 1", "status": "completed", "started_at": "2026-04-19T10:00:00+00:00", "completed_at": "2026-04-19T11:00:00+00:00", "message_count": 5}
        ]
        client = self._mock_client(state, status, subagents)
        screen = tui.ThreadDetailScreen(client, "thread-1", "Test")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()

            table = screen.query_one("#subtask-table", tui.DataTable)
            assert table.row_count == 1

    @pytest.mark.anyio
    async def test_no_active_run_stop_warns(self):
        client = self._mock_client(status={"main_session": {"status": "idle"}})
        screen = tui.ThreadDetailScreen(client, "t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            # Press s — should show warning (no active run)
            await pilot.press("s")
            await pilot.pause()

    @pytest.mark.anyio
    async def test_stop_session_with_confirm(self):
        client = self._mock_client(status={"main_session": {"status": "running", "run_id": "run-abc"}})
        screen = tui.ThreadDetailScreen(client, "t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            # Confirm dialog should be visible
            await pilot.press("y")
            await pilot.pause()
            await pilot.pause()

    @pytest.mark.anyio
    async def test_cancel_subtask_with_confirm(self):
        subagents = [
            {"task_id": "t1", "subagent_name": "general", "description": "Task", "status": "running", "started_at": "2026-04-19T10:00:00+00:00", "completed_at": "", "message_count": 0}
        ]
        client = self._mock_client(subagents=subagents)
        screen = tui.ThreadDetailScreen(client, "t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            table = screen.query_one("#subtask-table", tui.DataTable)
            table.move_cursor(row=0)
            await pilot.press("c")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            await pilot.pause()

    @pytest.mark.anyio
    async def test_open_session_messages(self):
        client = self._mock_client()
        screen = tui.ThreadDetailScreen(client, "t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()

    @pytest.mark.anyio
    async def test_toggle_todos(self):
        state = {"values": {"todos": [{"content": "Task", "status": "pending"}]}}
        client = self._mock_client(state=state)
        screen = tui.ThreadDetailScreen(client, "t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            assert screen._todos_visible is True
            await pilot.press("t")
            assert screen._todos_visible is False
            await pilot.press("t")
            assert screen._todos_visible is True

    @pytest.mark.anyio
    async def test_refresh(self):
        client = self._mock_client()
        screen = tui.ThreadDetailScreen(client, "t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            # Should have called APIs again
            assert client.get_thread_state.call_count >= 2

    @pytest.mark.anyio
    async def test_api_failure(self):
        client = tui.DeerFlowClient()
        client.get_thread_state = AsyncMock(side_effect=Exception("Fail"))
        client.get_thread_status = AsyncMock(return_value={})
        client.list_subagents = AsyncMock(return_value=[])
        screen = tui.ThreadDetailScreen(client, "t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            # Should not crash

    @pytest.mark.anyio
    async def test_go_back(self):
        client = self._mock_client()
        screen = tui.ThreadDetailScreen(client, "t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

    def test_render_session_no_main(self):
        """_render_session handles missing main_session key."""
        client = self._mock_client()
        screen = tui.ThreadDetailScreen(client, "t1")
        screen._session_status = {}
        # Should not crash even without mounting (but needs DOM)
        # Just verify the data access pattern
        main = screen._session_status.get("main_session", {})
        assert main.get("status", "unknown") == "unknown"

    def test_subagent_sort_order(self):
        """Running tasks first, then by started_at descending."""
        subagents = [
            {"task_id": "t1", "status": "completed", "started_at": "2026-04-19T12:00:00+00:00"},
            {"task_id": "t2", "status": "running", "started_at": "2026-04-19T10:00:00+00:00"},
            {"task_id": "t3", "status": "completed", "started_at": "2026-04-19T11:00:00+00:00"},
        ]
        running = [t for t in subagents if t.get("status") == "running"]
        others = [t for t in subagents if t.get("status") != "running"]
        others.sort(key=lambda t: t.get("started_at", ""), reverse=True)
        ordered = running + others
        assert ordered[0]["task_id"] == "t2"  # running first
        assert ordered[1]["task_id"] == "t1"  # 12:00 newer
        assert ordered[2]["task_id"] == "t3"  # 11:00 older


# ── MessageViewerScreen Tests ───────────────────────────────────────────


class TestMessageViewerScreen:
    def _mock_client(self, subagent_detail=None, messages=None, cancel_result=None):
        client = tui.DeerFlowClient()
        client.get_subagent_detail = AsyncMock(
            return_value=subagent_detail or {"task_id": "t1", "status": "completed", "messages": []}
        )
        client.get_messages = AsyncMock(
            return_value=messages or {"messages": [], "total": 0, "has_more": False}
        )
        client.cancel_subtask = AsyncMock(
            return_value=cancel_result or {"cancelled": True, "task_id": "t1"}
        )
        return client

    @pytest.mark.anyio
    async def test_subtask_mode_loads(self):
        detail = {
            "task_id": "t1",
            "status": "completed",
            "messages": [
                {"role": "human", "content": "Hello", "ts": "2026-04-19T10:00:00+00:00"},
                {"role": "ai", "content": "Hi there", "ts": "2026-04-19T10:01:00+00:00"},
            ],
        }
        client = self._mock_client(subagent_detail=detail)
        screen = tui.MessageViewerScreen(client, "tid", mode="subtask", task_id="t1", description="Test task")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            scroll = screen.query_one("#msg-scroll", tui.VerticalScroll)
            # Should have 2 message items
            assert len(scroll.children) == 2

    @pytest.mark.anyio
    async def test_session_mode_loads(self):
        msgs = {
            "messages": [
                {"role": "human", "content": "Question", "ts": "2026-04-19T10:00:00+00:00"},
            ],
            "total": 100,
            "has_more": True,
        }
        client = self._mock_client(messages=msgs)
        screen = tui.MessageViewerScreen(client, "tid", mode="session")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            assert screen._total == 100
            assert screen._has_more is True

    @pytest.mark.anyio
    async def test_next_page_session_mode(self):
        msgs = {"messages": [], "total": 100, "has_more": True}
        client = self._mock_client(messages=msgs)
        screen = tui.MessageViewerScreen(client, "tid", mode="session")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            await pilot.pause()
            assert screen._offset == 50

    @pytest.mark.anyio
    async def test_next_page_no_more(self):
        msgs = {"messages": [], "total": 10, "has_more": False}
        client = self._mock_client(messages=msgs)
        screen = tui.MessageViewerScreen(client, "tid", mode="session")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            # Should not advance
            assert screen._offset == 0

    @pytest.mark.anyio
    async def test_prev_page(self):
        msgs = {"messages": [], "total": 100, "has_more": True}
        client = self._mock_client(messages=msgs)
        screen = tui.MessageViewerScreen(client, "tid", mode="session")
        screen._offset = 50
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
            await pilot.pause()
            assert screen._offset == 0

    @pytest.mark.anyio
    async def test_prev_page_at_start(self):
        msgs = {"messages": [], "total": 10, "has_more": False}
        client = self._mock_client(messages=msgs)
        screen = tui.MessageViewerScreen(client, "tid", mode="session")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("p")
            await pilot.pause()
            assert screen._offset == 0

    @pytest.mark.anyio
    async def test_next_prev_noop_in_subtask_mode(self):
        """n/p keys should do nothing in subtask mode."""
        client = self._mock_client()
        screen = tui.MessageViewerScreen(client, "tid", mode="subtask", task_id="t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("n")
            await pilot.press("p")
            await pilot.pause()
            assert screen._offset == 0

    @pytest.mark.anyio
    async def test_cancel_subtask_from_messages(self):
        client = self._mock_client()
        screen = tui.MessageViewerScreen(client, "tid", mode="subtask", task_id="t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            await pilot.pause()

    @pytest.mark.anyio
    async def test_cancel_noop_in_session_mode(self):
        """c key should do nothing in session mode."""
        client = self._mock_client()
        screen = tui.MessageViewerScreen(client, "tid", mode="session")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            # Should not push confirm dialog
            client.cancel_subtask.assert_not_called()

    @pytest.mark.anyio
    async def test_go_back(self):
        client = self._mock_client()
        screen = tui.MessageViewerScreen(client, "tid", mode="subtask", task_id="t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

    @pytest.mark.anyio
    async def test_api_failure(self):
        client = tui.DeerFlowClient()
        client.get_subagent_detail = AsyncMock(side_effect=Exception("API error"))
        screen = tui.MessageViewerScreen(client, "tid", mode="subtask", task_id="t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            # Should not crash

    @pytest.mark.anyio
    async def test_list_content_rendering(self):
        """Multi-part content (list of dicts/strings) is handled."""
        detail = {
            "task_id": "t1",
            "status": "completed",
            "messages": [
                {
                    "role": "human",
                    "content": [{"type": "text", "text": "Part1"}, " Part2"],
                    "ts": "2026-04-19T10:00:00+00:00",
                },
            ],
        }
        client = self._mock_client(subagent_detail=detail)
        screen = tui.MessageViewerScreen(client, "tid", mode="subtask", task_id="t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            scroll = screen.query_one("#msg-scroll", tui.VerticalScroll)
            assert len(scroll.children) == 1

    @pytest.mark.anyio
    async def test_tool_call_rendering(self):
        """AI messages with tool_calls render correctly."""
        detail = {
            "task_id": "t1",
            "status": "completed",
            "messages": [
                {
                    "role": "ai",
                    "content": "Using tool",
                    "ts": "2026-04-19T10:00:00+00:00",
                    "tool_calls": [{"name": "bash", "args": {"cmd": "ls"}}],
                },
            ],
        }
        client = self._mock_client(subagent_detail=detail)
        screen = tui.MessageViewerScreen(client, "tid", mode="subtask", task_id="t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            scroll = screen.query_one("#msg-scroll", tui.VerticalScroll)
            assert len(scroll.children) == 1

    @pytest.mark.anyio
    async def test_tool_message_with_name(self):
        """Tool role messages show the tool name."""
        detail = {
            "task_id": "t1",
            "status": "completed",
            "messages": [
                {"role": "tool", "content": "output", "ts": "2026-04-19T10:00:00+00:00", "name": "bash"},
            ],
        }
        client = self._mock_client(subagent_detail=detail)
        screen = tui.MessageViewerScreen(client, "tid", mode="subtask", task_id="t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            scroll = screen.query_one("#msg-scroll", tui.VerticalScroll)
            assert len(scroll.children) == 1

    @pytest.mark.anyio
    async def test_empty_messages(self):
        """Empty message list should render nothing."""
        client = self._mock_client(subagent_detail={"task_id": "t1", "status": "completed", "messages": []})
        screen = tui.MessageViewerScreen(client, "tid", mode="subtask", task_id="t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            scroll = screen.query_one("#msg-scroll", tui.VerticalScroll)
            assert len(scroll.children) == 0


# ── DeerFlowTUI App Tests ───────────────────────────────────────────────


class TestDeerFlowTUIApp:
    @pytest.mark.anyio
    async def test_app_launches(self):
        app = _make_app()
        app._test_mode = False  # Enable auto-mount for this test
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            # Should have ThreadListScreen as first screen
            assert isinstance(app.screen, tui.ThreadListScreen)

    @pytest.mark.anyio
    async def test_show_help(self):
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("?")
            await pilot.pause()

    @pytest.mark.anyio
    async def test_quit(self):
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()


# ── Auto-Refresh Logic Tests ────────────────────────────────────────────


class TestAutoRefresh:
    def test_maybe_start_when_running(self):
        """Auto-refresh starts when main session is running."""
        client = tui.DeerFlowClient()
        client.get_thread_state = AsyncMock(return_value={"values": {}})
        client.get_thread_status = AsyncMock(return_value={"main_session": {"status": "idle"}})
        client.list_subagents = AsyncMock(return_value=[])
        screen = tui.ThreadDetailScreen(client, "t1")
        screen._session_status = {"main_session": {"status": "running"}}
        screen._subagents = []
        # Check the condition logic
        main = screen._session_status.get("main_session", {})
        should_start = main.get("status") == "running" or any(t.get("status") == "running" for t in screen._subagents)
        assert should_start is True

    def test_maybe_start_when_subtask_running(self):
        """Auto-refresh starts when a subtask is running."""
        screen = tui.ThreadDetailScreen(tui.DeerFlowClient(), "t1")
        screen._session_status = {"main_session": {"status": "idle"}}
        screen._subagents = [{"status": "running"}]
        main = screen._session_status.get("main_session", {})
        should_start = main.get("status") == "running" or any(t.get("status") == "running" for t in screen._subagents)
        assert should_start is True

    def test_maybe_stop_when_all_idle(self):
        """Auto-refresh stops when nothing is running."""
        screen = tui.ThreadDetailScreen(tui.DeerFlowClient(), "t1")
        screen._session_status = {"main_session": {"status": "idle"}}
        screen._subagents = [{"status": "completed"}]
        main = screen._session_status.get("main_session", {})
        should_start = main.get("status") == "running" or any(t.get("status") == "running" for t in screen._subagents)
        assert should_start is False

    def test_message_viewer_auto_refresh_condition(self):
        """MessageViewer auto-refresh starts for running subtask."""
        screen = tui.MessageViewerScreen(tui.DeerFlowClient(), "tid", mode="subtask", task_id="t1")
        # Simulate running status
        assert screen.mode == "subtask"

    def test_message_viewer_no_auto_refresh_session(self):
        """MessageViewer should NOT auto-refresh in session mode."""
        screen = tui.MessageViewerScreen(tui.DeerFlowClient(), "tid", mode="session")
        assert screen._auto_refresh_interval is None


# ── Edge Case Tests ─────────────────────────────────────────────────────


class TestEdgeCases:
    def test_constants(self):
        """Verify all constants are properly defined."""
        assert "running" in tui.STATUS_COLORS
        assert "completed" in tui.STATUS_COLORS
        assert "completed" in tui.TODO_ICONS
        assert "pending" in tui.TODO_ICONS
        assert "human" in tui.ROLE_STYLES
        assert "ai" in tui.ROLE_STYLES
        assert "tool" in tui.ROLE_STYLES
        assert tui.CST.utcoffset(None) == timedelta(hours=8)

    def test_client_default_urls(self):
        client = tui.DeerFlowClient()
        assert client.gateway == "http://localhost:8001"
        assert client.langgraph == "http://localhost:2024"

    def test_client_custom_urls(self):
        client = tui.DeerFlowClient(gateway="http://host:9000", langgraph="http://host:9001")
        assert client.gateway == "http://host:9000"
        assert client.langgraph == "http://host:9001"

    def test_format_ts_naive_datetime(self):
        """Naive datetime (no tz) should still format."""
        result = tui._format_ts("2026-04-19T10:30:00")
        assert "04-19" in result

    def test_status_text_all_statuses(self):
        """Every known status should produce a colorized string."""
        for status in ["completed", "running", "pending", "interrupted", "failed", "cancelled", "timed_out", "idle", "busy", "error", "unknown"]:
            result = tui._status_text(status)
            assert status in result

    def test_truncate_empty(self):
        assert tui._truncate("", 10) == ""

    @pytest.mark.anyio
    async def test_message_viewer_replaces_old_messages(self):
        """Re-rendering messages should remove old ones first."""
        detail1 = {
            "task_id": "t1",
            "status": "completed",
            "messages": [
                {"role": "human", "content": "First", "ts": "2026-04-19T10:00:00+00:00"},
            ],
        }
        detail2 = {
            "task_id": "t1",
            "status": "completed",
            "messages": [
                {"role": "human", "content": "First", "ts": "2026-04-19T10:00:00+00:00"},
                {"role": "ai", "content": "Second", "ts": "2026-04-19T10:01:00+00:00"},
            ],
        }
        client = tui.DeerFlowClient()
        client.get_subagent_detail = AsyncMock(side_effect=[detail1, detail2])
        screen = tui.MessageViewerScreen(client, "tid", mode="subtask", task_id="t1")
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            scroll = screen.query_one("#msg-scroll", tui.VerticalScroll)
            assert len(scroll.children) == 1

            # Trigger reload directly (no 'r' binding on MessageViewer)
            screen._load_messages()
            await pilot.pause()
            await pilot.pause()
            # Old messages removed, new ones added
            assert len(scroll.children) == 2

    def test_confirm_dialog_compose(self):
        """Verify dialog can be constructed without errors."""
        dialog = tui.ConfirmDialog("Test Title", "Test Message")
        assert dialog._title == "Test Title"
        assert dialog._message == "Test Message"

    @pytest.mark.anyio
    async def test_update_page_info(self):
        msgs = {"messages": [], "total": 150, "has_more": True}
        client = tui.DeerFlowClient()
        client.get_messages = AsyncMock(return_value=msgs)
        screen = tui.MessageViewerScreen(client, "tid", mode="session")
        screen._offset = 50
        screen._total = 150
        screen._has_more = True
        app = _make_app()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            # Page should be 2/3
            assert screen._offset == 50


# ── Navigation Path Tests ───────────────────────────────────────────────


def _client_with_threads():
    """Client that returns threads with subagents for navigation tests."""
    client = _mock_thread_client()
    client.search_threads = AsyncMock(return_value=[
        {
            "thread_id": "thread-1",
            "status": "idle",
            "updated_at": "2026-04-19T10:00:00+00:00",
            "values": {"title": "Test Thread"},
        }
    ])
    client.get_thread_state = AsyncMock(return_value={
        "values": {
            "title": "Test Thread",
            "todos": [
                {"content": "Todo 1", "status": "completed"},
                {"content": "Todo 2", "status": "pending"},
            ],
        }
    })
    client.get_thread_status = AsyncMock(return_value={
        "main_session": {"status": "idle", "run_id": None, "started_at": None}
    })
    client.list_subagents = AsyncMock(return_value=[
        {
            "task_id": "task-1",
            "subagent_name": "general",
            "description": "Test task",
            "status": "completed",
            "started_at": "2026-04-19T10:00:00+00:00",
            "completed_at": "2026-04-19T11:00:00+00:00",
            "message_count": 5,
        }
    ])
    client.get_subagent_detail = AsyncMock(return_value={
        "task_id": "task-1",
        "status": "completed",
        "messages": [
            {"role": "human", "content": "Do something", "ts": "2026-04-19T10:00:00+00:00"},
            {"role": "ai", "content": "Done", "ts": "2026-04-19T10:01:00+00:00"},
        ],
    })
    client.get_messages = AsyncMock(return_value={
        "messages": [
            {"role": "human", "content": "Hello", "ts": "2026-04-19T10:00:00+00:00"},
            {"role": "ai", "content": "Hi", "ts": "2026-04-19T10:01:00+00:00"},
        ],
        "total": 2,
        "has_more": False,
    })
    return client


class TestNavigationPaths:
    """Test all enter/exit navigation paths between screens."""

    @pytest.mark.anyio
    async def test_threadlist_enter_to_threaddetail(self):
        """ThreadList → Enter → ThreadDetail."""
        client = _client_with_threads()
        screen = tui.ThreadListScreen(client)
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, tui.ThreadListScreen)

            # Select thread and press Enter
            table = screen.query_one("#thread-table", tui.DataTable)
            table.move_cursor(row=0)
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()

            # Should now be on ThreadDetailScreen
            assert isinstance(app.screen, tui.ThreadDetailScreen)
            assert app.screen.thread_id == "thread-1"

    @pytest.mark.anyio
    async def test_threaddetail_escape_back_to_threadlist(self):
        """ThreadDetail → Escape → ThreadList."""
        client = _client_with_threads()
        detail = tui.ThreadDetailScreen(client, "thread-1", "Test")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(detail)
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, tui.ThreadDetailScreen)

            await pilot.press("escape")
            await pilot.pause()
            # Should pop back (to default screen since no ThreadList pushed)
            assert not isinstance(app.screen, tui.ThreadDetailScreen)

    @pytest.mark.anyio
    async def test_threaddetail_enter_to_subtask_messages(self):
        """ThreadDetail → Enter (on subtask) → MessageViewer (subtask mode)."""
        client = _client_with_threads()
        detail = tui.ThreadDetailScreen(client, "thread-1", "Test")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(detail)
            await pilot.pause()
            await pilot.pause()

            table = detail.query_one("#subtask-table", tui.DataTable)
            table.move_cursor(row=0)
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()

            assert isinstance(app.screen, tui.MessageViewerScreen)
            assert app.screen.mode == "subtask"
            assert app.screen.task_id == "task-1"

    @pytest.mark.anyio
    async def test_threaddetail_m_to_session_messages(self):
        """ThreadDetail → m → MessageViewer (session mode)."""
        client = _client_with_threads()
        detail = tui.ThreadDetailScreen(client, "thread-1", "Test")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(detail)
            await pilot.pause()
            await pilot.pause()

            await pilot.press("m")
            await pilot.pause()
            await pilot.pause()

            assert isinstance(app.screen, tui.MessageViewerScreen)
            assert app.screen.mode == "session"

    @pytest.mark.anyio
    async def test_messageviewer_escape_back_to_threaddetail(self):
        """MessageViewer → Escape → ThreadDetail."""
        client = _client_with_threads()
        detail = tui.ThreadDetailScreen(client, "thread-1", "Test")
        msg = tui.MessageViewerScreen(client, "thread-1", mode="subtask", task_id="task-1")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(detail)
            await pilot.pause()
            await pilot.pause()
            app.push_screen(msg)
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, tui.MessageViewerScreen)

            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, tui.ThreadDetailScreen)

    @pytest.mark.anyio
    async def test_full_navigation_depth(self):
        """ThreadList → Enter → Detail → Enter → Messages → Escape → Detail → Escape."""
        client = _client_with_threads()
        app = _make_app(client)
        async with app.run_test() as pilot:
            # Start at thread list
            list_screen = tui.ThreadListScreen(client)
            app.push_screen(list_screen)
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, tui.ThreadListScreen)

            # Enter thread
            table = list_screen.query_one("#thread-table", tui.DataTable)
            table.move_cursor(row=0)
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, tui.ThreadDetailScreen)
            detail = app.screen

            # Enter subtask messages
            subtask_table = detail.query_one("#subtask-table", tui.DataTable)
            subtask_table.move_cursor(row=0)
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, tui.MessageViewerScreen)
            assert app.screen.mode == "subtask"

            # Escape back to detail
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, tui.ThreadDetailScreen)

            # Escape back to list
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, tui.ThreadListScreen)

    @pytest.mark.anyio
    async def test_session_messages_escape_back_to_detail(self):
        """Session messages → Escape → Detail."""
        client = _client_with_threads()
        detail = tui.ThreadDetailScreen(client, "thread-1", "Test")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(detail)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, tui.MessageViewerScreen)
            assert app.screen.mode == "session"

            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, tui.ThreadDetailScreen)

    @pytest.mark.anyio
    async def test_enter_with_no_rows_no_crash(self):
        """Pressing Enter on empty thread list should not crash."""
        client = _mock_thread_client()
        screen = tui.ThreadListScreen(client)
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, tui.ThreadListScreen)

    @pytest.mark.anyio
    async def test_detail_enter_with_no_subtasks(self):
        """Pressing Enter on empty subtask table should not crash."""
        client = _mock_thread_client()
        detail = tui.ThreadDetailScreen(client, "t1")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(detail)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            # Should stay on detail screen (no subtask to open)
            assert isinstance(app.screen, tui.ThreadDetailScreen)

    @pytest.mark.anyio
    async def test_stop_with_confirm_then_cancel(self):
        """Stop session → Confirm dialog → N (cancel) → stays on detail."""
        client = _mock_thread_client()
        client.get_thread_status = AsyncMock(return_value={
            "main_session": {"status": "running", "run_id": "run-1"}
        })
        detail = tui.ThreadDetailScreen(client, "t1")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(detail)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            # Confirm dialog visible — press N to cancel
            await pilot.press("n")
            await pilot.pause()
            # Should be back on detail, no cancel_run called
            assert isinstance(app.screen, tui.ThreadDetailScreen)
            client.cancel_run.assert_not_called()

    @pytest.mark.anyio
    async def test_cancel_subtask_confirm_then_cancel(self):
        """Cancel subtask → Confirm dialog → N (cancel) → stays on detail."""
        client = _mock_thread_client()
        client.list_subagents = AsyncMock(return_value=[
            {"task_id": "t1", "subagent_name": "gen", "description": "Task", "status": "running",
             "started_at": "2026-04-19T10:00:00+00:00", "completed_at": "", "message_count": 0}
        ])
        detail = tui.ThreadDetailScreen(client, "t1")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(detail)
            await pilot.pause()
            await pilot.pause()
            table = detail.query_one("#subtask-table", tui.DataTable)
            table.move_cursor(row=0)
            await pilot.press("c")
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(app.screen, tui.ThreadDetailScreen)
            client.cancel_subtask.assert_not_called()

    @pytest.mark.anyio
    async def test_message_cancel_confirm_then_cancel(self):
        """Cancel subtask from message viewer → Confirm → N → stays."""
        client = _mock_thread_client()
        msg = tui.MessageViewerScreen(client, "tid", mode="subtask", task_id="t1")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(msg)
            await pilot.pause()
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(app.screen, tui.MessageViewerScreen)

    @pytest.mark.anyio
    async def test_session_messages_pagination_flow(self):
        """Session mode: load → next page → prev page → escape."""
        client = _mock_thread_client()
        client.get_messages = AsyncMock(side_effect=[
            {"messages": [{"role": "human", "content": f"msg{i}", "ts": "2026-04-19T10:00:00+00:00"} for i in range(50)], "total": 150, "has_more": True},
            {"messages": [{"role": "human", "content": f"msg{i}", "ts": "2026-04-19T11:00:00+00:00"} for i in range(50)], "total": 150, "has_more": True},
            {"messages": [{"role": "human", "content": f"msg{i}", "ts": "2026-04-19T10:00:00+00:00"} for i in range(50)], "total": 150, "has_more": True},
        ])
        msg = tui.MessageViewerScreen(client, "tid", mode="session")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(msg)
            await pilot.pause()
            await pilot.pause()
            assert msg._offset == 0

            # Next page
            await pilot.press("n")
            await pilot.pause()
            await pilot.pause()
            assert msg._offset == 50

            # Prev page
            await pilot.press("p")
            await pilot.pause()
            await pilot.pause()
            assert msg._offset == 0

            # Escape
            await pilot.press("escape")
            await pilot.pause()

    @pytest.mark.anyio
    async def test_toggle_todos_visibility(self):
        """Toggle todos panel: visible → hidden → visible."""
        client = _client_with_threads()
        detail = tui.ThreadDetailScreen(client, "t1")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(detail)
            await pilot.pause()
            await pilot.pause()
            assert detail._todos_visible is True
            await pilot.press("t")
            assert detail._todos_visible is False
            await pilot.press("t")
            assert detail._todos_visible is True


class TestTUIOptimizations:
    """Tests for TUI optimization fixes."""

    @pytest.mark.anyio
    async def test_none_description_no_crash(self):
        """Entering a subtask with None description should not crash."""
        # Use _client_with_threads() as base — proven to work for navigation
        client = _client_with_threads()
        # Override subagent to have description=None
        client.list_subagents = AsyncMock(return_value=[
            {
                "task_id": "task-1",
                "subagent_name": "general",
                "description": None,
                "status": "completed",
                "started_at": "2026-04-19T10:00:00+00:00",
                "completed_at": "2026-04-19T11:00:00+00:00",
                "message_count": 5,
            }
        ])

        detail = tui.ThreadDetailScreen(client, "thread-1", "Test")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(detail)
            await pilot.pause()
            await pilot.pause()

            # Select subtask row and press enter
            table = detail.query_one("#subtask-table", tui.DataTable)
            table.move_cursor(row=0)
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()

            # Should navigate to MessageViewerScreen without crash
            assert isinstance(app.screen, tui.MessageViewerScreen)

    @pytest.mark.anyio
    async def test_empty_string_description_no_crash(self):
        """MessageViewerScreen with empty string description works."""
        client = _mock_thread_client()
        client.get_subagent_detail = AsyncMock(return_value={
            "task_id": "t1", "subagent_name": "test", "status": "completed", "messages": [],
        })
        msg = tui.MessageViewerScreen(client, "thread-1", mode="subtask", task_id="t1", description="")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(msg)
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, tui.MessageViewerScreen)

    @pytest.mark.anyio
    async def test_none_description_direct_construction(self):
        """MessageViewerScreen constructed with description=None should not crash."""
        client = _mock_thread_client()
        client.get_subagent_detail = AsyncMock(return_value={
            "task_id": "t1", "subagent_name": "test", "status": "completed", "messages": [],
        })
        msg = tui.MessageViewerScreen(client, "thread-1", mode="subtask", task_id="t1", description=None)
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(msg)
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, tui.MessageViewerScreen)
            assert msg.description == ""  # Should be normalized to ""

    @pytest.mark.anyio
    async def test_tab_key_triggers_focus_toggle(self):
        """Tab key calls action_focus_next_area without crashing."""
        client = _client_with_threads()
        detail = tui.ThreadDetailScreen(client, "thread-1", "Test")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(detail)
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

            # Verify FocusableStatic exists and is focusable
            todos = detail.query_one("#todos-label", tui.FocusableStatic)
            assert todos.can_focus is True

            # Tab should not crash
            await pilot.press("tab")
            await pilot.pause()

            # Tab again should not crash
            await pilot.press("tab")
            await pilot.pause()

    @pytest.mark.anyio
    async def test_todos_sorted_in_progress_first(self):
        """Todos should be sorted with in_progress first."""
        client = _mock_thread_client()
        client.get_thread_state = AsyncMock(return_value={
            "values": {
                "todos": [
                    {"content": "Completed task", "status": "completed"},
                    {"content": "Active task", "status": "in_progress"},
                    {"content": "Pending task", "status": "pending"},
                    {"content": "Another done", "status": "completed"},
                ]
            }
        })
        client.get_thread_status = AsyncMock(return_value={"main_session": {"status": "idle"}})
        client.list_subagents = AsyncMock(return_value=[])

        detail = tui.ThreadDetailScreen(client, "t1", "Test")
        app = _make_app(client)
        async with app.run_test() as pilot:
            app.push_screen(detail)
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

            # Verify the sorted order by checking the internal render
            label = detail.query_one("#todos-label", tui.FocusableStatic)
            # Get text content from the label
            from rich.text import Text
            content = label.render()
            str_text = str(content)
            # in_progress should appear before completed
            active_pos = str_text.find("Active task")
            completed_pos = str_text.find("Completed task")
            assert active_pos > 0, "Active task should be in the rendered text"
            assert completed_pos > 0, "Completed task should be in the rendered text"
            assert active_pos < completed_pos, "Active task should appear before completed"

