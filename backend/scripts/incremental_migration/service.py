from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from scripts.incremental_migration.database import (
    DatabaseApplyResult,
    DatabasePreflightReport,
    SequenceState,
    apply_database_merge,
    copy_table_to_gzip,
    create_and_load_staging,
    drop_migration_schema,
    mark_database_verified,
    preflight_database_merge,
    read_database_marker,
    rollback_database_merge,
    staging_schema_name,
    verify_loaded_staging,
)
from scripts.incremental_migration.domain import (
    FileEntry,
    MigrationError,
    MigrationPhase,
    MigrationWindow,
    StorageClass,
    canonical_hash,
    sha256_file,
    validate_relative_path,
)
from scripts.incremental_migration.files import (
    FileMergeJournal,
    build_file_archives,
    merge_active_files,
    quarantine_runtime_controls,
    rollback_files,
    safe_extract_tar,
)
from scripts.incremental_migration.manifest import BaselineManifest, ExportManifest, TableFingerprintManifest
from scripts.incremental_migration.operations import (
    SourcePlan,
    assert_baseline_unchanged,
    build_source_plan,
    compare_baseline_snapshots,
    create_baseline_manifest,
    read_thread_owner_map,
    scan_home_files,
)
from scripts.incremental_migration.package import (
    ACTIVE_ARCHIVE_PATH,
    RUNTIME_ARCHIVE_PATH,
    build_package_archive,
    extract_package_archive,
    finalize_package_directory,
    package_content_sha256,
    verify_package_directory,
)
from scripts.incremental_migration.policy import TableRole, ordered_business_tables
from scripts.incremental_migration.postgres import (
    fingerprint_table,
    read_database_catalog,
    validate_catalog,
    validate_catalog_compatibility,
)
from scripts.incremental_migration.state import MigrationState, MigrationStateStore

STATE_FILE = "state.json"
FROZEN_BASELINE_FILE = "frozen-baseline.json"
PREFLIGHT_JOURNAL_FILE = "preflight-file-journal.json"
FILE_JOURNAL_FILE = "file-merge-journal.json"
RUNTIME_QUARANTINE_DIR = "target-runtime-quarantine"
FILE_BACKUP_DIR = "target-file-backup"
PACKAGE_DIR = "package"
STAGED_ACTIVE_DIR = "staged-active"
SOURCE_RUNTIME_ARCHIVE_DIR = "source-runtime-archive"
IMPORT_REPORT_FILE = "import-report.json"
LOCK_FILE = ".operation.lock"


class MigrationConflictError(MigrationError):
    def __init__(self, report: DatabasePreflightReport):
        self.report = report
        summary = ", ".join(f"{item.table_name}:{item.conflict_type}" for item in report.conflicts)
        super().__init__(f"database import is blocked by conflicts: {summary}")


@dataclass(frozen=True)
class TargetPlanResult:
    migration_id: str
    applied: bool
    runtime_control_count: int
    table_count: int
    row_count: int
    file_count: int
    baseline_path: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "migration_id": self.migration_id,
            "applied": self.applied,
            "runtime_control_count": self.runtime_control_count,
            "table_count": self.table_count,
            "row_count": self.row_count,
            "file_count": self.file_count,
            "baseline_path": self.baseline_path,
        }


@dataclass(frozen=True)
class SourcePlanResult:
    selected_thread_count: int
    selected_thread_digest: str
    activity_thread_count: int
    changed_database_thread_count: int
    changed_file_thread_count: int
    selected_file_count: int
    selected_file_bytes: int
    table_rows: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_thread_count": self.selected_thread_count,
            "selected_thread_digest": self.selected_thread_digest,
            "activity_thread_count": self.activity_thread_count,
            "changed_database_thread_count": self.changed_database_thread_count,
            "changed_file_thread_count": self.changed_file_thread_count,
            "selected_file_count": self.selected_file_count,
            "selected_file_bytes": self.selected_file_bytes,
            "table_rows": dict(self.table_rows),
        }


@dataclass(frozen=True)
class ExportResult:
    export_id: str
    package_path: str
    selected_thread_count: int
    table_count: int
    database_row_count: int
    file_count: int
    file_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "export_id": self.export_id,
            "package_path": self.package_path,
            "selected_thread_count": self.selected_thread_count,
            "table_count": self.table_count,
            "database_row_count": self.database_row_count,
            "file_count": self.file_count,
            "file_bytes": self.file_bytes,
        }


@dataclass(frozen=True)
class FileMergePreview:
    created: int
    replaced: int
    unchanged: int
    runtime_archived: int

    def to_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "replaced": self.replaced,
            "unchanged": self.unchanged,
            "runtime_archived": self.runtime_archived,
        }


@dataclass(frozen=True)
class ImportResult:
    export_id: str
    applied: bool
    phase: str
    database: DatabasePreflightReport
    files: FileMergePreview
    credential_key_confirmation_required: bool

    def to_dict(self, *, include_conflict_rows: bool = False) -> dict[str, Any]:
        conflicts = []
        for conflict in self.database.conflicts:
            item: dict[str, Any] = {
                "table_name": conflict.table_name,
                "conflict_type": conflict.conflict_type,
                "row_count": len(conflict.rows),
            }
            if include_conflict_rows:
                item["rows"] = list(conflict.rows)
            conflicts.append(item)
        return {
            "export_id": self.export_id,
            "applied": self.applied,
            "phase": self.phase,
            "database": {
                "tables": [
                    {
                        "table_name": table.table_name,
                        "insert_count": table.insert_count,
                        "update_count": table.update_count,
                        "same_count": table.same_count,
                        "target_only_count": table.target_only_count,
                    }
                    for table in self.database.tables
                ],
                "conflicts": conflicts,
                "warnings": [
                    {
                        "table_name": warning.table_name,
                        "warning_type": warning.conflict_type,
                        "row_count": len(warning.rows),
                    }
                    for warning in self.database.warnings
                ],
            },
            "files": self.files.to_dict(),
            "credential_key_confirmation_required": self.credential_key_confirmation_required,
        }


@dataclass(frozen=True)
class VerificationResult:
    export_id: str
    table_count: int
    row_count: int
    active_file_count: int
    runtime_archive_file_count: int
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "export_id": self.export_id,
            "table_count": self.table_count,
            "row_count": self.row_count,
            "active_file_count": self.active_file_count,
            "runtime_archive_file_count": self.runtime_archive_file_count,
            "verified": self.verified,
        }


def ensure_private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError(f"migration work directory cannot be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, indent=2, default=str).encode("utf-8") + b"\n"
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


@contextmanager
def operation_lock(state_dir: Path) -> Iterator[None]:
    ensure_private_directory(state_dir)
    lock_path = state_dir / LOCK_FILE
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MigrationError(f"another migration process is using {state_dir}") from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _require_services_stopped(confirmed: bool) -> None:
    if not confirmed:
        raise ValueError("this operation requires --confirm-services-stopped")


def _ensure_state_outside_target_home(*, state_dir: Path, target_home: Path) -> None:
    if state_dir.resolve().is_relative_to(target_home.resolve()):
        raise ValueError("migration state directory must be outside DEER_FLOW_HOME")


def _set_transaction(connection: Any, *, read_only: bool, serializable: bool = False) -> None:
    isolation = "SERIALIZABLE" if serializable else "REPEATABLE READ"
    suffix = " READ ONLY" if read_only else ""
    with connection.cursor() as cursor:
        cursor.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}{suffix}")


def _assert_no_active_runs(connection: Any) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM public.runs WHERE lower(status) IN ('pending', 'running')")
        active = int(cursor.fetchone()[0])
    if active:
        raise MigrationError(f"database still has {active} pending or running run(s)")


def _database_identity(connection: Any) -> dict[str, str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), (pg_catalog.pg_control_system()).system_identifier::text")
        database_name, system_identifier = cursor.fetchone()
    return {
        "database_name": str(database_name),
        "system_identifier": str(system_identifier),
    }


def _assert_database_matches_state(connection: Any, *, state: MigrationState) -> None:
    expected = state.details.get("target_database_identity")
    if not isinstance(expected, dict):
        raise ValueError("migration state does not record the frozen target database identity")
    if _database_identity(connection) != expected:
        raise ValueError("connected PostgreSQL database differs from the frozen baseline operation")


def _lock_public_tables(connection: Any, catalog: Any) -> None:
    from psycopg import sql

    roles = validate_catalog(catalog)
    business = ordered_business_tables(roles)
    bookkeeping = tuple(sorted(name for name, role in roles.items() if role is TableRole.BOOKKEEPING))
    names = (*business, *bookkeeping)
    if not names:
        return
    relations = sql.SQL(", ").join(sql.SQL("{}.{}").format(sql.Identifier("public"), sql.Identifier(name)) for name in names)
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(relations))


def _load_journal(path: Path, *, export_id: str) -> FileMergeJournal:
    if not path.exists():
        return FileMergeJournal(export_id=export_id)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid file journal: {path}")
    journal = FileMergeJournal.from_dict(value)
    if journal.export_id != export_id:
        raise ValueError(f"file journal belongs to a different export: {path}")
    return journal


def _state_store(state_dir: Path) -> MigrationStateStore:
    return MigrationStateStore(state_dir / STATE_FILE)


def _load_state(state_dir: Path, *, export_id: str | None = None) -> MigrationState:
    state = _state_store(state_dir).load()
    if export_id is not None and state.export_id != export_id:
        raise ValueError("migration state belongs to a different export")
    return state


def _load_frozen_baseline(state_dir: Path, state: MigrationState) -> BaselineManifest:
    path = state_dir / FROZEN_BASELINE_FILE
    baseline = BaselineManifest.read(path)
    if baseline.baseline_id != state.export_id:
        raise ValueError("frozen baseline id does not match migration state")
    expected_hash = state.details.get("baseline_sha256")
    if expected_hash and sha256_file(path) != expected_hash:
        raise ValueError("target-side frozen baseline checksum changed")
    return baseline


def _assert_target_home_matches_state(*, state: MigrationState, target_home: Path) -> None:
    expected_home = state.details.get("target_home")
    if expected_home is None:
        raise ValueError("migration state does not record the frozen target DEER_FLOW_HOME")
    if Path(str(expected_home)).resolve() != target_home.resolve():
        raise ValueError("target DEER_FLOW_HOME differs from the frozen baseline operation")


def _package_identity(extracted: Any) -> str:
    return package_content_sha256(extracted.root)


def _assert_package_identity(*, state: MigrationState, extracted: Any) -> str:
    identity = _package_identity(extracted)
    expected = state.details.get("package_identity")
    if expected is not None and expected != identity:
        raise ValueError("migration package differs from the package already bound to this operation")
    return identity


def _package_marker_details(extracted: Any) -> dict[str, Any]:
    return {
        "package_content_sha256": _package_identity(extracted),
        "window": extracted.manifest.window.to_dict(),
        "source_environment": extracted.manifest.source_environment,
        "target_environment": extracted.manifest.target_environment,
    }


def _assert_database_marker_package(marker: Any, extracted: Any) -> None:
    expected = _package_marker_details(extracted)
    actual = {key: marker.details.get(key) for key in expected}
    if actual != expected:
        raise ValueError("migration database marker does not match the retained package")


def _assert_import_apply_phase(state: MigrationState) -> None:
    allowed_phases = {
        MigrationPhase.BASELINE_FROZEN,
        MigrationPhase.STAGED,
        MigrationPhase.DB_APPLYING,
        MigrationPhase.DB_COMMITTED,
        MigrationPhase.FILES_APPLYING,
        MigrationPhase.FILES_APPLIED,
        MigrationPhase.VERIFYING,
    }
    if state.phase not in allowed_phases:
        raise ValueError(f"import apply is not valid in phase {state.phase.value}")


def _source_plan_result(plan: SourcePlan) -> SourcePlanResult:
    return SourcePlanResult(
        selected_thread_count=len(plan.selected_thread_ids),
        selected_thread_digest=canonical_hash(plan.selected_thread_ids),
        activity_thread_count=len(plan.activity_thread_ids),
        changed_database_thread_count=len(plan.changed_database_thread_ids),
        changed_file_thread_count=len(plan.changed_file_thread_ids),
        selected_file_count=len(plan.selected_files),
        selected_file_bytes=sum(entry.size for entry in plan.selected_files),
        table_rows=tuple((table.table_name, table.row_count) for table in plan.table_fingerprints),
    )


def prepare_target_baseline(
    connection: Any,
    *,
    target_home: Path,
    state_dir: Path,
    environment: str,
    window: MigrationWindow,
    source_environment: str,
    baseline_output: Path | None = None,
    migration_id: str | None = None,
    owner_overrides: dict[str, str] | None = None,
    apply: bool = False,
    confirm_services_stopped: bool = False,
) -> TargetPlanResult:
    migration_id = migration_id or str(uuid.uuid4())
    if apply:
        _require_services_stopped(confirm_services_stopped)
        _ensure_state_outside_target_home(state_dir=state_dir, target_home=target_home)

    connection.rollback()
    with connection.transaction():
        _set_transaction(connection, read_only=True)
        _assert_no_active_runs(connection)
        owners = read_thread_owner_map(connection, overrides=owner_overrides)
        files = scan_home_files(home=target_home, owner_by_thread=owners, require_all_owners=True)
        runtime_entries = [entry for entry in files if entry.storage_class is StorageClass.RUNTIME_ARCHIVE]
        quarantine_runtime_controls(
            home=target_home,
            quarantine_home=state_dir / RUNTIME_QUARANTINE_DIR,
            entries=list(files),
            journal=FileMergeJournal(export_id=migration_id),
            apply=False,
        )
        if not apply:
            baseline = create_baseline_manifest(
                connection=connection,
                home=target_home,
                environment=environment,
                owner_overrides=owner_overrides,
                baseline_id=migration_id,
                exclude_runtime_controls=True,
            )

    if not apply:
        return TargetPlanResult(
            migration_id=migration_id,
            applied=False,
            runtime_control_count=len(runtime_entries),
            table_count=len(baseline.tables),
            row_count=sum(table.row_count for table in baseline.tables),
            file_count=len(baseline.files),
            baseline_path=None,
        )

    with operation_lock(state_dir):
        store = _state_store(state_dir)
        target_database_identity = _database_identity(connection)
        state = store.create(
            migration_id,
            details={
                "environment": environment,
                "target_home": str(target_home.resolve()),
                "target_database_identity": target_database_identity,
                "expected_window": window.to_dict(),
                "expected_source_environment": source_environment,
                "expected_target_environment": environment,
                "runtime_control_count": len(runtime_entries),
            },
        )
        preflight_journal_path = state_dir / PREFLIGHT_JOURNAL_FILE
        journal = FileMergeJournal(export_id=migration_id)
        journal.write(preflight_journal_path)
        quarantine_runtime_controls(
            home=target_home,
            quarantine_home=state_dir / RUNTIME_QUARANTINE_DIR,
            entries=list(files),
            journal=journal,
            apply=True,
            journal_path=preflight_journal_path,
        )

        connection.rollback()
        with connection.transaction():
            _set_transaction(connection, read_only=True)
            _assert_no_active_runs(connection)
            baseline = create_baseline_manifest(
                connection=connection,
                home=target_home,
                environment=environment,
                owner_overrides=owner_overrides,
                baseline_id=migration_id,
            )
        internal_path = state_dir / FROZEN_BASELINE_FILE
        baseline.write(internal_path)
        if baseline_output is not None and baseline_output.resolve() != internal_path.resolve():
            baseline.write(baseline_output)
        state = store.transition(
            MigrationPhase.BASELINE_FROZEN,
            details={
                "baseline_sha256": sha256_file(internal_path),
                "baseline_row_count": sum(table.row_count for table in baseline.tables),
                "baseline_file_count": len(baseline.files),
            },
        )
        return TargetPlanResult(
            migration_id=state.export_id,
            applied=True,
            runtime_control_count=len(runtime_entries),
            table_count=len(baseline.tables),
            row_count=sum(table.row_count for table in baseline.tables),
            file_count=len(baseline.files),
            baseline_path=str((baseline_output or internal_path).resolve()),
        )


def plan_source(
    connection: Any,
    *,
    source_home: Path,
    baseline: BaselineManifest,
    window: MigrationWindow,
    owner_overrides: dict[str, str] | None = None,
) -> SourcePlanResult:
    connection.rollback()
    with connection.transaction():
        _set_transaction(connection, read_only=True)
        _assert_no_active_runs(connection)
        plan = build_source_plan(
            connection=connection,
            home=source_home,
            baseline=baseline,
            window=window,
            owner_overrides=owner_overrides,
        )
    return _source_plan_result(plan)


def export_package(
    connection: Any,
    *,
    source_home: Path,
    baseline: BaselineManifest,
    window: MigrationWindow,
    output_path: Path,
    work_root: Path,
    source_environment: str,
    target_environment: str,
    owner_overrides: dict[str, str] | None = None,
    confirm_services_stopped: bool = False,
) -> ExportResult:
    _require_services_stopped(confirm_services_stopped)
    if output_path.exists():
        raise ValueError(f"migration package output already exists: {output_path}")
    ensure_private_directory(work_root)
    with tempfile.TemporaryDirectory(dir=work_root, prefix="export-") as raw_package_root:
        package_root = Path(raw_package_root)
        os.chmod(package_root, 0o700)
        connection.rollback()
        with connection.transaction():
            _set_transaction(connection, read_only=True)
            _assert_no_active_runs(connection)
            plan = build_source_plan(
                connection=connection,
                home=source_home,
                baseline=baseline,
                window=window,
                owner_overrides=owner_overrides,
            )
            roles = validate_catalog(plan.catalog)
            table_manifests = []
            fingerprints = plan.table_fingerprint_by_name
            selected_scope = set(plan.selected_thread_ids)
            for table_name in ordered_business_tables(roles):
                role = roles[table_name]
                fingerprint = fingerprints[table_name]
                if role is TableRole.THREAD:
                    fingerprint = fingerprint.for_scope_keys(selected_scope)
                data_path = f"db/{table_name}.copy.gz"
                table_manifests.append(
                    copy_table_to_gzip(
                        connection,
                        table=plan.catalog.table_by_name[table_name],
                        output_path=package_root / data_path,
                        data_path=data_path,
                        role=role,
                        source_payload_hash=fingerprint.payload_hash,
                        selected_thread_ids=plan.selected_thread_ids,
                    )
                )

            build_file_archives(
                home=source_home,
                entries=list(plan.selected_files),
                active_tar=package_root / ACTIVE_ARCHIVE_PATH,
                runtime_tar=package_root / RUNTIME_ARCHIVE_PATH,
            )
            selected_owners = {thread_id: plan.owner_by_thread[thread_id] for thread_id in plan.selected_thread_ids}
            manifest = ExportManifest.create(
                baseline_id=baseline.baseline_id,
                source_environment=source_environment,
                target_environment=target_environment,
                window=window,
                source_catalog=plan.catalog,
                selected_thread_ids=plan.selected_thread_ids,
                owner_by_thread=selected_owners,
                tables=tuple(table_manifests),
                files=plan.selected_files,
                export_id=baseline.baseline_id,
            )
        manifest = finalize_package_directory(package_root, manifest=manifest, baseline=baseline)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_temp = tempfile.mkstemp(dir=output_path.parent, prefix=f".{output_path.name}.")
        os.close(fd)
        temp_output = Path(raw_temp)
        temp_output.unlink()
        try:
            build_package_archive(package_root, temp_output)
            with temp_output.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temp_output, output_path)
            os.chmod(output_path, 0o600)
        finally:
            temp_output.unlink(missing_ok=True)

    return ExportResult(
        export_id=manifest.export_id,
        package_path=str(output_path.resolve()),
        selected_thread_count=len(manifest.selected_thread_ids),
        table_count=len(manifest.tables),
        database_row_count=sum(table.row_count for table in manifest.tables),
        file_count=len(manifest.files),
        file_bytes=sum(entry.size for entry in manifest.files),
    )


@contextmanager
def _open_package(package_path: Path, *, work_root: Path) -> Iterator[Any]:
    if package_path.is_dir():
        yield verify_package_directory(package_path)
        return
    ensure_private_directory(work_root)
    with tempfile.TemporaryDirectory(dir=work_root, prefix="package-check-") as raw:
        yield extract_package_archive(package_path, Path(raw))


def _assert_package_matches_state(*, extracted: Any, state_dir: Path, state: MigrationState) -> BaselineManifest:
    manifest = extracted.manifest
    if manifest.export_id != state.export_id or manifest.baseline_id != state.export_id:
        raise ValueError("migration package does not match target-side migration state")
    expected_window = state.details.get("expected_window")
    expected_source = state.details.get("expected_source_environment")
    expected_target = state.details.get("expected_target_environment")
    if expected_window is None or expected_source is None or expected_target is None:
        raise ValueError("migration state does not record the approved package policy")
    if manifest.window.to_dict() != expected_window:
        raise ValueError("migration package window differs from the approved window")
    if manifest.source_environment != expected_source or manifest.target_environment != expected_target:
        raise ValueError("migration package environments differ from the approved source and target")
    frozen = _load_frozen_baseline(state_dir, state)
    differences = compare_baseline_snapshots(expected=frozen, current=extracted.baseline)
    if differences:
        raise ValueError("package baseline does not match the target-side frozen baseline: " + "; ".join(differences))
    return frozen


def _capture_target_snapshot(
    connection: Any,
    *,
    target_home: Path,
    baseline: BaselineManifest,
    owner_overrides: dict[str, str] | None,
) -> BaselineManifest:
    return create_baseline_manifest(
        connection=connection,
        home=target_home,
        environment=baseline.environment,
        owner_overrides=owner_overrides,
        baseline_id=baseline.baseline_id,
    )


def _assert_target_unchanged(
    connection: Any,
    *,
    target_home: Path,
    baseline: BaselineManifest,
    owner_overrides: dict[str, str] | None,
) -> Any:
    current = _capture_target_snapshot(
        connection,
        target_home=target_home,
        baseline=baseline,
        owner_overrides=owner_overrides,
    )
    assert_baseline_unchanged(expected=baseline, current=current)
    return current.catalog


def _safe_target_path(home: Path, relative_path: str) -> Path:
    relative = validate_relative_path(relative_path)
    target = home.joinpath(*relative.parts)
    current = home
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"target path contains a symlink parent: {current}")
    try:
        target.resolve(strict=False).relative_to(home.resolve())
    except ValueError as exc:
        raise ValueError(f"target path escapes DEER_FLOW_HOME: {relative_path}") from exc
    return target


def plan_file_merge(*, target_home: Path, entries: tuple[FileEntry, ...]) -> FileMergePreview:
    created = replaced = unchanged = runtime_archived = 0
    for entry in entries:
        if entry.storage_class is StorageClass.RUNTIME_ARCHIVE:
            runtime_archived += 1
            continue
        target = _safe_target_path(target_home, entry.relative_path)
        if target.is_symlink():
            raise ValueError(f"target symlink is not allowed: {target}")
        if target.is_file() and target.stat().st_size == entry.size and sha256_file(target) == entry.sha256:
            if stat.S_IMODE(target.stat().st_mode) == entry.mode:
                unchanged += 1
            else:
                replaced += 1
        else:
            parent_file = False
            current = target.parent
            while current != target_home:
                if current.exists() and current.is_file():
                    parent_file = True
                    break
                current = current.parent
            if target.exists() or parent_file:
                replaced += 1
            else:
                created += 1
    return FileMergePreview(created=created, replaced=replaced, unchanged=unchanged, runtime_archived=runtime_archived)


def _credential_confirmation_required(manifest: ExportManifest) -> bool:
    return any(table.table_name == "channel_credentials" and table.row_count > 0 for table in manifest.tables)


def _schema_exists(connection: Any, schema: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regnamespace(%s)", (schema,))
        return cursor.fetchone()[0] is not None


def _preflight_in_transaction(
    connection: Any,
    *,
    manifest: ExportManifest,
    baseline: BaselineManifest,
    target_home: Path,
    package_root: Path,
    owner_overrides: dict[str, str] | None,
    persistent_staging: bool,
) -> tuple[DatabasePreflightReport, Any, str]:
    target_catalog = _assert_target_unchanged(
        connection,
        target_home=target_home,
        baseline=baseline,
        owner_overrides=owner_overrides,
    )
    validate_catalog_compatibility(manifest.source_catalog, target_catalog)
    schema = staging_schema_name(manifest.export_id)
    if persistent_staging:
        if not _schema_exists(connection, schema):
            raise ValueError(f"persistent staging schema is missing: {schema}")
    else:
        create_and_load_staging(
            connection,
            package_root=package_root,
            manifest=manifest,
            target_catalog=target_catalog,
            schema=schema,
        )
    verify_loaded_staging(
        connection,
        manifest=manifest,
        target_catalog=target_catalog,
        staging_schema=schema,
    )
    report = preflight_database_merge(
        connection,
        manifest=manifest,
        target_catalog=target_catalog,
        staging_schema=schema,
    )
    report = _reclassify_existing_relationship_anomalies(
        connection,
        report=report,
        manifest=manifest,
        baseline=baseline,
        target_catalog=target_catalog,
        staging_schema=schema,
    )
    return report, target_catalog, schema


def _reclassify_existing_relationship_anomalies(
    connection: Any,
    *,
    report: DatabasePreflightReport,
    manifest: ExportManifest,
    baseline: BaselineManifest,
    target_catalog: Any,
    staging_schema: str,
) -> DatabasePreflightReport:
    relationship_types = {
        "missing_parent_checkpoint": "checkpoints",
        "missing_checkpoint": "checkpoint_writes",
        "missing_channel_blob": "checkpoints",
    }
    relevant = [item for item in (*report.conflicts, *report.warnings) if item.conflict_type in relationship_types]
    if not relevant:
        return report
    source_fingerprints: dict[str, TableFingerprintManifest] = {}
    for table_name in set(relationship_types[item.conflict_type] for item in relevant):
        payload = next((item for item in manifest.tables if item.table_name == table_name), None)
        if payload is None:
            continue
        table = target_catalog.table_by_name[table_name]
        source_fingerprints[table_name] = fingerprint_table(
            connection,
            table,
            schema=staging_schema,
            scope_column="thread_id",
        )
    baseline_maps = {table_name: {row.key_token: row.row_hash for row in baseline.table_by_name[table_name].rows} for table_name in source_fingerprints if table_name in baseline.table_by_name}
    source_maps = {table_name: {row.key_token: row.row_hash for row in fingerprint.rows} for table_name, fingerprint in source_fingerprints.items()}

    def row_is_existing(item: Any, row: dict[str, Any]) -> bool:
        table_name = relationship_types.get(item.conflict_type)
        if table_name is None or table_name not in source_maps:
            return False
        if item.conflict_type in {"missing_parent_checkpoint", "missing_channel_blob"}:
            key = (row.get("thread_id"), row.get("checkpoint_ns"), row.get("checkpoint_id"))
        else:
            key = (
                row.get("thread_id"),
                row.get("checkpoint_ns"),
                row.get("checkpoint_id"),
                row.get("task_id"),
                row.get("idx"),
            )
        token = canonical_hash(key)
        return source_maps[table_name].get(token) == baseline_maps.get(table_name, {}).get(token)

    conflicts = []
    warnings = list(report.warnings)
    for item in report.conflicts:
        if item.conflict_type not in relationship_types:
            conflicts.append(item)
            continue
        existing_rows = tuple(row for row in item.rows if row_is_existing(item, row))
        new_rows = tuple(row for row in item.rows if not row_is_existing(item, row))
        if existing_rows:
            warnings.append(replace(item, rows=existing_rows))
        if new_rows:
            conflicts.append(replace(item, rows=new_rows))
    return replace(report, conflicts=tuple(conflicts), warnings=tuple(warnings))


def dry_run_import(
    connection: Any,
    *,
    package_path: Path,
    target_home: Path,
    state_dir: Path,
    work_root: Path,
    owner_overrides: dict[str, str] | None = None,
) -> ImportResult:
    _ensure_state_outside_target_home(state_dir=state_dir, target_home=target_home)
    with operation_lock(state_dir), _open_package(package_path, work_root=work_root) as extracted:
        state = _load_state(state_dir, export_id=extracted.manifest.export_id)
        _assert_target_home_matches_state(state=state, target_home=target_home)
        _assert_database_matches_state(connection, state=state)
        package_identity = _assert_package_identity(state=state, extracted=extracted)
        if state.phase not in {MigrationPhase.BASELINE_FROZEN, MigrationPhase.STAGED}:
            raise ValueError(f"import dry-run is not valid in phase {state.phase.value}")
        baseline = _assert_package_matches_state(extracted=extracted, state_dir=state_dir, state=state)
        connection.rollback()
        try:
            _set_transaction(connection, read_only=False)
            _assert_no_active_runs(connection)
            report, _, _ = _preflight_in_transaction(
                connection,
                manifest=extracted.manifest,
                baseline=baseline,
                target_home=target_home,
                package_root=extracted.root,
                owner_overrides=owner_overrides,
                persistent_staging=state.phase is MigrationPhase.STAGED,
            )
        finally:
            connection.rollback()
        files = plan_file_merge(target_home=target_home, entries=extracted.manifest.files)
        result = ImportResult(
            export_id=extracted.manifest.export_id,
            applied=False,
            phase=state.phase.value,
            database=report,
            files=files,
            credential_key_confirmation_required=_credential_confirmation_required(extracted.manifest),
        )
        report_value = result.to_dict(include_conflict_rows=True)
        report_value["package_content_sha256"] = package_identity
        _write_private_json(state_dir / IMPORT_REPORT_FILE, report_value)
        return result


def _persist_package(*, package_path: Path, state_dir: Path) -> Any:
    destination = state_dir / PACKAGE_DIR
    if destination.exists():
        try:
            persisted = verify_package_directory(destination)
        except Exception:
            shutil.rmtree(destination)
            persisted = None
        if persisted is not None:
            with _open_package(package_path, work_root=state_dir) as requested:
                if _package_identity(persisted) != _package_identity(requested):
                    raise ValueError("requested migration package differs from the package retained in state")
            return persisted
    try:
        if package_path.is_dir():
            verify_package_directory(package_path)
            shutil.copytree(package_path, destination)
            return verify_package_directory(destination)
        return extract_package_archive(package_path, destination)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _verify_staged_file_class(*, root: Path, entries: tuple[FileEntry, ...], storage_class: StorageClass) -> None:
    expected = {entry.relative_path: entry for entry in entries if entry.storage_class is storage_class}
    actual: set[str] = set()
    for path in sorted(root.rglob("*")):
        file_stat = path.lstat()
        if stat.S_ISDIR(file_stat.st_mode):
            continue
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"staged file tree contains an unsafe entry: {path}")
        relative = path.relative_to(root).as_posix()
        entry = expected.get(relative)
        if entry is None or file_stat.st_size != entry.size or sha256_file(path) != entry.sha256:
            raise ValueError(f"staged file does not match manifest: {relative}")
        actual.add(relative)
    if actual != set(expected):
        raise ValueError(f"staged {storage_class.value} file set does not match manifest")


def _extract_staged_files(*, extracted: Any, state_dir: Path) -> tuple[Path, Path]:
    active_root = state_dir / STAGED_ACTIVE_DIR
    runtime_root = state_dir / SOURCE_RUNTIME_ARCHIVE_DIR
    for root, archive, storage_class in (
        (active_root, extracted.root / ACTIVE_ARCHIVE_PATH, StorageClass.ACTIVE),
        (runtime_root, extracted.root / RUNTIME_ARCHIVE_PATH, StorageClass.RUNTIME_ARCHIVE),
    ):
        if root.exists():
            try:
                _verify_staged_file_class(root=root, entries=extracted.manifest.files, storage_class=storage_class)
                continue
            except Exception:
                shutil.rmtree(root)
        safe_extract_tar(archive, root)
        _verify_staged_file_class(root=root, entries=extracted.manifest.files, storage_class=storage_class)
    return active_root, runtime_root


def _check_file_capacity(*, package_path: Path, state_dir: Path, manifest: ExportManifest) -> None:
    package_bytes = package_path.stat().st_size if package_path.is_file() else sum(path.stat().st_size for path in package_path.rglob("*") if path.is_file())
    payload_bytes = sum(entry.size for entry in manifest.files)
    required = package_bytes * 2 + payload_bytes
    free = shutil.disk_usage(state_dir).free
    if free < required:
        raise MigrationError(f"migration work filesystem has insufficient free space: required_at_least={required}, free={free}")


def _stage_database(
    connection: Any,
    *,
    extracted: Any,
    baseline: BaselineManifest,
    target_home: Path,
    owner_overrides: dict[str, str] | None,
) -> str:
    schema = staging_schema_name(extracted.manifest.export_id)
    connection.rollback()
    with connection.transaction():
        _set_transaction(connection, read_only=False)
        target_catalog = read_database_catalog(connection)
        _lock_public_tables(connection, target_catalog)
        _assert_no_active_runs(connection)
        target_catalog = _assert_target_unchanged(
            connection,
            target_home=target_home,
            baseline=baseline,
            owner_overrides=owner_overrides,
        )
        validate_catalog_compatibility(extracted.manifest.source_catalog, target_catalog)
        create_and_load_staging(
            connection,
            package_root=extracted.root,
            manifest=extracted.manifest,
            target_catalog=target_catalog,
            schema=schema,
        )
        verify_loaded_staging(
            connection,
            manifest=extracted.manifest,
            target_catalog=target_catalog,
            staging_schema=schema,
        )
        from psycopg import sql

        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("UPDATE {}.{} SET details = %s::jsonb").format(sql.Identifier(schema), sql.Identifier("__migration_meta")),
                (json.dumps(_package_marker_details(extracted), sort_keys=True),),
            )
    return schema


def _recover_or_stage(
    connection: Any,
    *,
    extracted: Any,
    baseline: BaselineManifest,
    target_home: Path,
    state_dir: Path,
    state: MigrationState,
    owner_overrides: dict[str, str] | None,
) -> MigrationState:
    store = _state_store(state_dir)
    schema = staging_schema_name(extracted.manifest.export_id)
    if state.phase is not MigrationPhase.BASELINE_FROZEN:
        return state
    connection.rollback()
    with connection.transaction():
        schema_exists = _schema_exists(connection, schema)
        if schema_exists:
            marker = read_database_marker(connection, staging_schema=schema)
            if marker.export_id != extracted.manifest.export_id or marker.status != "staged":
                raise ValueError("cannot recover staging schema from target-side state")
            _assert_database_marker_package(marker, extracted)
            target_catalog = read_database_catalog(connection)
            verify_loaded_staging(
                connection,
                manifest=extracted.manifest,
                target_catalog=target_catalog,
                staging_schema=schema,
            )
    if not schema_exists:
        _stage_database(
            connection,
            extracted=extracted,
            baseline=baseline,
            target_home=target_home,
            owner_overrides=owner_overrides,
        )
    return store.transition(MigrationPhase.STAGED, details={"staging_schema": schema})


def _apply_database(
    connection: Any,
    *,
    extracted: Any,
    baseline: BaselineManifest,
    target_home: Path,
    state_dir: Path,
    state: MigrationState,
    owner_overrides: dict[str, str] | None,
) -> tuple[MigrationState, DatabaseApplyResult, DatabasePreflightReport]:
    store = _state_store(state_dir)
    schema = staging_schema_name(extracted.manifest.export_id)
    if state.phase is MigrationPhase.DB_APPLYING:
        connection.rollback()
        with connection.transaction():
            marker = read_database_marker(connection, staging_schema=schema)
            _assert_database_marker_package(marker, extracted)
        if marker.status == "db_committed":
            result = DatabaseApplyResult(
                tables=tuple(),
                sequence_states=tuple(SequenceState.from_dict(item) for item in marker.details.get("sequence_states", [])),
            )
            state = store.transition(MigrationPhase.DB_COMMITTED, details={"database_marker_recovered": True})
            connection.rollback()
            with connection.transaction():
                target_catalog = read_database_catalog(connection)
                report = preflight_database_merge(
                    connection,
                    manifest=extracted.manifest,
                    target_catalog=target_catalog,
                    staging_schema=schema,
                )
            return state, result, report
        if marker.status != "staged":
            raise ValueError(f"cannot resume database apply from marker status {marker.status}")

    connection.rollback()
    with connection.transaction():
        _set_transaction(connection, read_only=False, serializable=True)
        target_catalog = read_database_catalog(connection)
        _lock_public_tables(connection, target_catalog)
        _assert_no_active_runs(connection)
        target_catalog = _assert_target_unchanged(
            connection,
            target_home=target_home,
            baseline=baseline,
            owner_overrides=owner_overrides,
        )
        verify_loaded_staging(
            connection,
            manifest=extracted.manifest,
            target_catalog=target_catalog,
            staging_schema=schema,
        )
        report = preflight_database_merge(
            connection,
            manifest=extracted.manifest,
            target_catalog=target_catalog,
            staging_schema=schema,
        )
        report = _reclassify_existing_relationship_anomalies(
            connection,
            report=report,
            manifest=extracted.manifest,
            baseline=baseline,
            target_catalog=target_catalog,
            staging_schema=schema,
        )
        if report.has_conflicts:
            raise MigrationConflictError(report)
        if state.phase is MigrationPhase.STAGED:
            state = store.transition(MigrationPhase.DB_APPLYING)
        result = apply_database_merge(
            connection,
            manifest=extracted.manifest,
            target_catalog=target_catalog,
            staging_schema=schema,
        )
    state = store.transition(
        MigrationPhase.DB_COMMITTED,
        details={
            "database_tables": [{"table_name": item.table_name, "inserted": item.inserted, "updated": item.updated} for item in result.tables],
            "sequence_states": [item.to_dict() for item in result.sequence_states],
        },
    )
    return state, result, report


def _file_signature(entry: FileEntry) -> tuple[Any, ...]:
    return (entry.user_id, entry.thread_id, entry.layout, entry.size, entry.mtime_ns, entry.mode, entry.sha256, entry.storage_class.value)


def _assert_target_files_still_at_baseline(
    connection: Any,
    *,
    target_home: Path,
    baseline: BaselineManifest,
    owner_overrides: dict[str, str] | None,
) -> None:
    owners = read_thread_owner_map(connection, overrides=owner_overrides)
    current = scan_home_files(home=target_home, owner_by_thread=owners, require_all_owners=True)
    before = {entry.relative_path: _file_signature(entry) for entry in baseline.files}
    after = {entry.relative_path: _file_signature(entry) for entry in current}
    if before != after:
        raise MigrationError("target thread files changed after the frozen baseline")


def _verify_expected_overlay(
    connection: Any,
    *,
    extracted: Any,
    baseline: BaselineManifest,
    target_home: Path,
    state_dir: Path,
    owner_overrides: dict[str, str] | None,
) -> VerificationResult:
    schema = staging_schema_name(extracted.manifest.export_id)
    connection.rollback()
    with connection.transaction():
        _set_transaction(connection, read_only=True)
        _assert_no_active_runs(connection)
        current = _capture_target_snapshot(
            connection,
            target_home=target_home,
            baseline=baseline,
            owner_overrides=owner_overrides,
        )
        if current.catalog.structural_fingerprint != baseline.catalog.structural_fingerprint:
            raise MigrationError("public schema changed while verifying migration")
        if current.catalog.security_fingerprint != baseline.catalog.security_fingerprint:
            raise MigrationError("public schema security changed while verifying migration")
        verify_loaded_staging(
            connection,
            manifest=extracted.manifest,
            target_catalog=current.catalog,
            staging_schema=schema,
        )
        marker = read_database_marker(connection, staging_schema=schema)
        if marker.status not in {"db_committed", "verified"}:
            raise MigrationError(f"database marker is not committed: {marker.status}")
        source_fingerprints: dict[str, TableFingerprintManifest] = {}
        for payload in extracted.manifest.tables:
            table = current.catalog.table_by_name[payload.table_name]
            role = TableRole(payload.role)
            source_fingerprints[payload.table_name] = fingerprint_table(
                connection,
                table,
                schema=schema,
                scope_column="thread_id" if role is TableRole.THREAD else None,
            )
        postflight = preflight_database_merge(
            connection,
            manifest=extracted.manifest,
            target_catalog=current.catalog,
            staging_schema=schema,
        )
        postflight = _reclassify_existing_relationship_anomalies(
            connection,
            report=postflight,
            manifest=extracted.manifest,
            baseline=baseline,
            target_catalog=current.catalog,
            staging_schema=schema,
        )
        if postflight.has_conflicts or any(table.insert_count or table.update_count for table in postflight.tables):
            raise MigrationError("database still differs from the source staging payload")

    baseline_tables = baseline.table_by_name
    current_tables = current.table_by_name
    if set(baseline_tables) != set(current_tables):
        raise MigrationError("database table fingerprint set changed after import")
    for table_name, baseline_table in baseline_tables.items():
        expected_rows = {row.key_token: row.row_hash for row in baseline_table.rows}
        source_table = source_fingerprints.get(table_name)
        if source_table is not None:
            expected_rows.update({row.key_token: row.row_hash for row in source_table.rows})
        actual_rows = {row.key_token: row.row_hash for row in current_tables[table_name].rows}
        if expected_rows != actual_rows:
            raise MigrationError(f"database table does not match baseline plus source overlay: {table_name}")

    expected_files = {entry.relative_path: _file_signature(entry) for entry in baseline.files}
    for entry in extracted.manifest.files:
        if entry.storage_class is StorageClass.ACTIVE:
            expected_files[entry.relative_path] = _file_signature(entry)
    actual_files = {entry.relative_path: _file_signature(entry) for entry in current.files}
    if expected_files != actual_files:
        raise MigrationError("target files do not match baseline plus active source overlay")
    _verify_staged_file_class(
        root=state_dir / SOURCE_RUNTIME_ARCHIVE_DIR,
        entries=extracted.manifest.files,
        storage_class=StorageClass.RUNTIME_ARCHIVE,
    )
    return VerificationResult(
        export_id=extracted.manifest.export_id,
        table_count=len(current.tables),
        row_count=sum(table.row_count for table in current.tables),
        active_file_count=sum(entry.storage_class is StorageClass.ACTIVE for entry in extracted.manifest.files),
        runtime_archive_file_count=sum(entry.storage_class is StorageClass.RUNTIME_ARCHIVE for entry in extracted.manifest.files),
        verified=False,
    )


def _apply_import_locked(
    connection: Any,
    *,
    package_path: Path,
    target_home: Path,
    state_dir: Path,
    owner_overrides: dict[str, str] | None,
    confirm_credential_key_compatible: bool,
) -> ImportResult:
    extracted = _persist_package(package_path=package_path, state_dir=state_dir)
    state = _load_state(state_dir, export_id=extracted.manifest.export_id)
    _assert_target_home_matches_state(state=state, target_home=target_home)
    _assert_database_matches_state(connection, state=state)
    _assert_import_apply_phase(state)
    package_identity = _assert_package_identity(state=state, extracted=extracted)
    baseline = _assert_package_matches_state(extracted=extracted, state_dir=state_dir, state=state)
    if "package_identity" not in state.details:
        state = _state_store(state_dir).record_details({"package_identity": package_identity})
    if _credential_confirmation_required(extracted.manifest) and not confirm_credential_key_compatible:
        raise ValueError("channel credential rows require --confirm-credential-key-compatible before apply")
    _check_file_capacity(package_path=package_path, state_dir=state_dir, manifest=extracted.manifest)
    active_root, _ = _extract_staged_files(extracted=extracted, state_dir=state_dir)
    state = _recover_or_stage(
        connection,
        extracted=extracted,
        baseline=baseline,
        target_home=target_home,
        state_dir=state_dir,
        state=state,
        owner_overrides=owner_overrides,
    )

    if state.phase in {MigrationPhase.STAGED, MigrationPhase.DB_APPLYING}:
        state, _, report = _apply_database(
            connection,
            extracted=extracted,
            baseline=baseline,
            target_home=target_home,
            state_dir=state_dir,
            state=state,
            owner_overrides=owner_overrides,
        )
    else:
        connection.rollback()
        with connection.transaction():
            target_catalog = read_database_catalog(connection)
            report = preflight_database_merge(
                connection,
                manifest=extracted.manifest,
                target_catalog=target_catalog,
                staging_schema=staging_schema_name(extracted.manifest.export_id),
            )

    store = _state_store(state_dir)
    if state.phase is MigrationPhase.DB_COMMITTED:
        connection.rollback()
        with connection.transaction():
            _set_transaction(connection, read_only=True)
            _assert_target_files_still_at_baseline(
                connection,
                target_home=target_home,
                baseline=baseline,
                owner_overrides=owner_overrides,
            )
        state = store.transition(MigrationPhase.FILES_APPLYING)

    if state.phase is MigrationPhase.FILES_APPLYING:
        journal_path = state_dir / FILE_JOURNAL_FILE
        journal = _load_journal(journal_path, export_id=extracted.manifest.export_id)
        if not journal_path.exists():
            journal.write(journal_path)
        stats = merge_active_files(
            staging_home=active_root,
            target_home=target_home,
            entries=list(extracted.manifest.files),
            backup_home=state_dir / FILE_BACKUP_DIR,
            journal=journal,
            journal_path=journal_path,
        )
        state = store.transition(
            MigrationPhase.FILES_APPLIED,
            details={
                "file_merge": {
                    "created": stats.created,
                    "replaced": stats.replaced,
                    "unchanged": stats.unchanged,
                    "runtime_archived": stats.archived,
                }
            },
        )

    verification = _verify_expected_overlay(
        connection,
        extracted=extracted,
        baseline=baseline,
        target_home=target_home,
        state_dir=state_dir,
        owner_overrides=owner_overrides,
    )
    if state.phase is MigrationPhase.FILES_APPLIED:
        state = store.transition(MigrationPhase.VERIFYING, details={"offline_verification": verification.to_dict()})
    files = plan_file_merge(target_home=target_home, entries=extracted.manifest.files)
    result = ImportResult(
        export_id=extracted.manifest.export_id,
        applied=True,
        phase=state.phase.value,
        database=report,
        files=files,
        credential_key_confirmation_required=_credential_confirmation_required(extracted.manifest),
    )
    report_value = result.to_dict(include_conflict_rows=True)
    report_value["package_content_sha256"] = package_identity
    _write_private_json(state_dir / IMPORT_REPORT_FILE, report_value)
    return result


def apply_import(
    connection: Any,
    *,
    package_path: Path,
    target_home: Path,
    state_dir: Path,
    owner_overrides: dict[str, str] | None = None,
    confirm_services_stopped: bool = False,
    confirm_credential_key_compatible: bool = False,
) -> ImportResult:
    _require_services_stopped(confirm_services_stopped)
    _ensure_state_outside_target_home(state_dir=state_dir, target_home=target_home)
    with operation_lock(state_dir):
        try:
            return _apply_import_locked(
                connection,
                package_path=package_path,
                target_home=target_home,
                state_dir=state_dir,
                owner_overrides=owner_overrides,
                confirm_credential_key_compatible=confirm_credential_key_compatible,
            )
        except MigrationConflictError:
            raise
        except Exception:
            try:
                connection.rollback()
                state = _load_state(state_dir)
                if state.phase in {
                    MigrationPhase.DB_APPLYING,
                    MigrationPhase.DB_COMMITTED,
                    MigrationPhase.FILES_APPLYING,
                    MigrationPhase.FILES_APPLIED,
                    MigrationPhase.VERIFYING,
                }:
                    _rollback_import_locked(
                        connection,
                        target_home=target_home,
                        state_dir=state_dir,
                        owner_overrides=owner_overrides,
                    )
            except Exception as rollback_error:
                raise MigrationError(f"import failed and automatic rollback also failed: {rollback_error}") from rollback_error
            raise


def verify_import(
    connection: Any,
    *,
    target_home: Path,
    state_dir: Path,
    owner_overrides: dict[str, str] | None = None,
    mark_verified: bool = False,
    confirm_services_stopped: bool = False,
    confirm_smoke_tests: bool = False,
) -> VerificationResult:
    if mark_verified:
        _require_services_stopped(confirm_services_stopped)
        if not confirm_smoke_tests:
            raise ValueError("marking VERIFIED requires --confirm-smoke-tests")
    _ensure_state_outside_target_home(state_dir=state_dir, target_home=target_home)
    with operation_lock(state_dir):
        state = _load_state(state_dir)
        _assert_target_home_matches_state(state=state, target_home=target_home)
        _assert_database_matches_state(connection, state=state)
        if state.phase not in {MigrationPhase.FILES_APPLIED, MigrationPhase.VERIFYING, MigrationPhase.VERIFIED}:
            raise ValueError(f"verification is not valid in phase {state.phase.value}")
        extracted = verify_package_directory(state_dir / PACKAGE_DIR)
        _assert_package_identity(state=state, extracted=extracted)
        baseline = _assert_package_matches_state(extracted=extracted, state_dir=state_dir, state=state)
        connection.rollback()
        with connection.transaction():
            marker = read_database_marker(connection, staging_schema=staging_schema_name(extracted.manifest.export_id))
            _assert_database_marker_package(marker, extracted)
        result = _verify_expected_overlay(
            connection,
            extracted=extracted,
            baseline=baseline,
            target_home=target_home,
            state_dir=state_dir,
            owner_overrides=owner_overrides,
        )
        if state.phase is MigrationPhase.FILES_APPLIED:
            state = _state_store(state_dir).transition(
                MigrationPhase.VERIFYING,
                details={"offline_verification": result.to_dict()},
            )
        if mark_verified and state.phase is not MigrationPhase.VERIFIED:
            connection.rollback()
            with connection.transaction():
                mark_database_verified(
                    connection,
                    manifest=extracted.manifest,
                    staging_schema=staging_schema_name(extracted.manifest.export_id),
                )
            state = _state_store(state_dir).transition(
                MigrationPhase.VERIFIED,
                details={"smoke_tests_confirmed": True, "verification": result.to_dict()},
            )
        return replace(result, verified=state.phase is MigrationPhase.VERIFIED)


def _verify_rollback(
    connection: Any,
    *,
    target_home: Path,
    state_dir: Path,
    state: MigrationState,
    owner_overrides: dict[str, str] | None,
) -> None:
    baseline_path = state_dir / FROZEN_BASELINE_FILE
    if not baseline_path.exists():
        return
    baseline = BaselineManifest.read(baseline_path)
    connection.rollback()
    with connection.transaction():
        _set_transaction(connection, read_only=True)
        current = _capture_target_snapshot(
            connection,
            target_home=target_home,
            baseline=baseline,
            owner_overrides=owner_overrides,
        )
    preflight = _load_journal(state_dir / PREFLIGHT_JOURNAL_FILE, export_id=state.export_id)
    restored_runtime = {entry.relative_path for entry in preflight.entries if entry.action == "quarantined"}
    filtered_current = replace(current, files=tuple(entry for entry in current.files if entry.relative_path not in restored_runtime))
    assert_baseline_unchanged(expected=baseline, current=filtered_current)
    for relative in restored_runtime:
        target = _safe_target_path(target_home, relative)
        backup = _safe_target_path(state_dir / RUNTIME_QUARANTINE_DIR, relative)
        if not target.is_file() or not backup.is_file() or sha256_file(target) != sha256_file(backup):
            raise MigrationError(f"restored runtime control failed rollback verification: {relative}")


def _rollback_import_locked(
    connection: Any,
    *,
    target_home: Path,
    state_dir: Path,
    owner_overrides: dict[str, str] | None,
) -> MigrationState:
    store = _state_store(state_dir)
    state = store.load()
    _assert_target_home_matches_state(state=state, target_home=target_home)
    _assert_database_matches_state(connection, state=state)
    if state.phase is MigrationPhase.ROLLED_BACK:
        return state
    if state.phase is MigrationPhase.VERIFIED:
        raise ValueError("VERIFIED is terminal; stop services and use the retained backups under an explicit recovery procedure")
    if state.phase is MigrationPhase.ROLLBACK_FAILED:
        state = store.transition(MigrationPhase.ROLLBACK_IN_PROGRESS)
    elif state.phase is not MigrationPhase.ROLLBACK_IN_PROGRESS:
        state = store.transition(MigrationPhase.ROLLBACK_IN_PROGRESS)
    try:
        merge_journal_path = state_dir / FILE_JOURNAL_FILE
        if merge_journal_path.exists():
            rollback_files(
                target_home=target_home,
                backup_home=state_dir / FILE_BACKUP_DIR,
                journal=_load_journal(merge_journal_path, export_id=state.export_id),
            )

        package_root = state_dir / PACKAGE_DIR
        schema = staging_schema_name(state.export_id)
        connection.rollback()
        with connection.transaction():
            if _schema_exists(connection, schema):
                marker = read_database_marker(connection, staging_schema=schema)
                if marker.export_id != state.export_id:
                    raise ValueError("database staging schema belongs to a different export")
                if marker.status == "db_committed":
                    if not package_root.exists():
                        raise ValueError("database rollback requires the retained migration package")
                    extracted = verify_package_directory(package_root)
                    _assert_package_identity(state=state, extracted=extracted)
                    _assert_database_marker_package(marker, extracted)
                    target_catalog = read_database_catalog(connection)
                    sequence_states = tuple(SequenceState.from_dict(item) for item in marker.details.get("sequence_states", []))
                    rollback_database_merge(
                        connection,
                        manifest=extracted.manifest,
                        target_catalog=target_catalog,
                        staging_schema=schema,
                        sequence_states=sequence_states,
                    )
                elif marker.status not in {"staged", "rolled_back"}:
                    raise ValueError(f"cannot rollback database marker status {marker.status}")
                drop_migration_schema(connection, schema, expected_export_id=state.export_id)

        preflight_path = state_dir / PREFLIGHT_JOURNAL_FILE
        if preflight_path.exists():
            rollback_files(
                target_home=target_home,
                backup_home=state_dir / RUNTIME_QUARANTINE_DIR,
                journal=_load_journal(preflight_path, export_id=state.export_id),
            )
        _verify_rollback(
            connection,
            target_home=target_home,
            state_dir=state_dir,
            state=state,
            owner_overrides=owner_overrides,
        )
        return store.transition(MigrationPhase.ROLLED_BACK)
    except Exception:
        connection.rollback()
        current = store.load()
        if current.phase is MigrationPhase.ROLLBACK_IN_PROGRESS:
            store.transition(MigrationPhase.ROLLBACK_FAILED)
        raise


def rollback_import(
    connection: Any,
    *,
    target_home: Path,
    state_dir: Path,
    owner_overrides: dict[str, str] | None = None,
    confirm_services_stopped: bool = False,
) -> MigrationState:
    _require_services_stopped(confirm_services_stopped)
    _ensure_state_outside_target_home(state_dir=state_dir, target_home=target_home)
    with operation_lock(state_dir):
        return _rollback_import_locked(
            connection,
            target_home=target_home,
            state_dir=state_dir,
            owner_overrides=owner_overrides,
        )
