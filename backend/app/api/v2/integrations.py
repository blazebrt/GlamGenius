"""Calendar integration.

Connecting is optional and disconnecting actually disconnects: revoking stops
integration-sourced events feeding plans, and clears the credential reference.
Events the user typed in themselves are theirs, and are left alone.

No access token is accepted, stored or returned by any route here. See
``ExternalIntegration.credential_ref``.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import GOOGLE_CALENDAR_APP_RETURN_URI
from app.domains.planning import calendar_sync, service
from app.domains.planning import context as context_stage
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


@router.post("/integrations/calendar/google/authorize")
async def google_authorize(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    url, expires_at = await calendar_sync.async_authorization_url(session, current.account_id)
    await session.commit()
    return {"authorization_url": url, "expires_at": expires_at.isoformat()}


@router.get("/integrations/calendar/google/callback", include_in_schema=False)
async def google_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """OAuth callback: only a fixed safe result is returned to the app."""
    result = "error"
    try:
        if not state:
            raise ValueError("oauth_state_missing")
        state_row = await calendar_sync.consume_state(session, state)
        account_id = state_row.account_id
        # Commit the one-time nonce before provider work so failed exchanges
        # cannot roll it back and make the callback replayable.
        await session.commit()
        if error or not code:
            result = "denied"
            return RedirectResponse(f"{GOOGLE_CALENDAR_APP_RETURN_URI}?result={result}")
        await calendar_sync.connect_from_callback(session, account_id, code)
        # Retain the usable refresh credential before the first provider sync;
        # a transient/ malformed initial response must be safely retryable.
        await session.commit()
        timezone_name = await context_stage.resolve_timezone_for(session, account_id)
        try:
            sync_result = await calendar_sync.sync_google_calendar(session, account_id, timezone_name)
        except Exception:  # noqa: BLE001 — provider details never reach the app
            await session.rollback()
            sync_result = {"synced": False}
        await session.commit()
        result = "connected" if sync_result.get("synced") else "sync_failed"
    except Exception:  # noqa: BLE001 — never reflect provider details to redirect/logs
        await session.rollback()
    separator = "&" if "?" in GOOGLE_CALENDAR_APP_RETURN_URI else "?"
    return RedirectResponse(f"{GOOGLE_CALENDAR_APP_RETURN_URI}{separator}result={result}")


@router.post("/integrations/calendar/google/sync")
async def google_sync(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)
    body = await calendar_sync.sync_google_calendar(session, current.account_id, timezone_name)
    await session.commit()
    return body


@router.delete("/integrations/calendar/google")
async def google_disconnect(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    body = await calendar_sync.disconnect_google_calendar(session, current.account_id)
    await session.commit()
    return body


@router.get("/integrations/providers")
async def list_providers(
    current: CurrentAccount = Depends(get_current_account),
):
    """Which sources exist, and which are actually usable right now."""
    return {
        **catalogue(),
        "note": (
            "Google Calendar is optional and read-only. Manual events remain fully supported."
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
        if key in {"title", "occasion_key", "dress_code_hint", "status"}:
            overrides = dict(row.user_overrides or {})
            overrides[key] = True
            row.user_overrides = overrides
    await session.commit()
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)
    return service.serialize_event(row, timezone_name)
