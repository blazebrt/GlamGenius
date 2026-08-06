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
