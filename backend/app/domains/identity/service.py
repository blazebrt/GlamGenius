"""Identity domain: lookup and explicit registration of ``accounts`` rows.

The ``accounts`` row keyed on the Supabase Auth user UUID is the canonical
GlamGenius identity. A Supabase token alone does **not** produce one — the
row exists only after ``register_account`` runs successfully, which is
called only from ``POST /api/v2/access/register``.

Callers that need an account object must go through
``app.shared.security.deps.get_current_account``, which returns 403
``REGISTRATION_REQUIRED`` when the row is missing. That is the invite-bypass
gate.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import Account


async def lock_account_against_delete(
    session: AsyncSession, account_id: uuid.UUID,
) -> uuid.UUID | None:
    """Hold the account row against deletion. Returns its id, or ``None`` if gone.

    One place emits this lock, because two places emitting *almost* the same one
    is how a lock order quietly stops being a lock order. It lives in the domain
    that owns ``accounts`` so that neither caller has to import the other's
    identity module to hold still the row they both depend on.

    ``FOR KEY SHARE`` is the weakest row lock PostgreSQL offers that still
    conflicts with ``DELETE``. That is exactly the guarantee wanted and no more:
    while a child row is being written the account it hangs off must not
    disappear, and two holders of this lock do not block each other, so
    unrelated work on the same account carries on.

    The reason it must be taken *first*, rather than merely somewhere, is the
    part that is easy to get wrong. Inserting any row whose ``account_id`` is an
    immediate foreign key makes PostgreSQL check the parent and take this same
    lock on its own. Taken explicitly at the start, the application's order
    matches the one the database will take anyway. Left implicit, it is taken
    *after* whatever child row the caller locked first — which is the reverse of
    account deletion's order (account first, then cascade into children) and
    deadlocks against it.

    Deliberately returns rather than raising: "the account is gone" is the same
    fact at every boundary, and each of them owes its caller a different
    sentence. Choosing one here would export one boundary's vocabulary to all
    the others.
    """
    return await session.scalar(
        select(Account.id)
        .where(Account.id == account_id)
        .with_for_update(read=True, key_share=True)
    )


async def get_account(
    session: AsyncSession, supabase_user_id: uuid.UUID
) -> Account | None:
    """Return the ``accounts`` row for a Supabase UUID or ``None``.

    Never creates. Never mutates. Safe on any protected request.
    """
    stmt = select(Account).where(Account.id == supabase_user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def register_account(
    session: AsyncSession, supabase_user_id: uuid.UUID
) -> Account:
    """Create the ``accounts`` row after invite redemption succeeded.

    Called from ``POST /api/v2/access/register`` inside the same transaction
    that redeems the invite. ``ON CONFLICT DO NOTHING`` keeps the call
    idempotent: a repeated finalisation from the same Supabase user does not
    duplicate rows and does not fail.
    """
    await session.execute(
        pg_insert(Account)
        .values(id=supabase_user_id)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    await session.flush()
    row = await get_account(session, supabase_user_id)
    if row is None:  # pragma: no cover - only if the row vanished
        raise RuntimeError(
            f"Could not create the accounts row for {supabase_user_id}"
        )
    return row


__all__ = ["get_account", "register_account"]
