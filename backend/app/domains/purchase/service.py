"""Account-scoped Care purchase candidate capture and review."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care import decisions as care_decisions
from app.domains.care import routine_plan as care_routine_plan
from app.domains.care import service as care_service
from app.domains.media import service as media_service
from app.domains.planning import context as planning_context
from app.domains.purchase import extraction, fragrance_extraction
from app.domains.purchase.candidate_truth import (
    build_care_candidate_truth,
    serialize_care_candidate_truth,
    validate_care_candidate_details,
)
from app.domains.purchase.care_assessment import assess_care_purchase
from app.domains.purchase.contract import (
    boundary_message,
    is_active_fragrance_category,
    resolve_purchase_strategy,
)
from app.domains.purchase.fragrance_truth import (
    serialize_fragrance_candidate_truth,
    validate_fragrance_candidate_details,
)
from app.domains.purchase.schemas import (
    CarePurchaseCandidateConfirm,
    CarePurchaseItemInput,
    PurchaseCandidateInspectRequest,
)
from app.domains.recommendation.models import ShoppingCandidate
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError


def _non_care_boundary(category: str) -> str:
    strategy = resolve_purchase_strategy(category)
    if strategy is not None:
        if strategy.key == "style_purchase":
            return "Use the active Style purchase check for wardrobe, shoes and accessories."
        return boundary_message(category, strategy)
    return "This candidate is not a supported Care purchase product."


def _require_care(category: str) -> None:
    if category not in {"beauty", "hair"}:
        raise ValidationFailedError(_non_care_boundary(category), field="item.category")


def _require_fragrance(category: str) -> None:
    if not is_active_fragrance_category(category):
        raise ValidationFailedError(_non_care_boundary(category), field="item.category")


def _validate_candidate_details(category: str, details: dict[str, Any] | None) -> dict[str, Any]:
    if category in {"beauty", "hair"}:
        return validate_care_candidate_details(category, details)
    if category == "perfumes":
        return validate_fragrance_candidate_details(details)
    return dict(details or {})


def _identity_error() -> ValidationFailedError:
    return ValidationFailedError(
        "This request key was already used for a different shopping candidate.",
        field="client_mutation_id",
    )


def _manual_identity_matches(row: ShoppingCandidate, item: CarePurchaseItemInput) -> bool:
    if item.category not in {"beauty", "hair", "perfumes"}:
        return False
    details = _validate_candidate_details(item.category, item.details)
    return (
        row.media_asset_id is None
        and row.source == "manual"
        and row.category == item.category
        and row.subcategory == item.subcategory
        and row.display_name == item.display_name
        and row.brand == item.brand
        and row.details == details
        and row.price == item.price
        and row.currency == item.currency.upper()
        and row.product_url == item.product_url
    )


def _screenshot_identity_matches(row: ShoppingCandidate, media_asset_id: uuid.UUID) -> bool:
    return row.media_asset_id == media_asset_id


def _apply_candidate_corrections(
    row: ShoppingCandidate, body: CarePurchaseCandidateConfirm
) -> None:
    """Apply explicit review corrections without changing candidate category."""
    fields = body.model_fields_set
    if "display_name" in fields and body.display_name is not None:
        row.display_name = body.display_name
    if "brand" in fields:
        row.brand = body.brand
    if "subcategory" in fields:
        row.subcategory = body.subcategory
    if "details" in fields and (row.category == "perfumes" or body.details is not None):
        # Fragrance review supports explicit null clearing so a stale
        # extracted concentration/family cannot survive customer correction.
        row.details = _validate_candidate_details(row.category, body.details or {})
    if "price" in fields:
        row.price = body.price
    if "currency" in fields and body.currency is not None:
        row.currency = body.currency.upper()
    if "product_url" in fields:
        row.product_url = body.product_url
    # Care facts belong only in the V3-05.1 JSONB container.  This clears any
    # legacy value on rows created by an earlier implementation as well.
    row.size = None


async def _candidate_for_key(
    session: AsyncSession, account_id: uuid.UUID, client_mutation_id: str | None
) -> ShoppingCandidate | None:
    if not client_mutation_id:
        return None
    return (
        await session.execute(
            select(ShoppingCandidate).where(
                ShoppingCandidate.account_id == account_id,
                ShoppingCandidate.client_mutation_id == client_mutation_id,
            )
        )
    ).scalar_one_or_none()


async def inspect_purchase_candidate(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    body: PurchaseCandidateInspectRequest,
) -> ShoppingCandidate:
    """Capture facts for a prospective Care or Fragrance product without evaluating it."""
    existing = await _candidate_for_key(session, account_id, body.client_mutation_id)
    if existing is not None:
        if body.media_asset_id is not None:
            if _screenshot_identity_matches(existing, body.media_asset_id):
                if body.expected_category == "perfumes":
                    _require_fragrance(existing.category)
                else:
                    _require_care(existing.category)
                return existing
        elif body.item is not None and _manual_identity_matches(existing, body.item):
            return existing
        raise _identity_error()

    if body.item is not None:
        if body.item.category == "perfumes":
            _require_fragrance(body.item.category)
        else:
            _require_care(body.item.category)
        details = _validate_candidate_details(body.item.category, body.item.details)
        row = ShoppingCandidate(
            account_id=account_id,
            source="manual",
            category=body.item.category,
            subcategory=body.item.subcategory,
            display_name=body.item.display_name,
            brand=body.item.brand,
            details=details,
            price=body.item.price,
            currency=body.item.currency.upper(),
            product_url=body.item.product_url,
            uncertain_fields=[],
            verification_state="user_declared",
            client_mutation_id=body.client_mutation_id,
        )
        session.add(row)
        await session.flush()
        return row

    # Check ownership before reading bytes or invoking the model. The same
    # ownership seam is used by every media route in the application.
    assert body.media_asset_id is not None
    await media_service.get_owned_asset(
        session, account_id=account_id, asset_id=body.media_asset_id
    )
    extractor = fragrance_extraction.extract_fragrance_candidate if body.expected_category == "perfumes" else extraction.extract_purchase_candidate
    result = await extractor(session, account_id=account_id, account_id_str=account_id_str, media_asset_id=body.media_asset_id)
    # The production seam returns gateway.AIResult.  Accepting a direct schema
    # object also keeps deterministic test doubles honest without coupling the
    # purchase domain to inventory's tuple-shaped extractor.
    extracted = getattr(result, "data", result)
    run_id = getattr(result, "run_id", None)
    model_version = getattr(result, "model", None)
    prompt_version = getattr(result, "prompt_version", extraction.PROMPT_VERSION)
    schema_version = getattr(result, "schema_version", extraction.SCHEMA_VERSION)
    if isinstance(result, tuple):
        extracted = result[0]
        run_id = result[1] if len(result) > 1 else None
        model_version = result[2] if len(result) > 2 else None
        prompt_version = result[3] if len(result) > 3 else extraction.PROMPT_VERSION
        schema_version = result[4] if len(result) > 4 else extraction.SCHEMA_VERSION
    if body.expected_category and extracted.category != body.expected_category:
        raise ValidationFailedError("The image did not match the selected purchase category. Review it before continuing.", field="category")
    if extracted.category == "perfumes":
        _require_fragrance(extracted.category)
    else:
        _require_care(extracted.category)
    details = _validate_candidate_details(extracted.category, extracted.details)
    row = ShoppingCandidate(
        account_id=account_id,
        source="photo_extracted",
        media_asset_id=body.media_asset_id,
        ai_run_id=run_id,
        category=extracted.category,
        subcategory=extracted.subcategory,
        display_name=extracted.display_name,
        brand=extracted.brand,
        details=details,
        price=extracted.price,
        currency=(extracted.currency or "INR").upper(),
        product_url=None,
        extraction_confidence=extracted.confidence,
        uncertain_fields=extracted.uncertain_fields,
        verification_state="draft",
        model_version=model_version,
        prompt_version=prompt_version,
        schema_version=schema_version,
        client_mutation_id=body.client_mutation_id,
    )
    session.add(row)
    await session.flush()
    return row


async def owned_purchase_candidate(
    session: AsyncSession, account_id: uuid.UUID, candidate_id: uuid.UUID
) -> ShoppingCandidate:
    row = (
        await session.execute(
            select(ShoppingCandidate).where(
                ShoppingCandidate.id == candidate_id,
                ShoppingCandidate.account_id == account_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("We could not find that shopping candidate.")
    return row


async def care_purchase_assessment(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    candidate_id: uuid.UUID,
    plan_date: date | None = None,
) -> dict[str, Any]:
    """Build a read-only deterministic assessment beside current Care state."""
    del account_id_str
    row = await owned_purchase_candidate(session, account_id, candidate_id)
    _require_care(row.category)
    truth = build_care_candidate_truth(row)
    if not truth.facts_trusted:
        raise ValidationFailedError(
            "Review and confirm the product details first so GlamGenius does not act on an unverified label read.",
            field="verification_state",
        )

    # Draft rejection intentionally precedes every expensive Care assembly.
    day_context = await planning_context.gather(
        session, account_id=account_id, plan_date=plan_date
    )
    care_context = await care_service.build_care_context(
        session, account_id, day_context=day_context
    )
    decision_set = care_decisions.evaluate_care_context(care_context)
    routine_plan = care_routine_plan.plan_care_routine(care_context, decision_set)
    assessment = assess_care_purchase(
        truth, care_context, decision_set, routine_plan
    )
    return assessment.as_dict()


async def care_purchase_evidence(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    candidate_id: uuid.UUID,
    plan_date: date | None = None,
) -> dict[str, Any]:
    """Project existing reviewed Evidence beside the canonical Care assessment."""
    from app.domains.purchase.evidence_service import resolve_care_purchase_evidence

    return await resolve_care_purchase_evidence(
        session,
        account_id=account_id,
        account_id_str=account_id_str,
        candidate_id=candidate_id,
        plan_date=plan_date,
    )


async def care_purchase_value(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    candidate_id: uuid.UUID,
    plan_date: date | None = None,
) -> dict[str, Any]:
    """Project read-only Care financial context beside the canonical assessment."""
    from app.domains.purchase.value_service import resolve_care_purchase_value

    return await resolve_care_purchase_value(
        session,
        account_id=account_id,
        account_id_str=account_id_str,
        candidate_id=candidate_id,
        plan_date=plan_date,
    )


async def care_purchase_verdict(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    candidate_id: uuid.UUID,
    plan_date: date | None = None,
) -> dict[str, Any]:
    """Resolve the read-only deterministic V3-05.5 Care verdict."""
    from app.domains.purchase.verdict_service import resolve_care_purchase_verdict

    return await resolve_care_purchase_verdict(
        session,
        account_id=account_id,
        account_id_str=account_id_str,
        candidate_id=candidate_id,
        plan_date=plan_date,
    )


async def fragrance_purchase_check(
    session: AsyncSession,
    *, account_id: uuid.UUID, candidate_id: uuid.UUID,
) -> dict[str, Any]:
    from app.domains.purchase.check_service import resolve_fragrance_check

    return await resolve_fragrance_check(session, account_id=account_id, candidate_id=candidate_id)


async def confirm_care_purchase_candidate(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    candidate_id: uuid.UUID,
    body: CarePurchaseCandidateConfirm,
) -> ShoppingCandidate:
    row = await owned_purchase_candidate(session, account_id, candidate_id)
    if row.category == "perfumes":
        _require_fragrance(row.category)
    else:
        _require_care(row.category)
    _apply_candidate_corrections(row, body)
    if row.verification_state == "draft":
        row.verification_state = "confirmed"
    row.uncertain_fields = []
    row.updated_at = utcnow()
    await session.flush()
    return row


def serialize_purchase_candidate(row: ShoppingCandidate) -> dict[str, Any]:
    if row.category in {"beauty", "hair"}:
        return serialize_care_candidate_truth(row)
    if row.category == "perfumes":
        return serialize_fragrance_candidate_truth(row)
    # The route is account-scoped but does not expose a purchase verdict.
    return {
        "candidate": {
            "id": str(row.id),
            "source": row.source,
            "category": row.category,
            "display_name": row.display_name,
            "brand": row.brand,
            "details": row.details,
            "verification_state": row.verification_state,
            "media_asset_id": str(row.media_asset_id) if row.media_asset_id else None,
            "in_inventory": False,
        },
        "review_required": row.verification_state == "draft",
        "facts_trusted": row.verification_state in {"user_declared", "confirmed"},
    }


# Names used by callers and review tooling.
inspect_candidate = inspect_purchase_candidate
owned_candidate = owned_purchase_candidate
confirm_purchase_candidate = confirm_care_purchase_candidate


__all__ = [
    "care_purchase_evidence",
    "care_purchase_assessment",
    "care_purchase_value",
    "care_purchase_verdict",
    "fragrance_purchase_check",
    "confirm_care_purchase_candidate",
    "inspect_candidate",
    "inspect_purchase_candidate",
    "owned_candidate",
    "owned_purchase_candidate",
    "serialize_purchase_candidate",
]
