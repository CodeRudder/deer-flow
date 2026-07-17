"""User Pydantic models for authentication."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from deerflow.persistence.user.status import AccountStatus, ApprovalEmailStatus


def _utc_now() -> datetime:
    """Return current UTC time (timezone-aware)."""
    return datetime.now(UTC)


class User(BaseModel):
    """Internal user representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4, description="Primary key")
    email: EmailStr = Field(..., description="Unique email address")
    password_hash: str | None = Field(None, description="bcrypt hash, nullable for OAuth users")
    system_role: Literal["admin", "user"] = Field(default="user")
    created_at: datetime = Field(default_factory=_utc_now)

    # Registration approval lifecycle
    account_status: AccountStatus = AccountStatus.ACTIVE
    registration_requested_at: datetime | None = None
    registration_approved_at: datetime | None = None
    registration_approved_by: str | None = None
    approval_email_status: ApprovalEmailStatus | None = None
    approval_email_last_attempt_at: datetime | None = None

    # OAuth linkage (optional)
    oauth_provider: str | None = Field(None, description="e.g. 'github', 'google'")
    oauth_id: str | None = Field(None, description="User ID from OAuth provider")

    # Auth lifecycle
    needs_setup: bool = Field(default=False, description="True when a reset account must complete setup")
    token_version: int = Field(default=0, description="Incremented on password change to invalidate old JWTs")


class UserStatusSummary(BaseModel):
    """Unpaginated account-status counts for the administrator dashboard."""

    total: int = 0
    active: int = 0
    pending: int = 0
    disabled: int = 0


class UserPage(BaseModel):
    """A server-paginated user query result."""

    items: list[User]
    total: int
    page: int
    page_size: int


class UserMutationResult(BaseModel):
    """Current row plus whether this call won its conditional update."""

    user: User | None
    changed: bool


class UserResponse(BaseModel):
    """Response model for user info endpoint."""

    id: str
    email: str
    system_role: Literal["admin", "user"]
    needs_setup: bool = False
    oauth_provider: str | None = Field(None, description="OAuth/SSO provider ID if the user logged in via SSO (e.g. 'keycloak')")
