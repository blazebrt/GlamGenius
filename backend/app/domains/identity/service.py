"""Resolving a V1 user into a V2 account link."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import AccountLink


async def get_or_create_account(session: AsyncSession, v1_user_id: str) -> AccountLink:
    """The account link for a V1 user, creating it on first use.

    Two concurrent first requests from the same user would both try to insert.
    ``ON CONFLICT DO NOTHING`` plus a re-read makes that safe without a lock and
    without the loser of the race seeing an error.
    """
    existing = await get_account(session, v1_user_id)
    if existing is not None:
        return existing

    await session.execute(
        pg_insert(AccountLink)
        .values(v1_user_id=v1_user_id)
        .on_conflict_do_nothing(index_elements=["v1_user_id"])
    )
    await session.flush()

    created = await get_account(session, v1_user_id)
    if created is None:  # pragma: no cover - only reachable if the insert vanished
        raise RuntimeError(f"Could not create an account link for {v1_user_id}")
    return created


async def get_account(
    session: AsyncSession, v1_user_id: str
) -> Optional[AccountLink]:
    stmt = select(AccountLink).where(AccountLink.v1_user_id == v1_user_id)
    return (await session.execute(stmt)).scalar_one_or_none()
