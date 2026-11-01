from __future__ import annotations

import gzip
import hashlib
import os
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from scripts.incremental_migration.domain import StorageClass, sha256_file, validate_relative_path
from scripts.incremental_migration.files import safe_extract_tar
from scripts.incremental_migration.manifest import BaselineManifest, ExportManifest

MANIFEST_PATH = "manifest.json"
BASELINE_PATH = "baseline.json"
CHECKSUMS_PATH = "checksums.sha256"
ACTIVE_ARCHIVE_PATH = "files/threads.tar.gz"
RUNTIME_ARCHIVE_PATH = "files/runtime-archive.tar.gz"


@dataclass(frozen=True)
class ExtractedPackage:
    root: Path
    manifest: ExportManifest
    baseline: BaselineManifest


def package_content_sha256(root: Path) -> str:
    """Return an identity for the verified logical package, independent of tar metadata."""
    from scripts.incremental_migration.domain import canonical_hash

    checksums = _read_checksums(root / CHECKSUMS_PATH)
    return canonical_hash(tuple(sorted(checksums.items())))


def _regular_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode):
            raise ValueError(f"package symlink is not allowed: {path}")
        if stat.S_ISDIR(file_stat.st_mode):
            continue
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"package special file is not allowed: {path}")
        files.append(path)
    return files


def _tar_file_names(path: Path) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            normalized = validate_relative_path(member.name).as_posix()
            if normalized in seen:
                raise ValueError(f"duplicate path in file archive: {normalized}")
            seen.add(normalized)
            if not member.isfile():
                raise ValueError(f"file payload archive may only contain regular files: {member.name}")
            names.append(normalized)
    return tuple(sorted(names))


def _verify_file_archives(root: Path, manifest: ExportManifest) -> None:
    expected_active = tuple(sorted(entry.relative_path for entry in manifest.files if entry.storage_class is StorageClass.ACTIVE))
    expected_runtime = tuple(sorted(entry.relative_path for entry in manifest.files if entry.storage_class is StorageClass.RUNTIME_ARCHIVE))
    active_path = root / ACTIVE_ARCHIVE_PATH
    runtime_path = root / RUNTIME_ARCHIVE_PATH
    if _tar_file_names(active_path) != expected_active:
        raise ValueError("active file archive does not match manifest")
    if _tar_file_names(runtime_path) != expected_runtime:
        raise ValueError("runtime file archive does not match manifest")

    entries = {entry.relative_path: entry for entry in manifest.files}
    if len(entries) != len(manifest.files):
        raise ValueError("file manifest contains duplicate relative paths")
    for archive_path, storage_class in (
        (active_path, StorageClass.ACTIVE),
        (runtime_path, StorageClass.RUNTIME_ARCHIVE),
    ):
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                entry = entries[validate_relative_path(member.name).as_posix()]
                if entry.storage_class is not storage_class or member.size != entry.size or (member.mode & 0o777) != entry.mode:
                    raise ValueError(f"file archive metadata mismatch: {member.name}")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"file archive payload is missing: {member.name}")
                digest = hashlib.sha256()
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                if digest.hexdigest() != entry.sha256:
                    raise ValueError(f"file archive checksum mismatch: {member.name}")


def _gzip_metrics(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    row_count = 0
    with gzip.open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            row_count += chunk.count(b"\n")
    return row_count, digest.hexdigest()


def _verify_database_payloads(root: Path, manifest: ExportManifest) -> None:
    seen_paths: set[str] = set()
    for table in manifest.tables:
        data_path = validate_relative_path(table.data_path).as_posix()
        if data_path in seen_paths:
            raise ValueError(f"duplicate database payload path: {data_path}")
        seen_paths.add(data_path)
        path = root.joinpath(*PurePosixPath(data_path).parts)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"database payload is missing or unsafe: {data_path}")
        row_count, raw_sha256 = _gzip_metrics(path)
        if row_count != table.row_count or raw_sha256 != table.raw_sha256:
            raise ValueError(f"database payload does not match manifest: {table.table_name}")


def _payload_checksums(root: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for path in _regular_files(root):
        relative = path.relative_to(root).as_posix()
        if relative in {MANIFEST_PATH, CHECKSUMS_PATH}:
            continue
        checksums[relative] = sha256_file(path)
    return checksums


def finalize_package_directory(root: Path, *, manifest: ExportManifest, baseline: BaselineManifest) -> ExportManifest:
    root.mkdir(parents=True, exist_ok=True)
    if manifest.baseline_id != baseline.baseline_id:
        raise ValueError("export manifest references a different baseline")
    baseline.write(root / BASELINE_PATH)
    _verify_database_payloads(root, manifest)
    _verify_file_archives(root, manifest)
    finalized = manifest.with_checksums(_payload_checksums(root))
    finalized.write(root / MANIFEST_PATH)
    checksum_lines = {
        **finalized.checksum_by_path,
        MANIFEST_PATH: sha256_file(root / MANIFEST_PATH),
    }
    checksums_path = root / CHECKSUMS_PATH
    checksums_path.write_text("".join(f"{digest}  {path}\n" for path, digest in sorted(checksum_lines.items())), encoding="utf-8")
    os.chmod(checksums_path, 0o600)
    verify_package_directory(root)
    return finalized


def _read_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line:
            continue
        digest, separator, relative_path = raw_line.partition("  ")
        if not separator or len(digest) != 64:
            raise ValueError(f"invalid checksum line: {raw_line!r}")
        relative = validate_relative_path(relative_path).as_posix()
        if relative in checksums:
            raise ValueError(f"duplicate checksum path: {relative}")
        checksums[relative] = digest
    return checksums


def verify_package_directory(root: Path) -> ExtractedPackage:
    checksums_path = root / CHECKSUMS_PATH
    if not checksums_path.is_file() or checksums_path.is_symlink():
        raise ValueError("migration package has no safe checksum file")
    expected = _read_checksums(checksums_path)
    actual_paths = {path.relative_to(root).as_posix() for path in _regular_files(root)} - {CHECKSUMS_PATH}
    if set(expected) != actual_paths:
        raise ValueError("migration package file list does not match checksums")
    for relative, digest in expected.items():
        path = root.joinpath(*PurePosixPath(relative).parts)
        if sha256_file(path) != digest:
            raise ValueError(f"checksum mismatch for {relative}")
    manifest = ExportManifest.read(root / MANIFEST_PATH)
    baseline = BaselineManifest.read(root / BASELINE_PATH)
    if manifest.baseline_id != baseline.baseline_id:
        raise ValueError("migration package baseline id mismatch")
    payload_expected = {path: digest for path, digest in expected.items() if path != MANIFEST_PATH}
    if manifest.checksum_by_path != payload_expected:
        raise ValueError("manifest payload checksums do not match checksums.sha256")
    _verify_database_payloads(root, manifest)
    _verify_file_archives(root, manifest)
    return ExtractedPackage(root=root, manifest=manifest, baseline=baseline)


def build_package_archive(root: Path, output_path: Path) -> None:
    verify_package_directory(root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for path in _regular_files(root):
            relative = path.relative_to(root).as_posix()
            info = archive.gettarinfo(str(path), arcname=relative)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            with path.open("rb") as handle:
                archive.addfile(info, handle)
    os.chmod(output_path, 0o600)


def extract_package_archive(archive_path: Path, destination: Path) -> ExtractedPackage:
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"package extraction directory is not empty: {destination}")
    safe_extract_tar(archive_path, destination)
    return verify_package_directory(destination)
