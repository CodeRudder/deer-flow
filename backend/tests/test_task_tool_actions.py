"""Tests for task_tool action dispatch: cancel, query, resume."""

import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from deerflow.subagents.config import SubagentConfig

# Re-import the module so monkeypatch targets the correct object.
task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


# ---------------------------------------------------------------------------
# Fake SubagentStatus — matches production enum values
# ---------------------------------------------------------------------------


class FakeSubagentStatus:
    PENDING = SimpleNamespace(value="pending")
    RUNNING = SimpleNamespace(value="running")
    COMPLETED = SimpleNamespace(value="completed")
    FAILED = SimpleNamespace(value="failed")
    CANCELLED = SimpleNamespace(value="cancelled")
    TIMED_OUT = SimpleNamespace(value="timed_out")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runtime(**overrides) -> SimpleNamespace:
    base = SimpleNamespace(
        state={
            "sandbox": {"sandbox_id": "local"},
            "thread_data": {
                "workspace_path": "/tmp/workspace",
                "uploads_path": "/tmp/uploads",
                "outputs_path": "/tmp/outputs",
            },
        },
        context={"thread_id": "thread-1"},
        config={"metadata": {"model_name": "test-model", "trace_id": "trace-1"}},
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _make_subagent_config() -> SubagentConfig:
    return SubagentConfig(
        name="general-purpose",
        description="General helper",
        system_prompt="Base system prompt",
        max_turns=50,
        timeout_seconds=10,
    )


def _run_task_tool(**kwargs) -> str:
    coroutine = getattr(task_tool_module.task_tool, "coroutine", None)
    if coroutine is not None:
        return asyncio.run(coroutine(**kwargs))
    return task_tool_module.task_tool.func(**kwargs)


async def _no_sleep(_: float) -> None:
    return None


def _patch_subagent_status(monkeypatch):
    """Patch SubagentStatus on the task_tool module for polling tests."""
    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)


# ---------------------------------------------------------------------------
# Cancel action tests
# ---------------------------------------------------------------------------


class TestActionCancel:
    """Test _action_cancel handler."""

    def test_cancel_requires_task_id(self):
        result = asyncio.run(task_tool_module._action_cancel(None))
        assert "Error" in result
        assert "task_id is required" in result

    def test_cancel_task_not_found(self, monkeypatch):
        monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda _: None)
        result = asyncio.run(task_tool_module._action_cancel("tc-missing"))
        assert "not found" in result

    def test_cancel_already_completed(self, monkeypatch):
        status = SimpleNamespace(value="completed")
        monkeypatch.setattr(
            task_tool_module,
            "get_background_task_result",
            lambda _: SimpleNamespace(status=status, result="done", error=None),
        )
        result = asyncio.run(task_tool_module._action_cancel("tc-done"))
        assert "cannot cancel" in result

    def test_cancel_running_task(self, monkeypatch):
        cancel_calls = []
        status = SimpleNamespace(value="running")
        monkeypatch.setattr(
            task_tool_module,
            "get_background_task_result",
            lambda _: SimpleNamespace(status=status, result=None, error=None),
        )
        monkeypatch.setattr(
            task_tool_module,
            "request_cancel_background_task",
            lambda tid: cancel_calls.append(tid),
        )
        result = asyncio.run(task_tool_module._action_cancel("tc-running"))
        assert "cancelled successfully" in result
        assert cancel_calls == ["tc-running"]

    def test_cancel_pending_task(self, monkeypatch):
        cancel_calls = []
        status = SimpleNamespace(value="pending")
        monkeypatch.setattr(
            task_tool_module,
            "get_background_task_result",
            lambda _: SimpleNamespace(status=status, result=None, error=None),
        )
        monkeypatch.setattr(
            task_tool_module,
            "request_cancel_background_task",
            lambda tid: cancel_calls.append(tid),
        )
        result = asyncio.run(task_tool_module._action_cancel("tc-pending"))
        assert "cancelled successfully" in result
        assert cancel_calls == ["tc-pending"]


# ---------------------------------------------------------------------------
# Query action tests
# ---------------------------------------------------------------------------


class TestActionQuery:
    """Test _action_query handler."""

    def test_query_requires_task_id(self):
        result = asyncio.run(task_tool_module._action_query(None))
        assert "Error" in result
        assert "task_id is required" in result

    def test_query_running_task_in_memory(self, monkeypatch):
        status = SimpleNamespace(value="running")
        monkeypatch.setattr(
            task_tool_module,
            "get_background_task_result",
            lambda _: SimpleNamespace(status=status, result=None, error=None, ai_messages=[]),
        )
        result = asyncio.run(task_tool_module._action_query("tc-running"))
        assert "status=running" in result

    def test_query_completed_task_in_memory(self, monkeypatch):
        status = SimpleNamespace(value="completed")
        monkeypatch.setattr(
            task_tool_module,
            "get_background_task_result",
            lambda _: SimpleNamespace(
                status=status,
                result="Build succeeded",
                error=None,
                ai_messages=[],
            ),
        )
        result = asyncio.run(task_tool_module._action_query("tc-done"))
        assert "status=completed" in result
        assert "Build succeeded" in result

    def test_query_failed_task_in_memory(self, monkeypatch):
        status = SimpleNamespace(value="failed")
        monkeypatch.setattr(
            task_tool_module,
            "get_background_task_result",
            lambda _: SimpleNamespace(
                status=status,
                result=None,
                error="Connection refused",
                ai_messages=[],
            ),
        )
        result = asyncio.run(task_tool_module._action_query("tc-failed"))
        assert "status=failed" in result
        assert "Connection refused" in result

    def test_query_task_not_found(self, monkeypatch):
        monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda _: None)
        # Also mock _find_thread_id_for_task to return None
        monkeypatch.setattr(task_tool_module, "_find_thread_id_for_task", lambda _: None)
        result = asyncio.run(task_tool_module._action_query("tc-gone"))
        assert "not found" in result

    def test_query_falls_back_to_disk(self, monkeypatch, tmp_path):
        """When task not in memory, try disk-based session lookup."""
        monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda _: None)
        monkeypatch.setattr(
            task_tool_module,
            "_find_thread_id_for_task",
            lambda _: "thread-1",
        )

        # Mock SubagentSession.get_resume_info
        mock_info = {
            "status": "interrupted",
            "subagent_type": "developer",
            "message_count": 5,
            "original_prompt": "fix the bug",
            "last_ai_content": "investigating...",
            "description": "Bug fix",
        }
        monkeypatch.setattr(
            task_tool_module.SubagentSession,
            "get_resume_info",
            staticmethod(lambda task_id, thread_id: mock_info),
        )

        result = asyncio.run(task_tool_module._action_query("tc-disk"))
        assert "status=interrupted" in result
        assert "subagent=developer" in result
        assert "steps=5" in result


# ---------------------------------------------------------------------------
# Resume action tests
# ---------------------------------------------------------------------------


class TestActionResume:
    """Test _action_resume handler."""

    def test_resume_requires_task_id(self):
        runtime = _make_runtime()
        result = asyncio.run(
            task_tool_module._action_resume(
                runtime=runtime,
                task_id=None,
                tool_call_id="tc-new",
                description="resume",
                prompt="continue",
                subagent_type="developer",
                max_turns=None,
            )
        )
        assert "Error" in result
        assert "task_id is required" in result

    def test_resume_requires_thread_id(self):
        runtime = SimpleNamespace(
            state={},
            context=None,
            config={"configurable": {}, "metadata": {}},
        )
        result = asyncio.run(
            task_tool_module._action_resume(
                runtime=runtime,
                task_id="tc-old",
                tool_call_id="tc-new",
                description="resume",
                prompt="continue",
                subagent_type="developer",
                max_turns=None,
            )
        )
        assert "Error" in result
        assert "Cannot determine thread_id" in result

    def test_resume_no_session_found(self, monkeypatch):
        runtime = _make_runtime()
        monkeypatch.setattr(
            task_tool_module.SubagentSession,
            "get_resume_info",
            staticmethod(lambda task_id, thread_id: None),
        )
        result = asyncio.run(
            task_tool_module._action_resume(
                runtime=runtime,
                task_id="tc-gone",
                tool_call_id="tc-new",
                description="resume",
                prompt="continue",
                subagent_type="developer",
                max_turns=None,
            )
        )
        assert "Error" in result
        assert "No session found" in result

    def test_resume_unknown_subagent_type(self, monkeypatch):
        runtime = _make_runtime()
        mock_info = {
            "status": "interrupted",
            "subagent_type": "nonexistent",
            "message_count": 3,
            "original_prompt": "do work",
            "last_ai_content": "halfway done",
            "description": "Old task",
        }
        monkeypatch.setattr(
            task_tool_module.SubagentSession,
            "get_resume_info",
            staticmethod(lambda task_id, thread_id: mock_info),
        )
        monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda: ["general-purpose"])
        monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: None)

        result = asyncio.run(
            task_tool_module._action_resume(
                runtime=runtime,
                task_id="tc-old",
                tool_call_id="tc-new",
                description="resume",
                prompt="continue",
                subagent_type="developer",
                max_turns=None,
            )
        )
        assert "Error" in result
        assert "Unknown subagent type" in result

    def test_resume_successful_execution(self, monkeypatch):
        """Test resume creates new executor and completes successfully."""
        config = _make_subagent_config()
        events = []
        captured = {}

        runtime = _make_runtime()

        mock_info = {
            "status": "interrupted",
            "subagent_type": "developer",
            "message_count": 5,
            "original_prompt": "Implement feature X",
            "last_ai_content": "Created module structure",
            "description": "Feature X",
        }

        # Mock SubagentSession.get_resume_info to return our mock info
        original_get_resume_info = task_tool_module.SubagentSession.get_resume_info
        monkeypatch.setattr(
            task_tool_module.SubagentSession,
            "get_resume_info",
            staticmethod(lambda task_id, thread_id: mock_info),
        )

        monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda: ["developer"])
        monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
        monkeypatch.setattr(task_tool_module, "get_skills_prompt_section", lambda: "")

        class DummyExecutor2:
            def __init__(self, **kwargs):
                captured["executor_kwargs"] = kwargs

            def execute_async(self, prompt, task_id=None, description=None):
                captured["prompt"] = prompt
                captured["task_id"] = task_id
                captured["description"] = description
                return task_id or "resumed-task"

        monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor2)
        _patch_subagent_status(monkeypatch)
        monkeypatch.setattr(
            task_tool_module,
            "get_background_task_result",
            lambda _: SimpleNamespace(
                status=FakeSubagentStatus.COMPLETED,
                result="Feature X complete",
                error=None,
                ai_messages=[{"id": "m1", "content": "resuming..."}],
            ),
        )
        monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
        monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
        monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
        monkeypatch.setattr(task_tool_module, "cleanup_background_task", lambda tid: None)

        result = asyncio.run(
            task_tool_module._action_resume(
                runtime=runtime,
                task_id="tc-old",
                tool_call_id="tc-new",
                description="Resume feature",
                prompt="continue",
                subagent_type="developer",
                max_turns=10,
            )
        )

        assert "Task Resumed" in result
        assert "Feature X complete" in result
        assert "recovery" in captured["prompt"].lower()
        assert "5 步" in captured["prompt"]  # message_count injected
        # Verify events
        event_types = [e["type"] for e in events]
        assert "task_started" in event_types
        assert "task_completed" in event_types

    def test_resume_uses_original_subagent_type(self, monkeypatch):
        """Resume should use the subagent_type from the original session."""
        config = _make_subagent_config()
        runtime = _make_runtime()
        captured = {}

        mock_info = {
            "status": "interrupted",
            "subagent_type": "architect",
            "message_count": 2,
            "original_prompt": "Design system",
            "last_ai_content": "Drafting...",
            "description": "Architecture",
        }

        monkeypatch.setattr(
            task_tool_module.SubagentSession,
            "get_resume_info",
            staticmethod(lambda task_id, thread_id: mock_info),
        )
        monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda: ["architect"])
        monkeypatch.setattr(task_tool_module, "get_skills_prompt_section", lambda: "")

        # Capture which subagent_type is looked up
        def track_config(name):
            captured["subagent_type"] = name
            return config

        monkeypatch.setattr(task_tool_module, "get_subagent_config", track_config)

        class DummyExecutor3:
            def __init__(self, **kwargs):
                pass

            def execute_async(self, prompt, task_id=None, description=None):
                return task_id or "tid"

        monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor3)
        _patch_subagent_status(monkeypatch)
        monkeypatch.setattr(
            task_tool_module,
            "get_background_task_result",
            lambda _: SimpleNamespace(
                status=FakeSubagentStatus.COMPLETED,
                result="done",
                error=None,
                ai_messages=[],
            ),
        )
        monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda e: None)
        monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
        monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
        monkeypatch.setattr(task_tool_module, "cleanup_background_task", lambda tid: None)

        asyncio.run(
            task_tool_module._action_resume(
                runtime=runtime,
                task_id="tc-old",
                tool_call_id="tc-new",
                description="resume",
                prompt="continue",
                subagent_type="general-purpose",  # Different from original
                max_turns=None,
            )
        )

        # Should use "architect" from session, not "general-purpose" from args
        assert captured["subagent_type"] == "architect"


# ---------------------------------------------------------------------------
# Action dispatch integration tests
# ---------------------------------------------------------------------------


class TestActionDispatch:
    """Test that task_tool dispatches to the correct action handler."""

    def test_create_action_is_default(self, monkeypatch):
        """Without action param, the tool should use create flow."""
        config = _make_subagent_config()
        events = []

        monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
        monkeypatch.setattr(task_tool_module, "get_skills_prompt_section", lambda: "")
        _patch_subagent_status(monkeypatch)

        monkeypatch.setattr(
            task_tool_module,
            "SubagentExecutor",
            type("E", (), {
                "__init__": lambda self, **kw: None,
                "execute_async": lambda self, p, task_id=None, description=None: task_id,
            }),
        )
        monkeypatch.setattr(
            task_tool_module,
            "get_background_task_result",
            lambda _: SimpleNamespace(status=FakeSubagentStatus.COMPLETED, result="done", error=None, ai_messages=[]),
        )
        monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
        monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
        monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
        monkeypatch.setattr(task_tool_module, "cleanup_background_task", lambda tid: None)

        # Call without action parameter (defaults to "create")
        result = _run_task_tool(
            runtime=_make_runtime(),
            description="test task",
            prompt="do work",
            subagent_type="general-purpose",
            tool_call_id="tc-create-1",
        )
        assert "Task Succeeded" in result

    def test_cancel_dispatch(self, monkeypatch):
        """action='cancel' should route to _action_cancel."""
        status = SimpleNamespace(value="running")
        monkeypatch.setattr(
            task_tool_module,
            "get_background_task_result",
            lambda _: SimpleNamespace(status=status, result=None, error=None),
        )
        monkeypatch.setattr(task_tool_module, "request_cancel_background_task", lambda _: None)

        result = _run_task_tool(
            runtime=None,
            description="cancel task",
            prompt="",
            subagent_type="general-purpose",
            tool_call_id="tc-disp-cancel",
            action="cancel",
            task_id="tc-target",
        )
        assert "cancelled successfully" in result

    def test_query_dispatch(self, monkeypatch):
        """action='query' should route to _action_query."""
        status = SimpleNamespace(value="completed")
        monkeypatch.setattr(
            task_tool_module,
            "get_background_task_result",
            lambda _: SimpleNamespace(status=status, result="done", error=None, ai_messages=[]),
        )

        result = _run_task_tool(
            runtime=None,
            description="query task",
            prompt="",
            subagent_type="general-purpose",
            tool_call_id="tc-disp-query",
            action="query",
            task_id="tc-target",
        )
        assert "status=completed" in result

    def test_resume_dispatch(self, monkeypatch):
        """action='resume' should route to _action_resume."""
        config = _make_subagent_config()
        events = []

        mock_info = {
            "status": "interrupted",
            "subagent_type": "general-purpose",
            "message_count": 1,
            "original_prompt": "do work",
            "last_ai_content": "started",
            "description": "test",
        }

        monkeypatch.setattr(
            task_tool_module.SubagentSession,
            "get_resume_info",
            staticmethod(lambda task_id, thread_id: mock_info),
        )

        monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda: ["general-purpose"])
        monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
        monkeypatch.setattr(task_tool_module, "get_skills_prompt_section", lambda: "")

        class DummyExec:
            def __init__(self, **kw):
                pass

            def execute_async(self, p, task_id=None, description=None):
                return task_id or "tid"

        monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExec)
        _patch_subagent_status(monkeypatch)
        monkeypatch.setattr(
            task_tool_module,
            "get_background_task_result",
            lambda _: SimpleNamespace(status=FakeSubagentStatus.COMPLETED, result="done", error=None, ai_messages=[]),
        )
        monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
        monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
        monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
        monkeypatch.setattr(task_tool_module, "cleanup_background_task", lambda tid: None)

        result = _run_task_tool(
            runtime=_make_runtime(),
            description="resume task",
            prompt="continue",
            subagent_type="general-purpose",
            tool_call_id="tc-disp-resume",
            action="resume",
            task_id="tc-old",
        )
        assert "Task Resumed" in result


# ---------------------------------------------------------------------------
# _find_thread_id_for_task tests
# ---------------------------------------------------------------------------


class TestFindThreadIdForTask:
    """Test _find_thread_id_for_task helper."""

    def test_finds_existing_task(self, tmp_path, monkeypatch):
        threads_dir = tmp_path / "threads"
        subagents = threads_dir / "thread-abc" / "subagents"
        subagents.mkdir(parents=True)
        (subagents / "tc-123.jsonl").write_text('{"ts":"t","role":"ai","content":"ok"}\n')

        mock_paths = MagicMock()
        mock_paths.base_dir = tmp_path
        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: mock_paths)

        result = task_tool_module._find_thread_id_for_task("tc-123")
        assert result == "thread-abc"

    def test_returns_none_for_unknown_task(self, tmp_path, monkeypatch):
        threads_dir = tmp_path / "threads"
        threads_dir.mkdir()

        mock_paths = MagicMock()
        mock_paths.base_dir = tmp_path
        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: mock_paths)

        result = task_tool_module._find_thread_id_for_task("tc-missing")
        assert result is None

    def test_returns_none_when_no_threads_dir(self, tmp_path, monkeypatch):
        mock_paths = MagicMock()
        mock_paths.base_dir = tmp_path
        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: mock_paths)

        result = task_tool_module._find_thread_id_for_task("tc-any")
        assert result is None


# ---------------------------------------------------------------------------
# Recovery prompt builder tests
# ---------------------------------------------------------------------------


class TestBuildRecoveryPrompt:
    """Test _build_recovery_prompt helper."""

    def test_builds_recovery_from_interrupted_sessions(self, tmp_path):
        from deerflow.subagents.session import SubagentSession

        # Create session directory
        d = tmp_path / "subagents"
        d.mkdir()

        # Write a JSONL with AI messages
        jsonl = d / "task-1.jsonl"
        lines = [
            json.dumps({"ts": "t", "role": "human", "content": "do work"}),
            json.dumps({"ts": "t", "role": "ai", "content": "Working on step 1"}),
            json.dumps({"ts": "t", "role": "ai", "content": "Step 2 complete"}),
        ]
        jsonl.write_text("\n".join(lines) + "\n")

        # Create session object with mocked paths
        session = SubagentSession.__new__(SubagentSession)
        session.thread_id = "thread-1"
        session.task_id = "task-1"
        session.subagent_name = "developer"
        session.description = "test"
        session.started_at = "t"
        session._jsonl_path = jsonl
        session._summary_path = d / "task-1.summary.json"

        result = task_tool_module._build_recovery_prompt([session])
        assert "<recovery_context>" in result
        assert "task-1" in result
        assert "developer" in result
        assert "3 steps" in result
        assert "Step 2 complete" in result

    def test_empty_sessions_list(self):
        result = task_tool_module._build_recovery_prompt([])
        assert "<recovery_context>" in result
        assert "</recovery_context>" in result
