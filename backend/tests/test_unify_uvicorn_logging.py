"""Tests for ``unify_uvicorn_logging``: pin the handler-clear +
propagate-restore contract end-to-end so lifespan-wiring regressions
surface immediately. See the function docstring for the underlying contract.
"""

from __future__ import annotations

import importlib
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from deerflow.config.app_config import unify_uvicorn_logging

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _restore_uvicorn_loggers(snapshot: dict[str, tuple[list[logging.Handler], bool, int]]) -> None:
    for name, (handlers, propagate, level) in snapshot.items():
        logger_obj = logging.getLogger(name)
        logger_obj.handlers.clear()
        for handler in handlers:
            logger_obj.addHandler(handler)
        logger_obj.propagate = propagate
        logger_obj.setLevel(level)


def _snapshot_uvicorn_loggers() -> dict[str, tuple[list[logging.Handler], bool, int]]:
    return {name: (list(logging.getLogger(name).handlers), logging.getLogger(name).propagate, logging.getLogger(name).level) for name in _UVICORN_LOGGERS}


class TestUnifyUvicornLogging:
    """Verify ``unify_uvicorn_logging`` strips per-logger handlers and restores propagation."""

    def setup_method(self) -> None:
        self._snapshot = _snapshot_uvicorn_loggers()

    def teardown_method(self) -> None:
        _restore_uvicorn_loggers(self._snapshot)

    def test_clears_handlers_on_uvicorn_access(self) -> None:
        uvicorn_access = logging.getLogger("uvicorn.access")
        uvicorn_access.addHandler(logging.NullHandler())
        uvicorn_access.propagate = False

        unify_uvicorn_logging()

        assert uvicorn_access.handlers == []

    def test_restores_propagate_on_uvicorn_access(self) -> None:
        uvicorn_access = logging.getLogger("uvicorn.access")
        uvicorn_access.propagate = False

        unify_uvicorn_logging()

        assert uvicorn_access.propagate is True

    def test_clears_handlers_on_uvicorn_error(self) -> None:
        """Even though uvicorn.error has no default handler today, a future
        dictConfig change or third-party monkey-patch could attach one. Pin
        that we strip it so we never reintroduce a third format."""
        uvicorn_error = logging.getLogger("uvicorn.error")
        uvicorn_error.addHandler(logging.NullHandler())

        unify_uvicorn_logging()

        assert uvicorn_error.handlers == []

    def test_restores_propagate_on_uvicorn_error(self) -> None:
        uvicorn_error = logging.getLogger("uvicorn.error")
        uvicorn_error.propagate = False

        unify_uvicorn_logging()

        assert uvicorn_error.propagate is True

    def test_clears_handlers_on_uvicorn_parent(self) -> None:
        uvicorn = logging.getLogger("uvicorn")
        uvicorn.addHandler(logging.NullHandler())

        unify_uvicorn_logging()

        assert uvicorn.handlers == []

    def test_does_not_modify_levels(self) -> None:
        """``apply_logging_level`` owns level policy; ``unify_uvicorn_logging``
        must not interfere so the third-party "don't touch uvicorn verbosity"
        contract holds."""
        for name in _UVICORN_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)

        unify_uvicorn_logging()

        for name in _UVICORN_LOGGERS:
            assert logging.getLogger(name).level == logging.WARNING

    def test_idempotent(self) -> None:
        uvicorn_access = logging.getLogger("uvicorn.access")
        uvicorn_access.addHandler(logging.NullHandler())
        uvicorn_access.propagate = False

        unify_uvicorn_logging()
        first_handlers = list(uvicorn_access.handlers)
        first_propagate = uvicorn_access.propagate

        unify_uvicorn_logging()
        assert uvicorn_access.handlers == first_handlers
        assert uvicorn_access.propagate == first_propagate

    def test_does_not_touch_deerflow_or_app_loggers(self) -> None:
        """``apply_logging_level`` owns the deerflow/app namespace; the unify
        step must not clear handlers or flip propagate on those loggers."""
        deerflow = logging.getLogger("deerflow")
        app = logging.getLogger("app")
        deerflow.addHandler(logging.NullHandler())
        app.addHandler(logging.NullHandler())
        deerflow_original = (list(deerflow.handlers), deerflow.propagate)
        app_original = (list(app.handlers), app.propagate)

        unify_uvicorn_logging()

        assert list(deerflow.handlers) == deerflow_original[0]
        assert deerflow.propagate == deerflow_original[1]
        assert list(app.handlers) == app_original[0]
        assert app.propagate == app_original[1]


class TestUnifyUvicornLoggingPropagation:
    """End-to-end: a record emitted on ``uvicorn.access`` reaches the root handler."""

    def setup_method(self) -> None:
        self._snapshot = _snapshot_uvicorn_loggers()
        # Remove the test's handler list from root so caplog can attach.
        self._root_handlers = list(logging.root.handlers)
        self._root_level = logging.root.level
        # Clear propagate/handlers so the test starts from a clean slate.
        for name in _UVICORN_LOGGERS:
            logging.getLogger(name).handlers.clear()
            logging.getLogger(name).propagate = True

    def teardown_method(self) -> None:
        _restore_uvicorn_loggers(self._snapshot)
        # Restore root handlers/level so other tests are unaffected.
        logging.root.handlers.clear()
        for handler in self._root_handlers:
            logging.root.addHandler(handler)
        logging.root.setLevel(self._root_level)

    def test_uvicorn_access_records_reach_root(self, caplog: pytest.LogCaptureFixture) -> None:
        unify_uvicorn_logging()

        with caplog.at_level(logging.INFO, logger="uvicorn.access"):
            logging.getLogger("uvicorn.access").info("hello-access")

        # caplog attaches to the root logger, so this assertion confirms the
        # record propagated through uvicorn.access -> root instead of being
        # swallowed by a per-logger handler.
        names = [record.name for record in caplog.records]
        assert "uvicorn.access" in names


class TestLifespanCallsUnifyUvicornLogging:
    """Drive the lifespan enough to verify it calls ``unify_uvicorn_logging``
    before yielding. Heavier subsystems are mocked the same way
    ``test_gateway_lifespan_shutdown.py`` does; we assert only on uvicorn
    logger state.
    """

    def setup_method(self) -> None:
        self._snapshot = _snapshot_uvicorn_loggers()

    def teardown_method(self) -> None:
        _restore_uvicorn_loggers(self._snapshot)

    def test_lifespan_clears_uvicorn_access_handlers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Mimic uvicorn's default LOGGING_CONFIG: uvicorn.access gets its own
        # handler and stops propagating to root.
        uvicorn_access = logging.getLogger("uvicorn.access")
        sentinel = logging.NullHandler()
        uvicorn_access.addHandler(sentinel)
        uvicorn_access.propagate = False

        # Build the mocks the lifespan expects, identical in spirit to
        # ``test_gateway_lifespan_shutdown.py::_run_lifespan_with_hanging_stop``.

        @asynccontextmanager
        async def _noop_langgraph_runtime(_app, _startup_config):
            yield

        # ``app.gateway.__init__`` re-exports ``app`` (the FastAPI instance),
        # so a plain ``from app.gateway import app`` would shadow the module
        # and break ``patch.object`` on module-level names. Reach the module
        # via ``importlib`` to land on the real module object.
        gateway_app_module = importlib.import_module("app.gateway.app")

        fake_service = MagicMock()
        fake_service.get_status = MagicMock(return_value={})

        async def _fake_start():
            return fake_service

        async def _fake_stop():
            return None

        # Override every lifespan dependency that comes after the new
        # ``unify_uvicorn_logging()`` call. Anything missing here causes
        # the lifespan to blow up before reaching the assertion, which is
        # the right failure mode for a wiring test.
        with (
            patch.object(gateway_app_module, "get_app_config", return_value=MagicMock(log_level="info")),
            patch.object(gateway_app_module, "get_gateway_config", return_value=MagicMock(host="x", port=0)),
            patch.object(gateway_app_module, "langgraph_runtime", _noop_langgraph_runtime),
            patch.object(gateway_app_module.auth, "close_oidc_service", AsyncMock()),
            patch("app.channels.service.start_channel_service", side_effect=_fake_start),
            patch("app.channels.service.stop_channel_service", side_effect=_fake_stop),
            patch.object(gateway_app_module, "_ensure_admin_user", AsyncMock()),
        ):
            import asyncio

            async def _drive():
                async with gateway_app_module.lifespan(FastAPI()):
                    # Lifespan reached the ``yield``: by now
                    # ``unify_uvicorn_logging()`` has already executed.
                    pass

            asyncio.run(_drive())

        assert uvicorn_access.handlers == [], "lifespan must call unify_uvicorn_logging() to strip uvicorn's per-logger handler before yielding"
        assert sentinel not in uvicorn_access.handlers
        assert uvicorn_access.propagate is True
