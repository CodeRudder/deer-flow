import copy
from typing import Annotated, Any, NotRequired, TypedDict

from langchain.agents import AgentState


class SandboxState(TypedDict):
    sandbox_id: NotRequired[str | None]


class ThreadDataState(TypedDict):
    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


class ViewedImageData(TypedDict):
    base64: str
    mime_type: str


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


def merge_todos(existing: list[dict] | None, new: list[dict] | dict[str, Any] | None) -> list[dict]:
    """Reducer for todos list - supports full replace and incremental operations.

    - If new is a list → full replace (backward compatible with original write_todos)
    - If new is a dict with ``_todo_ops`` → incremental operation:
      - ``updates``: list of ``{"index": int, "status?": str, "content?": str, "remove?": bool}``
      - ``adds``: list of ``{"content": str, "status?": str, "index?": int}``
      Operations are applied in order: updates first (including removes), then adds.
      Remove operations use descending-index order to avoid index shifting.
    """
    if new is None:
        return existing or []

    # Full replace (plain list from original write_todos or enhanced version)
    if isinstance(new, list):
        return new

    # Incremental operation
    if isinstance(new, dict) and new.get("_todo_ops"):
        result = copy.deepcopy(existing or [])

        # Separate removes from regular updates
        removes = []
        regular_updates = []
        for upd in new.get("updates", []):
            idx = upd.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(result):
                continue  # Skip invalid indices
            if upd.get("remove"):
                removes.append(idx)
            else:
                regular_updates.append(upd)

        # Apply regular updates first
        for upd in regular_updates:
            idx = upd["index"]
            if "status" in upd:
                result[idx]["status"] = upd["status"]
            if "content" in upd:
                result[idx]["content"] = upd["content"]

        # Apply removes in descending index order to avoid shifting
        for idx in sorted(removes, reverse=True):
            result.pop(idx)

        # Apply adds
        for add in new.get("adds", []):
            content = add.get("content")
            if not content or not isinstance(content, str):
                continue  # Skip invalid items
            item = {"content": content, "status": add.get("status", "pending")}
            idx = add.get("index")
            if idx is None:
                result.append(item)
            elif isinstance(idx, int):
                if idx < 0 or idx >= len(result):
                    # Negative or out-of-range index → append to end
                    result.append(item)
                else:
                    result.insert(idx, item)

        return result

    # Fallback: treat as full replace
    return new if isinstance(new, list) else existing or []


class ThreadState(AgentState):
    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]
    todos: Annotated[list[dict], merge_todos]
    uploaded_files: NotRequired[list[dict] | None]
    viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]  # image_path -> {base64, mime_type}
