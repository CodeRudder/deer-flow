from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scripts.incremental_migration.domain import canonical_hash
from scripts.incremental_migration.sql import BusinessKeySpec, ColumnSpec, TableSpec


@dataclass(frozen=True)
class ColumnCatalog:
    name: str
    type_name: str
    nullable: bool
    ordinal: int
    default: str | None = None
    identity: str = ""
    generated: str = ""
    collation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type_name": self.type_name,
            "nullable": self.nullable,
            "ordinal": self.ordinal,
            "default": self.default,
            "identity": self.identity,
            "generated": self.generated,
            "collation": self.collation,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ColumnCatalog:
        return cls(
            name=str(value["name"]),
            type_name=str(value["type_name"]),
            nullable=bool(value["nullable"]),
            ordinal=int(value["ordinal"]),
            default=str(value["default"]) if value.get("default") is not None else None,
            identity=str(value.get("identity", "")),
            generated=str(value.get("generated", "")),
            collation=str(value["collation"]) if value.get("collation") is not None else None,
        )

    def to_column_spec(self) -> ColumnSpec:
        return ColumnSpec(
            name=self.name,
            type_name=self.type_name,
            nullable=self.nullable,
            identity=self.identity,
            generated=self.generated,
        )


@dataclass(frozen=True)
class UniqueIndexCatalog:
    name: str
    columns: tuple[str, ...]
    primary: bool = False
    predicate: str | None = None
    nulls_not_distinct: bool = False
    definition: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": list(self.columns),
            "primary": self.primary,
            "predicate": self.predicate,
            "nulls_not_distinct": self.nulls_not_distinct,
            "definition": self.definition,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> UniqueIndexCatalog:
        return cls(
            name=str(value["name"]),
            columns=tuple(str(item) for item in value.get("columns", [])),
            primary=bool(value.get("primary", False)),
            predicate=str(value["predicate"]) if value.get("predicate") is not None else None,
            nulls_not_distinct=bool(value.get("nulls_not_distinct", False)),
            definition=str(value["definition"]) if value.get("definition") is not None else None,
        )


@dataclass(frozen=True)
class TableCatalog:
    name: str
    owner: str
    row_security: bool
    force_row_security: bool
    columns: tuple[ColumnCatalog, ...]
    primary_key: tuple[str, ...]
    unique_indexes: tuple[UniqueIndexCatalog, ...] = ()
    constraints: tuple[str, ...] = ()
    foreign_keys: tuple[str, ...] = ()
    indexes: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    policies: tuple[str, ...] = ()
    grants: tuple[str, ...] = ()

    def structural_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "row_security": self.row_security,
            "force_row_security": self.force_row_security,
            "columns": [column.to_dict() for column in sorted(self.columns, key=lambda item: (item.ordinal, item.name))],
            "primary_key": list(self.primary_key),
            "unique_indexes": [index.to_dict() for index in sorted(self.unique_indexes, key=lambda item: item.name)],
            "constraints": sorted(self.constraints),
            "foreign_keys": sorted(self.foreign_keys),
            "indexes": sorted(self.indexes),
            "triggers": sorted(self.triggers),
            "policies": sorted(self.policies),
        }

    def security_dict(self) -> dict[str, Any]:
        return {"name": self.name, "owner": self.owner, "grants": sorted(self.grants)}

    def to_dict(self) -> dict[str, Any]:
        return {**self.structural_dict(), **self.security_dict()}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TableCatalog:
        return cls(
            name=str(value["name"]),
            owner=str(value.get("owner", "")),
            row_security=bool(value.get("row_security", False)),
            force_row_security=bool(value.get("force_row_security", False)),
            columns=tuple(ColumnCatalog.from_dict(item) for item in value.get("columns", [])),
            primary_key=tuple(str(item) for item in value.get("primary_key", [])),
            unique_indexes=tuple(UniqueIndexCatalog.from_dict(item) for item in value.get("unique_indexes", [])),
            constraints=tuple(str(item) for item in value.get("constraints", [])),
            foreign_keys=tuple(str(item) for item in value.get("foreign_keys", [])),
            indexes=tuple(str(item) for item in value.get("indexes", [])),
            triggers=tuple(str(item) for item in value.get("triggers", [])),
            policies=tuple(str(item) for item in value.get("policies", [])),
            grants=tuple(str(item) for item in value.get("grants", [])),
        )

    @property
    def structural_fingerprint(self) -> str:
        return canonical_hash(self.structural_dict())

    def to_table_spec(self) -> TableSpec:
        business_keys = tuple(
            BusinessKeySpec(
                columns=index.columns,
                predicate=index.predicate,
                nulls_not_distinct=index.nulls_not_distinct,
            )
            for index in self.unique_indexes
            if not index.primary
        )
        return TableSpec(
            name=self.name,
            columns=tuple(column.to_column_spec() for column in sorted(self.columns, key=lambda item: item.ordinal)),
            primary_key=self.primary_key,
            business_keys=business_keys,
        )


@dataclass(frozen=True)
class DatabaseCatalog:
    postgres_version_num: int
    tables: tuple[TableCatalog, ...]
    extensions: tuple[str, ...] = ()

    @property
    def table_by_name(self) -> dict[str, TableCatalog]:
        return {table.name: table for table in self.tables}

    @property
    def structural_fingerprint(self) -> str:
        payload = {
            "tables": [table.structural_dict() for table in sorted(self.tables, key=lambda item: item.name)],
            "extensions": sorted(self.extensions),
        }
        return canonical_hash(payload)

    @property
    def security_fingerprint(self) -> str:
        payload = [table.security_dict() for table in sorted(self.tables, key=lambda item: item.name)]
        return canonical_hash(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "postgres_version_num": self.postgres_version_num,
            "structural_fingerprint": self.structural_fingerprint,
            "security_fingerprint": self.security_fingerprint,
            "tables": [table.to_dict() for table in sorted(self.tables, key=lambda item: item.name)],
            "extensions": sorted(self.extensions),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DatabaseCatalog:
        catalog = cls(
            postgres_version_num=int(value["postgres_version_num"]),
            tables=tuple(TableCatalog.from_dict(item) for item in value.get("tables", [])),
            extensions=tuple(str(item) for item in value.get("extensions", [])),
        )
        expected = value.get("structural_fingerprint")
        if expected and catalog.structural_fingerprint != expected:
            raise ValueError("database catalog structural fingerprint does not match its payload")
        return catalog
