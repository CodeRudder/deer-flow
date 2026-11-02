"""Thread interjection injection at model-call boundaries (plan-b).

The interjection queue lives on the run's :class:`InterjectionApplier`
(``RunRecord.interjections``), installed into the agent runtime context as
``__run_interjections`` by the worker.

Why a middleware and not ``agent.update_state`` mid-run: langgraph's pregel
loop keeps channel state in memory for the whole ``astream`` invocation and
never re-reads the checkpoint between super-steps, so an external
``update_state`` write is invisible to the running loop and gets superseded
by the loop's own checkpoint puts (observed against langgraph 1.x +
AsyncPostgresSaver). Instead, this middleware drains the queue at the next
``wrap_model_call`` boundary — the injected ``HumanMessage`` enters that
model call's request payload — and the worker persists applied messages into
the final checkpoint once the run ends, so subsequent turns still see them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage

from deerflow.runtime.runs.interjection import (
    INTERJECTION_APPLIED_EVENT,
    Interjection,
    InterjectionApplier,
)

logger = logging.getLogger(__name__)

# Runtime-context keys (worker-installed, ``__``-prefixed like ``__run_journal``).
RUN_INTERJECTIONS_CONTEXT_KEY = "__run_interjections"
RUN_BRIDGE_CONTEXT_KEY = "__run_bridge"


def _resolve_context(request: ModelRequest) -> dict[str, Any] | None:
    runtime = getattr(request, "runtime", None)
    context = getattr(runtime, "context", None)
    return context if isinstance(context, dict) else None


def _resolve_applier(context: dict[str, Any] | None) -> InterjectionApplier | None:
    if context is None:
        return None
    candidate = context.get(RUN_INTERJECTIONS_CONTEXT_KEY)
    return candidate if isinstance(candidate, InterjectionApplier) else None


def _build_messages(batch: list[Interjection]) -> list[HumanMessage]:
    return [
        HumanMessage(
            content=item.text,
            additional_kwargs={"interjection": True},
        )
        for item in batch
    ]


def _announce(context: dict[str, Any] | None, applier: InterjectionApplier, batch: list[Interjection], messages: list[HumanMessage]) -> None:
    """Journal + SSE-announce an applied batch (sync variants, fire-and-forget)."""
    run_id = getattr(applier, "run_id", "")
    journal = context.get("__run_journal") if context else None
    for item in batch:
        logger.info("Interjection applied at model-call boundary: id=%s run_id=%s seq=%d", item.id, run_id, item.seq)
        if journal is not None:
            try:
                journal.record_interjection(interjection_id=item.id, text=item.text, source=item.source, seq=item.seq)
                journal.record_interjection_message(messages[-1], interjection_id=item.id, seq=item.seq, source=item.source)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to journal interjection %s", item.id, exc_info=True)


def _publish_applied_best_effort(context: dict[str, Any] | None, applier: InterjectionApplier, batch: list[Interjection]) -> None:
    """Sync-path SSE announcement: schedule on a running loop when present.

    The web/gateway path always runs the async wrapper, so this is only a
    fallback for sync agents (e.g. ``DeerFlowClient.stream``); without a
    running loop there is no bridge delivery, and the applied state still
    lands in the journal + final checkpoint.
    """
    bridge = context.get(RUN_BRIDGE_CONTEXT_KEY) if context else None
    if bridge is None:
        return
    run_id = getattr(applier, "run_id", "")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("Interjection applied event skipped (no running loop) for run %s", run_id)
        return
    loop.create_task(
        bridge.publish(
            run_id,
            "custom",
            {
                "type": INTERJECTION_APPLIED_EVENT,
                "run_id": run_id,
                "thread_id": applier.thread_id,
                "interjections": [item.to_dict() for item in batch],
            },
        )
    )


async def _announce_async(context: dict[str, Any] | None, applier: InterjectionApplier, batch: list[Interjection], messages: list[HumanMessage]) -> None:
    _announce(context, applier, batch, messages)
    bridge = context.get(RUN_BRIDGE_CONTEXT_KEY) if context else None
    if bridge is None:
        return
    run_id = getattr(applier, "run_id", "")
    try:
        await bridge.publish(
            run_id,
            "custom",
            {
                "type": INTERJECTION_APPLIED_EVENT,
                "run_id": run_id,
                "thread_id": applier.thread_id,
                "interjections": [item.to_dict() for item in batch],
            },
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to publish %s for run %s", INTERJECTION_APPLIED_EVENT, run_id, exc_info=True)


class InterjectionMiddleware(AgentMiddleware):
    """Drain queued interjections into the next model call's user turn."""

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        context = _resolve_context(request)
        applier = _resolve_applier(context)
        if applier is None:
            return handler(request)
        batch = applier.drain_batch_sync()
        if not batch:
            return handler(request)
        messages = _build_messages(batch)
        applier.apply_batch_sync(batch)
        _announce(context, applier, batch, messages)
        _publish_applied_best_effort(context, applier, batch)
        return handler(request.override(messages=[*request.messages, *messages]))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        context = _resolve_context(request)
        applier = _resolve_applier(context)
        if applier is None:
            return await handler(request)

        batch = await applier.drain_batch(first_wait_timeout=0)
        if not batch:
            return await handler(request)

        messages = _build_messages(batch)
        await applier.apply_batch(batch)
        await _announce_async(context, applier, batch, messages)
        return await handler(request.override(messages=[*request.messages, *messages]))
