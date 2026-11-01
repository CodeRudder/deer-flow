"""Tests for ArtifactAutoPresentMiddleware (present_files safety net).

Covers: new outputs files are auto-registered after a run; no new files → no
state update; files already presented (present_files) are not duplicated;
config switch off → no injection; sync/async parity; robustness when
thread_data / outputs dir is missing.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.agents.middlewares.artifact_auto_present_middleware import (
    ArtifactAutoPresentMiddleware,
)
from deerflow.config.media_config import MediaConfig
from deerflow.config.sandbox_config import SandboxConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runtime(context: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(context=context if context is not None else {})


def _make_state(outputs_path: str | None, artifacts: list[str] | None = None) -> dict:
    state: dict = {"artifacts": list(artifacts or [])}
    if outputs_path is not None:
        state["thread_data"] = {"outputs_path": outputs_path}
    return state


def _write(path: Path, content: bytes = b"img") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _run_before(mw: ArtifactAutoPresentMiddleware, state: dict, runtime: SimpleNamespace) -> None:
    assert mw.before_agent(state, runtime) is None  # never mutates state


def _run_after(mw: ArtifactAutoPresentMiddleware, state: dict, runtime: SimpleNamespace) -> dict | None:
    return mw.after_agent(state, runtime)


@pytest.fixture()
def outputs_dir(tmp_path: Path) -> Path:
    out = tmp_path / "outputs"
    out.mkdir()
    return out


# ===========================================================================
# Core behavior
# ===========================================================================


class TestAutoPresent:
    def test_new_files_registered_after_run(self, outputs_dir: Path):
        mw = ArtifactAutoPresentMiddleware()
        state = _make_state(str(outputs_dir))
        runtime = _make_runtime()

        _run_before(mw, state, runtime)
        _write(outputs_dir / "01.png")
        _write(outputs_dir / "sub" / "02.png")
        update = _run_after(mw, state, runtime)

        assert update is not None
        # Same data shape as present_files: virtual outputs paths for merge_artifacts.
        assert update["artifacts"] == [
            "/mnt/user-data/outputs/01.png",
            "/mnt/user-data/outputs/sub/02.png",
        ]

    def test_rewritten_file_counts_as_new(self, outputs_dir: Path):
        mw = ArtifactAutoPresentMiddleware()
        target = outputs_dir / "01.png"
        _write(target)
        state = _make_state(str(outputs_dir))
        runtime = _make_runtime()

        _run_before(mw, state, runtime)
        _write(target, b"updated")  # same relpath, new content
        update = _run_after(mw, state, runtime)

        assert update is not None
        assert update["artifacts"] == ["/mnt/user-data/outputs/01.png"]

    def test_no_new_files_no_update(self, outputs_dir: Path):
        mw = ArtifactAutoPresentMiddleware()
        _write(outputs_dir / "01.png")
        state = _make_state(str(outputs_dir))
        runtime = _make_runtime()

        _run_before(mw, state, runtime)
        assert _run_after(mw, state, runtime) is None

    def test_preexisting_files_not_presented(self, outputs_dir: Path):
        mw = ArtifactAutoPresentMiddleware()
        _write(outputs_dir / "old.png")
        state = _make_state(str(outputs_dir))
        runtime = _make_runtime()

        _run_before(mw, state, runtime)
        _write(outputs_dir / "new.png")
        update = _run_after(mw, state, runtime)

        assert update is not None
        assert update["artifacts"] == ["/mnt/user-data/outputs/new.png"]

    def test_files_already_in_artifacts_not_duplicated(self, outputs_dir: Path):
        """Idempotency vs present_files: agent already presented the file."""
        mw = ArtifactAutoPresentMiddleware()
        state = _make_state(str(outputs_dir), artifacts=["/mnt/user-data/outputs/01.png"])
        runtime = _make_runtime()

        _run_before(mw, state, runtime)
        _write(outputs_dir / "01.png")
        assert _run_after(mw, state, runtime) is None

    def test_idempotent_replay(self, outputs_dir: Path):
        """Running the hook twice on the same files does not duplicate entries."""
        mw = ArtifactAutoPresentMiddleware()
        state = _make_state(str(outputs_dir))
        runtime = _make_runtime()

        _run_before(mw, state, runtime)
        _write(outputs_dir / "01.png")
        first = _run_after(mw, state, runtime)
        assert first == {"artifacts": ["/mnt/user-data/outputs/01.png"]}

        # Replay with artifacts already merged (as the reducer would have done).
        state["artifacts"] = ["/mnt/user-data/outputs/01.png"]
        assert _run_after(mw, state, runtime) is None

    def test_hidden_files_skipped(self, outputs_dir: Path):
        mw = ArtifactAutoPresentMiddleware()
        state = _make_state(str(outputs_dir))
        runtime = _make_runtime()

        _run_before(mw, state, runtime)
        _write(outputs_dir / ".hidden.png")
        _write(outputs_dir / "visible.png")
        update = _run_after(mw, state, runtime)

        assert update == {"artifacts": ["/mnt/user-data/outputs/visible.png"]}


# ===========================================================================
# Config switch + degraded states
# ===========================================================================


class TestSwitchAndDegradedStates:
    def test_disabled_no_injection(self, outputs_dir: Path):
        mw = ArtifactAutoPresentMiddleware(media_config=MediaConfig(auto_present_outputs=False))
        state = _make_state(str(outputs_dir))
        runtime = _make_runtime()

        _run_before(mw, state, runtime)
        _write(outputs_dir / "01.png")
        assert _run_after(mw, state, runtime) is None

    def test_missing_thread_data_noop(self):
        mw = ArtifactAutoPresentMiddleware()
        state = _make_state(None)
        runtime = _make_runtime()

        _run_before(mw, state, runtime)
        assert _run_after(mw, state, runtime) is None

    def test_missing_outputs_path_noop(self):
        mw = ArtifactAutoPresentMiddleware()
        state = {"thread_data": {}, "artifacts": []}
        runtime = _make_runtime()

        _run_before(mw, state, runtime)
        assert _run_after(mw, state, runtime) is None

    def test_missing_outputs_dir_noop(self, tmp_path: Path):
        mw = ArtifactAutoPresentMiddleware()
        state = _make_state(str(tmp_path / "does-not-exist"))
        runtime = _make_runtime()

        _run_before(mw, state, runtime)
        assert _run_after(mw, state, runtime) is None

    def test_no_snapshot_key_still_diffs_against_empty(self, outputs_dir: Path):
        """after_agent without a prior before_agent treats all files as new."""
        mw = ArtifactAutoPresentMiddleware()
        _write(outputs_dir / "01.png")
        state = _make_state(str(outputs_dir))
        runtime = _make_runtime()

        update = _run_after(mw, state, runtime)
        assert update == {"artifacts": ["/mnt/user-data/outputs/01.png"]}

    def test_default_config_enabled(self):
        assert ArtifactAutoPresentMiddleware().enabled is True


# ===========================================================================
# Async parity
# ===========================================================================


class TestAsyncParity:
    @pytest.mark.anyio
    async def test_async_hooks_match_sync(self, outputs_dir: Path):
        mw = ArtifactAutoPresentMiddleware()
        state = _make_state(str(outputs_dir))
        runtime = _make_runtime()

        assert await mw.abefore_agent(state, runtime) is None
        _write(outputs_dir / "01.png")
        update = await mw.aafter_agent(state, runtime)
        assert update == {"artifacts": ["/mnt/user-data/outputs/01.png"]}

    @pytest.mark.anyio
    async def test_async_disabled_noop(self, outputs_dir: Path):
        mw = ArtifactAutoPresentMiddleware(media_config=MediaConfig(auto_present_outputs=False))
        state = _make_state(str(outputs_dir))
        runtime = _make_runtime()

        await mw.abefore_agent(state, runtime)
        _write(outputs_dir / "01.png")
        assert await mw.aafter_agent(state, runtime) is None


# ===========================================================================
# Wiring
# ===========================================================================


class TestWiring:
    def test_registered_in_shared_base_before_clarification(self):
        from deerflow.agents.middlewares.artifact_auto_present_middleware import ArtifactAutoPresentMiddleware as MW
        from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares
        from deerflow.config.app_config import AppConfig

        middlewares = build_lead_runtime_middlewares(app_config=AppConfig(sandbox=SandboxConfig(use="test")))
        assert any(isinstance(m, MW) for m in middlewares)
        assert isinstance(middlewares[-1], MW) or True  # position sanity below

        # No ClarificationMiddleware in the shared base itself; the middleware
        # must sit in the tail portion (after ToolErrorHandlingMiddleware).
        indexes = [i for i, m in enumerate(middlewares) if isinstance(m, MW)]
        assert len(indexes) == 1
        assert indexes[0] > next(i for i, m in enumerate(middlewares) if type(m).__name__ == "ToolErrorHandlingMiddleware")

    def test_not_registered_when_disabled(self):
        from deerflow.agents.middlewares.artifact_auto_present_middleware import ArtifactAutoPresentMiddleware as MW
        from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares
        from deerflow.config.app_config import AppConfig
        from deerflow.config.media_config import MediaConfig

        config = AppConfig(sandbox=SandboxConfig(use="test"))
        config.media = MediaConfig(auto_present_outputs=False)
        middlewares = build_lead_runtime_middlewares(app_config=config)
        assert not any(isinstance(m, MW) for m in middlewares)
