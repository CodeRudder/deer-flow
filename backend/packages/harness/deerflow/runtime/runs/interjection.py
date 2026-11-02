"""Thread interjection: queue user messages into an in-flight run.

Design: docs/research/2026-11-01-thread-interjection/plan-b.md

Each :class:`~deerflow.runtime.runs.manager.RunRecord` carries an
``input_queue`` of :class:`Interjection` items. While a run is executing,
new user messages are enqueued instead of being rejected; a consumer task in
the worker drains the queue at step boundaries and injects the queued
messages into the graph state via ``agent.update_state(..., as_node="model")``
so the next model step naturally sees them.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from deerflow.utils.time import now_iso as _now_iso

logger = logging.getLogger(__name__)

# Node name used for ``agent.update_state(as_node=...)``. The lead agent graph
# (langchain ``create_agent``) has exactly two nodes: ``model`` and ``tools``.
# ``add_messages`` only appends regardless of as_node; choosing ``model`` makes
# checkpoint ``__prev__/__next__`` metadata point at the model path so the next
# super-step sees the injected messages.
INTERJECTION_AS_NODE = "model"

# SSE event published once queued interjections have been applied to state.
INTERJECTION_APPLIED_EVENT = "interjection_applied"

DEFAULT_MAX_PENDING = 8


@dataclass
class Interjection:
    """A single user message queued for injection into a running run."""

    id: str
    thread_id: str
    text: str
    source: Literal["web", "im", "api"] = "api"
    seq: int = 0
    created_at: str = ""
    # Set by the worker once ``update_state`` committed the message.
    applied_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "text": self.text,
            "source": self.source,
            "seq": self.seq,
            "created_at": self.created_at,
            "applied": self.applied,
        }


class InterjectionQueueFullError(Exception):
    """Raised when the per-run pending queue is above ``max_pending``."""


class InterjectionNotFoundError(Exception):
    """Raised when the interjection id is unknown (never existed or already removed)."""


class InterjectionAlreadyAppliedError(Exception):
    """Raised when cancelling an interjection that was already injected into model context."""


class InterjectionApplier:
    """Per-run interjection bookkeeping: queue, ordering, and cancellation.

    Owned by the :class:`RunRecord` lifecycle (created when the run starts,
    dropped when the run finishes). API handlers enqueue; the worker's
    consumer task drains and applies.
    """

    def __init__(self, thread_id: str, run_id: str, *, max_pending: int = DEFAULT_MAX_PENDING) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.max_pending = max_pending
        self._queue: asyncio.Queue[Interjection] = asyncio.Queue()
        self._pending: dict[str, Interjection] = {}
        self._applied_ids: set[str] = set()
        self._seq = itertools.count(1)

    # -- API side -----------------------------------------------------------

    async def enqueue(self, text: str, *, source: Literal["web", "im", "api"] = "api") -> Interjection:
        text = (text or "").strip()
        if not text:
            raise ValueError("Interjection text must not be empty")
        if self._queue.qsize() >= self.max_pending:
            raise InterjectionQueueFullError(f"Thread {self.thread_id} already has {self._queue.qsize()} queued interjection(s) (max {self.max_pending})")
        item = Interjection(
            id=str(uuid.uuid4()),
            thread_id=self.thread_id,
            text=text,
            source=source,
            seq=next(self._seq),
            created_at=_now_iso(),
        )
        self._pending[item.id] = item
        await self._queue.put(item)
        logger.info("Interjection queued: id=%s run_id=%s thread_id=%s seq=%d source=%s", item.id, self.run_id, self.thread_id, item.seq, item.source)
        return item

    def cancel(self, interjection_id: str) -> Interjection:
        """Remove a still-pending interjection from the queue.

        Raises :class:`InterjectionAlreadyAppliedError` when the item was
        already injected (irrevocable — it is in the model context) and
        :class:`InterjectionNotFoundError` when the id is unknown.
        """
        if interjection_id in self._applied_ids:
            raise InterjectionAlreadyAppliedError(f"Interjection {interjection_id} was already applied to the model context and cannot be cancelled")
        item = self._pending.pop(interjection_id, None)
        if item is None:
            raise InterjectionNotFoundError(f"Interjection {interjection_id} not found or no longer pending")
        # Queue consumers skip items missing from ``_pending`` (cancelled).
        logger.info("Interjection cancelled: id=%s run_id=%s thread_id=%s", item.id, self.run_id, self.thread_id)
        return item

    # -- Worker side --------------------------------------------------------

    async def drain_batch(self, first_wait_timeout: float = 0.5) -> list[Interjection]:
        """Async variant of :meth:`_drain_batch` used by the worker consumer."""
        if first_wait_timeout <= 0:
            batch = []
            while True:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    return [item for item in batch if item.id in self._pending]
        try:
            first = await asyncio.wait_for(self._queue.get(), timeout=first_wait_timeout)
        except (TimeoutError, asyncio.TimeoutError):
            return []
        batch = [first]
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        # Drop items cancelled while sitting in the queue.
        return [item for item in batch if item.id in self._pending]

    async def apply_batch(self, batch: list[Interjection]) -> list[Interjection]:
        """Mark a drained batch as applied (called after ``update_state`` succeeded)."""
        for item in batch:
            item.applied = True
            self._pending.pop(item.id, None)
            self._applied_ids.add(item.id)
            item.applied_event.set()
        return batch

    def pending_snapshot(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._pending.values()]
