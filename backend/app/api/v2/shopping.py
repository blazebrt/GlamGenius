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

from app.domains.family.decision_subject import serialize_decision_subject
from app.domains.purchase import decision_memory
from app.domains.purchase import service as purchase_service
from app.domains.purchase.contract import (
    PURCHASE_CATEGORY_LABELS,
    PURCHASE_STRATEGY_REGISTRY,
    PURCHASE_STRATEGY_REGISTRY_VERSION,
    resolve_purchase_strategy,
)
from app.domains.purchase.fragrance_truth import FRAGRANCE_SEASON_KEYS
from app.domains.purchase.schemas import (
    CarePurchaseCandidateConfirm,
    PurchaseCandidateInspectRequest,
)
from app.domains.recommendation.occasions import OCCASION_KEYS, OCCASIONS
from app.domains.recommendation.schemas import PurchaseDecisionCreate
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_shopping_decisions"))])


#: How a route turns an optional query parameter into a checked subject.
#:
#: Omitting it means "me", which is what every client sent before households
#: existed and must keep meaning. A named subject is a *claim*: it goes through
#: the Step 11A resolver under the authenticated account, so a member of another
#: household, a deactivated member and an invented id are refused identically
#: and without echoing the id — telling them apart would confirm that the id
#: names a real person in a household this caller cannot see.
async def _decision_subject(
    session: AsyncSession,
    current: CurrentAccount,
    subject_id: uuid.UUID | None,
    *,
    for_write: bool = False,
):
    from app.domains.family.decision_subject import (
        canonical_decision_subject,
        decision_subject_for_write,
    )
    from app.domains.family.subject import (
        SubjectNotFound,
        account_holder_subject,
        resolve_subject,
    )

    try:
        claim = (
            account_holder_subject(current.account_id)
            if subject_id is None
            else await resolve_subject(
                session, account_id=current.account_id, subject_id=subject_id,
            )
        )
        resolve = decision_subject_for_write if for_write else canonical_decision_subject
        return await resolve(
            session, principal_account_id=current.account_id, subject=claim,
        )
    except SubjectNotFound as exc:
        raise NotFoundError("That person is not on this account.") from exc


@router.get("/shopping/strategies")
async def get_purchase_strategies():
    """Return the deterministic purchase-strategy discovery contract."""
    return {
        "purchase_strategy_registry_version": PURCHASE_STRATEGY_REGISTRY_VERSION,
        "fragrance_context_options": {
            "occasions": [{"key": key, "label": OCCASIONS[key].label} for key in OCCASION_KEYS],
            "seasons": [{"key": key, "label": key.title()} for key in FRAGRANCE_SEASON_KEYS],
        },
        "strategies": [
            {
                "key": strategy.key,
                "label": strategy.label,
                "state": strategy.state,
                "categories": [
                    {"key": category, "label": PURCHASE_CATEGORY_LABELS[category]}
                    for category in strategy.categories
                    if category in PURCHASE_CATEGORY_LABELS
                ],
            }
            for strategy in PURCHASE_STRATEGY_REGISTRY
        ],
    }


@router.post("/shopping/candidates/inspect")
async def inspect_purchase_candidate(
    body: PurchaseCandidateInspectRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Capture prospective Care or Fragrance facts without running a purchase evaluation."""
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


@router.get("/shopping/candidates/{candidate_id}/care-value")
async def project_care_purchase_value(
    candidate_id: uuid.UUID,
    on: date | None = Query(None, description="Projection date; defaults to the account's local day"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Read-only projection of Care spend and owned Value to Recover context."""
    return await purchase_service.care_purchase_value(
        session,
        account_id=current.account_id,
        account_id_str=current.account_id_str,
        candidate_id=candidate_id,
        plan_date=on,
    )


@router.get("/shopping/candidates/{candidate_id}/care-verdict")
async def project_care_purchase_verdict(
    candidate_id: uuid.UUID,
    on: date | None = Query(None, description="Verdict date; defaults to the account's local day"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Return a recomputable deterministic Care Buy/Wait/Skip policy result."""
    return await purchase_service.care_purchase_verdict(
        session,
        account_id=current.account_id,
        account_id_str=current.account_id_str,
        candidate_id=candidate_id,
        plan_date=on,
    )


@router.get("/shopping/candidates/{candidate_id}/care-check")
async def get_care_purchase_check(
    candidate_id: uuid.UUID,
    on: date | None = Query(None, description="Check date; defaults to the account's local day"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Compose the trusted Care Purchase customer read model without writes."""
    from app.domains.purchase.check_service import resolve_care_purchase_check

    return await resolve_care_purchase_check(
        session,
        account_id=current.account_id,
        account_id_str=current.account_id_str,
        candidate_id=candidate_id,
        plan_date=on,
    )


@router.get("/shopping/candidates/{candidate_id}/fragrance-check")
async def get_fragrance_purchase_check(
    candidate_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Compose the trusted Fragrance Purchase customer read model without writes."""
    from app.domains.purchase.check_service import resolve_fragrance_check

    return await resolve_fragrance_check(
        session, account_id=current.account_id, candidate_id=candidate_id
    )


@router.post("/shopping/candidates/{candidate_id}/decision")
async def record_candidate_decision(
    candidate_id: uuid.UUID,
    body: PurchaseDecisionCreate,
    on: date | None = Query(None, description="Decision date; defaults to the account's local day"),
    subject_id: uuid.UUID | None = Query(None, description="Which household member this decision is for; omit for yourself"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Persist one human's Care or Fragrance outcome from its canonical check.

    Order matters here, and it changed in Step 11C. The subject authority is
    taken *first* — the account row, and for a named member their household row
    — and only then is the candidate frozen. Locking the candidate first and
    reaching back for the account afterwards is the inversion that deadlocks
    against account deletion.
    """
    decision_subject = await _decision_subject(
        session, current, subject_id, for_write=True,
    )
    candidate = await purchase_service.owned_purchase_candidate(session, current.account_id, candidate_id)
    # Freeze the trusted candidate facts before composing the canonical check.
    # Otherwise a concurrent confirmation could pair an old recommendation
    # snapshot with a newly changed Step 9A identity fingerprint.
    await session.refresh(candidate, with_for_update=True)
    strategy = resolve_purchase_strategy(candidate.category)
    if strategy is None or strategy.state != "active":
        raise ValidationFailedError("This candidate is not eligible for an active purchase strategy.", field="category")
    if strategy.key == "care_purchase":
        from app.domains.purchase.check_service import resolve_care_purchase_check
        check = await resolve_care_purchase_check(
            session, account_id=current.account_id,
            account_id_str=current.account_id_str, candidate_id=candidate_id,
            plan_date=on, decision_subject=decision_subject,
        )
        row = await decision_memory.save_care_decision(
            session, principal_account_id=current.account_id,
            decision_subject=decision_subject, candidate_id=candidate_id,
            check=check, decision=body.decision, note=body.note,
        )
    elif strategy.key == "fragrance_purchase":
        from app.domains.purchase.check_service import resolve_fragrance_check
        check = await resolve_fragrance_check(
            session, account_id=current.account_id, candidate_id=candidate_id,
            decision_subject=decision_subject,
        )
        row = await decision_memory.save_fragrance_decision(
            session, principal_account_id=current.account_id,
            decision_subject=decision_subject, candidate_id=candidate_id,
            check=check, decision=body.decision, note=body.note,
        )
    else:
        raise ValidationFailedError("This purchase strategy is not supported by this endpoint.", field="category")
    payload = decision_memory.serialize_purchase_decision(row)
    payload["subject"] = serialize_decision_subject(decision_subject)
    await session.commit()
    return payload


@router.get("/shopping/candidates/{candidate_id}/decision")
async def get_purchase_decision_memory(
    candidate_id: uuid.UUID,
    subject_id: uuid.UUID | None = Query(None, description="Which household member to read; omit for yourself"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Read one human's latest decision memory without choosing a strategy.

    A pure read. It never adopts a legacy row and never creates one — the same
    candidate may be waiting for one member and bought by another, and asking
    the question must not change any of those answers.
    """
    await purchase_service.owned_purchase_candidate(session, current.account_id, candidate_id)
    decision_subject = await _decision_subject(session, current, subject_id)
    row = await decision_memory.current_purchase_decision_for_subject(
        session,
        principal_account_id=current.account_id,
        decision_subject=decision_subject,
        candidate_id=candidate_id,
    )
    return {
        "purchase_decision_memory_version": decision_memory.PURCHASE_DECISION_MEMORY_VERSION,
        "subject": serialize_decision_subject(decision_subject),
        "decision": decision_memory.serialize_purchase_decision(row) if row else None,
    }


@router.get("/shopping/decision-history")
async def get_purchase_decision_history(
    limit: int = Query(20, ge=1, le=50),
    before: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = Query(None, description="Whose history to read; omit for yourself"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Bounded newest-first history for one human on this account.

    The coverage block is not decoration. Where decisions exist that cannot be
    attributed to anybody, this page says so rather than presenting a confident
    subset as the whole story.
    """
    decision_subject = await _decision_subject(session, current, subject_id)
    rows = await decision_memory.decision_history(
        session,
        principal_account_id=current.account_id,
        decision_subject=decision_subject,
        limit=limit,
        before=before,
    )
    return {
        "purchase_decision_event_version": decision_memory.PURCHASE_DECISION_EVENT_VERSION,
        "subject": serialize_decision_subject(decision_subject),
        "history_coverage": await decision_memory.history_coverage(
            session,
            principal_account_id=current.account_id,
            decision_subject=decision_subject,
        ),
        "items": [decision_memory.serialize_decision_event(row) for row in rows],
        "next_before": str(rows[-1].id) if len(rows) == limit else None,
    }


@router.get("/shopping/candidates/{candidate_id}/purchase-guard")
async def get_purchase_guard(
    candidate_id: uuid.UUID,
    subject_id: uuid.UUID | None = Query(None, description="Whose prior consideration to project; omit for yourself"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """One human's exact prior context; the active strategy remains decisive.

    "You considered this before" is a sentence about a person. Saying it to the
    wrong member of a household is both wrong and a disclosure of what somebody
    else decided, so the guard sees only this subject's own history.
    """
    candidate = await purchase_service.owned_purchase_candidate(session, current.account_id, candidate_id)
    strategy = resolve_purchase_strategy(candidate.category)
    if strategy is None or strategy.state != "active":
        raise ValidationFailedError("This candidate is not eligible for a purchase guard.", field="category")
    decision_subject = await _decision_subject(session, current, subject_id)
    return await decision_memory.purchase_guard(
        session,
        principal_account_id=current.account_id,
        decision_subject=decision_subject,
        candidate=candidate,
    )
