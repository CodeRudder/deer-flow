"""Write-side checkpoint slimming: replace persisted base64 images with file references.

Root cause (see docs/plans/2026-11-03-session-data-slimming.md): LangGraph
writes the full message history into every checkpoint, including the
``view_image_context`` human message that ``ViewImageMiddleware`` injects with
inline ``data:`` base64 image parts (2-3 MB each). Those payloads are copied
into every subsequent checkpoint blob, dominating the checkpointer database
(measured: 97.7% of a 1.24 GB thread).

``SlimmingCheckpointer`` wraps any ``BaseCheckpointSaver`` and rewrites
``image_url`` data-URL parts to a text placeholder carrying the archive path
before the delegate serializes anything to disk. The in-memory state used by
the running graph is untouched — replacement only affects the persisted copy,
so the model still "sees" the images on the turn they were injected.

Safety contract:
- Replacement is best-effort. Any exception in the slimming path is swallowed
  (logged) and the *original* value is written unchanged — a bloated
  checkpoint is always preferred over a lost one.
- Message ids are preserved, so the read-side pending-writes merge
  (``app.gateway.routers.threads._merge_pending_messages``, db5e768c) keeps
  deduplicating correctly.
- References use the ``/mnt/user-data/...`` virtual path namespace, which the
  artifacts router (``/api/threads/{thread_id}/artifacts/mnt/user-data/...``)
  can resolve.
"""

from __future__ import annotations

import base64 as _base64
import logging
import re
from typing import Any

from langchain_core.messages import BaseMessage
from langgraph.checkpoint.base import BaseCheckpointSaver, Checkpoint, CheckpointMetadata
from langgraph.config import RunnableConfig

logger = logging.getLogger(__name__)

# A data: URL carrying a base64 image payload.
_DATA_URL_RE = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,([A-Za-z0-9+/=\r\n]+)$")
# The text block ViewImageMiddleware emits before each image part:
#   \n- **/mnt/user-data/outputs/foo.png** (image/png)
_IMAGE_PATH_TEXT_RE = re.compile(r"^- \*\*(/mnt/user-data/[^*]+?)\*\* \(([^)]+)\)$")
# Base64 runs shorter than this are left alone (icons, tiny thumbnails).
_MIN_B64_CHARS = 4000

_FALLBACK_SUBDIR = "outputs/slimmed"
_PLACEHOLDER_TEMPLATE = "[图片已存档：{ref} ({mime}, ~{kb}KB)；需要查看请调用 view_image]"
_PLACEHOLDER_NO_REF = "[图片已省略 ({mime}, ~{kb}KB；原文件未找到)]"


def _detect_data_url(url: Any) -> tuple[str, int, str] | None:
    """Return (mime, payload_len, payload) if *url* is a data: image URL worth slimming."""
    if not isinstance(url, str) or not url.startswith("data:image/") or ";base64," not in url:
        return None
    match = _DATA_URL_RE.match(url)
    if match is None:
        return None
    mime, payload = match.group(1), match.group(2)
    if len(payload) < _MIN_B64_CHARS:
        return None
    return mime, len(payload), payload


def _archive_to_file(payload: str, mime: str, *, thread_id: str | None, name_hint: str) -> str | None:
    """Best-effort: write an orphan base64 payload into the thread's outputs dir.

    Used only when the message gives us no virtual path to reference (should
    never happen for view_image traffic — the middleware always emits the path
    text before each image part).
    """
    try:
        from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths

        data = _base64.b64decode(payload, validate=False)
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "bin")
        paths = get_paths()
        target_dir = paths.sandbox_outputs_dir(thread_id) / "slimmed"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{name_hint}.{ext}"
        target.write_bytes(data)
        return f"{VIRTUAL_PATH_PREFIX}/outputs/slimmed/{target.name}"
    except Exception:
        logger.warning("Failed to archive orphan base64 image payload", exc_info=True)
        return None


def _b64_part_placeholder(url: str, *, thread_id: str | None, name_hint: str) -> dict | None:
    """Build the replacement text part for a data-URL image part, or None to keep it."""
    detected = _detect_data_url(url)
    if detected is None:
        return None
    mime, size, payload = detected
    kb = max(1, size * 3 // 4 // 1024)
    ref = _archive_to_file(payload, mime, thread_id=thread_id, name_hint=name_hint)
    text = _PLACEHOLDER_TEMPLATE.format(ref=ref, mime=mime, kb=kb) if ref else _PLACEHOLDER_NO_REF.format(mime=mime, kb=kb)
    return {"type": "text", "text": text}


def _new_part_name_hint(thread_id: str | None, message_id: str | None, index: int) -> str:
    safe_thread = re.sub(r"[^A-Za-z0-9_-]", "-", thread_id or "thread")[:12]
    safe_msg = re.sub(r"[^A-Za-z0-9_-]", "-", message_id or "msg")[:24]
    return f"{safe_thread}-{safe_msg}-{index}"


def _slim_content_parts(
    parts: list[Any],
    *,
    thread_id: str | None,
    message_id: str | None,
) -> list[Any] | None:
    """Rewrite data-URL image parts in a message content list.

    Returns a new list when something changed, else ``None`` (so callers can
    avoid copying unchanged messages).
    """
    new_parts: list[Any] | None = None
    seen_image_index = 0
    last_image_path: str | None = None
    for index, part in enumerate(parts):
        # Track the path line ViewImageMiddleware emits before each image part.
        if isinstance(part, dict) and part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str):
                match = _IMAGE_PATH_TEXT_RE.match(text.strip())
                if match:
                    last_image_path = match.group(1)
        is_data_image = isinstance(part, dict) and part.get("type") == "image_url" and isinstance(part.get("image_url"), dict)
        if not is_data_image:
            if new_parts is not None:
                new_parts.append(part)
            continue
        url = part["image_url"].get("url")
        detected = _detect_data_url(url)
        if detected is None:
            if new_parts is not None:
                new_parts.append(part)
            continue
        if new_parts is None:
            # Copy-on-write: only from the first replaced part onwards.
            new_parts = list(parts[:index])
        seen_image_index += 1
        hint = _new_part_name_hint(thread_id, message_id, seen_image_index)
        if last_image_path:
            mime, size, _payload = detected
            kb = max(1, size * 3 // 4 // 1024)
            placeholder: dict[str, Any] = {
                "type": "text",
                "text": _PLACEHOLDER_TEMPLATE.format(ref=last_image_path, mime=mime, kb=kb),
            }
        else:
            placeholder = _b64_part_placeholder(url, thread_id=thread_id, name_hint=hint) or part
        new_parts.append(placeholder)
        if not last_image_path:
            logger.debug("Slimmed inline image part without a paired path (msg=%s)", message_id)
    return new_parts


def _slim_message_like(message: Any, *, thread_id: str | None) -> Any:
    """Rewrite one message (LangChain object or plain dict). Never mutates input."""
    content = None
    if isinstance(message, BaseMessage):
        content = message.content
    elif isinstance(message, dict):
        content = message.get("content")
    if not isinstance(content, list):
        return message

    message_id = None
    if isinstance(message, BaseMessage):
        message_id = getattr(message, "id", None)
    elif isinstance(message, dict):
        message_id = message.get("id")

    new_parts = _slim_content_parts(content, thread_id=thread_id, message_id=message_id)
    if new_parts is None:
        return message
    if isinstance(message, BaseMessage):
        copied = message.model_copy()
        copied.content = new_parts
        return copied
    return {**message, "content": new_parts}


def _slim_viewed_images(value: Any) -> Any:
    """Strip base64 payloads from the ``viewed_images`` channel value.

    Files referenced here already live on disk under /mnt/user-data (view_image
    only reads from workspace/uploads/outputs), so the path key itself is the
    durable reference.
    """
    if not isinstance(value, dict) or not value:
        return value
    changed = False
    new_value: dict[Any, Any] = {}
    for path, info in value.items():
        if isinstance(info, dict) and isinstance(info.get("base64"), str) and len(info["base64"]) >= _MIN_B64_CHARS:
            new_value[path] = {**info, "base64": None, "file_ref": path}
            changed = True
        else:
            new_value[path] = info
    return new_value if changed else value


def slim_channel_value(channel: str, value: Any, *, thread_id: str | None) -> Any:
    """Slim one checkpoint channel value. Returns the original object if unchanged."""
    try:
        if channel == "messages":
            if not isinstance(value, list):
                return value
            slimmed = [_slim_message_like(m, thread_id=thread_id) for m in value]
            if all(s is o for s, o in zip(slimmed, value)):
                return value
            return slimmed
        if channel == "viewed_images":
            return _slim_viewed_images(value)
        return value
    except Exception:
        logger.exception("Checkpoint slimming failed for channel %s — writing unchanged", channel)
        return value


def slim_writes(writes: Any, *, thread_id: str | None) -> Any:
    """Slim a ``writes`` list of ``(channel, value)`` tuples (or triples with task_id)."""
    if not isinstance(writes, (list, tuple)):
        return writes
    out: list[Any] = []
    changed = False
    for item in writes:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            channel, value = item
            slim_value = slim_channel_value(channel, value, thread_id=thread_id)
            out.append((channel, slim_value))
            if slim_value is not value:
                changed = True
        elif isinstance(item, (list, tuple)) and len(item) == 3:
            task_id, channel, value = item
            slim_value = slim_channel_value(channel, value, thread_id=thread_id)
            out.append((task_id, channel, slim_value))
            if slim_value is not value:
                changed = True
        else:
            out.append(item)
    return out if changed else writes


def slim_checkpoint_for_put(checkpoint: Checkpoint, *, thread_id: str | None) -> Checkpoint:
    """Return a checkpoint whose channel_values have been slimmed (copy-on-write)."""
    channel_values = checkpoint.get("channel_values")
    if not isinstance(channel_values, dict) or not channel_values:
        return checkpoint
    new_values: dict[str, Any] = {}
    changed = False
    for channel, value in channel_values.items():
        slim_value = slim_channel_value(channel, value, thread_id=thread_id)
        if slim_value is not value:
            changed = True
            new_values[channel] = slim_value
        elif changed:
            new_values[channel] = value
    if not changed:
        return checkpoint
    slimmed = dict(checkpoint)
    slimmed["channel_values"] = {**channel_values, **{k: v for k, v in new_values.items()}}
    return slimmed


class SlimmingCheckpointer(BaseCheckpointSaver):
    """Wrapper that slims base64 image payloads out of everything it persists.

    Delegates every operation to *delegate*; only the write paths (``aput`` /
    ``put`` / ``aput_writes`` / ``put_writes``) intercept and rewrite values
    first. Read paths are transparent, so checkpoint time travel, pending
    writes and the db5e768c interrupted-run collector behave identically.
    """

    def __init__(self, delegate: BaseCheckpointSaver) -> None:
        try:
            serde = delegate.serde
        except AttributeError:
            # Tolerate test doubles that don't implement the saver protocol
            # (thread-safety tests use plain objects as factory values).
            serde = None
        super().__init__(serde=serde)
        self._delegate = delegate

    @property
    def delegate(self) -> BaseCheckpointSaver:
        return self._delegate

    def __getattr__(self, name: str) -> Any:
        # Only called for attributes not found normally — forward everything
        # else (conn/pool, setup, adelete_thread, adelete_for_runs, ...).
        return getattr(self._delegate, name)

    def _thread_id(self, config: RunnableConfig | None) -> str | None:
        try:
            return (config or {}).get("configurable", {}).get("thread_id")
        except Exception:
            return None

    # -- async write paths ------------------------------------------------

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, Any] | None,
    ) -> RunnableConfig:
        slimmed = slim_checkpoint_for_put(checkpoint, thread_id=self._thread_id(config))
        return await self._delegate.aput(config, slimmed, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: list[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        slimmed = slim_writes(writes, thread_id=self._thread_id(config))
        await self._delegate.aput_writes(config, slimmed, task_id, task_path)

    # -- sync write paths ---------------------------------------------------

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, Any] | None,
    ) -> RunnableConfig:
        slimmed = slim_checkpoint_for_put(checkpoint, thread_id=self._thread_id(config))
        return self._delegate.put(config, slimmed, metadata, new_versions)

    def put_writes(self, config: RunnableConfig, writes: list[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        slimmed = slim_writes(writes, thread_id=self._thread_id(config))
        self._delegate.put_writes(config, slimmed, task_id, task_path=task_path)

    # -- explicit read/forwarding overrides ---------------------------------
    # The base class defines default implementations for these (aget_tuple
    # falls back to get_tuple etc.), so __getattr__ never sees the lookup.
    # Forward them explicitly so both sync and async read paths work against
    # backends that only implement one side (e.g. InMemorySaver).

    async def aget_tuple(self, config: RunnableConfig):
        return await self._delegate.aget_tuple(config)

    def get_tuple(self, config: RunnableConfig):
        return self._delegate.get_tuple(config)

    async def alist(self, config: RunnableConfig, **kwargs):  # noqa: ANN201, ANN003
        async for item in self._delegate.alist(config, **kwargs):
            yield item

    def list(self, config: RunnableConfig, **kwargs):  # noqa: ANN201, ANN003
        return self._delegate.list(config, **kwargs)

    def get_next_version(self, current: Any, channel: Any) -> Any:
        # Versioning must follow the delegate's scheme (e.g. Postgres string
        # versions) — the base class default raises NotImplementedError for
        # non-integer versions.
        return self._delegate.get_next_version(current, channel)

    def delete_thread(self, thread_id: str) -> None:
        self._delegate.delete_thread(thread_id)

    async def adelete_thread(self, thread_id: str) -> None:
        await self._delegate.adelete_thread(thread_id)


def slimming_enabled_from_config(app_config: Any) -> bool:
    """Read ``checkpoint_slimming.enabled`` from the app config extras (default: true)."""
    try:
        extra = getattr(app_config, "model_extra", None) or {}
        cfg = extra.get("checkpoint_slimming", {}) or {}
        return bool(cfg.get("enabled", True))
    except Exception:
        return True


# Process-wide slimming switch, set once at checkpointer construction (async
# provider, which owns the AppConfig) so the sync singleton — which must not
# load AppConfig itself (see get_checkpointer's lock-ordering constraints) —
# can honour the same switch.
_slimming_enabled_global: bool | None = None


def set_slimming_enabled(enabled: bool) -> None:
    """Record the process-wide slimming switch (called from the async factory)."""
    global _slimming_enabled_global
    _slimming_enabled_global = bool(enabled)


def get_slimming_enabled_default() -> bool:
    """The recorded process-wide switch; defaults to enabled."""
    return True if _slimming_enabled_global is None else _slimming_enabled_global


def wrap_with_slimming(checkpointer: BaseCheckpointSaver, *, enabled: bool) -> BaseCheckpointSaver:
    """Wrap *checkpointer* unless slimming is disabled or already applied."""
    if not enabled or isinstance(checkpointer, SlimmingCheckpointer):
        return checkpointer
    logger.info("Checkpoint slimming enabled: base64 image payloads will be replaced with file references on write")
    return SlimmingCheckpointer(checkpointer)
