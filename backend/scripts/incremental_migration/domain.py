from __future__ import annotations

import base64
import hashlib
import json
import os
import stat as stat_module
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum, StrEnum
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID


class MigrationError(RuntimeError):
    """Raised when a migration safety invariant is violated."""


class StorageClass(StrEnum):
    ACTIVE = "active"
    RUNTIME_ARCHIVE = "runtime_archive"


class MigrationPhase(StrEnum):
    TARGET_PREFLIGHT = "TARGET_PREFLIGHT"
    BASELINE_FROZEN = "BASELINE_FROZEN"
    STAGED = "STAGED"
    DB_APPLYING = "DB_APPLYING"
    DB_COMMITTED = "DB_COMMITTED"
    FILES_APPLYING = "FILES_APPLYING"
    FILES_APPLIED = "FILES_APPLIED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    ROLLBACK_IN_PROGRESS = "ROLLBACK_IN_PROGRESS"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


@dataclass(frozen=True)
class MigrationWindow:
    start_utc: datetime
    end_utc: datetime
    start_local: str
    end_local: str

    @classmethod
    def parse(cls, start: str, end: str) -> MigrationWindow:
        start_value = _parse_aware_datetime(start, label="start")
        end_value = _parse_aware_datetime(end, label="end")
        if start_value >= end_value:
            raise ValueError("migration window start must be before end")
        return cls(
            start_utc=start_value.astimezone(UTC),
            end_utc=end_value.astimezone(UTC),
            start_local=start,
            end_local=end,
        )

    def contains(self, value: datetime) -> bool:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("window comparisons require timezone-aware datetimes")
        utc_value = value.astimezone(UTC)
        return self.start_utc <= utc_value < self.end_utc

    def to_dict(self) -> dict[str, str]:
        return {
            "start_local": self.start_local,
            "end_local": self.end_local,
            "start_utc": self.start_utc.isoformat().replace("+00:00", "Z"),
            "end_utc": self.end_utc.isoformat().replace("+00:00", "Z"),
        }


def _parse_aware_datetime(raw: str, *, label: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {label} datetime: {raw!r}") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} datetime must include an explicit UTC offset")
    return value


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return {"$float": repr(value)}
        return value
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return {"$bytes": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, datetime):
        normalized = value.astimezone(UTC) if value.tzinfo is not None and value.utcoffset() is not None else value
        return {"$datetime": normalized.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, time):
        return {"$time": value.isoformat()}
    if isinstance(value, UUID):
        return {"$uuid": str(value)}
    if isinstance(value, Path):
        return {"$path": value.as_posix()}
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (set, frozenset)):
        normalized = [_canonical_value(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    if isinstance(value, Sequence):
        return [_canonical_value(item) for item in value]
    raise TypeError(f"unsupported canonical hash value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    normalized = _canonical_value(value)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def validate_relative_path(raw: str | PurePosixPath) -> PurePosixPath:
    text = str(raw)
    if "\\" in text:
        raise ValueError(f"unsafe relative path contains a backslash: {text!r}")
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe relative path: {text!r}")
    return path


@dataclass(frozen=True)
class FileEntry:
    relative_path: str
    user_id: str | None
    thread_id: str
    layout: str
    size: int
    mtime_ns: int
    mode: int
    sha256: str
    storage_class: StorageClass

    @classmethod
    def from_path(
        cls,
        *,
        home: Path,
        path: Path,
        user_id: str | None,
        thread_id: str,
        layout: str,
        storage_class: StorageClass,
        stat: os.stat_result | None = None,
    ) -> FileEntry:
        home_resolved = home.resolve()
        path_resolved = path.resolve(strict=True)
        try:
            relative = path_resolved.relative_to(home_resolved).as_posix()
        except ValueError as exc:
            raise ValueError(f"file is outside migration home: {path}") from exc
        validate_relative_path(relative)
        file_stat = stat or path.lstat()
        if not stat_module.S_ISREG(file_stat.st_mode):
            raise ValueError(f"migration files must be regular files: {path}")
        return cls(
            relative_path=relative,
            user_id=user_id,
            thread_id=thread_id,
            layout=layout,
            size=file_stat.st_size,
            mtime_ns=file_stat.st_mtime_ns,
            mode=stat_module.S_IMODE(file_stat.st_mode),
            sha256=sha256_file(path),
            storage_class=storage_class,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "layout": self.layout,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "mode": self.mode,
            "sha256": self.sha256,
            "storage_class": self.storage_class.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FileEntry:
        relative = validate_relative_path(str(value["relative_path"])).as_posix()
        return cls(
            relative_path=relative,
            user_id=str(value["user_id"]) if value.get("user_id") is not None else None,
            thread_id=str(value["thread_id"]),
            layout=str(value["layout"]),
            size=int(value["size"]),
            mtime_ns=int(value["mtime_ns"]),
            mode=int(value["mode"]),
            sha256=str(value["sha256"]),
            storage_class=StorageClass(str(value["storage_class"])),
        )
