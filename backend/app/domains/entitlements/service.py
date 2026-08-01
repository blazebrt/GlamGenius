"""Recording what a user actually consumed."""
from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Optional

from sqlalchemy import func, select

from app.domains.entitlements.models import UsageLedgerEntry
from app.domains.identity import service as identity
from app.shared.database.sql import get_sessionmaker

logger = logging.getLogger(__name__)


async def record_consumption(
    *,
    v1_user_id: str,
    feature: str,
    period_key: str,
    reason: str = "analysis_succeeded",
    quantity: int = 1,
    ai_run_id: Optional[uuid.UUID] = None,
) -> None:
    """Note that one unit of allowance was used.

    Opens its own session and never raises. The V1 counter on the Mongo user
    document remains authoritative for enforcement in this phase — this ledger
    is the itemised record beside it. A bookkeeping failure must not fail a scan
    the user has already paid for and received.
    """
    try:
        factory = get_sessionmaker()
        async with factory() as session:
            account = await identity.get_or_create_account(session, v1_user_id)
            session.add(
                UsageLedgerEntry(
                    account_id=account.id,
                    feature=feature,
                    quantity=quantity,
                    period_key=period_key,
                    reason=reason,
                    ai_run_id=ai_run_id,
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - see docstring
        logger.exception(
            "usage_ledger_write_failed feature=%s period=%s", feature, period_key
        )


async def totals_for_period(
    session, account_id: uuid.UUID, period_key: str
) -> Dict[str, int]:
    """How much of each feature this account used in a period."""
    stmt = (
        select(UsageLedgerEntry.feature, func.sum(UsageLedgerEntry.quantity))
        .where(UsageLedgerEntry.account_id == account_id)
        .where(UsageLedgerEntry.period_key == period_key)
        .group_by(UsageLedgerEntry.feature)
    )
    rows = (await session.execute(stmt)).all()
    return {feature: int(total or 0) for feature, total in rows}


async def entries_for_account(
    session, account_id: uuid.UUID, limit: int = 500
) -> List[UsageLedgerEntry]:
    stmt = (
        select(UsageLedgerEntry)
        .where(UsageLedgerEntry.account_id == account_id)
        .order_by(UsageLedgerEntry.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())
