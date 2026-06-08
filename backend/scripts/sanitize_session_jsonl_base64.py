"""Sanitize image base64 payloads from persisted session JSONL files.

This is a one-time maintenance script for existing debug/session logs. It only
rewrites local JSONL files used for main-session and sub-agent history; it does
not touch LangGraph checkpoints or model runtime state.

Usage:
    cd backend
    PYTHONPATH=. uv run python scripts/sanitize_session_jsonl_base64.py --dry-run
    PYTHONPATH=. uv run python scripts/sanitize_session_jsonl_base64.py --backup
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from deerflow.config.paths import get_paths
from deerflow.subagents.session import _sanitize_session_storage_value


@dataclass
class FileSanitizeResult:
    path: Path
    changed: bool
    total_lines: int = 0
    changed_lines: int = 0
    invalid_lines: int = 0
    backup_path: Path | None = None
    error: str | None = None


@dataclass
class SanitizeSummary:
    files_scanned: int = 0
    files_changed: int = 0
    lines_scanned: int = 0
    lines_changed: int = 0
    invalid_lines: int = 0
    backups_written: int = 0
    errors: int = 0

    def add(self, result: FileSanitizeResult) -> None:
        self.files_scanned += 1
        self.lines_scanned += result.total_lines
        self.lines_changed += result.changed_lines
        self.invalid_lines += result.invalid_lines
        if result.changed:
            self.files_changed += 1
        if result.backup_path is not None:
            self.backups_written += 1
        if result.error is not None:
            self.errors += 1


def iter_session_jsonl_files(base_dir: Path) -> Iterable[Path]:
    """Yield main-session and sub-agent JSONL files under DeerFlow data dirs."""
    patterns = (
        "threads/*/conversation.jsonl",
        "threads/*/subagents/*.jsonl",
        "users/*/threads/*/conversation.jsonl",
        "users/*/threads/*/subagents/*.jsonl",
    )
    seen: set[Path] = set()
    for pattern in patterns:
        for path in base_dir.glob(pattern):
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                continue
            seen.add(resolved)
            yield path


def _backup_path_for(path: Path) -> Path:
    candidate = path.with_name(f"{path.name}.bak")
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        candidate = path.with_name(f"{path.name}.bak.{index}")
        if not candidate.exists():
            return candidate
        index += 1


def _sanitize_jsonl_line(line: str) -> tuple[str, bool, bool]:
    """Return sanitized line, changed flag, invalid-json flag."""
    if not line.strip():
        return line, False, False

    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return line, False, True

    sanitized = _sanitize_session_storage_value(parsed)
    if sanitized == parsed:
        return line, False, False

    return json.dumps(sanitized, ensure_ascii=False) + "\n", True, False


def sanitize_jsonl_file(path: Path, *, dry_run: bool = False, backup: bool = False) -> FileSanitizeResult:
    """Sanitize one JSONL file, preserving invalid lines as-is."""
    result = FileSanitizeResult(path=path, changed=False)
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as exc:
        result.error = str(exc)
        return result

    output_lines: list[str] = []
    for line in lines:
        result.total_lines += 1
        sanitized_line, changed, invalid = _sanitize_jsonl_line(line)
        output_lines.append(sanitized_line)
        if changed:
            result.changed_lines += 1
        if invalid:
            result.invalid_lines += 1

    result.changed = result.changed_lines > 0
    if not result.changed or dry_run:
        return result

    tmp_path: Path | None = None
    try:
        if backup:
            result.backup_path = _backup_path_for(path)
            shutil.copy2(path, result.backup_path)

        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.writelines(output_lines)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(tmp_path, path)
    except OSError as exc:
        result.error = str(exc)
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    return result


def sanitize_session_jsonl_tree(base_dir: Path, *, dry_run: bool = False, backup: bool = False, verbose: bool = False) -> SanitizeSummary:
    """Sanitize every session JSONL file under *base_dir*."""
    summary = SanitizeSummary()
    for path in iter_session_jsonl_files(base_dir):
        result = sanitize_jsonl_file(path, dry_run=dry_run, backup=backup)
        summary.add(result)
        if verbose or result.changed or result.error:
            status = "changed" if result.changed else "unchanged"
            if result.error:
                status = f"error: {result.error}"
            backup_note = f", backup={result.backup_path}" if result.backup_path else ""
            print(f"{path}: {status}, lines={result.total_lines}, changed_lines={result.changed_lines}, invalid_lines={result.invalid_lines}{backup_note}")
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sanitize image base64 payloads from DeerFlow session JSONL files")
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=None,
        help="DeerFlow data directory. Defaults to get_paths().base_dir",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report changes without rewriting files")
    parser.add_argument("--backup", action="store_true", help="Write a .bak copy before replacing changed files")
    parser.add_argument("--verbose", action="store_true", help="Print every scanned file, including unchanged files")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    base_dir = args.base_dir.resolve() if args.base_dir is not None else get_paths().base_dir
    if not base_dir.exists():
        print(f"base dir does not exist: {base_dir}")
        return 1

    if args.dry_run:
        print("dry-run enabled: no files will be rewritten")
    summary = sanitize_session_jsonl_tree(base_dir, dry_run=args.dry_run, backup=args.backup, verbose=args.verbose)
    print(
        "summary: "
        f"files_scanned={summary.files_scanned}, "
        f"files_changed={summary.files_changed}, "
        f"lines_scanned={summary.lines_scanned}, "
        f"lines_changed={summary.lines_changed}, "
        f"invalid_lines={summary.invalid_lines}, "
        f"backups_written={summary.backups_written}, "
        f"errors={summary.errors}"
    )
    return 1 if summary.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
