"""Artifact auto-present safety net: register new outputs files at run end.

Problem this solves: agents (especially skill-driven flows) sometimes finish a
run having written files into the thread outputs directory
(``/mnt/user-data/outputs/...``) but never call the ``present_files`` tool, so
the client never renders the artifacts. Prompt-level constraints proved
unreliable in E2E testing, so the platform guarantees delivery instead.

Behavior:

- ``before_agent``: snapshot the thread outputs directory (relative path →
  ``(mtime_ns, size)`` signature) for later diffing.
- ``after_agent``: re-scan the same directory; every file that is new or was
  rewritten since the snapshot is registered into ``state["artifacts"]`` via
  the same data shape ``present_files`` uses (the ``merge_artifacts`` reducer
  deduplicates), so the client renders it without depending on agent behavior.

Relationship to ``present_files``: this is a fallback, not a replacement. If
the agent did call ``present_files``, the files are already in ``artifacts``
and the reducer makes the duplicate update a no-op (idempotent replay is
therefore free).

Placement: appended at the tail of the shared runtime base
(``_build_runtime_middlewares``), before ``ClarificationMiddleware``, so both
the lead agent and subagents get the same guarantee.

Failure policy: best-effort — any error scanning or registering is logged and
swallowed; the run's own result is never affected.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from deerflow.config.media_config import MediaConfig
from deerflow.tools.builtins.present_file_tool import OUTPUTS_VIRTUAL_PREFIX

logger = logging.getLogger(__name__)

# Runtime-context key holding the run-start snapshot (relpath -> (mtime_ns, size)).
_SNAPSHOT_CONTEXT_KEY = "__artifact_auto_present_snapshot"


class ArtifactAutoPresentMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema (artifacts via merge_artifacts)."""

    thread_data: NotRequired[dict | None]
    artifacts: NotRequired[list[str]]


def _snapshot_outputs_dir(outputs_dir: Path) -> dict[str, tuple[int, int]]:
    """Return ``{relpath: (mtime_ns, size)}`` for all non-hidden files under *outputs_dir*."""
    snapshot: dict[str, tuple[int, int]] = {}
    for path in outputs_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(outputs_dir).as_posix()
        if any(part.startswith(".") for part in Path(rel).parts):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[rel] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _to_virtual_path(relative_path: str) -> str:
    """Convert an outputs-relative path to the `/mnt/user-data/outputs/*` contract."""
    return f"{OUTPUTS_VIRTUAL_PREFIX}/{relative_path}"


class ArtifactAutoPresentMiddleware(AgentMiddleware[ArtifactAutoPresentMiddlewareState]):
    """Automatically present new outputs files at run end (present_files fallback)."""

    state_schema = ArtifactAutoPresentMiddlewareState

    def __init__(self, media_config: MediaConfig | None = None):
        self._config = media_config or MediaConfig()

    @property
    def enabled(self) -> bool:
        return self._config.auto_present_outputs

    @staticmethod
    def _resolve_outputs_dir(state: ArtifactAutoPresentMiddlewareState) -> Path | None:
        thread_data = state.get("thread_data") or {}
        outputs_path = thread_data.get("outputs_path")
        if not outputs_path:
            return None
        return Path(outputs_path)

    @override
    def before_agent(self, state: ArtifactAutoPresentMiddlewareState, runtime: Runtime) -> dict | None:
        if not self.enabled:
            return None
        return self._take_snapshot(state, runtime)

    @override
    async def abefore_agent(self, state: ArtifactAutoPresentMiddlewareState, runtime: Runtime) -> dict | None:
        if not self.enabled:
            return None
        # Offload the directory walk (rglob/stat) off the event loop, mirroring
        # UploadsMiddleware (see the blocking-IO runtime gate in tests/blocking_io/).
        await asyncio.to_thread(self._take_snapshot, state, runtime)
        return None

    def _take_snapshot(self, state: ArtifactAutoPresentMiddlewareState, runtime: Runtime) -> dict | None:
        try:
            outputs_dir = self._resolve_outputs_dir(state)
            if outputs_dir is None:
                return None
            snapshot = _snapshot_outputs_dir(outputs_dir)
            context = runtime.context
            if context is not None:
                context[_SNAPSHOT_CONTEXT_KEY] = snapshot
        except Exception:
            logger.debug("Artifact auto-present: failed to snapshot outputs dir", exc_info=True)
        return None

    @override
    def after_agent(self, state: ArtifactAutoPresentMiddlewareState, runtime: Runtime) -> dict | None:
        if not self.enabled:
            return None
        try:
            return self._collect_new_artifacts(state, runtime)
        except Exception:
            logger.warning("Artifact auto-present: failed to register new outputs files", exc_info=True)
            return None

    @override
    async def aafter_agent(self, state: ArtifactAutoPresentMiddlewareState, runtime: Runtime) -> dict | None:
        if not self.enabled:
            return None
        try:
            return await asyncio.to_thread(self._collect_new_artifacts, state, runtime)
        except Exception:
            logger.warning("Artifact auto-present: failed to register new outputs files", exc_info=True)
            return None

    def _collect_new_artifacts(self, state: ArtifactAutoPresentMiddlewareState, runtime: Runtime) -> dict | None:
        outputs_dir = self._resolve_outputs_dir(state)
        if outputs_dir is None:
            return None

        context = runtime.context
        baseline: dict[str, tuple[int, int]] = (context or {}).get(_SNAPSHOT_CONTEXT_KEY) or {}
        current = _snapshot_outputs_dir(outputs_dir)

        new_relpaths = [rel for rel, signature in current.items() if baseline.get(rel) != signature]
        if not new_relpaths:
            return None

        # Idempotency vs. present_files: skip anything the agent already presented.
        existing = set(state.get("artifacts") or [])
        to_present = [_to_virtual_path(rel) for rel in sorted(new_relpaths) if _to_virtual_path(rel) not in existing]
        if not to_present:
            return None

        logger.info("Artifact auto-present: registering %d new outputs file(s) not presented by the agent", len(to_present))
        # merge_artifacts reducer merges + deduplicates, same data shape as present_files.
        return {"artifacts": to_present}


__all__ = [
    "ArtifactAutoPresentMiddleware",
    "ArtifactAutoPresentMiddlewareState",
]
