"""Skin and Hair maintenance timing routes (VC-06).

Timing only. Nothing here books, recommends or prices a service, and no route
takes an account identifier — ownership always comes from the token.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care import maintenance_service
from app.domains.care.maintenance_rules import MAX_INTERVAL_DAYS, MIN_INTERVAL_DAYS
from app.domains.care.schemas import MaintenanceDoneRequest, MaintenancePreferenceRequest
from app.domains.planning import clock
from app.domains.planning import context as context_stage
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_routines"))])

SUMMARY_NOTE = (
    "These are timing reminders for upkeep you already do. GlamGenius does not "
    "book appointments or suggest places."
)


async def _today_for(session: AsyncSession, account_id) -> date:
    timezone_name = await context_stage.resolve_timezone_for(session, account_id)
    return clock.local_today(timezone_name)


@router.get("/maintenance")
async def list_maintenance(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Every maintenance kind, with where each one stands today."""
    plan_date = await _today_for(session, current.account_id)
    decided = await maintenance_service.build_maintenance(
        session, current.account_id, plan_date=plan_date,
    )
    payload = decided.as_payload()
    payload.update({
        "note": SUMMARY_NOTE,
        "interval_bounds": {"min_days": MIN_INTERVAL_DAYS, "max_days": MAX_INTERVAL_DAYS},
        "due": [row.kind_key for row in decided.due],
        "coming_up": [row.kind_key for row in decided.coming_up],
        "needs_cadence": [row.kind_key for row in decided.needs_cadence],
        "needs_anchor": [row.kind_key for row in decided.needs_anchor],
    })
    return payload


@router.put("/maintenance/{kind_key}")
async def update_maintenance(
    kind_key: str,
    body: MaintenancePreferenceRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Track or stop tracking a kind, set your own rhythm, or set reminders."""
    fields = body.model_dump(exclude_unset=True)
    await maintenance_service.set_preference(
        session, current.account_id, kind_key,
        tracked=fields.get("tracked"),
        interval_days=fields.get("interval_days"),
        clear_interval="interval_days" in fields and fields["interval_days"] is None,
        reminders_enabled=fields.get("reminders_enabled"),
    )
    await session.commit()
    return await list_maintenance(current=current, session=session)


@router.post("/maintenance/{kind_key}/done")
async def record_maintenance(
    kind_key: str,
    body: MaintenanceDoneRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Record a date this upkeep happened, so timing has something to work from."""
    plan_date = await _today_for(session, current.account_id)
    await maintenance_service.record_done(
        session, current.account_id, kind_key,
        done_on=body.done_on or plan_date, today=plan_date, note=body.note,
    )
    await session.commit()
    return await list_maintenance(current=current, session=session)


@router.delete("/maintenance/{kind_key}/done/{done_on}")
async def forget_maintenance(
    kind_key: str,
    done_on: date,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Remove a date you recorded. Your record, your correction."""
    removed = await maintenance_service.forget_done(
        session, current.account_id, kind_key, done_on=done_on,
    )
    await session.commit()
    payload = await list_maintenance(current=current, session=session)
    payload["removed"] = removed
    return payload


@router.get("/maintenance/{kind_key}/history")
async def maintenance_history(
    kind_key: str,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """The dates you have recorded for one kind, most recent first."""
    rows = await maintenance_service.history(session, current.account_id, kind_key)
    return {
        "kind": kind_key,
        "entries": [
            {
                "done_on": row.done_on.isoformat(),
                "source": row.source,
                "note": row.note,
            }
            for row in rows
        ],
    }
