"""V2 request dependencies.

Authentication is handled in ``app.shared.security.supabase_auth`` — that
module verifies the Supabase JWT and produces a ``SupabaseUser``. This module
adds two more gates, and they are two different questions:

* ``get_registered_account`` — does this verified Supabase identity have a
  GlamGenius ``accounts`` row at all? If not, 403 ``REGISTRATION_REQUIRED``.
  Whatever the account's lifecycle status. Used only by the account-deletion
  lifecycle routes, which must still recognise the owner of a pending
  deletion.
* ``get_current_account`` — is it an **active** account? Everything above,
  and ``accounts.status == 'active'``; otherwise 403 ``ACCOUNT_INACTIVE``.
  Every ordinary product route uses this, so an account whose deletion has
  been requested stops reading and writing product data at once, from one
  place, without a status check in each route.

The only route that accepts a raw ``SupabaseUser`` is
``POST /api/v2/access/register``, which is where the ``accounts`` row is
created in the same transaction as invite redemption.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity import service as identity
from app.domains.identity.models import ACCOUNT_STATUS_ACTIVE, Account
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import FeatureUnavailableError
from app.shared.flags import service as flags
from app.shared.security.network import client_ip
from app.shared.security.supabase_auth import (
    SupabaseUser,
    _bearer,
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


class AccountInactiveError(HTTPException):
    """403 raised when a registered account is not ``active``.

    In practice the account has asked to be deleted. It is registered, so
    ``REGISTRATION_REQUIRED`` would send the client down the wrong path. The
    body says nothing about the deletion job: the owner reads that from the
    privacy status route.
    """

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ACCOUNT_INACTIVE",
                "message": (
                    "This GlamGenius account is not active. If you asked for "
                    "it to be deleted, its deletion status is still available "
                    "in your privacy settings."
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


async def get_registered_account(
    supabase_user: SupabaseUser = Depends(get_current_supabase_user),
    session: AsyncSession = Depends(get_session),
) -> CurrentAccount:
    """Resolve the caller's GlamGenius account, whatever its lifecycle status.

    **Does not auto-create.** A valid Supabase token without a matching
    ``accounts`` row is refused with 403 ``REGISTRATION_REQUIRED``. The only
    place that creates an ``accounts`` row is
    ``POST /api/v2/access/register`` (through
    ``domains.identity.service.register_account``), and it does so atomically
    with invite redemption.

    Not for product routes. It is for the narrow account-deletion lifecycle
    only — request, status, cancel — where the owner of a pending deletion
    must still be recognised. Everything else uses
    :func:`get_current_account`.
    """
    account = await identity.get_account(session, supabase_user.id)
    if account is None:
        raise RegistrationRequiredError()
    return CurrentAccount(account=account, supabase_user=supabase_user)


async def get_current_account(
    registered: CurrentAccount = Depends(get_registered_account),
) -> CurrentAccount:
    """Resolve the caller's **active** GlamGenius account.

    A registered account whose status is not ``active`` — in practice one
    whose deletion has been requested — is refused with 403
    ``ACCOUNT_INACTIVE``, so no ordinary route can read or write product data
    for it, and none can create new data while the deletion runs.
    """
    if registered.account.status != ACCOUNT_STATUS_ACTIVE:
        raise AccountInactiveError()
    return registered


async def get_optional_account(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> CurrentAccount | None:
    """The caller's account when a token is presented, or ``None`` when none is.

    For the few routes an anonymous scanning device may call that also carry
    per-person context when somebody is signed in. Absence is the only thing
    that makes a caller anonymous: a token that is presented is verified
    exactly as :func:`get_current_account` verifies it, so an invalid or
    expired token is still a 401, a registered-less identity is still a 403,
    and an account that is not active is still a 403 ``ACCOUNT_INACTIVE`` —
    never a silent downgrade to anonymous.
    """
    if credentials is None or not credentials.credentials:
        return None
    supabase_user = await get_current_supabase_user(credentials)
    registered = await get_registered_account(supabase_user, session)
    return await get_current_account(registered)


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
    "AccountInactiveError",
    "CurrentAccount",
    "RegistrationRequiredError",
    "client_ip",
    "get_current_account",
    "get_optional_account",
    "get_current_supabase_user",
    "get_registered_account",
    "require_flag",
]
