"""Task tool for delegating work to subagents."""

import asyncio
import logging
import uuid
from dataclasses import replace
from typing import Annotated

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langgraph.config import get_stream_writer
from langgraph.typing import ContextT

from deerflow.agents.lead_agent.prompt import get_skills_prompt_section
from deerflow.agents.thread_state import ThreadState
from deerflow.sandbox.security import LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE, is_host_bash_allowed
from deerflow.subagents import SubagentExecutor, get_available_subagent_names, get_subagent_config
from deerflow.subagents.executor import SubagentStatus, cleanup_background_task, get_background_task_result, request_cancel_background_task
from deerflow.subagents.session import SubagentSession

logger = logging.getLogger(__name__)


def _build_recovery_prompt(sessions: list[SubagentSession]) -> str:
    """Build a recovery context from interrupted sub-agent sessions."""
    parts: list[str] = []
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


@tool("task", parse_docstring=True)
async def task_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    max_turns: int | None = None,
    task_id: str | None = None,
    action: str = "create",
) -> str:
    """Delegate a task to a specialized subagent that runs in its own context.

    Subagents help you:
    - Preserve context by keeping exploration and implementation separate
    - Handle complex multi-step tasks autonomously
    - Execute commands or operations in isolated contexts
    - Simulate a multi-role development team with specialized agents

    Available subagent types:
    - **general-purpose**: A capable agent for complex, multi-step tasks.
    - **bash**: Command execution specialist (only when host bash is allowed).
    - **pm**: Product Manager — requirements analysis, user stories, task prioritization.
    - **architect**: System Architect — technical design, architecture decisions, code review.
    - **developer**: Senior Developer — code implementation, debugging, optimization.
    - **tester**: QA Tester — test design, quality assurance, automated testing.
    - **devops**: DevOps Engineer — CI/CD, deployment, monitoring, infrastructure.

    Team workflow example:
    1. task("requirements", "Analyze requirements...", subagent_type="pm")
    2. task("architecture", "Design architecture...", subagent_type="architect")
    3. task("implementation", "Implement feature...", subagent_type="developer")
    4. task("testing", "Write and run tests...", subagent_type="tester")
    5. task("deployment", "Configure CI/CD...", subagent_type="devops")

    When to use this tool:
    - Complex tasks requiring multiple steps or tools
    - Tasks that produce verbose output
    - When you want to isolate context from the main conversation
    - Parallel research or exploration tasks

    When NOT to use this tool:
    - Simple, single-step operations (use tools directly)
    - Tasks requiring user interaction or clarification

    Actions:
    - **create** (default): Create and execute a new subtask.
    - **resume**: Resume an interrupted/failed subtask from where it left off.
      The system reads the previous session's conversation history and injects
      recovery context so the subagent continues without repeating completed work.
      Example: task(action="resume", task_id="call_xxx", description="Resume implementation", prompt="continue", subagent_type="developer")
    - **cancel**: Cancel a running subtask.
      Example: task(action="cancel", task_id="call_xxx", description="Cancel", prompt="", subagent_type="general-purpose")
    - **query**: Query a subtask's status and result.
      Example: task(action="query", task_id="call_xxx", description="Check status", prompt="", subagent_type="general-purpose")

    Args:
        description: A short (3-5 word) description of the task for logging/display. ALWAYS PROVIDE THIS PARAMETER FIRST.
        prompt: The task description for the subagent. Be specific and clear about what needs to be done. ALWAYS PROVIDE THIS PARAMETER SECOND.
        subagent_type: The type of subagent to use. ALWAYS PROVIDE THIS PARAMETER THIRD.
        max_turns: Optional maximum number of agent turns. Defaults to subagent's configured max.
        task_id: Target subtask ID for resume/cancel/query actions. Not needed for create.
        action: Action to perform: "create" (default), "resume", "cancel", or "query".
    """
    # ── Action dispatch ─────────────────────────────────────────────────
    if action == "cancel":
        return await _action_cancel(task_id)
    elif action == "query":
        return await _action_query(task_id)
    elif action == "resume":
        return await _action_resume(runtime, task_id, tool_call_id, description, prompt, subagent_type, max_turns)

    # ── Default: action="create" ────────────────────────────────────────
    available_subagent_names = get_available_subagent_names()

    # Get subagent configuration
    config = get_subagent_config(subagent_type)
    if config is None:
        available = ", ".join(available_subagent_names)
        return f"Error: Unknown subagent type '{subagent_type}'. Available: {available}"
    if subagent_type == "bash" and not is_host_bash_allowed():
        return f"Error: {LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE}"

    # Build config overrides
    overrides: dict = {}

    skills_section = get_skills_prompt_section()
    if skills_section:
        overrides["system_prompt"] = config.system_prompt + "\n\n" + skills_section

    if max_turns is not None:
        overrides["max_turns"] = max_turns

    if overrides:
        config = replace(config, **overrides)

    # Extract parent context from runtime
    sandbox_state = None
    thread_data = None
    thread_id = None
    parent_model = None
    trace_id = None

    if runtime is not None:
        sandbox_state = runtime.state.get("sandbox")
        thread_data = runtime.state.get("thread_data")
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            thread_id = runtime.config.get("configurable", {}).get("thread_id")
        if thread_id is None:
            # Fallback: try get_config() from LangGraph context
            try:
                from langgraph.config import get_config
                lg_config = get_config()
                thread_id = lg_config.get("configurable", {}).get("thread_id")
            except Exception:
                pass
        logger.debug(
            "task_tool runtime: thread_id=%s, context_keys=%s, config_configurable_keys=%s",
            thread_id,
            list(runtime.context.keys()) if runtime.context else None,
            list(runtime.config.get("configurable", {}).keys()) if runtime.config else None,
        )

        # Try to get parent model from configurable
        metadata = runtime.config.get("metadata", {})
        parent_model = metadata.get("model_name")

        # Get or generate trace_id for distributed tracing
        trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]

    # Get available tools (excluding task tool to prevent nesting)
    # Lazy import to avoid circular dependency
    from deerflow.tools import get_available_tools

    # Subagents should not have subagent tools enabled (prevent recursive nesting)
    tools = get_available_tools(model_name=parent_model, subagent_enabled=False)

    # Create executor
    executor = SubagentExecutor(
        config=config,
        tools=tools,
        parent_model=parent_model,
        sandbox_state=sandbox_state,
        thread_data=thread_data,
        thread_id=thread_id,
        trace_id=trace_id,
        session=None,  # will be set below
    )

    # Create session for persistence
    session: SubagentSession | None = None
    if thread_id:
        try:
            session = SubagentSession(
                thread_id=thread_id,
                task_id=tool_call_id,
                subagent_name=subagent_type,
                description=description,
            )
            executor.session = session
            logger.info("Created SubagentSession for thread=%s, task=%s, subagent=%s", thread_id, tool_call_id, subagent_type)
        except Exception:
            logger.exception("Failed to create SubagentSession for thread=%s, task=%s", thread_id, tool_call_id)
    else:
        logger.warning("No thread_id available — subagent session will NOT be persisted")

    # Check for interrupted sessions and inject recovery context
    if thread_id and session is not None:
        try:
            interrupted = SubagentSession.find_interrupted(thread_id)
            if interrupted:
                recovery = _build_recovery_prompt(interrupted)
                prompt = recovery + "\n\n" + prompt
                logger.info("Injected recovery context from %d interrupted session(s)", len(interrupted))
        except Exception:
            logger.exception("Failed to check interrupted sessions, continuing without recovery")

    # Start background execution (always async to prevent blocking)
    # Use tool_call_id as task_id for better traceability
    task_id = executor.execute_async(prompt, task_id=tool_call_id, description=description)

    # Poll for task completion in backend (removes need for LLM to poll)
    poll_count = 0
    last_status = None
    last_message_count = 0  # Track how many AI messages we've already sent
    # Polling timeout: execution timeout + 60s buffer, checked every 5s
    max_poll_count = (config.timeout_seconds + 60) // 5

    logger.info(f"[trace={trace_id}] Started background task {task_id} (subagent={subagent_type}, timeout={config.timeout_seconds}s, polling_limit={max_poll_count} polls)")

    writer = get_stream_writer()
    # Send Task Started message'
    writer({"type": "task_started", "task_id": task_id, "description": description})

    try:
        while True:
            result = get_background_task_result(task_id)

            if result is None:
                logger.error(f"[trace={trace_id}] Task {task_id} not found in background tasks")
                writer({"type": "task_failed", "task_id": task_id, "error": "Task disappeared from background tasks"})
                cleanup_background_task(task_id)
                return f"Error: Task {task_id} disappeared from background tasks"

            # Log status changes for debugging
            if result.status != last_status:
                logger.info(f"[trace={trace_id}] Task {task_id} status: {result.status.value}")
                last_status = result.status

            # Check for new AI messages and send task_running events
            current_message_count = len(result.ai_messages)
            if current_message_count > last_message_count:
                # Send task_running event for each new message
                for i in range(last_message_count, current_message_count):
                    message = result.ai_messages[i]
                    writer(
                        {
                            "type": "task_running",
                            "task_id": task_id,
                            "message": message,
                            "message_index": i + 1,  # 1-based index for display
                            "total_messages": current_message_count,
                        }
                    )
                    logger.info(f"[trace={trace_id}] Task {task_id} sent message #{i + 1}/{current_message_count}")
                last_message_count = current_message_count

            # Check if task completed, failed, or timed out
            if result.status == SubagentStatus.COMPLETED:
                writer({"type": "task_completed", "task_id": task_id, "result": result.result})
                logger.info(f"[trace={trace_id}] Task {task_id} completed after {poll_count} polls")
                cleanup_background_task(task_id)
                return f"Task Succeeded. Result: {result.result}"
            elif result.status == SubagentStatus.FAILED:
                writer({"type": "task_failed", "task_id": task_id, "error": result.error})
                logger.error(f"[trace={trace_id}] Task {task_id} failed: {result.error}")
                cleanup_background_task(task_id)
                return f"Task failed. Error: {result.error}"
            elif result.status == SubagentStatus.CANCELLED:
                writer({"type": "task_cancelled", "task_id": task_id, "error": result.error})
                logger.info(f"[trace={trace_id}] Task {task_id} cancelled: {result.error}")
                cleanup_background_task(task_id)
                return "Task cancelled by user."
            elif result.status == SubagentStatus.TIMED_OUT:
                writer({"type": "task_timed_out", "task_id": task_id, "error": result.error})
                logger.warning(f"[trace={trace_id}] Task {task_id} timed out: {result.error}")
                cleanup_background_task(task_id)
                return f"Task timed out. Error: {result.error}"

            # Still running, wait before next poll
            await asyncio.sleep(5)
            poll_count += 1

            # Polling timeout as a safety net (in case thread pool timeout doesn't work)
            # Set to execution timeout + 60s buffer, in 5s poll intervals
            # This catches edge cases where the background task gets stuck
            # Note: We don't call cleanup_background_task here because the task may
            # still be running in the background. The cleanup will happen when the
            # executor completes and sets a terminal status.
            if poll_count > max_poll_count:
                timeout_minutes = config.timeout_seconds // 60
                logger.error(f"[trace={trace_id}] Task {task_id} polling timed out after {poll_count} polls (should have been caught by thread pool timeout)")
                writer({"type": "task_timed_out", "task_id": task_id})
                return f"Task polling timed out after {timeout_minutes} minutes. This may indicate the background task is stuck. Status: {result.status.value}"
    except asyncio.CancelledError:
        # Signal the background subagent thread to stop cooperatively.
        # Without this, the thread (running in ThreadPoolExecutor with its
        # own event loop via asyncio.run) would continue executing even
        # after the parent task is cancelled.
        request_cancel_background_task(task_id)

        async def cleanup_when_done() -> None:
            max_cleanup_polls = max_poll_count
            cleanup_poll_count = 0

            while True:
                result = get_background_task_result(task_id)
                if result is None:
                    return

                if result.status in {SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.CANCELLED, SubagentStatus.TIMED_OUT} or getattr(result, "completed_at", None) is not None:
                    cleanup_background_task(task_id)
                    return

                if cleanup_poll_count > max_cleanup_polls:
                    logger.warning(f"[trace={trace_id}] Deferred cleanup for task {task_id} timed out after {cleanup_poll_count} polls")
                    return

                await asyncio.sleep(5)
                cleanup_poll_count += 1

        def log_cleanup_failure(cleanup_task: asyncio.Task[None]) -> None:
            if cleanup_task.cancelled():
                return

            exc = cleanup_task.exception()
            if exc is not None:
                logger.error(f"[trace={trace_id}] Deferred cleanup failed for task {task_id}: {exc}")

        logger.debug(f"[trace={trace_id}] Scheduling deferred cleanup for cancelled task {task_id}")
        asyncio.create_task(cleanup_when_done()).add_done_callback(log_cleanup_failure)
        raise


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


async def _action_cancel(task_id: str | None) -> str:
    """Cancel a running subtask."""
    if not task_id:
        return "Error: task_id is required for cancel action"

    result = get_background_task_result(task_id)
    if result is None:
        return f"Error: Task {task_id} not found"

    if result.status.value not in ("running", "pending"):
        return f"Error: Task {task_id} is {result.status.value}, cannot cancel"

    request_cancel_background_task(task_id)
    logger.info("Cancelled subtask %s via task tool", task_id)
    return f"Task {task_id} cancelled successfully."


async def _action_query(task_id: str | None) -> str:
    """Query subtask status and result."""
    if not task_id:
        return "Error: task_id is required for query action"

    # Check in-memory first
    result = get_background_task_result(task_id)
    if result is not None:
        status = result.status.value
        parts = [f"Task {task_id}: status={status}"]
        if result.result:
            parts.append(f"result={result.result[:500]}")
        if result.error:
            parts.append(f"error={result.error[:300]}")
        return "\n".join(parts)

    # Check on-disk session
    try:
        from deerflow.subagents.session import SubagentSession
        # Need thread_id — try to find it from session files
        info = SubagentSession.get_resume_info(task_id, _find_thread_id_for_task(task_id) or "")
        if info:
            return (
                f"Task {task_id}: status={info['status']}, "
                f"subagent={info['subagent_type']}, "
                f"steps={info['message_count']}"
            )
    except Exception:
        logger.exception("Failed to query task %s from disk", task_id)

    return f"Error: Task {task_id} not found (neither in memory nor on disk)"


async def _action_resume(
    runtime: ToolRuntime[ContextT, ThreadState],
    task_id: str | None,
    tool_call_id: str,
    description: str,
    prompt: str,
    subagent_type: str,
    max_turns: int | None,
) -> str:
    """Resume an interrupted/failed subtask from where it left off."""
    if not task_id:
        return "Error: task_id is required for resume action"

    # Get thread_id from runtime
    thread_id: str | None = None
    if runtime is not None:
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            thread_id = runtime.config.get("configurable", {}).get("thread_id")

    if not thread_id:
        return f"Error: Cannot determine thread_id for resuming task {task_id}"

    # Read session info
    from deerflow.subagents.session import SubagentSession

    info = SubagentSession.get_resume_info(task_id, thread_id)
    if info is None:
        return f"Error: No session found for task {task_id} in thread {thread_id}"

    # Use original subagent_type if available
    effective_subagent_type = info["subagent_type"] or subagent_type
    effective_description = description or f"Resume: {info['description']}"

    # Build recovery prompt
    recovery = (
        f"<recovery>\n"
        f"任务被中断。已执行 {info['message_count']} 步。\n"
        f"最后完成的工作：{info['last_ai_content'] or '（无）'}\n"
        f"原始任务：{info['original_prompt'][:500]}\n"
        f"请继续完成剩余工作，不要重复已完成的步骤。\n"
        f"</recovery>\n\n"
        f"{info['original_prompt'] or prompt}"
    )

    logger.info(
        "Resuming task %s (subagent=%s, steps_completed=%d)",
        task_id,
        effective_subagent_type,
        info["message_count"],
    )

    # Reuse the create flow by resetting action and calling with recovery prompt
    # We monkey-patch the call by directly executing the create logic
    available_subagent_names = get_available_subagent_names()

    config = get_subagent_config(effective_subagent_type)
    if config is None:
        available = ", ".join(available_subagent_names)
        return f"Error: Unknown subagent type '{effective_subagent_type}'. Available: {available}"

    # Build config overrides
    overrides: dict = {}
    skills_section = get_skills_prompt_section()
    if skills_section:
        overrides["system_prompt"] = config.system_prompt + "\n\n" + skills_section
    if max_turns is not None:
        overrides["max_turns"] = max_turns
    if overrides:
        from dataclasses import replace as _replace
        config = _replace(config, **overrides)

    # Extract context from runtime
    sandbox_state = None
    thread_data = None
    parent_model = None
    trace_id = None
    if runtime is not None:
        sandbox_state = runtime.state.get("sandbox")
        thread_data = runtime.state.get("thread_data")
        metadata = runtime.config.get("metadata", {})
        parent_model = metadata.get("model_name")
        trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]

    from deerflow.tools import get_available_tools

    tools = get_available_tools(model_name=parent_model, subagent_enabled=False)

    executor = SubagentExecutor(
        config=config,
        tools=tools,
        parent_model=parent_model,
        sandbox_state=sandbox_state,
        thread_data=thread_data,
        thread_id=thread_id,
        trace_id=trace_id,
        session=None,
    )

    # Create new session for the resumed run
    try:
        session = SubagentSession(
            thread_id=thread_id,
            task_id=tool_call_id,
            subagent_name=effective_subagent_type,
            description=effective_description,
        )
        executor.session = session
    except Exception:
        logger.exception("Failed to create session for resumed task")

    # Execute with recovery prompt
    new_task_id = executor.execute_async(recovery, task_id=tool_call_id, description=effective_description)

    writer = get_stream_writer()
    writer({"type": "task_started", "task_id": new_task_id, "description": effective_description})

    # Poll for completion (same logic as create)
    poll_count = 0
    last_status = None
    last_message_count = 0
    max_poll_count = (config.timeout_seconds + 60) // 5

    logger.info(f"[trace={trace_id}] Resumed task {task_id} as new task {new_task_id}")

    try:
        while True:
            result = get_background_task_result(new_task_id)
            if result is None:
                writer({"type": "task_failed", "task_id": new_task_id, "error": "Task disappeared"})
                return f"Error: Resumed task {new_task_id} disappeared"

            if result.status != last_status:
                logger.info(f"[trace={trace_id}] Resumed task {new_task_id} status: {result.status.value}")
                last_status = result.status

            current_message_count = len(result.ai_messages)
            if current_message_count > last_message_count:
                for i in range(last_message_count, current_message_count):
                    writer({"type": "task_running", "task_id": new_task_id, "message": result.ai_messages[i]})
                last_message_count = current_message_count

            if result.status == SubagentStatus.COMPLETED:
                writer({"type": "task_completed", "task_id": new_task_id, "result": result.result})
                cleanup_background_task(new_task_id)
                return f"Task Resumed. Result: {result.result}"
            elif result.status == SubagentStatus.FAILED:
                writer({"type": "task_failed", "task_id": new_task_id, "error": result.error})
                cleanup_background_task(new_task_id)
                return f"Task resumed but failed. Error: {result.error}"
            elif result.status == SubagentStatus.CANCELLED:
                cleanup_background_task(new_task_id)
                return "Resumed task cancelled by user."
            elif result.status == SubagentStatus.TIMED_OUT:
                writer({"type": "task_timed_out", "task_id": new_task_id})
                cleanup_background_task(new_task_id)
                return f"Resumed task timed out."

            await asyncio.sleep(5)
            poll_count += 1
            if poll_count > max_poll_count:
                return f"Resumed task polling timed out. Status: {result.status.value}"
    except asyncio.CancelledError:
        request_cancel_background_task(new_task_id)
        raise


def _find_thread_id_for_task(task_id: str) -> str | None:
    """Try to find the thread_id for a task by scanning session directories."""
    try:
        from deerflow.config.paths import get_paths
        threads_dir = get_paths().base_dir / "threads"
        if not threads_dir.exists():
            return None
        for thread_dir in threads_dir.iterdir():
            if not thread_dir.is_dir():
                continue
            subagents_dir = thread_dir / "subagents"
            if subagents_dir.exists():
                jsonl = subagents_dir / f"{task_id}.jsonl"
                if jsonl.exists():
                    return thread_dir.name
    except Exception:
        pass
    return None
