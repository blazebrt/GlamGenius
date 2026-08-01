"""Signed-out preview and authenticated scan routes."""
from fastapi import APIRouter, Depends, Request
from typing import Any, Dict, Optional
from datetime import datetime
import uuid

from database import db
from models import ScanPreviewRequest, ScanAnalysisRequest, ScanResult
from security import (
    get_current_user,
    _assert_preview_quota,
    _record_preview,
    _ensure_scan_quota,
    _month_key,
)
from ai import analyze_image_with_gemini, _assert_ai_quota
from settings import PREVIEW_COLOR_COUNT
from invites import assert_invite_usable

from app.config import MAX_IMAGE_BASE64_CHARS
from app.domains.ai_gateway.schemas import profile_value
from app.domains.entitlements import service as entitlements
from app.domains.entitlements.models import FEATURE_SCAN_ANALYZE
from app.shared.errors.exceptions import ValidationFailedError

router = APIRouter()


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    """Parse an ai_run id for the usage ledger. None when it is unusable."""
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, AttributeError, TypeError):
        return None


def _assert_image_within_limit(image_base64: Optional[str]) -> None:
    """Reject an oversized payload before it reaches a paid provider.

    Audit finding F26: there was no ceiling at all, so one request could push an
    arbitrarily large string straight to Gemini.
    """
    if image_base64 and len(image_base64) > MAX_IMAGE_BASE64_CHARS:
        raise ValidationFailedError(
            "That photo is too large to analyse. Please use a smaller one.",
            field="image_base64",
        )


@router.post("/scan/preview")
async def preview_scan(request: ScanPreviewRequest, http_request: Request):
    """A free taste of the result for someone who has not signed up yet.

    Deliberately stores nothing — no user, no scan record, no image — and
    returns only the top of the result. While invite-only, a valid invite
    code is required so open internet traffic cannot burn Gemini spend.
    """
    # Validate without consuming — signup is what spends a use.
    await assert_invite_usable(request.invite_code)
    _assert_image_within_limit(request.image_base64)

    client_ip = http_request.client.host if http_request.client else "unknown"
    await _assert_preview_quota(client_ip)
    await _record_preview(client_ip)

    # Raises AnalysisUnavailableError if the analysis cannot be trusted. There
    # is no fabricated fallback to fall back to any more.
    analysis = await analyze_image_with_gemini(request.image_base64, request.scan_type, {})

    profile = analysis.get("profile") or {}
    fashion = analysis.get("fashion") or {}
    colors = fashion.get("best_clothing_colors") or []

    return {
        "preview": True,
        "signup_required": True,
        "profile": {
            "skin_tone": profile.get("skin_tone"),
            "undertone": profile.get("undertone"),
            "face_shape": profile.get("face_shape"),
        },
        "best_clothing_colors": colors[:PREVIEW_COLOR_COUNT],
        "total_colors_found": len(colors),
        "locked": [
            "Colours to go easy on",
            "Silhouettes and fits for your body",
            "Outfit ideas for Indian occasions",
            "Skin and hair observations",
            "Daily care routine",
            "Ingredients to look for",
            "Indian foods for your goals",
            "Salon ideas",
            "Saved history and progress",
        ],
        "meta": {
            "disclaimer": (analysis.get("meta") or {}).get(
                "disclaimer",
                "General fashion, wellness and style guidance — not medical advice.",
            ),
            "scan_focus": request.scan_type,
        },
    }


@router.post("/scan/analyze")
async def analyze_scan(
    request: ScanAnalysisRequest, user: Dict[str, Any] = Depends(get_current_user)
):
    # Identity comes from the token. There is no anonymous scanning, and the
    # monthly free limit is always enforced.
    user_id = user["id"]
    _assert_image_within_limit(request.image_base64)
    await _assert_ai_quota(user)
    await _ensure_scan_quota(user)

    context = {
        "city": request.city or user.get("city"),
        "diet": request.diet or user.get("diet"),
        "budget_range": request.budget_range or user.get("budget_range"),
        "occasion": request.occasion,
        "height_cm": request.height_cm if request.height_cm is not None else user.get("height_cm"),
        "weight_kg": request.weight_kg if request.weight_kg is not None else user.get("weight_kg"),
        "body_type": request.body_type or user.get("body_type"),
        "style_vibe": request.style_vibe or user.get("style_vibe"),
    }

    # Everything below this line only runs when a validated result exists.
    # If the analysis fails, AnalysisUnavailableError propagates: no scan is
    # stored, no profile field is written, and no allowance is consumed.
    analysis = await analyze_image_with_gemini(
        request.image_base64, request.scan_type, context, v1_user_id=user_id
    )

    scan_result = ScanResult(
        user_id=user_id,
        scan_type=request.scan_type,
        image_base64=(request.image_base64[:80] + "...") if request.image_base64 else None,
        analysis=analysis,
    )
    await db.scans.insert_one(scan_result.dict())

    update_data: Dict[str, Any] = {"updated_at": datetime.utcnow()}
    profile = analysis.get("profile") or {}

    # Only values from the known vocabulary are written to the stored profile.
    # An odd one-off answer is still shown to the user, but must not quietly
    # become a permanent "fact" about them.
    for response_field, profile_field in (
        ("face_shape", "face_shape"),
        ("skin_type_visible", "skin_type"),
        ("hair_type_visible", "hair_type"),
        ("skin_tone", "skin_tone"),
        ("undertone", "undertone"),
    ):
        accepted = profile_value(response_field, profile.get(response_field))
        if accepted:
            update_data[profile_field] = accepted

    observations = analysis.get("observations") or []
    skin_obs = [o.get("what_i_see") for o in observations if isinstance(o, dict) and o.get("area") in ("face", "t_zone", "cheeks", "under_eyes", "hands", "nails")]
    hair_obs = [o.get("what_i_see") for o in observations if isinstance(o, dict) and o.get("area") in ("hair", "scalp", "ends")]
    if skin_obs:
        update_data["skin_concerns"] = skin_obs[:5]
    if hair_obs:
        update_data["hair_concerns"] = hair_obs[:5]

    month = _month_key()
    await db.users.update_one(
        {"id": user_id},
        {
            "$set": {**update_data, "scan_month_key": month},
            "$inc": {"scans_used_this_month": 1},
        },
    )

    # Itemised record of the check that was just spent, beside the V1 counter.
    provenance = (analysis.get("meta") or {}).get("provenance") or {}
    await entitlements.record_consumption(
        v1_user_id=user_id,
        feature=FEATURE_SCAN_ANALYZE,
        period_key=month,
        reason="analysis_succeeded",
        ai_run_id=_as_uuid(provenance.get("ai_run_id")),
    )

    return {
        "scan_id": scan_result.id,
        "analysis": analysis,
        "profile_updated": True,
    }


@router.get("/scan/history")
async def get_scan_history(user: Dict[str, Any] = Depends(get_current_user)):
    scans = await db.scans.find({"user_id": user["id"]}).sort("created_at", -1).to_list(50)
    return [
        {
            "id": s["id"],
            "scan_type": s["scan_type"],
            "analysis": s.get("analysis", {}),
            "created_at": s["created_at"],
            "scores": (s.get("analysis") or {}).get("wellness_scores", {}),
        }
        for s in scans
    ]


@router.get("/scan/trends")
async def get_scan_trends(user: Dict[str, Any] = Depends(get_current_user)):
    scans = await db.scans.find({"user_id": user["id"]}).sort("created_at", 1).to_list(30)
    points = []
    for s in scans:
        scores = (s.get("analysis") or {}).get("wellness_scores") or {}
        points.append(
            {
                "date": s["created_at"],
                "skin_score": scores.get("skin_score"),
                "hair_score": scores.get("hair_score"),
                "overall_score": scores.get("overall_score"),
                "scan_id": s["id"],
            }
        )
    return {"points": points}
