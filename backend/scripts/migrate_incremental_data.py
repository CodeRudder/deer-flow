from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from scripts.incremental_migration.domain import MigrationWindow
from scripts.incremental_migration.manifest import BaselineManifest
from scripts.incremental_migration.service import (
    apply_import,
    dry_run_import,
    export_package,
    plan_source,
    prepare_target_baseline,
    rollback_import,
    verify_import,
)
from scripts.incremental_migration.state import MigrationStateStore

DEFAULT_START = "2026-07-13T00:00:00+08:00"
DEFAULT_END = "2026-07-20T00:00:00+08:00"


def _normalize_dsn(value: str) -> str:
    return re.sub(r"^postgres(?:ql)?\+[^:]+://", "postgresql://", value)


def _load_dsn(args: argparse.Namespace) -> str:
    if args.dsn:
        return _normalize_dsn(args.dsn)
    from deerflow.config.app_config import AppConfig

    config = AppConfig.from_file(args.config)
    if config.database.backend != "postgres":
        raise ValueError("database.backend must be postgres for incremental migration")
    if not config.database.postgres_url:
        raise ValueError("config.yaml does not contain database.postgres_url")
    return _normalize_dsn(config.database.postgres_url)


def _default_home() -> Path:
    if raw := os.getenv("DEER_FLOW_HOME"):
        return Path(raw).expanduser().resolve()
    from deerflow.config.paths import Paths

    return Paths().base_dir


def _read_owner_overrides(path: str | None) -> dict[str, str] | None:
    if not path:
        return None
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise ValueError("owner map must be a JSON object of string thread IDs to string user IDs")
    return dict(value)


def _add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dsn", default=None, help="PostgreSQL DSN; defaults to config.yaml")
    parser.add_argument("--config", default=None, help="Path to config.yaml when --dsn is omitted")


def _add_home_arg(parser: argparse.ArgumentParser, *, flag: str = "--home") -> None:
    parser.add_argument(flag, type=Path, default=None, help="DEER_FLOW_HOME path")


def _add_owner_map_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--owner-map", default=None, help="JSON file for explicit legacy thread owner mappings")


def _add_confirmation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--apply", action="store_true", help="Allow the command to mutate target state")
    parser.add_argument("--confirm-services-stopped", action="store_true", help="Confirm all application writers are stopped")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="One-time DeerFlow dev-to-prod incremental migration")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Generate a target baseline or source selection plan")
    plan_roles = plan_parser.add_subparsers(dest="role", required=True)

    target = plan_roles.add_parser("target", help="Freeze the prod target baseline")
    _add_connection_args(target)
    _add_home_arg(target)
    _add_owner_map_arg(target)
    target.add_argument("--state-dir", type=Path, required=True)
    target.add_argument("--baseline-output", type=Path, default=None)
    target.add_argument("--environment", default="prod")
    target.add_argument("--source-environment", default="dev-deerflow")
    target.add_argument("--start", default=DEFAULT_START)
    target.add_argument("--end", default=DEFAULT_END)
    target.add_argument("--migration-id", default=None)
    _add_confirmation_args(target)

    source = plan_roles.add_parser("source", help="Identify source changes against a frozen baseline")
    _add_connection_args(source)
    _add_home_arg(source)
    _add_owner_map_arg(source)
    source.add_argument("--baseline", type=Path, required=True)
    source.add_argument("--start", default=DEFAULT_START)
    source.add_argument("--end", default=DEFAULT_END)

    export = subparsers.add_parser("export", help="Build an immutable source migration package")
    _add_connection_args(export)
    _add_home_arg(export)
    _add_owner_map_arg(export)
    export.add_argument("--baseline", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--work-root", type=Path, required=True)
    export.add_argument("--start", default=DEFAULT_START)
    export.add_argument("--end", default=DEFAULT_END)
    export.add_argument("--source-environment", default="dev-deerflow")
    export.add_argument("--target-environment", default="prod")
    _add_confirmation_args(export)

    import_parser = subparsers.add_parser("import", help="Dry-run or apply a migration package on prod")
    _add_connection_args(import_parser)
    _add_home_arg(import_parser)
    _add_owner_map_arg(import_parser)
    import_parser.add_argument("--package", type=Path, required=True)
    import_parser.add_argument("--state-dir", type=Path, required=True)
    import_parser.add_argument("--work-root", type=Path, default=None)
    import_parser.add_argument("--confirm-credential-key-compatible", action="store_true")
    _add_confirmation_args(import_parser)

    verify = subparsers.add_parser("verify", help="Verify the post-import expected overlay")
    _add_connection_args(verify)
    _add_home_arg(verify)
    _add_owner_map_arg(verify)
    verify.add_argument("--state-dir", type=Path, required=True)
    _add_confirmation_args(verify)
    verify.add_argument("--confirm-smoke-tests", action="store_true")

    rollback = subparsers.add_parser("rollback", help="Preview or apply migration compensation")
    _add_connection_args(rollback)
    _add_home_arg(rollback)
    _add_owner_map_arg(rollback)
    rollback.add_argument("--state-dir", type=Path, required=True)
    _add_confirmation_args(rollback)

    return parser


def _paths(args: argparse.Namespace) -> tuple[Path, Path | None]:
    home = (args.home or _default_home()).expanduser().resolve()
    state_dir = args.state_dir.expanduser().resolve() if getattr(args, "state_dir", None) else None
    return home, state_dir


def _print(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, default=str))


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "plan" and args.role == "target":
        home, state_dir = _paths(args)
        assert state_dir is not None
        with _connect(args) as connection:
            result = prepare_target_baseline(
                connection,
                target_home=home,
                state_dir=state_dir,
                environment=args.environment,
                window=MigrationWindow.parse(args.start, args.end),
                source_environment=args.source_environment,
                baseline_output=args.baseline_output,
                migration_id=args.migration_id,
                owner_overrides=_read_owner_overrides(args.owner_map),
                apply=args.apply,
                confirm_services_stopped=args.confirm_services_stopped,
            )
        return result.to_dict()

    if args.command == "plan" and args.role == "source":
        home, _ = _paths(args)
        baseline = BaselineManifest.read(args.baseline)
        window = MigrationWindow.parse(args.start, args.end)
        with _connect(args) as connection:
            result = plan_source(
                connection,
                source_home=home,
                baseline=baseline,
                window=window,
                owner_overrides=_read_owner_overrides(args.owner_map),
            )
        return result.to_dict()

    if args.command == "export":
        home, _ = _paths(args)
        baseline = BaselineManifest.read(args.baseline)
        if not args.apply:
            window = MigrationWindow.parse(args.start, args.end)
            with _connect(args) as connection:
                return {
                    "apply_required": True,
                    "plan": plan_source(
                        connection,
                        source_home=home,
                        baseline=baseline,
                        window=window,
                        owner_overrides=_read_owner_overrides(args.owner_map),
                    ).to_dict(),
                }
        with _connect(args) as connection:
            result = export_package(
                connection,
                source_home=home,
                baseline=baseline,
                window=MigrationWindow.parse(args.start, args.end),
                output_path=args.output.expanduser().resolve(),
                work_root=args.work_root.expanduser().resolve(),
                source_environment=args.source_environment,
                target_environment=args.target_environment,
                owner_overrides=_read_owner_overrides(args.owner_map),
                confirm_services_stopped=args.confirm_services_stopped,
            )
        return result.to_dict()

    if args.command == "import":
        home, state_dir = _paths(args)
        assert state_dir is not None
        work_root = (args.work_root or state_dir.parent).expanduser().resolve()
        with _connect(args) as connection:
            if args.apply:
                result = apply_import(
                    connection,
                    package_path=args.package.expanduser().resolve(),
                    target_home=home,
                    state_dir=state_dir,
                    owner_overrides=_read_owner_overrides(args.owner_map),
                    confirm_services_stopped=args.confirm_services_stopped,
                    confirm_credential_key_compatible=args.confirm_credential_key_compatible,
                )
            else:
                result = dry_run_import(
                    connection,
                    package_path=args.package.expanduser().resolve(),
                    target_home=home,
                    state_dir=state_dir,
                    work_root=work_root,
                    owner_overrides=_read_owner_overrides(args.owner_map),
                )
        return result.to_dict(include_conflict_rows=True)

    if args.command == "verify":
        home, state_dir = _paths(args)
        assert state_dir is not None
        with _connect(args) as connection:
            result = verify_import(
                connection,
                target_home=home,
                state_dir=state_dir,
                owner_overrides=_read_owner_overrides(args.owner_map),
                mark_verified=args.apply,
                confirm_services_stopped=args.confirm_services_stopped,
                confirm_smoke_tests=args.confirm_smoke_tests,
            )
        return result.to_dict()

    if args.command == "rollback":
        home, state_dir = _paths(args)
        assert state_dir is not None
        if not args.apply:
            state = MigrationStateStore(state_dir / "state.json").load()
            return {"apply_required": True, "export_id": state.export_id, "phase": state.phase.value}
        with _connect(args) as connection:
            state = rollback_import(
                connection,
                target_home=home,
                state_dir=state_dir,
                owner_overrides=_read_owner_overrides(args.owner_map),
                confirm_services_stopped=args.confirm_services_stopped,
            )
        return state.to_dict()

    raise ValueError("unsupported migration command")


def _connect(args: argparse.Namespace):
    import psycopg

    return psycopg.connect(_load_dsn(args))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _print(_run(args))
    except KeyboardInterrupt:
        print("migration interrupted; keep services stopped and inspect state.json", file=sys.stderr)
        return 130
    except Exception as exc:
        try:
            import psycopg
        except ImportError:
            psycopg = None
        if psycopg is not None and isinstance(exc, psycopg.Error):
            print(f"migration failed: {type(exc).__name__}: database operation failed", file=sys.stderr)
            return 2
        # Do not echo DSNs, SQL parameters, or database row contents in the CLI error path.
        print(f"migration failed: {type(exc).__name__}: {str(exc).splitlines()[0]}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
