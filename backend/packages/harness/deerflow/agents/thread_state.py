import copy
from typing import Annotated, NotRequired, TypedDict

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


def apply_todo_ops(
    existing: list[dict] | None,
    updates: list[dict] | None,
    adds: list[dict] | None,
) -> list[dict]:
    """Apply incremental todo operations to an existing list."""
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
            todo = {"content": content, "status": item.get("status", "pending")}
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


class ThreadState(AgentState):
    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]
    todos: Annotated[list | None, merge_todos]
    uploaded_files: NotRequired[list[dict] | None]
    viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]  # image_path -> {base64, mime_type}
