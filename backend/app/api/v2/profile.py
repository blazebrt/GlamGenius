"""Structured Care Profile routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.profile import service
from app.domains.profile.registry import ATTRIBUTE_REGISTRY
from app.domains.profile.schemas import ProfilePatch
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import ValidationFailedError
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_profile"))])

ALLOWED_KEYS = {"care_skin_usual_feel", "care_skin_sensitivity"}

async def _profile(session: AsyncSession, current: CurrentAccount):
    return await service.get_or_create_profile(session, current.account_id)

def _filter_profile(body: dict) -> dict:
    """Filter legacy appearance attributes out of the active customer payload."""
    if "attributes" in body:
        body["attributes"] = [attr for attr in body["attributes"] if attr.get("key") in ALLOWED_KEYS]
    
    body.pop("baseline_status", None)
    body.pop("readiness", None)
    
    if "change_history" in body:
        body["change_history"] = [
            item for item in body["change_history"]
            if item.get("key") in ALLOWED_KEYS
        ]
        
    return body

@router.get("/profile")
async def get_profile(current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    profile = await _profile(session, current)
    body = await service.serialize_profile(session, profile)
    body["change_history"] = await service.change_history(session, profile.id)
    await session.commit()
    return _filter_profile(body)

@router.patch("/profile")
async def patch_profile(body: ProfilePatch, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    for item in body.attributes:
        if item.key not in ALLOWED_KEYS:
            raise ValidationFailedError(f"Profile key '{item.key}' is retired or invalid.", field="attributes")
            
    profile = await _profile(session, current)
    try:
        await service.apply_attributes(session, profile, [item.model_dump() for item in body.attributes if item.key in ALLOWED_KEYS])
    except ValueError as exc:
        raise ValidationFailedError(str(exc)) from exc
    await session.commit()
    body_res = await service.serialize_profile(session, profile)
    return _filter_profile(body_res)

@router.get("/profile/attributes")
async def get_attributes(current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    profile = await _profile(session, current)
    rows = await service.attributes_for(session, profile.id)
    await session.commit()
    return {
        "attributes": [service.serialize_attribute(row) for row in rows if row.key in ALLOWED_KEYS],
        "registry": [
            {
                "key": spec.key,
                "label": spec.label,
                "section": spec.section,
                "kind": spec.kind,
                "choices": list(spec.choices) or None,
                "min_items": spec.min_items,
                "exclusive_choices": list(spec.exclusive_choices) or None,
            }
            for spec in ATTRIBUTE_REGISTRY.values() if spec.key in ALLOWED_KEYS
        ],
        "weight_required": False,
    }
