"""Cleanup script: strip base64 image data from thread states.

Removes:
1. All image_url content parts from messages (replaced with placeholder text)
2. viewed_images state field (cleared to empty dict)

Usage:
    # Clean a specific thread
    cd backend && PYTHONPATH=. uv run python scripts/cleanup_thread_state.py <thread_id>

    # Scan all threads and report which ones need cleaning
    cd backend && PYTHONPATH=. uv run python scripts/cleanup_thread_state.py --scan

    # Clean all threads that have image data (>1 KB)
    cd backend && PYTHONPATH=. uv run python scripts/cleanup_thread_state.py --all

    # Dry run: report what would be cleaned without writing
    cd backend && PYTHONPATH=. uv run python scripts/cleanup_thread_state.py --all --dry-run
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


def _analyze_channel_values(channel_values: dict) -> tuple[int, int, int, int]:
    """Return (total_bytes, image_bytes, viewed_images_bytes, num_messages)."""
    total = len(json.dumps(channel_values, ensure_ascii=False, default=str))
    img_bytes = 0
    for msg in channel_values.get("messages", []):
        content = getattr(msg, "content", "") if not isinstance(msg, dict) else msg.get("content", "")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    img_bytes += len(json.dumps(part, ensure_ascii=False))
    vi = channel_values.get("viewed_images", {})
    vi_bytes = len(json.dumps(vi, ensure_ascii=False)) if vi else 0
    return total, img_bytes, vi_bytes, len(channel_values.get("messages", []))


async def cleanup_thread(thread_id: str, *, dry_run: bool = False) -> bool:
    """Clean a single thread. Returns True if any cleaning was done."""
    from deerflow.agents.checkpointer.async_provider import make_checkpointer

    async with make_checkpointer() as cp:
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

        latest = None
        async for tpl in cp.alist(config, limit=1):
            latest = tpl

        if latest is None:
            print(f"  No checkpoints found for thread {thread_id}")
            return False

        ckpt = getattr(latest, "checkpoint", {})
        meta = getattr(latest, "metadata", {})
        channel_values = ckpt.get("channel_values", {})

        original_size, img_bytes, vi_bytes, num_msgs = _analyze_channel_values(channel_values)

        if img_bytes == 0 and vi_bytes == 0:
            print(f"  {thread_id}: clean ({num_msgs} msgs, {original_size / 1024:.0f} KB)")
            return False

        print(f"  {thread_id}: {original_size / 1024 / 1024:.2f} MB ({num_msgs} msgs, "
              f"{img_bytes / 1024 / 1024:.2f} MB images, {vi_bytes / 1024 / 1024:.2f} MB viewed_images)")

        if dry_run:
            print(f"    [DRY RUN] Would clean {(img_bytes + vi_bytes) / 1024 / 1024:.2f} MB")
            return True

        # --- Clean viewed_images ---
        channel_values["viewed_images"] = {}

        # --- Clean messages ---
        messages = channel_values.get("messages", [])
        cleaned_msgs, _ = _clean_messages(messages)
        channel_values["messages"] = cleaned_msgs

        # --- Measure after ---
        cleaned_size = len(json.dumps(channel_values, ensure_ascii=False, default=str))
        print(f"    Cleaned: {original_size / 1024 / 1024:.2f} → {cleaned_size / 1024 / 1024:.2f} MB "
              f"(saved {(original_size - cleaned_size) / 1024 / 1024:.2f} MB)")

        # --- Write new checkpoint ---
        ckpt["channel_values"] = channel_values
        meta["updated_at"] = __import__("time").time()
        meta["source"] = "cleanup_script"
        meta["step"] = meta.get("step", 0) + 1

        write_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        new_config = await cp.aput(write_config, ckpt, meta, {})
        new_ckpt_id = new_config.get("configurable", {}).get("checkpoint_id")
        print(f"    New checkpoint: {new_ckpt_id}")
        return True


async def scan_all_threads() -> list[str]:
    """List all thread IDs from the checkpointer."""
    from deerflow.agents.checkpointer.async_provider import make_checkpointer

    thread_ids: list[str] = []
    async with make_checkpointer() as cp:
        async for tpl in cp.alist_tuple(limit=10000):
            config = getattr(tpl, "config", {})
            tid = config.get("configurable", {}).get("thread_id", "")
            if tid and tid not in thread_ids:
                thread_ids.append(tid)
    return thread_ids


async def main() -> None:
    parser = argparse.ArgumentParser(description="Cleanup bloated thread states")
    parser.add_argument("thread_id", nargs="?", help="Thread ID to clean")
    parser.add_argument("--all", action="store_true", help="Clean all threads with image data")
    parser.add_argument("--scan", action="store_true", help="Scan and report all threads")
    parser.add_argument("--dry-run", action="store_true", help="Report only, don't write")
    args = parser.parse_args()

    if args.thread_id:
        await cleanup_thread(args.thread_id, dry_run=args.dry_run)
        return

    print("Scanning all threads...")
    thread_ids = await scan_all_threads()
    print(f"Found {len(thread_ids)} threads\n")

    cleaned = 0
    for tid in thread_ids:
        did_clean = await cleanup_thread(tid, dry_run=args.dry_run)
        if did_clean:
            cleaned += 1

    print(f"\n{'Would clean' if args.dry_run else 'Cleaned'} {cleaned}/{len(thread_ids)} threads")


if __name__ == "__main__":
    asyncio.run(main())
