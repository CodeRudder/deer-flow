from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.incremental_migration.catalog import DatabaseCatalog, TableCatalog
from scripts.incremental_migration.domain import validate_relative_path
from scripts.incremental_migration.manifest import ExportManifest, TableExportManifest
from scripts.incremental_migration.policy import TableRole, ordered_business_tables
from scripts.incremental_migration.sql import (
    build_business_conflict_statements,
    build_merge_statements,
    quote_identifier,
)

MIGRATION_META_TABLE = "__migration_meta"


def _require_transactional_connection(connection: Any, *, operation: str) -> None:
    if bool(getattr(connection, "autocommit", False)):
        raise ValueError(f"{operation} requires autocommit=False so all statements share one transaction")


def staging_schema_name(export_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]", "", export_id).lower()
    if not normalized:
        raise ValueError("export id does not contain any safe identifier characters")
    return f"deerflow_migration_{normalized[:24]}"[:63]


def _qualified(schema: str, table: str) -> str:
    return f"{quote_identifier(schema)}.{quote_identifier(table)}"


def _transfer_columns(table: TableCatalog) -> tuple[str, ...]:
    return tuple(column.name for column in sorted(table.columns, key=lambda item: item.ordinal) if not column.generated)


def _pk_join(table: TableCatalog, *, left: str = "t", right: str = "s") -> str:
    return " AND ".join(f"{left}.{quote_identifier(column)} = {right}.{quote_identifier(column)}" for column in table.primary_key)


def copy_table_to_gzip(
    connection: Any,
    *,
    table: TableCatalog,
    output_path: Path,
    data_path: str,
    role: TableRole,
    source_payload_hash: str,
    selected_thread_ids: tuple[str, ...] = (),
    source_schema: str = "public",
) -> TableExportManifest:
    from psycopg import sql

    _require_transactional_connection(connection, operation="database export")
    relative_data_path = validate_relative_path(data_path).as_posix()
    columns = _transfer_columns(table)
    if not columns:
        raise ValueError(f"table {table.name} has no transferable columns")
    if role is TableRole.THREAD and "thread_id" not in columns:
        raise ValueError(f"thread table {table.name} has no thread_id column")

    select_query = sql.SQL("SELECT {} FROM {}.{}").format(
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        sql.Identifier(source_schema),
        sql.Identifier(table.name),
    )
    params: tuple[Any, ...] = ()
    if role is TableRole.THREAD:
        select_query += sql.SQL(" WHERE {} = ANY(%s)").format(sql.Identifier("thread_id"))
        params = (list(selected_thread_ids),)
    select_query += sql.SQL(" ORDER BY {} ").format(sql.SQL(", ").join(sql.Identifier(column) for column in table.primary_key))
    copy_query = sql.SQL("COPY ({}) TO STDOUT WITH (FORMAT text)").format(select_query)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    row_count = 0
    fd, raw_temp = tempfile.mkstemp(dir=output_path.parent, prefix=f".{output_path.name}.")
    temp_path = Path(raw_temp)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as raw_output:
            with gzip.GzipFile(fileobj=raw_output, mode="wb") as compressed:
                with connection.cursor() as cursor:
                    with cursor.copy(copy_query, params) as copy:
                        for chunk in copy:
                            payload = bytes(chunk)
                            digest.update(payload)
                            row_count += payload.count(b"\n")
                            compressed.write(payload)
            raw_output.flush()
            os.fsync(raw_output.fileno())
        os.replace(temp_path, output_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return TableExportManifest(
        table_name=table.name,
        role=role.value,
        scope="thread" if role is TableRole.THREAD else "full",
        data_path=relative_data_path,
        columns=columns,
        primary_key=table.primary_key,
        row_count=row_count,
        raw_sha256=digest.hexdigest(),
        source_payload_hash=source_payload_hash,
    )


def _read_gzip_chunks(path: Path, *, chunk_size: int = 1024 * 1024):
    with gzip.open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            yield chunk


def create_and_load_staging(
    connection: Any,
    *,
    package_root: Path,
    manifest: ExportManifest,
    target_catalog: DatabaseCatalog,
    schema: str | None = None,
) -> str:
    from psycopg import sql

    _require_transactional_connection(connection, operation="staging load")
    schema = schema or staging_schema_name(manifest.export_id)
    target_tables = target_catalog.table_by_name
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regnamespace(%s)", (schema,))
        if cursor.fetchone()[0] is not None:
            raise ValueError(f"migration staging schema already exists: {schema}")
        cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        cursor.execute(
            sql.SQL("CREATE TABLE {}.{} (singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton), export_id text NOT NULL, status text NOT NULL, details jsonb NOT NULL DEFAULT '{{}}'::jsonb)").format(
                sql.Identifier(schema), sql.Identifier(MIGRATION_META_TABLE)
            )
        )
        cursor.execute(
            sql.SQL("INSERT INTO {}.{} (export_id, status) VALUES (%s, 'staged')").format(sql.Identifier(schema), sql.Identifier(MIGRATION_META_TABLE)),
            (manifest.export_id,),
        )

        for payload in manifest.tables:
            table = target_tables.get(payload.table_name)
            if table is None:
                raise ValueError(f"target catalog is missing table {payload.table_name}")
            columns = _transfer_columns(table)
            if columns != payload.columns or table.primary_key != payload.primary_key:
                raise ValueError(f"target table shape does not match export manifest: {table.name}")
            column_sql = sql.SQL(", ").join(sql.Identifier(column) for column in columns)
            cursor.execute(
                sql.SQL("CREATE UNLOGGED TABLE {}.{} AS SELECT {} FROM {}.{} WITH NO DATA").format(
                    sql.Identifier(schema),
                    sql.Identifier(table.name),
                    column_sql,
                    sql.Identifier("public"),
                    sql.Identifier(table.name),
                )
            )
            copy_query = sql.SQL("COPY {}.{} ({}) FROM STDIN WITH (FORMAT text)").format(
                sql.Identifier(schema),
                sql.Identifier(table.name),
                column_sql,
            )
            relative = validate_relative_path(payload.data_path)
            data_file = package_root.joinpath(*PurePosixPath(relative.as_posix()).parts)
            digest = hashlib.sha256()
            row_count = 0
            with cursor.copy(copy_query) as copy:
                for chunk in _read_gzip_chunks(data_file):
                    digest.update(chunk)
                    row_count += chunk.count(b"\n")
                    copy.write(chunk)
            if digest.hexdigest() != payload.raw_sha256 or row_count != payload.row_count:
                raise ValueError(f"database payload changed while loading staging: {payload.table_name}")
            cursor.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table.name)))
            loaded_count = int(cursor.fetchone()[0])
            if loaded_count != payload.row_count:
                raise ValueError(f"staging row count mismatch for {payload.table_name}: expected={payload.row_count}, actual={loaded_count}")
    return schema


@dataclass(frozen=True)
class DatabaseConflict:
    table_name: str
    conflict_type: str
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class TableMergePreview:
    table_name: str
    insert_count: int
    update_count: int
    same_count: int
    target_only_count: int


@dataclass(frozen=True)
class DatabasePreflightReport:
    tables: tuple[TableMergePreview, ...]
    conflicts: tuple[DatabaseConflict, ...]
    warnings: tuple[DatabaseConflict, ...] = ()

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)


@dataclass(frozen=True)
class DatabaseMarker:
    export_id: str
    status: str
    details: dict[str, Any]


def read_database_marker(connection: Any, *, staging_schema: str) -> DatabaseMarker:
    from psycopg import sql
    from psycopg.rows import dict_row

    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(sql.SQL("SELECT export_id, status, details FROM {}.{}").format(sql.Identifier(staging_schema), sql.Identifier(MIGRATION_META_TABLE)))
        rows = cursor.fetchall()
    if len(rows) != 1:
        raise ValueError(f"invalid migration database marker in schema {staging_schema}")
    row = rows[0]
    return DatabaseMarker(export_id=str(row["export_id"]), status=str(row["status"]), details=dict(row["details"] or {}))


def _fetch_dict_rows(cursor: Any, query: str, params: tuple[Any, ...] | list[Any] | None = None) -> tuple[dict[str, Any], ...]:
    cursor.execute(query, params or ())
    return tuple(dict(row) for row in cursor.fetchall())


def _target_only_sql(table: TableCatalog, *, staging_schema: str, role: TableRole) -> tuple[str, bool]:
    target = _qualified("public", table.name)
    source = _qualified(staging_schema, table.name)
    pk_join = _pk_join(table)
    scope = f"t.{quote_identifier('thread_id')} = ANY(%s) AND " if role is TableRole.THREAD else ""
    return (
        f"SELECT count(*) AS target_only_count FROM {target} AS t WHERE {scope}NOT EXISTS (SELECT 1 FROM {source} AS s WHERE {pk_join})",
        role is TableRole.THREAD,
    )


def _special_conflict_queries(staging_schema: str, imported_tables: set[str]) -> tuple[tuple[str, str, str], ...]:
    queries: list[tuple[str, str, str]] = []
    if "threads_meta" in imported_tables:
        queries.append(
            (
                "threads_meta",
                "owner_mismatch",
                f"SELECT s.thread_id, s.user_id AS source_user_id, t.user_id AS target_user_id "
                f"FROM {_qualified(staging_schema, 'threads_meta')} s "
                f"JOIN {_qualified('public', 'threads_meta')} t ON t.thread_id = s.thread_id "
                "WHERE s.user_id IS DISTINCT FROM t.user_id LIMIT 100",
            )
        )
    if "run_events" in imported_tables:
        queries.append(
            (
                "run_events",
                "primary_key_logical_event_mismatch",
                f"SELECT s.id, s.thread_id AS source_thread_id, t.thread_id AS target_thread_id, "
                f"s.seq AS source_seq, t.seq AS target_seq FROM {_qualified(staging_schema, 'run_events')} s "
                f"JOIN {_qualified('public', 'run_events')} t ON t.id = s.id "
                "WHERE s.thread_id IS DISTINCT FROM t.thread_id OR s.seq IS DISTINCT FROM t.seq LIMIT 100",
            )
        )
        queries.append(
            (
                "run_events",
                "logical_event_primary_key_mismatch",
                f"SELECT s.thread_id, s.seq, s.id AS source_id, t.id AS target_id "
                f"FROM {_qualified(staging_schema, 'run_events')} s "
                f"JOIN {_qualified('public', 'run_events')} t ON t.thread_id = s.thread_id AND t.seq = s.seq "
                "WHERE s.id IS DISTINCT FROM t.id LIMIT 100",
            )
        )
    if "feedback" in imported_tables:
        queries.append(
            (
                "feedback",
                "anonymous_business_key_duplicate",
                "SELECT thread_id, run_id, count(DISTINCT feedback_id) AS feedback_count FROM ("
                f"SELECT thread_id, run_id, feedback_id FROM {_qualified(staging_schema, 'feedback')} WHERE user_id IS NULL "
                "UNION ALL "
                f"SELECT t.thread_id, t.run_id, t.feedback_id FROM {_qualified('public', 'feedback')} t "
                "WHERE t.user_id IS NULL AND EXISTS ("
                f"SELECT 1 FROM {_qualified(staging_schema, 'feedback')} s "
                "WHERE s.user_id IS NULL AND s.thread_id = t.thread_id AND s.run_id = t.run_id)"
                ") anonymous_feedback GROUP BY thread_id, run_id HAVING count(DISTINCT feedback_id) > 1 LIMIT 100",
            )
        )
    if "checkpoints" in imported_tables:
        queries.append(
            (
                "checkpoints",
                "missing_parent_checkpoint",
                f"SELECT c.thread_id, c.checkpoint_ns, c.checkpoint_id, c.parent_checkpoint_id "
                f"FROM {_qualified(staging_schema, 'checkpoints')} c "
                "WHERE c.parent_checkpoint_id IS NOT NULL "
                f"AND NOT EXISTS (SELECT 1 FROM {_qualified(staging_schema, 'checkpoints')} p "
                "WHERE p.thread_id = c.thread_id AND p.checkpoint_ns = c.checkpoint_ns "
                "AND p.checkpoint_id = c.parent_checkpoint_id) "
                f"AND NOT EXISTS (SELECT 1 FROM {_qualified('public', 'checkpoints')} p "
                "WHERE p.thread_id = c.thread_id AND p.checkpoint_ns = c.checkpoint_ns "
                "AND p.checkpoint_id = c.parent_checkpoint_id) LIMIT 100",
            )
        )
    if {"checkpoint_writes", "checkpoints"}.issubset(imported_tables):
        queries.append(
            (
                "checkpoint_writes",
                "missing_checkpoint",
                f"SELECT w.thread_id, w.checkpoint_ns, w.checkpoint_id, w.task_id, w.idx "
                f"FROM {_qualified(staging_schema, 'checkpoint_writes')} w "
                f"WHERE NOT EXISTS (SELECT 1 FROM {_qualified(staging_schema, 'checkpoints')} c "
                "WHERE c.thread_id = w.thread_id AND c.checkpoint_ns = w.checkpoint_ns AND c.checkpoint_id = w.checkpoint_id) "
                f"AND NOT EXISTS (SELECT 1 FROM {_qualified('public', 'checkpoints')} c "
                "WHERE c.thread_id = w.thread_id AND c.checkpoint_ns = w.checkpoint_ns AND c.checkpoint_id = w.checkpoint_id) "
                "LIMIT 100",
            )
        )
    if {"checkpoint_blobs", "checkpoints"}.issubset(imported_tables):
        queries.append(
            (
                "checkpoint_blobs",
                "missing_channel_blob",
                f"SELECT c.thread_id, c.checkpoint_ns, c.checkpoint_id, versions.key AS channel, versions.value AS version "
                f"FROM {_qualified(staging_schema, 'checkpoints')} c "
                "CROSS JOIN LATERAL jsonb_each_text(coalesce(c.checkpoint::jsonb -> 'channel_versions', '{}'::jsonb)) versions "
                f"WHERE NOT EXISTS (SELECT 1 FROM {_qualified(staging_schema, 'checkpoint_blobs')} b "
                "WHERE b.thread_id = c.thread_id AND b.checkpoint_ns = c.checkpoint_ns "
                "AND b.channel = versions.key AND b.version = versions.value) "
                f"AND NOT EXISTS (SELECT 1 FROM {_qualified('public', 'checkpoint_blobs')} b "
                "WHERE b.thread_id = c.thread_id AND b.checkpoint_ns = c.checkpoint_ns "
                "AND b.channel = versions.key AND b.version = versions.value) LIMIT 100",
            )
        )
    return tuple(queries)


def preflight_database_merge(
    connection: Any,
    *,
    manifest: ExportManifest,
    target_catalog: DatabaseCatalog,
    staging_schema: str,
) -> DatabasePreflightReport:
    from psycopg.rows import dict_row

    target_tables = target_catalog.table_by_name
    previews: list[TableMergePreview] = []
    conflicts: list[DatabaseConflict] = []
    warnings: list[DatabaseConflict] = []
    imported_tables = {table.table_name for table in manifest.tables}
    with connection.cursor(row_factory=dict_row) as cursor:
        for payload in manifest.tables:
            table = target_tables[payload.table_name]
            spec = table.to_table_spec()
            statements = build_merge_statements(spec, staging_schema=staging_schema)
            duplicate_rows = _fetch_dict_rows(cursor, statements.duplicate_primary_keys)
            if duplicate_rows:
                conflicts.append(DatabaseConflict(table.name, "duplicate_primary_key", duplicate_rows))
            for statement in build_business_conflict_statements(spec, staging_schema=staging_schema):
                rows = _fetch_dict_rows(cursor, statement.sql)
                if rows:
                    conflicts.append(DatabaseConflict(table.name, statement.name, rows))
            cursor.execute(statements.counts)
            counts = dict(cursor.fetchone())
            role = TableRole(payload.role)
            target_only_sql, needs_scope = _target_only_sql(table, staging_schema=staging_schema, role=role)
            cursor.execute(target_only_sql, (list(manifest.selected_thread_ids),) if needs_scope else ())
            target_only_count = int(cursor.fetchone()["target_only_count"])
            previews.append(
                TableMergePreview(
                    table_name=table.name,
                    insert_count=int(counts["insert_count"]),
                    update_count=int(counts["update_count"]),
                    same_count=int(counts["same_count"]),
                    target_only_count=target_only_count,
                )
            )
        for table_name, conflict_type, query in _special_conflict_queries(staging_schema, imported_tables):
            rows = _fetch_dict_rows(cursor, query)
            if rows:
                destination = warnings if conflict_type == "missing_channel_blob" else conflicts
                destination.append(DatabaseConflict(table_name, conflict_type, rows))
    return DatabasePreflightReport(tables=tuple(previews), conflicts=tuple(conflicts), warnings=tuple(warnings))


def verify_loaded_staging(
    connection: Any,
    *,
    manifest: ExportManifest,
    target_catalog: DatabaseCatalog,
    staging_schema: str,
) -> None:
    from scripts.incremental_migration.postgres import fingerprint_table

    marker = read_database_marker(connection, staging_schema=staging_schema)
    if marker.export_id != manifest.export_id:
        raise ValueError("staging schema belongs to a different export")
    if marker.status not in {"staged", "db_committed"}:
        raise ValueError(f"staging schema has an invalid status: {marker.status}")
    for payload in manifest.tables:
        table = target_catalog.table_by_name[payload.table_name]
        role = TableRole(payload.role)
        fingerprint = fingerprint_table(
            connection,
            table,
            schema=staging_schema,
            scope_column="thread_id" if role is TableRole.THREAD else None,
        )
        if fingerprint.row_count != payload.row_count or fingerprint.payload_hash != payload.source_payload_hash:
            raise ValueError(f"staging payload fingerprint mismatch: {payload.table_name}")


@dataclass(frozen=True)
class SequenceState:
    table_name: str
    column_name: str
    sequence_schema: str
    sequence_name: str
    last_value: int
    is_called: bool

    @property
    def qualified_name(self) -> str:
        return f"{quote_identifier(self.sequence_schema)}.{quote_identifier(self.sequence_name)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_name": self.table_name,
            "column_name": self.column_name,
            "sequence_schema": self.sequence_schema,
            "sequence_name": self.sequence_name,
            "last_value": self.last_value,
            "is_called": self.is_called,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SequenceState:
        return cls(
            table_name=str(value["table_name"]),
            column_name=str(value["column_name"]),
            sequence_schema=str(value["sequence_schema"]),
            sequence_name=str(value["sequence_name"]),
            last_value=int(value["last_value"]),
            is_called=bool(value["is_called"]),
        )


def capture_sequence_states(
    connection: Any,
    *,
    catalog: DatabaseCatalog,
    table_names: tuple[str, ...],
) -> tuple[SequenceState, ...]:
    from psycopg import sql
    from psycopg.rows import dict_row

    states: list[SequenceState] = []
    with connection.cursor(row_factory=dict_row) as cursor:
        for table_name in table_names:
            table = catalog.table_by_name[table_name]
            relation = f"{quote_identifier('public')}.{quote_identifier(table_name)}"
            for column in table.columns:
                cursor.execute("SELECT pg_get_serial_sequence(%s, %s) AS sequence_name", (relation, column.name))
                sequence_row = cursor.fetchone()
                sequence_regclass = sequence_row["sequence_name"] if sequence_row else None
                if not sequence_regclass:
                    continue
                cursor.execute(
                    """
                    SELECT namespace_row.nspname AS sequence_schema, sequence_row.relname AS sequence_name
                    FROM pg_catalog.pg_class sequence_row
                    JOIN pg_catalog.pg_namespace namespace_row ON namespace_row.oid = sequence_row.relnamespace
                    WHERE sequence_row.oid = %s::regclass
                    """,
                    (sequence_regclass,),
                )
                identity = cursor.fetchone()
                if identity is None:
                    raise ValueError(f"cannot resolve sequence {sequence_regclass}")
                cursor.execute(
                    sql.SQL("SELECT last_value, is_called FROM {}.{}").format(
                        sql.Identifier(identity["sequence_schema"]),
                        sql.Identifier(identity["sequence_name"]),
                    )
                )
                value = cursor.fetchone()
                states.append(
                    SequenceState(
                        table_name=table_name,
                        column_name=column.name,
                        sequence_schema=str(identity["sequence_schema"]),
                        sequence_name=str(identity["sequence_name"]),
                        last_value=int(value["last_value"]),
                        is_called=bool(value["is_called"]),
                    )
                )
    return tuple(states)


@dataclass(frozen=True)
class TableMergeResult:
    table_name: str
    inserted: int
    updated: int


@dataclass(frozen=True)
class DatabaseApplyResult:
    tables: tuple[TableMergeResult, ...]
    sequence_states: tuple[SequenceState, ...]


def _backup_table_name(table_name: str) -> str:
    return f"backup__{table_name}"


def _inserted_table_name(table_name: str) -> str:
    return f"inserted__{table_name}"


def _ordered_manifest_tables(manifest: ExportManifest) -> tuple[str, ...]:
    roles = {payload.table_name: TableRole(payload.role) for payload in manifest.tables}
    return ordered_business_tables(roles)


def _prepare_database_backups(
    connection: Any,
    *,
    manifest: ExportManifest,
    catalog: DatabaseCatalog,
    staging_schema: str,
) -> None:
    from psycopg import sql

    with connection.cursor() as cursor:
        for table_name in _ordered_manifest_tables(manifest):
            table = catalog.table_by_name[table_name]
            pk_join = _pk_join(table)
            cursor.execute(
                sql.SQL("CREATE TABLE {}.{} AS SELECT t.* FROM {}.{} t JOIN {}.{} s ON ").format(
                    sql.Identifier(staging_schema),
                    sql.Identifier(_backup_table_name(table_name)),
                    sql.Identifier("public"),
                    sql.Identifier(table_name),
                    sql.Identifier(staging_schema),
                    sql.Identifier(table_name),
                )
                + sql.SQL(pk_join)
            )
            pk_columns = sql.SQL(", ").join(sql.SQL("s.{} ").format(sql.Identifier(column)) for column in table.primary_key)
            cursor.execute(
                sql.SQL("CREATE TABLE {}.{} AS SELECT {} FROM {}.{} s WHERE NOT EXISTS (SELECT 1 FROM {}.{} t WHERE ").format(
                    sql.Identifier(staging_schema),
                    sql.Identifier(_inserted_table_name(table_name)),
                    pk_columns,
                    sql.Identifier(staging_schema),
                    sql.Identifier(table_name),
                    sql.Identifier("public"),
                    sql.Identifier(table_name),
                )
                + sql.SQL(pk_join)
                + sql.SQL(")")
            )


def _adjust_sequences(connection: Any, *, states: tuple[SequenceState, ...]) -> None:
    from psycopg import sql

    with connection.cursor() as cursor:
        for state in states:
            cursor.execute(
                sql.SQL("SELECT max({}) FROM {}.{}").format(
                    sql.Identifier(state.column_name),
                    sql.Identifier("public"),
                    sql.Identifier(state.table_name),
                )
            )
            maximum = cursor.fetchone()[0]
            if maximum is None:
                continue
            maximum = int(maximum)
            cursor.execute(
                sql.SQL("SELECT last_value, is_called FROM {}.{}").format(
                    sql.Identifier(state.sequence_schema),
                    sql.Identifier(state.sequence_name),
                )
            )
            current_last, current_is_called = cursor.fetchone()
            if maximum > int(current_last) or (maximum == int(current_last) and not bool(current_is_called)):
                cursor.execute("SELECT setval(%s::regclass, %s, true)", (state.qualified_name, maximum))


def apply_database_merge(
    connection: Any,
    *,
    manifest: ExportManifest,
    target_catalog: DatabaseCatalog,
    staging_schema: str,
) -> DatabaseApplyResult:
    _require_transactional_connection(connection, operation="database merge")
    marker = read_database_marker(connection, staging_schema=staging_schema)
    if marker.export_id != manifest.export_id:
        raise ValueError("staging schema belongs to a different export")
    if marker.status != "staged":
        raise ValueError(f"database merge requires a staged marker, found {marker.status}")
    verify_loaded_staging(
        connection,
        manifest=manifest,
        target_catalog=target_catalog,
        staging_schema=staging_schema,
    )
    report = preflight_database_merge(
        connection,
        manifest=manifest,
        target_catalog=target_catalog,
        staging_schema=staging_schema,
    )
    if report.has_conflicts:
        summary = ", ".join(f"{item.table_name}:{item.conflict_type}" for item in report.conflicts)
        raise ValueError(f"database merge preflight found conflicts: {summary}")
    ordered_tables = _ordered_manifest_tables(manifest)
    sequence_states = capture_sequence_states(connection, catalog=target_catalog, table_names=ordered_tables)
    _prepare_database_backups(
        connection,
        manifest=manifest,
        catalog=target_catalog,
        staging_schema=staging_schema,
    )
    results: list[TableMergeResult] = []
    with connection.cursor() as cursor:
        for table_name in ordered_tables:
            table = target_catalog.table_by_name[table_name]
            statements = build_merge_statements(table.to_table_spec(), staging_schema=staging_schema)
            cursor.execute(statements.insert)
            inserted = max(cursor.rowcount, 0)
            updated = 0
            if statements.update:
                cursor.execute(statements.update)
                updated = max(cursor.rowcount, 0)
            results.append(TableMergeResult(table_name=table_name, inserted=inserted, updated=updated))
    _adjust_sequences(connection, states=sequence_states)
    result = DatabaseApplyResult(tables=tuple(results), sequence_states=sequence_states)
    details = {
        "tables": [{"table_name": item.table_name, "inserted": item.inserted, "updated": item.updated} for item in result.tables],
        "sequence_states": [state.to_dict() for state in result.sequence_states],
    }
    from psycopg import sql

    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("UPDATE {}.{} SET status = 'db_committed', details = details || %s::jsonb").format(sql.Identifier(staging_schema), sql.Identifier(MIGRATION_META_TABLE)),
            (json.dumps(details, sort_keys=True),),
        )
        if cursor.rowcount != 1:
            raise ValueError("failed to update migration database marker")
    return result


def _restore_sequence_states(connection: Any, states: tuple[SequenceState, ...]) -> None:
    with connection.cursor() as cursor:
        for state in states:
            cursor.execute(
                "SELECT setval(%s::regclass, %s, %s)",
                (state.qualified_name, state.last_value, state.is_called),
            )


def rollback_database_merge(
    connection: Any,
    *,
    manifest: ExportManifest,
    target_catalog: DatabaseCatalog,
    staging_schema: str,
    sequence_states: tuple[SequenceState, ...],
) -> None:
    from psycopg import sql

    _require_transactional_connection(connection, operation="database rollback")
    marker = read_database_marker(connection, staging_schema=staging_schema)
    if marker.export_id != manifest.export_id:
        raise ValueError("staging schema belongs to a different export")
    if marker.status != "db_committed":
        raise ValueError(f"database rollback requires a committed marker, found {marker.status}")
    ordered_tables = _ordered_manifest_tables(manifest)
    with connection.cursor() as cursor:
        for table_name in reversed(ordered_tables):
            table = target_catalog.table_by_name[table_name]
            pk_join = _pk_join(table, left="t", right="i")
            cursor.execute(
                sql.SQL("DELETE FROM {}.{} t USING {}.{} i WHERE ").format(
                    sql.Identifier("public"),
                    sql.Identifier(table_name),
                    sql.Identifier(staging_schema),
                    sql.Identifier(_inserted_table_name(table_name)),
                )
                + sql.SQL(pk_join)
            )

        for table_name in ordered_tables:
            table = target_catalog.table_by_name[table_name]
            columns = [column for column in sorted(table.columns, key=lambda item: item.ordinal) if not column.generated]
            update_columns = [column for column in columns if column.name not in table.primary_key and not column.identity]
            pk_join = _pk_join(table, left="t", right="b")
            if update_columns:
                assignments = sql.SQL(", ").join(sql.SQL("{} = b.{}").format(sql.Identifier(column.name), sql.Identifier(column.name)) for column in update_columns)
                cursor.execute(
                    sql.SQL("UPDATE {}.{} t SET {} FROM {}.{} b WHERE ").format(
                        sql.Identifier("public"),
                        sql.Identifier(table_name),
                        assignments,
                        sql.Identifier(staging_schema),
                        sql.Identifier(_backup_table_name(table_name)),
                    )
                    + sql.SQL(pk_join)
                )
            column_list = sql.SQL(", ").join(sql.Identifier(column.name) for column in columns)
            selected = sql.SQL(", ").join(sql.SQL("b.{}").format(sql.Identifier(column.name)) for column in columns)
            identity_clause = sql.SQL(" OVERRIDING SYSTEM VALUE") if any(column.identity for column in columns) else sql.SQL("")
            cursor.execute(
                sql.SQL("INSERT INTO {}.{} ({})").format(
                    sql.Identifier("public"),
                    sql.Identifier(table_name),
                    column_list,
                )
                + identity_clause
                + sql.SQL(" SELECT {} FROM {}.{} b WHERE NOT EXISTS (SELECT 1 FROM {}.{} t WHERE ").format(
                    selected,
                    sql.Identifier(staging_schema),
                    sql.Identifier(_backup_table_name(table_name)),
                    sql.Identifier("public"),
                    sql.Identifier(table_name),
                )
                + sql.SQL(pk_join)
                + sql.SQL(")")
            )
    _restore_sequence_states(connection, sequence_states)
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("UPDATE {}.{} SET status = 'rolled_back'").format(sql.Identifier(staging_schema), sql.Identifier(MIGRATION_META_TABLE)))


def mark_database_verified(connection: Any, *, manifest: ExportManifest, staging_schema: str) -> None:
    from psycopg import sql

    _require_transactional_connection(connection, operation="database verification")
    marker = read_database_marker(connection, staging_schema=staging_schema)
    if marker.export_id != manifest.export_id or marker.status not in {"db_committed", "verified"}:
        raise ValueError("cannot verify a staging schema without the matching committed database marker")
    if marker.status == "verified":
        return
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("UPDATE {}.{} SET status = 'verified'").format(sql.Identifier(staging_schema), sql.Identifier(MIGRATION_META_TABLE)))


def drop_migration_schema(connection: Any, schema: str, *, expected_export_id: str | None = None) -> None:
    from psycopg import sql

    _require_transactional_connection(connection, operation="migration schema cleanup")
    if not schema.startswith("deerflow_migration_"):
        raise ValueError(f"refusing to drop non-migration schema: {schema}")
    marker = read_database_marker(connection, staging_schema=schema)
    if expected_export_id is not None and marker.export_id != expected_export_id:
        raise ValueError("refusing to drop a migration schema owned by a different export")
    if marker.status not in {"staged", "rolled_back", "verified"}:
        raise ValueError(f"refusing to drop migration schema in status {marker.status}")
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
