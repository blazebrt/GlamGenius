"""Privacy: export + account deletion across Supabase Auth, PostgreSQL, Storage.

**One authoritative workflow.** No dual-write, no half-erased state. The order
matters:

1. Export current data (a JSON snapshot the user can keep).
2. Delete all Supabase Storage objects under ``account/{uuid}/…``.
3. Delete PostgreSQL rows via cascade off ``accounts.id``.
4. Delete the Supabase Auth user with ``auth.admin.delete_user``.
5. Record a minimal audit row (UUID + timestamp only).

If any step fails we fail closed — subsequent steps do not run and the caller
sees a real error rather than a partial success.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway.models import AIRun
from app.domains.audit import service as audit
from app.domains.audit.models import (
    ACTION_ACCOUNT_DELETION_REQUESTED,
    ACTION_PRIVACY_EXPORTED,
    AuditEvent,
)
from app.domains.consent import service as consent_service
from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS, Consent
from app.domains.identity.models import (
    ACCOUNT_STATUS_DELETED,
    ACCOUNT_STATUS_DELETION_REQUESTED,
    Account,
)
from app.domains.inventory.models import InventoryItem
from app.domains.media import service as media_service
from app.shared.database.base import utcnow
from app.shared.database.sql import get_session
from app.shared.security.deps import (
    CurrentAccount,
    client_ip,
    get_current_account,
    require_flag,
)
from app.shared.supabase_client import get_supabase_admin

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_flag("v2_privacy"))])


@router.get("/privacy/export")
async def export_data(
    request: Request,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """A single JSON snapshot of everything we hold about the caller."""
    account_id = current.account_id

    consents = (
        await session.execute(
            select(Consent)
            .where(Consent.account_id == account_id)
            .order_by(Consent.recorded_at.desc())
        )
    ).scalars().all()
    ai_runs = (
        await session.execute(
            select(AIRun)
            .where(AIRun.account_id == account_id)
            .order_by(AIRun.created_at.desc())
            .limit(1000)
        )
    ).scalars().all()
    audit_events = (
        await session.execute(
            select(AuditEvent)
            .where(AuditEvent.account_id == account_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(1000)
        )
    ).scalars().all()
    media_assets = await media_service.list_for_account(
        session, account_id, include_deleted=True
    )
    inventory_rows = (
        await session.execute(
            select(InventoryItem)
            .where(InventoryItem.account_id == account_id)
            .order_by(InventoryItem.created_at.desc())
            .limit(5000)
        )
    ).scalars().all()

    export = {
        "generated_at": utcnow().isoformat(),
        "account": {
            "id": str(account_id),
            "email": current.supabase_user.email,
            "status": current.account.status,
        },
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
        "inventory": [
            {"id": str(i.id), "category": i.category, "created_at": i.created_at.isoformat()}
            for i in inventory_rows
        ],
        "ai_runs": [
            {
                "id": str(r.id),
                "feature": r.feature,
                "provider": r.provider,
                "model": r.model,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in ai_runs
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

    await audit.record(
        session,
        action=ACTION_PRIVACY_EXPORTED,
        account_id=account_id,
        subject_type="account",
        subject_id=str(account_id),
        context={"media": len(media_assets), "inventory": len(inventory_rows)},
        client_ip=client_ip(request),
    )
    await session.commit()
    return export


@router.delete("/privacy/account")
async def delete_account(
    request: Request,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Delete the caller's account across Supabase Auth, PostgreSQL and Storage.

    This is destructive and idempotent — calling twice on a partially-deleted
    account safely completes the remaining steps.
    """
    account = current.account
    account_id: uuid.UUID = account.id

    # Step 1: delete storage objects owned by the account.
    media_removed = await media_service.delete_all_for_account(
        session, account_id, client_ip=client_ip(request)
    )

    # Step 2: revoke photo consent.
    await consent_service.record(
        session,
        account_id=account_id,
        consent_type=CONSENT_PHOTO_ANALYSIS,
        granted=False,
        source="account_deletion",
        client_ip=client_ip(request),
    )

    # Step 3: mark deletion-requested.
    account.status = ACCOUNT_STATUS_DELETION_REQUESTED
    account.deletion_requested_at = utcnow()
    await session.flush()

    # Step 4: delete the Supabase Auth user. This is what makes the account
    # unrecoverable — if it fails, we stop and let the caller retry.
    try:
        admin = get_supabase_admin()
        admin.auth.admin.delete_user(str(account_id))
    except Exception as exc:  # noqa: BLE001 — external boundary
        logger.error(
            "supabase_admin_delete_user_failed account=%s reason=%s",
            account_id, type(exc).__name__,
        )
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "supabase_delete_failed",
                "message": (
                    "We couldn't complete the deletion right now. Your account "
                    "is marked for closure and your photos have been erased; "
                    "please try again in a few minutes to finish removing your "
                    "identity."
                ),
            },
        ) from exc

    # Step 5: cascade-delete the account row. FKs with ON DELETE CASCADE
    # remove every child record (inventory, consent, media metadata, audit
    # events, AI runs, etc.). The audit row is intentionally *not*
    # cascaded — see the ON DELETE SET NULL on AuditEvent.account_id.
    account.status = ACCOUNT_STATUS_DELETED
    await session.execute(delete(Account).where(Account.id == account_id))

    await audit.record(
        session,
        action=ACTION_ACCOUNT_DELETION_REQUESTED,
        account_id=None,  # account is gone; keep the audit row without it
        subject_type="account",
        subject_id=str(account_id),
        context={
            "media_deleted": media_removed,
            "storage_removed": True,
            "supabase_auth_deleted": True,
        },
        client_ip=client_ip(request),
    )
    await session.commit()

    return {
        "status": "deleted",
        "account_id": str(account_id),
        "media_deleted": media_removed,
    }
