"""Factory-level wiring test for ClarificationMiddleware sibling-tool dropping.

Unit tests in ``test_clarification_middleware.py`` call ``after_model``
directly. This file builds a real ``langchain.agents.create_agent`` graph
so a langchain hook-dispatch regression or a same-id ``add_messages``
replacement failure would reintroduce the sibling-execution bug instead of
staying green. A second graph path covers malformed ``ask_clarification``
parked on ``invalid_tool_calls`` beside a valid sibling.
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from deerflow.agents.middlewares.clarification_middleware import ClarificationMiddleware
from deerflow.tools.builtins.clarification_tool import ask_clarification_tool

_BASH_INVOCATIONS: list[str] = []
_MIXED_MESSAGE_ID = "ai-clarification-with-sibling"
_INVALID_MIXED_MESSAGE_ID = "ai-invalid-clarification-with-sibling"


@tool
def bash(command: str) -> str:
    """Pretend to run a shell command. Records the call for assertion."""
    _BASH_INVOCATIONS.append(command)
    return f"ran: {command}"


class _MixedBatchModel(BaseChatModel):
    """First call emits ``ask_clarification`` plus ``bash``; a second call is a wiring failure."""

    call_count: int = 0

    @property
    def _llm_type(self) -> str:
        return "mixed-batch-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stopped=None, run_manager=None, **kwargs) -> ChatResult:
        self.call_count += 1
        if self.call_count == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {"name": "ask_clarification", "args": {"question": "Which env?"}, "id": "call-clarify", "type": "tool_call"},
                    {"name": "bash", "args": {"command": "echo should-not-run"}, "id": "call-bash", "type": "tool_call"},
                ],
                id=_MIXED_MESSAGE_ID,
            )
        else:  # pragma: no cover - a second model call means the turn did not stop
            raise AssertionError("model was called again; the turn should have ended after the clarification")
        return ChatResult(generations=[ChatGeneration(message=message)])


class _InvalidMixedBatchModel(BaseChatModel):
    """Same as above but the clarification args are malformed JSON on the wire."""

    call_count: int = 0

    @property
    def _llm_type(self) -> str:
        return "invalid-mixed-batch-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stopped=None, run_manager=None, **kwargs) -> ChatResult:
        self.call_count += 1
        if self.call_count == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {"name": "bash", "args": {"command": "echo should-not-run"}, "id": "call-bash", "type": "tool_call"},
                ],
                # LangChain parses each provider call independently: the malformed
                # clarification is parked on invalid_tool_calls while the valid
                # sibling stays executable on tool_calls.
                invalid_tool_calls=[
                    {"name": "ask_clarification", "args": "{not json", "id": "call-clarify", "error": "invalid JSON"},
                ],
                id=_INVALID_MIXED_MESSAGE_ID,
            )
        else:  # pragma: no cover
            raise AssertionError("model was called again; the turn should have ended after the invalid clarification")
        return ChatResult(generations=[ChatGeneration(message=message)])


def _build_agent(model: BaseChatModel):
    return create_agent(
        model,
        tools=[bash, ask_clarification_tool],
        middleware=[ClarificationMiddleware()],
    )


def test_mixed_batch_runs_only_clarification_and_interrupts():
    _BASH_INVOCATIONS.clear()
    agent = _build_agent(_MixedBatchModel())
    result = agent.invoke({"messages": [HumanMessage(content="deploy")]})

    assert _BASH_INVOCATIONS == [], "sibling tool must not execute before the user answers"
    last = result["messages"][-1]
    assert isinstance(last, ToolMessage)
    assert last.name == "ask_clarification"
    assert last.tool_call_id == "call-clarify"
    assert "Which env?" in last.content
    # The structured payload rides along in the artifact.
    assert last.artifact["human_input"]["kind"] == "human_input_request"
    assert last.artifact["human_input"]["request_id"] == "clarification:call-clarify"


def test_invalid_clarification_beside_valid_sibling_still_stops():
    _BASH_INVOCATIONS.clear()
    agent = _build_agent(_InvalidMixedBatchModel())
    result = agent.invoke({"messages": [HumanMessage(content="deploy")]})

    assert _BASH_INVOCATIONS == [], "valid sibling must not execute when the clarification is malformed"
    last = result["messages"][-1]
    # The malformed clarification is answered with the proceed/interrupt ToolMessage
    # (here: the middleware's normal interrupt path never ran because the call was
    # invalid — the graph simply ends with the sibling dropped).
    assert isinstance(last, ToolMessage) or isinstance(last, AIMessage)
