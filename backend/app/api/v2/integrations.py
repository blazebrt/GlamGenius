"""Calendar integration.

Connecting is optional and disconnecting actually disconnects: revoking stops
integration-sourced events feeding plans, and clears the credential reference.
Events the user typed in themselves are theirs, and are left alone.

No access token is accepted, stored or returned by any route here. See
``ExternalIntegration.credential_ref``.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.planning import context as context_stage
from app.domains.planning import service
from app.domains.planning.providers import catalogue
from app.domains.planning.schemas import CalendarConnect, CalendarEventPatch
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_today"))])


@router.get("/integrations/calendar/status")
async def calendar_status(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    body = await service.calendar_status(session, current.account_id)
    await session.commit()
    return body


@router.post("/integrations/calendar/connect")
async def connect_calendar(
    body: CalendarConnect,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Connect a calendar source and optionally seed it with events.

    ``manual`` is the provider that works today: you send the events you care
    about and nothing outside the app is contacted.
    """
    integration = await service.connect_calendar(
        session, current.account_id, body.provider, body.credential_ref, body.label
    )
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)

    created = 0
    duplicates = 0
    for event in body.events:
        _, was_created = await service.upsert_event(
            session, current.account_id, event,
            provider=body.provider, integration_id=integration.id,
        )
        created += 1 if was_created else 0
        duplicates += 0 if was_created else 1

    await session.commit()
    status = await service.calendar_status(session, current.account_id)
    status.update({
        "events_added": created,
        "duplicates_ignored": duplicates,
        "timezone": timezone_name,
    })
    return status


@router.delete("/integrations/calendar")
async def disconnect_calendar(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Disconnect every calendar integration for this account."""
    revoked = await service.disconnect_calendar(session, current.account_id)
    await session.commit()
    body = await service.calendar_status(session, current.account_id)
    body.update({
        "revoked": len(revoked),
        "message": (
            "Calendar access is disconnected. Events that came from it are no longer used "
            "in your plans. Anything you added yourself is untouched."
        ),
    })
    return body


@router.get("/integrations/providers")
async def list_providers(
    current: CurrentAccount = Depends(get_current_account),
):
    """Which sources exist, and which are actually usable right now."""
    return {
        **catalogue(),
        "note": (
            "Only the manual source is connected in this release. The planner is fully "
            "usable with it — you add the events that matter and nothing leaves the app."
        ),
    }


@router.patch("/integrations/calendar/events/{event_id}")
async def patch_event(
    event_id: uuid.UUID,
    body: CalendarEventPatch,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Correct what we guessed about an event.

    A user correction is authoritative: it sets confidence to certain, so the
    planner stops asking about it.
    """
    row = await service.owned_event(session, current.account_id, event_id)
    fields = body.model_dump(exclude_unset=True)
    if fields.get("occasion_key"):
        row.user_confirmed = True
        row.inference_confidence = 1.0
    for key, value in fields.items():
        if value is not None:
            setattr(row, key, value)
    await session.commit()
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)
    return service.serialize_event(row, timezone_name)
