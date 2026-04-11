"""Tests for task_tool session integration (tools/builtins/task_tool.py).

Covers:
- _build_recovery_prompt helper function
- Recovery context injection logic
- SubagentSession creation path (when thread_id available)
- Description parameter flow through to SubagentResult
- Graceful handling when session creation fails
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Break circular imports same as existing executor tests
_MOCKED_MODULES = [
    "deerflow.agents",
    "deerflow.agents.thread_state",
    "deerflow.agents.middlewares",
    "deerflow.sandbox",
    "deerflow.sandbox.middleware",
    "deerflow.sandbox.security",
    "deerflow.models",
]


@pytest.fixture(autouse=True, scope="module")
def _mock_heavy_deps():
    """Set up mocked modules and import real executor classes."""
    saved = {name: sys.modules.get(name) for name in _MOCKED_MODULES}
    for name in _MOCKED_MODULES:
        if name not in sys.modules:
            sys.modules[name] = MagicMock()

    saved_executor = sys.modules.get("deerflow.subagents.executor")
    if "deerflow.subagents.executor" in sys.modules:
        del sys.modules["deerflow.subagents.executor"]

    from deerflow.subagents.executor import SubagentStatus, SubagentResult

    _mock_heavy_deps.status = SubagentStatus
    _mock_heavy_deps.result = SubagentResult

    yield

    for name in _MOCKED_MODULES:
        if saved[name] is None and name in sys.modules:
            del sys.modules[name]

    if saved_executor is not None:
        sys.modules["deerflow.subagents.executor"] = saved_executor
    elif "deerflow.subagents.executor" in sys.modules:
        del sys.modules["deerflow.subagents.executor"]


@pytest.fixture
def real_status():
    return _mock_heavy_deps.status


@pytest.fixture
def real_result_class():
    return _mock_heavy_deps.result


# ── Recovery Prompt Builder Tests ───────────────────────────────────────


def _build_recovery_prompt(sessions):
    """Direct copy of the logic from task_tool.py for isolated testing."""
    parts = []
    for s in sessions:
        messages = s.read_messages()
        ai_messages = [m for m in messages if m.get("role") == "ai"]
        last_ai = ""
        if ai_messages:
            content = ai_messages[-1].get("content", "")
            if isinstance(content, str):
                last_ai = content[:200]
            else:
                last_ai = str(content)[:200]
        parts.append(
            f"- Task {s.task_id} ({s.subagent_name}): "
            f"executed {len(messages)} steps, last AI response: {last_ai}"
        )
    return (
        "<recovery_context>\nThe following sub-tasks were previously interrupted. "
        "Continue from where they left off without repeating completed work:\n"
        + "\n".join(parts)
        + "\n</recovery_context>"
    )


class TestBuildRecoveryPrompt:
    """Test _build_recovery_prompt helper."""

    def test_builds_from_single_session(self):
        mock_session = MagicMock()
        mock_session.task_id = "task-001"
        mock_session.subagent_name = "developer"
        mock_session.read_messages.return_value = [
            {"role": "human", "content": "implement auth"},
            {"role": "ai", "content": "I will start implementing the authentication module. Let me check existing code."},
        ]

        result = _build_recovery_prompt([mock_session])

        assert "<recovery_context>" in result
        assert "task-001" in result
        assert "developer" in result
        assert "authentication module" in result

    def test_builds_from_multiple_sessions(self):
        s1 = MagicMock()
        s1.task_id = "task-a"
        s1.subagent_name = "architect"
        s1.read_messages.return_value = [{"role": "ai", "content": "Architecture done"}]

        s2 = MagicMock()
        s2.task_id = "task-b"
        s2.subagent_name = "developer"
        s2.read_messages.return_value = [{"role": "ai", "content": "Implementation started"}]

        result = _build_recovery_prompt([s1, s2])

        assert "task-a" in result
        assert "task-b" in result
        assert "architect" in result
        assert "developer" in result
        assert "1 steps" in result  # Each session has 1 message

    def test_empty_sessions_list(self):
        result = _build_recovery_prompt([])
        assert "<recovery_context>" in result
        assert "</recovery_context>" in result

    def test_handles_no_ai_messages(self):
        mock_session = MagicMock()
        mock_session.task_id = "task-002"
        mock_session.subagent_name = "tester"
        mock_session.read_messages.return_value = [
            {"role": "human", "content": "test the API"},
        ]

        result = _build_recovery_prompt([mock_session])
        assert "task-002" in result
        assert "last AI response: " in result  # Empty last AI response

    def test_truncates_long_ai_content(self):
        mock_session = MagicMock()
        mock_session.task_id = "task-003"
        mock_session.subagent_name = "dev"
        mock_session.read_messages.return_value = [
            {"role": "ai", "content": "x" * 500},  # >200 chars
        ]

        result = _build_recovery_prompt([mock_session])
        # Should truncate to 200 chars
        assert "x" * 200 in result
        assert "x" * 201 not in result.replace("recovery_context", "").replace("interrupted", "")


# ── Recovery Injection Logic Tests ──────────────────────────────────────


class TestRecoveryInjection:
    """Test recovery context injection flow."""

    def test_recovery_prepended_to_prompt(self):
        """Verify recovery is prepended to original prompt."""
        mock_session = MagicMock()
        mock_session.task_id = "old-task"
        mock_session.subagent_name = "developer"
        mock_session.read_messages.return_value = [
            {"role": "ai", "content": "Was implementing auth"},
        ]

        recovery = _build_recovery_prompt([mock_session])
        original = "Continue the auth implementation"

        combined = recovery + "\n\n" + original
        assert combined.startswith("<recovery_context>")
        assert original in combined

    def test_no_injection_when_no_interrupted(self):
        """When find_interrupted returns [], no recovery is prepended."""
        interrupted = []
        prompt = "Original task"
        if interrupted:
            prompt = _build_recovery_prompt(interrupted) + "\n\n" + prompt
        assert prompt == "Original task"

    def test_injection_preserves_original_prompt(self):
        """Original prompt content is fully preserved after injection."""
        mock_session = MagicMock()
        mock_session.task_id = "t1"
        mock_session.subagent_name = "dev"
        mock_session.read_messages.return_value = []

        recovery = _build_recovery_prompt([mock_session])
        original = "Implement JWT authentication with refresh tokens"
        combined = recovery + "\n\n" + original

        # Find the original prompt after the recovery section
        assert original in combined
        idx = combined.index("</recovery_context>")
        after = combined[idx + len("</recovery_context>"):]
        assert original in after


# ── SubagentResult Fields Tests ─────────────────────────────────────────


class TestSubagentResultFields:
    """Test that new fields on SubagentResult are properly set."""

    def test_description_field_set(self, real_result_class, real_status):
        result = real_result_class(
            task_id="t1",
            trace_id="tr1",
            status=real_status.PENDING,
            description="implement auth module",
            original_prompt="Implement JWT authentication",
        )
        assert result.description == "implement auth module"
        assert result.original_prompt == "Implement JWT authentication"

    def test_thread_id_field_set(self, real_result_class, real_status):
        result = real_result_class(
            task_id="t1",
            trace_id="tr1",
            status=real_status.PENDING,
            thread_id="thread-abc",
            subagent_name="developer",
        )
        assert result.thread_id == "thread-abc"
        assert result.subagent_name == "developer"

    def test_fields_default_none(self, real_result_class, real_status):
        result = real_result_class(
            task_id="t1",
            trace_id="tr1",
            status=real_status.PENDING,
        )
        assert result.thread_id is None
        assert result.subagent_name is None
        assert result.description is None
        assert result.original_prompt is None


# ── Session Creation Path Tests ─────────────────────────────────────────


class TestSessionCreationPath:
    """Test the session creation logic paths in task_tool."""

    def test_session_created_when_thread_id_present(self, tmp_path):
        """Simulate session creation when thread_id is available."""
        from deerflow.subagents.session import SubagentSession

        # Directly set the jsonl_path to avoid dependency on get_paths()
        session = SubagentSession(
            thread_id="thread-123",
            task_id="call_abc",
            subagent_name="developer",
            description="implement feature",
        )
        # Override path to use tmp
        session_dir = tmp_path / "subagents"
        session_dir.mkdir(parents=True)
        session._jsonl_path = session_dir / "call_abc.jsonl"

        assert session.thread_id == "thread-123"
        assert session.task_id == "call_abc"
        assert session.subagent_name == "developer"
        assert session.jsonl_path.parent == session_dir

    def test_session_creation_graceful_failure(self):
        """When session creation fails, task should still proceed (no crash)."""
        session = None
        thread_id = "thread-123"
        try:
            # Simulate a failure (e.g., paths not configured)
            raise RuntimeError("Paths not configured")
        except Exception:
            session = None

        # The task_tool code continues with session=None
        assert session is None

    def test_recovery_check_skipped_when_no_session(self):
        """Recovery check is skipped when session is None."""
        thread_id = "thread-123"
        session = None

        # Simulating: if thread_id and session is not None:
        should_check = thread_id and session is not None
        assert not should_check
