"""Migrate legacy no-auth session data into an admin-owned layout.

Usage:
    PYTHONPATH=. python scripts/migrate_legacy_sessions_to_admin.py --admin-email admin@example.com --dry-run
    PYTHONPATH=. python scripts/migrate_legacy_sessions_to_admin.py --admin-email admin@example.com --apply

The migration is intentionally explicit and offline-oriented.  It rebuilds the
new SQL indexes from legacy LangGraph store/checkpoint data and old filesystem
directories, rather than assuming old conversations already exist in
threads_meta/runs/run_events.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.config.app_config import AppConfig
from deerflow.config.paths import Paths, get_paths
from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow

logger = logging.getLogger(__name__)

THREADS_NAMESPACE = ("threads",)
LEGACY_RUN_NAMESPACE = uuid.UUID("7f7420ff-1a0b-43f4-9b98-9b8f51a3fce0")


@dataclass
class LegacyThreadCandidate:
    thread_id: str
    sources: set[str] = field(default_factory=set)
    store_value: dict[str, Any] | None = None
    legacy_dir: Path | None = None
    jsonl_path: Path | None = None
    checkpoint_tuple: Any | None = None


@dataclass
class MigrationStats:
    admin_id: str
    admin_email: str | None = None
    dry_run: bool = True
    candidates: int = 0
    source_counts: Counter[str] = field(default_factory=Counter)
    threads_meta_created: int = 0
    threads_meta_owner_updated: int = 0
    store_owner_updated: int = 0
    legacy_dirs_moved: int = 0
    memory_migrated: int = 0
    agents_migrated: int = 0
    runs_created: int = 0
    run_events_created: int = 0
    event_sources: Counter[str] = field(default_factory=Counter)
    owner_backfill: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def legacy_run_id(thread_id: str) -> str:
    return f"legacy-{uuid.uuid5(LEGACY_RUN_NAMESPACE, thread_id)}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    return None


def _message_type(message: Any) -> str:
    if isinstance(message, dict):
        raw = message.get("type") or message.get("role") or message.get("message_type")
        if raw:
            return str(raw)
        data = message.get("data")
        if isinstance(data, dict):
            raw = data.get("type") or data.get("role")
            if raw:
                return str(raw)
    raw = getattr(message, "type", None)
    if raw:
        return str(raw)
    class_name = type(message).__name__.lower()
    if "human" in class_name:
        return "human"
    if "ai" in class_name:
        return "ai"
    if "tool" in class_name:
        return "tool"
    return "unknown"


def _message_content(message: Any) -> Any:
    if isinstance(message, dict):
        if "content" in message:
            return message.get("content")
        data = message.get("data")
        if isinstance(data, dict):
            return data.get("content")
        kwargs = message.get("kwargs")
        if isinstance(kwargs, dict):
            return kwargs.get("content")
    return getattr(message, "content", "")


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _message_dump(message: Any) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        dumped = message.model_dump()
        return dumped if isinstance(dumped, dict) else {"content": dumped}
    if isinstance(message, dict):
        return dict(message)
    return {"type": _message_type(message), "content": _message_content(message)}


def _event_type_for_message(message: Any) -> str | None:
    typ = _message_type(message).lower()
    if typ in {"human", "user"}:
        return "llm.human.input"
    if typ in {"ai", "assistant"}:
        return "llm.ai.response"
    if typ == "tool":
        return "llm.tool.result"
    return None


def _messages_from_checkpoint_tuple(checkpoint_tuple: Any | None) -> list[Any]:
    if checkpoint_tuple is None:
        return []
    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
    if not isinstance(checkpoint, dict):
        return []
    channel_values = checkpoint.get("channel_values", {}) or {}
    if not isinstance(channel_values, dict):
        return []
    messages = channel_values.get("messages")
    return list(messages) if isinstance(messages, list) else []


def _checkpoint_metadata(checkpoint_tuple: Any | None) -> dict[str, Any]:
    if checkpoint_tuple is None:
        return {}
    metadata = getattr(checkpoint_tuple, "metadata", {}) or {}
    return metadata if isinstance(metadata, dict) else {}


def _checkpoint_title(checkpoint_tuple: Any | None) -> str | None:
    if checkpoint_tuple is None:
        return None
    checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
    channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    if isinstance(channel_values, dict):
        title = channel_values.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    metadata = _checkpoint_metadata(checkpoint_tuple)
    for key in ("title", "display_name"):
        title = metadata.get(key)
        if isinstance(title, str) and title.strip():
            return title.strip()
    return None


def _read_jsonl_messages(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    messages: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                messages.append(item)
    return messages


def _jsonl_to_message_dump(item: dict[str, Any]) -> dict[str, Any]:
    role = str(item.get("role") or "unknown")
    msg: dict[str, Any] = {
        "type": {"human": "human", "user": "human", "ai": "ai", "assistant": "ai", "tool": "tool"}.get(role, role),
        "content": item.get("content", ""),
    }
    if item.get("id"):
        msg["id"] = item["id"]
    if item.get("tool_calls"):
        msg["tool_calls"] = item["tool_calls"]
    if item.get("tool_call_id"):
        msg["tool_call_id"] = item["tool_call_id"]
    if item.get("name"):
        msg["name"] = item["name"]
    return msg


def _event_type_for_jsonl(item: dict[str, Any]) -> str | None:
    role = str(item.get("role") or "").lower()
    if role in {"human", "user"}:
        return "llm.human.input"
    if role in {"ai", "assistant"}:
        return "llm.ai.response"
    if role == "tool":
        return "llm.tool.result"
    return None


def _first_text(messages: Iterable[Any], *types: str) -> str | None:
    wanted = {t.lower() for t in types}
    for message in messages:
        if _message_type(message).lower() in wanted:
            text = _text_content(_message_content(message)).strip()
            if text:
                return text[:4000]
    return None


def _last_text(messages: Iterable[Any], *types: str) -> str | None:
    wanted = {t.lower() for t in types}
    for message in reversed(list(messages)):
        if _message_type(message).lower() in wanted:
            text = _text_content(_message_content(message)).strip()
            if text:
                return text[:4000]
    return None


async def _iter_store_items(store: Any, namespace: tuple[str, ...], *, page_size: int = 500):
    offset = 0
    while True:
        batch = await store.asearch(namespace, limit=page_size, offset=offset)
        if not batch:
            return
        for item in batch:
            yield item
        if len(batch) < page_size:
            return
        offset += page_size


async def collect_legacy_thread_candidates(
    *,
    paths: Paths,
    store: Any | None = None,
) -> dict[str, LegacyThreadCandidate]:
    candidates: dict[str, LegacyThreadCandidate] = {}

    def get_candidate(thread_id: str) -> LegacyThreadCandidate:
        candidate = candidates.get(thread_id)
        if candidate is None:
            candidate = LegacyThreadCandidate(thread_id=thread_id)
            candidates[thread_id] = candidate
        return candidate

    if store is not None:
        async for item in _iter_store_items(store, THREADS_NAMESPACE):
            thread_id = str(item.key)
            candidate = get_candidate(thread_id)
            candidate.sources.add("store")
            value = getattr(item, "value", None)
            if isinstance(value, dict):
                candidate.store_value = value

    legacy_threads = paths.base_dir / "threads"
    if legacy_threads.exists():
        for thread_dir in sorted(legacy_threads.iterdir()):
            if not thread_dir.is_dir():
                continue
            thread_id = thread_dir.name
            candidate = get_candidate(thread_id)
            candidate.sources.add("legacy_dir")
            candidate.legacy_dir = thread_dir
            jsonl_path = thread_dir / "conversation.jsonl"
            if jsonl_path.exists():
                candidate.sources.add("conversation_jsonl")
                candidate.jsonl_path = jsonl_path

    return candidates


async def attach_checkpoints(candidates: dict[str, LegacyThreadCandidate], checkpointer: Any | None) -> None:
    if checkpointer is None:
        return
    for candidate in candidates.values():
        config = {"configurable": {"thread_id": candidate.thread_id, "checkpoint_ns": ""}}
        try:
            candidate.checkpoint_tuple = await checkpointer.aget_tuple(config)
        except Exception as exc:
            candidate.checkpoint_tuple = None
            candidate.sources.add("checkpoint_error")
            logger.warning("Failed to read checkpoint for thread %s: %s", candidate.thread_id, exc)
        else:
            if candidate.checkpoint_tuple is not None:
                candidate.sources.add("checkpoint")


async def resolve_admin_user(
    sf: async_sessionmaker[AsyncSession],
    *,
    admin_id: str | None = None,
    admin_email: str | None = None,
) -> tuple[str, str | None]:
    async with sf() as session:
        stmt = select(UserRow).where(UserRow.system_role == "admin")
        if admin_id:
            stmt = stmt.where(UserRow.id == admin_id)
        if admin_email:
            stmt = stmt.where(UserRow.email == admin_email)
        rows = list((await session.execute(stmt)).scalars())

    if not rows:
        raise SystemExit("No matching admin user found. Create /setup admin first or pass the correct --admin-id/--admin-email.")
    if len(rows) > 1:
        raise SystemExit("Multiple admin users found. Pass --admin-id or --admin-email explicitly.")
    row = rows[0]
    return str(row.id), row.email


def _candidate_title(candidate: LegacyThreadCandidate, checkpoint_messages: list[Any], jsonl_messages: list[dict[str, Any]]) -> str | None:
    value = candidate.store_value or {}
    values = value.get("values") if isinstance(value, dict) else None
    if isinstance(values, dict):
        title = values.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    title = _checkpoint_title(candidate.checkpoint_tuple)
    if title:
        return title
    first_human = _first_text(checkpoint_messages, "human", "user")
    if first_human:
        return first_human[:120]
    first_jsonl = _first_text(jsonl_messages, "human", "user")
    if first_jsonl:
        return first_jsonl[:120]
    return None


def _candidate_metadata(candidate: LegacyThreadCandidate) -> dict[str, Any]:
    value = candidate.store_value or {}
    metadata = value.get("metadata") if isinstance(value, dict) else None
    merged = dict(metadata) if isinstance(metadata, dict) else {}
    merged.pop("owner_id", None)
    merged.pop("user_id", None)
    merged["legacy_migrated"] = True
    merged["legacy_sources"] = sorted(candidate.sources)
    return merged


def _candidate_created_updated(candidate: LegacyThreadCandidate, jsonl_messages: list[dict[str, Any]]) -> tuple[datetime, datetime]:
    metadata = _checkpoint_metadata(candidate.checkpoint_tuple)
    created = _coerce_datetime(metadata.get("created_at")) or _coerce_datetime(metadata.get("thread_created_at"))
    updated = _coerce_datetime(metadata.get("updated_at")) or _coerce_datetime(metadata.get("created_at"))

    if jsonl_messages:
        created = created or _coerce_datetime(jsonl_messages[0].get("ts"))
        updated = updated or _coerce_datetime(jsonl_messages[-1].get("ts"))

    if candidate.legacy_dir is not None and candidate.legacy_dir.exists():
        mtime = datetime.fromtimestamp(candidate.legacy_dir.stat().st_mtime, tz=UTC)
        created = created or mtime
        updated = updated or mtime

    now = _utc_now()
    return created or now, updated or created or now


async def upsert_thread_meta(
    session: AsyncSession,
    candidate: LegacyThreadCandidate,
    *,
    admin_id: str,
    checkpoint_messages: list[Any],
    jsonl_messages: list[dict[str, Any]],
    dry_run: bool,
    stats: MigrationStats,
) -> None:
    row = await session.get(ThreadMetaRow, candidate.thread_id)
    if row is not None:
        if row.user_id is None:
            stats.threads_meta_owner_updated += 1
            if not dry_run:
                row.user_id = admin_id
                row.updated_at = _utc_now()
            return
        if row.user_id == admin_id:
            stats.skipped["threads_meta_owned"] += 1
            return
        stats.conflicts.append(f"thread {candidate.thread_id} already owned by {row.user_id}; not reassigning")
        return

    created_at, updated_at = _candidate_created_updated(candidate, jsonl_messages)
    stats.threads_meta_created += 1
    if dry_run:
        return

    session.add(
        ThreadMetaRow(
            thread_id=candidate.thread_id,
            assistant_id=None,
            user_id=admin_id,
            display_name=_candidate_title(candidate, checkpoint_messages, jsonl_messages),
            status="idle",
            metadata_json=_candidate_metadata(candidate),
            created_at=created_at,
            updated_at=updated_at,
        )
    )


async def migrate_store_owner(
    store: Any | None,
    candidates: dict[str, LegacyThreadCandidate],
    *,
    admin_id: str,
    dry_run: bool,
    stats: MigrationStats,
) -> None:
    if store is None:
        return
    for candidate in candidates.values():
        if not candidate.store_value:
            continue
        value = dict(candidate.store_value)
        metadata = dict(value.get("metadata") or {})
        existing = metadata.get("user_id")
        if existing == admin_id:
            stats.skipped["store_owned"] += 1
            continue
        if existing:
            stats.conflicts.append(f"store thread {candidate.thread_id} already has metadata.user_id={existing}; not reassigning")
            continue
        metadata["user_id"] = admin_id
        value["metadata"] = metadata
        stats.store_owner_updated += 1
        if not dry_run:
            await store.aput(THREADS_NAMESPACE, candidate.thread_id, value)


def migrate_legacy_thread_dirs(
    paths: Paths,
    candidates: dict[str, LegacyThreadCandidate],
    *,
    admin_id: str,
    dry_run: bool,
    stats: MigrationStats,
) -> None:
    for candidate in candidates.values():
        source = candidate.legacy_dir
        if source is None or not source.exists():
            continue
        dest = paths.thread_dir(candidate.thread_id, user_id=admin_id)
        if dest.exists():
            conflict_dir = paths.base_dir / "migration-conflicts" / "threads" / candidate.thread_id
            stats.conflicts.append(f"thread dir conflict for {candidate.thread_id}: {source} -> {conflict_dir}")
            if not dry_run:
                conflict_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(conflict_dir))
            continue
        stats.legacy_dirs_moved += 1
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(dest))

    legacy_threads = paths.base_dir / "threads"
    if not dry_run and legacy_threads.exists() and not any(legacy_threads.iterdir()):
        legacy_threads.rmdir()


def migrate_legacy_memory_and_agents(paths: Paths, *, admin_id: str, dry_run: bool, stats: MigrationStats) -> None:
    legacy_mem = paths.base_dir / "memory.json"
    if legacy_mem.exists():
        dest = paths.user_memory_file(admin_id)
        if dest.exists():
            conflict = paths.base_dir / "migration-conflicts" / "memory.json"
            stats.conflicts.append(f"memory conflict: {legacy_mem} -> {conflict}")
            if not dry_run:
                conflict.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy_mem), str(conflict))
        else:
            stats.memory_migrated += 1
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy_mem), str(dest))

    legacy_agents = paths.agents_dir
    if not legacy_agents.exists():
        return
    for agent_dir in sorted(legacy_agents.iterdir()):
        if not agent_dir.is_dir():
            continue
        dest = paths.user_agent_dir(admin_id, agent_dir.name)
        if dest.exists():
            conflict = paths.base_dir / "migration-conflicts" / "agents" / agent_dir.name
            stats.conflicts.append(f"agent conflict: {agent_dir} -> {conflict}")
            if not dry_run:
                conflict.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(agent_dir), str(conflict))
            continue
        stats.agents_migrated += 1
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(agent_dir), str(dest))
    if not dry_run and legacy_agents.exists() and not any(legacy_agents.iterdir()):
        legacy_agents.rmdir()


def _run_summary_messages(checkpoint_messages: list[Any], jsonl_messages: list[dict[str, Any]]) -> list[Any]:
    return checkpoint_messages if checkpoint_messages else jsonl_messages


async def ensure_synthetic_run(
    session: AsyncSession,
    candidate: LegacyThreadCandidate,
    *,
    admin_id: str,
    checkpoint_messages: list[Any],
    jsonl_messages: list[dict[str, Any]],
    dry_run: bool,
    stats: MigrationStats,
) -> str:
    run_id = legacy_run_id(candidate.thread_id)
    existing = await session.get(RunRow, run_id)
    if existing is not None:
        stats.skipped["run_exists"] += 1
        return run_id

    summary_messages = _run_summary_messages(checkpoint_messages, jsonl_messages)
    created_at, updated_at = _candidate_created_updated(candidate, jsonl_messages)
    stats.runs_created += 1
    if not dry_run:
        session.add(
            RunRow(
                run_id=run_id,
                thread_id=candidate.thread_id,
                assistant_id=None,
                user_id=admin_id,
                status="success" if summary_messages else "interrupted",
                model_name=None,
                multitask_strategy="reject",
                metadata_json={"legacy_migrated": True, "legacy_sources": sorted(candidate.sources)},
                kwargs_json={},
                error=None,
                message_count=len(summary_messages),
                first_human_message=_first_text(summary_messages, "human", "user"),
                last_ai_message=_last_text(summary_messages, "ai", "assistant"),
                total_input_tokens=0,
                total_output_tokens=0,
                total_tokens=0,
                llm_call_count=0,
                lead_agent_tokens=0,
                subagent_tokens=0,
                middleware_tokens=0,
                follow_up_to_run_id=None,
                created_at=created_at,
                updated_at=updated_at,
            )
        )
    return run_id


async def rebuild_run_events(
    session: AsyncSession,
    candidate: LegacyThreadCandidate,
    *,
    admin_id: str,
    run_id: str,
    checkpoint_messages: list[Any],
    jsonl_messages: list[dict[str, Any]],
    dry_run: bool,
    overwrite_events: bool,
    stats: MigrationStats,
) -> None:
    existing_count = await session.scalar(
        select(func.count())
        .select_from(RunEventRow)
        .where(
            RunEventRow.thread_id == candidate.thread_id,
            RunEventRow.run_id == run_id,
        )
    )
    if existing_count and existing_count > 0:
        if not overwrite_events:
            stats.skipped["run_events_exist"] += 1
            return
        if not dry_run:
            await session.execute(delete(RunEventRow).where(RunEventRow.thread_id == candidate.thread_id, RunEventRow.run_id == run_id))

    events: list[tuple[str, dict[str, Any], datetime | None, dict[str, Any]]] = []
    if checkpoint_messages:
        for message in checkpoint_messages:
            event_type = _event_type_for_message(message)
            if event_type is None:
                continue
            events.append(
                (
                    event_type,
                    _message_dump(message),
                    None,
                    {"legacy_migrated": True, "legacy_source": "checkpoint", "legacy_run_id": run_id},
                )
            )
        stats.event_sources["checkpoint"] += len(events)
    elif jsonl_messages:
        for item in jsonl_messages:
            event_type = _event_type_for_jsonl(item)
            if event_type is None:
                continue
            events.append(
                (
                    event_type,
                    _jsonl_to_message_dump(item),
                    _coerce_datetime(item.get("ts")),
                    {"legacy_migrated": True, "legacy_source": "conversation_jsonl", "legacy_run_id": run_id, "lossy": True},
                )
            )
        stats.event_sources["conversation_jsonl"] += len(events)
    else:
        stats.event_sources["skipped"] += 1
        return

    if not events:
        stats.event_sources["skipped"] += 1
        return

    stats.run_events_created += len(events)
    if dry_run:
        return

    created_default, _ = _candidate_created_updated(candidate, jsonl_messages)
    for seq, (event_type, content, created_at, metadata) in enumerate(events, start=1):
        session.add(
            RunEventRow(
                thread_id=candidate.thread_id,
                run_id=run_id,
                user_id=admin_id,
                event_type=event_type,
                category="message",
                content=json.dumps(content, default=str, ensure_ascii=False),
                event_metadata={**metadata, "content_is_json": True, "content_is_dict": True},
                seq=seq,
                created_at=created_at or created_default,
            )
        )


async def owner_backfill(
    session: AsyncSession,
    *,
    admin_id: str,
    dry_run: bool,
    stats: MigrationStats,
) -> None:
    tables = [
        ("threads_meta", ThreadMetaRow),
        ("runs", RunRow),
        ("run_events", RunEventRow),
    ]
    for name, model in tables:
        count = await session.scalar(select(func.count()).select_from(model).where(model.user_id.is_(None))) or 0
        stats.owner_backfill[name] += count
        if count and not dry_run:
            await session.execute(update(model).where(model.user_id.is_(None)).values(user_id=admin_id))

    feedback_null = await session.scalar(select(func.count()).select_from(FeedbackRow).where(FeedbackRow.user_id.is_(None))) or 0
    stats.owner_backfill["feedback"] += feedback_null
    if feedback_null == 0:
        return
    duplicate_rows = await session.execute(select(FeedbackRow.thread_id, FeedbackRow.run_id, func.count()).where(FeedbackRow.user_id.is_(None)).group_by(FeedbackRow.thread_id, FeedbackRow.run_id).having(func.count() > 1))
    duplicates = list(duplicate_rows)
    if duplicates:
        for thread_id, run_id, count in duplicates:
            stats.conflicts.append(f"feedback NULL owner conflict: thread={thread_id} run={run_id} rows={count}")
        return
    if not dry_run:
        await session.execute(update(FeedbackRow).where(FeedbackRow.user_id.is_(None)).values(user_id=admin_id))


async def migrate_legacy_sessions(
    *,
    sf: async_sessionmaker[AsyncSession],
    paths: Paths,
    admin_id: str,
    admin_email: str | None = None,
    store: Any | None = None,
    checkpointer: Any | None = None,
    dry_run: bool = True,
    overwrite_events: bool = False,
) -> MigrationStats:
    stats = MigrationStats(admin_id=admin_id, admin_email=admin_email, dry_run=dry_run)
    candidates = await collect_legacy_thread_candidates(paths=paths, store=store)
    await attach_checkpoints(candidates, checkpointer)
    stats.candidates = len(candidates)
    for candidate in candidates.values():
        stats.source_counts.update(candidate.sources)

    async with sf() as session:
        for candidate in candidates.values():
            checkpoint_messages = _messages_from_checkpoint_tuple(candidate.checkpoint_tuple)
            jsonl_messages = _read_jsonl_messages(candidate.jsonl_path)
            await upsert_thread_meta(
                session,
                candidate,
                admin_id=admin_id,
                checkpoint_messages=checkpoint_messages,
                jsonl_messages=jsonl_messages,
                dry_run=dry_run,
                stats=stats,
            )
            run_id = await ensure_synthetic_run(
                session,
                candidate,
                admin_id=admin_id,
                checkpoint_messages=checkpoint_messages,
                jsonl_messages=jsonl_messages,
                dry_run=dry_run,
                stats=stats,
            )
            await rebuild_run_events(
                session,
                candidate,
                admin_id=admin_id,
                run_id=run_id,
                checkpoint_messages=checkpoint_messages,
                jsonl_messages=jsonl_messages,
                dry_run=dry_run,
                overwrite_events=overwrite_events,
                stats=stats,
            )
        await owner_backfill(session, admin_id=admin_id, dry_run=dry_run, stats=stats)
        if dry_run:
            await session.rollback()
        else:
            await session.commit()

    await migrate_store_owner(store, candidates, admin_id=admin_id, dry_run=dry_run, stats=stats)
    migrate_legacy_thread_dirs(paths, candidates, admin_id=admin_id, dry_run=dry_run, stats=stats)
    migrate_legacy_memory_and_agents(paths, admin_id=admin_id, dry_run=dry_run, stats=stats)
    return stats


def print_summary(stats: MigrationStats) -> None:
    print(f"admin: {stats.admin_email or ''} ({stats.admin_id})")
    print(f"dry_run: {stats.dry_run}")
    print(f"threads discovered: {stats.candidates}")
    print(f"sources: {dict(stats.source_counts)}")
    print(f"threads_meta created: {stats.threads_meta_created}")
    print(f"threads_meta owner updated: {stats.threads_meta_owner_updated}")
    print(f"store owner updated: {stats.store_owner_updated}")
    print(f"legacy thread dirs moved: {stats.legacy_dirs_moved}")
    print(f"memory migrated: {stats.memory_migrated}")
    print(f"agents migrated: {stats.agents_migrated}")
    print(f"runs created: {stats.runs_created}")
    print(f"run_events created: {stats.run_events_created}")
    print(f"run_event sources: {dict(stats.event_sources)}")
    print(f"owner backfill candidates: {dict(stats.owner_backfill)}")
    print(f"skipped: {dict(stats.skipped)}")
    if stats.conflicts:
        print("conflicts:")
        for item in stats.conflicts:
            print(f"  - {item}")
    if stats.warnings:
        print("warnings:")
        for item in stats.warnings:
            print(f"  - {item}")


async def _main_async() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy no-auth DeerFlow sessions to an admin user")
    parser.add_argument("--admin-id", default=None, help="Admin user id that should own legacy data")
    parser.add_argument("--admin-email", default=None, help="Admin email that should own legacy data")
    parser.add_argument("--dry-run", action="store_true", help="Inspect actions without writing data")
    parser.add_argument("--apply", action="store_true", help="Apply the migration")
    parser.add_argument("--overwrite-events", action="store_true", help="Rebuild synthetic legacy run_events if they already exist")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument(
        "--base-dir",
        default=None,
        help=("DeerFlow data directory containing legacy threads/, memory.json, and agents/. Defaults to DEER_FLOW_HOME or Paths' local .deer-flow."),
    )
    args = parser.parse_args()

    if args.dry_run == args.apply:
        raise SystemExit("Pass exactly one of --dry-run or --apply.")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    app_config = AppConfig.from_file(args.config)
    if app_config.database.backend == "memory":
        raise SystemExit("database.backend=memory has no SQL persistence to migrate.")

    from contextlib import AsyncExitStack

    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
    from deerflow.runtime.checkpointer import make_checkpointer
    from deerflow.runtime.store import make_store

    await init_engine_from_config(app_config.database)
    try:
        sf = get_session_factory()
        if sf is None:
            raise SystemExit("SQL session factory is not available.")
        admin_id, admin_email = await resolve_admin_user(sf, admin_id=args.admin_id, admin_email=args.admin_email)
        paths = Paths(args.base_dir) if args.base_dir else get_paths()
        async with AsyncExitStack() as stack:
            store = await stack.enter_async_context(make_store(app_config))
            checkpointer = await stack.enter_async_context(make_checkpointer(app_config))
            stats = await migrate_legacy_sessions(
                sf=sf,
                paths=paths,
                admin_id=admin_id,
                admin_email=admin_email,
                store=store,
                checkpointer=checkpointer,
                dry_run=args.dry_run,
                overwrite_events=args.overwrite_events,
            )
        print_summary(stats)
    finally:
        await close_engine()


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
