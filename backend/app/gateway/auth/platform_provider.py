"""Shadow-user provisioning for company gateway JWT identities."""

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError

from app.gateway.auth.models import User
from app.gateway.auth.password import hash_password_async
from app.gateway.auth.platform_jwt import PlatformUserClaims
from app.gateway.auth.repositories.base import UserRepository
from deerflow.config.platform_auth_config import PlatformAuthConfig

logger = logging.getLogger(__name__)


class PlatformAuthProvider:
    """Map platform JWT identities to local DeerFlow shadow users."""

    def __init__(self, repository: UserRepository, config: PlatformAuthConfig):
        self._repo = repository
        self._config = config

    async def get_or_create_user_from_claims(self, claims: PlatformUserClaims) -> User:
        external_user_id = self._external_user_id(claims)
        user = await self._repo.get_user_by_oauth(self._config.provider, external_user_id)
        if user is not None:
            return await self._sync_existing_user(user, claims)

        if not self._config.auto_provision:
            raise ValueError("Platform user is not provisioned and auto_provision=false")

        by_email = await self._repo.get_user_by_email(claims.email)
        if by_email is not None:
            return await self._bind_existing_email_user(by_email, claims)

        return await self._create_shadow_user_with_retry(claims)

    @staticmethod
    def _external_user_id(claims: PlatformUserClaims) -> str:
        return claims.name

    async def _sync_existing_user(self, user: User, claims: PlatformUserClaims) -> User:
        changed = False
        if user.email != claims.email:
            user.email = claims.email
            changed = True
        if user.password_hash is None:
            user.password_hash = await hash_password_async(self._config.default_password)
            changed = True
        if changed:
            return await self._repo.update_user(user)
        return user

    async def _bind_existing_email_user(self, user: User, claims: PlatformUserClaims) -> User:
        changed = False
        if user.oauth_provider != self._config.provider:
            user.oauth_provider = self._config.provider
            changed = True
        external_user_id = self._external_user_id(claims)
        if user.oauth_id != external_user_id:
            user.oauth_id = external_user_id
            changed = True
        if user.email != claims.email:
            user.email = claims.email
            changed = True
        if user.password_hash is None:
            user.password_hash = await hash_password_async(self._config.default_password)
            changed = True
        if changed:
            try:
                return await self._repo.update_user(user)
            except (IntegrityError, ValueError):
                logger.info("Platform user bind raced; reloading identity", exc_info=True)
                reloaded = await self._reload_after_integrity_race(claims)
                if reloaded is not None:
                    return reloaded
                raise
        return user

    async def _create_shadow_user_with_retry(self, claims: PlatformUserClaims) -> User:
        user = User(
            email=claims.email,
            password_hash=await hash_password_async(self._config.default_password),
            system_role="user",
            oauth_provider=self._config.provider,
            oauth_id=self._external_user_id(claims),
            needs_setup=False,
        )
        try:
            return await self._repo.create_user(user)
        except (IntegrityError, ValueError):
            logger.info("Platform user create raced; reloading identity", exc_info=True)
            reloaded = await self._reload_after_integrity_race(claims)
            if reloaded is not None:
                return reloaded
            raise

    async def _reload_after_integrity_race(self, claims: PlatformUserClaims) -> User | None:
        user = await self._repo.get_user_by_oauth(self._config.provider, self._external_user_id(claims))
        if user is not None:
            return await self._sync_existing_user(user, claims)
        user = await self._repo.get_user_by_email(claims.email)
        if user is not None:
            return await self._bind_existing_email_user(user, claims)
        return None
