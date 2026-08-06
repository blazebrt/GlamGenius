"""V2 request dependencies.

Authentication is handled in ``app.shared.security.supabase_auth`` — that
module verifies the Supabase JWT and produces a ``SupabaseUser``. This module
adds the second gate: a verified Supabase identity is **not** on its own a
GlamGenius account. Protected routes must call ``get_current_account``, which
looks up the ``accounts`` row and refuses (403 REGISTRATION_REQUIRED) if the
caller has never completed invite-gated registration.

The only route that accepts a raw ``SupabaseUser`` is
``POST /api/v2/access/register``, which is where the ``accounts`` row is
created in the same transaction as invite redemption.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
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


class RegistrationRequiredError(HTTPException):
    """403 raised when a valid Supabase user has no GlamGenius account row.

    The Supabase token is fine; the caller has simply never redeemed an
    invite. Distinct from 401 so the client knows to send the user to the
    registration flow, not the login flow.
    """

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "REGISTRATION_REQUIRED",
                "message": (
                    "Your Supabase account is authenticated, but you have not "
                    "yet completed the GlamGenius invite-only registration. "
                    "Redeem your invite to continue."
                ),
                "retryable": False,
            },
        )


@dataclass
class CurrentAccount:
    """A caller with a completed GlamGenius account.

    ``account_id`` is the canonical account UUID and equals the Supabase Auth
    user UUID. There is no separate identity or bridge; the invariant
    ``accounts.id == auth.users.id`` is enforced everywhere.
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
    def supabase_user_id(self) -> uuid.UUID:
        """Alias that reads well at call sites that emphasise identity."""
        return self.account.id

    @property
    def is_admin(self) -> bool:
        return self.supabase_user.is_admin


async def get_current_account(
    supabase_user: SupabaseUser = Depends(get_current_supabase_user),
    session: AsyncSession = Depends(get_session),
) -> CurrentAccount:
    """Resolve the caller's GlamGenius account.

    **Does not auto-create.** A valid Supabase token without a matching
    ``accounts`` row is refused with 403 ``REGISTRATION_REQUIRED``. The only
    place that creates an ``accounts`` row is
    ``POST /api/v2/access/register`` (through
    ``domains.identity.service.register_account``), and it does so atomically
    with invite redemption.
    """
    account = await identity.get_account(session, supabase_user.id)
    if account is None:
        raise RegistrationRequiredError()
    return CurrentAccount(account=account, supabase_user=supabase_user)


# Kept for symmetry with the spec §1 wording. The FastAPI dependency object
# is the same callable — renaming the import site is optional and does not
# change semantics.
get_registered_account = get_current_account


def client_ip(request: Request) -> str | None:
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
    "RegistrationRequiredError",
    "client_ip",
    "get_current_account",
    "get_current_supabase_user",
    "get_registered_account",
    "require_flag",
]
