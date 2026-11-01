"""Migration regression for feat32 user-management columns."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def _config(database_url: str) -> Config:
    migrations = Path(__file__).resolve().parents[1] / "packages/harness/deerflow/persistence/migrations"
    config = Config()
    config.set_main_option("script_location", str(migrations))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_user_management_migration_backfills_existing_users(tmp_path):
    database = tmp_path / "migration.db"
    url = f"sqlite:///{database}"
    config = _config(f"sqlite+aiosqlite:///{database}")
    command.upgrade(config, "company_20260715_group_quotas")

    engine = sa.create_engine(url)
    created_at = "2026-07-01 00:00:00"
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO users (
                    id, email, password_hash, system_role, created_at,
                    oauth_provider, oauth_id, needs_setup, token_version
                ) VALUES (
                    :id, :email, :password_hash, :system_role, :created_at,
                    NULL, NULL, 0, :token_version
                )
                """
            ),
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "email": "legacy@sz-jlc.com",
                "password_hash": "hash",
                "system_role": "user",
                "created_at": created_at,
                "token_version": 7,
            },
        )

    command.upgrade(config, "head")

    inspector = sa.inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("users")}
    assert columns["account_status"]["nullable"] is False
    assert {
        "registration_requested_at",
        "registration_approved_at",
        "registration_approved_by",
        "approval_email_status",
        "approval_email_last_attempt_at",
    } <= columns.keys()
    assert {index["name"] for index in inspector.get_indexes("users")} >= {
        "idx_users_account_status_created",
        "idx_users_pending_queue",
    }
    with engine.connect() as connection:
        row = (
            connection.execute(
                sa.text(
                    """
                SELECT account_status, token_version, registration_requested_at,
                       registration_approved_at, registration_approved_by,
                       approval_email_status, approval_email_last_attempt_at
                FROM users WHERE id = :id
                """
                ),
                {"id": "00000000-0000-0000-0000-000000000001"},
            )
            .mappings()
            .one()
        )
    assert row == {
        "account_status": "active",
        "token_version": 7,
        "registration_requested_at": None,
        "registration_approved_at": None,
        "registration_approved_by": None,
        "approval_email_status": None,
        "approval_email_last_attempt_at": None,
    }
    engine.dispose()
