"""Focused tests for registration-approval SMTP delivery."""

from unittest.mock import MagicMock, patch

import pytest

from app.gateway.auth.approval_email import send_approval_email
from deerflow.config.auth_config import ApprovalEmailConfig


def _config() -> ApprovalEmailConfig:
    return ApprovalEmailConfig.model_validate(
        {
            "enabled": True,
            "from_name": "DeerFlow",
            "from_address": "noreply@sz-jlc.com",
            "login_url": "https://deerflow.example.com/login",
            "smtp": {
                "host": "smtp.example.com",
                "port": 465,
                "security": "ssl",
                "username": "smtp-user",
                "password": "smtp-secret",
                "timeout_seconds": 10,
            },
        }
    )


@pytest.mark.asyncio
async def test_approval_email_uses_implicit_ssl_and_expected_envelope():
    client = MagicMock()

    with (
        patch("app.gateway.auth.approval_email.smtplib.SMTP_SSL", return_value=client) as smtp,
        patch("app.gateway.auth.approval_email.ssl.create_default_context", return_value="ssl-context"),
    ):
        await send_approval_email(_config(), "user@sz-jlc.com")

    smtp.assert_called_once_with("smtp.example.com", 465, timeout=10, context="ssl-context")
    client.starttls.assert_not_called()
    client.login.assert_called_once_with("smtp-user", "smtp-secret")
    message = client.send_message.call_args.args[0]
    assert message["Subject"] == "DeerFlow 注册申请已通过"
    assert message["To"] == "user@sz-jlc.com"
    assert "https://deerflow.example.com/login" in message.get_content()
    assert "登录地址：https://deerflow.example.com/login（临时域名）" in message.get_content()
    assert client.send_message.call_args.kwargs == {}
    client.quit.assert_called_once_with()
