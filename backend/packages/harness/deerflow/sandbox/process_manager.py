"""Background command process manager for the sandbox system.

Provides async bash command execution with output tracking,
process listing, and kill capabilities. All output is redirected to
log files for persistence across service restarts.

Features:
- Background command execution (non-blocking)
- Output persisted to log files per command
- State persisted to JSON per thread
- Orphan process detection and management on restart
- Kill by process group (SIGTERM → SIGKILL)
"""

import json
import logging
import os
import signal
import subprocess
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

_PERSISTENCE_FILENAME = "background_commands.json"


class CommandStatus(str, Enum):
    """Status of a background command."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"
    TIMED_OUT = "timed_out"


@dataclass
class CommandInfo:
    """Tracks a single background command. Serializable for persistence."""

    command_id: str
    command: str
    description: str
    thread_id: str
    status: str  # CommandStatus value
    pid: int | None
    log_file: str  # Absolute path to output log
    started_at: str  # ISO format
    completed_at: str | None = None
    return_code: int | None = None
    # Runtime-only fields (not persisted)
    _process: subprocess.Popen | None = field(default=None, repr=False, compare=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def to_dict(self) -> dict:
        """Serialize to dict for JSON persistence (excludes runtime fields)."""
        return {
            "command_id": self.command_id,
            "command": self.command,
            "description": self.description,
            "thread_id": self.thread_id,
            "status": self.status,
            "pid": self.pid,
            "log_file": self.log_file,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "return_code": self.return_code,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CommandInfo":
        """Deserialize from dict."""
        return cls(
            command_id=data["command_id"],
            command=data["command"],
            description=data["description"],
            thread_id=data["thread_id"],
            status=data["status"],
            pid=data.get("pid"),
            log_file=data["log_file"],
            started_at=data["started_at"],
            completed_at=data.get("completed_at"),
            return_code=data.get("return_code"),
        )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _get_base_dir() -> Path:
    """Get the DeerFlow base data directory."""
    try:
        from deerflow.config.paths import get_paths

        return get_paths().base_dir
    except Exception:
        return Path(".deer-flow")


def _cmd_log_dir(thread_id: str) -> Path:
    """Get the log directory for a thread's background commands."""
    return _get_base_dir() / "threads" / thread_id / "cmd_logs"


def _cmd_log_path(thread_id: str, command_id: str) -> Path:
    """Get the log file path for a specific command."""
    return _cmd_log_dir(thread_id) / f"{command_id}.log"


def _persistence_path(thread_id: str) -> Path:
    """Get the persistence JSON file path for a thread."""
    return _get_base_dir() / "threads" / thread_id / _PERSISTENCE_FILENAME


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_commands: dict[str, CommandInfo] = {}
_commands_lock = threading.Lock()
_restore_done = False


def _generate_command_id() -> str:
    return f"cmd_{uuid.uuid4().hex[:24]}"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _save_thread_commands(thread_id: str) -> None:
    """Persist all commands for a thread to JSON file."""
    try:
        path = _persistence_path(thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        commands_data = []
        with _commands_lock:
            for info in _commands.values():
                if info.thread_id == thread_id:
                    commands_data.append(info.to_dict())

        # Atomic write via temp + rename
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"commands": commands_data}, f, indent=2, ensure_ascii=False)
        tmp_path.rename(path)
    except Exception as e:
        logger.warning("Failed to persist commands for thread %s: %s", thread_id, e)


def _load_all_commands() -> None:
    """Load persisted commands from all threads on startup."""
    base_dir = _get_base_dir()
    threads_dir = base_dir / "threads"
    if not threads_dir.is_dir():
        return

    loaded = 0
    recovered = 0
    for thread_dir in threads_dir.iterdir():
        if not thread_dir.is_dir():
            continue
        persist_file = thread_dir / _PERSISTENCE_FILENAME
        if not persist_file.is_file():
            continue

        try:
            with open(persist_file, encoding="utf-8") as f:
                data = json.load(f)

            thread_id = thread_dir.name
            changed = False
            for cmd_data in data.get("commands", []):
                info = CommandInfo.from_dict(cmd_data)

                # Check if running commands are still alive
                if info.status == CommandStatus.RUNNING:
                    if info.pid and _is_pid_alive(info.pid):
                        logger.info("Recovered running command: %s (PID %d)", info.command_id, info.pid)
                        recovered += 1
                    else:
                        # Process died while we were away
                        info.status = CommandStatus.FAILED
                        info.completed_at = datetime.now(timezone.utc).isoformat()
                        info.return_code = -1
                        changed = True
                        logger.info("Marked orphan command as failed: %s (PID %d was dead)", info.command_id, info.pid)

                with _commands_lock:
                    _commands[info.command_id] = info
                loaded += 1

            if changed:
                _save_thread_commands(thread_id)
        except Exception as e:
            logger.warning("Failed to load commands from %s: %s", persist_file, e)

    if loaded:
        logger.info("Loaded %d background command(s), %d still running", loaded, recovered)


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# ---------------------------------------------------------------------------
# Reader thread — writes output to log file via pipe
# ---------------------------------------------------------------------------


def _reader_loop(info: CommandInfo) -> None:
    """Background thread: read stdout/stderr from pipe, write to log file."""
    proc = info._process
    if proc is None:
        return

    log_path = Path(info.log_file)
    try:
        log_file = open(log_path, "a", encoding="utf-8")
    except Exception:
        return

    try:
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            log_file.write(line)
            log_file.flush()
        # Read remaining stdout
        remaining = proc.stdout.read()
        if remaining:
            log_file.write(remaining)
            log_file.flush()
        # Read stderr
        err = proc.stderr.read()
        if err:
            log_file.write(f"\nStd Error:\n{err}")
            log_file.flush()
    except Exception:
        pass
    finally:
        log_file.close()
        proc.wait()
        # Update status
        with info._lock:
            if info.status == CommandStatus.RUNNING:
                info.return_code = proc.returncode
                info.completed_at = datetime.now(timezone.utc).isoformat()
                info.status = CommandStatus.COMPLETED if proc.returncode == 0 else CommandStatus.FAILED
        # Persist state change
        _save_thread_commands(info.thread_id)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def start(
    command: str,
    description: str,
    shell: str,
    thread_id: str,
) -> str:
    """Start a background command and return its command_id."""
    global _restore_done
    if not _restore_done:
        _restore_done = True
        _load_all_commands()

    command_id = _generate_command_id()

    # Ensure log directory exists
    log_dir = _cmd_log_dir(thread_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = _cmd_log_path(thread_id, command_id)

    proc = subprocess.Popen(
        command,
        executable=shell,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=os.setsid,
    )

    now = datetime.now(timezone.utc).isoformat()
    info = CommandInfo(
        command_id=command_id,
        command=command,
        description=description,
        thread_id=thread_id,
        status=CommandStatus.RUNNING,
        pid=proc.pid,
        log_file=str(log_path),
        started_at=now,
        _process=proc,
    )

    # Start reader thread
    reader = threading.Thread(
        target=_reader_loop,
        args=(info,),
        name=f"cmd-reader-{command_id[:12]}",
        daemon=True,
    )
    with _commands_lock:
        _commands[command_id] = info

    # Start reader after registering so _save_thread_commands can find it
    reader.start()

    _save_thread_commands(thread_id)
    logger.info("Background command started: %s (PID %d) in thread %s", command_id, proc.pid, thread_id)
    return command_id


def get_output(command_id: str, start_line: int | None = None, line_count: int = 10) -> tuple[str, str, str | None]:
    """Get status and output of a background command.

    Args:
        command_id: The command ID to query.
        start_line: Starting line number (0-based). None = read from end (tail mode).
        line_count: Number of lines to read (default: 10, max: 50).

    Returns:
        Tuple of (status_value, output_with_metadata, log_file_path).
        Returns (FAILED, error_message, None) if not found.
    """
    with _commands_lock:
        info = _commands.get(command_id)
    if info is None:
        return CommandStatus.FAILED, f"Command {command_id} not found.", None

    MAX_LINES = 50
    line_count = min(max(line_count, 1), MAX_LINES)

    log_file_str: str | None = info.log_file

    try:
        log_path = Path(info.log_file)
        if log_path.is_file():
            all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            total_lines = len(all_lines)

            if start_line is not None:
                # Offset mode: read from start_line
                actual_start = max(min(start_line, total_lines), 0)
                actual_end = min(actual_start + line_count, total_lines)
            else:
                # Tail mode: read last line_count lines
                actual_start = max(total_lines - line_count, 0)
                actual_end = total_lines

            selected = all_lines[actual_start:actual_end]
            output = "".join(selected)

            # Build metadata header
            showing = actual_end - actual_start
            display_start = actual_start + 1  # 1-based for display
            display_end = actual_start + showing
            meta = f"Total lines: {total_lines}, showing lines {display_start}-{display_end} (start_line={actual_start}, line_count={showing})"
            if total_lines > MAX_LINES and actual_end < total_lines:
                remaining_after = total_lines - actual_end
                meta += f", {remaining_after} lines after (use start_line={actual_end} to continue)"
            if actual_start > 0:
                remaining_before = actual_start
                meta += f", {remaining_before} lines before"

            output = meta + "\n\n" + output if output else meta
        else:
            output = "(no output yet)"
            log_file_str = None
    except Exception as e:
        output = f"(error reading output: {e})"

    return info.status, output, log_file_str


def kill(command_id: str) -> tuple[bool, str]:
    """Kill a background command by command_id.

    Returns:
        Tuple of (killed, message).
    """
    with _commands_lock:
        info = _commands.get(command_id)
    if info is None:
        return False, f"Command {command_id} not found."
    if info.status != CommandStatus.RUNNING:
        return False, f"Command {command_id} is not running (status: {info.status})."

    pid = info.pid

    # Kill by process group if we have the Popen
    if info._process is not None:
        try:
            os.killpg(os.getpgid(info._process.pid), signal.SIGTERM)
            info._process.wait(timeout=3)
        except Exception:
            try:
                os.killpg(os.getpgid(info._process.pid), signal.SIGKILL)
                info._process.wait(timeout=5)
            except Exception:
                pass
    elif pid and _is_pid_alive(pid):
        # Orphan process — kill by PID group
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass

    info.status = CommandStatus.KILLED
    info.return_code = -9
    info.completed_at = datetime.now(timezone.utc).isoformat()

    _save_thread_commands(info.thread_id)
    logger.info("Background command killed: %s", command_id)

    # Read final output (show all, up to 50 lines max)
    _, output, _ = get_output(command_id, start_line=0, line_count=50)
    return True, output


def list_commands(thread_id: str | None = None) -> list[dict]:
    """List all background commands, optionally filtered by thread_id."""
    with _commands_lock:
        items = list(_commands.values())

    result = []
    for info in items:
        if thread_id and info.thread_id != thread_id:
            continue
        result.append(
            {
                "command_id": info.command_id,
                "command": info.command[:80],
                "description": info.description,
                "status": info.status,
                "pid": info.pid,
                "started_at": info.started_at,
                "return_code": info.return_code,
            }
        )
    return result


def cleanup(command_id: str) -> None:
    """Remove a command from the registry. Kills if still running."""
    with _commands_lock:
        info = _commands.pop(command_id, None)
    if info is None:
        return
    if info.status == CommandStatus.RUNNING:
        kill(command_id)
    logger.info("Background command cleaned up: %s", command_id)


def cleanup_by_thread(thread_id: str) -> None:
    """Kill and remove all commands for a given thread."""
    with _commands_lock:
        to_clean = [cid for cid, info in _commands.items() if info.thread_id == thread_id]

    for cid in to_clean:
        cleanup(cid)

    if to_clean:
        _save_thread_commands(thread_id)
        logger.info("Cleaned up %d background command(s) for thread %s", len(to_clean), thread_id)


def restore() -> None:
    """Load persisted commands on service startup."""
    _load_all_commands()
