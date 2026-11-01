from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.incremental_migration.catalog import ColumnCatalog, DatabaseCatalog, TableCatalog, UniqueIndexCatalog
from scripts.incremental_migration.database import staging_schema_name
from scripts.incremental_migration.domain import (
    FileEntry,
    MigrationPhase,
    MigrationWindow,
    StorageClass,
    canonical_hash,
)
from scripts.incremental_migration.files import (
    FileMergeJournal,
    build_file_archives,
    classify_storage,
    discover_thread_roots,
    merge_active_files,
    quarantine_runtime_controls,
    rollback_files,
    safe_extract_tar,
    scan_thread_root,
)
from scripts.incremental_migration.manifest import (
    BaselineManifest,
    ExportManifest,
    RowFingerprint,
    TableExportManifest,
    TableFingerprintManifest,
)
from scripts.incremental_migration.operations import assert_baseline_unchanged, compare_baseline_snapshots, create_baseline_manifest
from scripts.incremental_migration.package import (
    build_package_archive,
    extract_package_archive,
    finalize_package_directory,
    package_content_sha256,
    verify_package_directory,
)
from scripts.incremental_migration.policy import TableRole, classify_table_set
from scripts.incremental_migration.postgres import (
    build_catalog_from_records,
    fingerprint_records,
    validate_catalog,
    validate_catalog_compatibility,
)
from scripts.incremental_migration.selection import changed_file_thread_ids, changed_source_thread_ids
from scripts.incremental_migration.service import (
    _assert_import_apply_phase,
    _assert_package_matches_state,
    _assert_target_home_matches_state,
    _extract_staged_files,
)
from scripts.incremental_migration.sql import (
    BusinessKeySpec,
    ColumnSpec,
    TableSpec,
    build_business_conflict_statements,
    build_merge_statements,
)
from scripts.incremental_migration.state import MigrationState, MigrationStateStore


def test_window_uses_half_open_utc_boundaries() -> None:
    window = MigrationWindow.parse(
        "2026-07-13T00:00:00+08:00",
        "2026-07-20T00:00:00+08:00",
    )

    assert window.start_utc == datetime(2026, 7, 12, 16, tzinfo=UTC)
    assert window.end_utc == datetime(2026, 7, 19, 16, tzinfo=UTC)
    assert window.contains(window.start_utc)
    assert not window.contains(window.end_utc)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-07-13T00:00:00", "2026-07-20T00:00:00+08:00"),
        ("2026-07-20T00:00:00+08:00", "2026-07-13T00:00:00+08:00"),
    ],
)
def test_window_rejects_naive_or_reversed_values(start: str, end: str) -> None:
    with pytest.raises(ValueError):
        MigrationWindow.parse(start, end)


def test_canonical_hash_is_stable_for_json_and_sensitive_to_bytes() -> None:
    left = {"payload": {"b": 2, "a": 1}, "blob": b"abc"}
    right = {"blob": memoryview(b"abc"), "payload": {"a": 1, "b": 2}}

    assert canonical_hash(left) == canonical_hash(right)
    assert canonical_hash(left) != canonical_hash({**left, "blob": b"abd"})


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("background_commands.json", StorageClass.RUNTIME_ARCHIVE),
        ("background_commands.json.a1b2c3.tmp", StorageClass.RUNTIME_ARCHIVE),
        ("subagents/task-1.cancel", StorageClass.RUNTIME_ARCHIVE),
        ("sandbox.lock", StorageClass.RUNTIME_ARCHIVE),
        ("user-data/workspace/poetry.lock", StorageClass.ACTIVE),
        ("user-data/workspace/task.cancel", StorageClass.ACTIVE),
        ("nested/background_commands.json", StorageClass.ACTIVE),
        ("user-data/workspace/model.tmp", StorageClass.ACTIVE),
        ("cmd_logs/cmd-1.log", StorageClass.ACTIVE),
    ],
)
def test_runtime_file_classification_is_narrow(path: str, expected: StorageClass) -> None:
    assert classify_storage(Path(path)) is expected


def test_scan_thread_root_hashes_regular_files_and_rejects_symlinks(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / "users" / "user-1" / "threads" / "thread-1"
    workspace = root / "user-data" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "note.txt").write_text("hello", encoding="utf-8")
    (root / "subagents").mkdir()
    (root / "subagents" / "task.cancel").write_text("cancelled", encoding="utf-8")

    entries = scan_thread_root(
        home=home,
        root=root,
        user_id="user-1",
        thread_id="thread-1",
        layout="user",
    )

    assert [entry.relative_path for entry in entries] == [
        "users/user-1/threads/thread-1/subagents/task.cancel",
        "users/user-1/threads/thread-1/user-data/workspace/note.txt",
    ]
    assert entries[0].storage_class is StorageClass.RUNTIME_ARCHIVE
    assert entries[1].storage_class is StorageClass.ACTIVE
    assert len(entries[1].sha256) == 64

    (workspace / "unsafe").symlink_to(workspace / "note.txt")
    with pytest.raises(ValueError, match="symlink"):
        scan_thread_root(
            home=home,
            root=root,
            user_id="user-1",
            thread_id="thread-1",
            layout="user",
        )


def test_file_archives_are_mutually_exclusive(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / "users" / "u" / "threads" / "t"
    root.mkdir(parents=True)
    active = root / "conversation.jsonl"
    runtime = root / "background_commands.json"
    active.write_text("{}\n", encoding="utf-8")
    runtime.write_text('{"pid":123}', encoding="utf-8")
    entries = scan_thread_root(home=home, root=root, user_id="u", thread_id="t", layout="user")

    active_tar = tmp_path / "threads.tar.gz"
    runtime_tar = tmp_path / "runtime.tar.gz"
    build_file_archives(home=home, entries=entries, active_tar=active_tar, runtime_tar=runtime_tar)

    with tarfile.open(active_tar, "r:gz") as archive:
        assert archive.getnames() == ["users/u/threads/t/conversation.jsonl"]
    with tarfile.open(runtime_tar, "r:gz") as archive:
        assert archive.getnames() == ["users/u/threads/t/background_commands.json"]


def test_safe_extract_rejects_traversal_and_links(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("../escape.txt")
        payload = b"escape"
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    with pytest.raises(ValueError, match="unsafe archive path"):
        safe_extract_tar(archive_path, tmp_path / "extract")


def test_file_merge_is_source_wins_idempotent_and_rollbackable(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    relative = Path("users/u/threads/t/user-data/workspace/data.txt")
    source_file = staging / relative
    target_file = target / relative
    source_file.parent.mkdir(parents=True)
    target_file.parent.mkdir(parents=True)
    source_file.write_text("source", encoding="utf-8")
    target_file.write_text("target", encoding="utf-8")
    target_only = target / "users/u/threads/t/target-only.txt"
    target_only.write_text("keep", encoding="utf-8")

    stat = source_file.stat()
    entry = FileEntry.from_path(
        home=staging,
        path=source_file,
        user_id="u",
        thread_id="t",
        layout="user",
        storage_class=StorageClass.ACTIVE,
        stat=stat,
    )
    journal = FileMergeJournal(export_id="exp-1")

    first = merge_active_files(
        staging_home=staging,
        target_home=target,
        entries=[entry],
        backup_home=backup,
        journal=journal,
    )
    second = merge_active_files(
        staging_home=staging,
        target_home=target,
        entries=[entry],
        backup_home=backup,
        journal=journal,
    )

    assert first.replaced == 1
    assert second.unchanged == 1
    assert target_file.read_text(encoding="utf-8") == "source"
    assert target_only.read_text(encoding="utf-8") == "keep"

    rollback_files(target_home=target, backup_home=backup, journal=journal)
    assert target_file.read_text(encoding="utf-8") == "target"
    assert target_only.read_text(encoding="utf-8") == "keep"


def test_file_merge_updates_mtime_when_content_and_mode_match(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    target = tmp_path / "target"
    relative = Path("users/u/threads/t/data.txt")
    source = staging / relative
    destination = target / relative
    source.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    source.write_text("same", encoding="utf-8")
    destination.write_text("same", encoding="utf-8")
    source_mtime = 1_700_000_000_123_456_789
    target_mtime = source_mtime - 1_000_000_000
    os.utime(source, ns=(source_mtime, source_mtime))
    os.utime(destination, ns=(target_mtime, target_mtime))
    journal = FileMergeJournal(export_id="exp")
    entry = FileEntry.from_path(
        home=staging,
        path=source,
        user_id="u",
        thread_id="t",
        layout="user",
        storage_class=StorageClass.ACTIVE,
    )

    stats = merge_active_files(
        staging_home=staging,
        target_home=target,
        entries=[entry],
        backup_home=tmp_path / "backup",
        journal=journal,
    )

    assert stats.replaced == 1
    assert destination.stat().st_mtime_ns == source_mtime
    rollback_files(target_home=target, backup_home=tmp_path / "backup", journal=journal)
    assert destination.stat().st_mtime_ns == target_mtime


def test_state_store_persists_valid_transitions_and_rejects_skips(tmp_path: Path) -> None:
    store = MigrationStateStore(tmp_path / "state.json")
    state = store.create("exp-1")
    assert state.phase is MigrationPhase.TARGET_PREFLIGHT

    state = store.transition(MigrationPhase.BASELINE_FROZEN)
    reloaded = MigrationStateStore(tmp_path / "state.json").load()
    assert reloaded.phase is MigrationPhase.BASELINE_FROZEN

    with pytest.raises(ValueError, match="invalid migration state transition"):
        store.transition(MigrationPhase.DB_COMMITTED)


def test_target_home_binding_is_fail_closed(tmp_path: Path) -> None:
    state = MigrationState(
        export_id="exp",
        phase=MigrationPhase.BASELINE_FROZEN,
        created_at="now",
        updated_at="now",
        details={"target_home": str(tmp_path / "expected")},
    )

    _assert_target_home_matches_state(state=state, target_home=tmp_path / "expected")
    with pytest.raises(ValueError, match="differs from the frozen baseline"):
        _assert_target_home_matches_state(state=state, target_home=tmp_path / "other")
    state.details.clear()
    with pytest.raises(ValueError, match="does not record"):
        _assert_target_home_matches_state(state=state, target_home=tmp_path / "expected")


@pytest.mark.parametrize(
    "phase",
    [MigrationPhase.VERIFIED, MigrationPhase.ROLLED_BACK, MigrationPhase.ROLLBACK_FAILED],
)
def test_import_apply_rejects_terminal_or_failed_rollback_phases(phase: MigrationPhase) -> None:
    state = MigrationState(
        export_id="exp",
        phase=phase,
        created_at="now",
        updated_at="now",
    )

    with pytest.raises(ValueError, match="import apply is not valid"):
        _assert_import_apply_phase(state)


def test_sql_merge_is_type_aware_and_never_uses_row_comparison() -> None:
    table = TableSpec(
        name="runs",
        columns=(
            ColumnSpec("run_id", "character varying(64)", nullable=False),
            ColumnSpec("metadata_json", "json", nullable=False),
            ColumnSpec("payload", "bytea", nullable=True),
            ColumnSpec("updated_at", "timestamp with time zone", nullable=False),
        ),
        primary_key=("run_id",),
    )

    statements = build_merge_statements(table, staging_schema="migration_exp")

    assert "ROW(" not in statements.update
    assert 't."metadata_json"::jsonb IS DISTINCT FROM s."metadata_json"::jsonb' in statements.update
    assert 't."payload" IS DISTINCT FROM s."payload"' in statements.update
    assert "ON CONFLICT" not in statements.insert
    assert "NOT EXISTS" in statements.insert

    xml = ColumnSpec("payload", "xml", nullable=True)
    assert xml.difference_expression() == 't."payload"::text IS DISTINCT FROM s."payload"::text'


def test_sql_merge_quotes_catalog_identifiers() -> None:
    table = TableSpec(
        name='odd"table',
        columns=(ColumnSpec('odd"id', "text", nullable=False),),
        primary_key=('odd"id',),
    )

    statements = build_merge_statements(table, staging_schema='odd"schema')

    assert '"odd""schema"."odd""table"' in statements.insert
    assert '"odd""id"' in statements.insert


def test_business_conflict_sql_handles_partial_and_nullable_unique_keys() -> None:
    table = TableSpec(
        name="channel_connections",
        columns=(
            ColumnSpec("id", "text", nullable=False),
            ColumnSpec("provider", "text", nullable=False),
            ColumnSpec("external_account_id", "text", nullable=True),
            ColumnSpec("status", "text", nullable=False),
        ),
        primary_key=("id",),
        business_keys=(
            BusinessKeySpec(
                columns=("provider", "external_account_id"),
                predicate="status <> 'revoked'",
            ),
        ),
    )

    statements = build_business_conflict_statements(table, staging_schema="migration")

    assert len(statements) == 2
    assert "status <> 'revoked'" in statements[0].sql
    assert '"external_account_id" IS NOT NULL' in statements[0].sql
    assert 's."provider" = t."provider"' in statements[1].sql
    assert 's."id" IS DISTINCT FROM t."id"' in statements[1].sql


def test_file_entry_json_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "users" / "u" / "threads" / "t" / "a.txt"
    path.parent.mkdir(parents=True)
    path.write_text("a", encoding="utf-8")
    entry = FileEntry.from_path(
        home=tmp_path,
        path=path,
        user_id="u",
        thread_id="t",
        layout="user",
        storage_class=StorageClass.ACTIVE,
    )

    encoded = json.loads(json.dumps(entry.to_dict()))
    assert FileEntry.from_dict(encoded) == entry


def test_table_policy_recognizes_business_and_bookkeeping_tables() -> None:
    roles = classify_table_set(
        {
            "users",
            "threads_meta",
            "checkpoints",
            "checkpoint_blobs",
            "checkpoint_writes",
            "alembic_version",
            "checkpoint_migrations",
            "store_migrations",
        }
    )

    assert roles["users"] is TableRole.GLOBAL
    assert roles["threads_meta"] is TableRole.THREAD
    assert roles["checkpoint_migrations"] is TableRole.BOOKKEEPING


@pytest.mark.parametrize("table_name", ["unknown_business_table", "store_vectors"])
def test_table_policy_blocks_unknown_or_unimplemented_tables(table_name: str) -> None:
    with pytest.raises(ValueError, match="unsupported public table"):
        classify_table_set({table_name})


def _catalog_with_columns(columns: tuple[ColumnCatalog, ...]) -> DatabaseCatalog:
    table = TableCatalog(
        name="example",
        owner="owner",
        row_security=False,
        force_row_security=False,
        columns=columns,
        primary_key=("id",),
        unique_indexes=(UniqueIndexCatalog(name="example_pkey", columns=("id",), primary=True),),
    )
    return DatabaseCatalog(postgres_version_num=160000, tables=(table,))


def test_schema_fingerprint_is_stable_for_catalog_order() -> None:
    id_column = ColumnCatalog(name="id", type_name="text", nullable=False, ordinal=1)
    json_column = ColumnCatalog(name="payload", type_name="json", nullable=True, ordinal=2)
    first = _catalog_with_columns((id_column, json_column))
    second = _catalog_with_columns((json_column, id_column))

    assert first.structural_fingerprint == second.structural_fingerprint


def test_changed_source_threads_ignores_target_only_and_finds_hidden_write() -> None:
    baseline = TableFingerprintManifest(
        table_name="checkpoint_writes",
        primary_key=("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"),
        row_count=2,
        payload_hash="baseline",
        rows=(
            RowFingerprint(key=("unchanged", "", "cp", "task", 0), row_hash="same"),
            RowFingerprint(key=("target-only", "", "cp", "task", 0), row_hash="target"),
        ),
    )
    source = TableFingerprintManifest(
        table_name="checkpoint_writes",
        primary_key=baseline.primary_key,
        row_count=2,
        payload_hash="source",
        rows=(
            RowFingerprint(key=("unchanged", "", "cp", "task", 0), row_hash="same"),
            RowFingerprint(key=("hidden-write", "", "old-cp", "task", 1), row_hash="new"),
        ),
    )

    assert changed_source_thread_ids(source=source, baseline=baseline) == {"hidden-write"}


def test_baseline_manifest_roundtrip(tmp_path: Path) -> None:
    catalog = _catalog_with_columns((ColumnCatalog(name="id", type_name="text", nullable=False, ordinal=1),))
    baseline = BaselineManifest.create(
        environment="prod",
        catalog=catalog,
        tables={
            "example": TableFingerprintManifest(
                table_name="example",
                primary_key=("id",),
                row_count=1,
                payload_hash="payload",
                rows=(RowFingerprint(key=("1",), row_hash="row"),),
            )
        },
        files=(),
    )
    path = tmp_path / "baseline.json"

    baseline.write(path)

    assert BaselineManifest.read(path) == baseline
    assert path.stat().st_mode & 0o777 == 0o600


def test_catalog_builder_captures_types_predicates_and_security() -> None:
    catalog = build_catalog_from_records(
        postgres_version_num=160002,
        table_records=[
            {
                "table_name": "users",
                "owner": "app",
                "row_security": True,
                "force_row_security": False,
            }
        ],
        column_records=[
            {
                "table_name": "users",
                "ordinal": 1,
                "column_name": "id",
                "type_name": "character varying(36)",
                "not_null": True,
                "default_expression": None,
                "identity": "",
                "generated": "",
                "collation": None,
            },
            {
                "table_name": "users",
                "ordinal": 2,
                "column_name": "metadata",
                "type_name": "json",
                "not_null": False,
                "default_expression": "'{}'::json",
                "identity": "",
                "generated": "",
                "collation": None,
            },
        ],
        unique_index_records=[
            {
                "table_name": "users",
                "index_name": "users_pkey",
                "primary": True,
                "nulls_not_distinct": False,
                "predicate": None,
                "definition": "CREATE UNIQUE INDEX users_pkey ON users (id)",
                "columns": ["id"],
                "has_expression": False,
            },
            {
                "table_name": "users",
                "index_name": "users_active_key",
                "primary": False,
                "nulls_not_distinct": False,
                "predicate": "status <> 'revoked'",
                "definition": "CREATE UNIQUE INDEX users_active_key ON users (id) WHERE status <> 'revoked'",
                "columns": ["id"],
                "has_expression": False,
            },
        ],
        constraint_records=[{"table_name": "users", "constraint_type": "p", "definition": "PRIMARY KEY (id)"}],
        index_records=[{"table_name": "users", "definition": "CREATE UNIQUE INDEX users_pkey ON users (id)"}],
        trigger_records=[{"table_name": "users", "definition": "trigger:def"}],
        grant_records=[{"table_name": "users", "grant": "app:SELECT:YES"}],
    )

    users = catalog.table_by_name["users"]
    assert users.primary_key == ("id",)
    assert users.columns[1].type_name == "json"
    assert {index.name: index for index in users.unique_indexes}["users_active_key"].predicate == "status <> 'revoked'"
    assert users.row_security is True
    assert users.grants == ("app:SELECT:YES",)


def test_catalog_builder_rejects_expression_unique_indexes() -> None:
    with pytest.raises(ValueError, match="expression-based unique index"):
        build_catalog_from_records(
            postgres_version_num=160000,
            table_records=[{"table_name": "users", "owner": "app", "row_security": False, "force_row_security": False}],
            column_records=[
                {
                    "table_name": "users",
                    "ordinal": 1,
                    "column_name": "id",
                    "type_name": "text",
                    "not_null": True,
                    "default_expression": None,
                    "identity": "",
                    "generated": "",
                    "collation": None,
                }
            ],
            unique_index_records=[
                {
                    "table_name": "users",
                    "index_name": "users_expr_key",
                    "primary": False,
                    "nulls_not_distinct": False,
                    "predicate": None,
                    "definition": "CREATE UNIQUE INDEX users_expr_key ON users ((lower(id)))",
                    "columns": [],
                    "has_expression": True,
                }
            ],
            constraint_records=[],
            index_records=[],
            trigger_records=[],
            grant_records=[],
        )


def test_fingerprint_records_orders_by_primary_key_and_hashes_json_canonically() -> None:
    table = TableCatalog(
        name="example",
        owner="app",
        row_security=False,
        force_row_security=False,
        columns=(
            ColumnCatalog(name="id", type_name="text", nullable=False, ordinal=1),
            ColumnCatalog(name="payload", type_name="json", nullable=False, ordinal=2),
        ),
        primary_key=("id",),
    )
    records = [
        {"id": "2", "payload": {"b": 2, "a": 1}},
        {"payload": {"a": 1, "b": 2}, "id": "1"},
    ]

    result = fingerprint_records(table, records)

    assert [row.key for row in result.rows] == [("1",), ("2",)]
    assert result.rows[0].row_hash != ""
    assert result.rows[0].row_hash != result.rows[1].row_hash  # id is part of the row hash


def test_fingerprint_records_stably_hashes_unkeyed_bookkeeping_multiset() -> None:
    table = TableCatalog(
        name="alembic_version",
        owner="app",
        row_security=False,
        force_row_security=False,
        columns=(ColumnCatalog(name="version_num", type_name="text", nullable=False, ordinal=1),),
        primary_key=(),
    )
    records = [
        {"version_num": "revision-b"},
        {"version_num": "revision-a"},
        {"version_num": "revision-a"},
    ]

    first = fingerprint_records(table, records, allow_unkeyed=True)
    second = fingerprint_records(table, reversed(records), allow_unkeyed=True)
    changed = fingerprint_records(table, [{"version_num": "revision-a"}], allow_unkeyed=True)

    assert first == second
    assert first.primary_key == ()
    assert first.row_count == 3
    assert len({row.key_token for row in first.rows}) == 3
    assert first.payload_hash != changed.payload_hash
    with pytest.raises(ValueError, match="without a primary key"):
        fingerprint_records(table, records)


def test_catalog_compatibility_checks_major_version_and_structure() -> None:
    source = _catalog_with_columns((ColumnCatalog(name="id", type_name="text", nullable=False, ordinal=1),))
    target = DatabaseCatalog(postgres_version_num=170000, tables=source.tables)
    with pytest.raises(ValueError, match="PostgreSQL major version"):
        validate_catalog_compatibility(source, target)

    changed = _catalog_with_columns((ColumnCatalog(name="id", type_name="bigint", nullable=False, ordinal=1),))
    with pytest.raises(ValueError, match="schema structural fingerprint"):
        validate_catalog_compatibility(source, changed)


def test_catalog_validation_rejects_generated_business_columns() -> None:
    table = TableCatalog(
        name="users",
        owner="app",
        row_security=False,
        force_row_security=False,
        columns=(
            ColumnCatalog(name="id", type_name="text", nullable=False, ordinal=1),
            ColumnCatalog(name="normalized", type_name="text", nullable=False, ordinal=2, generated="s"),
        ),
        primary_key=("id",),
    )

    with pytest.raises(ValueError, match="unsupported generated columns"):
        validate_catalog(DatabaseCatalog(postgres_version_num=160000, tables=(table,)))


def test_discovers_user_and_legacy_thread_roots(tmp_path: Path) -> None:
    user_root = tmp_path / "users" / "user-1" / "threads" / "thread-user"
    legacy_root = tmp_path / "threads" / "thread-legacy"
    user_root.mkdir(parents=True)
    legacy_root.mkdir(parents=True)

    roots = discover_thread_roots(
        tmp_path,
        owner_by_thread={"thread-user": "user-1", "thread-legacy": "user-1"},
        require_legacy_owner=True,
    )

    assert [(root.layout, root.thread_id, root.user_id) for root in roots] == [
        ("legacy", "thread-legacy", "user-1"),
        ("user", "thread-user", "user-1"),
    ]


def test_legacy_thread_without_owner_is_blocked(tmp_path: Path) -> None:
    (tmp_path / "threads" / "thread-legacy").mkdir(parents=True)

    with pytest.raises(ValueError, match="has no owner mapping"):
        discover_thread_roots(tmp_path, owner_by_thread={}, require_legacy_owner=True)


def test_file_selection_uses_hash_diff_and_ignores_target_only(tmp_path: Path) -> None:
    source_home = tmp_path / "source"
    source_root = source_home / "users" / "u" / "threads" / "changed"
    source_root.mkdir(parents=True)
    source_file = source_root / "same-mtime.txt"
    source_file.write_text("new", encoding="utf-8")
    old_ns = int(datetime(2026, 7, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    source_file.touch()
    source_file_stat = source_file.stat()
    source_entry = FileEntry.from_path(
        home=source_home,
        path=source_file,
        user_id="u",
        thread_id="changed",
        layout="user",
        storage_class=StorageClass.ACTIVE,
        stat=source_file_stat,
    )
    source_entry = FileEntry(**{**source_entry.to_dict(), "mtime_ns": old_ns, "storage_class": StorageClass.ACTIVE})
    baseline_entry = FileEntry(
        **{
            **source_entry.to_dict(),
            "sha256": "0" * 64,
            "storage_class": StorageClass.ACTIVE,
        }
    )
    target_only = FileEntry(
        relative_path="users/u/threads/target-only/file.txt",
        user_id="u",
        thread_id="target-only",
        layout="user",
        size=1,
        mtime_ns=old_ns,
        mode=0o600,
        sha256="1" * 64,
        storage_class=StorageClass.ACTIVE,
    )
    window = MigrationWindow.parse("2026-07-13T00:00:00+08:00", "2026-07-20T00:00:00+08:00")

    assert changed_file_thread_ids(source=(source_entry,), baseline=(baseline_entry, target_only), window=window) == {"changed"}


def test_runtime_preflight_blocks_running_commands(tmp_path: Path) -> None:
    home = tmp_path / "home"
    command_file = home / "threads" / "t" / "background_commands.json"
    command_file.parent.mkdir(parents=True)
    command_file.write_text('{"commands":[{"status":"running","pid":123}]}', encoding="utf-8")
    entries = scan_thread_root(home=home, root=command_file.parent, user_id="u", thread_id="t", layout="legacy")

    with pytest.raises(ValueError, match="running background command"):
        quarantine_runtime_controls(
            home=home,
            quarantine_home=tmp_path / "quarantine",
            entries=entries,
            journal=FileMergeJournal(export_id="exp"),
            apply=True,
        )


def test_runtime_preflight_blocks_unknown_command_status(tmp_path: Path) -> None:
    home = tmp_path / "home"
    command_file = home / "threads" / "t" / "background_commands.json"
    command_file.parent.mkdir(parents=True)
    command_file.write_text('{"commands":[{"status":"starting","pid":123}]}', encoding="utf-8")
    entries = scan_thread_root(home=home, root=command_file.parent, user_id="u", thread_id="t", layout="legacy")

    with pytest.raises(ValueError, match="unknown status"):
        quarantine_runtime_controls(
            home=home,
            quarantine_home=tmp_path / "quarantine",
            entries=entries,
            journal=FileMergeJournal(export_id="exp"),
            apply=True,
        )


def test_runtime_preflight_quarantines_and_rolls_back(tmp_path: Path) -> None:
    home = tmp_path / "home"
    marker = home / "threads" / "t" / "subagents" / "task.cancel"
    marker.parent.mkdir(parents=True)
    marker.write_text("cancelled", encoding="utf-8")
    entries = scan_thread_root(home=home, root=home / "threads" / "t", user_id="u", thread_id="t", layout="legacy")
    quarantine = tmp_path / "quarantine"
    journal = FileMergeJournal(export_id="exp")

    stats = quarantine_runtime_controls(
        home=home,
        quarantine_home=quarantine,
        entries=entries,
        journal=journal,
        apply=True,
    )

    assert stats.archived == 1
    assert not marker.exists()
    assert (quarantine / "threads/t/subagents/task.cancel").read_text(encoding="utf-8") == "cancelled"

    rollback_files(target_home=home, backup_home=quarantine, journal=journal)
    assert marker.read_text(encoding="utf-8") == "cancelled"


def test_baseline_fingerprints_business_and_bookkeeping_tables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    users = TableCatalog(
        name="users",
        owner="app",
        row_security=False,
        force_row_security=False,
        columns=(ColumnCatalog(name="id", type_name="text", nullable=False, ordinal=1),),
        primary_key=("id",),
    )
    alembic = TableCatalog(
        name="alembic_version",
        owner="app",
        row_security=False,
        force_row_security=False,
        columns=(ColumnCatalog(name="version_num", type_name="text", nullable=False, ordinal=1),),
        primary_key=(),
    )
    catalog = DatabaseCatalog(postgres_version_num=160000, tables=(users, alembic))
    monkeypatch.setattr("scripts.incremental_migration.operations.read_database_catalog", lambda connection: catalog)
    monkeypatch.setattr("scripts.incremental_migration.operations.read_thread_owner_map", lambda connection, overrides=None: {})
    fingerprint_calls: list[tuple[str, bool]] = []

    def fake_fingerprint(connection, table, *, scope_column=None, allow_unkeyed=False):
        fingerprint_calls.append((table.name, allow_unkeyed))
        row_hash = f"row-{table.name}"
        key = (row_hash, 0) if allow_unkeyed else ("1",)
        return TableFingerprintManifest(
            table_name=table.name,
            primary_key=table.primary_key,
            row_count=1,
            payload_hash=f"hash-{table.name}",
            rows=(RowFingerprint(key=key, row_hash=row_hash),),
        )

    monkeypatch.setattr(
        "scripts.incremental_migration.operations.fingerprint_table",
        fake_fingerprint,
    )

    baseline = create_baseline_manifest(connection=object(), home=tmp_path, environment="prod")

    assert set(baseline.table_by_name) == {"users", "alembic_version"}
    assert baseline.catalog == catalog
    assert fingerprint_calls == [("alembic_version", True), ("users", False)]


def test_scoped_table_fingerprint_preserves_order_and_rehashes() -> None:
    fingerprint = TableFingerprintManifest(
        table_name="runs",
        primary_key=("run_id",),
        row_count=3,
        payload_hash="full",
        rows=(
            RowFingerprint(key=("r1",), row_hash="h1", scope_key="t1"),
            RowFingerprint(key=("r2",), row_hash="h2", scope_key="t2"),
            RowFingerprint(key=("r3",), row_hash="h3", scope_key="t1"),
        ),
    )

    scoped = fingerprint.for_scope_keys({"t1"})

    assert scoped.row_count == 2
    assert [row.key for row in scoped.rows] == [("r1",), ("r3",)]
    assert scoped.payload_hash != fingerprint.payload_hash


def test_baseline_comparison_detects_data_and_file_divergence(tmp_path: Path) -> None:
    table = TableFingerprintManifest(
        table_name="users",
        primary_key=("id",),
        row_count=1,
        payload_hash="before",
        rows=(RowFingerprint(key=("u1",), row_hash="before"),),
    )
    catalog = _catalog_with_columns((ColumnCatalog(name="id", type_name="text", nullable=False, ordinal=1),))
    expected = BaselineManifest.create(environment="prod", catalog=catalog, tables={"users": table}, files=())
    current = BaselineManifest.create(
        environment="prod",
        catalog=catalog,
        tables={
            "users": TableFingerprintManifest(
                table_name="users",
                primary_key=("id",),
                row_count=1,
                payload_hash="after",
                rows=(RowFingerprint(key=("u1",), row_hash="after"),),
            )
        },
        files=(),
    )

    differences = compare_baseline_snapshots(expected=expected, current=current)

    assert differences == ("table data changed: users",)
    with pytest.raises(ValueError, match="frozen baseline"):
        assert_baseline_unchanged(expected=expected, current=current)


def test_file_merge_persists_intent_before_replacing_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    relative = Path("users/u/threads/t/data.txt")
    source_file = staging / relative
    target_file = target / relative
    source_file.parent.mkdir(parents=True)
    target_file.parent.mkdir(parents=True)
    source_file.write_text("source", encoding="utf-8")
    target_file.write_text("target", encoding="utf-8")
    entry = FileEntry.from_path(
        home=staging,
        path=source_file,
        user_id="u",
        thread_id="t",
        layout="user",
        storage_class=StorageClass.ACTIVE,
    )
    journal_path = tmp_path / "journal.json"

    def fail_copy(*args: object, **kwargs: object) -> None:
        raise OSError("simulated copy failure")

    monkeypatch.setattr("scripts.incremental_migration.files._copy_file_atomic", fail_copy)
    with pytest.raises(OSError, match="simulated"):
        merge_active_files(
            staging_home=staging,
            target_home=target,
            entries=[entry],
            backup_home=backup,
            journal=FileMergeJournal(export_id="exp"),
            journal_path=journal_path,
        )

    persisted = FileMergeJournal.from_dict(json.loads(journal_path.read_text(encoding="utf-8")))
    assert persisted.entries[0].action == "replaced"
    assert (backup / relative).read_text(encoding="utf-8") == "target"


def test_file_merge_replaces_parent_file_and_rolls_it_back(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    target = tmp_path / "target"
    backup = tmp_path / "backup"
    relative = Path("users/u/threads/t/data.txt")
    source_file = staging / relative
    source_file.parent.mkdir(parents=True)
    source_file.write_text("source", encoding="utf-8")
    conflicting_parent = target / "users/u"
    conflicting_parent.parent.mkdir(parents=True)
    conflicting_parent.write_text("original parent file", encoding="utf-8")
    entry = FileEntry.from_path(
        home=staging,
        path=source_file,
        user_id="u",
        thread_id="t",
        layout="user",
        storage_class=StorageClass.ACTIVE,
    )
    journal = FileMergeJournal(export_id="exp")

    merge_active_files(
        staging_home=staging,
        target_home=target,
        entries=[entry],
        backup_home=backup,
        journal=journal,
    )

    assert (target / relative).read_text(encoding="utf-8") == "source"
    rollback_files(target_home=target, backup_home=backup, journal=journal)
    assert conflicting_parent.read_text(encoding="utf-8") == "original parent file"


def _minimal_export_manifest(baseline: BaselineManifest, *, files: tuple[FileEntry, ...] = ()) -> ExportManifest:
    selected = tuple(sorted({entry.thread_id for entry in files}))
    owners = {entry.thread_id: str(entry.user_id) for entry in files}
    return ExportManifest.create(
        baseline_id=baseline.baseline_id,
        source_environment="dev",
        target_environment="prod",
        window=MigrationWindow.parse("2026-07-13T00:00:00+08:00", "2026-07-20T00:00:00+08:00"),
        source_catalog=baseline.catalog,
        selected_thread_ids=selected,
        owner_by_thread=owners,
        tables=(
            TableExportManifest(
                table_name="example",
                role="global",
                scope="full",
                data_path="db/example.copy.gz",
                columns=("id",),
                primary_key=("id",),
                row_count=1,
                raw_sha256=hashlib.sha256(b"1\n").hexdigest(),
                source_payload_hash="b" * 64,
            ),
        ),
        files=files,
    )


def test_package_roundtrip_and_checksum_tamper_detection(tmp_path: Path) -> None:
    catalog = _catalog_with_columns((ColumnCatalog(name="id", type_name="text", nullable=False, ordinal=1),))
    baseline = BaselineManifest.create(environment="prod", catalog=catalog, tables={}, files=())
    package_dir = tmp_path / "package"
    (package_dir / "db").mkdir(parents=True)
    with gzip.open(package_dir / "db/example.copy.gz", "wb") as handle:
        handle.write(b"1\n")
    (package_dir / "files").mkdir()
    with tarfile.open(package_dir / "files/threads.tar.gz", "w:gz"):
        pass
    with tarfile.open(package_dir / "files/runtime-archive.tar.gz", "w:gz"):
        pass
    manifest = finalize_package_directory(package_dir, manifest=_minimal_export_manifest(baseline), baseline=baseline)
    archive = tmp_path / "migration.tar.gz"

    build_package_archive(package_dir, archive)
    extracted = extract_package_archive(archive, tmp_path / "extracted")

    assert extracted.manifest == manifest
    assert extracted.baseline == baseline
    assert package_content_sha256(extracted.root) == package_content_sha256(package_dir)

    with (extracted.root / "db/example.copy.gz").open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_package_directory(extracted.root)


def test_package_policy_binds_window_and_environments(tmp_path: Path) -> None:
    catalog = _catalog_with_columns((ColumnCatalog(name="id", type_name="text", nullable=False, ordinal=1),))
    baseline = BaselineManifest.create(environment="prod", catalog=catalog, tables={}, files=(), baseline_id="exp")
    package_dir = tmp_path / "package"
    (package_dir / "db").mkdir(parents=True)
    with gzip.open(package_dir / "db/example.copy.gz", "wb") as handle:
        handle.write(b"1\n")
    (package_dir / "files").mkdir()
    with tarfile.open(package_dir / "files/threads.tar.gz", "w:gz"):
        pass
    with tarfile.open(package_dir / "files/runtime-archive.tar.gz", "w:gz"):
        pass
    manifest = _minimal_export_manifest(baseline)
    manifest = ExportManifest.from_dict({**manifest.to_dict(), "export_id": "exp"})
    finalize_package_directory(package_dir, manifest=manifest, baseline=baseline)
    extracted = verify_package_directory(package_dir)
    baseline.write(tmp_path / "frozen-baseline.json")
    state = MigrationState(
        export_id="exp",
        phase=MigrationPhase.BASELINE_FROZEN,
        created_at="now",
        updated_at="now",
        details={
            "baseline_sha256": hashlib.sha256((tmp_path / "frozen-baseline.json").read_bytes()).hexdigest(),
            "expected_window": manifest.window.to_dict(),
            "expected_source_environment": "dev",
            "expected_target_environment": "prod",
        },
    )

    _assert_package_matches_state(extracted=extracted, state_dir=tmp_path, state=state)
    state.details["expected_window"] = MigrationWindow.parse("2026-07-14T00:00:00+08:00", "2026-07-20T00:00:00+08:00").to_dict()
    with pytest.raises(ValueError, match="window differs"):
        _assert_package_matches_state(extracted=extracted, state_dir=tmp_path, state=state)


def test_package_rejects_file_archive_class_mismatch(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = home / "users/u/threads/t/background_commands.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("{}", encoding="utf-8")
    entry = FileEntry.from_path(
        home=home,
        path=runtime,
        user_id="u",
        thread_id="t",
        layout="user",
        storage_class=StorageClass.RUNTIME_ARCHIVE,
    )
    catalog = _catalog_with_columns((ColumnCatalog(name="id", type_name="text", nullable=False, ordinal=1),))
    baseline = BaselineManifest.create(environment="prod", catalog=catalog, tables={}, files=())
    package_dir = tmp_path / "package"
    (package_dir / "db").mkdir(parents=True)
    with gzip.open(package_dir / "db/example.copy.gz", "wb") as handle:
        handle.write(b"1\n")
    (package_dir / "files").mkdir()
    # Deliberately put a runtime-only path in the active archive.
    with tarfile.open(package_dir / "files/threads.tar.gz", "w:gz") as archive:
        archive.add(runtime, arcname=entry.relative_path)
    with tarfile.open(package_dir / "files/runtime-archive.tar.gz", "w:gz"):
        pass

    with pytest.raises(ValueError, match="active file archive does not match manifest"):
        finalize_package_directory(package_dir, manifest=_minimal_export_manifest(baseline, files=(entry,)), baseline=baseline)


def test_export_manifest_rejects_owner_path_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "users/u/threads/t/data.txt"
    source.parent.mkdir(parents=True)
    source.write_text("payload", encoding="utf-8")
    entry = FileEntry.from_path(
        home=tmp_path,
        path=source,
        user_id="other-user",
        thread_id="t",
        layout="user",
        storage_class=StorageClass.ACTIVE,
    )
    catalog = _catalog_with_columns((ColumnCatalog(name="id", type_name="text", nullable=False, ordinal=1),))

    with pytest.raises(ValueError, match="path does not match"):
        ExportManifest.create(
            baseline_id="baseline",
            source_environment="dev",
            target_environment="prod",
            window=MigrationWindow.parse("2026-07-13T00:00:00+08:00", "2026-07-20T00:00:00+08:00"),
            source_catalog=catalog,
            selected_thread_ids=("t",),
            owner_by_thread={"t": "other-user"},
            tables=(),
            files=(entry,),
        )


def test_staged_file_extraction_rebuilds_partial_directories(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = home / "users/u/threads/t/data.txt"
    source.parent.mkdir(parents=True)
    source.write_text("payload", encoding="utf-8")
    entry = FileEntry.from_path(
        home=home,
        path=source,
        user_id="u",
        thread_id="t",
        layout="user",
        storage_class=StorageClass.ACTIVE,
    )
    catalog = _catalog_with_columns((ColumnCatalog(name="id", type_name="text", nullable=False, ordinal=1),))
    baseline = BaselineManifest.create(environment="prod", catalog=catalog, tables={}, files=())
    package_dir = tmp_path / "package"
    (package_dir / "db").mkdir(parents=True)
    with gzip.open(package_dir / "db/example.copy.gz", "wb") as handle:
        handle.write(b"1\n")
    build_file_archives(
        home=home,
        entries=[entry],
        active_tar=package_dir / "files/threads.tar.gz",
        runtime_tar=package_dir / "files/runtime-archive.tar.gz",
    )
    manifest = _minimal_export_manifest(baseline, files=(entry,))
    finalize_package_directory(package_dir, manifest=manifest, baseline=baseline)
    extracted = verify_package_directory(package_dir)
    state_dir = tmp_path / "state"
    partial = state_dir / "staged-active/users/u/threads/t"
    partial.mkdir(parents=True)
    (partial / "partial.txt").write_text("interrupted", encoding="utf-8")

    active_root, runtime_root = _extract_staged_files(extracted=extracted, state_dir=state_dir)

    assert (active_root / entry.relative_path).read_text(encoding="utf-8") == "payload"
    assert not (partial / "partial.txt").exists()
    assert runtime_root.is_dir()


def test_staging_schema_name_is_safe_and_deterministic() -> None:
    assert staging_schema_name("7da9d95e-8cb8-4f58-a489-bd3589942cda") == "deerflow_migration_7da9d95e8cb84f58a489bd35"
    assert len(staging_schema_name("x" * 100)) <= 63
    with pytest.raises(ValueError, match="export id"):
        staging_schema_name("---")
