from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.incremental_migration.catalog import DatabaseCatalog
from scripts.incremental_migration.domain import FileEntry, MigrationWindow, StorageClass
from scripts.incremental_migration.files import discover_thread_roots, scan_thread_root
from scripts.incremental_migration.manifest import BaselineManifest, TableFingerprintManifest
from scripts.incremental_migration.policy import TableRole
from scripts.incremental_migration.postgres import fingerprint_table, read_database_catalog, validate_catalog, validate_catalog_compatibility
from scripts.incremental_migration.selection import changed_file_thread_ids, changed_source_thread_ids

_TIME_COLUMNS: dict[str, tuple[str, ...]] = {
    "threads_meta": ("created_at", "updated_at"),
    "runs": ("created_at", "updated_at"),
    "run_events": ("created_at",),
    "feedback": ("created_at",),
    "channel_conversations": ("created_at", "updated_at"),
}


def read_thread_owner_map(connection: Any, *, overrides: dict[str, str] | None = None) -> dict[str, str]:
    from psycopg.rows import dict_row

    owners: dict[str, str] = {}
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT thread_id, user_id FROM public.threads_meta WHERE user_id IS NOT NULL ORDER BY thread_id")
        for row in cursor.fetchall():
            owners[str(row["thread_id"])] = str(row["user_id"])

        cursor.execute(
            """
            SELECT key AS thread_id, value -> 'metadata' ->> 'user_id' AS user_id
            FROM public.store
            WHERE prefix = 'threads' AND value -> 'metadata' ->> 'user_id' IS NOT NULL
            ORDER BY key
            """
        )
        for row in cursor.fetchall():
            thread_id = str(row["thread_id"])
            user_id = str(row["user_id"])
            existing = owners.get(thread_id)
            if existing is not None and existing != user_id:
                raise ValueError(f"thread {thread_id!r} has conflicting owners in threads_meta and store")
            owners[thread_id] = user_id

    for thread_id, user_id in (overrides or {}).items():
        existing = owners.get(thread_id)
        if existing is not None and existing != user_id:
            raise ValueError(f"owner override for thread {thread_id!r} conflicts with PostgreSQL owner {existing!r}")
        owners[thread_id] = user_id
    return owners


def scan_home_files(
    *,
    home: Path,
    owner_by_thread: dict[str, str],
    require_all_owners: bool,
) -> tuple[FileEntry, ...]:
    entries: list[FileEntry] = []
    seen: set[str] = set()
    roots = discover_thread_roots(
        home,
        owner_by_thread=owner_by_thread,
        require_legacy_owner=require_all_owners,
    )
    for root in roots:
        if require_all_owners and root.thread_id not in owner_by_thread:
            raise ValueError(f"thread {root.thread_id!r} has files but no PostgreSQL or explicit owner mapping")
        for entry in scan_thread_root(
            home=home,
            root=root.root,
            user_id=root.user_id,
            thread_id=root.thread_id,
            layout=root.layout,
        ):
            if entry.relative_path in seen:
                raise ValueError(f"duplicate file path discovered: {entry.relative_path}")
            seen.add(entry.relative_path)
            entries.append(entry)
    return tuple(sorted(entries, key=lambda item: item.relative_path))


def create_baseline_manifest(
    *,
    connection: Any,
    home: Path,
    environment: str,
    owner_overrides: dict[str, str] | None = None,
    baseline_id: str | None = None,
    exclude_runtime_controls: bool = False,
) -> BaselineManifest:
    catalog = read_database_catalog(connection)
    roles = validate_catalog(catalog)
    owners = read_thread_owner_map(connection, overrides=owner_overrides)
    table_fingerprints: dict[str, TableFingerprintManifest] = {}
    for name, role in sorted(roles.items()):
        table = catalog.table_by_name[name]
        scope_column = "thread_id" if role is TableRole.THREAD else None
        table_fingerprints[name] = fingerprint_table(
            connection,
            table,
            scope_column=scope_column,
            allow_unkeyed=role is TableRole.BOOKKEEPING,
        )
    files = scan_home_files(home=home, owner_by_thread=owners, require_all_owners=True)
    if exclude_runtime_controls:
        files = tuple(entry for entry in files if entry.storage_class is not StorageClass.RUNTIME_ARCHIVE)
    return BaselineManifest.create(
        environment=environment,
        catalog=catalog,
        tables=table_fingerprints,
        files=files,
        baseline_id=baseline_id,
    )


def compare_baseline_snapshots(*, expected: BaselineManifest, current: BaselineManifest) -> tuple[str, ...]:
    differences: list[str] = []
    if expected.catalog.postgres_version_num // 10000 != current.catalog.postgres_version_num // 10000:
        differences.append("PostgreSQL major version changed")
    if expected.catalog.structural_fingerprint != current.catalog.structural_fingerprint:
        differences.append("public schema structure changed")
    if expected.catalog.security_fingerprint != current.catalog.security_fingerprint:
        differences.append("public schema owner or grants changed")

    expected_tables = expected.table_by_name
    current_tables = current.table_by_name
    if set(expected_tables) != set(current_tables):
        differences.append("fingerprinted table set changed")
    for table_name in sorted(set(expected_tables) & set(current_tables)):
        before = expected_tables[table_name]
        after = current_tables[table_name]
        if before.primary_key != after.primary_key or before.row_count != after.row_count or before.payload_hash != after.payload_hash:
            differences.append(f"table data changed: {table_name}")

    def file_signature(entry: FileEntry) -> tuple[object, ...]:
        return (
            entry.user_id,
            entry.thread_id,
            entry.layout,
            entry.size,
            entry.mtime_ns,
            entry.mode,
            entry.sha256,
            entry.storage_class.value,
        )

    expected_files = {entry.relative_path: file_signature(entry) for entry in expected.files}
    current_files = {entry.relative_path: file_signature(entry) for entry in current.files}
    if expected_files != current_files:
        changed_paths = sorted(path for path in set(expected_files) | set(current_files) if expected_files.get(path) != current_files.get(path))
        preview = ", ".join(changed_paths[:10])
        suffix = " ..." if len(changed_paths) > 10 else ""
        differences.append(f"thread files changed: {preview}{suffix}")
    return tuple(differences)


def assert_baseline_unchanged(*, expected: BaselineManifest, current: BaselineManifest) -> None:
    differences = compare_baseline_snapshots(expected=expected, current=current)
    if differences:
        raise ValueError("target no longer matches the frozen baseline: " + "; ".join(differences))


def discover_activity_thread_ids(
    connection: Any,
    *,
    catalog: DatabaseCatalog,
    window: MigrationWindow,
) -> set[str]:
    from psycopg import sql
    from psycopg.rows import dict_row

    selected: set[str] = set()
    table_by_name = catalog.table_by_name
    with connection.cursor(row_factory=dict_row) as cursor:
        for table_name, candidate_columns in _TIME_COLUMNS.items():
            table = table_by_name.get(table_name)
            if table is None:
                continue
            available = {column.name for column in table.columns}
            columns = [name for name in candidate_columns if name in available]
            if not columns:
                continue
            predicates = sql.SQL(" OR ").join(sql.SQL("({column} >= %s AND {column} < %s)").format(column=sql.Identifier(column)) for column in columns)
            query = sql.SQL("SELECT DISTINCT thread_id FROM public.{table} WHERE ").format(table=sql.Identifier(table_name)) + predicates
            params: list[datetime] = []
            for _ in columns:
                params.extend((window.start_utc, window.end_utc))
            cursor.execute(query, params)
            selected.update(str(row["thread_id"]) for row in cursor.fetchall())

        if "checkpoints" in table_by_name:
            cursor.execute("SELECT thread_id, checkpoint ->> 'ts' AS checkpoint_ts FROM public.checkpoints ORDER BY thread_id")
            for row in cursor:
                raw = row.get("checkpoint_ts")
                if not raw:
                    continue
                try:
                    value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if value.tzinfo is not None and window.contains(value):
                    selected.add(str(row["thread_id"]))
    return selected


@dataclass(frozen=True)
class SourcePlan:
    catalog: DatabaseCatalog
    selected_thread_ids: tuple[str, ...]
    owner_by_thread: dict[str, str]
    table_fingerprints: tuple[TableFingerprintManifest, ...]
    all_files: tuple[FileEntry, ...]
    selected_files: tuple[FileEntry, ...]
    activity_thread_ids: tuple[str, ...]
    changed_database_thread_ids: tuple[str, ...]
    changed_file_thread_ids: tuple[str, ...]

    @property
    def table_fingerprint_by_name(self) -> dict[str, TableFingerprintManifest]:
        return {table.table_name: table for table in self.table_fingerprints}


def build_source_plan(
    *,
    connection: Any,
    home: Path,
    baseline: BaselineManifest,
    window: MigrationWindow,
    owner_overrides: dict[str, str] | None = None,
) -> SourcePlan:
    catalog = read_database_catalog(connection)
    validate_catalog_compatibility(catalog, baseline.catalog)
    roles = validate_catalog(catalog)
    owners = read_thread_owner_map(connection, overrides=owner_overrides)
    fingerprints: dict[str, TableFingerprintManifest] = {}
    changed_database: set[str] = set()
    baseline_tables = baseline.table_by_name
    for name, role in sorted(roles.items()):
        table = catalog.table_by_name[name]
        scope_column = "thread_id" if role is TableRole.THREAD else None
        fingerprint = fingerprint_table(
            connection,
            table,
            scope_column=scope_column,
            allow_unkeyed=role is TableRole.BOOKKEEPING,
        )
        fingerprints[name] = fingerprint
        target_fingerprint = baseline_tables.get(name)
        if target_fingerprint is None:
            raise ValueError(f"baseline manifest is missing table {name!r}")
        if role is TableRole.BOOKKEEPING:
            if fingerprint.primary_key != target_fingerprint.primary_key or fingerprint.row_count != target_fingerprint.row_count or fingerprint.payload_hash != target_fingerprint.payload_hash:
                raise ValueError(f"schema bookkeeping data differs between source and target baseline: {name}")
        elif role is TableRole.THREAD:
            changed_database.update(changed_source_thread_ids(source=fingerprint, baseline=target_fingerprint))

    all_files = scan_home_files(home=home, owner_by_thread=owners, require_all_owners=True)
    changed_files = changed_file_thread_ids(source=all_files, baseline=baseline.files, window=window)
    activity = discover_activity_thread_ids(connection, catalog=catalog, window=window)
    selected = activity | changed_database | changed_files
    missing_owner = sorted(thread_id for thread_id in selected if not owners.get(thread_id))
    if missing_owner:
        raise ValueError(f"selected threads have no owner mapping: {missing_owner}")
    selected_files = tuple(entry for entry in all_files if entry.thread_id in selected)
    return SourcePlan(
        catalog=catalog,
        selected_thread_ids=tuple(sorted(selected)),
        owner_by_thread=owners,
        table_fingerprints=tuple(sorted(fingerprints.values(), key=lambda item: item.table_name)),
        all_files=all_files,
        selected_files=selected_files,
        activity_thread_ids=tuple(sorted(activity)),
        changed_database_thread_ids=tuple(sorted(changed_database)),
        changed_file_thread_ids=tuple(sorted(changed_files)),
    )
