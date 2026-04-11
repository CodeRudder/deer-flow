"""Background command management endpoints.

Provides REST API for the frontend to list, inspect, and terminate
background commands associated with a specific thread.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/threads/{thread_id}/commands", tags=["commands"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class CommandItem(BaseModel):
    """Summary of a single background command."""

    command_id: str = Field(description="Unique command identifier")
    command: str = Field(description="The shell command that was executed")
    description: str = Field(description="Human-readable description")
    status: str = Field(description="Command status: running, completed, failed, killed, timed_out")
    pid: int | None = Field(default=None, description="System process ID")
    started_at: str = Field(description="ISO timestamp when the command was started")
    return_code: int | None = Field(default=None, description="Process exit code")


class CommandListResponse(BaseModel):
    """Response model for listing background commands."""

    commands: list[CommandItem]


class PaginationInfo(BaseModel):
    """Pagination metadata for command output."""

    total_lines: int = Field(description="Total number of output lines")
    start_line: int = Field(description="Starting line of current page (0-based)")
    line_count: int = Field(description="Number of lines in current page")
    has_more: bool = Field(description="Whether there are more lines after this page")


class CommandOutputResponse(BaseModel):
    """Response model for command output."""

    command_id: str
    status: str
    output: str
    log_file: str | None = None
    pagination: PaginationInfo


class CommandKillResponse(BaseModel):
    """Response model for killing a command."""

    killed: bool
    message: str
    final_output: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_pm():
    """Lazy import process_manager to avoid import issues at module level."""
    from deerflow.sandbox.process_manager import CommandStatus

    return CommandStatus


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=CommandListResponse)
async def list_commands(thread_id: str) -> CommandListResponse:
    """List all background commands for a thread."""
    from deerflow.sandbox.process_manager import list_commands as pm_list

    commands = pm_list(thread_id=thread_id)
    return CommandListResponse(
        commands=[
            CommandItem(
                command_id=c["command_id"],
                command=c["command"],
                description=c["description"],
                status=c["status"],
                pid=c["pid"],
                started_at=c["started_at"],
                return_code=c.get("return_code"),
            )
            for c in commands
        ],
    )


@router.get("/{command_id}/output", response_model=CommandOutputResponse)
async def get_command_output(
    thread_id: str,
    command_id: str,
    start_line: int | None = Query(default=None, description="Starting line (0-based). Null = tail mode."),
    line_count: int = Query(default=10, ge=1, le=50, description="Number of lines to read (max 50)"),
) -> CommandOutputResponse:
    """Get paginated output for a background command."""
    from deerflow.sandbox.process_manager import get_output

    status, output, log_file = get_output(command_id, start_line=start_line, line_count=line_count)

    # Parse pagination metadata from the output string
    total_lines = 0
    actual_start = 0
    actual_count = 0
    has_more = False

    if output.startswith("Total lines:"):
        # Format: "Total lines: N, showing lines S-E (start_line=X, line_count=Y)..."
        header = output.split("\n\n")[0] if "\n\n" in output else output
        try:
            # Extract total_lines
            if "Total lines:" in header:
                total_lines = int(header.split("Total lines:")[1].split(",")[0].strip())
            # Extract showing range
            if "showing lines " in header:
                range_part = header.split("showing lines ")[1].split(" ")[0]
                parts = range_part.split("-")
                actual_start = int(parts[0]) - 1  # Convert 1-based display to 0-based
                actual_count = int(parts[1]) - actual_start
            # Check for has_more
            has_more = "lines after" in header
        except (ValueError, IndexError):
            pass

    status_str = status.value if hasattr(status, "value") else str(status)

    return CommandOutputResponse(
        command_id=command_id,
        status=status_str,
        output=output,
        log_file=log_file,
        pagination=PaginationInfo(
            total_lines=total_lines,
            start_line=actual_start,
            line_count=actual_count,
            has_more=has_more,
        ),
    )


@router.post("/{command_id}/kill", response_model=CommandKillResponse)
async def kill_command(
    thread_id: str,
    command_id: str,
) -> CommandKillResponse:
    """Kill a running background command."""
    from deerflow.sandbox.process_manager import kill as pm_kill

    killed, message = pm_kill(command_id)
    return CommandKillResponse(
        killed=killed,
        message=message,
        final_output=message if killed else None,
    )
