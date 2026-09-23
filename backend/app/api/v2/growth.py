"""V2 growth routes: the referral code, growth telemetry, admin aggregates.

* ``GET  /api/v2/growth/referral`` — what this account could share. Reads only.
* ``POST /api/v2/growth/referral`` — ensure the code this account may share now
  exists. Logically idempotent; concurrent calls converge on one code.
* ``POST /api/v2/growth/events`` — one whitelisted telemetry event. Fire and
  forget: a storage failure answers ``recorded: false``, never an error that
  could be mistaken for the action itself failing.
* ``GET  /api/v2/admin/growth/metrics`` — aggregates only, admins only.

A referral code is redeemed exactly like any other invite, through
``/access/reserve`` and ``/access/register``; no route here admits anybody.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.growth import analytics, metrics, referral
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount, get_current_account
from app.shared.security.rate_limit import FixedWindowLimiter
from app.shared.security.supabase_auth import SupabaseUser, get_current_admin

logger = logging.getLogger(__name__)
router = APIRouter()

#: Per-account ceiling on telemetry writes. The app sends one event per tap;
#: anything near this is a loop or a script, and telemetry is not worth a
#: table's worth of either.
_event_limiter = FixedWindowLimiter(
    window_seconds=60.0,
    max_per_window=30,
    max_keys=20_000,
    sweep_interval_seconds=5.0,
)


@router.get("/growth/referral")
async def read_referral(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    state = await referral.read_referral(session, account_id=current.account_id)
    return state.as_payload()


@router.post("/growth/referral")
async def ensure_referral(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        state = await referral.ensure_referral(session, account_id=current.account_id)
    except referral.ReferralIssueFailed as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "referral_unavailable", "retryable": True},
        ) from exc
    await session.commit()
    return state.as_payload()


class GrowthEventBody(BaseModel):
    """The whole envelope. The property bag is checked against the whitelist."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    client_event_id: uuid.UUID
    properties: dict[str, Any] = Field(max_length=8)


@router.post("/growth/events", status_code=status.HTTP_202_ACCEPTED)
async def record_growth_event(
    body: GrowthEventBody,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    if _event_limiter.hit(f"account:{current.account_id}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "retryable": True},
        )
    try:
        analytics.validate_event(body.name, body.properties)
    except analytics.InvalidGrowthEvent as exc:
        # The code only. The refused input is not echoed anywhere.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "retryable": False},
        ) from exc
    try:
        recorded = await analytics.record_event(
            session,
            account_id=current.account_id,
            name=body.name,
            client_event_id=body.client_event_id,
            properties=body.properties,
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — telemetry is disposable
        logger.warning("growth_event_write_failed type=%s", type(exc).__name__)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 — a session we cannot reset is already lost
            logger.warning("growth_event_rollback_failed")
        return {"recorded": False}
    await analytics.prune_opportunistically(session)
    return {"recorded": recorded}


@router.get("/admin/growth/metrics")
async def admin_growth_metrics(
    window_days: int = Query(
        default=metrics.DEFAULT_WINDOW_DAYS, ge=metrics.MIN_WINDOW_DAYS, le=metrics.MAX_WINDOW_DAYS,
    ),
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    del admin
    return await metrics.growth_metrics(session, window_days=window_days)


__all__ = ["router"]
