"""Focused tests for registration-approval configuration."""

import pytest
from pydantic import ValidationError

from deerflow.config.auth_config import AuthAppConfig


def _approval_config(**overrides):
    approval_email = {
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
    approval_email.update(overrides)
    return {
        "require_admin_approval": True,
        "approval_email": approval_email,
    }


def test_registration_approval_defaults_are_backward_compatible():
    config = AuthAppConfig()

    assert config.local_registration.require_admin_approval is False
    assert config.local_registration.approval_email.enabled is False
    assert config.local_registration.approval_email.smtp is None


def test_registration_approval_accepts_complete_ssl_config():
    config = AuthAppConfig(local_registration=_approval_config())

    email = config.local_registration.approval_email
    assert email.enabled is True
    assert email.from_address == "noreply@sz-jlc.com"
    assert email.smtp is not None
    assert email.smtp.port == 465
    assert email.smtp.security == "ssl"


@pytest.mark.parametrize(
    "local_registration",
    [
        {"require_admin_approval": True},
        {
            "require_admin_approval": True,
            "approval_email": {"enabled": False},
        },
        _approval_config(from_address=None),
        _approval_config(smtp=None),
    ],
)
def test_approval_switch_requires_enabled_complete_email_config(local_registration):
    with pytest.raises(ValidationError):
        AuthAppConfig(local_registration=local_registration)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("port", 0),
        ("port", 65536),
        ("timeout_seconds", 0),
        ("timeout_seconds", 31),
        ("security", "starttls"),
    ],
)
def test_approval_smtp_rejects_unsupported_transport_values(field, value):
    config = _approval_config()
    config["approval_email"]["smtp"][field] = value

    with pytest.raises(ValidationError):
        AuthAppConfig(local_registration=config)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("from_name", "DeerFlow\r\nBcc: attacker@example.com"),
        ("from_address", "not-an-email"),
        ("from_address", "noreply@xn--invalid.example"),
    ],
)
def test_approval_email_rejects_header_injection_and_invalid_sender(field, value):
    with pytest.raises(ValidationError):
        AuthAppConfig(local_registration=_approval_config(**{field: value}))
