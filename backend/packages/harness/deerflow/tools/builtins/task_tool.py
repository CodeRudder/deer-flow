"""Task tool for delegating work to subagents."""

import asyncio
import logging
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, cast

from langchain.tools import InjectedToolCallId, tool
from langchain_core.callbacks import BaseCallbackManager
from langgraph.config import get_stream_writer

from deerflow.config import get_app_config
from deerflow.models.image_generation.types import ImageGenerationPreference
from deerflow.models.video_generation.types import VideoGenerationPreference
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.sandbox.security import LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE, is_host_bash_allowed
from deerflow.subagents import SubagentExecutor, get_available_subagent_names, get_subagent_config
from deerflow.subagents.config import resolve_subagent_model_name
from deerflow.subagents.executor import (
    SubagentStatus,
    cleanup_background_task,
    get_background_task_result,
    request_cancel_background_task,
)
from deerflow.subagents.session import SubagentSession
from deerflow.tools.types import Runtime

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

TASK_CAPABILITY_IMAGE_GENERATION = "image_generation"
TASK_CAPABILITY_VIDEO_GENERATION = "video_generation"

# Cache subagent token usage by tool_call_id so TokenUsageMiddleware can
# write it back to the triggering AIMessage's usage_metadata.
_subagent_usage_cache: dict[str, dict[str, int]] = {}


@dataclass(frozen=True)
class TaskCapabilityContext:
    """Runtime preferences available to task capability injectors."""

    image_generation: ImageGenerationPreference
    video_generation: VideoGenerationPreference


def _token_usage_cache_enabled(app_config: "AppConfig | None") -> bool:
    if app_config is None:
        try:
            app_config = get_app_config()
        except FileNotFoundError:
            return False
    return bool(getattr(getattr(app_config, "token_usage", None), "enabled", False))


def _cache_subagent_usage(tool_call_id: str, usage: dict | None, *, enabled: bool = True) -> None:
    if enabled and usage:
        _subagent_usage_cache[tool_call_id] = usage


def pop_cached_subagent_usage(tool_call_id: str) -> dict | None:
    return _subagent_usage_cache.pop(tool_call_id, None)


def _is_subagent_terminal(result: Any) -> bool:
    """Return whether a background subagent result is safe to clean up."""
    return result.status in {SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.CANCELLED, SubagentStatus.TIMED_OUT} or getattr(result, "completed_at", None) is not None


async def _await_subagent_terminal(task_id: str, max_polls: int) -> Any | None:
    """Poll until the background subagent reaches a terminal status or we run out of polls."""
    for _ in range(max_polls):
        result = get_background_task_result(task_id)
        if result is None:
            return None
        if _is_subagent_terminal(result):
            return result
        await asyncio.sleep(5)
    return None


async def _deferred_cleanup_subagent_task(task_id: str, trace_id: str, max_polls: int) -> None:
    """Keep polling a cancelled subagent until it can be safely removed."""
    cleanup_poll_count = 0
    while True:
        result = get_background_task_result(task_id)
        if result is None:
            return
        if _is_subagent_terminal(result):
            cleanup_background_task(task_id)
            return
        if cleanup_poll_count >= max_polls:
            logger.warning(f"[trace={trace_id}] Deferred cleanup for task {task_id} timed out after {cleanup_poll_count} polls")
            return
        await asyncio.sleep(5)
        cleanup_poll_count += 1


def _log_cleanup_failure(cleanup_task: asyncio.Task[None], *, trace_id: str, task_id: str) -> None:
    if cleanup_task.cancelled():
        return

    exc = cleanup_task.exception()
    if exc is not None:
        logger.error(f"[trace={trace_id}] Deferred cleanup failed for task {task_id}: {exc}")


def _schedule_deferred_subagent_cleanup(task_id: str, trace_id: str, max_polls: int) -> None:
    logger.debug(f"[trace={trace_id}] Scheduling deferred cleanup for cancelled task {task_id}")
    cleanup_task = asyncio.create_task(_deferred_cleanup_subagent_task(task_id, trace_id, max_polls))
    cleanup_task.add_done_callback(lambda task: _log_cleanup_failure(task, trace_id=trace_id, task_id=task_id))


def _find_usage_recorder(runtime: Any) -> Any | None:
    """Find a callback handler with ``record_external_llm_usage_records`` in the runtime config.

    LangChain may pass ``config["callbacks"]`` in three different shapes:

    - ``None`` (no callbacks registered): no recorder.
    - A plain ``list[BaseCallbackHandler]``: iterate it directly.
    - A ``BaseCallbackManager`` instance (e.g. ``AsyncCallbackManager`` on async
      tool runs): managers are not iterable, so we unwrap ``.handlers`` first.

    Any other shape (e.g. a single handler object accidentally passed without a
    list wrapper) cannot be iterated safely; treat it as "no recorder" rather
    than raise.
    """
    if runtime is None:
        return None
    config = getattr(runtime, "config", None)
    if not isinstance(config, dict):
        return None
    callbacks = config.get("callbacks")
    if isinstance(callbacks, BaseCallbackManager):
        callbacks = callbacks.handlers
    if not callbacks:
        return None
    if not isinstance(callbacks, list):
        return None
    for cb in callbacks:
        if hasattr(cb, "record_external_llm_usage_records"):
            return cb
    return None


def _summarize_usage(records: list[dict] | None) -> dict | None:
    """Summarize token usage records into a compact dict for SSE events."""
    if not records:
        return None
    return {
        "input_tokens": sum(r.get("input_tokens", 0) or 0 for r in records),
        "output_tokens": sum(r.get("output_tokens", 0) or 0 for r in records),
        "total_tokens": sum(r.get("total_tokens", 0) or 0 for r in records),
    }


def _report_subagent_usage(runtime: Any, result: Any) -> None:
    """Report subagent token usage to the parent RunJournal, if available.

    Each subagent task must be reported only once (guarded by usage_reported).
    """
    if getattr(result, "usage_reported", True):
        return
    records = getattr(result, "token_usage_records", None) or []
    if not records:
        return
    journal = _find_usage_recorder(runtime)
    if journal is None:
        logger.debug("No usage recorder found in runtime callbacks — subagent token usage not recorded")
        return
    try:
        journal.record_external_llm_usage_records(records)
        result.usage_reported = True
    except Exception:
        logger.warning("Failed to report subagent token usage", exc_info=True)


def _get_runtime_app_config(runtime: Any) -> "AppConfig | None":
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        app_config = context.get("app_config")
        if app_config is not None:
            return cast("AppConfig", app_config)
    return None


def _merge_skill_allowlists(parent: list[str] | None, child: list[str] | None) -> list[str] | None:
    """Return the effective subagent skill allowlist under the parent policy."""
    if parent is None:
        return child
    if child is None:
        return list(parent)

    parent_set = set(parent)
    return [skill for skill in child if skill in parent_set]


def _start_executor_async(executor: Any, prompt: str, *, task_id: str, description: str) -> str:
    try:
        return executor.execute_async(prompt, task_id=task_id, description=description)
    except TypeError as exc:
        if "description" not in str(exc):
            raise
        return executor.execute_async(prompt, task_id=task_id)


def _status_value(status: object) -> str:
    """Return a comparable status string for real enums and test doubles."""
    value = getattr(status, "value", status)
    return str(value)


def _runtime_value(runtime: Any, key: str) -> Any:
    if runtime is None:
        return None
    context = getattr(runtime, "context", None)
    if isinstance(context, dict) and key in context:
        return context[key]
    config = getattr(runtime, "config", None)
    if isinstance(config, dict):
        context = config.get("context", {})
        if isinstance(context, dict) and key in context:
            return context[key]
        configurable = config.get("configurable", {})
        if isinstance(configurable, dict) and key in configurable:
            return configurable[key]
    return None


def _append_image_generation_preference(prompt: str, image_generation: ImageGenerationPreference | None) -> str:
    if image_generation is None or image_generation.is_empty:
        return prompt

    command_args = []
    if image_generation.model:
        command_args.append(f"--model {image_generation.model}")

    details = []
    if image_generation.model:
        details.append(f"- Model: `{image_generation.model}`")

    preference = (
        "<image_generation_runtime_preference>\n"
        "The parent run selected the following image generation preference for the current run:\n"
        f"{chr(10).join(details)}\n"
        "This is only a preference. If this subtask is not an image generation task, ignore it and proceed normally.\n"
        "If this subtask uses the image-generation skill, call generate.py with these explicit arguments:\n"
        f"`{' '.join(command_args)}`\n"
        "</image_generation_runtime_preference>"
    )
    return f"{prompt}\n\n{preference}"


def _apply_image_generation_capability(prompt: str, context: TaskCapabilityContext) -> str:
    return _append_image_generation_preference(prompt, context.image_generation)


def _append_video_generation_preference(prompt: str, video_generation: VideoGenerationPreference | None) -> str:
    if video_generation is None or video_generation.is_empty:
        return prompt

    command_args = []
    if video_generation.model:
        command_args.append(f"--model {video_generation.model}")

    details = []
    if video_generation.model:
        details.append(f"- Model: `{video_generation.model}`")

    preference = (
        "<video_generation_runtime_preference>\n"
        "The parent run selected the following video generation preference for the current run:\n"
        f"{chr(10).join(details)}\n"
        "This is only a preference. If this subtask is not a video generation task, ignore it and proceed normally.\n"
        "If this subtask uses the video-generation skill, call generate.py with these explicit arguments:\n"
        f"`{' '.join(command_args)}`\n"
        "</video_generation_runtime_preference>"
    )
    return f"{prompt}\n\n{preference}"


def _apply_video_generation_capability(prompt: str, context: TaskCapabilityContext) -> str:
    return _append_video_generation_preference(prompt, context.video_generation)


_TASK_CAPABILITY_INJECTORS = {
    TASK_CAPABILITY_IMAGE_GENERATION: _apply_image_generation_capability,
    TASK_CAPABILITY_VIDEO_GENERATION: _apply_video_generation_capability,
}


def _normalize_task_capabilities(capabilities: list[str] | None) -> list[str]:
    if not capabilities:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for capability in capabilities:
        if not isinstance(capability, str):
            continue
        value = capability.strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _apply_task_capabilities(prompt: str, *, capabilities: list[str], context: TaskCapabilityContext) -> str:
    for capability in capabilities:
        injector = _TASK_CAPABILITY_INJECTORS.get(capability)
        if injector is None:
            logger.debug("Ignoring unknown task capability: %s", capability)
            continue
        prompt = injector(prompt, context)
    return prompt


def _image_generation_preference_from_runtime(runtime: Any) -> ImageGenerationPreference:
    if runtime is None:
        return ImageGenerationPreference()

    metadata = runtime.config.get("metadata", {}) if getattr(runtime, "config", None) else {}
    if not isinstance(metadata, dict):
        metadata = {}

    return ImageGenerationPreference.from_mapping(
        {
            "image_generation_model": _runtime_value(runtime, "image_generation_model") or metadata.get("image_generation_model"),
        }
    )


def _video_generation_preference_from_runtime(runtime: Any) -> VideoGenerationPreference:
    if runtime is None:
        return VideoGenerationPreference()

    metadata = runtime.config.get("metadata", {}) if getattr(runtime, "config", None) else {}
    if not isinstance(metadata, dict):
        metadata = {}

    return VideoGenerationPreference.from_mapping(
        {
            "video_generation_model": _runtime_value(runtime, "video_generation_model") or metadata.get("video_generation_model"),
        }
    )


def _build_recovery_prompt(sessions: list[SubagentSession]) -> str:
    """Build a recovery context from interrupted sub-agent sessions."""
    parts: list[str] = []
    for session in sessions:
        messages = session.read_messages()
        ai_messages = [m for m in messages if m.get("role") == "ai"]
        last_ai = ""
        if ai_messages:
            content = ai_messages[-1].get("content", "")
            last_ai = content[:200] if isinstance(content, str) else str(content)[:200]
        parts.append(f"- Task {session.task_id} ({session.subagent_name}): executed {len(messages)} steps, last AI response: {last_ai}")
    return "<recovery_context>\nThe following sub-tasks were previously interrupted. Continue from where they left off without repeating completed work:\n" + "\n".join(parts) + "\n</recovery_context>"


async def _create_persisted_session(
    thread_id: str,
    task_id: str,
    subagent_name: str,
    description: str,
    user_id: str | None = None,
    capabilities: list[str] | None = None,
) -> SubagentSession | None:
    """Create a sub-agent session and write the initial summary off the event loop."""

    def _create() -> SubagentSession:
        session = SubagentSession(
            thread_id=thread_id,
            task_id=task_id,
            subagent_name=subagent_name,
            description=description,
            user_id=user_id,
            capabilities=capabilities,
        )
        session._write_summary("running", message_count=0)
        return session

    try:
        return await asyncio.to_thread(_create)
    except Exception:
        logger.exception("Failed to create SubagentSession for thread=%s, task=%s", thread_id, task_id)
        return None


async def _find_interrupted_sessions(thread_id: str, *, user_id: str | None = None) -> list[SubagentSession]:
    """Read interrupted sessions without blocking the event loop."""
    try:
        return await asyncio.to_thread(SubagentSession.find_interrupted, thread_id, user_id=user_id)
    except Exception:
        logger.exception("Failed to check interrupted sessions for thread=%s", thread_id)
        return []


async def _find_thread_id_for_task_async(task_id: str, *, user_id: str | None = None) -> str | None:
    """Look up a task's thread without blocking the event loop."""
    return await asyncio.to_thread(_find_thread_id_for_task, task_id, user_id=user_id)


async def _get_resume_info_async(task_id: str, thread_id: str, *, user_id: str | None = None) -> dict[str, object] | None:
    """Read resume metadata without blocking the event loop."""
    return await asyncio.to_thread(SubagentSession.get_resume_info, task_id, thread_id, user_id=user_id)


async def _write_cancelled_summary(session: SubagentSession, message_count: int) -> None:
    """Persist a cancelled summary off the event loop."""
    await asyncio.to_thread(session._write_summary, "cancelled", message_count=message_count)


async def _is_cancel_requested(session: SubagentSession) -> bool:
    """Check the cross-process cancel marker without blocking the event loop."""
    return await asyncio.to_thread(session.is_cancel_requested)


@tool("task", parse_docstring=True)
async def task_tool(
    runtime: Runtime,
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    max_turns: int | None = None,
    task_id: str | None = None,
    action: str = "create",
    capabilities: list[str] | None = None,
) -> str:
    """Delegate a task to a specialized subagent that runs in its own context.

    Subagents help you:
    - Preserve context by keeping exploration and implementation separate
    - Handle complex multi-step tasks autonomously
    - Execute commands or operations in isolated contexts

    Built-in subagent types:
    - **general-purpose**: A capable agent for complex, multi-step tasks that require
      both exploration and action. Use when the task requires complex reasoning,
      multiple dependent steps, or would benefit from isolated context.
    - **bash**: Command execution specialist for running bash commands. This is only
      available when host bash is explicitly allowed or when using an isolated shell
      sandbox such as `AioSandboxProvider`.

    Additional custom subagent types may be defined in config.yaml under
    `subagents.custom_agents`. Each custom type can have its own system prompt,
    tools, skills, model, and timeout configuration. If an unknown subagent_type
    is provided, the error message will list all available types.

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
    - **cancel**: Cancel a running subtask.
    - **query**: Query a subtask's status and result.

    Capabilities:
    - Use capabilities=["image_generation"] only for subtasks that create, edit, or generate images.
    - Use capabilities=["video_generation"] only for subtasks that create or generate videos.
    - Leave capabilities empty for normal calculation, analysis, code, search, summarization, and file-operation subtasks.

    Args:
        description: A short (3-5 word) description of the task for logging/display. ALWAYS PROVIDE THIS PARAMETER FIRST.
        prompt: The task description for the subagent. Be specific and clear about what needs to be done. ALWAYS PROVIDE THIS PARAMETER SECOND.
        subagent_type: The type of subagent to use. ALWAYS PROVIDE THIS PARAMETER THIRD.
        max_turns: Optional maximum number of agent turns. Defaults to subagent's configured max.
        task_id: Target subtask ID for resume/cancel/query actions. Not needed for create.
        action: Action to perform: "create" (default), "resume", "cancel", or "query".
        capabilities: Optional subtask capability keys. Use ["image_generation"] only when this subtask creates or edits images; use ["video_generation"] only when this subtask creates or edits videos.
    """
    if action == "cancel":
        return await _action_cancel(runtime, task_id)
    if action == "query":
        return await _action_query(runtime, task_id)
    if action == "resume":
        return await _action_resume(runtime, task_id, tool_call_id, description, prompt, subagent_type, max_turns, capabilities)

    runtime_app_config = _get_runtime_app_config(runtime)
    cache_token_usage = _token_usage_cache_enabled(runtime_app_config)
    available_subagent_names = get_available_subagent_names(app_config=runtime_app_config) if runtime_app_config is not None else get_available_subagent_names()

    # Get subagent configuration
    config = get_subagent_config(subagent_type, app_config=runtime_app_config) if runtime_app_config is not None else get_subagent_config(subagent_type)
    if config is None:
        available = ", ".join(available_subagent_names)
        return f"Error: Unknown subagent type '{subagent_type}'. Available: {available}"
    if subagent_type == "bash":
        host_bash_allowed = is_host_bash_allowed(runtime_app_config) if runtime_app_config is not None else is_host_bash_allowed()
        if not host_bash_allowed:
            return f"Error: {LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE}"

    # Build config overrides
    overrides: dict = {}

    # Skills are loaded by SubagentExecutor per-session (aligned with Codex's pattern:
    # each subagent loads its own skills based on config, injected as conversation items).
    # No longer appended to system_prompt here.
    if max_turns is not None:
        overrides["max_turns"] = max_turns

    # Extract parent context from runtime
    sandbox_state = None
    thread_data = None
    thread_id = None
    user_id = None
    parent_model = None
    trace_id = None
    metadata: dict = {}
    image_generation = ImageGenerationPreference()
    video_generation = VideoGenerationPreference()

    if runtime is not None:
        sandbox_state = runtime.state.get("sandbox")
        thread_data = runtime.state.get("thread_data")
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            thread_id = runtime.config.get("configurable", {}).get("thread_id")
        user_id = resolve_runtime_user_id(runtime)

        # Try to get parent model from configurable
        metadata = runtime.config.get("metadata", {})
        parent_model = metadata.get("model_name")

        # Get or generate trace_id for distributed tracing
        trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]
        image_generation = _image_generation_preference_from_runtime(runtime)
        video_generation = _video_generation_preference_from_runtime(runtime)

    task_capabilities = _normalize_task_capabilities(capabilities)
    capability_context = TaskCapabilityContext(image_generation=image_generation, video_generation=video_generation)
    prompt = _apply_task_capabilities(prompt, capabilities=task_capabilities, context=capability_context)

    # Get user_id for tracing (uses standard resolution order)
    user_id = resolve_runtime_user_id(runtime)

    # Propagate the authenticated runtime context so delegated tool calls are
    # evaluated by GuardrailMiddleware with the same identity/attribution as
    # the lead agent. Sourced from the server-side context written by
    # inject_authenticated_user_context (and run_id by the run worker); stays
    # None when absent (e.g. internal-auth runs) so guardrail behavior is
    # unchanged. Without this, role-aware policy silently mis-attributes any
    # tool call delegated to a subagent (user_role=None).
    parent_context = runtime.context if runtime is not None else None
    parent_context = parent_context if isinstance(parent_context, dict) else {}
    user_role = parent_context.get("user_role")
    oauth_provider = parent_context.get("oauth_provider")
    oauth_id = parent_context.get("oauth_id")
    run_id = parent_context.get("run_id")
    quota_runtime_bridge = parent_context.get("__quota_runtime_bridge")

    parent_available_skills = metadata.get("available_skills")
    if parent_available_skills is not None:
        overrides["skills"] = _merge_skill_allowlists(list(parent_available_skills), config.skills)

    if overrides:
        config = replace(config, **overrides)

    # Get available tools (excluding task tool to prevent nesting)
    # Lazy import to avoid circular dependency
    from deerflow.tools import get_available_tools

    # Inherit parent agent's tool_groups so subagents respect the same restrictions
    parent_tool_groups = metadata.get("tool_groups")
    resolved_app_config = runtime_app_config
    if config.model == "inherit" and parent_model is None and resolved_app_config is None:
        resolved_app_config = get_app_config()
    effective_model = resolve_subagent_model_name(config, parent_model, app_config=resolved_app_config)

    # Subagents should not have subagent tools enabled (prevent recursive nesting)
    available_tools_kwargs = {
        "model_name": effective_model,
        "groups": parent_tool_groups,
        "subagent_enabled": False,
    }
    if resolved_app_config is not None:
        available_tools_kwargs["app_config"] = resolved_app_config
    tools = get_available_tools(**available_tools_kwargs)

    # Create executor
    executor_kwargs = {
        "config": config,
        "tools": tools,
        "parent_model": parent_model,
        "sandbox_state": sandbox_state,
        "thread_data": thread_data,
        "thread_id": thread_id,
        "user_id": user_id,
        "trace_id": trace_id,
        "image_generation": image_generation,
        "video_generation": video_generation,
        "user_role": user_role,
        "oauth_provider": oauth_provider,
        "oauth_id": oauth_id,
        "run_id": run_id,
        "quota_runtime_bridge": quota_runtime_bridge,
    }
    if resolved_app_config is not None:
        executor_kwargs["app_config"] = resolved_app_config
    executor = SubagentExecutor(**executor_kwargs)

    session: SubagentSession | None = None
    if thread_id:
        session = await _create_persisted_session(thread_id, tool_call_id, subagent_type, description, user_id=user_id, capabilities=task_capabilities)
        if session is not None:
            executor.session = session
            logger.info("Created SubagentSession for thread=%s, task=%s, subagent=%s", thread_id, tool_call_id, subagent_type)
    else:
        logger.warning("No thread_id available — subagent session will NOT be persisted")

    if thread_id and session is not None:
        interrupted = await _find_interrupted_sessions(thread_id, user_id=user_id)
        if interrupted:
            recovery = await asyncio.to_thread(_build_recovery_prompt, interrupted)
            prompt = recovery + "\n\n" + prompt
            logger.info("Injected recovery context from %d interrupted session(s)", len(interrupted))

    # Start background execution (always async to prevent blocking)
    # Use tool_call_id as task_id for better traceability
    task_id = _start_executor_async(executor, prompt, task_id=tool_call_id, description=description)

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
                logger.info(f"[trace={trace_id}] Task {task_id} status: {_status_value(result.status)}")
                last_status = result.status

            if session is not None and await _is_cancel_requested(session):
                request_cancel_background_task(task_id)
                logger.info(f"[trace={trace_id}] Task {task_id} cancel marker detected")

            # Check for new AI messages and send task_running events
            ai_messages = result.ai_messages or []
            current_message_count = len(ai_messages)
            if current_message_count > last_message_count:
                # Send task_running event for each new message
                for i in range(last_message_count, current_message_count):
                    message = ai_messages[i]
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
            usage = _summarize_usage(getattr(result, "token_usage_records", None))
            status = _status_value(result.status)
            if status == "completed":
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_completed", "task_id": task_id, "result": result.result, "usage": usage})
                logger.info(f"[trace={trace_id}] Task {task_id} completed after {poll_count} polls")
                cleanup_background_task(task_id)
                return f"Task Succeeded. Result: {result.result}"
            elif status == "failed":
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_failed", "task_id": task_id, "error": result.error, "usage": usage})
                logger.error(f"[trace={trace_id}] Task {task_id} failed: {result.error}")
                cleanup_background_task(task_id)
                return f"Task failed. Error: {result.error}"
            elif status == "cancelled":
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_cancelled", "task_id": task_id, "error": result.error, "usage": usage})
                logger.info(f"[trace={trace_id}] Task {task_id} cancelled: {result.error}")
                cleanup_background_task(task_id)
                return "Task cancelled by user."
            elif status == "timed_out":
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_timed_out", "task_id": task_id, "error": result.error, "usage": usage})
                logger.warning(f"[trace={trace_id}] Task {task_id} timed out: {result.error}")
                cleanup_background_task(task_id)
                return f"Task timed out. Error: {result.error}"

            # Still running, wait before next poll
            await asyncio.sleep(5)
            poll_count += 1

            # Polling timeout as a safety net (in case thread pool timeout doesn't work)
            # Set to execution timeout + 60s buffer, in 5s poll intervals
            # This catches edge cases where the background task gets stuck
            if poll_count > max_poll_count:
                timeout_minutes = config.timeout_seconds // 60
                logger.error(f"[trace={trace_id}] Task {task_id} polling timed out after {poll_count} polls (should have been caught by thread pool timeout)")
                _report_subagent_usage(runtime, result)
                usage = _summarize_usage(getattr(result, "token_usage_records", None))
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                writer({"type": "task_timed_out", "task_id": task_id, "usage": usage})
                # The task may still be running in the background. Signal cooperative
                # cancellation and schedule deferred cleanup to remove the entry from
                # _background_tasks once the background thread reaches a terminal state.
                request_cancel_background_task(task_id)
                _schedule_deferred_subagent_cleanup(task_id, trace_id, max_poll_count)
                return f"Task polling timed out after {timeout_minutes} minutes. This may indicate the background task is stuck. Status: {_status_value(result.status)}"
    except asyncio.CancelledError:
        # Signal the background subagent thread to stop cooperatively.
        request_cancel_background_task(task_id)

        # Wait (shielded) for the subagent to reach a terminal state so the
        # final token usage snapshot is reported to the parent RunJournal
        # before the parent worker persists get_completion_data().
        terminal_result = None
        try:
            terminal_result = await asyncio.shield(_await_subagent_terminal(task_id, max_poll_count))
        except asyncio.CancelledError:
            pass

        # Report whatever the subagent collected (even if we timed out).
        final_result = terminal_result or get_background_task_result(task_id)
        if final_result is not None:
            _report_subagent_usage(runtime, final_result)
        if final_result is not None and _is_subagent_terminal(final_result):
            cleanup_background_task(task_id)
        else:
            _schedule_deferred_subagent_cleanup(task_id, trace_id, max_poll_count)
        _subagent_usage_cache.pop(tool_call_id, None)
        raise
    except Exception:
        _subagent_usage_cache.pop(tool_call_id, None)
        raise


async def _action_cancel(runtime: Runtime, task_id: str | None) -> str:
    """Cancel a running or interrupted subtask."""
    if not task_id:
        return "Error: task_id is required for cancel action"

    user_id = resolve_runtime_user_id(runtime)
    result = get_background_task_result(task_id)
    if result is None:
        thread_id = await _find_thread_id_for_task_async(task_id, user_id=user_id)
        if thread_id:
            try:
                session = SubagentSession(thread_id=thread_id, task_id=task_id, subagent_name="", description="", user_id=user_id)
                summary = await asyncio.to_thread(session.read_summary)
                if summary and summary.get("status") in ("running", "pending", "unknown", "interrupted"):
                    await _write_cancelled_summary(session, message_count=summary.get("message_count", 0))
                    logger.info("Marked interrupted task %s as cancelled on disk", task_id)
                    return f"Task {task_id} cancelled successfully."
            except Exception:
                logger.exception("Failed to cancel task %s on disk", task_id)
        return f"Error: Task {task_id} not found"

    status = _status_value(result.status)
    if status in ("running", "pending"):
        request_cancel_background_task(task_id)
        logger.info("Cancelled subtask %s via task tool", task_id)
        return f"Task {task_id} cancelled successfully."

    if status == "interrupted":
        result.status = SubagentStatus.CANCELLED
        result.error = "Cancelled by user"
        result.completed_at = datetime.now()
        cleanup_background_task(task_id)
        logger.info("Marked interrupted task %s as cancelled", task_id)
        return f"Task {task_id} cancelled successfully."

    return f"Error: Task {task_id} is {status}, cannot cancel"


async def _action_query(runtime: Runtime, task_id: str | None) -> str:
    """Query subtask status and result."""
    if not task_id:
        return "Error: task_id is required for query action"

    user_id = resolve_runtime_user_id(runtime)
    result = get_background_task_result(task_id)
    if result is not None:
        status = _status_value(result.status)
        parts = [f"Task {task_id}: status={status}"]
        if result.result:
            parts.append(f"result={result.result[:500]}")
        if result.error:
            parts.append(f"error={result.error[:300]}")
        return "\n".join(parts)

    try:
        thread_id = await _find_thread_id_for_task_async(task_id, user_id=user_id)
        info = await _get_resume_info_async(task_id, thread_id, user_id=user_id) if thread_id else None
        if info:
            return f"Task {task_id}: status={info['status']}, subagent={info['subagent_type']}, steps={info['message_count']}"
    except Exception:
        logger.exception("Failed to query task %s from disk", task_id)

    return f"Error: Task {task_id} not found (neither in memory nor on disk)"


async def _action_resume(
    runtime: Runtime,
    task_id: str | None,
    tool_call_id: str,
    description: str,
    prompt: str,
    subagent_type: str,
    max_turns: int | None,
    capabilities: list[str] | None = None,
) -> str:
    """Resume an interrupted/failed subtask from where it left off."""
    if not task_id:
        return "Error: task_id is required for resume action"

    runtime_app_config = _get_runtime_app_config(runtime)
    cache_token_usage = _token_usage_cache_enabled(runtime_app_config)

    thread_id = _runtime_value(runtime, "thread_id")
    if not thread_id:
        return f"Error: Cannot determine thread_id for resuming task {task_id}"

    user_id = resolve_runtime_user_id(runtime)
    info = await _get_resume_info_async(task_id, thread_id, user_id=user_id)
    if info is None:
        return f"Error: No session found for task {task_id} in thread {thread_id}"

    effective_subagent_type = info.get("subagent_type") or subagent_type
    effective_description = description or f"Resume: {info.get('description', '')}"
    config = get_subagent_config(effective_subagent_type, app_config=runtime_app_config) if runtime_app_config is not None else get_subagent_config(effective_subagent_type)
    if config is None:
        available_names = get_available_subagent_names(app_config=runtime_app_config) if runtime_app_config is not None else get_available_subagent_names()
        return f"Error: Unknown subagent type '{effective_subagent_type}'. Available: {', '.join(available_names)}"
    if effective_subagent_type == "bash":
        host_bash_allowed = is_host_bash_allowed(runtime_app_config) if runtime_app_config is not None else is_host_bash_allowed()
        if not host_bash_allowed:
            return f"Error: {LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE}"

    overrides: dict = {}
    if max_turns is not None:
        overrides["max_turns"] = max_turns

    sandbox_state = runtime.state.get("sandbox") if runtime is not None else None
    thread_data = runtime.state.get("thread_data") if runtime is not None else None
    metadata = runtime.config.get("metadata", {}) if runtime is not None else {}
    parent_model = metadata.get("model_name") if isinstance(metadata, dict) else None
    trace_id = metadata.get("trace_id") if isinstance(metadata, dict) else None
    trace_id = trace_id or str(uuid.uuid4())[:8]
    image_generation = _image_generation_preference_from_runtime(runtime)
    video_generation = _video_generation_preference_from_runtime(runtime)
    parent_context = runtime.context if runtime is not None else None
    parent_context = parent_context if isinstance(parent_context, dict) else {}
    user_role = parent_context.get("user_role")
    oauth_provider = parent_context.get("oauth_provider")
    oauth_id = parent_context.get("oauth_id")
    run_id = parent_context.get("run_id")
    quota_runtime_bridge = parent_context.get("__quota_runtime_bridge")
    raw_resume_capabilities = info.get("capabilities")
    task_capabilities = _normalize_task_capabilities(raw_resume_capabilities if isinstance(raw_resume_capabilities, list) and raw_resume_capabilities else capabilities)
    capability_context = TaskCapabilityContext(image_generation=image_generation, video_generation=video_generation)

    parent_available_skills = metadata.get("available_skills") if isinstance(metadata, dict) else None
    if parent_available_skills is not None:
        overrides["skills"] = _merge_skill_allowlists(list(parent_available_skills), config.skills)
    if overrides:
        config = replace(config, **overrides)

    recovery = (
        f"<recovery>\n"
        f"任务被中断。已执行 {info.get('message_count', 0)} 步。\n"
        f"最后完成的工作：{info.get('last_ai_content') or '（无）'}\n"
        f"原始任务：{str(info.get('original_prompt') or '')[:500]}\n"
        f"请继续完成剩余工作，不要重复已完成的步骤。\n"
        f"</recovery>\n\n"
        f"{info.get('original_prompt') or prompt}"
    )
    recovery = _apply_task_capabilities(recovery, capabilities=task_capabilities, context=capability_context)

    from deerflow.tools import get_available_tools

    parent_tool_groups = metadata.get("tool_groups") if isinstance(metadata, dict) else None
    resolved_app_config = runtime_app_config
    if config.model == "inherit" and parent_model is None and resolved_app_config is None:
        resolved_app_config = get_app_config()
    effective_model = resolve_subagent_model_name(config, parent_model, app_config=resolved_app_config)
    available_tools_kwargs = {
        "model_name": effective_model,
        "groups": parent_tool_groups,
        "subagent_enabled": False,
    }
    if resolved_app_config is not None:
        available_tools_kwargs["app_config"] = resolved_app_config
    tools = get_available_tools(**available_tools_kwargs)

    executor_kwargs = {
        "config": config,
        "tools": tools,
        "parent_model": parent_model,
        "sandbox_state": sandbox_state,
        "thread_data": thread_data,
        "thread_id": thread_id,
        "user_id": user_id,
        "trace_id": trace_id,
        "image_generation": image_generation,
        "video_generation": video_generation,
        "user_role": user_role,
        "oauth_provider": oauth_provider,
        "oauth_id": oauth_id,
        "run_id": run_id,
        "quota_runtime_bridge": quota_runtime_bridge,
    }
    if resolved_app_config is not None:
        executor_kwargs["app_config"] = resolved_app_config
    executor = SubagentExecutor(**executor_kwargs)

    session = await _create_persisted_session(thread_id, tool_call_id, effective_subagent_type, effective_description, user_id=user_id, capabilities=task_capabilities)
    if session is not None:
        executor.session = session

    new_task_id = _start_executor_async(executor, recovery, task_id=tool_call_id, description=effective_description)
    writer = get_stream_writer()
    writer({"type": "task_started", "task_id": new_task_id, "description": effective_description})

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
                logger.info(f"[trace={trace_id}] Resumed task {new_task_id} status: {_status_value(result.status)}")
                last_status = result.status

            if session is not None and await _is_cancel_requested(session):
                request_cancel_background_task(new_task_id)

            ai_messages = result.ai_messages or []
            if len(ai_messages) > last_message_count:
                for i in range(last_message_count, len(ai_messages)):
                    writer({"type": "task_running", "task_id": new_task_id, "message": ai_messages[i]})
                last_message_count = len(ai_messages)

            usage = _summarize_usage(getattr(result, "token_usage_records", None))
            status = _status_value(result.status)
            if status == "completed":
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_completed", "task_id": new_task_id, "result": result.result, "usage": usage})
                cleanup_background_task(new_task_id)
                return f"Task Resumed. Result: {result.result}"
            if status == "failed":
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_failed", "task_id": new_task_id, "error": result.error, "usage": usage})
                cleanup_background_task(new_task_id)
                return f"Task resumed but failed. Error: {result.error}"
            if status == "cancelled":
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                cleanup_background_task(new_task_id)
                return "Resumed task cancelled by user."
            if status == "timed_out":
                _cache_subagent_usage(tool_call_id, usage, enabled=cache_token_usage)
                _report_subagent_usage(runtime, result)
                writer({"type": "task_timed_out", "task_id": new_task_id, "usage": usage})
                cleanup_background_task(new_task_id)
                return "Resumed task timed out."

            await asyncio.sleep(5)
            poll_count += 1
            if poll_count > max_poll_count:
                request_cancel_background_task(new_task_id)
                _schedule_deferred_subagent_cleanup(new_task_id, trace_id, max_poll_count)
                return f"Resumed task polling timed out. Status: {_status_value(result.status)}"
    except asyncio.CancelledError:
        request_cancel_background_task(new_task_id)
        raise


def _find_thread_id_for_task(task_id: str, *, user_id: str | None = None) -> str | None:
    """Try to find the thread_id for a task by scanning session directories."""
    try:
        from deerflow.config.paths import get_paths

        if user_id is None:
            return None
        threads_dir = get_paths().user_dir(user_id) / "threads"
        if not threads_dir.exists():
            return None
        for thread_dir in threads_dir.iterdir():
            if not thread_dir.is_dir():
                continue
            subagents_dir = thread_dir / "subagents"
            if not subagents_dir.exists():
                continue
            if (subagents_dir / f"{task_id}.jsonl").exists():
                return thread_dir.name
    except Exception:
        pass
    return None
