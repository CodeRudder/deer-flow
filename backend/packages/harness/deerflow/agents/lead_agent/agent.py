import logging

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, SummarizationMiddleware
from langchain_core.runnables import RunnableConfig

from deerflow.agents.lead_agent.prompt import apply_prompt_template
from deerflow.agents.middlewares.clarification_middleware import ClarificationMiddleware
from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
from deerflow.agents.middlewares.main_session_middleware import MainSessionMiddleware
from deerflow.agents.middlewares.retryable_summarization_middleware import RetryableSummarizationMiddleware
from deerflow.agents.middlewares.summarization_loop_middleware import SummarizationLoopMiddleware

from deerflow.agents.lead_agent.prompt import apply_prompt_template
from deerflow.agents.middlewares.clarification_middleware import ClarificationMiddleware
from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
from deerflow.agents.middlewares.main_session_middleware import MainSessionMiddleware
from deerflow.agents.middlewares.summarization_loop_middleware import SummarizationLoopMiddleware
from deerflow.agents.middlewares.memory_middleware import MemoryMiddleware
from deerflow.agents.middlewares.subagent_limit_middleware import SubagentLimitMiddleware
from deerflow.agents.middlewares.title_middleware import TitleMiddleware
from deerflow.agents.middlewares.todo_middleware import TodoMiddleware
from deerflow.agents.middlewares.token_usage_middleware import TokenUsageMiddleware
from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares
from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware
from deerflow.agents.thread_state import ThreadState
from deerflow.config.agents_config import load_agent_config
from deerflow.config.app_config import get_app_config
from deerflow.config.summarization_config import get_summarization_config
from deerflow.models import create_chat_model

logger = logging.getLogger(__name__)


def _resolve_model_name(requested_model_name: str | None = None) -> str:
    """Resolve a runtime model name safely, falling back to default if invalid. Returns None if no models are configured."""
    app_config = get_app_config()
    default_model_name = app_config.models[0].name if app_config.models else None
    if default_model_name is None:
        raise ValueError("No chat models are configured. Please configure at least one model in config.yaml.")

    if requested_model_name and app_config.get_model_config(requested_model_name):
        return requested_model_name

    if requested_model_name and requested_model_name != default_model_name:
        logger.warning(f"Model '{requested_model_name}' not found in config; fallback to default model '{default_model_name}'.")
    return default_model_name


def _create_summarization_middleware() -> SummarizationMiddleware | None:
    """Create and configure the summarization middleware from config."""
    config = get_summarization_config()

    if not config.enabled:
        return None

    # Prepare trigger parameter
    trigger = None
    if config.trigger is not None:
        if isinstance(config.trigger, list):
            trigger = [t.to_tuple() for t in config.trigger]
        else:
            trigger = config.trigger.to_tuple()

    # Prepare keep parameter
    keep = config.keep.to_tuple()

    # Prepare model parameter
    if config.model_name:
        model = create_chat_model(name=config.model_name, thinking_enabled=False)
    else:
        # Use a lightweight model for summarization to save costs
        # Falls back to default model if not explicitly specified
        model = create_chat_model(thinking_enabled=False)

    # Prepare kwargs
    kwargs = {
        "model": model,
        "trigger": trigger,
        "keep": keep,
    }

    if config.trim_tokens_to_summarize is not None:
        kwargs["trim_tokens_to_summarize"] = config.trim_tokens_to_summarize

    if config.summary_prompt is not None:
        kwargs["summary_prompt"] = config.summary_prompt
    else:
        # Use task-aware summary prompt that tracks task progress
        kwargs["summary_prompt"] = _TASK_AWARE_SUMMARY_PROMPT

    return RetryableSummarizationMiddleware(**kwargs)


_TASK_AWARE_SUMMARY_PROMPT = """<role>
Context Extraction Assistant
</role>

<primary_objective>
Extract the most critical context from the conversation history. The summary will REPLACE the existing history, so focus on what's essential for continuing work.
</primary_objective>

<output_format>
Produce a summary with EXACTLY these sections:

<task_goal>
[The user's original goal/requirement in 1-3 sentences]
</task_goal>

<key_messages>
[Select up to 10 of the most IMPORTANT messages from the conversation. For each:]
- msg[N] (role): [1-2 sentence summary of key content, preserving file paths, URLs, technical details, decisions, and error messages]
[Include: user requests, key decisions, completed task results, error resolutions.]
[Exclude: routine tool outputs, file contents, intermediate exploration steps.]
</key_messages>

<recent_messages>
[The LAST 5 messages verbatim or near-verbatim — these are the most recent context and must be preserved accurately.]
</recent_messages>

<task_progress>
Last task status: [completed / in-progress / not-started]
Tasks completed: [list or "none"]
Tasks pending: [list or "none"]
</task_progress>
</output_format>

<rules>
1. PRESERVE: user goals, decisions, file paths, URLs, error messages, technical details.
2. DISCARD: large file contents, routine tool outputs, exploration noise.
3. NEVER include full file contents — summarize each file reference in 1-2 sentences.
4. If the agent read >5 files or >50KB total, summarize each in ONE sentence only.

**CRITICAL — Main Session Restriction:**
The main session MUST NOT read large amounts of files or write code directly. Complex tasks MUST be delegated to subtasks via the `task` tool, with relevant file paths described in the subtask prompt.
If the agent performed these operations directly:
- Summarize what was done in 1-2 sentences (keep the result, discard the process details).
- Add a reminder in the summary: "File reading and code writing should use subtasks — describe relevant files in the subtask prompt. Do NOT read files or write code in the main session."

**CRITICAL — Stuck/Loop Detection:**
If the agent repeated a failing action 2+ times or no tasks were completed:
- Note the failure pattern once with the error message.
- Recommend breaking remaining work into smaller subtasks.
- Do NOT repeat the failed approach in the summary.

**CRITICAL — Subtask Failure Recovery:**
When a subtask fails or is interrupted, the main session MUST NOT take over and execute the work directly (no reading files, no writing code, no running commands).
Instead, ALWAYS recover by creating a new subtask or resuming the failed one:
- Add a reminder: "Subtask failed — MUST create a new subtask to retry. Do NOT execute directly in the main session."
- Include the failed task description and any error details in the new subtask prompt.
- The main session is a task orchestrator, not a code executor.
</rules>

Do NOT include any additional text before or after the extracted context.

<messages>
Messages to summarize:
{messages}
</messages>"""


def _create_todo_list_middleware(is_plan_mode: bool) -> TodoMiddleware | None:
    """Create and configure the TodoList middleware.

    Args:
        is_plan_mode: Whether to enable plan mode with TodoList middleware.

    Returns:
        TodoMiddleware instance if plan mode is enabled, None otherwise.
    """
    if not is_plan_mode:
        return None

    # Custom prompts matching DeerFlow's style
    system_prompt = """
<todo_list_system>
You have access to the `write_todos` tool to help you manage and track complex multi-step objectives.

**CRITICAL RULES:**
- Mark todos as completed IMMEDIATELY after finishing each step - do NOT batch completions
- Keep EXACTLY ONE task as `in_progress` at any time (unless tasks can run in parallel)
- Update the todo list in REAL-TIME as you work - this gives users visibility into your progress
- DO NOT use this tool for simple tasks (< 3 steps) - just complete them directly

**When to Use:**
This tool is designed for complex objectives that require systematic tracking:
- Complex multi-step tasks requiring 3+ distinct steps
- Non-trivial tasks needing careful planning and execution
- User explicitly requests a todo list
- User provides multiple tasks (numbered or comma-separated list)
- The plan may need revisions based on intermediate results

**When NOT to Use:**
- Single, straightforward tasks
- Trivial tasks (< 3 steps)
- Purely conversational or informational requests
- Simple tool calls where the approach is obvious

**Incremental Operations (preferred for updates):**
The tool supports three modes — use the appropriate one to avoid losing existing tasks:

1. **Full replace** (`todos`): Provide the entire list. Use ONLY when creating a new plan from scratch or explicitly rewriting ALL tasks. AVOID using this for status updates — use `updates` instead.

2. **Update items** (`updates`): Change status/content of specific items by index. Other items are preserved.
   Example: `write_todos(updates=[{"index": 0, "status": "completed"}, {"index": 1, "status": "in_progress"}])`
   Remove an item: `write_todos(updates=[{"index": 2, "remove": true}])`

3. **Add items** (`adds`): Insert new tasks. Without `index` → append to end. With `index` → insert at that position.
   Example: `write_todos(adds=[{"content": "New task", "status": "pending"}])`
   Example with position: `write_todos(adds=[{"content": "Urgent", "status": "in_progress", "index": 0}])`

You can combine `updates` and `adds` in a single call:
   `write_todos(updates=[{"index": 0, "status": "completed"}], adds=[{"content": "Follow-up", "status": "pending"}])`

**IMPORTANT:** Do NOT combine `todos` with `updates`/`adds` — use one mode per call.

**Subtask Execution:**
- Use the `task` tool to delegate todos to sub-agents for parallel execution
- Split work into subtasks of ~10 minutes each — avoid oversized tasks
- When a subtask is abnormally interrupted or fails, create a new subtask to resume and complete the remaining work
- After all subtasks finish, verify results and proceed to the next todo item

**Task Management:**
Writing todos takes time and tokens - use it when helpful for managing complex problems, not for simple requests.
</todo_list_system>
"""

    tool_description = """Use this tool to create and manage a structured task list for complex work sessions.

**IMPORTANT: Only use this tool for complex tasks (3+ steps). For simple requests, just do the work directly.**

## Parameters

- `todos`: Full task list — replaces ALL existing tasks. Use ONLY for initial plan creation or explicit full rewrite.
- `updates`: Update/remove items by index — `[{"index": 0, "status": "completed"}]` or `[{"index": 2, "remove": true}]`
- `adds`: Insert new tasks — `[{"content": "task desc", "status?": "pending", "index?": 0}]`

Rules:
- Use ONLY `todos` (full replace) OR `updates`/`adds` (incremental) — never both together.
- `updates` and `adds` can be combined in one call (updates applied first, then adds).
- For `adds`: omit `index` to append at end; specify `index` to insert at that position.
- For `updates`: use `"remove": true` to delete an item by index.

## When to Use

1. Complex multi-step tasks (3+ steps)
2. Non-trivial tasks needing careful planning
3. User explicitly requests a todo list
4. Multiple tasks provided
5. Dynamic plans needing updates

## When NOT to Use

1. Straightforward tasks (< 3 steps)
2. Trivial tasks
3. Purely conversational requests
4. Clear what to do — just do it directly

## Task States

- `pending`: Not yet started
- `in_progress`: Currently working on
- `completed`: Finished successfully

## Best Practices

- Mark first task(s) as `in_progress` immediately
- Always have at least one `in_progress` task until all are completed
- Update status in real-time — don't batch completions
- Only mark `completed` when FULLY done
- Use `updates` to change status of individual tasks (prevents accidentally dropping other tasks)

**Remember**: If you only need a few tool calls and it's clear what to do, just do the work directly.
"""

    return TodoMiddleware(system_prompt=system_prompt, tool_description=tool_description)


# ThreadDataMiddleware must be before SandboxMiddleware to ensure thread_id is available
# UploadsMiddleware should be after ThreadDataMiddleware to access thread_id
# DanglingToolCallMiddleware patches missing ToolMessages before model sees the history
# SummarizationMiddleware should be early to reduce context before other processing
# TodoListMiddleware should be before ClarificationMiddleware to allow todo management
# TitleMiddleware generates title after first exchange
# MemoryMiddleware queues conversation for memory update (after TitleMiddleware)
# ViewImageMiddleware should be before ClarificationMiddleware to inject image details before LLM
# ToolErrorHandlingMiddleware should be before ClarificationMiddleware to convert tool exceptions to ToolMessages
# ClarificationMiddleware should be last to intercept clarification requests after model calls
def _build_middlewares(config: RunnableConfig, model_name: str | None, agent_name: str | None = None, custom_middlewares: list[AgentMiddleware] | None = None):
    """Build middleware chain based on runtime configuration.

    Args:
        config: Runtime configuration containing configurable options like is_plan_mode.
        agent_name: If provided, MemoryMiddleware will use per-agent memory storage.
        custom_middlewares: Optional list of custom middlewares to inject into the chain.

    Returns:
        List of middleware instances.
    """
    middlewares = build_lead_runtime_middlewares(lazy_init=True)

    # Persist main conversation to local JSONL for debugging (always on)
    middlewares.append(MainSessionMiddleware())

    # Add summarization middleware if enabled
    summarization_middleware = _create_summarization_middleware()
    if summarization_middleware is not None:
        middlewares.append(summarization_middleware)
        # Detect and break summarization loops — must run after SummarizationMiddleware
        middlewares.append(SummarizationLoopMiddleware())

    # Add TodoList middleware if plan mode is enabled
    is_plan_mode = config.get("configurable", {}).get("is_plan_mode", False)
    todo_list_middleware = _create_todo_list_middleware(is_plan_mode)
    if todo_list_middleware is not None:
        middlewares.append(todo_list_middleware)

    # Add TokenUsageMiddleware when token_usage tracking is enabled
    if get_app_config().token_usage.enabled:
        middlewares.append(TokenUsageMiddleware())

    # Add TitleMiddleware
    middlewares.append(TitleMiddleware())

    # Add MemoryMiddleware (after TitleMiddleware)
    middlewares.append(MemoryMiddleware(agent_name=agent_name))

    # Add ViewImageMiddleware only if the current model supports vision.
    # Use the resolved runtime model_name from make_lead_agent to avoid stale config values.
    app_config = get_app_config()
    model_config = app_config.get_model_config(model_name) if model_name else None
    if model_config is not None and model_config.supports_vision:
        middlewares.append(ViewImageMiddleware())

    # Add DeferredToolFilterMiddleware to hide deferred tool schemas from model binding
    if app_config.tool_search.enabled:
        from deerflow.agents.middlewares.deferred_tool_filter_middleware import DeferredToolFilterMiddleware

        middlewares.append(DeferredToolFilterMiddleware())

    # Add SubagentLimitMiddleware to truncate excess parallel task calls
    subagent_enabled = config.get("configurable", {}).get("subagent_enabled", False)
    if subagent_enabled:
        max_concurrent_subagents = config.get("configurable", {}).get("max_concurrent_subagents", 3)
        middlewares.append(SubagentLimitMiddleware(max_concurrent=max_concurrent_subagents))

    # LoopDetectionMiddleware — detect and break repetitive tool call loops
    middlewares.append(LoopDetectionMiddleware())

    # Inject custom middlewares before ClarificationMiddleware
    if custom_middlewares:
        middlewares.extend(custom_middlewares)

    # ClarificationMiddleware should always be last
    middlewares.append(ClarificationMiddleware())
    return middlewares


def make_lead_agent(config: RunnableConfig):
    # Lazy import to avoid circular dependency
    from deerflow.tools import get_available_tools
    from deerflow.tools.builtins import setup_agent

    cfg = config.get("configurable", {})

    thinking_enabled = cfg.get("thinking_enabled", True)
    reasoning_effort = cfg.get("reasoning_effort", None)
    requested_model_name: str | None = cfg.get("model_name") or cfg.get("model")
    is_plan_mode = cfg.get("is_plan_mode", False)
    subagent_enabled = cfg.get("subagent_enabled", False)
    max_concurrent_subagents = cfg.get("max_concurrent_subagents", 3)
    is_bootstrap = cfg.get("is_bootstrap", False)
    agent_name = cfg.get("agent_name")

    agent_config = load_agent_config(agent_name) if not is_bootstrap else None
    # Custom agent model or fallback to global/default model resolution
    agent_model_name = agent_config.model if agent_config and agent_config.model else _resolve_model_name()

    # Final model name resolution with request override, then agent config, then global default
    model_name = requested_model_name or agent_model_name

    app_config = get_app_config()
    model_config = app_config.get_model_config(model_name) if model_name else None

    if model_config is None:
        raise ValueError("No chat model could be resolved. Please configure at least one model in config.yaml or provide a valid 'model_name'/'model' in the request.")
    if thinking_enabled and not model_config.supports_thinking:
        logger.warning(f"Thinking mode is enabled but model '{model_name}' does not support it; fallback to non-thinking mode.")
        thinking_enabled = False

    logger.info(
        "Create Agent(%s) -> thinking_enabled: %s, reasoning_effort: %s, model_name: %s, is_plan_mode: %s, subagent_enabled: %s, max_concurrent_subagents: %s",
        agent_name or "default",
        thinking_enabled,
        reasoning_effort,
        model_name,
        is_plan_mode,
        subagent_enabled,
        max_concurrent_subagents,
    )

    # Inject run metadata for LangSmith trace tagging
    if "metadata" not in config:
        config["metadata"] = {}

    config["metadata"].update(
        {
            "agent_name": agent_name or "default",
            "model_name": model_name or "default",
            "thinking_enabled": thinking_enabled,
            "reasoning_effort": reasoning_effort,
            "is_plan_mode": is_plan_mode,
            "subagent_enabled": subagent_enabled,
        }
    )

    if is_bootstrap:
        # Special bootstrap agent with minimal prompt for initial custom agent creation flow
        return create_agent(
            model=create_chat_model(name=model_name, thinking_enabled=thinking_enabled),
            tools=get_available_tools(model_name=model_name, subagent_enabled=subagent_enabled) + [setup_agent],
            middleware=_build_middlewares(config, model_name=model_name),
            system_prompt=apply_prompt_template(subagent_enabled=subagent_enabled, max_concurrent_subagents=max_concurrent_subagents, available_skills=set(["bootstrap"])),
            state_schema=ThreadState,
        )

    # Default lead agent (unchanged behavior)
    return create_agent(
        model=create_chat_model(name=model_name, thinking_enabled=thinking_enabled, reasoning_effort=reasoning_effort),
        tools=get_available_tools(model_name=model_name, groups=agent_config.tool_groups if agent_config else None, subagent_enabled=subagent_enabled),
        middleware=_build_middlewares(config, model_name=model_name, agent_name=agent_name),
        system_prompt=apply_prompt_template(
            subagent_enabled=subagent_enabled, max_concurrent_subagents=max_concurrent_subagents, agent_name=agent_name, available_skills=set(agent_config.skills) if agent_config and agent_config.skills is not None else None
        ),
        state_schema=ThreadState,
    )
