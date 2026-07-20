from __future__ import annotations

import json
import os
import shutil
import stat
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.incremental_migration.domain import FileEntry, StorageClass, sha256_file, validate_relative_path


@dataclass(frozen=True)
class ThreadRoot:
    root: Path
    user_id: str | None
    user_bucket: str | None
    thread_id: str
    layout: str


def discover_thread_roots(
    home: Path,
    *,
    owner_by_thread: dict[str, str | None] | None = None,
    require_legacy_owner: bool = False,
) -> list[ThreadRoot]:
    from deerflow.config.paths import make_safe_user_id

    home = home.resolve()
    owners = owner_by_thread or {}
    roots: list[ThreadRoot] = []
    seen_user_threads: dict[str, str] = {}

    users_dir = home / "users"
    if users_dir.exists():
        if users_dir.is_symlink() or not users_dir.is_dir():
            raise ValueError(f"users path is not a regular directory: {users_dir}")
        for bucket in sorted(users_dir.iterdir(), key=lambda item: item.name):
            if bucket.is_symlink():
                raise ValueError(f"user bucket symlink is not allowed: {bucket}")
            threads_dir = bucket / "threads"
            if not threads_dir.exists():
                continue
            if threads_dir.is_symlink() or not threads_dir.is_dir():
                raise ValueError(f"thread root is not a regular directory: {threads_dir}")
            for root in sorted(threads_dir.iterdir(), key=lambda item: item.name):
                if root.is_symlink() or not root.is_dir():
                    raise ValueError(f"thread entry is not a regular directory: {root}")
                thread_id = root.name
                previous_bucket = seen_user_threads.get(thread_id)
                if previous_bucket is not None and previous_bucket != bucket.name:
                    raise ValueError(f"thread {thread_id!r} exists in multiple user buckets")
                seen_user_threads[thread_id] = bucket.name
                owner = owners.get(thread_id)
                if owner is not None and make_safe_user_id(owner) != bucket.name:
                    raise ValueError(f"thread {thread_id!r} owner maps to a different user bucket")
                roots.append(
                    ThreadRoot(
                        root=root,
                        user_id=owner or bucket.name,
                        user_bucket=bucket.name,
                        thread_id=thread_id,
                        layout="user",
                    )
                )

    legacy_dir = home / "threads"
    if legacy_dir.exists():
        if legacy_dir.is_symlink() or not legacy_dir.is_dir():
            raise ValueError(f"legacy threads path is not a regular directory: {legacy_dir}")
        for root in sorted(legacy_dir.iterdir(), key=lambda item: item.name):
            if root.is_symlink() or not root.is_dir():
                raise ValueError(f"legacy thread entry is not a regular directory: {root}")
            owner = owners.get(root.name)
            if owner is None and require_legacy_owner:
                raise ValueError(f"legacy thread {root.name!r} has no owner mapping")
            roots.append(
                ThreadRoot(
                    root=root,
                    user_id=owner,
                    user_bucket=None,
                    thread_id=root.name,
                    layout="legacy",
                )
            )
    return sorted(roots, key=lambda item: (item.layout, item.thread_id, item.user_bucket or ""))


def classify_storage(path_within_thread: Path | PurePosixPath) -> StorageClass:
    path = PurePosixPath(path_within_thread.as_posix())
    name = path.name
    if len(path.parts) == 1 and (name == "background_commands.json" or (name.startswith("background_commands.json.") and name.endswith(".tmp"))):
        return StorageClass.RUNTIME_ARCHIVE
    if len(path.parts) == 1 and name.endswith(".lock"):
        return StorageClass.RUNTIME_ARCHIVE
    if len(path.parts) == 2 and path.parts[0] == "subagents" and name.endswith(".cancel"):
        return StorageClass.RUNTIME_ARCHIVE
    return StorageClass.ACTIVE


def scan_thread_root(
    *,
    home: Path,
    root: Path,
    user_id: str | None,
    thread_id: str,
    layout: str,
) -> list[FileEntry]:
    home = home.resolve()
    root = root.resolve()
    try:
        root.relative_to(home)
    except ValueError as exc:
        raise ValueError(f"thread root is outside migration home: {root}") from exc
    if not root.is_dir():
        return []

    entries: list[FileEntry] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode):
            raise ValueError(f"symlink is not allowed in a migration package: {path}")
        if stat.S_ISDIR(file_stat.st_mode):
            continue
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"special file is not allowed in a migration package: {path}")
        relative_within_thread = path.relative_to(root)
        entries.append(
            FileEntry.from_path(
                home=home,
                path=path,
                user_id=user_id,
                thread_id=thread_id,
                layout=layout,
                storage_class=classify_storage(relative_within_thread),
                stat=file_stat,
            )
        )
    return entries


def _write_archive(*, home: Path, entries: list[FileEntry], archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for entry in sorted(entries, key=lambda item: item.relative_path):
            relative = validate_relative_path(entry.relative_path).as_posix()
            if relative in seen:
                raise ValueError(f"duplicate file entry in migration archive: {relative}")
            seen.add(relative)
            source = home / Path(*PurePosixPath(relative).parts)
            file_stat = source.lstat()
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError(f"archive source is not a regular file: {source}")
            if file_stat.st_size != entry.size or file_stat.st_mtime_ns != entry.mtime_ns or stat.S_IMODE(file_stat.st_mode) != entry.mode or sha256_file(source) != entry.sha256:
                raise ValueError(f"file changed while building migration archive: {source}")
            info = tarfile.TarInfo(name=relative)
            info.size = file_stat.st_size
            info.mode = entry.mode
            info.mtime = file_stat.st_mtime
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            with source.open("rb") as handle:
                archive.addfile(info, handle)


def build_file_archives(*, home: Path, entries: list[FileEntry], active_tar: Path, runtime_tar: Path) -> None:
    by_path: dict[str, StorageClass] = {}
    for entry in entries:
        existing = by_path.get(entry.relative_path)
        if existing is not None:
            raise ValueError(f"file appears more than once in migration manifest: {entry.relative_path}")
        by_path[entry.relative_path] = entry.storage_class
    _write_archive(
        home=home,
        entries=[entry for entry in entries if entry.storage_class is StorageClass.ACTIVE],
        archive_path=active_tar,
    )
    _write_archive(
        home=home,
        entries=[entry for entry in entries if entry.storage_class is StorageClass.RUNTIME_ARCHIVE],
        archive_path=runtime_tar,
    )


def _archive_destination(destination: Path, member_name: str) -> Path:
    try:
        relative = validate_relative_path(member_name)
    except ValueError as exc:
        raise ValueError(f"unsafe archive path: {member_name!r}") from exc
    candidate = destination.joinpath(*relative.parts)
    try:
        candidate.resolve(strict=False).relative_to(destination.resolve())
    except ValueError as exc:
        raise ValueError(f"unsafe archive path: {member_name!r}") from exc
    return candidate


def safe_extract_tar(archive_path: Path, destination: Path) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    seen: set[str] = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            target = _archive_destination(destination, member.name)
            normalized = validate_relative_path(member.name).as_posix()
            if normalized in seen:
                raise ValueError(f"duplicate archive path: {normalized}")
            seen.add(normalized)
            if member.issym() or member.islnk():
                raise ValueError(f"archive links are not allowed: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError(f"archive special files are not allowed: {member.name}")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"archive file has no payload: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", delete=False) as temp:
                temp_path = Path(temp.name)
                shutil.copyfileobj(source, temp)
                temp.flush()
                os.fsync(temp.fileno())
            try:
                os.chmod(temp_path, member.mode & 0o777)
                os.replace(temp_path, target)
                mtime_ns = int(member.mtime * 1_000_000_000)
                os.utime(target, ns=(mtime_ns, mtime_ns))
            finally:
                temp_path.unlink(missing_ok=True)
            extracted.append(normalized)
    return extracted


@dataclass
class FileJournalEntry:
    relative_path: str
    action: str
    backup_relative_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "action": self.action,
            "backup_relative_path": self.backup_relative_path,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> FileJournalEntry:
        return cls(
            relative_path=str(value["relative_path"]),
            action=str(value["action"]),
            backup_relative_path=str(value["backup_relative_path"]) if value.get("backup_relative_path") else None,
        )


@dataclass
class FileMergeJournal:
    export_id: str
    entries: list[FileJournalEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"export_id": self.export_id, "entries": [entry.to_dict() for entry in self.entries]}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> FileMergeJournal:
        return cls(export_id=str(value["export_id"]), entries=[FileJournalEntry.from_dict(item) for item in value.get("entries", [])])

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), sort_keys=True, indent=2).encode("utf-8") + b"\n"
        _atomic_write(path, payload)


@dataclass
class FileMergeStats:
    created: int = 0
    replaced: int = 0
    unchanged: int = 0
    archived: int = 0


def _atomic_write(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temp_path = Path(raw_temp)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _copy_file_atomic(source: Path, target: Path, *, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", delete=False) as temp:
        temp_path = Path(temp.name)
        with source.open("rb") as source_handle:
            shutil.copyfileobj(source_handle, temp)
        temp.flush()
        os.fsync(temp.fileno())
    try:
        os.chmod(temp_path, mode)
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)


def _backup_path_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(dir=destination.parent, prefix=f".{destination.name}.backup."))
    payload = temp / "payload"
    try:
        if source.is_dir():
            shutil.copytree(source, payload)
        else:
            shutil.copy2(source, payload)
        os.replace(payload, destination)
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def _safe_join(home: Path, relative_path: str) -> Path:
    relative = validate_relative_path(relative_path)
    target = home.joinpath(*relative.parts)
    try:
        target.resolve(strict=False).relative_to(home.resolve())
    except ValueError as exc:
        raise ValueError(f"migration path escapes target home: {relative_path!r}") from exc
    current = home
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"migration path contains a symlink parent: {current}")
    return target


def _assert_regular_tree(path: Path) -> None:
    for child in path.rglob("*"):
        child_stat = child.lstat()
        if stat.S_ISLNK(child_stat.st_mode) or not (stat.S_ISDIR(child_stat.st_mode) or stat.S_ISREG(child_stat.st_mode)):
            raise ValueError(f"target directory contains an unsafe entry: {child}")


def _same_regular_file_state(left: Path, right: Path) -> bool:
    if not left.is_file() or not right.is_file():
        return False
    left_stat = left.stat()
    right_stat = right.stat()
    return left_stat.st_size == right_stat.st_size and left_stat.st_mtime_ns == right_stat.st_mtime_ns and stat.S_IMODE(left_stat.st_mode) == stat.S_IMODE(right_stat.st_mode) and sha256_file(left) == sha256_file(right)


def _prepare_target_parents(
    *,
    target_home: Path,
    backup_home: Path,
    relative_path: str,
    journal: FileMergeJournal,
    journal_path: Path | None,
    recorded: set[str],
) -> None:
    relative = validate_relative_path(relative_path)
    current = target_home
    for part in relative.parts[:-1]:
        current = current / part
        current_relative = current.relative_to(target_home).as_posix()
        if current.is_symlink():
            raise ValueError(f"target parent symlink is not allowed: {current}")
        if current.exists() and current.is_dir():
            continue
        if current_relative in recorded:
            raise ValueError(f"journal already records an unresolved parent operation for {current_relative}")
        if current.exists():
            if not current.is_file():
                raise ValueError(f"target parent is a special file: {current}")
            backup = _safe_join(backup_home, current_relative)
            if backup.exists():
                raise ValueError(f"rollback backup already exists: {backup}")
            _backup_path_atomic(current, backup)
            journal.entries.append(FileJournalEntry(current_relative, "replaced_parent_file", current_relative))
            if journal_path is not None:
                journal.write(journal_path)
            current.unlink()
            current.mkdir()
        else:
            journal.entries.append(FileJournalEntry(current_relative, "created_directory"))
            if journal_path is not None:
                journal.write(journal_path)
            current.mkdir()
        recorded.add(current_relative)


def merge_active_files(
    *,
    staging_home: Path,
    target_home: Path,
    entries: list[FileEntry],
    backup_home: Path,
    journal: FileMergeJournal,
    journal_path: Path | None = None,
) -> FileMergeStats:
    stats = FileMergeStats()
    recorded = {entry.relative_path for entry in journal.entries}
    for entry in sorted(entries, key=lambda item: item.relative_path):
        if entry.storage_class is not StorageClass.ACTIVE:
            stats.archived += 1
            continue
        source = _safe_join(staging_home, entry.relative_path)
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"staged migration file is missing or unsafe: {source}")
        if source.stat().st_size != entry.size or sha256_file(source) != entry.sha256:
            raise ValueError(f"staged migration file failed manifest validation: {source}")
        _prepare_target_parents(
            target_home=target_home,
            backup_home=backup_home,
            relative_path=entry.relative_path,
            journal=journal,
            journal_path=journal_path,
            recorded=recorded,
        )
        target = _safe_join(target_home, entry.relative_path)
        if target.is_symlink():
            raise ValueError(f"target symlink is not allowed: {target}")
        if target.is_file() and sha256_file(target) == entry.sha256 and stat.S_IMODE(target.stat().st_mode) == entry.mode:
            if target.stat().st_mtime_ns == entry.mtime_ns:
                stats.unchanged += 1
                continue
        if entry.relative_path in recorded:
            raise ValueError(f"journal already records a non-idempotent operation for {entry.relative_path}")

        if target.exists():
            backup = _safe_join(backup_home, entry.relative_path)
            if backup.exists():
                raise ValueError(f"rollback backup already exists: {backup}")
            if target.is_dir():
                _assert_regular_tree(target)
                _backup_path_atomic(target, backup)
                action = "replaced_directory"
            elif target.is_file():
                _backup_path_atomic(target, backup)
                action = "replaced"
            else:
                raise ValueError(f"target special file is not allowed: {target}")
            journal.entries.append(FileJournalEntry(entry.relative_path, action, entry.relative_path))
            if journal_path is not None:
                journal.write(journal_path)
            if target.is_dir():
                shutil.rmtree(target)
            stats.replaced += 1
        else:
            journal.entries.append(FileJournalEntry(entry.relative_path, "created"))
            if journal_path is not None:
                journal.write(journal_path)
            stats.created += 1
        recorded.add(entry.relative_path)
        _copy_file_atomic(source, target, mode=entry.mode)
        os.utime(target, ns=(entry.mtime_ns, entry.mtime_ns))
        if journal_path is not None:
            journal.write(journal_path)
    return stats


def _assert_no_running_background_commands(path: Path) -> None:
    if path.name != "background_commands.json":
        return
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot validate background command state: {path}") from exc
    commands = value.get("commands", []) if isinstance(value, dict) else []
    if not isinstance(commands, list):
        raise ValueError(f"invalid background command registry: {path}")
    terminal = {"completed", "failed", "killed", "timed_out"}
    for command in commands:
        if not isinstance(command, dict):
            raise ValueError(f"invalid background command entry found in {path}")
        status_value = str(command.get("status", "")).lower()
        if status_value == "running":
            raise ValueError(f"running background command found in {path}")
        if status_value not in terminal:
            raise ValueError(f"background command with unknown status found in {path}")


def quarantine_runtime_controls(
    *,
    home: Path,
    quarantine_home: Path,
    entries: list[FileEntry],
    journal: FileMergeJournal,
    apply: bool,
    journal_path: Path | None = None,
) -> FileMergeStats:
    stats = FileMergeStats()
    recorded = {entry.relative_path for entry in journal.entries}
    runtime_entries = sorted(
        (entry for entry in entries if entry.storage_class is StorageClass.RUNTIME_ARCHIVE),
        key=lambda item: item.relative_path,
    )
    for entry in runtime_entries:
        source = _safe_join(home, entry.relative_path)
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"runtime control file is missing or unsafe: {source}")
        if sha256_file(source) != entry.sha256:
            raise ValueError(f"runtime control file changed during preflight: {source}")
        _assert_no_running_background_commands(source)
        stats.archived += 1
        if not apply:
            continue
        if entry.relative_path in recorded:
            raise ValueError(f"runtime control already appears in preflight journal: {entry.relative_path}")
        quarantine = _safe_join(quarantine_home, entry.relative_path)
        if quarantine.exists():
            raise ValueError(f"runtime quarantine target already exists: {quarantine}")
        _backup_path_atomic(source, quarantine)
        if sha256_file(quarantine) != entry.sha256:
            quarantine.unlink(missing_ok=True)
            raise ValueError(f"runtime quarantine copy failed checksum validation: {quarantine}")
        journal.entries.append(FileJournalEntry(entry.relative_path, "quarantined", entry.relative_path))
        recorded.add(entry.relative_path)
        if journal_path is not None:
            journal.write(journal_path)
        source.unlink()
        if journal_path is not None:
            journal.write(journal_path)
    return stats


def rollback_files(*, target_home: Path, backup_home: Path, journal: FileMergeJournal) -> None:
    for entry in reversed(journal.entries):
        target = _safe_join(target_home, entry.relative_path)
        if target.is_symlink():
            raise ValueError(f"refusing to rollback over a symlink: {target}")
        if entry.action == "created":
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
            continue
        if entry.action == "created_directory":
            if not target.exists():
                continue
            if not target.is_dir():
                raise ValueError(f"cannot remove a created directory replaced by another file: {target}")
            try:
                target.rmdir()
            except OSError as exc:
                raise ValueError(f"cannot remove non-empty migration-created directory: {target}") from exc
            continue
        if entry.action == "quarantined":
            if not entry.backup_relative_path:
                raise ValueError(f"quarantine rollback has no backup path: {entry.relative_path}")
            backup = _safe_join(backup_home, entry.backup_relative_path)
            if not backup.is_file():
                raise ValueError(f"runtime quarantine backup is missing: {backup}")
            if target.exists():
                if _same_regular_file_state(target, backup):
                    continue
                raise ValueError(f"cannot restore runtime control over an existing target: {target}")
            _copy_file_atomic(backup, target, mode=stat.S_IMODE(backup.stat().st_mode))
            continue
        if entry.action not in {"replaced", "replaced_directory", "replaced_parent_file"} or not entry.backup_relative_path:
            raise ValueError(f"unknown file rollback action: {entry.action}")
        backup = _safe_join(backup_home, entry.backup_relative_path)
        if not backup.exists():
            raise ValueError(f"rollback backup is missing: {backup}")
        if _same_regular_file_state(target, backup):
            continue
        if target.is_dir():
            if entry.action == "replaced_parent_file":
                try:
                    target.rmdir()
                except OSError as exc:
                    raise ValueError(f"cannot restore parent file over non-empty directory: {target}") from exc
            else:
                shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        if backup.is_dir():
            shutil.copytree(backup, target)
        else:
            shutil.copy2(backup, target)
