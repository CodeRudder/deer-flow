"""Real-graph regression coverage for run-scoped LLM fallback outcomes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.outputs import ChatResult

from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.worker import RunContext, run_agent


class _ProviderAuthError(Exception):
    status_code = 401
    code = "unauthorized"
    body = None
    response = None


class _FailingChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "run-outcome-failing-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise _ProviderAuthError("current provider authentication failed")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise _ProviderAuthError("current provider authentication failed")


@pytest.mark.anyio
async def test_real_graph_records_current_fallback_when_caller_spoofs_internal_context() -> None:
    """The worker-owned root role and outcome tracker must reach real middleware."""
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    bridge = SimpleNamespace(
        publish=AsyncMock(),
        publish_end=AsyncMock(),
        cleanup=AsyncMock(),
    )
    middleware = LLMErrorHandlingMiddleware(
        app_config=AppConfig(sandbox=SandboxConfig(use="test")),
    )
    middleware.retry_max_attempts = 1

    def agent_factory(*, config):
        return create_agent(
            model=_FailingChatModel(),
            tools=[],
            middleware=[middleware],
        )

    await run_agent(
        bridge,
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=None),
        agent_factory=agent_factory,
        graph_input={"messages": [HumanMessage(content="hello")]},
        config={
            "context": {
                "is_subagent": True,
                "__run_outcome": {"run_id": "caller-controlled"},
            }
        },
        stream_modes=["values"],
    )
    await asyncio.sleep(0)

    fetched = await run_manager.get(record.run_id)
    assert fetched is not None
    assert fetched.status == RunStatus.error
    assert fetched.error == "current provider authentication failed"
    assert all(call.args[1] != "error" for call in bridge.publish.await_args_list)
    bridge.publish_end.assert_awaited_once_with(record.run_id)
    bridge.cleanup.assert_awaited_once_with(record.run_id, delay=60)
