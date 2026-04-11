import asyncio
import logging
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import ThreadDataState
from deerflow.config.paths import Paths, get_paths

logger = logging.getLogger(__name__)


class ThreadDataMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    thread_data: NotRequired[ThreadDataState | None]


class ThreadDataMiddleware(AgentMiddleware[ThreadDataMiddlewareState]):
    """Create thread data directories for each thread execution.

    Creates the following directory structure:
    - {base_dir}/threads/{thread_id}/user-data/workspace
    - {base_dir}/threads/{thread_id}/user-data/uploads
    - {base_dir}/threads/{thread_id}/user-data/outputs
    - {base_dir}/threads/{thread_id}/subagents/

    Directories are created eagerly in ``abefore_agent()`` via
    ``asyncio.to_thread`` to avoid LangGraph's blocking-call detector.
    """

    state_schema = ThreadDataMiddlewareState

    def __init__(self, base_dir: str | None = None, lazy_init: bool = True):
        """Initialize the middleware.

        Args:
            base_dir: Base directory for thread data. Defaults to Paths resolution.
            lazy_init: Kept for backward compatibility but no longer affects
                      directory creation — directories are always created in
                      ``abefore_agent()``.
        """
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()

    def _get_thread_paths(self, thread_id: str) -> dict[str, str]:
        """Get the paths for a thread's data directories."""
        return {
            "workspace_path": str(self._paths.sandbox_work_dir(thread_id)),
            "uploads_path": str(self._paths.sandbox_uploads_dir(thread_id)),
            "outputs_path": str(self._paths.sandbox_outputs_dir(thread_id)),
        }

    def _extract_thread_id(self, runtime: Runtime) -> str:
        """Extract thread_id from runtime context or LangGraph config."""
        context = runtime.context or {}
        thread_id = context.get("thread_id")
        if thread_id is None:
            config = get_config()
            thread_id = config.get("configurable", {}).get("thread_id")
        if thread_id is None:
            raise ValueError("Thread ID is required in runtime context or config.configurable")
        return thread_id

    @override
    def before_agent(self, state: ThreadDataMiddlewareState, runtime: Runtime) -> dict | None:
        thread_id = self._extract_thread_id(runtime)
        paths = self._get_thread_paths(thread_id)
        return {"thread_data": {**paths}}

    @override
    async def abefore_agent(self, state: ThreadDataMiddlewareState, runtime: Runtime) -> dict | None:
        """Async hook: create all thread directories in a worker thread.

        This is the preferred entry point when running under LangGraph's
        ASGI server.  ``asyncio.to_thread`` offloads the blocking ``mkdir``
        calls so LangGraph's blocking-call detector (blockbuster) won't
        raise.
        """
        thread_id = self._extract_thread_id(runtime)
        await asyncio.to_thread(self._paths.ensure_thread_dirs, thread_id)
        paths = self._get_thread_paths(thread_id)
        return {"thread_data": {**paths}}
