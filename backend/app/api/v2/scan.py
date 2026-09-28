"""Scan V2 route.

Consent is enforced server-side. Failed AI runs do not consume the beta
allowance. Image base64 is never stored.

An ``idempotency_key`` names one logical photo check. The first request with it
reserves the allowance for that key and is the only one that reaches the
provider; a duplicate while it runs is told it is in progress; a retry after
it succeeded replays the stored result. A known failure leaves the key free to
try again. The key is the client's identifier, never derived from the image.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import MAX_IMAGE_BASE64_CHARS, REQUIRE_ANALYSIS_CONSENT
from app.domains.beta_access import service as beta
from app.domains.beta_access.models import IDEMPOTENCY_KEY_MAX_LENGTH
from app.domains.consent import service as consent_service
from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS
from app.domains.scan import strings as scan_copy
from app.domains.scan.models import SCAN_STATUS_OK, Scan
from app.shared.database.sql import get_session
from app.shared.security.deps import (
    CurrentAccount,
    get_current_account,
    require_flag,
)

router = APIRouter(dependencies=[Depends(require_flag("v2_scan"))])


#: A usable idempotency key: visible ASCII, no spaces, nothing empty.
IDEMPOTENCY_KEY_PATTERN = r"^[!-~]+$"
#: Set on a response that replays an earlier successful result.
REPLAYED_HEADER = "Idempotent-Replayed"

#: The system instruction for a photo check. It is the role sentence that used
#: to open the task prompt, moved verbatim into the channel the provider
#: adapter requires — not a new persona, and no new instruction.
SCAN_SYSTEM = "You are a considerate, evidence-first appearance coach."
#: ``scan.v2``: the role sentence moved from the task prompt to ``system``.
#: The task itself is unchanged.
SCAN_PROMPT_VERSION = "scan.v2"
#: The JSON the model is asked for is unchanged.
SCAN_SCHEMA_VERSION = "scan.v1"


class ScanAnalyseRequest(BaseModel):
    image_base64: str
    scan_type: str = Field(default="face", pattern="^(face|hair|hands|full)$")
    # Validated here, before anything is reserved, sent or stored: a key the
    # usage and scan tables cannot hold is refused as a request error, never
    # discovered by PostgreSQL after the provider has been paid.
    idempotency_key: str | None = Field(
        default=None, min_length=1, max_length=IDEMPOTENCY_KEY_MAX_LENGTH, pattern=IDEMPOTENCY_KEY_PATTERN,
    )


async def _completed_scan(session: AsyncSession, account_id: uuid.UUID, key: str) -> Scan | None:
    """The successful result this account stored under ``key``, if it still exists."""
    return (await session.execute(
        select(Scan).where(
            Scan.account_id == account_id,
            Scan.idempotency_key == key,
            Scan.status == SCAN_STATUS_OK,
        )
    )).scalar_one_or_none()


def _serialise_scan(scan: Scan) -> dict[str, Any]:
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
    response: Response,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Analyse a face/hair/hands photo. The image is discarded after the call.

    - 400 when the image is missing or too large.
    - 403 when photo-analysis consent has not been given.
    - 409 ``scan_in_progress`` when a request with the same idempotency key is
      still running; ``scan_already_completed`` when that key's check was
      counted but its result is no longer stored. Neither reaches the provider.
    - 422 when the idempotency key is empty, too long or not visible ASCII.
    - 429 when the beta scan limit for the month is reached.
    - 502 when the AI provider fails — the beta allowance is **not** consumed.

    A retry with the key of a check that succeeded answers with that stored
    result, unchanged, and the ``Idempotent-Replayed: true`` header.
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

    # Beta cost/abuse limit — fail closed *before* the AI call. One unit is
    # reserved atomically (a check followed later by a record let parallel
    # requests all see "one left" and all spend it), and committed before the
    # provider is called, so no lock or transaction is held across that call.
    try:
        reservation = await beta.reserve_usage(
            session,
            account_id=current.account_id,
            feature=beta.FEATURE_SCAN,
            idempotency_key=body.idempotency_key,
        )
    except beta.UsageOperationCompleted as exc:
        # The key's check already succeeded and was counted: replay it. The
        # key names that one operation, so a new photo under it is not
        # analysed, and nothing is reserved, sent or counted.
        replayed = await _completed_scan(session, current.account_id, exc.idempotency_key)
        # Serialised before the rollback, which expires every loaded row.
        replay_body = None if replayed is None else _serialise_scan(replayed)
        await session.rollback()
        if replay_body is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "scan_already_completed",
                    "allowance_consumed": False,
                    "message": scan_copy.text("scan.idempotency.result_unavailable"),
                },
            ) from exc
        response.headers[REPLAYED_HEADER] = "true"
        return replay_body
    except beta.UsageOperationInProgress as exc:
        # A live reservation holds the key: the first request is still
        # running, or died less than a reservation lifetime ago with an
        # unknown outcome. Either way this one never reaches the provider.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "scan_in_progress",
                "allowance_consumed": False,
                "message": scan_copy.text("scan.idempotency.in_progress"),
            },
        ) from exc
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
    await session.commit()

    async def give_back() -> None:
        """A known failure delivered nothing: the reserved unit goes back."""
        if reservation is not None:
            await beta.release_usage(session, reservation)

    # ---- Run the analysis via the AI provider. ----------------------------
    # A provider failure surfaces cleanly here. A failure does NOT record
    # beta usage and does NOT persist an "analysis" row.
    import json as _json

    from app.domains.ai_gateway.providers import gemini as ai_provider

    if not ai_provider.is_configured():
        await give_back()
        await session.commit()
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

    # The task only; the role sentence travels as ``system`` (SCAN_SYSTEM).
    prompt = (
        f"Analyse the attached {body.scan_type} photo and return a compact JSON object with "
        'these keys: {"observations": [string], "colour_palette": [string], '
        '"recommended_next_steps": [string], "confidence": number between 0 and 1}.\n'
        "Use premium, constructive language. Never use judgmental terms."
    )

    provider_failure_reason: str | None = None
    provider_response = None
    started = __import__("time").monotonic()
    try:
        # Keyword arguments against the adapter's own contract: ``system`` is
        # required, and omitting it was a TypeError before anything was sent.
        provider_response = await ai_provider.generate(
            prompt=prompt,
            system=SCAN_SYSTEM,
            image_base64=body.image_base64,
        )
    except (ai_provider.ProviderTimeout, ai_provider.ProviderCallFailed) as exc:
        provider_failure_reason = f"{type(exc).__name__}: {exc}"
    except ai_provider.ImageRejected as exc:
        # Bad image is a client error — do not persist an analysis row.
        await give_back()
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "image_invalid", "message": str(exc)},
        ) from exc
    except ai_provider.ProviderNotConfigured as exc:
        await give_back()
        await session.commit()
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
        await give_back()
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
    analysis: dict[str, Any]
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
        await give_back()
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

    # The one successful result, carrying the key a retry replays it by. A
    # failed row above never carries the key, so a known failure leaves the
    # key free for a retry.
    scan = Scan(
        account_id=current.account_id,
        scan_type=body.scan_type,
        status=SCAN_STATUS_OK,
        provider=ai_provider.PROVIDER_NAME,
        model=provider_response.model,
        prompt_version=SCAN_PROMPT_VERSION,
        schema_version=SCAN_SCHEMA_VERSION,
        latency_ms=int((__import__("time").monotonic() - started) * 1000),
        analysis=analysis,
        idempotency_key=body.idempotency_key,
    )
    session.add(scan)
    await session.flush()

    # Only successful runs consume the beta allowance. One commit establishes
    # all three: the replayable result, the usage event under the same key,
    # and the reservation gone. There is no state with one and not the others.
    if reservation is not None:
        await beta.settle_usage(session, reservation)
    else:
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
