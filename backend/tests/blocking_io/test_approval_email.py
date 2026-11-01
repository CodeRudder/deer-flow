"""Regression anchor: approval SMTP work must stay off the event loop."""

from unittest.mock import patch

import pytest

from app.gateway.auth.approval_email import send_approval_email
from deerflow.config.auth_config import ApprovalEmailConfig

pytestmark = pytest.mark.asyncio


async def test_approval_email_smtp_does_not_block_event_loop() -> None:
    config = ApprovalEmailConfig.model_validate(
        {
            "enabled": True,
            "from_address": "noreply@sz-jlc.com",
            "smtp": {
                "host": "smtp.example.com",
                "username": "smtp-user",
                "password": "smtp-secret",
            },
        }
    )

    with (
        patch("app.gateway.auth.approval_email.smtplib.SMTP_SSL"),
        patch("app.gateway.auth.approval_email.ssl.create_default_context"),
    ):
        await send_approval_email(config, "user@sz-jlc.com")
