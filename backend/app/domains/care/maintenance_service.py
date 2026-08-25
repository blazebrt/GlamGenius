"""Account-scoped persistence for maintenance timing (VC-06).

The engine in :mod:`app.domains.care.maintenance` stays pure; this module is
the only place that reads or writes maintenance rows. Every query is scoped by
``account_id``.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care.maintenance import (
    MaintenanceSet,
    MaintenanceState,
    decide_maintenance,
    maintenance_fingerprint,
)
from app.domains.care.maintenance_rules import (
    MAX_INTERVAL_DAYS,
    MIN_INTERVAL_DAYS,
    get_kind,
)
from app.domains.routines.models import MaintenanceEvent, MaintenancePreference
from app.shared.database.base import new_uuid
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError


async def _preferences(session: AsyncSession, account_id: uuid.UUID) -> dict[str, MaintenancePreference]:
    rows = (await session.execute(
        select(MaintenancePreference).where(MaintenancePreference.account_id == account_id)
    )).scalars().all()
    return {row.kind_key: row for row in rows}


async def _latest_done(session: AsyncSession, account_id: uuid.UUID, *, through: date) -> dict[str, date]:
    """The most recent recorded date per kind, ignoring anything in the future.

    A date after the planning day cannot anchor that day's schedule, so it is
    excluded rather than allowed to push the next date past the horizon.
    """
    rows = (await session.execute(
        select(MaintenanceEvent.kind_key, func.max(MaintenanceEvent.done_on))
        .where(
            MaintenanceEvent.account_id == account_id,
            MaintenanceEvent.done_on <= through,
        )
        .group_by(MaintenanceEvent.kind_key)
    )).all()
    return {kind_key: done_on for kind_key, done_on in rows}


async def build_states(
    session: AsyncSession, account_id: uuid.UUID, *, plan_date: date,
) -> dict[str, MaintenanceState]:
    preferences = await _preferences(session, account_id)
    last_done = await _latest_done(session, account_id, through=plan_date)
    states: dict[str, MaintenanceState] = {}
    for kind_key, row in preferences.items():
        if get_kind(kind_key) is None:
            # A row for a kind the catalogue no longer offers is inert rather
            # than an error: the customer's other choices still work.
            continue
        states[kind_key] = MaintenanceState(
            kind_key=kind_key,
            tracked=row.tracked,
            interval_days=row.interval_days,
            last_done_on=last_done.get(kind_key),
            reminders_enabled=row.reminders_enabled,
        )
    return states


async def build_maintenance(
    session: AsyncSession, account_id: uuid.UUID, *, plan_date: date,
) -> MaintenanceSet:
    """The one deterministic maintenance material for an account and day."""
    states = await build_states(session, account_id, plan_date=plan_date)
    return decide_maintenance(states, plan_date=plan_date)


async def fingerprint_for(
    session: AsyncSession, account_id: uuid.UUID, *, plan_date: date,
) -> tuple[MaintenanceSet, str]:
    decided = await build_maintenance(session, account_id, plan_date=plan_date)
    return decided, maintenance_fingerprint(decided)


def _require_kind(kind_key: str):
    kind = get_kind(kind_key)
    if kind is None:
        raise NotFoundError("That maintenance kind is not one we track.")
    return kind


async def set_preference(
    session: AsyncSession, account_id: uuid.UUID, kind_key: str, *,
    tracked: bool | None = None,
    interval_days: int | None = None,
    clear_interval: bool = False,
    reminders_enabled: bool | None = None,
) -> MaintenancePreference:
    """Record an explicit customer choice about one kind.

    Only the fields actually supplied change. Nothing here is inferred from
    behaviour: tracking, rhythm and reminders are each a deliberate action.
    """
    _require_kind(kind_key)
    if interval_days is not None and (interval_days < MIN_INTERVAL_DAYS or interval_days > MAX_INTERVAL_DAYS):
        raise ValidationFailedError(
            f"Choose a rhythm between {MIN_INTERVAL_DAYS} and {MAX_INTERVAL_DAYS} days.",
            field="interval_days",
        )
    row = (await session.execute(
        select(MaintenancePreference).where(
            MaintenancePreference.account_id == account_id,
            MaintenancePreference.kind_key == kind_key,
        ).with_for_update()
    )).scalar_one_or_none()
    if row is None:
        row = MaintenancePreference(account_id=account_id, kind_key=kind_key)
        session.add(row)
    if tracked is not None:
        row.tracked = tracked
    if clear_interval:
        row.interval_days = None
    elif interval_days is not None:
        row.interval_days = interval_days
    if reminders_enabled is not None:
        row.reminders_enabled = reminders_enabled
    if not row.tracked:
        # Reminders for something they are not tracking would be noise.
        row.reminders_enabled = False
    await session.flush()
    return row


async def record_done(
    session: AsyncSession, account_id: uuid.UUID, kind_key: str, *,
    done_on: date, today: date, note: str | None = None,
) -> MaintenanceEvent:
    """Record a date the customer says this upkeep happened."""
    _require_kind(kind_key)
    if done_on > today:
        raise ValidationFailedError("Choose a date that has already happened.", field="done_on")

    # Recording the same day twice is the same fact, not a second one. A SELECT
    # followed by an INSERT would let two concurrent retries both see nothing
    # and race into the unique constraint, so the database resolves it instead.
    # An omitted note leaves any existing note alone, which keeps a bare retry
    # of the same payload deterministic.
    insert_statement = pg_insert(MaintenanceEvent).values(
        id=new_uuid(), account_id=account_id, kind_key=kind_key, done_on=done_on,
        source="user_declared", note=note,
    )
    statement = insert_statement.on_conflict_do_update(
        constraint="uq_maintenance_event_account_kind_date",
        set_={"note": func.coalesce(insert_statement.excluded.note, MaintenanceEvent.note)},
    ).returning(MaintenanceEvent.id)
    event_id = (await session.execute(statement)).scalar_one()
    # Recording a date is itself a statement that this kind matters to them.
    # It is not a statement about how often they do it, so no rhythm is set.
    await set_preference(session, account_id, kind_key, tracked=True)
    await session.flush()
    return (await session.execute(
        select(MaintenanceEvent).where(MaintenanceEvent.id == event_id)
    )).scalar_one()


async def forget_done(
    session: AsyncSession, account_id: uuid.UUID, kind_key: str, *, done_on: date,
) -> bool:
    """Remove a recorded date. Their record, their correction."""
    _require_kind(kind_key)
    result = await session.execute(
        delete(MaintenanceEvent).where(
            MaintenanceEvent.account_id == account_id,
            MaintenanceEvent.kind_key == kind_key,
            MaintenanceEvent.done_on == done_on,
        )
    )
    await session.flush()
    return bool(result.rowcount)


async def replace_done(
    session: AsyncSession, account_id: uuid.UUID, kind_key: str, *,
    old_done_on: date, new_done_on: date, today: date,
) -> MaintenanceEvent:
    """Atomically correct one recorded date without a delete/create gap.

    The source fact must belong to this account and kind.  A correction onto
    an already-recorded date merges the duplicate deterministically: the
    existing target row remains authoritative for that date, and it keeps its
    note when present (otherwise the source note is carried forward).  This
    preserves the unique account/kind/date invariant inside one transaction.
    """
    _require_kind(kind_key)
    if new_done_on > today:
        raise ValidationFailedError("Choose a date that has already happened.", field="done_on")

    source = (await session.execute(
        select(MaintenanceEvent).where(
            MaintenanceEvent.account_id == account_id,
            MaintenanceEvent.kind_key == kind_key,
            MaintenanceEvent.done_on == old_done_on,
        ).with_for_update()
    )).scalar_one_or_none()
    if source is None:
        raise NotFoundError("That maintenance date was not found.")
    if old_done_on == new_done_on:
        return source

    target = (await session.execute(
        select(MaintenanceEvent).where(
            MaintenanceEvent.account_id == account_id,
            MaintenanceEvent.kind_key == kind_key,
            MaintenanceEvent.done_on == new_done_on,
        ).with_for_update()
    )).scalar_one_or_none()
    if target is None:
        source.done_on = new_done_on
        await session.flush()
        return source

    if target.note is None:
        target.note = source.note
    await session.delete(source)
    await session.flush()
    return target


async def history(
    session: AsyncSession, account_id: uuid.UUID, kind_key: str, *, limit: int = 12,
) -> list[MaintenanceEvent]:
    _require_kind(kind_key)
    return list((await session.execute(
        select(MaintenanceEvent)
        .where(
            MaintenanceEvent.account_id == account_id,
            MaintenanceEvent.kind_key == kind_key,
        )
        .order_by(MaintenanceEvent.done_on.desc())
        .limit(limit)
    )).scalars().all())


__all__ = [
    "build_maintenance",
    "build_states",
    "fingerprint_for",
    "forget_done",
    "history",
    "replace_done",
    "record_done",
    "set_preference",
]
