"""V2 request dependencies.

Authentication is **not** reimplemented here. ``get_current_user`` from the V1
``security`` module stays the single auth gate for the whole application —
identity always comes from the signed token. This layer only adds the V2 account
link on top of it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity import service as identity
from app.domains.identity.models import AccountLink
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import FeatureUnavailableError
from app.shared.flags import service as flags
from security import get_current_user


@dataclass
class CurrentAccount:
    """A signed-in caller, on both sides of the V1/V2 line."""

    user: Dict[str, Any]
    account: AccountLink

    @property
    def v1_user_id(self) -> str:
        return str(self.user["id"])

    @property
    def account_id(self):
        return self.account.id


async def get_current_account(
    user: Dict[str, Any] = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CurrentAccount:
    """Resolve the caller and their V2 account link, creating it on first use."""
    account = await identity.get_or_create_account(session, str(user["id"]))
    # The link is a read-through cache of "this user exists in V2". Committing
    # here means a later failure in the request does not undo it, and the next
    # request does not have to create it again.
    await session.commit()
    return CurrentAccount(user=user, account=account)


def client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def require_flag(key: str):
    """Dependency factory gating a route behind a feature flag.

    Returns 404 rather than 403 when off — a switched-off feature should look
    like it does not exist, not like something the caller is missing access to.
    """

    async def _dependency(session: AsyncSession = Depends(get_session)) -> None:
        if not await flags.is_enabled(session, key):
            raise FeatureUnavailableError(key)

    return _dependency
