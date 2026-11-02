"""Thread workspace file browsing API.

Lists files/directories under a thread's user-data tree (the same virtual
``/mnt/user-data`` namespace the artifacts router serves), with optional
recursive filename search. Read-only: entries are metadata only — file
contents are fetched through the existing artifacts endpoints.
"""

import asyncio
import logging
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.gateway.authz import require_permission
from app.gateway.path_utils import resolve_thread_virtual_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["workspace-files"])

VIRTUAL_ROOT = "/mnt/user-data"
MAX_LIST_ENTRIES = 500
MAX_SEARCH_ENTRIES = 500
MAX_SEARCH_DEPTH = 8
# Directories never surfaced by the browser (internal caches etc.)
IGNORED_DIR_NAMES = {".image-derivatives"}
IGNORED_FILE_NAMES: set[str] = set()


class WorkspaceEntry(BaseModel):
    name: str
    path: str
    type: Literal["file", "directory"]
    size: int | None = None
    modified_at: datetime | None = None


class WorkspaceFilesResponse(BaseModel):
    path: str
    entries: list[WorkspaceEntry]
    truncated: bool


def _virtual_join(parent: str, name: str) -> str:
    return parent.rstrip("/") + "/" + name


def _stat_entry(actual_path: Path, virtual_path: str) -> WorkspaceEntry | None:
    """Build a WorkspaceEntry from a stat; returns None for ignored entries."""
    try:
        st = os.lstat(actual_path)
    except OSError:
        return None
    if stat.S_ISDIR(st.st_mode):
        if actual_path.name in IGNORED_DIR_NAMES:
            return None
        return WorkspaceEntry(name=actual_path.name, path=virtual_path, type="directory", size=None, modified_at=datetime.fromtimestamp(st.st_mtime, tz=UTC))
    if not stat.S_ISREG(st.st_mode):
        return None
    if actual_path.name in IGNORED_FILE_NAMES:
        return None
    return WorkspaceEntry(
        name=actual_path.name,
        path=virtual_path,
        type="file",
        size=st.st_size,
        modified_at=datetime.fromtimestamp(st.st_mtime, tz=UTC),
    )


def _sort_entries(entries: list[WorkspaceEntry]) -> list[WorkspaceEntry]:
    return sorted(entries, key=lambda e: (e.type != "directory", e.name.lower()))


def _list_directory_sync(actual_dir: Path, virtual_dir: str) -> WorkspaceFilesResponse:
    if not actual_dir.exists():
        raise HTTPException(status_code=404, detail=f"Directory not found: {virtual_dir}")
    if not actual_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {virtual_dir}")

    entries: list[WorkspaceEntry] = []
    truncated = False
    try:
        children = sorted(actual_dir.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        raise HTTPException(status_code=500, detail=f"Failed to list directory: {virtual_dir}")

    for child in children:
        if len(entries) >= MAX_LIST_ENTRIES:
            truncated = True
            break
        entry = _stat_entry(child, _virtual_join(virtual_dir, child.name))
        if entry is not None:
            entries.append(entry)

    return WorkspaceFilesResponse(path=virtual_dir, entries=_sort_entries(entries), truncated=truncated)


def _search_sync(actual_dir: Path, virtual_dir: str, keyword: str) -> WorkspaceFilesResponse:
    """Recursive filename substring search, depth/entry capped."""
    keyword_lower = keyword.lower()
    matches: list[WorkspaceEntry] = []
    truncated = False

    def walk(dir_path: Path, virtual_base: str, depth: int) -> None:
        nonlocal truncated
        if truncated or depth > MAX_SEARCH_DEPTH:
            if depth > MAX_SEARCH_DEPTH:
                truncated = True
            return
        try:
            children = sorted(dir_path.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for child in children:
            if len(matches) >= MAX_SEARCH_ENTRIES:
                truncated = True
                return
            entry = _stat_entry(child, _virtual_join(virtual_base, child.name))
            if entry is None:
                continue
            if keyword_lower in entry.name.lower():
                matches.append(entry)
            if entry.type == "directory":
                walk(child, entry.path, depth + 1)

    walk(actual_dir, virtual_dir, 1)
    # Directories first for consistent rendering with the listing view.
    return WorkspaceFilesResponse(path=virtual_dir, entries=_sort_entries(matches), truncated=truncated)


@router.get(
    "/threads/{thread_id}/workspace/files",
    summary="List Thread Workspace Files",
    description=(
        "List files and directories under the thread's user-data workspace "
        "(/mnt/user-data virtual namespace). With `search`, recursively matches "
        "filenames (depth- and count-capped). File contents are served by the "
        "existing artifacts endpoints."
    ),
)
@require_permission("threads", "read", owner_check=True)
async def list_workspace_files(
    thread_id: str,
    request: Request,
    path: str = Query(default=VIRTUAL_ROOT, description="Virtual directory to list, e.g. /mnt/user-data/outputs"),
    search: str | None = Query(default=None, max_length=200, description="Recursive filename substring search"),
) -> WorkspaceFilesResponse:
    """Browse the thread workspace.

    Args:
        thread_id: The thread ID (owner-checked).
        path: Virtual directory to list; defaults to the /mnt/user-data root.
        search: When set, recursively match filenames instead of listing ``path``.

    Returns:
        Directory entries (metadata only) sorted directories-first, plus a
        ``truncated`` flag when the result list hit the server-side cap.

    Raises:
        HTTPException: 400 invalid/non-directory path, 403 path traversal or
            access denied, 404 directory not found.
    """
    # resolve_thread_virtual_path enforces the /mnt/user-data prefix, rejects
    # traversal, and scopes resolution to the requesting owner's bucket.
    actual_dir = await asyncio.to_thread(resolve_thread_virtual_path, thread_id, path)

    if search is not None and search.strip():
        keyword = search.strip()
        return await asyncio.to_thread(_search_sync, actual_dir, path, keyword)
    return await asyncio.to_thread(_list_directory_sync, actual_dir, path)
