"""Unit tests for local-auth email domain configuration and enforcement."""

from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException
from pydantic import ValidationError

from app.gateway.auth.email_domain import enforce_email_domain_allowed, is_email_domain_allowed
from app.gateway.auth.errors import AuthErrorCode
from deerflow.config.auth_config import AuthAppConfig


def test_local_email_domain_config_defaults():
    config = AuthAppConfig()

    assert config.allowed_email_domains == ["sz-jlc.com"]
    assert config.enforce_email_domain_on_login is False


def test_local_email_domains_are_normalized_and_deduplicated():
    config = AuthAppConfig(
        allowed_email_domains=[
            " SZ-JLC.COM ",
            "@Example.COM",
            "sz-jlc.com",
            "@EXAMPLE.com",
        ]
    )

    assert config.allowed_email_domains == [
        "sz-jlc.com",
        "example.com",
    ]


def test_local_email_domain_config_does_not_change_oidc_provider_domains():
    config = AuthAppConfig(
        allowed_email_domains=["local.example"],
        oidc={
            "providers": {
                "company": {
                    "display_name": "Company SSO",
                    "issuer": "https://id.example.com",
                    "client_id": "deerflow",
                    "allowed_email_domains": ["@OIDC.EXAMPLE"],
                }
            }
        },
    )

    assert config.allowed_email_domains == ["local.example"]
    assert config.oidc.providers["company"].allowed_email_domains == ["@OIDC.EXAMPLE"]


def test_local_email_domain_config_rejects_an_empty_list():
    with pytest.raises(ValidationError, match="at least one domain"):
        AuthAppConfig(allowed_email_domains=[])


@pytest.mark.parametrize(
    "domain",
    [
        "",
        "   ",
        "@",
        "@@example.com",
        "user@example.com",
        "http://example.com",
        "example.com:443",
        "example.com/path",
        "*.example.com",
        ".example.com",
        "example.com.",
        "example..com",
        "-example.com",
        "example-.com",
        "exa_mple.com",
        "例子.公司",
        "xn--abc.com",
        "xn--fa-hia.de",
        f"{'a' * 64}.com",
        f"{'a.' * 126}aa",
    ],
)
def test_local_email_domain_config_rejects_invalid_domains(domain: str):
    with pytest.raises(ValidationError, match="valid email domain"):
        AuthAppConfig(allowed_email_domains=[domain])


def test_config_example_exposes_local_email_domain_fields_as_real_yaml():
    example_path = Path(__file__).resolve().parents[2] / "config.example.yaml"
    raw_config = yaml.safe_load(example_path.read_text(encoding="utf-8"))

    assert raw_config["config_version"] == 24
    assert raw_config["auth"]["allowed_email_domains"] == ["sz-jlc.com"]
    assert raw_config["auth"]["enforce_email_domain_on_login"] is False
    assert raw_config["auth"]["local_registration"] == {
        "require_admin_approval": False,
        "approval_email": {"enabled": False},
    }


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("user@sz-jlc.com", True),
        ("user@SZ-JLC.COM", True),
        ("user@sub.sz-jlc.com", False),
        ("user@fake-sz-jlc.com", False),
        ("user@sz-jlc.com.example.org", False),
        ("user@example.org", False),
        ("user@fass.de", True),
        ("user@faß.de", False),
        ("user@xn--fa-hia.de", False),
        ("missing-at.example", False),
        ("@sz-jlc.com", False),
        ("user@", False),
        ("user@@sz-jlc.com", False),
        (" user@sz-jlc.com ", False),
    ],
)
def test_is_email_domain_allowed_uses_exact_normalized_matching(email: str, expected: bool):
    allowed_domains = ["sz-jlc.com", "fass.de"]

    assert is_email_domain_allowed(email, allowed_domains) is expected


def test_enforce_email_domain_allowed_returns_normally_for_allowed_email():
    assert enforce_email_domain_allowed("user@sz-jlc.com", ["sz-jlc.com"]) is None


def test_enforce_email_domain_allowed_raises_structured_403():
    with pytest.raises(HTTPException) as exc_info:
        enforce_email_domain_allowed("user@outside.example", ["sz-jlc.com", "example.org"])

    error = exc_info.value
    assert error.status_code == 403
    assert error.detail == {
        "code": AuthErrorCode.EMAIL_DOMAIN_NOT_ALLOWED,
        "message": "请使用公司邮箱注册",
    }
