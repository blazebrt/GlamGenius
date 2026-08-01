"""GET /api/v2/privacy/export — everything we hold about the caller."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway.models import AIRun
from app.domains.audit import service as audit
from app.domains.audit.models import ACTION_PRIVACY_EXPORTED, AuditEvent
from app.domains.consent.models import Consent
from app.domains.entitlements import service as entitlements
from app.domains.media import service as media_service
from app.domains.inventory import service as inventory_service
from app.domains.inventory.models import InventoryItem
from app.domains.profile import observations as profile_observations
from app.domains.profile import onboarding as profile_onboarding
from app.domains.profile import service as profile_service
from app.domains.profile.models import AppearanceProfile, OnboardingSession
from app.shared.database.base import utcnow
from app.shared.database.sql import get_session
from app.shared.security.deps import (
    CurrentAccount,
    client_ip,
    get_current_account,
    require_flag,
)
from database import db
from security import _public_user

router = APIRouter(dependencies=[Depends(require_flag("v2_privacy"))])


@router.get("/privacy/export")
async def export_data(
    request: Request,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """A single JSON document covering both databases.

    Synchronous and complete for a private-beta data volume. When accounts get
    large this becomes a background job writing to storage — the shape of the
    document does not need to change for that.

    Scan records include their analysis but **not** the stored image string:
    face photos are truncated to 83 characters at write time and that fragment
    is not meaningful data to hand back.
    """
    account_id = current.account_id
    v1_user_id = current.v1_user_id

    scans = (
        await db.scans.find({"user_id": v1_user_id}).sort("created_at", -1).to_list(500)
    )
    plans = (
        await db.style_plans.find({"user_id": v1_user_id})
        .sort("created_at", -1)
        .to_list(500)
    )

    consents = (
        (
            await session.execute(
                select(Consent)
                .where(Consent.account_id == account_id)
                .order_by(Consent.recorded_at.desc())
            )
        )
        .scalars()
        .all()
    )
    ai_runs = (
        (
            await session.execute(
                select(AIRun)
                .where(AIRun.account_id == account_id)
                .order_by(AIRun.created_at.desc())
                .limit(1000)
            )
        )
        .scalars()
        .all()
    )
    audit_events = (
        (
            await session.execute(
                select(AuditEvent)
                .where(AuditEvent.account_id == account_id)
                .order_by(AuditEvent.created_at.desc())
                .limit(1000)
            )
        )
        .scalars()
        .all()
    )
    media_assets = await media_service.list_for_account(
        session, account_id, include_deleted=True
    )
    ledger = await entitlements.entries_for_account(session, account_id)
    appearance_profile = (
        await session.execute(
            select(AppearanceProfile).where(AppearanceProfile.account_id == account_id)
        )
    ).scalar_one_or_none()
    digital_twin = None
    if appearance_profile is not None:
        onboarding_row = (
            await session.execute(
                select(OnboardingSession)
                .where(OnboardingSession.profile_id == appearance_profile.id)
                .order_by(OnboardingSession.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        digital_twin = await profile_service.serialize_profile(session, appearance_profile)
        digital_twin["observations"] = [
            profile_observations.serialize(row)
            for row in await profile_observations.list_for_profile(
                session, appearance_profile.id
            )
        ]
        digital_twin["change_history"] = await profile_service.change_history(
            session, appearance_profile.id
        )
        digital_twin["onboarding"] = (
            profile_onboarding.serialize(onboarding_row) if onboarding_row else None
        )
    inventory_rows = (
        await session.execute(
            select(InventoryItem)
            .where(InventoryItem.account_id == account_id)
            .order_by(InventoryItem.created_at.desc())
            .limit(5000)
        )
    ).scalars().all()
    inventory = [
        await inventory_service.serialize_item(session, item, include_history=True)
        for item in inventory_rows
    ]

    export = {
        "generated_at": utcnow().isoformat(),
        "account": {
            "v2_account_id": str(account_id),
            "v1_user_id": v1_user_id,
            "status": current.account.status,
        },
        "profile": _public_user(dict(current.user)),
        "appearance_digital_twin": digital_twin,
        "appearance_inventory": inventory,
        "scans": [
            {
                "id": s.get("id"),
                "scan_type": s.get("scan_type"),
                "analysis": s.get("analysis"),
                "created_at": (
                    s["created_at"].isoformat() if s.get("created_at") else None
                ),
            }
            for s in scans
        ],
        "style_plans": [
            {
                "id": p.get("id"),
                "occasion": p.get("occasion"),
                "mood": p.get("mood"),
                "plan": p.get("plan"),
                "created_at": (
                    p["created_at"].isoformat() if p.get("created_at") else None
                ),
            }
            for p in plans
        ],
        "consents": [
            {
                "consent_type": c.consent_type,
                "granted": c.granted,
                "version": c.version,
                "source": c.source,
                "recorded_at": c.recorded_at.isoformat() if c.recorded_at else None,
            }
            for c in consents
        ],
        "media": [media_service.to_public_dict(a) for a in media_assets],
        "ai_runs": [
            {
                "id": str(r.id),
                "feature": r.feature,
                "provider": r.provider,
                "model": r.model,
                "prompt_version": r.prompt_version,
                "schema_version": r.schema_version,
                "status": r.status,
                "failure_type": r.failure_type,
                "latency_ms": r.latency_ms,
                "allowance_consumed": r.allowance_consumed,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in ai_runs
        ],
        "usage_ledger": [
            {
                "feature": e.feature,
                "quantity": e.quantity,
                "period_key": e.period_key,
                "reason": e.reason,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in ledger
        ],
        "audit_events": [
            {
                "action": e.action,
                "subject_type": e.subject_type,
                "subject_id": e.subject_id,
                "context": e.context,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in audit_events
        ],
    }

    # Exporting your own data is itself worth recording.
    await audit.record(
        session,
        action=ACTION_PRIVACY_EXPORTED,
        account_id=account_id,
        subject_type="account",
        subject_id=str(account_id),
        context={"scans": len(scans), "media": len(media_assets)},
        client_ip=client_ip(request),
    )
    await session.commit()
    return export
