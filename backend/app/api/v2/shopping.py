"""Should-I-buy-this routes.

Nothing here sells anything, links to a shop, or takes a payment. The user
brings something they are already looking at, and gets a straight answer about
whether it earns its place in what they already own.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.purchase import service as purchase_service
from app.domains.purchase.contract import (
    PURCHASE_STRATEGY_REGISTRY_VERSION,
    STYLE_PURCHASE_CATEGORIES,
)
from app.domains.purchase.schemas import (
    CarePurchaseCandidateConfirm,
    PurchaseCandidateInspectRequest,
)
from app.domains.recommendation import orchestrator, roi, service
from app.domains.recommendation.schemas import PurchaseDecisionCreate, ShoppingEvaluateRequest
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_shopping_decisions"))])


@router.post("/shopping/candidates/inspect")
async def inspect_purchase_candidate(
    body: PurchaseCandidateInspectRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Capture prospective Care facts without running a purchase evaluation."""
    row = await purchase_service.inspect_purchase_candidate(
        session,
        account_id=current.account_id,
        account_id_str=current.account_id_str,
        body=body,
    )
    await session.commit()
    return purchase_service.serialize_purchase_candidate(row)


@router.get("/shopping/candidates/{candidate_id}")
async def get_purchase_candidate(
    candidate_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    row = await purchase_service.owned_purchase_candidate(
        session, current.account_id, candidate_id
    )
    return purchase_service.serialize_purchase_candidate(row)


@router.post("/shopping/candidates/{candidate_id}/confirm")
async def confirm_purchase_candidate(
    candidate_id: uuid.UUID,
    body: CarePurchaseCandidateConfirm,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    row = await purchase_service.confirm_care_purchase_candidate(
        session,
        account_id=current.account_id,
        candidate_id=candidate_id,
        body=body,
    )
    await session.commit()
    return purchase_service.serialize_purchase_candidate(row)


@router.get("/shopping/candidates/{candidate_id}/care-assessment")
async def assess_care_purchase_candidate(
    candidate_id: uuid.UUID,
    on: date | None = Query(None, description="Assessment date; defaults to the account's local day"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Read-only deterministic Care purchase assessment; no verdict is emitted."""
    return await purchase_service.care_purchase_assessment(
        session,
        account_id=current.account_id,
        account_id_str=current.account_id_str,
        candidate_id=candidate_id,
        plan_date=on,
    )


@router.get("/shopping/candidates/{candidate_id}/care-evidence")
async def project_care_purchase_evidence(
    candidate_id: uuid.UUID,
    on: date | None = Query(None, description="Projection date; defaults to the account's local day"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Read-only projection of first-class reviewed Care Evidence."""
    return await purchase_service.care_purchase_evidence(
        session,
        account_id=current.account_id,
        account_id_str=current.account_id_str,
        candidate_id=candidate_id,
        plan_date=on,
    )


@router.get("/shopping/roi-model")
async def get_roi_model():
    """The Appearance ROI formula, in the open.

    A user who is told to skip something is entitled to see exactly how that was
    worked out, without asking anyone.
    """
    return {
        "version": roi.ROI_VERSION,
        "formula": "roi = sum(factor value x factor weight) / sum(weight of the factors that could be scored)",
        "note": "A factor with no data is left out and the rest are reweighted, so a missing price lowers confidence rather than the score.",
        "thresholds": {"buy": roi.BUY_THRESHOLD, "wait": roi.WAIT_THRESHOLD},
        "overrides": [
            "Something that closely matches what you already own cannot be a Buy.",
            "Something that creates no new outfit combinations cannot be a Buy.",
        ],
        "factors": [
            {"key": key, "label": roi.FACTOR_LABELS[key], "weight": weight}
            for key, weight in roi.FACTOR_WEIGHTS.items()
        ],
        "supported_categories": list(STYLE_PURCHASE_CATEGORIES),
        "strategy": "style_purchase",
        "purchase_strategy_registry_version": PURCHASE_STRATEGY_REGISTRY_VERSION,
    }


@router.post("/shopping/evaluate")
async def evaluate_purchase(
    body: ShoppingEvaluateRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Buy, Wait or Skip, with the whole calculation attached."""
    result = await orchestrator.evaluate_purchase(
        session, account_id=current.account_id, account_id_str=current.account_id_str, body=body
    )
    await session.commit()
    return result


@router.get("/shopping/evaluations/{evaluation_id}")
async def get_evaluation(
    evaluation_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    evaluation = await service.owned_evaluation(session, current.account_id, evaluation_id)
    return await service.serialize_evaluation(session, evaluation)


@router.post("/shopping/evaluations/{evaluation_id}/decision")
async def record_decision(
    evaluation_id: uuid.UUID,
    body: PurchaseDecisionCreate,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    evaluation = await service.owned_evaluation(session, current.account_id, evaluation_id)
    await service.save_decision(session, evaluation, body.decision, body.note)
    await session.commit()
    return await service.serialize_evaluation(session, evaluation)
