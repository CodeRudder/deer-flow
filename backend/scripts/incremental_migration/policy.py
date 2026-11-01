from __future__ import annotations

from enum import StrEnum


class TableRole(StrEnum):
    GLOBAL = "global"
    THREAD = "thread"
    BOOKKEEPING = "bookkeeping"


GLOBAL_TABLES = frozenset(
    {
        "users",
        "quota_scopes",
        "user_quota_usage_periods",
        "user_quota_periods",
        "channel_connections",
        "channel_credentials",
        "channel_oauth_states",
        "store",
    }
)

THREAD_TABLES = frozenset(
    {
        "threads_meta",
        "runs",
        "run_events",
        "feedback",
        "channel_conversations",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
    }
)

BOOKKEEPING_TABLES = frozenset(
    {
        "alembic_version",
        "checkpoint_migrations",
        "store_migrations",
        "vector_migrations",
    }
)

UNIMPLEMENTED_TABLES = frozenset({"store_vectors"})

MERGE_ORDER = (
    "users",
    "quota_scopes",
    "channel_connections",
    "threads_meta",
    "channel_conversations",
    "channel_credentials",
    "channel_oauth_states",
    "user_quota_usage_periods",
    "user_quota_periods",
    "runs",
    "run_events",
    "feedback",
    "store",
    "checkpoint_blobs",
    "checkpoints",
    "checkpoint_writes",
)


def classify_table_set(table_names: set[str]) -> dict[str, TableRole]:
    roles: dict[str, TableRole] = {}
    for name in sorted(table_names):
        if name in UNIMPLEMENTED_TABLES:
            raise ValueError(f"unsupported public table {name!r}: no reviewed merge spec is implemented")
        if name in GLOBAL_TABLES:
            roles[name] = TableRole.GLOBAL
        elif name in THREAD_TABLES:
            roles[name] = TableRole.THREAD
        elif name in BOOKKEEPING_TABLES:
            roles[name] = TableRole.BOOKKEEPING
        else:
            raise ValueError(f"unsupported public table {name!r}: classify it before migration")
    return roles


def ordered_business_tables(roles: dict[str, TableRole]) -> tuple[str, ...]:
    business = {name for name, role in roles.items() if role is not TableRole.BOOKKEEPING}
    ordered = [name for name in MERGE_ORDER if name in business]
    missing = business - set(ordered)
    if missing:
        raise ValueError(f"business tables have no merge order: {sorted(missing)}")
    return tuple(ordered)
