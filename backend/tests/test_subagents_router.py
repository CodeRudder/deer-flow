"""Tests for subagents API router.

Covers:
- GET /api/threads/{thread_id}/subagents — list sessions
- GET /api/threads/{thread_id}/subagents/{task_id} — session detail
- POST /api/threads/{thread_id}/subagents/{task_id}/resume — resume subtask
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import subagents

router = subagents.router

# SubagentSession is lazy-imported inside route handlers, so we patch at the
# source module: deerflow.subagents.session.SubagentSession
SESSION_PATH = "deerflow.subagents.session.SubagentSession"
GET_CLIENT_PATH = "langgraph_sdk.get_client"


@pytest.fixture()
def app():
    _app = FastAPI()
    _app.include_router(router)
    return _app


@pytest.fixture()
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(task_id="task-1", subagent_name="developer", description="test", started_at="2026-01-01T00:00:00"):
    """Create a mock SubagentSession."""
    s = MagicMock()
    s.task_id = task_id
    s.subagent_name = subagent_name
    s.description = description
    s.started_at = started_at
    s.is_terminal = False
    return s


# ---------------------------------------------------------------------------
# GET /api/threads/{thread_id}/subagents
# ---------------------------------------------------------------------------


class TestListSessions:
    """Test list_subagent_sessions endpoint."""

    def test_returns_empty_list_when_no_sessions(self, client):
        with patch(SESSION_PATH) as MockSession:
            MockSession.list_sessions.return_value = []
            resp = client.get("/api/threads/thread-1/subagents")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_sessions_with_summaries(self, client):
        s1 = _make_session(task_id="task-a", subagent_name="developer")
        s1.read_summary.return_value = {
            "status": "completed",
            "started_at": "2026-01-01T00:00:00",
            "completed_at": "2026-01-01T00:01:00",
            "message_count": 10,
        }

        s2 = _make_session(task_id="task-b", subagent_name="architect")
        s2.read_summary.return_value = {
            "status": "failed",
            "started_at": "2026-01-01T00:00:00",
            "completed_at": "2026-01-01T00:02:00",
            "message_count": 5,
        }

        with patch(SESSION_PATH) as MockSession:
            MockSession.list_sessions.return_value = [s1, s2]
            resp = client.get("/api/threads/thread-1/subagents")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

        assert data[0]["task_id"] == "task-a"
        assert data[0]["subagent_name"] == "developer"
        assert data[0]["status"] == "completed"
        assert data[0]["message_count"] == 10

        assert data[1]["task_id"] == "task-b"
        assert data[1]["status"] == "failed"

    def test_returns_sessions_without_summaries(self, client):
        s1 = _make_session(task_id="task-running")
        s1.read_summary.return_value = None
        s1.is_terminal = False
        s1.read_messages.return_value = [{"role": "ai", "content": "working"}]

        with patch(SESSION_PATH) as MockSession:
            MockSession.list_sessions.return_value = [s1]
            resp = client.get("/api/threads/thread-1/subagents")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["task_id"] == "task-running"
        assert data[0]["status"] == "running"
        assert data[0]["message_count"] == 1

    def test_returns_unknown_status_for_terminal_without_summary(self, client):
        s1 = _make_session(task_id="task-old")
        s1.read_summary.return_value = None
        s1.is_terminal = True
        s1.read_messages.return_value = []

        with patch(SESSION_PATH) as MockSession:
            MockSession.list_sessions.return_value = [s1]
            resp = client.get("/api/threads/thread-1/subagents")

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["status"] == "unknown"

    def test_returns_empty_on_exception(self, client):
        with patch(SESSION_PATH) as MockSession:
            MockSession.list_sessions.side_effect = OSError("disk error")
            resp = client.get("/api/threads/thread-1/subagents")

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/threads/{thread_id}/subagents/{task_id}
# ---------------------------------------------------------------------------


class TestGetSession:
    """Test get_subagent_session endpoint."""

    def test_returns_session_detail(self, client):
        mock_session = MagicMock()
        mock_session.read_messages.return_value = [
            {"role": "human", "content": "do work"},
            {"role": "ai", "content": "done"},
        ]
        mock_session.read_summary.return_value = {
            "status": "completed",
            "subagent_name": "developer",
        }

        with patch(SESSION_PATH, return_value=mock_session):
            resp = client.get("/api/threads/thread-1/subagents/task-1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "task-1"
        assert data["subagent_name"] == "developer"
        assert data["status"] == "completed"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "human"
        assert data["messages"][1]["content"] == "done"

    def test_returns_unknown_without_summary(self, client):
        mock_session = MagicMock()
        mock_session.read_messages.return_value = []
        mock_session.read_summary.return_value = None

        with patch(SESSION_PATH, return_value=mock_session):
            resp = client.get("/api/threads/thread-1/subagents/task-x")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "unknown"
        assert data["subagent_name"] == "unknown"
        assert data["messages"] == []

    def test_returns_partial_summary(self, client):
        mock_session = MagicMock()
        mock_session.read_messages.return_value = [
            {"role": "ai", "content": "thinking..."},
        ]
        mock_session.read_summary.return_value = {
            "status": "interrupted",
            # subagent_name missing — should default to "unknown"
        }

        with patch(SESSION_PATH, return_value=mock_session):
            resp = client.get("/api/threads/thread-1/subagents/task-int")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "interrupted"
        assert data["subagent_name"] == "unknown"


# ---------------------------------------------------------------------------
# POST /api/threads/{thread_id}/subagents/{task_id}/resume
# ---------------------------------------------------------------------------


class TestResumeSession:
    """Test resume_subagent_session endpoint."""

    def test_returns_404_when_session_not_found(self, client):
        with patch(SESSION_PATH + ".get_resume_info", return_value=None):
            resp = client.post(
                "/api/threads/thread-1/subagents/task-gone/resume",
                json={},
            )

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_returns_400_when_task_completed(self, client):
        with patch(SESSION_PATH + ".get_resume_info", return_value={
            "status": "completed",
            "subagent_type": "developer",
            "description": "done task",
        }):
            resp = client.post(
                "/api/threads/thread-1/subagents/task-done/resume",
                json={},
            )

        assert resp.status_code == 400
        assert "completed" in resp.json()["detail"]

    def test_returns_400_when_task_running(self, client):
        with patch(SESSION_PATH + ".get_resume_info", return_value={
            "status": "running",
            "subagent_type": "developer",
            "description": "still running",
        }):
            resp = client.post(
                "/api/threads/thread-1/subagents/task-running/resume",
                json={},
            )

        assert resp.status_code == 400
        assert "running" in resp.json()["detail"]

    def test_resumes_interrupted_task(self, client):
        mock_run_result = {"run_id": "run-resumed-123"}

        with patch(SESSION_PATH + ".get_resume_info", return_value={
            "status": "interrupted",
            "subagent_type": "developer",
            "description": "Feature X",
            "message_count": 5,
            "original_prompt": "Implement feature X",
            "last_ai_content": "halfway done",
        }), patch(GET_CLIENT_PATH) as mock_get_client:
            mock_client = MagicMock()
            mock_client.runs.create = AsyncMock(return_value=mock_run_result)
            mock_get_client.return_value = mock_client

            resp = client.post(
                "/api/threads/thread-1/subagents/task-1/resume",
                json={"description": "Resume feature X"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "task-1" in data["message"]

        # Verify LangGraph client was called correctly
        mock_client.runs.create.assert_called_once()
        call_kwargs = mock_client.runs.create.call_args
        assert call_kwargs[0][0] == "thread-1"  # thread_id
        assert call_kwargs[1]["assistant_id"] == "lead_agent"
        assert call_kwargs[1]["config"]["recursion_limit"] == 500
        assert call_kwargs[1]["context"]["subagent_enabled"] is True

    def test_resumes_failed_task(self, client):
        mock_run_result = {"run_id": "run-resumed-456"}

        with patch(SESSION_PATH + ".get_resume_info", return_value={
            "status": "failed",
            "subagent_type": "architect",
            "description": "Design system",
            "message_count": 3,
            "original_prompt": "Design the API",
            "last_ai_content": "drafting...",
        }), patch(GET_CLIENT_PATH) as mock_get_client:
            mock_client = MagicMock()
            mock_client.runs.create = AsyncMock(return_value=mock_run_result)
            mock_get_client.return_value = mock_client

            resp = client.post(
                "/api/threads/thread-1/subagents/task-fail/resume",
                json={},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

        # Verify the message includes the correct subagent_type
        call_args = mock_client.runs.create.call_args
        input_msgs = call_args[1]["input"]["messages"]
        assert "architect" in input_msgs[0]["content"]

    def test_resumes_unknown_status_task(self, client):
        """Tasks with 'unknown' status should also be resumable."""
        mock_run_result = {"run_id": "run-resumed-789"}

        with patch(SESSION_PATH + ".get_resume_info", return_value={
            "status": "unknown",
            "subagent_type": "general-purpose",
            "description": "Mystery task",
            "message_count": 0,
            "original_prompt": "do something",
            "last_ai_content": "",
        }), patch(GET_CLIENT_PATH) as mock_get_client:
            mock_client = MagicMock()
            mock_client.runs.create = AsyncMock(return_value=mock_run_result)
            mock_get_client.return_value = mock_client

            resp = client.post(
                "/api/threads/thread-1/subagents/task-unknown/resume",
                json={},
            )

        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_returns_500_when_langgraph_client_fails(self, client):
        with patch(SESSION_PATH + ".get_resume_info", return_value={
            "status": "interrupted",
            "subagent_type": "developer",
            "description": "task",
            "message_count": 1,
            "original_prompt": "work",
            "last_ai_content": "",
        }), patch(GET_CLIENT_PATH) as mock_get_client:
            mock_client = MagicMock()
            mock_client.runs.create = AsyncMock(side_effect=ConnectionError("refused"))
            mock_get_client.return_value = mock_client

            resp = client.post(
                "/api/threads/thread-1/subagents/task-1/resume",
                json={},
            )

        assert resp.status_code == 500
        assert "Failed to send resume message" in resp.json()["detail"]

    def test_uses_description_from_body_when_provided(self, client):
        """Custom description in request body overrides session description."""
        mock_run_result = {"run_id": "run-1"}

        with patch(SESSION_PATH + ".get_resume_info", return_value={
            "status": "interrupted",
            "subagent_type": "developer",
            "description": "Original desc",
            "message_count": 1,
            "original_prompt": "work",
            "last_ai_content": "",
        }), patch(GET_CLIENT_PATH) as mock_get_client:
            mock_client = MagicMock()
            mock_client.runs.create = AsyncMock(return_value=mock_run_result)
            mock_get_client.return_value = mock_client

            resp = client.post(
                "/api/threads/thread-1/subagents/task-1/resume",
                json={"description": "Custom resume description"},
            )

        assert resp.status_code == 200
        # Verify custom description is in the message
        call_args = mock_client.runs.create.call_args
        input_content = call_args[1]["input"]["messages"][0]["content"]
        assert "Custom resume description" in input_content
