"""Invite code validation and consumption."""
from fastapi import HTTPException
from typing import Dict, Any, Optional
from datetime import datetime
from pymongo import ReturnDocument
import secrets
import re

from database import db


def _normalize_code(code: Optional[str]) -> str:
    return re.sub(r"\s+", "", (code or "")).upper()


def generate_invite_code() -> str:
    """Short human-typable code, e.g. GG-A1B2C3."""
    return "GG-" + secrets.token_hex(3).upper()


async def assert_invite_usable(raw_code: Optional[str]) -> Dict[str, Any]:
    """Return the invite doc if it is active and still has uses left.

    Does not consume a use — call consume_invite after a successful signup.
    """
    code = _normalize_code(raw_code)
    if not code:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVITE_REQUIRED",
                "message": (
                    "GlamGenius is invite-only right now. "
                    "Enter the invite code you were given to continue."
                ),
            },
        )

    invite = await db.invite_codes.find_one({"code": code})
    if not invite:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVITE_INVALID",
                "message": (
                    "That invite code is not recognised. "
                    "Double-check it, or ask whoever invited you for a fresh one."
                ),
            },
        )

    if not invite.get("active", False):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVITE_INACTIVE",
                "message": "That invite code has been turned off. Please ask for a new one.",
            },
        )

    uses = int(invite.get("uses") or 0)
    max_uses = int(invite.get("max_uses") or 0)
    if max_uses > 0 and uses >= max_uses:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVITE_EXHAUSTED",
                "message": (
                    "That invite code has already been used up. "
                    "Please ask for a new one."
                ),
            },
        )

    return invite


async def consume_invite(raw_code: Optional[str]) -> Dict[str, Any]:
    """Atomically spend one use of an invite code. Raises if it cannot."""
    invite = await assert_invite_usable(raw_code)
    code = invite["code"]
    max_uses = int(invite.get("max_uses") or 0)

    query: Dict[str, Any] = {"code": code, "active": True}
    if max_uses > 0:
        query["$expr"] = {"$lt": ["$uses", "$max_uses"]}

    updated = await db.invite_codes.find_one_and_update(
        query,
        {
            "$inc": {"uses": 1},
            "$set": {"updated_at": datetime.utcnow()},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVITE_EXHAUSTED",
                "message": (
                    "That invite code has already been used up. "
                    "Please ask for a new one."
                ),
            },
        )
    return updated
