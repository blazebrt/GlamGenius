"""V2 access routes: registration finalisation, invite admin, usage summary.

**Note on registration.** The Supabase Auth sign-up itself happens client-side
using the Supabase JS SDK (or the Supabase REST endpoints directly). What this
endpoint does is *finalise* a fresh Supabase user against an invite: it takes
a valid Supabase access token *and* the invite code, atomically redeems the
invite, creates the ``accounts`` row and returns the account snapshot. The
backend never sees a password.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import INVITE_REQUIRED
from app.domains.beta_access import service as beta
from app.domains.beta_access.models import Invite, InviteRedemption
from app.domains.identity import service as identity
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount, get_current_account
from app.shared.security.supabase_auth import (
    SupabaseUser,
    get_current_admin,
    get_current_supabase_user,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    invite_code: Optional[str] = Field(default=None, description="Invite code. Required when INVITE_REQUIRED is true.")


@router.post("/access/register")
async def register(
    body: RegisterRequest,
    supabase_user: SupabaseUser = Depends(get_current_supabase_user),
    session: AsyncSession = Depends(get_session),
):
    """Finalise an invite-gated account for a newly-registered Supabase user.

    Idempotent: called twice for the same user, the second call is a no-op
    that returns the same account snapshot.
    """
    account_id = supabase_user.id

    # If the account already exists, we are done. No re-check of the invite
    # against the same account.
    existing = await identity.get_account(session, account_id)
    if existing is not None:
        return {
            "account": {
                "id": str(existing.id),
                "status": existing.status,
                "created_at": existing.created_at.isoformat() if existing.created_at else None,
            },
            "invite_redeemed": False,
        }

    if INVITE_REQUIRED:
        code = (body.invite_code or "").strip()
        if not code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invite_required",
                    "message": "An invite code is required to join the private beta.",
                },
            )
        # Create the account first so the InviteRedemption FK is satisfied.
        account = await identity.get_or_create_account(session, account_id)
        try:
            invite = await beta.redeem_invite(session, code=code, account_id=account_id)
        except ValueError as exc:
            # Roll back the auto-created account so a rejected code does not
            # leave a half-registered account behind.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invite_rejected", "message": str(exc)},
            ) from exc
    else:
        invite = None
        account = await identity.get_or_create_account(session, account_id)
    await session.commit()

    return {
        "account": {
            "id": str(account.id),
            "status": account.status,
            "created_at": account.created_at.isoformat() if account.created_at else None,
        },
        "invite_redeemed": invite is not None,
        "invite_code": invite.code if invite else None,
    }


# ---------------------------------------------------------------------------
# Beta usage summary
# ---------------------------------------------------------------------------

@router.get("/access/usage")
async def get_usage(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Neutral beta usage summary. No plans, no upgrade CTA, no pricing."""
    return await beta.usage_summary(session, account_id=current.account_id)


# ---------------------------------------------------------------------------
# Admin invite management
# ---------------------------------------------------------------------------

class InviteCreateRequest(BaseModel):
    label: str = ""
    max_uses: int = 1
    expires_at: Optional[datetime] = None
    code: Optional[str] = None


@router.post("/access/admin/invites")
async def admin_create_invite(
    body: InviteCreateRequest,
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    invite = await beta.create_invite(
        session,
        label=body.label,
        max_uses=body.max_uses,
        expires_at=body.expires_at,
        code=body.code,
        created_by=admin.id,
    )
    await session.commit()
    return beta.serialise_invite(invite)


@router.get("/access/admin/invites")
async def admin_list_invites(
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    invites = await beta.list_invites(session)
    return {"invites": [beta.serialise_invite(inv) for inv in invites]}


@router.post("/access/admin/invites/{invite_id}/deactivate")
async def admin_deactivate_invite(
    invite_id: uuid.UUID,
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    invite = await beta.deactivate_invite(session, invite_id)
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await session.commit()
    return beta.serialise_invite(invite)


@router.get("/access/admin/invites/{invite_id}")
async def admin_get_invite(
    invite_id: uuid.UUID,
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    invite = await session.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    redemptions = (
        await session.execute(
            select(InviteRedemption)
            .where(InviteRedemption.invite_id == invite_id)
            .order_by(InviteRedemption.created_at.desc())
        )
    ).scalars().all()
    payload = beta.serialise_invite(invite)
    payload["redemptions"] = [
        {
            "account_id": str(r.account_id),
            "redeemed_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in redemptions
    ]
    return payload
