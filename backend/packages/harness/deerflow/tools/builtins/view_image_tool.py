import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from deerflow.agents.thread_state import ThreadDataState
from deerflow.config import get_app_config
from deerflow.config.paths import VIRTUAL_PATH_PREFIX
from deerflow.config.vision_model_config import get_vision_model_config, has_configured_vision_model
from deerflow.tools.types import Runtime
from deerflow.vision import VisionClient, VisionUnderstandingError
from deerflow.vision.vision_client import VISION_UNDERSTANDING_ERROR_MESSAGE

_ALLOWED_IMAGE_VIRTUAL_ROOTS = (
    f"{VIRTUAL_PATH_PREFIX}/workspace",
    f"{VIRTUAL_PATH_PREFIX}/uploads",
    f"{VIRTUAL_PATH_PREFIX}/outputs",
)
_ALLOWED_IMAGE_VIRTUAL_ROOTS_TEXT = ", ".join(_ALLOWED_IMAGE_VIRTUAL_ROOTS)
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_EXTENSION_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


@dataclass(frozen=True)
class _LoadedImage:
    data: bytes
    expected_mime_type: str


class _ViewImageInputError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _is_allowed_image_virtual_path(image_path: str) -> bool:
    return any(image_path == root or image_path.startswith(f"{root}/") for root in _ALLOWED_IMAGE_VIRTUAL_ROOTS)


def _detect_image_mime(image_data: bytes) -> str | None:
    if image_data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(image_data) >= 12 and image_data.startswith(b"RIFF") and image_data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _sanitize_image_error(error: Exception, thread_data: ThreadDataState | None) -> str:
    from deerflow.sandbox.tools import mask_local_paths_in_output

    return mask_local_paths_in_output(f"{type(error).__name__}: {error}", thread_data)


def _get_runtime_app_config(runtime: Runtime):
    context = runtime.context if runtime is not None else None
    if isinstance(context, dict) and context.get("app_config") is not None:
        return context["app_config"]
    return get_app_config()


def _get_runtime_vision_model_name(runtime: Runtime) -> str | None:
    context = runtime.context if runtime is not None else None
    if not isinstance(context, dict):
        return None
    value = context.get("vision_model_name")
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)


def _load_image_file_sync(image_path: str, thread_data: ThreadDataState | None) -> _LoadedImage:
    from deerflow.sandbox.exceptions import SandboxRuntimeError
    from deerflow.sandbox.tools import (
        resolve_and_validate_user_data_path,
        validate_local_tool_path,
    )

    try:
        validate_local_tool_path(image_path, thread_data, read_only=True)
        actual_path = resolve_and_validate_user_data_path(image_path, thread_data)
    except (PermissionError, SandboxRuntimeError) as e:
        raise _ViewImageInputError(f"Error: {str(e)}") from e

    path = Path(actual_path)

    if not path.exists():
        raise _ViewImageInputError(f"Error: Image file not found: {image_path}")

    if not path.is_file():
        raise _ViewImageInputError(f"Error: Path is not a file: {image_path}")

    expected_mime_type = _EXTENSION_TO_MIME.get(path.suffix.lower())
    if expected_mime_type is None:
        raise _ViewImageInputError(f"Error: Unsupported image format: {path.suffix}. Supported formats: {', '.join(_EXTENSION_TO_MIME)}")

    try:
        image_size = path.stat().st_size
    except OSError as e:
        raise _ViewImageInputError(f"Error reading image metadata: {_sanitize_image_error(e, thread_data)}") from e

    if image_size > _MAX_IMAGE_BYTES:
        raise _ViewImageInputError(f"Error: Image file is too large: {image_size} bytes. Maximum supported size is {_MAX_IMAGE_BYTES} bytes")

    try:
        with open(actual_path, "rb") as f:
            image_data = f.read()
    except Exception as e:
        raise _ViewImageInputError(f"Error reading image file: {_sanitize_image_error(e, thread_data)}") from e

    return _LoadedImage(data=image_data, expected_mime_type=expected_mime_type)


@tool("view_image", parse_docstring=True)
async def view_image_tool(
    runtime: Runtime,
    image_path: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Read an image file.

    Use this tool to read an image file. When an independent vision model is configured, this tool returns a text understanding of the image. Otherwise, it makes the image available to the main vision-capable model.

    When to use the view_image tool:
    - When you need to view an image file.

    When NOT to use the view_image tool:
    - For non-image files (use present_files instead)
    - For multiple files at once (use present_files instead)

    Args:
        image_path: Absolute /mnt/user-data virtual path to the image file. Common formats supported: jpg, jpeg, png, webp.
    """
    from deerflow.sandbox.tools import get_thread_data

    thread_data = get_thread_data(runtime)

    if not _is_allowed_image_virtual_path(image_path):
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"Error: Only image paths under {_ALLOWED_IMAGE_VIRTUAL_ROOTS_TEXT} are allowed",
                        tool_call_id=tool_call_id,
                    )
                ]
            },
        )

    try:
        loaded = await asyncio.to_thread(_load_image_file_sync, image_path, thread_data)
    except _ViewImageInputError as e:
        return Command(
            update={"messages": [ToolMessage(e.message, tool_call_id=tool_call_id)]},
        )

    image_data = loaded.data
    detected_mime_type = _detect_image_mime(image_data)
    if detected_mime_type is None:
        return Command(
            update={"messages": [ToolMessage("Error: File contents do not match a supported image format", tool_call_id=tool_call_id)]},
        )
    if detected_mime_type != loaded.expected_mime_type:
        return Command(
            update={"messages": [ToolMessage(f"Error: Image contents are {detected_mime_type}, but file extension indicates {loaded.expected_mime_type}", tool_call_id=tool_call_id)]},
        )
    mime_type = detected_mime_type
    image_base64 = base64.b64encode(image_data).decode("utf-8")

    app_config = _get_runtime_app_config(runtime)
    vision_model_name = _get_runtime_vision_model_name(runtime)
    vision_model_config = get_vision_model_config(app_config, vision_model_name)
    if vision_model_name and vision_model_config is None and has_configured_vision_model(app_config):
        return Command(
            update={
                "viewed_images": {},
                "messages": [ToolMessage(VISION_UNDERSTANDING_ERROR_MESSAGE, tool_call_id=tool_call_id)],
            },
        )
    if vision_model_config is not None:
        try:
            understanding = await VisionClient(vision_model_config, app_config.vision).understand_image_base64(
                image_base64=image_base64,
                mime_type=mime_type,
                image_path=image_path,
            )
        except VisionUnderstandingError:
            return Command(
                update={
                    "viewed_images": {},
                    "messages": [ToolMessage(VISION_UNDERSTANDING_ERROR_MESSAGE, tool_call_id=tool_call_id)],
                },
            )
        return Command(
            update={
                "viewed_images": {},
                "messages": [ToolMessage(understanding, tool_call_id=tool_call_id)],
            },
        )

    # Update viewed_images in state
    # The merge_viewed_images reducer will handle merging with existing images
    new_viewed_images = {image_path: {"base64": image_base64, "mime_type": mime_type}}

    return Command(
        update={"viewed_images": new_viewed_images, "messages": [ToolMessage("Successfully read image", tool_call_id=tool_call_id)]},
    )
