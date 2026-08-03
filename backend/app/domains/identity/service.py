"""Get-or-create an ``accounts`` row for a verified Supabase user."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import Account


async def get_or_create_account(
    session: AsyncSession, supabase_user_id: uuid.UUID
) -> Account:
    """Return the ``accounts`` row for ``supabase_user_id``, creating one on
    first request.

    Two concurrent first requests from the same user both try to insert.
    ``ON CONFLICT DO NOTHING`` plus a re-read makes that safe.
    """
    existing = await get_account(session, supabase_user_id)
    if existing is not None:
        return existing

    await session.execute(
        pg_insert(Account)
        .values(id=supabase_user_id)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    await session.flush()

    created = await get_account(session, supabase_user_id)
    if created is None:  # pragma: no cover - only if the row vanished
        raise RuntimeError(
            f"Could not create an account row for {supabase_user_id}"
        )
    return created


async def get_account(
    session: AsyncSession, supabase_user_id: uuid.UUID
) -> Optional[Account]:
    stmt = select(Account).where(Account.id == supabase_user_id)
    return (await session.execute(stmt)).scalar_one_or_none()
