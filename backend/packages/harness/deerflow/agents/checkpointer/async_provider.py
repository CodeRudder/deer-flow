"""Async checkpointer factory.

Provides an **async context manager** for long-running async servers that need
proper resource cleanup.

Supported backends: memory, sqlite, postgres.

Usage (e.g. FastAPI lifespan)::

    from deerflow.agents.checkpointer.async_provider import make_checkpointer

    async with make_checkpointer() as checkpointer:
        app.state.checkpointer = checkpointer  # InMemorySaver if not configured

For sync usage see :mod:`deerflow.agents.checkpointer.provider`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from langgraph.types import Checkpointer

from deerflow.agents.checkpointer.provider import (
    POSTGRES_CONN_REQUIRED,
    POSTGRES_INSTALL,
    SQLITE_INSTALL,
)
from deerflow.config.app_config import get_app_config
from deerflow.runtime.store._sqlite_utils import ensure_sqlite_parent_dir, resolve_sqlite_conn_str

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Async factory
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _async_checkpointer(config) -> AsyncIterator[Checkpointer]:
    """Async context manager that constructs and tears down a checkpointer."""
    if config.type == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return

    if config.type == "sqlite":
        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        except ImportError as exc:
            raise ImportError(SQLITE_INSTALL) from exc

        conn_str = resolve_sqlite_conn_str(config.connection_string or "store.db")
        await asyncio.to_thread(ensure_sqlite_parent_dir, conn_str)
        async with AsyncSqliteSaver.from_conn_string(conn_str) as saver:
            await saver.setup()
            # Tune SQLite memory and IO behaviour after schema init.
            # - cache_size: SQLite pager cache (in-memory B-tree pages).
            #   Default -2000 (2MB) is fine; keep it bounded.
            # - mmap_size: memory-mapped IO — lets SQLite read pages via mmap
            #   instead of read() syscalls.  mmap pages are *not* charged to
            #   cgroup memory.current, so this reduces reported memory usage
            #   for large DBs.  64 MB is a safe cap.
            # - wal_autocheckpoint: flush WAL more aggressively (every 1000
            #   pages = 4 MB) to prevent WAL from growing unbounded.
            # - temp_store: keep temp tables in memory (faster, less IO).
            try:
                async with saver.conn.executescript(
                    """
                    PRAGMA cache_size=-8000;
                    PRAGMA mmap_size=67108864;
                    PRAGMA wal_autocheckpoint=1000;
                    PRAGMA temp_store=MEMORY;
                    """
                ) as _cur:
                    pass
            except Exception:
                logger.debug("Failed to apply SQLite PRAGMA tuning", exc_info=True)
            yield saver
        return

    if config.type == "postgres":
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:
            raise ImportError(POSTGRES_INSTALL) from exc

        if not config.connection_string:
            raise ValueError(POSTGRES_CONN_REQUIRED)

        async with AsyncPostgresSaver.from_conn_string(config.connection_string) as saver:
            await saver.setup()
            yield saver
        return

    raise ValueError(f"Unknown checkpointer type: {config.type!r}")


# ---------------------------------------------------------------------------
# Public async context manager
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def make_checkpointer() -> AsyncIterator[Checkpointer]:
    """Async context manager that yields a checkpointer for the caller's lifetime.
    Resources are opened on enter and closed on exit — no global state::

        async with make_checkpointer() as checkpointer:
            app.state.checkpointer = checkpointer

    Yields an ``InMemorySaver`` when no checkpointer is configured in *config.yaml*.
    """

    config = get_app_config()

    if config.checkpointer is None:
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return

    async with _async_checkpointer(config.checkpointer) as saver:
        yield saver
