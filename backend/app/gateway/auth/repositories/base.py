"""User repository interface for abstracting database operations."""

from abc import ABC, abstractmethod
from datetime import datetime

from app.gateway.auth.models import AccountStatus, User, UserMutationResult, UserPage, UserStatusSummary


class UserNotFoundError(LookupError):
    """Raised when a user repository operation targets a non-existent row.

    Subclass of :class:`LookupError` so callers that already catch
    ``LookupError`` for "missing entity" can keep working unchanged,
    while specific call sites can pin to this class to distinguish
    "concurrent delete during update" from other lookups.
    """


class UserRepository(ABC):
    """Abstract interface for user data storage.

    Implement this interface to support different storage backends
    (SQLite)
    """

    @abstractmethod
    async def create_user(self, user: User) -> User:
        """Create a new user.

        Args:
            user: User object to create

        Returns:
            Created User with ID assigned

        Raises:
            ValueError: If email already exists
        """
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_id(self, user_id: str) -> User | None:
        """Get user by ID.

        Args:
            user_id: User UUID as string

        Returns:
            User if found, None otherwise
        """
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_email(self, email: str) -> User | None:
        """Get user by email.

        Args:
            email: User email address

        Returns:
            User if found, None otherwise
        """
        raise NotImplementedError

    @abstractmethod
    async def get_user_summary(self) -> UserStatusSummary:
        """Return total and per-account-status user counts."""
        raise NotImplementedError

    @abstractmethod
    async def list_users(
        self,
        *,
        status: AccountStatus | None,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> UserPage:
        """Return a filtered, ordered page of users."""
        raise NotImplementedError

    @abstractmethod
    async def approve_user(self, user_id: str, *, admin_id: str, now: datetime) -> UserMutationResult:
        """Atomically transition a normal user from pending to active."""
        raise NotImplementedError

    @abstractmethod
    async def disable_user(self, user_id: str) -> UserMutationResult:
        """Atomically disable an active normal user and revoke sessions."""
        raise NotImplementedError

    @abstractmethod
    async def enable_user(self, user_id: str) -> UserMutationResult:
        """Atomically restore a disabled normal user."""
        raise NotImplementedError

    @abstractmethod
    async def claim_approval_email(
        self,
        user_id: str,
        *,
        attempted_at: datetime,
        stale_before: datetime,
    ) -> UserMutationResult:
        """Try to claim the approval-notification send for one request."""
        raise NotImplementedError

    @abstractmethod
    async def finish_approval_email(self, user_id: str, *, success: bool) -> UserMutationResult:
        """Finish a claimed approval-notification send."""
        raise NotImplementedError

    @abstractmethod
    async def update_local_email(self, user_id: str, email: str) -> UserMutationResult:
        """Atomically update a local account email and revoke its sessions."""
        raise NotImplementedError

    @abstractmethod
    async def update_user(self, user: User) -> User:
        """Update an existing user.

        Args:
            user: User object with updated fields

        Returns:
            Updated User

        Raises:
            UserNotFoundError: If no row exists for ``user.id``. This is
                a hard failure (not a no-op) so callers cannot mistake a
                concurrent-delete race for a successful update.
        """
        raise NotImplementedError

    @abstractmethod
    async def count_users(self) -> int:
        """Return total number of registered users."""
        raise NotImplementedError

    @abstractmethod
    async def count_admin_users(self) -> int:
        """Return number of users with system_role == 'admin'."""
        raise NotImplementedError

    @abstractmethod
    async def get_user_by_oauth(self, provider: str, oauth_id: str) -> User | None:
        """Get user by OAuth provider and ID.

        Args:
            provider: OAuth provider name (e.g. 'github', 'google')
            oauth_id: User ID from the OAuth provider

        Returns:
            User if found, None otherwise
        """
        raise NotImplementedError
