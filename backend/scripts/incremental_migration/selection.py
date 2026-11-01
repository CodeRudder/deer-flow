from __future__ import annotations

from datetime import UTC, datetime

from scripts.incremental_migration.domain import FileEntry, MigrationWindow
from scripts.incremental_migration.manifest import TableFingerprintManifest


def changed_source_thread_ids(*, source: TableFingerprintManifest, baseline: TableFingerprintManifest) -> set[str]:
    if source.table_name != baseline.table_name or source.primary_key != baseline.primary_key:
        raise ValueError("cannot compare row fingerprints from different table schemas")
    baseline_rows = {row.key_token: row.row_hash for row in baseline.rows}
    changed: set[str] = set()
    for row in source.rows:
        if not row.key:
            raise ValueError(f"table {source.table_name} has an empty primary key value")
        if baseline_rows.get(row.key_token) != row.row_hash:
            changed.add(row.scope_key or str(row.key[0]))
    return changed


def changed_file_thread_ids(
    *,
    source: tuple[FileEntry, ...],
    baseline: tuple[FileEntry, ...],
    window: MigrationWindow,
) -> set[str]:
    baseline_by_path = {entry.relative_path: entry for entry in baseline}
    changed: set[str] = set()
    for entry in source:
        target_entry = baseline_by_path.get(entry.relative_path)
        differs = target_entry is None or (target_entry.sha256 != entry.sha256 or target_entry.size != entry.size or target_entry.storage_class is not entry.storage_class)
        mtime = datetime.fromtimestamp(entry.mtime_ns / 1_000_000_000, tz=UTC)
        if differs or window.contains(mtime):
            changed.add(entry.thread_id)
    return changed
