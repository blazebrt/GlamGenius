"""Persistence, ownership and serialisation for the decision engine.

Two rules run through everything here:

* **Ownership is checked, never assumed.** Every read and write is filtered by
  ``account_id`` taken from the caller's token. An id in a URL that belongs to
  someone else returns "not found", not their data.
* **An owned item in a response is always a real row.** ``serialize_look``
  resolves each ``look_items.inventory_item_id`` against the account's inventory
  and drops anything that no longer resolves, so an archived or deleted item can
  never keep appearing in a saved look.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.models import InventoryItem
from app.domains.recommendation.candidates import OptionalAddition, OutfitCandidate
from app.domains.recommendation.context import StyleContext
from app.domains.recommendation.models import (
    ENGINE_VERSION, OWNERSHIP_OPTIONAL, OWNERSHIP_OWNED, CompatibilityEdge, Look, LookAdjustment,
    LookFeedback, LookItem, OccasionRecord, PurchaseDecision, PurchaseEvaluation,
    PurchaseEvaluationFactor, RecommendationEntitlement, RecommendationInput, RecommendationRun,
    ShoppingCandidate, StyleRequest,
)
from app.domains.recommendation.occasions import (
    SLOT_ACCESSORIES, SLOT_CLOTHING, SLOT_GROOMING, SLOT_HAIR, SLOT_PERFUME, SLOT_SHOES, get_occasion, serialize_occasion,
)
from app.domains.recommendation.ranking import RankedLook
from app.domains.recommendation.schemas import OccasionCreate, OccasionPatch
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError

SLOT_ORDER = (SLOT_CLOTHING, SLOT_SHOES, SLOT_ACCESSORIES, SLOT_PERFUME, SLOT_HAIR, SLOT_GROOMING)

# The decision engine is a paid feature and billing is unavailable, so every
# account gets the same backend-granted beta allowance. No payment path can
# change this number, and the app never decides it.
BETA_MONTHLY_LOOKS = 60
BETA_MONTHLY_EVALUATIONS = 60
FEATURE_STYLE = "style_occasion"
FEATURE_SHOPPING = "shopping_evaluation"


def period_key(moment: Optional[datetime] = None) -> str:
    now = moment or utcnow()
    return f"{now.year:04d}-{now.month:02d}"


# --- Entitlements -----------------------------------------------------------


async def entitlement_for(session: AsyncSession, account_id: uuid.UUID, feature: str) -> RecommendationEntitlement:
    key = period_key()
    row = (await session.execute(
        select(RecommendationEntitlement).where(
            RecommendationEntitlement.account_id == account_id,
            RecommendationEntitlement.feature == feature,
            RecommendationEntitlement.period_key == key,
        )
    )).scalar_one_or_none()
    if row is None:
        included = BETA_MONTHLY_LOOKS if feature == FEATURE_STYLE else BETA_MONTHLY_EVALUATIONS
        row = RecommendationEntitlement(account_id=account_id, feature=feature, period_key=key, included=included, used=0, source="beta_grant")
        session.add(row)
        await session.flush()
    return row


async def consume_entitlement(session: AsyncSession, account_id: uuid.UUID, feature: str) -> RecommendationEntitlement:
    row = await entitlement_for(session, account_id, feature)
    if row.used >= row.included:
        raise ValidationFailedError(
            f"You have used all {row.included} of this month's {feature.replace('_', ' ')} runs. "
            "The allowance resets at the start of next month.",
            field="entitlement",
        )
    row.used += 1
    await session.flush()
    return row


def serialize_entitlement(row: RecommendationEntitlement) -> Dict[str, Any]:
    return {
        "feature": row.feature, "period": row.period_key, "included": row.included,
        "used": row.used, "remaining": max(0, row.included - row.used), "source": row.source,
    }


# --- Occasions --------------------------------------------------------------


async def create_occasion(session: AsyncSession, account_id: uuid.UUID, body: OccasionCreate) -> OccasionRecord:
    record = OccasionRecord(
        account_id=account_id, occasion_key=body.occasion_key,
        title=body.title or get_occasion(body.occasion_key).label,
        event_date=body.event_date, time_of_day=body.time_of_day, location=body.location,
        setting=body.setting, dress_code=body.dress_code, weather=body.weather,
        comfort_preference=body.comfort_preference, optional_budget=body.optional_budget,
        currency=body.currency.upper(), preparation_time=body.preparation_time, notes=body.notes,
    )
    session.add(record)
    await session.flush()
    return record


async def owned_occasion(session: AsyncSession, account_id: uuid.UUID, occasion_id: uuid.UUID) -> OccasionRecord:
    record = (await session.execute(
        select(OccasionRecord).where(OccasionRecord.id == occasion_id, OccasionRecord.account_id == account_id)
    )).scalar_one_or_none()
    if record is None:
        raise NotFoundError("We could not find that occasion.")
    return record


async def update_occasion(session: AsyncSession, record: OccasionRecord, body: OccasionPatch) -> OccasionRecord:
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(record, key, value)
    record.version += 1
    record.updated_at = utcnow()
    await session.flush()
    return record


async def list_occasions(session: AsyncSession, account_id: uuid.UUID, *, include_archived: bool = False) -> List[OccasionRecord]:
    stmt = select(OccasionRecord).where(OccasionRecord.account_id == account_id)
    if not include_archived:
        stmt = stmt.where(OccasionRecord.status == "active")
    return list((await session.execute(stmt.order_by(OccasionRecord.created_at.desc()))).scalars().all())


def serialize_occasion_record(record: OccasionRecord) -> Dict[str, Any]:
    return {
        "id": str(record.id), "occasion_key": record.occasion_key, "title": record.title,
        "event_date": record.event_date.isoformat() if record.event_date else None,
        "time_of_day": record.time_of_day, "location": record.location, "setting": record.setting,
        "dress_code": record.dress_code, "weather": record.weather,
        "comfort_preference": record.comfort_preference,
        "optional_budget": float(record.optional_budget) if record.optional_budget is not None else None,
        "currency": record.currency, "preparation_time": record.preparation_time,
        "notes": record.notes, "status": record.status, "version": record.version,
        "definition": serialize_occasion(get_occasion(record.occasion_key)),
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


# --- Runs -------------------------------------------------------------------


async def start_run(session: AsyncSession, account_id: uuid.UUID, kind: str, style_request_id: Optional[uuid.UUID] = None) -> RecommendationRun:
    run = RecommendationRun(account_id=account_id, kind=kind, style_request_id=style_request_id, status="running", engine_version=ENGINE_VERSION)
    session.add(run)
    await session.flush()
    return run


async def record_inputs(session: AsyncSession, run: RecommendationRun, rows: Iterable[Dict[str, Any]]) -> None:
    for row in rows:
        session.add(RecommendationInput(run_id=run.id, input_type=row["input_type"], input_key=row["input_key"], value=row["value"], source=row["source"]))
    await session.flush()


async def finish_run(session: AsyncSession, run: RecommendationRun, *, status: str, considered: int = 0, ai_run_id: Optional[uuid.UUID] = None, explanation_source: str = "deterministic", error_code: Optional[str] = None, latency_ms: Optional[int] = None) -> None:
    run.status = status
    run.candidates_considered = considered
    run.explanation_source = explanation_source
    run.error_code = error_code
    run.latency_ms = latency_ms
    run.completed_at = utcnow()
    if ai_run_id is not None:
        from app.domains.ai_gateway.models import AIRun

        # The gateway records runs on its own session; only link a row that
        # really landed, so a failed ledger write cannot break a foreign key.
        if await session.get(AIRun, ai_run_id):
            run.ai_run_id = ai_run_id
    await session.flush()


# --- Looks ------------------------------------------------------------------


async def persist_look(session: AsyncSession, run: RecommendationRun, ranked: RankedLook, rank: int) -> Look:
    candidate = ranked.candidate
    look = Look(
        account_id=run.account_id, run_id=run.id, variant=ranked.variant, rank=rank,
        title=ranked.title, score=ranked.score, confidence=ranked.confidence,
        why_it_works=ranked.why_it_works, weather_note=ranked.weather_note,
        dress_code_note=ranked.dress_code_note, preparation_steps=ranked.preparation_steps,
        missing_information=ranked.missing_information, factor_scores=candidate.factor_scores,
    )
    session.add(look)
    await session.flush()
    await write_look_items(session, look, candidate)
    return look


async def write_look_items(session: AsyncSession, look: Look, candidate: OutfitCandidate) -> None:
    position = 0
    for slot, rows in (
        (SLOT_CLOTHING, candidate.clothing),
        (SLOT_SHOES, [candidate.shoes] if candidate.shoes else []),
        (SLOT_ACCESSORIES, candidate.accessories),
        (SLOT_PERFUME, [candidate.perfume] if candidate.perfume else []),
        (SLOT_HAIR, [candidate.hair] if candidate.hair else []),
        (SLOT_GROOMING, [candidate.grooming] if candidate.grooming else []),
    ):
        for scored in rows:
            session.add(LookItem(
                look_id=look.id, slot=slot, ownership=OWNERSHIP_OWNED,
                inventory_item_id=scored.id, display_name=scored.item.display_name,
                role_note=scored.reasons.get("formality", ""), score=scored.score, position=position,
                estimated_price=Decimal(str(scored.item.purchase_price)) if scored.item.purchase_price is not None else None,
                currency=scored.item.currency,
            ))
            position += 1
    for addition in candidate.optional_additions:
        session.add(LookItem(
            look_id=look.id, slot=addition.slot, ownership=OWNERSHIP_OPTIONAL,
            inventory_item_id=None, display_name=addition.description,
            role_note=addition.reason, score=0.0, position=position,
        ))
        position += 1
    await session.flush()


async def owned_look(session: AsyncSession, account_id: uuid.UUID, look_id: uuid.UUID) -> Look:
    look = (await session.execute(
        select(Look).where(Look.id == look_id, Look.account_id == account_id)
    )).scalar_one_or_none()
    if look is None:
        raise NotFoundError("We could not find that look.")
    return look


async def _resolve_owned_items(session: AsyncSession, account_id: uuid.UUID, item_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, InventoryItem]:
    if not item_ids:
        return {}
    rows = (await session.execute(
        select(InventoryItem).where(
            InventoryItem.id.in_(list(item_ids)),
            InventoryItem.account_id == account_id,
            InventoryItem.status == "active",
        )
    )).scalars().all()
    return {row.id: row for row in rows}


async def serialize_look(session: AsyncSession, look: Look, *, include_history: bool = False) -> Dict[str, Any]:
    rows = list((await session.execute(
        select(LookItem).where(LookItem.look_id == look.id).order_by(LookItem.position)
    )).scalars().all())
    resolved = await _resolve_owned_items(session, look.account_id, [row.inventory_item_id for row in rows if row.inventory_item_id])

    slots: Dict[str, List[Dict[str, Any]]] = {slot: [] for slot in SLOT_ORDER}
    owned_items: List[Dict[str, Any]] = []
    optional_items: List[Dict[str, Any]] = []
    unavailable: List[str] = []

    for row in rows:
        if row.ownership == OWNERSHIP_OWNED:
            item = resolved.get(row.inventory_item_id) if row.inventory_item_id else None
            if item is None:
                # It was owned when the look was built and is not any more.
                unavailable.append(row.display_name)
                continue
            payload = {
                "id": str(row.id), "slot": row.slot, "ownership": OWNERSHIP_OWNED,
                "inventory_item_id": str(item.id), "display_name": item.display_name,
                "brand": item.brand, "category": item.category, "subcategory": item.subcategory,
                "role_note": row.role_note, "score": row.score,
                "owned": True, "label": "In your inventory",
            }
            owned_items.append(payload)
        else:
            payload = {
                "id": str(row.id), "slot": row.slot, "ownership": OWNERSHIP_OPTIONAL,
                "inventory_item_id": None, "display_name": row.display_name,
                "brand": None, "category": None, "subcategory": None,
                "role_note": row.role_note, "score": row.score,
                "owned": False, "label": "Optional addition — you do not own this",
            }
            optional_items.append(payload)
        slots[row.slot].append(payload)

    body: Dict[str, Any] = {
        "id": str(look.id), "run_id": str(look.run_id), "variant": look.variant,
        "title": look.title, "rank": look.rank, "score": look.score, "confidence": look.confidence,
        "why_it_works": look.why_it_works, "weather_note": look.weather_note,
        "dress_code_note": look.dress_code_note, "preparation_steps": look.preparation_steps,
        "missing_information": look.missing_information, "factor_scores": look.factor_scores,
        "explanation_source": look.explanation_source, "status": look.status, "saved": look.saved,
        "version": look.version, "slots": slots,
        "owned_items": owned_items, "optional_additions": optional_items,
        "owned_item_count": len(owned_items), "optional_addition_count": len(optional_items),
        "unavailable_items": unavailable,
        "share": shareable(look, owned_items, optional_items),
        "created_at": look.created_at.isoformat() if look.created_at else None,
    }
    if include_history:
        history = (await session.execute(
            select(LookAdjustment).where(LookAdjustment.look_id == look.id).order_by(LookAdjustment.created_at.desc()).limit(50)
        )).scalars().all()
        body["adjustments"] = [{
            "id": str(row.id), "adjustment_type": row.adjustment_type, "slot": row.slot,
            "from_item_id": str(row.from_item_id) if row.from_item_id else None,
            "to_item_id": str(row.to_item_id) if row.to_item_id else None,
            "reason": row.reason, "actor": row.actor,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in history]
        feedback = (await session.execute(
            select(LookFeedback).where(LookFeedback.look_id == look.id, LookFeedback.account_id == look.account_id)
        )).scalar_one_or_none()
        body["feedback"] = None if feedback is None else {
            "rating": feedback.rating, "reason": feedback.reason, "note": feedback.note,
            "worn_on": feedback.worn_on.isoformat() if feedback.worn_on else None,
        }
    return body


def shareable(look: Look, owned_items: Sequence[Dict[str, Any]], optional_items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """A version of the look that is safe to send to someone else.

    Names of garments only. No account id, no inventory ids, no profile, no
    photo, nothing about the person.
    """
    lines = [f"{look.title} — {len(owned_items)} pieces from my own wardrobe"]
    lines.extend(f"• {row['display_name']}" for row in owned_items)
    if optional_items:
        lines.append("Optional additions (not owned):")
        lines.extend(f"• {row['display_name']}" for row in optional_items)
    return {
        "title": look.title,
        "text": "\n".join(lines),
        "includes_personal_data": False,
        "note": "Shares the names of the pieces only.",
    }


async def record_adjustment(session: AsyncSession, look: Look, *, adjustment_type: str, slot: Optional[str] = None, from_item_id: Optional[uuid.UUID] = None, to_item_id: Optional[uuid.UUID] = None, reason: Optional[str] = None) -> None:
    session.add(LookAdjustment(
        look_id=look.id, account_id=look.account_id, adjustment_type=adjustment_type,
        slot=slot, from_item_id=from_item_id, to_item_id=to_item_id, reason=reason, actor="user",
    ))


async def save_feedback(session: AsyncSession, look: Look, *, rating: str, reason: Optional[str], note: Optional[str], worn_on: Optional[date]) -> LookFeedback:
    row = (await session.execute(
        select(LookFeedback).where(LookFeedback.look_id == look.id, LookFeedback.account_id == look.account_id)
    )).scalar_one_or_none()
    if row is None:
        row = LookFeedback(look_id=look.id, account_id=look.account_id, rating=rating, reason=reason, note=note, worn_on=worn_on)
        session.add(row)
    else:
        row.rating, row.reason, row.note, row.worn_on = rating, reason, note, worn_on
    if rating in ("loved", "saved", "worn"):
        look.saved = True
    await session.flush()
    return row


# --- Compatibility cache ----------------------------------------------------


async def cache_edges(session: AsyncSession, account_id: uuid.UUID, edges: Iterable[tuple[uuid.UUID, uuid.UUID, float, Dict[str, Any]]]) -> None:
    """Store computed pair scores, once per pair.

    The same pair legitimately turns up in more than one look of the same run,
    so the batch is deduplicated before anything is looked up — relying on the
    unique constraint to catch it would abort the whole transaction.
    """
    wanted: Dict[tuple[uuid.UUID, uuid.UUID], tuple[float, Dict[str, Any]]] = {}
    for first, second, score, basis in edges:
        a, b = sorted([first, second], key=str)
        wanted.setdefault((a, b), (score, basis))
    if not wanted:
        return
    already = set((await session.execute(
        select(CompatibilityEdge.item_a_id, CompatibilityEdge.item_b_id).where(
            CompatibilityEdge.account_id == account_id,
            CompatibilityEdge.metric_version == ENGINE_VERSION,
        )
    )).all())
    for (a, b), (score, basis) in wanted.items():
        if (a, b) not in already:
            session.add(CompatibilityEdge(account_id=account_id, item_a_id=a, item_b_id=b, score=score, basis=basis, metric_version=ENGINE_VERSION))
    await session.flush()


# --- Shopping ---------------------------------------------------------------


async def owned_candidate(session: AsyncSession, account_id: uuid.UUID, candidate_id: uuid.UUID) -> ShoppingCandidate:
    row = (await session.execute(
        select(ShoppingCandidate).where(ShoppingCandidate.id == candidate_id, ShoppingCandidate.account_id == account_id)
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError("We could not find that shopping item.")
    return row


async def owned_evaluation(session: AsyncSession, account_id: uuid.UUID, evaluation_id: uuid.UUID) -> PurchaseEvaluation:
    row = (await session.execute(
        select(PurchaseEvaluation).where(PurchaseEvaluation.id == evaluation_id, PurchaseEvaluation.account_id == account_id)
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError("We could not find that evaluation.")
    return row


def serialize_candidate(row: ShoppingCandidate) -> Dict[str, Any]:
    return {
        "id": str(row.id), "source": row.source, "category": row.category, "subcategory": row.subcategory,
        "display_name": row.display_name, "brand": row.brand, "colour": row.colour, "size": row.size,
        "fabric": row.fabric, "fit": row.fit, "formality": row.formality,
        "occasion_tags": row.occasion_tags, "season_tags": row.season_tags,
        "price": float(row.price) if row.price is not None else None, "currency": row.currency,
        "product_url": row.product_url, "extraction_confidence": row.extraction_confidence,
        "uncertain_fields": row.uncertain_fields, "verification_state": row.verification_state,
        "media_asset_id": str(row.media_asset_id) if row.media_asset_id else None,
        "in_inventory": False,
        "note": "This is something you are considering. It has not been added to your inventory.",
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def serialize_evaluation(session: AsyncSession, evaluation: PurchaseEvaluation) -> Dict[str, Any]:
    candidate = await session.get(ShoppingCandidate, evaluation.candidate_id)
    factors = (await session.execute(
        select(PurchaseEvaluationFactor).where(PurchaseEvaluationFactor.evaluation_id == evaluation.id).order_by(PurchaseEvaluationFactor.position)
    )).scalars().all()
    referenced = [uuid.UUID(value) for value in (evaluation.duplicate_item_ids + evaluation.alternative_item_ids)]
    resolved = await _resolve_owned_items(session, evaluation.account_id, referenced)

    def items(ids: Sequence[str]) -> List[Dict[str, Any]]:
        out = []
        for value in ids:
            item = resolved.get(uuid.UUID(value))
            if item is not None:
                out.append({"inventory_item_id": str(item.id), "display_name": item.display_name, "brand": item.brand, "category": item.category})
        return out

    decision = (await session.execute(
        select(PurchaseDecision).where(PurchaseDecision.evaluation_id == evaluation.id, PurchaseDecision.account_id == evaluation.account_id)
    )).scalar_one_or_none()

    return {
        "id": str(evaluation.id), "run_id": str(evaluation.run_id),
        "candidate": serialize_candidate(candidate) if candidate else None,
        "verdict": evaluation.verdict, "headline": evaluation.headline,
        "appearance_roi": {
            "score": evaluation.roi_score, "version": evaluation.roi_version,
            "formula": "Weighted sum of the factors below, divided by the total weight of the factors that could be scored.",
            "thresholds": {"buy": 0.65, "wait": 0.45},
            "factors": [{
                "key": row.factor_key, "label": row.label, "value": row.raw_value,
                "weight": row.weight, "contribution": row.contribution, "explanation": row.explanation,
            } for row in factors],
        },
        "confidence": evaluation.confidence,
        "new_combinations": evaluation.new_combinations,
        "summary": evaluation.summary,
        "explanation_source": evaluation.explanation_source,
        "similar_owned_products": items(evaluation.duplicate_item_ids),
        "existing_alternatives": items(evaluation.alternative_item_ids),
        "fit_risks": evaluation.fit_risks, "colour_risks": evaluation.colour_risks,
        "climate_notes": evaluation.climate_notes,
        "missing_information": evaluation.missing_information,
        "decision": None if decision is None else {
            "decision": decision.decision, "note": decision.note,
            "followed_recommendation": decision.followed_recommendation,
            "created_at": decision.created_at.isoformat() if decision.created_at else None,
        },
        "created_at": evaluation.created_at.isoformat() if evaluation.created_at else None,
    }


VERDICT_TO_DECISION = {"buy": "bought", "wait": "waiting", "skip": "skipped"}


async def save_decision(session: AsyncSession, evaluation: PurchaseEvaluation, decision: str, note: Optional[str]) -> PurchaseDecision:
    row = (await session.execute(
        select(PurchaseDecision).where(PurchaseDecision.evaluation_id == evaluation.id, PurchaseDecision.account_id == evaluation.account_id)
    )).scalar_one_or_none()
    followed = VERDICT_TO_DECISION.get(evaluation.verdict) == decision
    if row is None:
        row = PurchaseDecision(evaluation_id=evaluation.id, account_id=evaluation.account_id, decision=decision, note=note, followed_recommendation=followed)
        session.add(row)
    else:
        row.decision, row.note, row.followed_recommendation = decision, note, followed
    await session.flush()
    return row


async def replayed_evaluation(
    session: AsyncSession, account_id: uuid.UUID, client_mutation_id: Optional[str]
) -> Optional[PurchaseEvaluation]:
    """The evaluation a previous send of this request already produced.

    The client sends the same ``client_mutation_id`` when it retries, so a
    dropped response must return the original verdict rather than score the
    item again — scoring twice would charge the allowance twice and could hand
    back a different answer for the same question.
    """
    if not client_mutation_id:
        return None
    candidate = (await session.execute(
        select(ShoppingCandidate).where(
            ShoppingCandidate.account_id == account_id,
            ShoppingCandidate.client_mutation_id == client_mutation_id,
        )
    )).scalar_one_or_none()
    if candidate is None:
        return None
    return (await session.execute(
        select(PurchaseEvaluation)
        .where(
            PurchaseEvaluation.account_id == account_id,
            PurchaseEvaluation.candidate_id == candidate.id,
        )
        .order_by(PurchaseEvaluation.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()


async def create_style_request(session: AsyncSession, account_id: uuid.UUID, occasion: OccasionRecord, preferred_item_ids: Sequence[uuid.UUID], client_mutation_id: Optional[str]) -> StyleRequest:
    if client_mutation_id:
        replay = (await session.execute(
            select(StyleRequest).where(StyleRequest.account_id == account_id, StyleRequest.client_mutation_id == client_mutation_id)
        )).scalar_one_or_none()
        if replay is not None:
            return replay
    request = StyleRequest(
        account_id=account_id, occasion_id=occasion.id,
        preferred_item_ids=[str(value) for value in preferred_item_ids],
        answers=serialize_occasion_record(occasion), status="pending",
        client_mutation_id=client_mutation_id,
    )
    session.add(request)
    await session.flush()
    return request
