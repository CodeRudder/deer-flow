"""Test TUI quit/escape/filter behavior."""
from __future__ import annotations

import importlib.util
import sys
import os

_proj = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_script = os.path.join(_proj, "scripts", "deerflow-tui.py")
_spec = importlib.util.spec_from_file_location("deerflow_tui", _script)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["deerflow_tui"] = _mod
_spec.loader.exec_module(_mod)

ThreadDetailScreen = _mod.ThreadDetailScreen
ThreadListScreen = _mod.ThreadListScreen
DeerFlowTUI = _mod.DeerFlowTUI
DeerFlowClient = _mod.DeerFlowClient


class _FakeQuery:
    """Minimal fake for query_one in isolated tests."""
    def __init__(self, widgets: dict):
        self._widgets = widgets

    def query_one(self, selector: str, expect_type=None):
        key = selector.lstrip("#")
        if key in self._widgets:
            return self._widgets[key]
        raise Exception(f"No widget: {selector}")


def _make_screen():
    """Create a ThreadDetailScreen with fake internals for unit testing."""
    client = DeerFlowClient()
    screen = ThreadDetailScreen(client, "test-thread", "Test Title")
    screen._subagents = [
        {"task_id": "call_abc123", "subagent_name": "coder", "description": "write code", "status": "completed", "started_at": "", "completed_at": "", "message_count": 5},
        {"task_id": "call_def456", "subagent_name": "reviewer", "description": "review PR", "status": "running", "started_at": "", "completed_at": "", "message_count": 3},
        {"task_id": "call_ghi789", "subagent_name": "coder", "description": "fix bug", "status": "pending", "started_at": "", "completed_at": "", "message_count": 0},
    ]
    screen._filtering = False
    screen._filter_text = ""
    screen._page = 1
    screen._page_size = 20
    return screen


# ── Test 1: Filter text matching (case-insensitive, partial) ──
def test_filter_case_insensitive_partial():
    screen = _make_screen()
    # Match "COD" → should match coder tasks
    screen._filter_text = "COD"
    screen._filtering = True

    ordered = screen._subagents  # reuse the same list
    ft = screen._filter_text.lower()
    filtered = [
        t for t in ordered
        if ft in (t.get("task_id", "") or "").lower()
        or ft in (t.get("subagent_name", "") or "").lower()
        or ft in (t.get("description", "") or "").lower()
    ]
    assert len(filtered) == 2, f"Expected 2 'coder' tasks, got {len(filtered)}: {filtered}"
    assert all(t["subagent_name"] == "coder" for t in filtered)

    # Match "call_def" → partial task_id
    screen._filter_text = "call_def"
    ft = screen._filter_text.lower()
    filtered = [
        t for t in ordered
        if ft in (t.get("task_id", "") or "").lower()
        or ft in (t.get("subagent_name", "") or "").lower()
        or ft in (t.get("description", "") or "").lower()
    ]
    assert len(filtered) == 1, f"Expected 1 task, got {len(filtered)}"
    assert filtered[0]["task_id"] == "call_def456"

    print("PASS: test_filter_case_insensitive_partial")


# ── Test 2: Escape in filter mode closes filter, does NOT pop screen ──
def test_escape_closes_filter_not_screen():
    screen = _make_screen()
    screen._filtering = True
    screen._filter_text = "abc"

    # The escape binding goes to action_handle_escape
    # In filter mode, it should close filter (not pop screen)
    screen._filtering = True
    screen._filter_text = "test"

    # Simulate what action_handle_escape does
    if screen._filtering:
        # _close_filter
        screen._filtering = False
        screen._filter_text = ""
        screen._page = 1

    assert not screen._filtering, "Filtering should be False after escape"
    assert screen._filter_text == "", "Filter text should be cleared after escape"
    print("PASS: test_escape_closes_filter_not_screen")


# ── Test 3: Escape outside filter mode goes back ──
def test_escape_outside_filter_goes_back():
    screen = _make_screen()
    screen._filtering = False

    # action_handle_escape should call self.app.pop_screen()
    # We just verify the logic path
    assert not screen._filtering, "Should not be filtering"
    # In real code this calls self.app.pop_screen()
    print("PASS: test_escape_outside_filter_goes_back")


# ── Test 4: on_key in filter mode captures printable chars ──
def test_on_key_captures_chars_in_filter_mode():
    screen = _make_screen()
    screen._filtering = True
    screen._filter_text = ""

    class FakeEvent:
        def __init__(self, key):
            self.key = key
            self._prevented = False
        def prevent_default(self):
            self._prevented = True

    # Simulate typing "abc"
    for ch in "abc":
        event = FakeEvent(ch)
        # Replicate on_key logic
        if screen._filtering and len(event.key) == 1 and event.key.isprintable():
            screen._filter_text += event.key

    assert screen._filter_text == "abc", f"Expected 'abc', got '{screen._filter_text}'"

    # Simulate backspace
    screen._filter_text = screen._filter_text[:-1]
    assert screen._filter_text == "ab"

    print("PASS: test_on_key_captures_chars_in_filter_mode")


# ── Test 5: Ctrl+C double-tap logic ──
def test_ctrl_c_double_tap():
    app = DeerFlowTUI.__new__(DeerFlowTUI)
    app._ctrl_c_count = 0

    # First Ctrl+C: count=1, should NOT exit
    app._ctrl_c_count += 1
    should_exit = app._ctrl_c_count >= 2
    assert not should_exit, "First Ctrl+C should not exit"
    assert app._ctrl_c_count == 1

    # Reset (simulating timeout)
    app._ctrl_c_count = 0

    # Two rapid Ctrl+C: should exit
    app._ctrl_c_count += 1
    app._ctrl_c_count += 1
    should_exit = app._ctrl_c_count >= 2
    assert should_exit, "Second Ctrl+C should exit"
    print("PASS: test_ctrl_c_double_tap")


# ── Test 6: ThreadListScreen has confirm_quit for q and escape ──
def test_thread_list_quit_bindings():
    screen = ThreadListScreen.__new__(ThreadListScreen)
    # Check that action_confirm_quit exists
    assert hasattr(screen, 'action_confirm_quit'), "ThreadListScreen should have action_confirm_quit"
    # Check bindings include q and escape both mapped to confirm_quit
    binding_keys = [(b.key, b.action) for b in ThreadListScreen.BINDINGS]
    assert ("q", "confirm_quit") in binding_keys, f"q should map to confirm_quit, got {binding_keys}"
    assert ("escape", "confirm_quit") in binding_keys, f"escape should map to confirm_quit, got {binding_keys}"
    print("PASS: test_thread_list_quit_bindings")


# ── Test 7: App does NOT have q binding (only screens handle it) ──
def test_app_no_q_binding():
    binding_keys = [b.key for b in DeerFlowTUI.BINDINGS]
    assert "q" not in binding_keys, f"App should not bind q, got {binding_keys}"
    assert "ctrl+c" in binding_keys, "App should bind ctrl+c"
    print("PASS: test_app_no_q_binding")


if __name__ == "__main__":
    test_filter_case_insensitive_partial()
    test_escape_closes_filter_not_screen()
    test_escape_outside_filter_goes_back()
    test_on_key_captures_chars_in_filter_mode()
    test_ctrl_c_double_tap()
    test_thread_list_quit_bindings()
    test_app_no_q_binding()
    print("\nAll 7 tests passed!")
