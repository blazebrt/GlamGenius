"""Read-only resolver for the V3-05.5 Care purchase verdict."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care.environment_decision import evaluate_environment
from app.domains.purchase import service as purchase_service
from app.domains.purchase.candidate_truth import build_care_candidate_truth
from app.domains.purchase.care_verdict import project_care_purchase_verdict
from app.domains.purchase.evidence_service import resolve_care_purchase_evidence
from app.domains.purchase.value_service import resolve_care_purchase_value
from app.shared.errors.exceptions import ValidationFailedError


async def resolve_care_purchase_verdict(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    candidate_id: uuid.UUID,
    plan_date: date | None,
    assessment: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
    value: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one current-context Care verdict without creating any rows."""
    candidate = await purchase_service.owned_purchase_candidate(
        session, account_id, candidate_id
    )
    purchase_service._require_care(candidate.category)
    truth = build_care_candidate_truth(candidate)
    if not truth.facts_trusted:
        raise ValidationFailedError(
            "Review and confirm the product details first so GlamGenius does not act on an unverified label read.",
            field="verification_state",
        )

    if assessment is None:
        assessment = await purchase_service.care_purchase_assessment(
            session,
            account_id=account_id,
            account_id_str=account_id_str,
            candidate_id=candidate_id,
            plan_date=plan_date,
        )
    canonical_date = assessment["plan_date"]
    if isinstance(canonical_date, str):
        canonical_date = date.fromisoformat(canonical_date)
    if evidence is None:
        evidence = await resolve_care_purchase_evidence(
            session,
            account_id=account_id,
            account_id_str=account_id_str,
            candidate_id=candidate_id,
            plan_date=canonical_date,
            assessment=assessment,
        )
    if value is None:
        value = await resolve_care_purchase_value(
            session,
            account_id=account_id,
            account_id_str=account_id_str,
            candidate_id=candidate_id,
            plan_date=canonical_date,
            assessment=assessment,
        )
    payload = project_care_purchase_verdict(assessment, evidence, value).as_dict()
    payload["environment"] = await _environment_context(
        session, account_id=account_id, plan_date=canonical_date, families=truth.recognised_ingredient_families,
    )
    return payload


#: Ingredient families the air-quality rules defer. A verdict about one of these
#: is worth qualifying with what the air has been doing.
STRONG_ACTIVE_FAMILIES = frozenset({"retinoid", "aha", "bha", "benzoyl_peroxide"})


async def _environment_context(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    plan_date: date,
    families: tuple[str, ...],
) -> dict[str, Any] | None:
    """What recent air quality means for buying this particular product.

    The verdict itself is not changed: whether something fills a gap in a
    routine does not depend on today's weather, and the ordered policy that
    decides Buy, Wait or Skip is left exactly as it is. What is added is the
    thing a person would otherwise find out the hard way — that the strong
    active they are about to buy is currently deferred, and until when.

    ``None`` for anything that is not a strong active, so the ordinary case
    carries no environmental noise at all.
    """
    from app.domains.care import environment_service
    from app.domains.care.environment_decision import EnvironmentAction

    matched = sorted(set(families) & STRONG_ACTIVE_FAMILIES)
    if not matched:
        return None
    window = await environment_service.load_window(
        session, account_id=account_id, plan_date=plan_date,
    )
    today = window.today
    if not today.is_indian_reading:
        return None
    allowed = await environment_service.allowed_environment_rule_ids(session)
    decision = evaluate_environment(window, allowed_rule_ids=allowed)
    if decision is None:
        return None
    # Whether strong actives are deferred is a question about the whole day, not
    # only about the line that happened to win the precedence order. On a Very
    # Poor day the primary decision may be the post-exposure cleanse while the
    # deferral is still in force underneath it.
    from app.domains.care.environment_rules import ENVIRONMENT_RULE_BY_ID

    deferring = any(
        ENVIRONMENT_RULE_BY_ID[rule_id].action is EnvironmentAction.DEFER_ACTIVE
        for rule_id in decision.fired_rule_ids
    )
    return {
        "strong_active_families": matched,
        "aqi": today.aqi,
        "aqi_category": today.category,
        "index_system": today.index_system,
        "rule_id": decision.rule_id,
        "currently_deferred": deferring,
        "note": (
            f"Air is {(today.category or 'unknown').lower()} today "
            f"(NAQI {today.aqi}, CPCB category {today.category}). "
            "Exfoliation and retinoids are deferred until the air is Satisfactory "
            "or better, so a new one would sit unused for now."
            if deferring
            else f"Air is {(today.category or 'unknown').lower()} today "
            f"(NAQI {today.aqi}, CPCB category {today.category})."
        ),
    }


__all__ = ["resolve_care_purchase_verdict"]
