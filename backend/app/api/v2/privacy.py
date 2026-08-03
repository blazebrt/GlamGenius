"""Privacy — export + account-deletion state machine.

The API is thin: it delegates to :mod:`app.domains.privacy.export` for the
JSON snapshot, and to :mod:`app.domains.privacy.deletion_service` for the
deletion state machine.

Guarantees the routes give back
-------------------------------
* Export includes every domain the registry marks ``INCLUDED`` and never any
  secret, credential, raw storage path or raw face-image byte.
* Deletion is idempotent, returns ``202 Accepted`` while it runs, and never
  claims cross-system atomicity.
* Reading another account's deletion status is impossible — the endpoint
  scopes by the caller's account id.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.audit import service as audit
from app.domains.audit.models import (
    ACTION_ACCOUNT_DELETION_REQUESTED,
    ACTION_PRIVACY_EXPORTED,
)
from app.domains.privacy import export as export_service
from app.domains.privacy import deletion_service
from app.domains.privacy.models import DESTRUCTIVE_STATES
from app.shared.database.sql import get_session
from app.shared.security.deps import (
    CurrentAccount,
    client_ip,
    get_current_account,
    require_flag,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_flag("v2_privacy"))])


@router.get("/privacy/export")
async def export_data(
    request: Request,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Complete versioned export of everything the caller owns.

    Shape::

        {"schema_version": "1.0", "generated_at": "...", "account": {...},
         "domains": {...}}
    """
    payload = await export_service.build_export(session, current.account_id)
    await audit.record(
        session,
        action=ACTION_PRIVACY_EXPORTED,
        account_id=current.account_id,
        subject_type="account",
        subject_id=str(current.account_id),
        context={
            "schema_version": payload["schema_version"],
            "domains": sorted(payload["domains"].keys()),
        },
        client_ip=client_ip(request),
    )
    await session.commit()
    return payload


@router.delete("/privacy/account", status_code=status.HTTP_202_ACCEPTED)
async def delete_account(
    request: Request,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Enqueue an account-deletion job. Idempotent.

    Returns ``202 Accepted`` with the job status. The worker in
    :mod:`app.workers.account_deletion` performs the destructive stages.
    """
    job = await deletion_service.request_deletion(session, current.account_id)
    await audit.record(
        session,
        action=ACTION_ACCOUNT_DELETION_REQUESTED,
        account_id=current.account_id,
        subject_type="account",
        subject_id=str(current.account_id),
        context={"state": job.state},
        client_ip=client_ip(request),
    )
    await session.commit()
    return deletion_service.status_payload(job)


@router.get("/privacy/account-deletion")
async def get_deletion_status(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    job = await deletion_service.get_job(session, current.account_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "no_deletion_requested",
                "message": "There is no account-deletion job for this account.",
            },
        )
    return deletion_service.status_payload(job)


@router.post("/privacy/account-deletion/cancel")
async def cancel_deletion(
    request: Request,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Cancel a pending deletion. Only possible before destructive work begins."""
    job = await deletion_service.get_job(session, current.account_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "no_deletion_requested",
                "message": "There is nothing to cancel.",
            },
        )
    if job.state in DESTRUCTIVE_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "deletion_in_progress",
                "message": (
                    "Your deletion has already started and cannot be cancelled."
                ),
            },
        )
    # Only the safe, pre-destructive states are cancellable. We drop the row
    # entirely so a subsequent request creates a fresh job.
    from sqlalchemy import delete
    from app.domains.identity.models import ACCOUNT_STATUS_ACTIVE, Account
    from app.domains.privacy.models import AccountDeletionJob

    await session.execute(
        delete(AccountDeletionJob).where(AccountDeletionJob.id == job.id)
    )
    account = await session.get(Account, current.account_id)
    if account is not None:
        account.status = ACCOUNT_STATUS_ACTIVE
        account.deletion_requested_at = None
    await session.commit()
    return {"status": "cancelled"}
