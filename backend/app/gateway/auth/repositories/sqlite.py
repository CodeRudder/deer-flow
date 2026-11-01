"""SQLAlchemy-backed UserRepository implementation.

Uses the shared async session factory from
``deerflow.persistence.engine`` — the ``users`` table lives in the
same database as ``threads_meta``, ``runs``, ``run_events``, and
``feedback``.

Constructor takes the session factory directly (same pattern as the
other four repositories in ``deerflow.persistence.*``). Callers
construct this after ``init_engine_from_config()`` has run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.auth.models import (
    AccountStatus,
    ApprovalEmailStatus,
    User,
    UserMutationResult,
    UserPage,
    UserStatusSummary,
)
from app.gateway.auth.repositories.base import UserNotFoundError, UserRepository
from deerflow.persistence.user.model import UserRow


class SQLiteUserRepository(UserRepository):
    """Async user repository backed by the shared SQLAlchemy engine."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ── Converters ────────────────────────────────────────────────────

    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)

    @staticmethod
    def _row_to_user(row: UserRow) -> User:
        return User(
            id=UUID(row.id),
            email=row.email,
            password_hash=row.password_hash,
            system_role=row.system_role,  # type: ignore[arg-type]
            # SQLite loses tzinfo on read; reattach UTC so downstream
            # code can compare timestamps reliably.
            created_at=SQLiteUserRepository._utc(row.created_at),
            account_status=AccountStatus(row.account_status),
            registration_requested_at=SQLiteUserRepository._utc(row.registration_requested_at),
            registration_approved_at=SQLiteUserRepository._utc(row.registration_approved_at),
            registration_approved_by=row.registration_approved_by,
            approval_email_status=ApprovalEmailStatus(row.approval_email_status) if row.approval_email_status is not None else None,
            approval_email_last_attempt_at=SQLiteUserRepository._utc(row.approval_email_last_attempt_at),
            oauth_provider=row.oauth_provider,
            oauth_id=row.oauth_id,
            needs_setup=row.needs_setup,
            token_version=row.token_version,
        )

    @staticmethod
    def _user_to_row(user: User) -> UserRow:
        return UserRow(
            id=str(user.id),
            email=user.email,
            password_hash=user.password_hash,
            system_role=user.system_role,
            created_at=user.created_at,
            account_status=user.account_status.value,
            registration_requested_at=user.registration_requested_at,
            registration_approved_at=user.registration_approved_at,
            registration_approved_by=user.registration_approved_by,
            approval_email_status=user.approval_email_status.value if user.approval_email_status is not None else None,
            approval_email_last_attempt_at=user.approval_email_last_attempt_at,
            oauth_provider=user.oauth_provider,
            oauth_id=user.oauth_id,
            needs_setup=user.needs_setup,
            token_version=user.token_version,
        )

    # ── CRUD ──────────────────────────────────────────────────────────

    async def create_user(self, user: User) -> User:
        """Insert a new user. Raises ``ValueError`` on duplicate email."""
        row = self._user_to_row(user)
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ValueError(f"Email already registered: {user.email}") from exc
        return user

    async def get_user_by_id(self, user_id: str) -> User | None:
        async with self._sf() as session:
            row = await session.get(UserRow, user_id)
            return self._row_to_user(row) if row is not None else None

    async def get_user_by_email(self, email: str) -> User | None:
        stmt = select(UserRow).where(UserRow.email == email)
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._row_to_user(row) if row is not None else None

    async def get_user_summary(self) -> UserStatusSummary:
        stmt = select(
            func.count().label("total"),
            *(func.sum(case((UserRow.account_status == account_status.value, 1), else_=0)).label(account_status.value) for account_status in AccountStatus),
        ).select_from(UserRow)
        async with self._sf() as session:
            row = (await session.execute(stmt)).one()
        counts = {account_status.value: getattr(row, account_status.value) or 0 for account_status in AccountStatus}
        return UserStatusSummary(total=row.total or 0, **counts)

    async def list_users(
        self,
        *,
        status: AccountStatus | None,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> UserPage:
        if page < 1 or page_size < 1:
            raise ValueError("page and page_size must be positive")

        normalized_status = AccountStatus(status) if status is not None else None
        filters = []
        if normalized_status is not None:
            filters.append(UserRow.account_status == normalized_status.value)
        normalized_keyword = keyword.strip().lower() if keyword else ""
        if normalized_keyword:
            filters.append(
                or_(
                    func.lower(UserRow.id).contains(normalized_keyword, autoescape=True),
                    func.lower(UserRow.email).contains(normalized_keyword, autoescape=True),
                )
            )

        count_stmt = select(func.count()).select_from(UserRow).where(*filters)
        items_stmt = select(UserRow).where(*filters)
        if normalized_status == AccountStatus.PENDING:
            items_stmt = items_stmt.order_by(
                func.coalesce(UserRow.registration_requested_at, UserRow.created_at).asc(),
                UserRow.id.asc(),
            )
        else:
            items_stmt = items_stmt.order_by(UserRow.created_at.desc(), UserRow.id.asc())
        items_stmt = items_stmt.offset((page - 1) * page_size).limit(page_size)

        async with self._sf() as session:
            total = await session.scalar(count_stmt) or 0
            rows = (await session.scalars(items_stmt)).all()
        return UserPage(
            items=[self._row_to_user(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def _conditional_update(self, user_id: str, stmt) -> UserMutationResult:
        async with self._sf() as session:
            result = await session.execute(stmt.execution_options(synchronize_session=False))
            changed = result.rowcount == 1
            row = await session.get(UserRow, user_id)
            user = self._row_to_user(row) if row is not None else None
            await session.commit()
        return UserMutationResult(user=user, changed=changed)

    async def approve_user(self, user_id: str, *, admin_id: str, now: datetime) -> UserMutationResult:
        stmt = (
            update(UserRow)
            .where(
                UserRow.id == user_id,
                UserRow.system_role == "user",
                UserRow.account_status == AccountStatus.PENDING.value,
            )
            .values(
                account_status=AccountStatus.ACTIVE.value,
                registration_approved_at=now,
                registration_approved_by=admin_id,
                approval_email_status=ApprovalEmailStatus.PENDING.value,
            )
        )
        return await self._conditional_update(user_id, stmt)

    async def disable_user(self, user_id: str) -> UserMutationResult:
        stmt = (
            update(UserRow)
            .where(
                UserRow.id == user_id,
                UserRow.system_role == "user",
                UserRow.account_status == AccountStatus.ACTIVE.value,
            )
            .values(
                account_status=AccountStatus.DISABLED.value,
                token_version=UserRow.token_version + 1,
            )
        )
        return await self._conditional_update(user_id, stmt)

    async def enable_user(self, user_id: str) -> UserMutationResult:
        stmt = (
            update(UserRow)
            .where(
                UserRow.id == user_id,
                UserRow.system_role == "user",
                UserRow.account_status == AccountStatus.DISABLED.value,
            )
            .values(account_status=AccountStatus.ACTIVE.value)
        )
        return await self._conditional_update(user_id, stmt)

    async def claim_approval_email(
        self,
        user_id: str,
        *,
        attempted_at: datetime,
        stale_before: datetime,
    ) -> UserMutationResult:
        claimable = or_(
            UserRow.approval_email_status.in_((ApprovalEmailStatus.PENDING.value, ApprovalEmailStatus.FAILED.value)),
            and_(
                UserRow.approval_email_status == ApprovalEmailStatus.SENDING.value,
                or_(
                    UserRow.approval_email_last_attempt_at.is_(None),
                    UserRow.approval_email_last_attempt_at <= stale_before,
                ),
            ),
        )
        stmt = (
            update(UserRow)
            .where(UserRow.id == user_id, claimable)
            .values(
                approval_email_status=ApprovalEmailStatus.SENDING.value,
                approval_email_last_attempt_at=attempted_at,
            )
        )
        return await self._conditional_update(user_id, stmt)

    async def finish_approval_email(self, user_id: str, *, success: bool) -> UserMutationResult:
        stmt = (
            update(UserRow)
            .where(
                UserRow.id == user_id,
                UserRow.approval_email_status == ApprovalEmailStatus.SENDING.value,
            )
            .values(approval_email_status=ApprovalEmailStatus.SENT.value if success else ApprovalEmailStatus.FAILED.value)
        )
        return await self._conditional_update(user_id, stmt)

    async def update_local_email(self, user_id: str, email: str) -> UserMutationResult:
        stmt = (
            update(UserRow)
            .where(
                UserRow.id == user_id,
                UserRow.oauth_provider.is_(None),
                UserRow.email != email,
            )
            .values(
                email=email,
                token_version=UserRow.token_version + 1,
            )
        )
        try:
            return await self._conditional_update(user_id, stmt)
        except IntegrityError as exc:
            raise ValueError(f"Email already registered: {email}") from exc

    async def update_user(self, user: User) -> User:
        async with self._sf() as session:
            row = await session.get(UserRow, str(user.id))
            if row is None:
                # Hard fail on concurrent delete: callers (reset_admin,
                # password change handlers, _ensure_admin_user) all
                # fetched the user just before this call, so a missing
                # row here means the row vanished underneath us. Silent
                # success would let the caller log "password reset" for
                # a row that no longer exists.
                raise UserNotFoundError(f"User {user.id} no longer exists")
            row.email = user.email
            row.password_hash = user.password_hash
            row.system_role = user.system_role
            row.account_status = user.account_status.value
            row.registration_requested_at = user.registration_requested_at
            row.registration_approved_at = user.registration_approved_at
            row.registration_approved_by = user.registration_approved_by
            row.approval_email_status = user.approval_email_status.value if user.approval_email_status is not None else None
            row.approval_email_last_attempt_at = user.approval_email_last_attempt_at
            row.oauth_provider = user.oauth_provider
            row.oauth_id = user.oauth_id
            row.needs_setup = user.needs_setup
            row.token_version = user.token_version
            await session.commit()
        return user

    async def count_users(self) -> int:
        stmt = select(func.count()).select_from(UserRow)
        async with self._sf() as session:
            return await session.scalar(stmt) or 0

    async def count_admin_users(self) -> int:
        stmt = select(func.count()).select_from(UserRow).where(UserRow.system_role == "admin")
        async with self._sf() as session:
            return await session.scalar(stmt) or 0

    async def get_user_by_oauth(self, provider: str, oauth_id: str) -> User | None:
        stmt = select(UserRow).where(UserRow.oauth_provider == provider, UserRow.oauth_id == oauth_id)
        async with self._sf() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._row_to_user(row) if row is not None else None
