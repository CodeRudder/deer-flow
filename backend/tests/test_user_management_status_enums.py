"""Contracts for user-management lifecycle status enums."""

import pytest
from pydantic import ValidationError

from app.gateway.admin.schemas import UserStatusFilter
from app.gateway.auth.models import AccountStatus, ApprovalEmailStatus, User


def test_status_enums_keep_persisted_and_wire_values_stable():
    assert [status.value for status in AccountStatus] == ["active", "pending", "disabled"]
    assert [status.value for status in ApprovalEmailStatus] == ["pending", "sending", "sent", "failed"]


def test_user_parses_database_strings_to_enums_and_serializes_strings():
    user = User(
        email="pending@sz-jlc.com",
        account_status="pending",
        approval_email_status="sending",
    )

    assert user.account_status is AccountStatus.PENDING
    assert user.approval_email_status is ApprovalEmailStatus.SENDING
    payload = user.model_dump(mode="json")
    assert payload["account_status"] == "pending"
    assert payload["approval_email_status"] == "sending"


def test_status_enums_reject_unknown_values_and_cover_all_filter():
    with pytest.raises(ValidationError):
        User(email="invalid@sz-jlc.com", account_status="unknown")

    assert UserStatusFilter("all") is UserStatusFilter.ALL
