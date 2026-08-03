"""Scan V2 route.

Consent is enforced server-side. Failed AI runs do not consume the beta
allowance. Image base64 is never stored.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import MAX_IMAGE_BASE64_CHARS, REQUIRE_ANALYSIS_CONSENT
from app.domains.beta_access import service as beta
from app.domains.consent import service as consent_service
from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS
from app.domains.scan.models import Scan
from app.shared.database.sql import get_session
from app.shared.security.deps import (
    CurrentAccount,
    get_current_account,
    require_flag,
)

router = APIRouter(dependencies=[Depends(require_flag("v2_scan"))])


class ScanAnalyseRequest(BaseModel):
    image_base64: str
    scan_type: str = Field(default="face", pattern="^(face|hair|hands|full)$")
    idempotency_key: Optional[str] = None


def _serialise_scan(scan: Scan) -> Dict[str, Any]:
    return {
        "id": str(scan.id),
        "scan_type": scan.scan_type,
        "status": scan.status,
        "provider": scan.provider,
        "model": scan.model,
        "latency_ms": scan.latency_ms,
        "analysis": scan.analysis,
        "failure_reason": scan.failure_reason,
        "created_at": scan.created_at.isoformat() if scan.created_at else None,
    }


@router.post("/scan/analyse", status_code=status.HTTP_201_CREATED)
async def analyse_scan(
    body: ScanAnalyseRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Analyse a face/hair/hands photo. The image is discarded after the call.

    - 400 when the image is missing or too large.
    - 403 when photo-analysis consent has not been given.
    - 429 when the beta scan limit for the month is reached.
    - 502 when the AI provider fails — the beta allowance is **not** consumed.
    """
    if not body.image_base64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "image_required", "message": "An image is required."},
        )
    if len(body.image_base64) > MAX_IMAGE_BASE64_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "image_too_large",
                "message": "This photo is too large. Please pick a smaller one.",
            },
        )

    # Consent gate. Server-side, always. The client may lie about consent
    # in a request body; the recorded consent row is the source of truth.
    if REQUIRE_ANALYSIS_CONSENT:
        has_consent = await consent_service.has_consent(
            session, current.account_id, CONSENT_PHOTO_ANALYSIS
        )
        if not has_consent:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "consent_missing",
                    "message": (
                        "Please confirm you agree to have your photo analysed "
                        "before continuing."
                    ),
                },
            )

    # Beta cost/abuse limit — fail closed *before* the AI call.
    try:
        await beta.check_limit(
            session,
            account_id=current.account_id,
            feature=beta.FEATURE_SCAN,
        )
    except beta.UsageExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "beta_limit_reached",
                "feature": exc.feature,
                "limit": exc.limit,
                "period": exc.period,
                "message": (
                    "You have used your monthly beta allowance for photo checks. "
                    "It resets at the start of next month."
                ),
            },
        ) from exc

    # ---- Run the analysis via the AI provider. ----------------------------
    # A provider failure surfaces cleanly here. A failure does NOT record
    # beta usage and does NOT persist an "analysis" row.
    from app.domains.ai_gateway.providers import gemini as ai_provider
    import json as _json

    if not ai_provider.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "provider_not_configured",
                "message": (
                    "Photo analysis is temporarily unavailable. This attempt "
                    "was not counted against your allowance."
                ),
            },
        )

    prompt = (
        "You are a considerate, evidence-first appearance coach. Analyse the "
        f"attached {body.scan_type} photo and return a compact JSON object with "
        'these keys: {"observations": [string], "colour_palette": [string], '
        '"recommended_next_steps": [string], "confidence": number between 0 and 1}.\n'
        "Use premium, constructive language. Never use judgmental terms."
    )

    provider_failure_reason: Optional[str] = None
    provider_response = None
    started = __import__("time").monotonic()
    try:
        provider_response = await ai_provider.generate(
            prompt=prompt, image_base64=body.image_base64
        )
    except (ai_provider.ProviderTimeout, ai_provider.ProviderCallFailed) as exc:
        provider_failure_reason = f"{type(exc).__name__}: {exc}"
    except ai_provider.ImageRejected as exc:
        # Bad image is a client error — do not persist an analysis row.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "image_invalid", "message": str(exc)},
        ) from exc
    except ai_provider.ProviderNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "provider_not_configured", "message": str(exc)},
        ) from exc

    if provider_failure_reason is not None:
        scan = Scan(
            account_id=current.account_id,
            scan_type=body.scan_type,
            status="provider_failure",
            provider=ai_provider.PROVIDER_NAME,
            failure_reason=provider_failure_reason[:400],
            analysis={},
        )
        session.add(scan)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "provider_failure",
                "message": (
                    "We couldn't run this analysis just now. Please try again "
                    "in a moment; this attempt was not counted against your "
                    "allowance."
                ),
            },
        )

    # Parse the provider's JSON leniently — a bad response is a provider
    # failure, not a client error.
    analysis: Dict[str, Any]
    try:
        text = provider_response.text.strip()
        # Strip Markdown code fences the model sometimes emits.
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip("` \n")
        analysis = _json.loads(text)
        if not isinstance(analysis, dict):
            raise ValueError("expected a JSON object")
    except (ValueError, AttributeError) as exc:
        scan = Scan(
            account_id=current.account_id,
            scan_type=body.scan_type,
            status="provider_failure",
            provider=ai_provider.PROVIDER_NAME,
            model=provider_response.model if provider_response else None,
            failure_reason=f"invalid_json: {exc}"[:400],
            analysis={},
        )
        session.add(scan)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "provider_failure",
                "message": (
                    "We couldn't understand the analysis just now. Please try "
                    "again; this attempt was not counted against your allowance."
                ),
            },
        ) from exc

    scan = Scan(
        account_id=current.account_id,
        scan_type=body.scan_type,
        status="ok",
        provider=ai_provider.PROVIDER_NAME,
        model=provider_response.model,
        prompt_version="scan.v1",
        schema_version="scan.v1",
        latency_ms=int((__import__("time").monotonic() - started) * 1000),
        analysis=analysis,
    )
    session.add(scan)
    await session.flush()

    # Only successful runs consume the beta allowance.
    await beta.record_usage(
        session,
        account_id=current.account_id,
        feature=beta.FEATURE_SCAN,
        idempotency_key=body.idempotency_key,
    )
    await session.commit()

    return _serialise_scan(scan)


@router.get("/scan/history")
async def scan_history(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
):
    rows = (
        await session.execute(
            select(Scan)
            .where(Scan.account_id == current.account_id)
            .order_by(Scan.created_at.desc())
            .limit(min(max(limit, 1), 200))
        )
    ).scalars().all()
    return {"scans": [_serialise_scan(s) for s in rows]}


@router.get("/scan/history/{scan_id}")
async def scan_detail(
    scan_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    scan = await session.get(Scan, scan_id)
    if scan is None or scan.account_id != current.account_id:
        # Not-found for both cases — no cross-account probing.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return _serialise_scan(scan)
