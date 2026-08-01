"""Admin invite-code management. Protected by ADMIN_SECRET, no login UI."""
from fastapi import APIRouter, HTTPException, Header
from typing import Optional, Dict, Any
from datetime import datetime
import uuid

from database import db
from models import InviteCreateRequest
from invites import generate_invite_code, _normalize_code
from settings import ADMIN_SECRET

router = APIRouter()


def _require_admin(x_admin_secret: Optional[str]) -> None:
    if not ADMIN_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Admin invites are not configured (ADMIN_SECRET is unset).",
        )
    if not x_admin_secret or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Invalid admin secret")


@router.post("/admin/invites")
async def create_invite(
    body: InviteCreateRequest,
    x_admin_secret: Optional[str] = Header(default=None),
):
    """Create an invite code. Send header X-Admin-Secret."""
    _require_admin(x_admin_secret)

    code = _normalize_code(body.code) if body.code else generate_invite_code()
    if not code:
        raise HTTPException(status_code=400, detail="Invite code cannot be empty")

    existing = await db.invite_codes.find_one({"code": code})
    if existing:
        raise HTTPException(status_code=400, detail="That invite code already exists")

    max_uses = int(body.max_uses) if body.max_uses is not None else 1
    if max_uses < 1:
        raise HTTPException(status_code=400, detail="max_uses must be at least 1")

    doc: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "code": code,
        "label": (body.label or "").strip(),
        "max_uses": max_uses,
        "uses": 0,
        "active": bool(body.active),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    await db.invite_codes.insert_one(doc)
    return {
        "id": doc["id"],
        "code": doc["code"],
        "label": doc["label"],
        "max_uses": doc["max_uses"],
        "uses": doc["uses"],
        "active": doc["active"],
        "created_at": doc["created_at"],
    }


@router.get("/admin/invites")
async def list_invites(x_admin_secret: Optional[str] = Header(default=None)):
    """List invite codes and how many times each has been used."""
    _require_admin(x_admin_secret)
    rows = await db.invite_codes.find().sort("created_at", -1).to_list(500)
    return [
        {
            "id": r.get("id"),
            "code": r.get("code"),
            "label": r.get("label") or "",
            "max_uses": int(r.get("max_uses") or 0),
            "uses": int(r.get("uses") or 0),
            "active": bool(r.get("active")),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
        }
        for r in rows
    ]
