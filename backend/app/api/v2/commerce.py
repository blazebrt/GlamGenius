"""V2 Commerce routes (Step 16): a disclosed outbound handoff, after the decision.

* ``GET  /api/v2/scan/verdict/{barcode}/commerce-handoff`` — whether this pack's
  finished decision supports one outbound partner search link, and for which
  product. The same device authority as the Product Result and the Purchase
  OS: ``X-Device-Token``, no weaker path. Read-only.
* ``POST /api/v2/commerce/events`` — one whitelisted ``commerce.outbound_open``
  event. Signed-in accounts only; fire and forget.
* ``GET  /api/v2/admin/commerce/metrics`` — aggregates only, admins only.

Nothing here decides anything about a product. The handoff route asks the
Product Result and the Purchase OS for their answers, exactly as their own
routes build them, and hands the finished answer to
:mod:`app.domains.commerce.handoff`. It passes no account and no subject, so
Decision Memory, the shelf and any override are never even read.

Nothing here logs an outbound address.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v2.product import BARCODE_PATH, current_device, read_product_verdict
from app.domains.commerce import analytics, handoff, metrics, partners
from app.domains.growth.analytics import prune_opportunistically
from app.domains.product.models import ScanDevice
from app.domains.purchase import operating_system
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount, get_current_account
from app.shared.security.rate_limit import FixedWindowLimiter
from app.shared.security.supabase_auth import SupabaseUser, get_current_admin

logger = logging.getLogger(__name__)
router = APIRouter()

#: Per-account ceiling on Commerce telemetry writes. One open is one event;
#: anything near this is a loop or a script.
_event_limiter = FixedWindowLimiter(
    window_seconds=60.0,
    max_per_window=30,
    max_keys=20_000,
    sweep_interval_seconds=5.0,
)


@router.get("/scan/verdict/{barcode}/commerce-handoff")
async def read_commerce_handoff(
    barcode: str = BARCODE_PATH,
    physical_pack_context: bool = True,
    device: ScanDevice = Depends(current_device),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """One ``commerce-handoff-v1`` answer for the pack this device holds.

    With no partner enabled the answer is ``unavailable`` and nothing is read.
    Otherwise the Product Result and the Purchase OS are built as their own
    routes build them — same pack ceiling, same official-record envelope — and
    the finished answer decides whether a link exists. ``physical_pack_context``
    is a ceiling, as everywhere else: ``false`` can only withhold.
    """
    active = partners.active_partner()
    if active is None:
        return handoff.partner_not_configured()
    product_result = await read_product_verdict(
        barcode=barcode, physical_pack_context=physical_pack_context, device=device, session=session,
    )
    purchase_check = await operating_system.scan_purchase_check(
        session,
        barcode=barcode,
        product_result=product_result,
        device=device,
        requested_physical_pack_context=physical_pack_context,
        principal_account_id=None,
        decision_subject=None,
    )
    return handoff.build_handoff(purchase_check, active)


class CommerceEventBody(BaseModel):
    """The whole envelope. The property bag is checked against the whitelist."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    client_event_id: uuid.UUID
    properties: dict[str, Any] = Field(max_length=8)


@router.post("/commerce/events", status_code=status.HTTP_202_ACCEPTED)
async def record_commerce_event(
    body: CommerceEventBody,
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
    except analytics.InvalidCommerceEvent as exc:
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
        logger.warning("commerce_event_write_failed type=%s", type(exc).__name__)
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 — a session we cannot reset is already lost
            logger.warning("commerce_event_rollback_failed")
        return {"recorded": False}
    # The shared ``app_events`` retention: the same bounded, throttled pruner
    # Step 15 runs after its own writes.
    await prune_opportunistically(session)
    return {"recorded": recorded}


@router.get("/admin/commerce/metrics")
async def admin_commerce_metrics(
    window_days: int = Query(
        default=metrics.DEFAULT_WINDOW_DAYS, ge=metrics.MIN_WINDOW_DAYS, le=metrics.MAX_WINDOW_DAYS,
    ),
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    del admin
    return await metrics.commerce_metrics(session, window_days=window_days)


__all__ = ["router"]
