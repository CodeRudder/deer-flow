"""Rebuild thread store records after checkpoint DB cleanup.

Inserts known thread metadata into the store table so that thread IDs and
titles are preserved.  Run this after deer-flow has auto-created a new
checkpoints.db (e.g. after first thread access or server restart).

Usage:
    cd backend && python scripts/rebuild_threads.py

    # Custom DB path
    cd backend && python scripts/rebuild_threads.py --db /path/to/checkpoints.db
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time


# Thread metadata recovered from the previous store table.
# Each thread keeps its original ID so the frontend and local thread dirs
# remain valid.
KNOWN_THREADS = [
    {"thread_id": "54e645f2-e703-4ce9-9914-416288c05d14", "title": "游戏引擎架构与测试框架分析"},
    {"thread_id": "f365ab77-f454-4610-bba6-6736bccf31c8", "title": "Game Portal 添加挂机游戏栏目"},
    {"thread_id": "cce2df85-bd78-4bc4-a414-d324d7cff8cb", "title": "查看游戏开发进度"},
    {"thread_id": "df886916-d106-4944-ba66-5161ec715bb3", "title": "Game Portal Batch 4 Development"},
    {"thread_id": "a2dfa0b1-a96a-47c3-a4ef-ec3f28fd736e", "title": "多Agent协作小游戏网站设计"},
    {"thread_id": "b65fd9ed-8ff6-4d38-a7eb-88696416f9be", "title": "可用的 Subagent 类型列表"},
    {"thread_id": "16ebbd15-53b2-482f-b959-41118a2487ef", "title": "DeerFlow 2.0 自我介绍"},
    {"thread_id": "5fd0b281-392e-43bd-8162-a7be2a105c01", "title": "极客软件架构专家"},
    {"thread_id": "b1b0b907-8bd1-4869-bb96-682f9e50266e", "title": "三国游戏迭代开发计划"},
]


def find_db(custom_path: str | None = None) -> str:
    if custom_path and os.path.exists(custom_path):
        return custom_path
    candidates = [
        os.path.expanduser("~/.deer-flow/checkpoints.db"),
        os.path.join(os.path.dirname(__file__), "..", ".deer-flow", "checkpoints.db"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    print("Database not found. Start deer-flow first to auto-create it, then re-run.", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild thread store records")
    parser.add_argument("--db", help="Path to checkpoints.db")
    args = parser.parse_args()

    db_path = find_db(args.db)
    print(f"Database: {db_path}")

    conn = sqlite3.connect(db_path)

    # Check store table exists
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    if "store" not in tables:
        print("ERROR: store table not found. DB schema incomplete.", file=sys.stderr)
        conn.close()
        sys.exit(1)

    existing = {r[0] for r in conn.execute("SELECT key FROM store WHERE prefix='threads'")}
    print(f"Existing store records: {len(existing)}")

    now = time.time()
    inserted = 0
    skipped = 0
    for t in KNOWN_THREADS:
        tid = t["thread_id"]
        if tid in existing:
            print(f"  SKIP (exists): {t['title']}")
            skipped += 1
            continue

        record = json.dumps({
            "thread_id": tid,
            "status": "idle",
            "created_at": now,
            "updated_at": now,
            "metadata": {"graph_id": "lead_agent", "assistant_id": "bee7d354-5df5-5f26-a978-10ea053f620d"},
            "values": {"title": t["title"]},
        }, ensure_ascii=False)

        conn.execute(
            "INSERT INTO store (prefix, key, value, created_at, updated_at) VALUES (?, ?, ?, datetime('now'), datetime('now'))",
            ("threads", tid, record.encode("utf-8")),
        )
        print(f"  INSERT: {t['title']}")
        inserted += 1

    conn.commit()
    conn.close()
    print(f"\nDone: inserted {inserted}, skipped {skipped}, total {inserted + skipped} threads")


if __name__ == "__main__":
    main()
