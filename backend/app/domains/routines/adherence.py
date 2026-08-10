"""Read-only interpretations of durable routine adherence."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.routines.models import Routine, RoutineAdherence

WASH_DAY_CORE_SLOTS = frozenset({"shampoo", "conditioner"})


async def last_completed_wash_on(
    session: AsyncSession, *, account_id: uuid.UUID, through: date,
) -> date | None:
    """Return the latest qualifying wash date, including retired routines."""
    return await session.scalar(
        select(func.max(RoutineAdherence.done_on)).join(
            Routine, Routine.id == RoutineAdherence.routine_id,
        ).where(
            RoutineAdherence.account_id == account_id,
            Routine.kind == "wash_day",
            RoutineAdherence.slot.in_(WASH_DAY_CORE_SLOTS),
            RoutineAdherence.completed.is_(True),
            RoutineAdherence.done_on <= through,
        )
    )


__all__ = ["WASH_DAY_CORE_SLOTS", "last_completed_wash_on"]
