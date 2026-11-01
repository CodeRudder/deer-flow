from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from scripts.incremental_migration.catalog import ColumnCatalog, DatabaseCatalog, TableCatalog, UniqueIndexCatalog
from scripts.incremental_migration.domain import canonical_hash, canonical_json_bytes
from scripts.incremental_migration.manifest import RowFingerprint, TableFingerprintManifest
from scripts.incremental_migration.policy import TableRole, classify_table_set


def build_catalog_from_records(
    *,
    postgres_version_num: int,
    table_records: Iterable[Mapping[str, Any]],
    column_records: Iterable[Mapping[str, Any]],
    unique_index_records: Iterable[Mapping[str, Any]],
    constraint_records: Iterable[Mapping[str, Any]],
    index_records: Iterable[Mapping[str, Any]],
    trigger_records: Iterable[Mapping[str, Any]],
    grant_records: Iterable[Mapping[str, Any]],
    policy_records: Iterable[Mapping[str, Any]] = (),
    extensions: Iterable[str] = (),
) -> DatabaseCatalog:
    tables = {str(row["table_name"]): dict(row) for row in table_records}
    columns: dict[str, list[ColumnCatalog]] = defaultdict(list)
    unique_indexes: dict[str, list[UniqueIndexCatalog]] = defaultdict(list)
    constraints: dict[str, list[str]] = defaultdict(list)
    foreign_keys: dict[str, list[str]] = defaultdict(list)
    indexes: dict[str, list[str]] = defaultdict(list)
    triggers: dict[str, list[str]] = defaultdict(list)
    policies: dict[str, list[str]] = defaultdict(list)
    grants: dict[str, list[str]] = defaultdict(list)

    for row in column_records:
        table_name = str(row["table_name"])
        columns[table_name].append(
            ColumnCatalog(
                name=str(row["column_name"]),
                type_name=str(row["type_name"]),
                nullable=not bool(row["not_null"]),
                ordinal=int(row["ordinal"]),
                default=str(row["default_expression"]) if row.get("default_expression") is not None else None,
                identity=str(row.get("identity") or ""),
                generated=str(row.get("generated") or ""),
                collation=str(row["collation"]) if row.get("collation") is not None else None,
            )
        )

    for row in unique_index_records:
        table_name = str(row["table_name"])
        if bool(row.get("has_expression")):
            raise ValueError(f"expression-based unique index is unsupported: {table_name}.{row['index_name']}")
        unique_indexes[table_name].append(
            UniqueIndexCatalog(
                name=str(row["index_name"]),
                columns=tuple(str(item) for item in (row.get("columns") or [])),
                primary=bool(row.get("primary")),
                predicate=str(row["predicate"]) if row.get("predicate") is not None else None,
                nulls_not_distinct=bool(row.get("nulls_not_distinct", False)),
                definition=str(row["definition"]) if row.get("definition") is not None else None,
            )
        )

    for row in constraint_records:
        table_name = str(row["table_name"])
        definition = str(row["definition"])
        if str(row.get("constraint_type", "")) == "f":
            foreign_keys[table_name].append(definition)
        else:
            constraints[table_name].append(definition)
    for row in index_records:
        indexes[str(row["table_name"])].append(str(row["definition"]))
    for row in trigger_records:
        triggers[str(row["table_name"])].append(str(row["definition"]))
    for row in policy_records:
        policies[str(row["table_name"])].append(str(row["definition"]))
    for row in grant_records:
        grants[str(row["table_name"])].append(str(row["grant"]))

    result: list[TableCatalog] = []
    for name, table_row in sorted(tables.items()):
        table_unique_indexes = tuple(sorted(unique_indexes[name], key=lambda item: item.name))
        primary_indexes = [item for item in table_unique_indexes if item.primary]
        if len(primary_indexes) > 1:
            raise ValueError(f"table {name} has multiple primary indexes")
        primary_key = primary_indexes[0].columns if primary_indexes else ()
        result.append(
            TableCatalog(
                name=name,
                owner=str(table_row.get("owner", "")),
                row_security=bool(table_row.get("row_security", False)),
                force_row_security=bool(table_row.get("force_row_security", False)),
                columns=tuple(sorted(columns[name], key=lambda item: item.ordinal)),
                primary_key=primary_key,
                unique_indexes=table_unique_indexes,
                constraints=tuple(sorted(constraints[name])),
                foreign_keys=tuple(sorted(foreign_keys[name])),
                indexes=tuple(sorted(indexes[name])),
                triggers=tuple(sorted(triggers[name])),
                policies=tuple(sorted(policies[name])),
                grants=tuple(sorted(grants[name])),
            )
        )
    return DatabaseCatalog(
        postgres_version_num=postgres_version_num,
        tables=tuple(result),
        extensions=tuple(sorted(set(extensions))),
    )


def fingerprint_records(
    table: TableCatalog,
    records: Iterable[Mapping[str, Any]],
    *,
    scope_column: str | None = None,
    allow_unkeyed: bool = False,
) -> TableFingerprintManifest:
    if not table.primary_key and not allow_unkeyed:
        raise ValueError(f"cannot fingerprint table without a primary key: {table.name}")
    if not table.primary_key and scope_column is not None:
        raise ValueError(f"cannot scope an unkeyed table fingerprint: {table.name}")
    transfer_columns = [column.name for column in sorted(table.columns, key=lambda item: item.ordinal) if not column.generated]
    materialized = [dict(record) for record in records]
    for record in materialized:
        missing = set(transfer_columns) - set(record)
        if missing:
            raise ValueError(f"record for {table.name} is missing columns: {sorted(missing)}")
    if table.primary_key:
        materialized.sort(key=lambda record: canonical_json_bytes(tuple(record[name] for name in table.primary_key)))
    else:
        materialized.sort(key=lambda record: _unkeyed_row_sort_key(record, transfer_columns))
    return _fingerprint_ordered_records(table, materialized, transfer_columns=transfer_columns, scope_column=scope_column)


def _unkeyed_row_sort_key(record: Mapping[str, Any], transfer_columns: Sequence[str]) -> bytes:
    return canonical_json_bytes({name: record[name] for name in transfer_columns})


def _fingerprint_ordered_records(
    table: TableCatalog,
    records: Iterable[Mapping[str, Any]],
    *,
    transfer_columns: Sequence[str],
    scope_column: str | None,
) -> TableFingerprintManifest:
    digest = hashlib.sha256()
    rows: list[RowFingerprint] = []
    duplicate_ordinals: dict[str, int] = defaultdict(int)
    for record in records:
        row_payload = {name: record[name] for name in transfer_columns}
        row_hash = canonical_hash(row_payload)
        if table.primary_key:
            key = tuple(record[name] for name in table.primary_key)
        else:
            # Bookkeeping tables are never merged, but their full row multiset
            # must remain comparable even when the schema defines no key.
            key = (row_hash, duplicate_ordinals[row_hash])
            duplicate_ordinals[row_hash] += 1
        scope_key = str(record[scope_column]) if scope_column is not None else None
        fingerprint = RowFingerprint(key=key, row_hash=row_hash, scope_key=scope_key)
        rows.append(fingerprint)
        digest.update(canonical_json_bytes(fingerprint.to_dict()))
        digest.update(b"\n")
    return TableFingerprintManifest(
        table_name=table.name,
        primary_key=table.primary_key,
        row_count=len(rows),
        payload_hash=digest.hexdigest(),
        rows=tuple(rows),
    )


def validate_catalog(catalog: DatabaseCatalog) -> dict[str, TableRole]:
    roles = classify_table_set(set(catalog.table_by_name))
    for name, role in roles.items():
        table = catalog.table_by_name[name]
        if role is not TableRole.BOOKKEEPING and not table.primary_key:
            raise ValueError(f"business table {name!r} does not have a stable primary key")
        generated = [column.name for column in table.columns if column.generated]
        if role is not TableRole.BOOKKEEPING and generated:
            raise ValueError(f"business table {name!r} has unsupported generated columns: {generated}")
        non_key_identity = [column.name for column in table.columns if column.identity and column.name not in table.primary_key]
        if role is not TableRole.BOOKKEEPING and non_key_identity:
            raise ValueError(f"business table {name!r} has unsupported non-key identity columns: {non_key_identity}")
    return roles


def validate_catalog_compatibility(
    source: DatabaseCatalog,
    target: DatabaseCatalog,
    *,
    require_security_match: bool = False,
) -> None:
    source_major = source.postgres_version_num // 10000
    target_major = target.postgres_version_num // 10000
    if source_major != target_major:
        raise ValueError(f"PostgreSQL major version mismatch: source={source_major}, target={target_major}")
    if source.structural_fingerprint != target.structural_fingerprint:
        raise ValueError("source and target schema structural fingerprint mismatch")
    if require_security_match and source.security_fingerprint != target.security_fingerprint:
        raise ValueError("source and target schema security fingerprint mismatch")
    validate_catalog(source)
    validate_catalog(target)


def read_database_catalog(connection: Any, *, schema: str = "public") -> DatabaseCatalog:
    from psycopg.rows import dict_row

    if bool(getattr(connection, "autocommit", False)):
        raise ValueError("catalog inspection requires autocommit=False for a stable search_path")
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SET LOCAL search_path = pg_catalog")
        cursor.execute("SHOW server_version_num")
        version_row = cursor.fetchone()
        if version_row is None:
            raise ValueError("PostgreSQL did not return server_version_num")
        postgres_version_num = int(version_row["server_version_num"])
        nulls_not_distinct = "ix.indnullsnotdistinct" if postgres_version_num >= 150000 else "false"

        cursor.execute(
            """
            SELECT c.relname AS table_name,
                   owner_role.rolname AS owner,
                   c.relrowsecurity AS row_security,
                   c.relforcerowsecurity AS force_row_security
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = c.relowner
            WHERE n.nspname = %s AND c.relkind IN ('r', 'p')
            ORDER BY c.relname
            """,
            (schema,),
        )
        table_records = cursor.fetchall()

        cursor.execute(
            """
            SELECT c.relname AS table_name,
                   a.attnum AS ordinal,
                   a.attname AS column_name,
                   pg_catalog.format_type(a.atttypid, a.atttypmod) AS type_name,
                   a.attnotnull AS not_null,
                   pg_catalog.pg_get_expr(ad.adbin, ad.adrelid) AS default_expression,
                   a.attidentity AS identity,
                   a.attgenerated AS generated,
                   coll.collname AS collation
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
            LEFT JOIN pg_catalog.pg_attrdef ad ON ad.adrelid = c.oid AND ad.adnum = a.attnum
            LEFT JOIN pg_catalog.pg_collation coll ON coll.oid = a.attcollation AND a.attcollation <> 0
            WHERE n.nspname = %s AND c.relkind IN ('r', 'p')
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY c.relname, a.attnum
            """,
            (schema,),
        )
        column_records = cursor.fetchall()

        cursor.execute(
            f"""
            SELECT table_class.relname AS table_name,
                   index_class.relname AS index_name,
                   ix.indisprimary AS primary,
                   {nulls_not_distinct} AS nulls_not_distinct,
                   pg_catalog.pg_get_expr(ix.indpred, ix.indrelid) AS predicate,
                   pg_catalog.pg_get_indexdef(ix.indexrelid) AS definition,
                   array_agg(attribute.attname ORDER BY key_part.ordinality)
                       FILTER (WHERE key_part.attnum <> 0) AS columns,
                   bool_or(key_part.attnum = 0) AS has_expression
            FROM pg_catalog.pg_index ix
            JOIN pg_catalog.pg_class table_class ON table_class.oid = ix.indrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = table_class.relnamespace
            JOIN pg_catalog.pg_class index_class ON index_class.oid = ix.indexrelid
            CROSS JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS key_part(attnum, ordinality)
            LEFT JOIN pg_catalog.pg_attribute attribute
              ON attribute.attrelid = table_class.oid AND attribute.attnum = key_part.attnum
            WHERE n.nspname = %s AND ix.indisunique
              AND key_part.ordinality <= ix.indnkeyatts
            GROUP BY table_class.relname, index_class.relname, ix.indisprimary,
                     ix.indnullsnotdistinct, ix.indpred, ix.indrelid, ix.indexrelid
            ORDER BY table_class.relname, index_class.relname
            """
            if postgres_version_num >= 150000
            else f"""
            SELECT table_class.relname AS table_name,
                   index_class.relname AS index_name,
                   ix.indisprimary AS primary,
                   {nulls_not_distinct} AS nulls_not_distinct,
                   pg_catalog.pg_get_expr(ix.indpred, ix.indrelid) AS predicate,
                   pg_catalog.pg_get_indexdef(ix.indexrelid) AS definition,
                   array_agg(attribute.attname ORDER BY key_part.ordinality)
                       FILTER (WHERE key_part.attnum <> 0) AS columns,
                   bool_or(key_part.attnum = 0) AS has_expression
            FROM pg_catalog.pg_index ix
            JOIN pg_catalog.pg_class table_class ON table_class.oid = ix.indrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = table_class.relnamespace
            JOIN pg_catalog.pg_class index_class ON index_class.oid = ix.indexrelid
            CROSS JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS key_part(attnum, ordinality)
            LEFT JOIN pg_catalog.pg_attribute attribute
              ON attribute.attrelid = table_class.oid AND attribute.attnum = key_part.attnum
            WHERE n.nspname = %s AND ix.indisunique
              AND key_part.ordinality <= ix.indnkeyatts
            GROUP BY table_class.relname, index_class.relname, ix.indisprimary,
                     ix.indpred, ix.indrelid, ix.indexrelid
            ORDER BY table_class.relname, index_class.relname
            """,
            (schema,),
        )
        unique_index_records = cursor.fetchall()

        cursor.execute(
            """
            SELECT table_class.relname AS table_name,
                   constraint_row.contype AS constraint_type,
                   constraint_row.conname || ':' || pg_catalog.pg_get_constraintdef(constraint_row.oid, true) AS definition
            FROM pg_catalog.pg_constraint constraint_row
            JOIN pg_catalog.pg_class table_class ON table_class.oid = constraint_row.conrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = table_class.relnamespace
            WHERE n.nspname = %s
            ORDER BY table_class.relname, constraint_row.conname
            """,
            (schema,),
        )
        constraint_records = cursor.fetchall()

        cursor.execute(
            """
            SELECT table_class.relname AS table_name,
                   pg_catalog.pg_get_indexdef(ix.indexrelid) AS definition
            FROM pg_catalog.pg_index ix
            JOIN pg_catalog.pg_class table_class ON table_class.oid = ix.indrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = table_class.relnamespace
            WHERE n.nspname = %s
            ORDER BY table_class.relname, ix.indexrelid
            """,
            (schema,),
        )
        index_records = cursor.fetchall()

        cursor.execute(
            """
            SELECT table_class.relname AS table_name,
                   trigger_row.tgname || ':' || pg_catalog.pg_get_triggerdef(trigger_row.oid, true) AS definition
            FROM pg_catalog.pg_trigger trigger_row
            JOIN pg_catalog.pg_class table_class ON table_class.oid = trigger_row.tgrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = table_class.relnamespace
            WHERE n.nspname = %s AND NOT trigger_row.tgisinternal
            ORDER BY table_class.relname, trigger_row.tgname
            """,
            (schema,),
        )
        trigger_records = cursor.fetchall()

        cursor.execute(
            """
            SELECT tablename AS table_name,
                   policyname || ':' || permissive || ':' || roles::text || ':' || cmd || ':' ||
                   coalesce(qual, '') || ':' || coalesce(with_check, '') AS definition
            FROM pg_catalog.pg_policies
            WHERE schemaname = %s
            ORDER BY tablename, policyname
            """,
            (schema,),
        )
        policy_records = cursor.fetchall()

        cursor.execute(
            """
            SELECT table_name,
                   grantee || ':' || privilege_type || ':' || is_grantable AS grant
            FROM information_schema.table_privileges
            WHERE table_schema = %s
            ORDER BY table_name, grantee, privilege_type
            """,
            (schema,),
        )
        grant_records = cursor.fetchall()

        cursor.execute("SELECT extname FROM pg_catalog.pg_extension ORDER BY extname")
        extensions = [str(row["extname"]) for row in cursor.fetchall()]

    return build_catalog_from_records(
        postgres_version_num=postgres_version_num,
        table_records=table_records,
        column_records=column_records,
        unique_index_records=unique_index_records,
        constraint_records=constraint_records,
        index_records=index_records,
        trigger_records=trigger_records,
        grant_records=grant_records,
        policy_records=policy_records,
        extensions=extensions,
    )


def fingerprint_table(
    connection: Any,
    table: TableCatalog,
    *,
    schema: str = "public",
    batch_size: int = 1000,
    scope_column: str | None = None,
    allow_unkeyed: bool = False,
) -> TableFingerprintManifest:
    from psycopg import sql
    from psycopg.rows import dict_row

    transfer_columns = [column.name for column in sorted(table.columns, key=lambda item: item.ordinal) if not column.generated]
    if not table.primary_key and not allow_unkeyed:
        raise ValueError(f"cannot fingerprint table without a primary key: {table.name}")
    if not table.primary_key and scope_column is not None:
        raise ValueError(f"cannot scope an unkeyed table fingerprint: {table.name}")
    query = sql.SQL("SELECT {} FROM {}.{}").format(
        sql.SQL(", ").join(sql.Identifier(name) for name in transfer_columns),
        sql.Identifier(schema),
        sql.Identifier(table.name),
    )
    if table.primary_key:
        query += sql.SQL(" ORDER BY {} ").format(sql.SQL(", ").join(sql.Identifier(name) for name in table.primary_key))
    cursor_name = f"deerflow_fp_{hashlib.sha1(table.name.encode(), usedforsecurity=False).hexdigest()[:12]}"
    with connection.cursor(name=cursor_name, row_factory=dict_row) as cursor:
        cursor.execute(query)
        cursor.itersize = batch_size
        records: Iterable[Mapping[str, Any]] = cursor
        if not table.primary_key:
            materialized = [dict(record) for record in cursor]
            materialized.sort(key=lambda record: _unkeyed_row_sort_key(record, transfer_columns))
            records = materialized
        return _fingerprint_ordered_records(
            table,
            records,
            transfer_columns=transfer_columns,
            scope_column=scope_column,
        )
