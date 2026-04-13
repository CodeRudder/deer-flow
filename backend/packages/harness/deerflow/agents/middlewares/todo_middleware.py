"""Middleware that extends TodoListMiddleware with context-loss detection.

When the message history is truncated (e.g., by SummarizationMiddleware), the
original ``write_todos`` tool call and its ToolMessage can be scrolled out of the
active context window. This middleware detects that situation and injects a
reminder message so the model still knows about the outstanding todo list.

The ``write_todos`` tool is overridden to support incremental operations
(``updates``, ``adds``) in addition to the original full-replace (``todos``).
Incremental operations read the current todos from state via ``InjectedState``
and compute the full replacement list, avoiding the need for a channel-level
reducer and keeping backward compatibility with existing checkpoints.
"""

from __future__ import annotations

from typing import Any, override

from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.todo import PlanningState, Todo
from langchain.tools import InjectedToolCallId
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from langgraph.runtime import Runtime
from langgraph.types import Command
from typing_extensions import Annotated

from deerflow.agents.thread_state import apply_todo_ops


def _todos_in_messages(messages: list[Any]) -> bool:
    """Return True if any AIMessage in *messages* contains a write_todos tool call."""
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.get("name") == "write_todos":
                    return True
    return False


def _reminder_in_messages(messages: list[Any]) -> bool:
    """Return True if a todo_reminder HumanMessage is already present in *messages*."""
    for msg in messages:
        if isinstance(msg, HumanMessage) and getattr(msg, "name", None) == "todo_reminder":
            return True
    return False


def _format_todos(todos: list[Todo]) -> str:
    """Format a list of Todo items into a human-readable string."""
    lines: list[str] = []
    for i, todo in enumerate(todos):
        status = todo.get("status", "pending")
        content = todo.get("content", "")
        lines.append(f"- [{i}] [{status}] {content}")
    return "\n".join(lines)


class TodoMiddleware(TodoListMiddleware):
    """Extends TodoListMiddleware with ``write_todos`` context-loss detection.

    When the original ``write_todos`` tool call has been truncated from the message
    history (e.g., after summarization), the model loses awareness of the current
    todo list. This middleware detects that gap in ``before_model`` / ``abefore_model``
    and injects a reminder message so the model can continue tracking progress.

    The ``write_todos`` tool is overridden to support incremental operations:
    - ``todos`` — full replace (backward compatible)
    - ``updates`` — update specific items by index
    - ``adds`` — insert new items (with optional position)
    """

    def __init__(
        self,
        *,
        system_prompt: str | None = None,
        tool_description: str | None = None,
    ) -> None:
        super().__init__(
            system_prompt=system_prompt or "",
            tool_description=tool_description or "",
        )
        # Override the tool with our enhanced version
        desc = tool_description or ""

        @tool(description=desc)
        def write_todos(
            todos: list[Todo] | None = None,
            updates: list[dict] | None = None,
            adds: list[dict] | None = None,
            state: Annotated[dict, InjectedState] = None,
            tool_call_id: Annotated[str, InjectedToolCallId] = "",
        ) -> Command:
            """Create and manage a structured task list for your current work session."""
            has_todos = todos is not None
            has_updates = updates is not None
            has_adds = adds is not None

            # Reject conflicting parameter combinations
            if has_todos and (has_updates or has_adds):
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content="Error: Cannot combine 'todos' (full replace) with 'updates' or 'adds'. Use 'todos' alone for full replace, or 'updates'/'adds' for incremental operations.",
                                tool_call_id=tool_call_id,
                                status="error",
                            )
                        ],
                    }
                )

            if not has_todos and not has_updates and not has_adds:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content="Error: Must provide at least one of: 'todos', 'updates', or 'adds'.",
                                tool_call_id=tool_call_id,
                                status="error",
                            )
                        ],
                    }
                )

            # Full replace (backward compatible)
            if has_todos:
                return Command(
                    update={
                        "todos": todos,
                        "messages": [
                            ToolMessage(
                                content=f"Updated todo list to {todos}",
                                tool_call_id=tool_call_id,
                            )
                        ],
                    }
                )

            # Incremental operation — read current state and compute full list
            current_todos = (state or {}).get("todos") or []
            new_todos = apply_todo_ops(current_todos, updates, adds)

            parts: list[str] = []
            if has_updates:
                parts.append(f"update {len(updates)} item(s)")
            if has_adds:
                parts.append(f"add {len(adds)} item(s)")

            return Command(
                update={
                    "todos": new_todos,
                    "messages": [
                        ToolMessage(
                            content=f"Applied todo operations: {', '.join(parts)}",
                            tool_call_id=tool_call_id,
                        )
                    ],
                }
            )

        self.tools = [write_todos]

    @override
    def after_model(
        self,
        state: Any,
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """Reject parallel write_todos tool calls."""
        messages = state.get("messages", [])
        if not messages:
            return None

        last_ai_msg = next((msg for msg in reversed(messages) if isinstance(msg, AIMessage)), None)
        if not last_ai_msg or not last_ai_msg.tool_calls:
            return None

        write_todos_calls = [tc for tc in last_ai_msg.tool_calls if tc["name"] == "write_todos"]
        if len(write_todos_calls) > 1:
            error_messages = [
                ToolMessage(
                    content=(
                        "Error: The `write_todos` tool should never be called multiple times "
                        "in parallel. Please call it only once per model invocation."
                    ),
                    tool_call_id=tc["id"],
                    status="error",
                )
                for tc in write_todos_calls
            ]
            return {"messages": error_messages}

        return None

    @override
    async def aafter_model(
        self,
        state: Any,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        """Async version of after_model."""
        return self.after_model(state, runtime)

    @override
    def before_model(
        self,
        state: PlanningState,
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """Inject a todo-list reminder when write_todos has left the context window."""
        todos: list[Todo] = state.get("todos") or []  # type: ignore[assignment]
        if not todos:
            return None

        messages = state.get("messages") or []
        if _todos_in_messages(messages):
            return None

        if _reminder_in_messages(messages):
            return None

        formatted = _format_todos(todos)
        reminder = HumanMessage(
            name="todo_reminder",
            content=(
                "<system_reminder>\n"
                "Your todo list from later is no longer visible in the current context window, "
                "but it is still active. Here is the current state:\n\n"
                f"{formatted}\n\n"
                "Continue tracking and updating this todo list as you work. "
                "Call `write_todos` whenever the status of any item changes.\n"
                "</system_reminder>"
            ),
        )
        return {"messages": [reminder]}

    @override
    async def abefore_model(
        self,
        state: PlanningState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        """Async version of before_model."""
        return self.before_model(state, runtime)
