"""V2 request dependencies.

Authentication is done in ``app.shared.security.supabase_auth`` — this module
only lifts the verified Supabase user into a session-bound ``CurrentAccount``
and provides the feature-flag gate.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity import service as identity
from app.domains.identity.models import Account
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import FeatureUnavailableError
from app.shared.flags import service as flags
from app.shared.security.supabase_auth import (
    SupabaseUser,
    get_current_supabase_user,
)


@dataclass
class CurrentAccount:
    """A signed-in caller.

    ``account_id`` is the canonical account UUID and equals the Supabase Auth
    user UUID.  ``supabase_user`` carries the raw verified claims for the rare
    caller that needs email or admin state.
    """

    account: Account
    supabase_user: SupabaseUser

    @property
    def account_id(self) -> uuid.UUID:
        return self.account.id

    @property
    def account_id_str(self) -> str:
        return str(self.account.id)

    @property
    def is_admin(self) -> bool:
        return self.supabase_user.is_admin


async def get_current_account(
    supabase_user: SupabaseUser = Depends(get_current_supabase_user),
    session: AsyncSession = Depends(get_session),
) -> CurrentAccount:
    """Resolve the caller and their ``accounts`` row, creating it on first use.

    Committing here means a later failure inside the request does not undo the
    create, and the next request does not have to redo it.
    """
    account = await identity.get_or_create_account(session, supabase_user.id)
    await session.commit()
    return CurrentAccount(account=account, supabase_user=supabase_user)


def client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def require_flag(key: str):
    """Dependency factory gating a route behind a feature flag.

    Returns 404 when off — a switched-off feature must look like it does not
    exist, not like something the caller is missing access to.
    """

    async def _dependency(session: AsyncSession = Depends(get_session)) -> None:
        if not await flags.is_enabled(session, key):
            raise FeatureUnavailableError(key)

    return _dependency


__all__ = [
    "CurrentAccount",
    "client_ip",
    "get_current_account",
    "get_current_supabase_user",
    "require_flag",
]
