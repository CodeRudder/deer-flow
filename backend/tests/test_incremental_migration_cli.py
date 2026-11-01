from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.migrate_incremental_data import DEFAULT_END, DEFAULT_START, _normalize_dsn, _read_owner_overrides, build_parser


def test_cli_exposes_safe_default_dry_run_modes() -> None:
    parser = build_parser()
    target_args = parser.parse_args(["plan", "target", "--state-dir", "state"])
    import_args = parser.parse_args(
        [
            "import",
            "--package",
            "package.tar.gz",
            "--state-dir",
            "state",
        ]
    )
    verify_args = parser.parse_args(["verify", "--state-dir", "state"])

    assert import_args.apply is False
    assert verify_args.apply is False
    assert target_args.start == DEFAULT_START
    assert target_args.end == DEFAULT_END
    assert target_args.source_environment == "dev-deerflow"
    assert target_args.environment == "prod"
    assert DEFAULT_START == "2026-07-13T00:00:00+08:00"
    assert DEFAULT_END == "2026-07-20T00:00:00+08:00"


def test_cli_normalizes_async_sqlalchemy_dsn_without_echoing_it() -> None:
    assert _normalize_dsn("postgresql+asyncpg://user:pass@example.test/db") == "postgresql://user:pass@example.test/db"
    assert _normalize_dsn("postgres://user:pass@example.test/db") == "postgres://user:pass@example.test/db"


def test_cli_owner_map_requires_string_object(tmp_path: Path) -> None:
    valid = tmp_path / "owners.json"
    valid.write_text(json.dumps({"thread-1": "user-1"}), encoding="utf-8")
    assert _read_owner_overrides(str(valid)) == {"thread-1": "user-1"}

    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(["thread-1"]), encoding="utf-8")
    with pytest.raises(ValueError, match="owner map"):
        _read_owner_overrides(str(invalid))
