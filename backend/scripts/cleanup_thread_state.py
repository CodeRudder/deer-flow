"""Cleanup script: strip base64 image data from a thread's state.

Removes:
1. All image_url content parts from messages (replaced with placeholder text)
2. viewed_images state field (cleared to empty dict)

This is a one-time migration to fix bloated thread states caused by
ViewImageMiddleware accumulating base64 images across turns.

Usage:
    cd backend && PYTHONPATH=. uv run python scripts/cleanup_thread_state.py <thread_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


def _clean_messages(messages: list) -> tuple[list, int]:
    """Remove image_url parts from messages, return cleaned list and bytes saved."""
    from langchain_core.messages import HumanMessage

    cleaned = []
    bytes_saved = 0

    for msg in messages:
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            new_parts = []
            has_images = False
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    bytes_saved += len(json.dumps(part, ensure_ascii=False))
                    has_images = True
                else:
                    new_parts.append(part)
            if has_images:
                new_parts.append({"type": "text", "text": "[图片数据已清理]"})
                cleaned.append(
                    HumanMessage(content=new_parts, id=getattr(msg, "id", None))
                )
            else:
                cleaned.append(msg)
        else:
            cleaned.append(msg)

    return cleaned, bytes_saved


async def cleanup_thread(thread_id: str) -> None:
    from deerflow.agents.checkpointer.async_provider import make_checkpointer

    print(f"Cleaning thread: {thread_id}")

    async with make_checkpointer() as cp:
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

        # Read the latest checkpoint
        latest = None
        async for tpl in cp.alist(config, limit=1):
            latest = tpl

        if latest is None:
            print(f"No checkpoints found for thread {thread_id}")
            return

        ckpt = getattr(latest, "checkpoint", {})
        meta = getattr(latest, "metadata", {})
        channel_values = ckpt.get("channel_values", {})

        # --- Measure before ---
        original_size = len(json.dumps(channel_values, ensure_ascii=False, default=str))
        print(f"Original state size: {original_size / 1024 / 1024:.2f} MB")

        # --- Clean viewed_images ---
        vi = channel_values.get("viewed_images", {})
        vi_size = len(json.dumps(vi, ensure_ascii=False)) if vi else 0
        channel_values["viewed_images"] = {}
        print(f"viewed_images: cleared {vi_size / 1024 / 1024:.2f} MB ({len(vi)} images)")

        # --- Clean messages ---
        messages = channel_values.get("messages", [])
        cleaned_msgs, img_bytes_saved = _clean_messages(messages)
        channel_values["messages"] = cleaned_msgs
        print(f"Messages: stripped {img_bytes_saved / 1024 / 1024:.2f} MB of image data")

        # --- Measure after ---
        cleaned_size = len(json.dumps(channel_values, ensure_ascii=False, default=str))
        print(f"Cleaned state size: {cleaned_size / 1024 / 1024:.2f} MB")
        print(f"Saved: {(original_size - cleaned_size) / 1024 / 1024:.2f} MB")

        # --- Write new checkpoint ---
        ckpt["channel_values"] = channel_values
        meta["updated_at"] = __import__("time").time()
        meta["source"] = "cleanup_script"
        meta["step"] = meta.get("step", 0) + 1

        write_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        new_config = await cp.aput(write_config, ckpt, meta, {})
        new_ckpt_id = new_config.get("configurable", {}).get("checkpoint_id")
        print(f"New checkpoint written: {new_ckpt_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cleanup bloated thread state")
    parser.add_argument("thread_id", help="Thread ID to clean")
    args = parser.parse_args()

    asyncio.run(cleanup_thread(args.thread_id))
