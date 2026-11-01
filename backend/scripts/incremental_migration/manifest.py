from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.incremental_migration.catalog import DatabaseCatalog
from scripts.incremental_migration.domain import (
    FileEntry,
    MigrationWindow,
    canonical_hash,
    canonical_json_bytes,
    validate_relative_path,
)

BASELINE_FORMAT_VERSION = 2
EXPORT_FORMAT_VERSION = 1


@dataclass(frozen=True)
class RowFingerprint:
    key: tuple[Any, ...]
    row_hash: str
    scope_key: str | None = None

    @property
    def key_token(self) -> str:
        return canonical_hash(self.key)

    def to_dict(self) -> dict[str, Any]:
        return {"key": list(self.key), "row_hash": self.row_hash, "scope_key": self.scope_key}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RowFingerprint:
        result = cls(
            key=tuple(value.get("key", [])),
            row_hash=str(value["row_hash"]),
            scope_key=str(value["scope_key"]) if value.get("scope_key") is not None else None,
        )
        return result


@dataclass(frozen=True)
class TableFingerprintManifest:
    table_name: str
    primary_key: tuple[str, ...]
    row_count: int
    payload_hash: str
    rows: tuple[RowFingerprint, ...]

    def for_scope_keys(self, scope_keys: set[str]) -> TableFingerprintManifest:
        rows = tuple(row for row in self.rows if row.scope_key in scope_keys)
        digest = hashlib.sha256()
        for row in rows:
            digest.update(canonical_json_bytes(row.to_dict()))
            digest.update(b"\n")
        return TableFingerprintManifest(
            table_name=self.table_name,
            primary_key=self.primary_key,
            row_count=len(rows),
            payload_hash=digest.hexdigest(),
            rows=rows,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_name": self.table_name,
            "primary_key": list(self.primary_key),
            "row_count": self.row_count,
            "payload_hash": self.payload_hash,
            "rows": [row.to_dict() for row in self.rows],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TableFingerprintManifest:
        rows = tuple(RowFingerprint.from_dict(item) for item in value.get("rows", []))
        result = cls(
            table_name=str(value["table_name"]),
            primary_key=tuple(str(item) for item in value.get("primary_key", [])),
            row_count=int(value["row_count"]),
            payload_hash=str(value["payload_hash"]),
            rows=rows,
        )
        if result.row_count != len(rows):
            raise ValueError(f"row count mismatch in baseline table {result.table_name}")
        return result


@dataclass(frozen=True)
class BaselineManifest:
    format_version: int
    baseline_id: str
    created_at_utc: str
    environment: str
    catalog: DatabaseCatalog
    tables: tuple[TableFingerprintManifest, ...]
    files: tuple[FileEntry, ...]

    @classmethod
    def create(
        cls,
        *,
        environment: str,
        catalog: DatabaseCatalog,
        tables: dict[str, TableFingerprintManifest],
        files: tuple[FileEntry, ...],
        baseline_id: str | None = None,
    ) -> BaselineManifest:
        return cls(
            format_version=BASELINE_FORMAT_VERSION,
            baseline_id=baseline_id or str(uuid.uuid4()),
            created_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            environment=environment,
            catalog=catalog,
            tables=tuple(sorted(tables.values(), key=lambda item: item.table_name)),
            files=tuple(sorted(files, key=lambda item: item.relative_path)),
        )

    @property
    def table_by_name(self) -> dict[str, TableFingerprintManifest]:
        return {table.table_name: table for table in self.tables}

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "baseline_id": self.baseline_id,
            "created_at_utc": self.created_at_utc,
            "environment": self.environment,
            "catalog": self.catalog.to_dict(),
            "tables": [table.to_dict() for table in self.tables],
            "files": [entry.to_dict() for entry in self.files],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BaselineManifest:
        format_version = int(value["format_version"])
        if format_version != BASELINE_FORMAT_VERSION:
            raise ValueError(f"unsupported baseline format version: {format_version}")
        return cls(
            format_version=format_version,
            baseline_id=str(value["baseline_id"]),
            created_at_utc=str(value["created_at_utc"]),
            environment=str(value["environment"]),
            catalog=DatabaseCatalog.from_dict(dict(value["catalog"])),
            tables=tuple(TableFingerprintManifest.from_dict(item) for item in value.get("tables", [])),
            files=tuple(FileEntry.from_dict(item) for item in value.get("files", [])),
        )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
        fd, raw_temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        temp_path = Path(raw_temp)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)

    @classmethod
    def read(cls, path: Path) -> BaselineManifest:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"invalid baseline manifest: {path}")
        return cls.from_dict(value)


@dataclass(frozen=True)
class TableExportManifest:
    table_name: str
    role: str
    scope: str
    data_path: str
    columns: tuple[str, ...]
    primary_key: tuple[str, ...]
    row_count: int
    raw_sha256: str
    source_payload_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_name": self.table_name,
            "role": self.role,
            "scope": self.scope,
            "data_path": self.data_path,
            "columns": list(self.columns),
            "primary_key": list(self.primary_key),
            "row_count": self.row_count,
            "raw_sha256": self.raw_sha256,
            "source_payload_hash": self.source_payload_hash,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TableExportManifest:
        data_path = validate_relative_path(str(value["data_path"])).as_posix()
        return cls(
            table_name=str(value["table_name"]),
            role=str(value["role"]),
            scope=str(value["scope"]),
            data_path=data_path,
            columns=tuple(str(item) for item in value.get("columns", [])),
            primary_key=tuple(str(item) for item in value.get("primary_key", [])),
            row_count=int(value["row_count"]),
            raw_sha256=str(value["raw_sha256"]),
            source_payload_hash=str(value["source_payload_hash"]),
        )


@dataclass(frozen=True)
class ExportManifest:
    format_version: int
    export_id: str
    baseline_id: str
    created_at_utc: str
    source_environment: str
    target_environment: str
    window: MigrationWindow
    source_catalog: DatabaseCatalog
    selected_thread_ids: tuple[str, ...]
    owner_by_thread: tuple[tuple[str, str], ...]
    tables: tuple[TableExportManifest, ...]
    files: tuple[FileEntry, ...]
    checksums: tuple[tuple[str, str], ...] = ()

    @classmethod
    def create(
        cls,
        *,
        baseline_id: str,
        source_environment: str,
        target_environment: str,
        window: MigrationWindow,
        source_catalog: DatabaseCatalog,
        selected_thread_ids: tuple[str, ...],
        owner_by_thread: dict[str, str],
        tables: tuple[TableExportManifest, ...],
        files: tuple[FileEntry, ...],
        export_id: str | None = None,
    ) -> ExportManifest:
        result = cls(
            format_version=EXPORT_FORMAT_VERSION,
            export_id=export_id or str(uuid.uuid4()),
            baseline_id=baseline_id,
            created_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            source_environment=source_environment,
            target_environment=target_environment,
            window=window,
            source_catalog=source_catalog,
            selected_thread_ids=tuple(sorted(selected_thread_ids)),
            owner_by_thread=tuple(sorted(owner_by_thread.items())),
            tables=tuple(sorted(tables, key=lambda item: item.table_name)),
            files=tuple(sorted(files, key=lambda item: item.relative_path)),
        )
        _validate_export_scope(result)
        return result

    @property
    def checksum_by_path(self) -> dict[str, str]:
        return dict(self.checksums)

    def with_checksums(self, checksums: dict[str, str]) -> ExportManifest:
        return replace(self, checksums=tuple(sorted(checksums.items())))

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "export_id": self.export_id,
            "baseline_id": self.baseline_id,
            "created_at_utc": self.created_at_utc,
            "source_environment": self.source_environment,
            "target_environment": self.target_environment,
            "window": self.window.to_dict(),
            "source_catalog": self.source_catalog.to_dict(),
            "selected_thread_ids": list(self.selected_thread_ids),
            "owner_by_thread": dict(self.owner_by_thread),
            "tables": [table.to_dict() for table in self.tables],
            "files": [entry.to_dict() for entry in self.files],
            "checksums": dict(self.checksums),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExportManifest:
        format_version = int(value["format_version"])
        if format_version != EXPORT_FORMAT_VERSION:
            raise ValueError(f"unsupported export format version: {format_version}")
        window_value = dict(value["window"])
        window = MigrationWindow.parse(str(window_value["start_local"]), str(window_value["end_local"]))
        if window.to_dict()["start_utc"] != window_value.get("start_utc") or window.to_dict()["end_utc"] != window_value.get("end_utc"):
            raise ValueError("export window UTC values do not match local values")
        result = cls(
            format_version=format_version,
            export_id=str(value["export_id"]),
            baseline_id=str(value["baseline_id"]),
            created_at_utc=str(value["created_at_utc"]),
            source_environment=str(value["source_environment"]),
            target_environment=str(value["target_environment"]),
            window=window,
            source_catalog=DatabaseCatalog.from_dict(dict(value["source_catalog"])),
            selected_thread_ids=tuple(str(item) for item in value.get("selected_thread_ids", [])),
            owner_by_thread=tuple(sorted((str(key), str(item)) for key, item in dict(value.get("owner_by_thread", {})).items())),
            tables=tuple(TableExportManifest.from_dict(item) for item in value.get("tables", [])),
            files=tuple(FileEntry.from_dict(item) for item in value.get("files", [])),
            checksums=tuple(sorted((str(key), str(item)) for key, item in dict(value.get("checksums", {})).items())),
        )
        _validate_export_scope(result)
        return result

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
        fd, raw_temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        temp_path = Path(raw_temp)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)

    @classmethod
    def read(cls, path: Path) -> ExportManifest:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"invalid export manifest: {path}")
        return cls.from_dict(value)


def _validate_export_scope(manifest: ExportManifest) -> None:
    selected = set(manifest.selected_thread_ids)
    owners = dict(manifest.owner_by_thread)
    if set(owners) != selected:
        raise ValueError("export owner map must exactly match selected thread IDs")
    if len(selected) != len(manifest.selected_thread_ids):
        raise ValueError("export contains duplicate selected thread IDs")
    seen_paths: set[str] = set()
    for entry in manifest.files:
        if entry.thread_id not in selected or entry.user_id != owners.get(entry.thread_id):
            raise ValueError(f"export file owner or thread is outside the selected scope: {entry.relative_path}")
        path = validate_relative_path(entry.relative_path)
        if entry.layout == "user":
            from deerflow.config.paths import make_safe_user_id

            expected_prefix = ("users", make_safe_user_id(str(entry.user_id)), "threads", entry.thread_id)
        elif entry.layout == "legacy":
            expected_prefix = ("threads", entry.thread_id)
        else:
            raise ValueError(f"unsupported export file layout: {entry.layout}")
        if path.parts[: len(expected_prefix)] != expected_prefix:
            raise ValueError(f"export file path does not match its owner/thread metadata: {entry.relative_path}")
        if entry.relative_path in seen_paths:
            raise ValueError(f"export contains duplicate file path: {entry.relative_path}")
        seen_paths.add(entry.relative_path)
