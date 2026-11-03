"""Tests for SlimmingCheckpointer (P1 of session-data-slimming plan).

Covers:
- replacement correctness (view_image_context injected messages, viewed_images,
  plain dict messages, pending writes)
- copy-on-write semantics (in-memory objects untouched)
- failure degradation (original payload persisted when slimming raises)
- co-existence with the db5e768c interrupted-run collector (aput_writes of
  streamed AIMessageChunks) and the read-side ``_merge_pending_messages``
- archived reference format resolvable by the artifacts endpoint
"""

from __future__ import annotations

import base64
import json
import re
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from deerflow.runtime.checkpointer.slimming import (
    SlimmingCheckpointer,
    slim_channel_value,
    slim_checkpoint_for_put,
    slim_writes,
    wrap_with_slimming,
)

# A 4096+ char valid-ish base64 payload (well above the 4000-char threshold).
_B64_PAYLOAD = base64.b64encode(b"x" * 3072).decode()  # -> 4096 chars
assert len(_B64_PAYLOAD) >= 4000


def _image_part(mime: str = "image/png") -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{_B64_PAYLOAD}"}}


def _view_image_context_message(image_path: str = "/mnt/user-data/outputs/pic.png") -> HumanMessage:
    """Message shaped exactly like ViewImageMiddleware's injection."""
    return HumanMessage(
        content=[
            {"type": "text", "text": "Here are the images you've viewed:"},
            {"type": "text", "text": f"\n- **{image_path}** (image/png)"},
            _image_part(),
        ],
        name="view_image_context",
        additional_kwargs={"hide_from_ui": True},
        id="viewctx-1",
    )


CONFIG = {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}}


@pytest.fixture
def wrapped_saver():
    return wrap_with_slimming(InMemorySaver(), enabled=True)


def _data_url_count(value) -> int:
    return json.dumps(value, default=str).count("data:image/")


class TestSlimChannelValue:
    def test_view_image_context_message_replaced_with_path_reference(self):
        msg = _view_image_context_message()
        slimmed = slim_channel_value("messages", [msg], thread_id="t1")
        slim_msg = slimmed[0]
        assert slim_msg is not msg  # copy-on-write
        text = "".join(p["text"] for p in slim_msg.content if p["type"] == "text")
        assert "/mnt/user-data/outputs/pic.png" in text
        assert "view_image" in text
        assert _data_url_count(slim_msg.content) == 0
        assert slim_msg.id == msg.id
        assert msg.content[2] == _image_part()  # original untouched

    def test_small_images_kept(self):
        small = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 100}}
        msg = HumanMessage(content=[small], id="m1")
        slimmed = slim_channel_value("messages", [msg], thread_id="t1")
        assert slimmed is not None and slimmed[0].content[0] == small

    def test_dict_message_form(self):
        msg = {"id": "m2", "role": "human", "content": [{"type": "text", "text": "- **/mnt/user-data/outputs/a.jpg** (image/jpeg)"}, _image_part("image/jpeg")]}
        slimmed = slim_channel_value("messages", [msg], thread_id="t1")
        assert _data_url_count(slimmed[0]["content"]) == 0
        assert "/mnt/user-data/outputs/a.jpg" in slimmed[0]["content"][-1]["text"]

    def test_viewed_images_channel_stripped(self):
        value = {"/mnt/user-data/outputs/pic.png": {"base64": _B64_PAYLOAD, "mime_type": "image/png"}}
        slimmed = slim_channel_value("viewed_images", value, thread_id="t1")
        assert slimmed["/mnt/user-data/outputs/pic.png"]["base64"] is None
        assert slimmed["/mnt/user-data/outputs/pic.png"]["file_ref"] == "/mnt/user-data/outputs/pic.png"

    def test_other_channels_untouched(self):
        value = [{"big": _B64_PAYLOAD}]
        assert slim_channel_value("todos", value, thread_id="t1") is value

    def test_failure_degrades_to_original(self, monkeypatch):
        import deerflow.runtime.checkpointer.slimming as mod

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(mod, "_slim_message_like", boom)
        msg = _view_image_context_message()
        result = slim_channel_value("messages", [msg], thread_id="t1")
        assert result == [msg]  # original payload kept, no exception
        assert _data_url_count(result[0].content) == 1


class TestSlimWrites:
    def test_writes_pair_form(self):
        writes = [("messages", [_view_image_context_message()])]
        slimmed = slim_writes(writes, thread_id="t1")
        assert _data_url_count(slimmed[0][1]) == 0

    def test_writes_triple_form_keeps_task_id(self):
        writes = [("task-1", "messages", [_view_image_context_message()])]
        slimmed = slim_writes(writes, thread_id="t1")
        assert slimmed[0][0] == "task-1"
        assert _data_url_count(slimmed[0][2]) == 0


class TestSlimCheckpointForPut:
    def test_copy_on_write_checkpoint(self):
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"]["messages"] = [_view_image_context_message()]
        slimmed = slim_checkpoint_for_put(checkpoint, thread_id="t1")
        assert slimmed is not checkpoint
        assert _data_url_count(slimmed["channel_values"]["messages"]) == 0
        assert _data_url_count(checkpoint["channel_values"]["messages"]) == 1

    def test_unchanged_checkpoint_returned_as_is(self):
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"]["messages"] = [HumanMessage("hi", id="h1")]
        assert slim_checkpoint_for_put(checkpoint, thread_id="t1") is checkpoint


class TestSlimmingCheckpointerPersistence:
    """Assert against what the delegate actually persisted (InMemorySaver stores
    serialized blobs in ``.blobs``; the live in-memory graph state is untouched)."""

    @staticmethod
    def _stored_blob_text(saver) -> str:
        parts = []
        for (_tid, _ns, channel, _version), (_type, blob) in saver.delegate.blobs.items():
            if channel == "__pregel_tasks":
                continue
            parts.append(blob.decode("utf-8", "replace"))
        for per_checkpoint in saver.delegate.writes.values():
            for entry in per_checkpoint.values():
                # entry: (task_id, channel, (type, blob), [task_path...])
                blob = entry[2][1]
                parts.append(bytes(blob).decode("utf-8", "replace"))
        return "".join(parts)

    @pytest.mark.asyncio
    async def test_aput_stores_no_base64(self, wrapped_saver):
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"]["messages"] = [_view_image_context_message()]
        await wrapped_saver.aput(CONFIG, checkpoint, {"source": "loop"}, {"messages": 1})
        stored = self._stored_blob_text(wrapped_saver)
        assert "data:image/" not in stored
        assert "/mnt/user-data/outputs/pic.png" in stored
        assert "view_image" in stored

    @pytest.mark.asyncio
    async def test_original_in_memory_state_untouched(self, wrapped_saver):
        checkpoint = empty_checkpoint()
        msg = _view_image_context_message()
        checkpoint["channel_values"]["messages"] = [msg]
        await wrapped_saver.aput(CONFIG, checkpoint, {}, {"messages": 1})
        assert msg.content[2] == _image_part()

    @pytest.mark.asyncio
    async def test_aput_writes_pending_messages_slimmed(self, wrapped_saver):
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"]["messages"] = [HumanMessage("hi", id="h1")]
        returned = await wrapped_saver.aput(CONFIG, checkpoint, {}, {"messages": 1})
        checkpoint_id = returned["configurable"]["checkpoint_id"]
        write_config = {**CONFIG, "configurable": {**CONFIG["configurable"], "checkpoint_id": checkpoint_id}}
        # db5e768c collector path: partial reply committed via aput_writes
        reply = AIMessageChunk(
            content=[{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_B64_PAYLOAD}"}}],
            id="partial-1",
        )
        await wrapped_saver.aput_writes(write_config, [("messages", [reply])], "task-1")
        stored = self._stored_blob_text(wrapped_saver)
        assert "data:image/" not in stored
        assert "partial-1" in stored  # message id preserved for merge dedupe

    @pytest.mark.asyncio
    async def test_viewed_images_persisted_without_base64(self, wrapped_saver):
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"]["viewed_images"] = {"/mnt/user-data/outputs/p.png": {"base64": _B64_PAYLOAD, "mime_type": "image/png"}}
        await wrapped_saver.aput(CONFIG, checkpoint, {}, {"viewed_images": 1})
        stored = self._stored_blob_text(wrapped_saver)
        assert _B64_PAYLOAD not in stored
        assert "/mnt/user-data/outputs/p.png" in stored

    @pytest.mark.asyncio
    async def test_delegation_transparent_reads(self, wrapped_saver):
        inner = wrapped_saver.delegate
        assert isinstance(wrapped_saver, SlimmingCheckpointer)
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"]["messages"] = [HumanMessage("hi", id="h1")]
        await wrapped_saver.aput(CONFIG, checkpoint, {}, {"messages": 1})
        # reading through the wrapper and the delegate sees the same storage
        via_wrapper = await wrapped_saver.aget_tuple(CONFIG)
        via_delegate = await inner.aget_tuple(CONFIG)
        assert via_wrapper.checkpoint["id"] == via_delegate.checkpoint["id"]

    def test_disabled_passthrough(self):
        saver = InMemorySaver()
        assert wrap_with_slimming(saver, enabled=False) is saver
        assert wrap_with_slimming(wrap_with_slimming(saver, enabled=True), enabled=True).delegate is saver


class TestReferenceResolvableByArtifactsEndpoint:
    def test_reference_matches_artifacts_route_shape(self, wrapped_saver):
        """The placeholder's /mnt/user-data path must map to the artifacts route
        /api/threads/{thread_id}/artifacts/mnt/user-data/..."""
        msg = _view_image_context_message("/mnt/user-data/outputs/room-design.png")
        slimmed = slim_channel_value("messages", [msg], thread_id="t1")
        text = "".join(p["text"] for p in slimmed[0].content)
        match = re.search(r"/mnt/user-data/\S+?\.(?:png|jpg|jpeg|webp)", text)
        assert match, text
        virtual = match.group(0)
        assert virtual.startswith("/mnt/user-data/outputs/")
        # strip the leading slash the way the artifacts route does: {path:path}
        route_path = virtual.lstrip("/")
        assert re.match(r"mnt/user-data/outputs/[\w.\-]+\.png$", route_path)


class TestCollectorAndMergeCompat:
    def test_merge_pending_messages_with_slimmed_writes(self):
        """Slimmed committed messages + pending writes merge by id without
        duplication — the exact invariant db5e768c's read side relies on."""
        from app.gateway.routers.threads import _merge_pending_messages

        committed = [HumanMessage("hi", id="h1"), AIMessage("partial reply", id="partial-1")]
        channel_values = {"messages": committed}
        checkpoint_tuple = SimpleNamespace(pending_writes=[("task-1", "messages", [AIMessageChunk("stranded reply", id="stranded-1")])])
        merged = _merge_pending_messages(checkpoint_tuple, channel_values)
        assert [m.id for m in merged] == ["h1", "partial-1", "stranded-1"]
        # identical pending write (already committed) is deduped -> None fallback
        dup = SimpleNamespace(pending_writes=[("task-1", "messages", [AIMessageChunk("partial reply", id="partial-1")])])
        assert _merge_pending_messages(dup, channel_values) is None

    def test_merge_merge_works_on_slimmed_committed_messages(self):
        from app.gateway.routers.threads import _merge_pending_messages

        slimmed_committed = slim_channel_value("messages", [_view_image_context_message()], thread_id="t1")
        channel_values = {"messages": slimmed_committed}
        checkpoint_tuple = SimpleNamespace(pending_writes=[("task-1", "messages", [AIMessageChunk("reply", id="reply-1")])])
        merged = _merge_pending_messages(checkpoint_tuple, channel_values)
        assert [m.id for m in merged] == ["viewctx-1", "reply-1"]
        assert _data_url_count(merged) == 0


class TestVersioningForwarding:
    def test_get_next_version_delegates_to_postgres_style_versions(self):
        """Existing-thread continuation on Postgres uses string versions; the
        wrapper must forward versioning to the delegate (the base class
        default raises NotImplementedError for non-integer versions)."""

        class DelegateSaver(InMemorySaver):
            def get_next_version(self, current, channel):
                return f"0000{int(current or 0) + 1:04d}"

        wrapped = SlimmingCheckpointer(DelegateSaver())
        assert wrapped.get_next_version("00000001", None) == "00000002"
        # None (fresh thread) goes through the delegate too
        plain = wrap_with_slimming(InMemorySaver(), enabled=True)
        assert plain.get_next_version(None, None) is not None
