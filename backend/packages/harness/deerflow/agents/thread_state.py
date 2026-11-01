import copy
from collections.abc import Callable
from typing import Annotated, Any, NotRequired, TypedDict

from langchain.agents import AgentState

from deerflow.utils.time import now_iso


class SandboxState(TypedDict):
    sandbox_id: NotRequired[str | None]


class ThreadDataState(TypedDict):
    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


class ViewedImageData(TypedDict):
    base64: str
    mime_type: str


def merge_sandbox(existing: SandboxState | None, new: SandboxState | None) -> SandboxState | None:
    """Reducer for sandbox state - accepts idempotent writes only.

    Multiple sandbox tools can initialize lazily in the same graph step and
    emit the same sandbox_id via Command(update=...). LangGraph needs an
    explicit reducer for that shared state key. Different sandbox ids in the
    same thread indicate a lifecycle/isolation bug, so fail closed instead of
    choosing one silently.
    """
    if new is None:
        return existing
    if existing is None:
        return new

    existing_id = existing.get("sandbox_id")
    new_id = new.get("sandbox_id")
    if existing_id == new_id:
        return existing
    raise ValueError(f"Conflicting sandbox state updates: {existing_id!r} != {new_id!r}")


SandboxStateField = Annotated[NotRequired[SandboxState | None], merge_sandbox]


def merge_artifacts(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """Reducer for artifacts list - merges and deduplicates artifacts."""
    if existing is None:
        return new or []
    if new is None:
        return existing
    # Use dict.fromkeys to deduplicate while preserving order
    return list(dict.fromkeys(existing + new))


def merge_viewed_images(existing: dict[str, ViewedImageData] | None, new: dict[str, ViewedImageData] | None) -> dict[str, ViewedImageData]:
    """Reducer for viewed_images dict - merges image dictionaries.

    Special case: If new is an empty dict {}, it clears the existing images.
    This allows middlewares to clear the viewed_images state after processing.
    """
    if existing is None:
        return new or {}
    if new is None:
        return existing
    # Special case: empty dict means clear all viewed images
    if len(new) == 0:
        return {}
    # Merge dictionaries, new values override existing ones for same keys
    return {**existing, **new}


def _stamp_status_transition(todo: dict, status: Any, previous: Any, now: Callable[[], str]) -> None:
    """Write the wall-clock marks a todo earns by *changing* into `status`.

    Keyed on the change, never on the write: the helper agent calls
    `write_todos` on every turn it touches the list, and often rewrites the
    status a todo already has. Re-stamping there would reset the mark to "just
    now" on every call, so the elapsed times the phone reads would all be 0s.

    A todo owns at most one open run at a time, so entering `in_progress`
    starts a fresh one and drops the previous end (a reworked item is timed
    from its restart, not from its first attempt). `completed` keeps whatever
    start was observed: a pending item the agent completes directly has no
    observed start, and none is invented — the phone draws no duration rather
    than a fabricated zero.
    """
    if status == previous:
        return
    if status == "in_progress":
        todo["started_at"] = now()
        todo.pop("completed_at", None)
    elif status == "completed":
        todo["completed_at"] = now()
    elif status == "pending":
        todo.pop("started_at", None)
        todo.pop("completed_at", None)


def apply_todo_ops(
    existing: list[dict] | None,
    updates: list[dict] | None,
    adds: list[dict] | None,
    *,
    now: Callable[[], str] | None = None,
) -> list[dict]:
    """Apply incremental todo operations to an existing list.

    Status changes carry `started_at` / `completed_at` (ISO 8601 UTC strings,
    the format every other DeerFlow timestamp uses) so a reader can tell how
    long each item took. `now` is injectable for tests; production callers
    take the wall clock, which is safe here because this runs in the
    `write_todos` tool — a real tool invocation, not a reducer, so it is not
    re-executed on replay. It is read through `now_iso` on every call rather
    than bound as a default, so a patched clock is honoured (see
    `config/models_section.py` for the same shape).
    """
    stamp = now or now_iso
    result = copy.deepcopy(existing or [])

    if updates:
        removes = []
        regular_updates = []
        for update in updates:
            index = update.get("index")
            if not isinstance(index, int) or index < 0 or index >= len(result):
                continue
            if update.get("remove"):
                removes.append(index)
            else:
                regular_updates.append(update)

        for update in regular_updates:
            index = update["index"]
            if "status" in update:
                _stamp_status_transition(result[index], update["status"], result[index].get("status"), stamp)
                result[index]["status"] = update["status"]
            if "content" in update:
                result[index]["content"] = update["content"]

        for index in sorted(removes, reverse=True):
            result.pop(index)

    if adds:
        for item in adds:
            content = item.get("content")
            if not content or not isinstance(content, str):
                continue
            status = item.get("status", "pending")
            todo = {"content": content, "status": status}
            # An added item has no history, so whatever status it is born with
            # is a transition from nothing — `pending` (the default) earns no
            # mark, `in_progress` starts its clock now.
            _stamp_status_transition(todo, status, None, stamp)
            index = item.get("index")
            if isinstance(index, int) and 0 <= index < len(result):
                result.insert(index, todo)
            else:
                result.append(todo)

    return result


def merge_todos(existing: list | None, new: list | None) -> list | None:
    """Reducer for todos list - keeps the last non-None value.

    Semantics:
    - If `new` is None (node didn't touch todos), preserve `existing`.
    - If `new` is provided (even empty list), it represents an explicit
      update and wins over `existing`.
    """
    if new is None:
        return existing
    return new


class PromotedTools(TypedDict):
    catalog_hash: str
    names: list[str]


def merge_promoted(existing: PromotedTools | None, new: PromotedTools | None) -> PromotedTools | None:
    """Reducer for deferred-tool promotions, scoped by catalog hash.

    - new None/empty -> preserve existing (node didn't touch promotions).
    - catalog_hash changed -> replace wholesale, dropping stale names (prevents a
      persisted bare name from exposing a different tool after catalog drift).
    - same catalog_hash -> union names, dedupe, preserve order.
    """
    if not new:
        return existing
    if existing is None or existing.get("catalog_hash") != new["catalog_hash"]:
        return {
            "catalog_hash": new["catalog_hash"],
            "names": list(dict.fromkeys(new["names"])),
        }
    return {
        "catalog_hash": existing["catalog_hash"],
        "names": list(dict.fromkeys(existing["names"] + new["names"])),
    }


class ThreadState(AgentState):
    sandbox: SandboxStateField
    thread_data: NotRequired[ThreadDataState | None]
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]
    todos: Annotated[list | None, merge_todos]
    uploaded_files: NotRequired[list[dict] | None]
    viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]  # image_path -> {base64, mime_type}
    promoted: Annotated[PromotedTools | None, merge_promoted]
