"""Administrator user listing, lifecycle actions, and approval notifications."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.gateway.auth.approval_email import send_approval_email
from app.gateway.auth.email_domain import enforce_email_domain_allowed
from app.gateway.auth.models import AccountStatus, ApprovalEmailStatus, User
from app.gateway.auth.repositories.base import UserRepository
from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

UserSource = Literal["local", "oidc", "platform"]
UserAction = Literal["edit", "approve", "disable", "enable", "retry_approval_email"]

_STATUS_ACTIONS: dict[AccountStatus, UserAction] = {
    AccountStatus.PENDING: "approve",
    AccountStatus.ACTIVE: "disable",
    AccountStatus.DISABLED: "enable",
}


class UserManagementError(Exception):
    """Structured domain error translated by the admin router."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class UserManagementService:
    """Coordinate user repository operations and external email delivery."""

    def __init__(self, repository: UserRepository, config: AppConfig) -> None:
        self._repository = repository
        self._config = config

    async def summary(self) -> dict[str, Any]:
        counts = await self._repository.get_user_summary()
        return {
            "registration_approval_enabled": self._config.auth.local_registration.require_admin_approval,
            **counts.model_dump(),
        }

    async def list_users(
        self,
        *,
        status: AccountStatus | None,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        result = await self._repository.list_users(
            status=status,
            keyword=keyword.strip() if keyword and keyword.strip() else None,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self.user_item(user) for user in result.items],
            "total": result.total,
            "page": result.page,
            "page_size": result.page_size,
        }

    async def approve(self, user_id: str, *, admin_id: str) -> dict[str, Any]:
        result = await self._repository.approve_user(user_id, admin_id=admin_id, now=datetime.now(UTC))
        user = self._require_user(result.user)
        self._reject_admin_status_change(user)

        if not result.changed:
            if user.account_status == AccountStatus.ACTIVE and user.registration_approved_at is not None:
                return self.user_item(user)
            self._raise_invalid_transition(user, "approve")

        user = await self._deliver_approval_email(str(user.id))
        return self.user_item(user)

    async def disable(self, user_id: str) -> dict[str, Any]:
        result = await self._repository.disable_user(user_id)
        user = self._require_user(result.user)
        self._reject_admin_status_change(user)
        if not result.changed and user.account_status != AccountStatus.DISABLED:
            self._raise_invalid_transition(user, "disable")
        return self.user_item(user)

    async def enable(self, user_id: str) -> dict[str, Any]:
        result = await self._repository.enable_user(user_id)
        user = self._require_user(result.user)
        self._reject_admin_status_change(user)
        if not result.changed and user.account_status != AccountStatus.ACTIVE:
            self._raise_invalid_transition(user, "enable")
        return self.user_item(user)

    async def retry_approval_email(self, user_id: str) -> dict[str, Any]:
        user = self._require_user(await self._repository.get_user_by_id(user_id))
        self._reject_admin_status_change(user)
        if user.registration_approved_at is None or user.approval_email_status not in {
            ApprovalEmailStatus.FAILED,
            ApprovalEmailStatus.SENDING,
        }:
            raise UserManagementError(409, "approval_email_not_retryable", "Approval email is not retryable")

        now = datetime.now(UTC)
        stale_before = now - self._email_stale_after()
        if user.approval_email_status == ApprovalEmailStatus.SENDING and user.approval_email_last_attempt_at is not None and self._as_utc(user.approval_email_last_attempt_at) > stale_before:
            raise UserManagementError(409, "approval_email_in_progress", "Approval email is already being sent")

        return self.user_item(await self._deliver_approval_email(user_id, attempted_at=now))

    async def update_email(self, user_id: str, email: str) -> dict[str, Any]:
        user = self._require_user(await self._repository.get_user_by_id(user_id))
        if user.system_role == "admin":
            raise UserManagementError(
                409,
                "admin_profile_edit_not_allowed",
                "Administrator information cannot be edited",
            )
        if self._source(user) != "local":
            raise UserManagementError(
                409,
                "external_identity_email_not_editable",
                "Email is managed by an external identity provider",
            )

        enforce_email_domain_allowed(email, self._config.auth.allowed_email_domains)
        try:
            result = await self._repository.update_local_email(user_id, email)
        except ValueError as exc:
            raise UserManagementError(409, "email_already_exists", "Email is already registered") from exc

        updated = self._require_user(result.user)
        if not result.changed and self._source(updated) != "local":
            raise UserManagementError(
                409,
                "external_identity_email_not_editable",
                "Email is managed by an external identity provider",
            )
        return self.user_item(updated)

    def user_item(self, user: User) -> dict[str, Any]:
        source = self._source(user)
        return {
            "id": str(user.id),
            "email": str(user.email),
            "role": user.system_role,
            "source": source,
            "source_provider": user.oauth_provider if source != "local" else None,
            "created_at": user.created_at,
            "account_status": user.account_status,
            "registration_requested_at": user.registration_requested_at,
            "registration_approved_at": user.registration_approved_at,
            "registration_approved_by": user.registration_approved_by,
            "approval_email_status": user.approval_email_status,
            "approval_email_last_attempt_at": user.approval_email_last_attempt_at,
            "allowed_actions": self._allowed_actions(user, source),
        }

    async def _deliver_approval_email(
        self,
        user_id: str,
        *,
        attempted_at: datetime | None = None,
    ) -> User:
        now = attempted_at or datetime.now(UTC)
        claimed = await self._repository.claim_approval_email(
            user_id,
            attempted_at=now,
            stale_before=now - self._email_stale_after(),
        )
        user = self._require_user(claimed.user)
        if not claimed.changed:
            if user.approval_email_status == ApprovalEmailStatus.SENDING:
                raise UserManagementError(409, "approval_email_in_progress", "Approval email is already being sent")
            raise UserManagementError(409, "approval_email_not_retryable", "Approval email is not retryable")

        email_config = self._config.auth.local_registration.approval_email
        success = False
        try:
            await send_approval_email(email_config, str(user.email))
            success = True
        except Exception as exc:  # noqa: BLE001 - external SMTP errors are best-effort
            logger.warning(
                "Approval email delivery failed for user %s (%s)",
                user_id,
                type(exc).__name__,
            )

        finished = await self._repository.finish_approval_email(user_id, success=success)
        return self._require_user(finished.user)

    def _email_stale_after(self) -> timedelta:
        smtp = self._config.auth.local_registration.approval_email.smtp
        timeout_seconds = smtp.timeout_seconds if smtp is not None else 30
        return timedelta(seconds=max(60, timeout_seconds * 2))

    def _source(self, user: User) -> UserSource:
        if user.oauth_provider is None:
            return "local"
        if user.oauth_provider == self._config.platform_auth.provider:
            return "platform"
        return "oidc"

    @staticmethod
    def _allowed_actions(user: User, source: UserSource) -> list[UserAction]:
        actions: list[UserAction] = []
        if source == "local" and user.system_role != "admin":
            actions.append("edit")
        if user.system_role == "user":
            actions.append(_STATUS_ACTIONS[user.account_status])
            if user.registration_approved_at is not None and user.approval_email_status == ApprovalEmailStatus.FAILED:
                actions.append("retry_approval_email")
        return actions

    @staticmethod
    def _require_user(user: User | None) -> User:
        if user is None:
            raise UserManagementError(404, "user_not_found", "User not found")
        return user

    @staticmethod
    def _reject_admin_status_change(user: User) -> None:
        if user.system_role == "admin":
            raise UserManagementError(409, "admin_status_change_not_allowed", "Administrator status cannot be changed")

    @staticmethod
    def _raise_invalid_transition(user: User, action: str) -> None:
        raise UserManagementError(
            409,
            "invalid_account_status_transition",
            f"Cannot {action} an account in {user.account_status} status",
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
