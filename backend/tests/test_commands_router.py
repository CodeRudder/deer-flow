"""Tests for the background commands Gateway API router."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import commands
from deerflow.sandbox.process_manager import CommandStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app():
    """Create a minimal FastAPI app with the commands router."""
    _app = FastAPI()
    _app.include_router(commands.router)
    return _app


@pytest.fixture()
def client(app):
    return TestClient(app)


SAMPLE_COMMANDS = [
    {
        "command_id": "cmd_001",
        "command": "npm run dev",
        "description": "Start dev server",
        "status": CommandStatus.RUNNING,
        "pid": 12345,
        "started_at": "2026-04-11T09:00:00+00:00",
        "return_code": None,
    },
    {
        "command_id": "cmd_002",
        "command": "python -m pytest",
        "description": "Run tests",
        "status": CommandStatus.COMPLETED,
        "pid": None,
        "started_at": "2026-04-11T08:00:00+00:00",
        "return_code": 0,
    },
    {
        "command_id": "cmd_003",
        "command": "npx vite build",
        "description": "Build frontend",
        "status": CommandStatus.FAILED,
        "pid": None,
        "started_at": "2026-04-11T07:00:00+00:00",
        "return_code": 1,
    },
]


# ---------------------------------------------------------------------------
# GET /api/threads/{thread_id}/commands — list commands
# ---------------------------------------------------------------------------


def test_list_commands_returns_commands(client):
    with patch(
        "app.gateway.routers.commands.pm_list" if hasattr(commands, "pm_list")
        else "deerflow.sandbox.process_manager.list_commands",
        return_value=SAMPLE_COMMANDS,
    ):
        resp = client.get("/api/threads/thread-abc/commands")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["commands"]) == 3

    # Verify first command (running)
    c0 = data["commands"][0]
    assert c0["command_id"] == "cmd_001"
    assert c0["command"] == "npm run dev"
    assert c0["description"] == "Start dev server"
    assert c0["status"] == "running"
    assert c0["pid"] == 12345
    assert c0["return_code"] is None

    # Verify second command (completed)
    c1 = data["commands"][1]
    assert c1["status"] == "completed"
    assert c1["return_code"] == 0

    # Verify third command (failed)
    c2 = data["commands"][2]
    assert c2["status"] == "failed"
    assert c2["return_code"] == 1


def test_list_commands_returns_empty_list(client):
    with patch(
        "deerflow.sandbox.process_manager.list_commands",
        return_value=[],
    ):
        resp = client.get("/api/threads/thread-empty/commands")

    assert resp.status_code == 200
    assert resp.json() == {"commands": []}


def test_list_commands_preserves_all_fields(client):
    """Ensure all CommandItem fields are returned."""
    with patch(
        "deerflow.sandbox.process_manager.list_commands",
        return_value=[SAMPLE_COMMANDS[0]],
    ):
        resp = client.get("/api/threads/thread-abc/commands")

    cmd = resp.json()["commands"][0]
    expected_keys = {
        "command_id",
        "command",
        "description",
        "status",
        "pid",
        "started_at",
        "return_code",
    }
    assert set(cmd.keys()) == expected_keys


# ---------------------------------------------------------------------------
# GET /api/threads/{thread_id}/commands/{command_id}/output — get output
# ---------------------------------------------------------------------------

SAMPLE_OUTPUT = (
    "Total lines: 50, showing lines 1-10 (start_line=0, line_count=10), "
    "40 lines after (use start_line=10 to continue)\n\nLine 1\nLine 2\nLine 3"
)


def test_get_output_returns_paginated_output(client):
    with patch(
        "deerflow.sandbox.process_manager.get_output",
        return_value=(CommandStatus.RUNNING, SAMPLE_OUTPUT, "/tmp/cmd_001.log"),
    ):
        resp = client.get(
            "/api/threads/thread-abc/commands/cmd_001/output?start_line=0&line_count=10"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["command_id"] == "cmd_001"
    assert data["status"] == "running"
    assert "Total lines: 50" in data["output"]
    assert data["log_file"] == "/tmp/cmd_001.log"
    assert data["pagination"]["total_lines"] == 50
    assert data["pagination"]["start_line"] == 0
    assert data["pagination"]["line_count"] == 10
    assert data["pagination"]["has_more"] is True


def test_get_output_tail_mode(client):
    """When start_line is omitted, pagination is parsed from the header."""
    tail_output = (
        "Total lines: 30, showing lines 21-30 (start_line=20, line_count=10)\n\n"
        "Last line"
    )
    with patch(
        "deerflow.sandbox.process_manager.get_output",
        return_value=(CommandStatus.COMPLETED, tail_output, None),
    ):
        resp = client.get(
            "/api/threads/thread-abc/commands/cmd_002/output?line_count=10"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["pagination"]["total_lines"] == 30
    assert data["pagination"]["start_line"] == 20
    assert data["pagination"]["has_more"] is False


def test_get_output_missing_command(client):
    """Command not found returns output with 'not found' message."""
    error_output = "Command missing_cmd not found."
    with patch(
        "deerflow.sandbox.process_manager.get_output",
        return_value=(CommandStatus.FAILED, error_output, None),
    ):
        resp = client.get(
            "/api/threads/thread-abc/commands/missing_cmd/output"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert "not found" in data["output"]


def test_get_output_default_line_count(client):
    """When line_count is not specified, default is 10."""
    simple_output = "Total lines: 5, showing lines 1-5 (start_line=0, line_count=5)\n\noutput"
    with patch(
        "deerflow.sandbox.process_manager.get_output",
        return_value=(CommandStatus.RUNNING, simple_output, "/tmp/cmd.log"),
    ) as mock:
        resp = client.get("/api/threads/thread-abc/commands/cmd_001/output")

    assert resp.status_code == 200
    # Verify default line_count=10 was passed to get_output
    mock.assert_called_once_with("cmd_001", start_line=None, line_count=10)


def test_get_output_line_count_validation(client):
    """line_count must be between 1 and 50."""
    resp = client.get(
        "/api/threads/thread-abc/commands/cmd_001/output?line_count=0"
    )
    assert resp.status_code == 422

    resp = client.get(
        "/api/threads/thread-abc/commands/cmd_001/output?line_count=51"
    )
    assert resp.status_code == 422


def test_get_output_no_pagination_header(client):
    """When output doesn't have the standard header, pagination defaults to zeros."""
    plain_output = "(no output yet)"
    with patch(
        "deerflow.sandbox.process_manager.get_output",
        return_value=(CommandStatus.RUNNING, plain_output, None),
    ):
        resp = client.get(
            "/api/threads/thread-abc/commands/cmd_001/output"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["pagination"]["total_lines"] == 0
    assert data["pagination"]["start_line"] == 0
    assert data["pagination"]["line_count"] == 0
    assert data["pagination"]["has_more"] is False


# ---------------------------------------------------------------------------
# POST /api/threads/{thread_id}/commands/{command_id}/kill — kill command
# ---------------------------------------------------------------------------


def test_kill_command_success(client):
    kill_output = "Command cmd_001 killed."
    with patch(
        "deerflow.sandbox.process_manager.kill",
        return_value=(True, kill_output),
    ):
        resp = client.post("/api/threads/thread-abc/commands/cmd_001/kill")

    assert resp.status_code == 200
    data = resp.json()
    assert data["killed"] is True
    assert data["message"] == kill_output
    assert data["final_output"] == kill_output


def test_kill_command_not_running(client):
    with patch(
        "deerflow.sandbox.process_manager.kill",
        return_value=(False, "Command cmd_002 is not running (status: completed)."),
    ):
        resp = client.post("/api/threads/thread-abc/commands/cmd_002/kill")

    assert resp.status_code == 200
    data = resp.json()
    assert data["killed"] is False
    assert data["final_output"] is None
    assert "not running" in data["message"]


def test_kill_command_not_found(client):
    with patch(
        "deerflow.sandbox.process_manager.kill",
        return_value=(False, "Command missing_cmd not found."),
    ):
        resp = client.post("/api/threads/thread-abc/commands/missing_cmd/kill")

    assert resp.status_code == 200
    data = resp.json()
    assert data["killed"] is False
    assert "not found" in data["message"]
