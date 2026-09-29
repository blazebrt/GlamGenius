"""Privacy — export + account-deletion state machine.

The API is thin: it delegates to :mod:`app.domains.privacy.export` for the
JSON snapshot, and to :mod:`app.domains.privacy.deletion_service` for the
deletion state machine.

Guarantees the routes give back
-------------------------------
* Export includes every table the registry marks ``INCLUDED`` — each through
  its entry in ``app.domains.privacy.coverage.EXPORT_COVERAGE`` — or it is
  not returned at all; and it never carries any secret, credential, raw
  storage path or raw face-image byte.
* Deletion is idempotent, returns ``202 Accepted`` while it runs, and never
  claims cross-system atomicity.
* Reading another account's deletion status is impossible — the endpoint
  scopes by the caller's account id.

Who may call what
-----------------
A deletion-requested account is no longer an active account, and every
ordinary route refuses it through ``get_current_account``. The three deletion
lifecycle routes below — request (idempotent), status and cancel — take
``get_registered_account`` instead: the owner of a pending deletion must still
be able to see it and, while no worker has touched it, cancel it. Export stays
on the active-account dependency.
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
from app.domains.privacy import deletion_service
from app.domains.privacy import export as export_service
from app.shared.database.sql import get_session
from app.shared.security.deps import (
    CurrentAccount,
    client_ip,
    get_current_account,
    get_registered_account,
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

        {"schema_version": "1.6", "generated_at": "...", "account": {...},
         "domains": {...}, "registry_summary": {...}}

    ``200`` means complete: every table the registry classifies ``INCLUDED``
    is in the file, with every row this account owns. When that cannot be
    shown — a domain failed, or a covered table was not delivered — the
    exporter raises ``PrivacyExportIncomplete`` and this answers ``503
    PRIVACY_EXPORT_INCOMPLETE`` with a fixed message, before anything is
    recorded. The successful-export audit event is written only after a
    complete payload exists, so a failed attempt leaves no trace that says
    the person received their data. Retrying is safe: building the export
    reads and never writes.
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
    # Registered, not active: a repeat request after the first has already
    # made the account deletion-requested must still return the same job.
    current: CurrentAccount = Depends(get_registered_account),
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
    current: CurrentAccount = Depends(get_registered_account),
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
    current: CurrentAccount = Depends(get_registered_account),
    session: AsyncSession = Depends(get_session),
):
    """Cancel a pending deletion, only if no worker has ever started it.

    The decision is the deletion service's, made under the job's row lock
    (:func:`deletion_service.cancel_deletion`). This route only maps its
    answer; it never inspects the job itself. On success the job row is
    removed, so a later request creates a fresh job.
    """
    try:
        await deletion_service.cancel_deletion(session, current.account_id)
    except deletion_service.NoDeletionRequested:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "no_deletion_requested",
                "message": "There is nothing to cancel.",
            },
        ) from None
    except deletion_service.DeletionNotCancellable:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "deletion_in_progress",
                "message": (
                    "Your deletion has already started and cannot be cancelled."
                ),
            },
        ) from None
    await session.commit()
    return {"status": "cancelled"}
