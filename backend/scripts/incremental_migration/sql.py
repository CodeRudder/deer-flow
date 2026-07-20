from __future__ import annotations

from dataclasses import dataclass


def quote_identifier(value: str) -> str:
    if not value or "\x00" in value:
        raise ValueError("invalid PostgreSQL identifier")
    return '"' + value.replace('"', '""') + '"'


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type_name: str
    nullable: bool
    identity: str = ""
    generated: str = ""

    @property
    def transferable(self) -> bool:
        return not self.generated

    def difference_expression(self, *, target_alias: str = "t", source_alias: str = "s") -> str:
        name = quote_identifier(self.name)
        target = f"{target_alias}.{name}"
        source = f"{source_alias}.{name}"
        normalized_type = self.type_name.lower().strip()
        if normalized_type == "json":
            return f"{target}::jsonb IS DISTINCT FROM {source}::jsonb"
        if normalized_type == "xml":
            return f"{target}::text IS DISTINCT FROM {source}::text"
        return f"{target} IS DISTINCT FROM {source}"


@dataclass(frozen=True)
class BusinessKeySpec:
    columns: tuple[str, ...]
    predicate: str | None = None
    nulls_not_distinct: bool = False


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[ColumnSpec, ...]
    primary_key: tuple[str, ...]
    business_keys: tuple[BusinessKeySpec, ...] = ()

    def __post_init__(self) -> None:
        names = [column.name for column in self.columns]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate columns in table spec {self.name}")
        missing = set(self.primary_key) - set(names)
        if missing:
            raise ValueError(f"primary key columns missing from table spec {self.name}: {sorted(missing)}")
        if not self.primary_key:
            raise ValueError(f"table spec {self.name} requires a primary key")

    @property
    def column_by_name(self) -> dict[str, ColumnSpec]:
        return {column.name: column for column in self.columns}


@dataclass(frozen=True)
class MergeStatements:
    insert: str
    update: str
    duplicate_primary_keys: str
    counts: str


@dataclass(frozen=True)
class ConflictStatement:
    name: str
    sql: str


def _qualified(schema: str, table: str) -> str:
    return f"{quote_identifier(schema)}.{quote_identifier(table)}"


def _pk_join(table: TableSpec, *, target_alias: str = "t", source_alias: str = "s") -> str:
    return " AND ".join(f"{target_alias}.{quote_identifier(name)} = {source_alias}.{quote_identifier(name)}" for name in table.primary_key)


def build_merge_statements(table: TableSpec, *, staging_schema: str, target_schema: str = "public") -> MergeStatements:
    target = _qualified(target_schema, table.name)
    source = _qualified(staging_schema, table.name)
    transferable = [column for column in table.columns if column.transferable]
    insert_columns = ", ".join(quote_identifier(column.name) for column in transferable)
    select_columns = ", ".join(f"s.{quote_identifier(column.name)}" for column in transferable)
    identity_clause = " OVERRIDING SYSTEM VALUE" if any(column.identity for column in transferable) else ""
    pk_join = _pk_join(table)
    insert = f"INSERT INTO {target} ({insert_columns}){identity_clause} SELECT {select_columns} FROM {source} AS s WHERE NOT EXISTS (SELECT 1 FROM {target} AS t WHERE {pk_join})"

    update_columns = [column for column in transferable if column.name not in table.primary_key and not column.identity]
    if update_columns:
        assignments = ", ".join(f"{quote_identifier(column.name)} = s.{quote_identifier(column.name)}" for column in update_columns)
        differences = " OR ".join(column.difference_expression() for column in update_columns)
        update = f"UPDATE {target} AS t SET {assignments} FROM {source} AS s WHERE {pk_join} AND ({differences})"
    else:
        differences = "FALSE"
        update = ""

    pk_columns = ", ".join(quote_identifier(name) for name in table.primary_key)
    duplicate_primary_keys = f"SELECT {pk_columns}, count(*) AS duplicate_count FROM {source} GROUP BY {pk_columns} HAVING count(*) > 1"
    first_pk = quote_identifier(table.primary_key[0])
    counts = (
        "SELECT "
        f"count(*) FILTER (WHERE t.{first_pk} IS NULL) AS insert_count, "
        f"count(*) FILTER (WHERE t.{first_pk} IS NOT NULL AND ({differences})) AS update_count, "
        f"count(*) FILTER (WHERE t.{first_pk} IS NOT NULL AND NOT ({differences})) AS same_count "
        f"FROM {source} AS s LEFT JOIN {target} AS t ON {pk_join}"
    )
    return MergeStatements(insert=insert, update=update, duplicate_primary_keys=duplicate_primary_keys, counts=counts)


def build_business_conflict_statements(
    table: TableSpec,
    *,
    staging_schema: str,
    target_schema: str = "public",
) -> tuple[ConflictStatement, ...]:
    source = _qualified(staging_schema, table.name)
    target = _qualified(target_schema, table.name)
    statements: list[ConflictStatement] = []
    for index, business_key in enumerate(table.business_keys):
        missing = set(business_key.columns) - set(table.column_by_name)
        if missing:
            raise ValueError(f"business key columns missing from {table.name}: {sorted(missing)}")
        key_columns = ", ".join(quote_identifier(name) for name in business_key.columns)
        filters: list[str] = []
        if business_key.predicate:
            if ";" in business_key.predicate or "\x00" in business_key.predicate:
                raise ValueError(f"unsafe catalog predicate on {table.name}")
            filters.append(f"({business_key.predicate})")
        if not business_key.nulls_not_distinct:
            filters.extend(f"{quote_identifier(name)} IS NOT NULL" for name in business_key.columns)
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        duplicate_sql = f"SELECT {key_columns}, count(*) AS duplicate_count FROM {source}{where} GROUP BY {key_columns} HAVING count(*) > 1 LIMIT 100"
        statements.append(ConflictStatement(name=f"business_key_{index}_source_duplicates", sql=duplicate_sql))

        selected_columns = tuple(dict.fromkeys((*table.primary_key, *business_key.columns)))
        selected = ", ".join(quote_identifier(name) for name in selected_columns)
        if business_key.nulls_not_distinct:
            join = " AND ".join(f"s.{quote_identifier(name)} IS NOT DISTINCT FROM t.{quote_identifier(name)}" for name in business_key.columns)
        else:
            join = " AND ".join(f"s.{quote_identifier(name)} = t.{quote_identifier(name)}" for name in business_key.columns)
        different_pk = " OR ".join(f"s.{quote_identifier(name)} IS DISTINCT FROM t.{quote_identifier(name)}" for name in table.primary_key)
        source_pk = ", ".join(f"s.{quote_identifier(name)} AS {quote_identifier(f'source_{name}')}" for name in table.primary_key)
        target_pk = ", ".join(f"t.{quote_identifier(name)} AS {quote_identifier(f'target_{name}')}" for name in table.primary_key)
        collision_sql = f"WITH s AS (SELECT {selected} FROM {source}{where}), t AS (SELECT {selected} FROM {target}{where}) SELECT {source_pk}, {target_pk} FROM s JOIN t ON {join} WHERE ({different_pk}) LIMIT 100"
        statements.append(ConflictStatement(name=f"business_key_{index}_target_collision", sql=collision_sql))
    return tuple(statements)
