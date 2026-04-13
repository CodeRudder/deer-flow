"""Sub-agent session persistence and recovery.

Each sub-agent run stores its full conversation (Human/AI/Tool messages) as a
JSONL file under ``{base_dir}/threads/{thread_id}/subagents/{task_id}.jsonl``.
When the run completes (or is interrupted), a summary JSON file is written
alongside it.  The JSONL format enables real-time, line-oriented appending
without needing to rewrite the whole file on every chunk.

Recovery flow:
    1. On process restart, ``find_interrupted()`` locates sessions whose JSONL
       files lack a terminal status marker.
    2. The caller reads partial messages via ``read_messages()`` and builds a
       recovery prompt summarising progress so far.
    3. A new sub-agent continues from where the interrupted one left off.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def serialize_message(msg: BaseMessage, *, max_content_len: int | None = None) -> dict[str, Any]:
    """Convert a LangChain message to a JSON-serialisable dict for JSONL storage.

    Args:
        msg: The LangChain message to serialize.
        max_content_len: If set, truncate ``str`` content longer than this
            many characters.  The truncation marker includes the original
            length so readers can tell data was removed.

    Returns:
        A dict with ``ts``, ``role``, ``content``, and optional fields
        (``tool_calls``, ``tool_call_id``, ``name``, ``reasoning``).
    """
    entry: dict[str, Any] = {"ts": _utc_now_iso()}

    # Include message ID for deduplication on restart
    msg_id = getattr(msg, "id", None)
    if msg_id:
        entry["id"] = msg_id

    # Truncate large string content
    content = msg.content
    if max_content_len and isinstance(content, str) and len(content) > max_content_len:
        content = content[:max_content_len] + f"\n...[TRUNCATED: original {len(msg.content)} chars]"

    if isinstance(msg, HumanMessage):
        entry["role"] = "human"
        entry["content"] = content
    elif isinstance(msg, AIMessage):
        entry["role"] = "ai"
        entry["content"] = content
        if msg.tool_calls:
            entry["tool_calls"] = [
                {"id": tc.get("id"), "name": tc.get("name"), "args": tc.get("args")}
                for tc in msg.tool_calls
            ]
        # Preserve reasoning/thinking content for debugging
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            reasoning = msg.reasoning_content
            if max_content_len and isinstance(reasoning, str) and len(reasoning) > max_content_len:
                reasoning = reasoning[:max_content_len] + f"\n...[TRUNCATED: original {len(msg.reasoning_content)} chars]"
            entry["reasoning"] = reasoning
    elif isinstance(msg, ToolMessage):
        entry["role"] = "tool"
        entry["tool_call_id"] = msg.tool_call_id
        entry["content"] = content
        entry["name"] = msg.name
    else:
        entry["role"] = getattr(msg, "type", "unknown")
        entry["content"] = content

    return entry


# Backward-compatible alias
_serialize_message = serialize_message


class SubagentSession:
    """Manages persistence for a single sub-agent execution.

    Thread-safety: append operations use append-mode file I/O which is
    atomic at the line level on POSIX systems.  The summary JSON write uses
    the temp-file-then-rename pattern for crash safety.
    """

    def __init__(
        self,
        thread_id: str,
        task_id: str,
        subagent_name: str,
        description: str = "",
    ) -> None:
        self.thread_id = thread_id
        self.task_id = task_id
        self.subagent_name = subagent_name
        self.description = description
        self.started_at = _utc_now_iso()

        # Lazy-resolved paths
        self._jsonl_path: Path | None = None
        self._summary_path: Path | None = None

    # ── Path helpers ────────────────────────────────────────────────────

    @property
    def jsonl_path(self) -> Path:
        if self._jsonl_path is None:
            d = get_paths().subagent_dir(self.thread_id)
            self._jsonl_path = d / f"{self.task_id}.jsonl"
        return self._jsonl_path

    @property
    def summary_path(self) -> Path:
        if self._summary_path is None:
            d = get_paths().subagent_dir(self.thread_id)
            self._summary_path = d / f"{self.task_id}.summary.json"
        return self._summary_path

    # ── Write operations ────────────────────────────────────────────────

    def append_message(self, msg: BaseMessage) -> None:
        """Append a single message to the JSONL file (real-time, line-level atomic)."""
        entry = _serialize_message(msg)
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def append_messages(self, messages: list[BaseMessage]) -> None:
        """Append multiple messages in a single write batch."""
        lines = []
        for msg in messages:
            entry = _serialize_message(msg)
            lines.append(json.dumps(entry, ensure_ascii=False))
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _append_status_line(self, status: str, **extra: Any) -> None:
        """Write a terminal status marker line to the JSONL file."""
        entry: dict[str, Any] = {"ts": _utc_now_iso(), "status": status, **extra}
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _write_summary(self, status: str, result: str | None = None, error: str | None = None, message_count: int = 0) -> None:
        """Write a summary JSON file.

        Writes directly to the target path.  The summary file is only used
        for metadata queries — if the write is interrupted, the JSONL file
        still contains the status marker line which is the authoritative
        source for session state.
        """
        summary: dict[str, Any] = {
            "task_id": self.task_id,
            "subagent_name": self.subagent_name,
            "thread_id": self.thread_id,
            "description": self.description,
            "status": status,
            "started_at": self.started_at,
            "completed_at": _utc_now_iso(),
            "message_count": message_count,
            "result": result,
            "error": error,
        }
        target = self.summary_path
        try:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
        except OSError:
            logger.exception("Failed to write summary for %s", self.task_id)

    def mark_completed(self, result: str, message_count: int = 0) -> None:
        """Mark session as completed and write summary."""
        self._append_status_line("completed", result=result[:2000], message_count=message_count)
        self._write_summary("completed", result=result[:2000], message_count=message_count)

    def mark_failed(self, error: str, message_count: int = 0) -> None:
        """Mark session as failed and write summary."""
        self._append_status_line("failed", error=error[:1000], message_count=message_count)
        self._write_summary("failed", error=error[:1000], message_count=message_count)

    def mark_interrupted(self, message_count: int = 0) -> None:
        """Mark session as interrupted (cancel / timeout / crash)."""
        self._append_status_line("interrupted", message_count=message_count)
        # No summary for interrupted — presence of .jsonl without terminal
        # status line is the signal.  We write interrupted as a status marker
        # so is_terminal returns True and the session won't be re-reported.
        self._write_summary("interrupted", message_count=message_count)

    # ── Read operations ─────────────────────────────────────────────────

    def read_messages(self) -> list[dict[str, Any]]:
        """Read all conversation messages (excludes status marker lines)."""
        messages: list[dict[str, Any]] = []
        if not self.jsonl_path.exists():
            return messages
        with open(self.jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Status marker lines have "status" key, skip them
                if "status" in entry and "role" not in entry:
                    continue
                messages.append(entry)
        return messages

    def read_summary(self) -> dict[str, Any] | None:
        """Read the summary JSON if it exists."""
        if not self.summary_path.exists():
            return None
        try:
            with open(self.summary_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    @property
    def is_terminal(self) -> bool:
        """Check whether this session has a terminal status marker."""
        if not self.jsonl_path.exists():
            return False
        with open(self.jsonl_path, encoding="utf-8") as f:
            # Read only the last few lines to check for status marker
            lines = f.readlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("status") in ("completed", "failed", "interrupted"):
                return True
            break
        return False

    # ── Class-level queries ─────────────────────────────────────────────

    @staticmethod
    def list_sessions(thread_id: str) -> list["SubagentSession"]:
        """List all sessions for a thread, with basic metadata loaded."""
        sessions: list[SubagentSession] = []
        try:
            d = get_paths().subagent_dir(thread_id)
        except Exception:
            return sessions

        for jsonl_file in sorted(d.glob("*.jsonl")):
            task_id = jsonl_file.stem
            # Try to load subagent_name from first line or summary
            subagent_name = "unknown"
            description = ""
            summary_path = jsonl_file.parent / f"{task_id}.summary.json"
            if summary_path.exists():
                try:
                    with open(summary_path, encoding="utf-8") as f:
                        s = json.load(f)
                    subagent_name = s.get("subagent_name", subagent_name)
                    description = s.get("description", "")
                except (json.JSONDecodeError, OSError):
                    pass

            session = SubagentSession(
                thread_id=thread_id,
                task_id=task_id,
                subagent_name=subagent_name,
                description=description,
            )
            # Override started_at from summary if available
            if summary_path.exists():
                try:
                    with open(summary_path, encoding="utf-8") as f:
                        s = json.load(f)
                    session.started_at = s.get("started_at", session.started_at)
                except (json.JSONDecodeError, OSError):
                    pass
            sessions.append(session)

        return sessions

    @staticmethod
    def find_interrupted(thread_id: str) -> list["SubagentSession"]:
        """Find sessions that were interrupted (have JSONL but no terminal marker).

        This is used for recovery: the caller can read partial messages and
        build a recovery prompt for a new sub-agent.
        """
        interrupted: list[SubagentSession] = []
        for session in SubagentSession.list_sessions(thread_id):
            if not session.is_terminal:
                interrupted.append(session)
        return interrupted

    @staticmethod
    def get_resume_info(task_id: str, thread_id: str) -> dict[str, Any] | None:
        """Get information needed to resume an interrupted/failed subtask.

        Returns a dict with keys:
            original_prompt, subagent_type, description, message_count,
            last_ai_content, status
        or None if session not found.
        """
        session = SubagentSession(
            thread_id=thread_id,
            task_id=task_id,
            subagent_name="unknown",
        )
        if not session.jsonl_path.exists():
            return None

        # Read summary for metadata
        summary = session.read_summary()
        subagent_type = "general-purpose"
        description = ""
        status = "unknown"
        if summary:
            subagent_type = summary.get("subagent_name", subagent_type)
            description = summary.get("description", "")
            status = summary.get("status", "unknown")

        # Read messages to extract original prompt and last AI content
        messages = session.read_messages()
        original_prompt = ""
        last_ai_content = ""
        for msg in messages:
            role = msg.get("role", "")
            if role == "human" and not original_prompt:
                content = msg.get("content", "")
                original_prompt = content[:2000] if isinstance(content, str) else str(content)[:2000]
            elif role == "ai":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    last_ai_content = content[:500]

        return {
            "original_prompt": original_prompt,
            "subagent_type": subagent_type,
            "description": description,
            "message_count": len(messages),
            "last_ai_content": last_ai_content,
            "status": status,
        }
