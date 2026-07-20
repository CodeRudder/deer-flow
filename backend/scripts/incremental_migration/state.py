from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.incremental_migration.domain import MigrationPhase, canonical_hash

_ALLOWED_TRANSITIONS: dict[MigrationPhase, set[MigrationPhase]] = {
    MigrationPhase.TARGET_PREFLIGHT: {MigrationPhase.BASELINE_FROZEN, MigrationPhase.ROLLBACK_IN_PROGRESS},
    MigrationPhase.BASELINE_FROZEN: {MigrationPhase.STAGED, MigrationPhase.ROLLBACK_IN_PROGRESS},
    MigrationPhase.STAGED: {MigrationPhase.DB_APPLYING, MigrationPhase.ROLLBACK_IN_PROGRESS},
    MigrationPhase.DB_APPLYING: {MigrationPhase.DB_COMMITTED, MigrationPhase.ROLLBACK_IN_PROGRESS},
    MigrationPhase.DB_COMMITTED: {MigrationPhase.FILES_APPLYING, MigrationPhase.ROLLBACK_IN_PROGRESS},
    MigrationPhase.FILES_APPLYING: {MigrationPhase.FILES_APPLIED, MigrationPhase.ROLLBACK_IN_PROGRESS},
    MigrationPhase.FILES_APPLIED: {MigrationPhase.VERIFYING, MigrationPhase.ROLLBACK_IN_PROGRESS},
    MigrationPhase.VERIFYING: {MigrationPhase.VERIFIED, MigrationPhase.ROLLBACK_IN_PROGRESS},
    MigrationPhase.ROLLBACK_IN_PROGRESS: {MigrationPhase.ROLLED_BACK, MigrationPhase.ROLLBACK_FAILED},
    MigrationPhase.ROLLBACK_FAILED: {MigrationPhase.ROLLBACK_IN_PROGRESS},
    MigrationPhase.VERIFIED: set(),
    MigrationPhase.ROLLED_BACK: set(),
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class MigrationState:
    export_id: str
    phase: MigrationPhase
    created_at: str
    updated_at: str
    history: list[dict[str, str]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "export_id": self.export_id,
            "phase": self.phase.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "history": self.history,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MigrationState:
        return cls(
            export_id=str(value["export_id"]),
            phase=MigrationPhase(str(value["phase"])),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
            history=[dict(item) for item in value.get("history", [])],
            details=dict(value.get("details", {})),
        )


class MigrationStateStore:
    def __init__(self, path: Path):
        self.path = path

    def create(self, export_id: str, *, details: dict[str, Any] | None = None) -> MigrationState:
        if self.path.exists():
            raise ValueError(f"migration state already exists: {self.path}")
        if self.path.parent.exists():
            unexpected = [item for item in self.path.parent.iterdir() if item.name != ".operation.lock"]
            if unexpected:
                raise ValueError(f"migration state directory is not empty: {self.path.parent}")
        now = _utc_now()
        initial_details = dict(details or {})
        history_entry = {"phase": MigrationPhase.TARGET_PREFLIGHT.value, "at": now}
        if initial_details:
            history_entry["details_hash"] = canonical_hash(initial_details)
        state = MigrationState(
            export_id=export_id,
            phase=MigrationPhase.TARGET_PREFLIGHT,
            created_at=now,
            updated_at=now,
            history=[history_entry],
            details=initial_details,
        )
        self._write(state)
        return state

    def load(self) -> MigrationState:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"migration state does not exist: {self.path}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"invalid migration state document: {self.path}")
        return MigrationState.from_dict(value)

    def transition(self, phase: MigrationPhase, *, details: dict[str, Any] | None = None) -> MigrationState:
        state = self.load()
        allowed = _ALLOWED_TRANSITIONS[state.phase]
        if phase not in allowed:
            raise ValueError(f"invalid migration state transition: {state.phase.value} -> {phase.value}")
        now = _utc_now()
        state.phase = phase
        state.updated_at = now
        history_entry = {"phase": phase.value, "at": now}
        if details:
            history_entry["details_hash"] = canonical_hash(details)
        state.history.append(history_entry)
        if details:
            state.details.update(details)
        self._write(state)
        return state

    def record_details(self, details: dict[str, Any]) -> MigrationState:
        if not details:
            return self.load()
        state = self.load()
        state.updated_at = _utc_now()
        history_entry = {
            "phase": state.phase.value,
            "at": state.updated_at,
            "details_hash": canonical_hash(details),
        }
        state.history.append(history_entry)
        state.details.update(details)
        self._write(state)
        return state

    def _write(self, state: MigrationState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state.to_dict(), sort_keys=True, indent=2).encode("utf-8") + b"\n"
        fd, raw_temp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
        temp_path = Path(raw_temp)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            temp_path.unlink(missing_ok=True)
