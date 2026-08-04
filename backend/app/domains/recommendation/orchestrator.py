"""The recommendation orchestrator.

The ten stages, in order, and where each one lives:

===  ==============================  ====================================
 1   Context gathering               ``context.gather``
 2   Constraint resolution           ``context.resolve_constraints``
 3   Deterministic candidate filter  ``candidates.filter_candidates``
 4   Compatibility scoring           ``compatibility`` + ``candidates``
 5   Outfit candidate generation     ``candidates.build_candidates``
 6   Ranking into three options      ``ranking.rank``
 7   LLM explanation                 ``explanation.explain_looks``
 8   Schema validation               the AI gateway, then ``explanation``
 9   User feedback                   ``service.save_feedback``
10   Recommendation memory           ``recommendation_inputs`` + feedback
===  ==============================  ====================================

Purchase-ROI calculation (the sixth item in the same list for the shopping
workflow) lives in ``roi`` and is reached from :func:`evaluate_purchase`.

Stages 1-6 never call a model. Stage 7 can fail without affecting stages 1-6,
which is what makes "no recommendation depends on a fabricated fallback" true by
construction rather than by promise.
"""
from __future__ import annotations

import logging
import time
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.models import InventoryItem
from app.domains.media import service as media_service
from app.domains.media.models import MEDIA_PURPOSE_INVENTORY
from app.domains.recommendation import candidates as candidate_stage
from app.domains.recommendation import compatibility as compat
from app.domains.recommendation import context as context_stage
from app.domains.recommendation import explanation as explanation_stage
from app.domains.recommendation import ranking as ranking_stage
from app.domains.recommendation import roi as roi_stage
from app.domains.recommendation import service
from app.domains.recommendation.candidates import OutfitCandidate, ScoredItem
from app.domains.recommendation.context import OwnedItem, StyleContext
from app.domains.recommendation.models import (
    OWNERSHIP_OWNED, Look, LookItem, OccasionRecord, PurchaseEvaluation,
    PurchaseEvaluationFactor, RecommendationRun, ShoppingCandidate, StyleRequest,
)
from app.domains.recommendation.occasions import SLOT_CATEGORY, get_occasion
from app.domains.recommendation.schemas import LookRevise, LookSwapItem, ShoppingEvaluateRequest
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError

logger = logging.getLogger(__name__)

NOT_ENOUGH_INVENTORY = "not_enough_inventory"

EMPTY_GUIDANCE = [
    "Add a few things you actually wear — three or four tops, two bottoms and a pair of shoes is enough to start.",
    "Confirm anything sitting in your inventory as a draft. Drafts are not used until you confirm them.",
    "You can add items by photo or by typing them in.",
]


# --- Style me for an occasion -----------------------------------------------


async def style_for_occasion(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    occasion_record: OccasionRecord,
    preferred_item_ids: Sequence[uuid.UUID] = (),
    client_mutation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the full styling pipeline and persist everything it produced."""
    started = time.perf_counter()

    style_request = await service.create_style_request(session, account_id, occasion_record, preferred_item_ids, client_mutation_id)
    run = await service.start_run(session, account_id, kind="style_occasion", style_request_id=style_request.id)

    # Stages 1 and 2.
    context = await context_stage.gather(session, account_id=account_id, occasion_record=occasion_record, preferred_item_ids=preferred_item_ids)
    await service.record_inputs(session, run, context_stage.input_rows(context))

    # Stages 3 to 6.
    buckets = candidate_stage.filter_candidates(context)
    pool = candidate_stage.build_candidates(context, buckets)
    ranked = ranking_stage.rank(context, pool)

    if not ranked:
        await service.finish_run(session, run, status="empty", considered=len(pool), latency_ms=int((time.perf_counter() - started) * 1000))
        style_request.status = "empty"
        return {
            "status": NOT_ENOUGH_INVENTORY,
            "run_id": str(run.id),
            "occasion": service.serialize_occasion_record(occasion_record),
            "looks": [],
            "message": (
                "There is not enough confirmed inventory to build a look for this yet. "
                "We will not invent clothes you do not own."
            ),
            "guidance": EMPTY_GUIDANCE,
            "confirmed_item_count": len(context.owned),
            "unconfirmed_draft_count": context.draft_count,
            "missing_information": context.missing_information,
            "entitlement": service.serialize_entitlement(await service.entitlement_for(session, account_id, service.FEATURE_STYLE)),
        }

    # Only charge for a run that produced something.
    entitlement = await service.consume_entitlement(session, account_id, service.FEATURE_STYLE)

    # Stages 7 and 8. Failure here is survivable by design.
    narratives, ai_run_id, source = await explanation_stage.explain_looks(ranked, context, account_id_str=account_id_str)

    looks: List[Look] = []
    for index, item in enumerate(ranked, start=1):
        narrative = narratives.get(item.variant)
        if narrative is not None:
            item.why_it_works = narrative.why_it_works
            item.weather_note = narrative.weather_note or item.weather_note
            item.dress_code_note = narrative.dress_code_note or item.dress_code_note
            if narrative.preparation_steps:
                item.preparation_steps = narrative.preparation_steps
        look = await service.persist_look(session, run, item, index)
        look.explanation_source = explanation_stage.SOURCE_AI if narrative is not None else explanation_stage.SOURCE_DETERMINISTIC
        looks.append(look)

    await _cache_look_edges(session, account_id, ranked)
    await service.finish_run(
        session, run, status="succeeded", considered=len(pool), ai_run_id=ai_run_id,
        explanation_source=source, latency_ms=int((time.perf_counter() - started) * 1000),
    )
    style_request.status = "completed"
    await session.flush()

    return {
        "status": "ready",
        "run_id": str(run.id),
        "style_request_id": str(style_request.id),
        "occasion": service.serialize_occasion_record(occasion_record),
        "looks": [await service.serialize_look(session, look) for look in looks],
        "explanation_source": source,
        "engine_version": run.engine_version,
        "candidates_considered": len(pool),
        "confirmed_item_count": len(context.owned),
        "unconfirmed_draft_count": context.draft_count,
        "missing_information": context.missing_information,
        "entitlement": service.serialize_entitlement(entitlement),
        "disclaimer": "Style guidance based on what you own and told us. Not medical or diagnostic advice.",
    }


async def _cache_look_edges(session: AsyncSession, account_id: uuid.UUID, ranked: Sequence[ranking_stage.RankedLook]) -> None:
    edges = []
    for look in ranked:
        rows = look.candidate.owned_items()
        for index, first in enumerate(rows):
            for second in rows[index + 1:]:
                score, reason = compat.colour_compatibility(first.item.colour, second.item.colour)
                edges.append((first.id, second.id, score, {"basis": "colour", "explanation": reason}))
    await service.cache_edges(session, account_id, edges)


# --- Rebuilding context for an existing look --------------------------------


async def _occasion_for_look(session: AsyncSession, look: Look) -> OccasionRecord:
    """Walk look -> run -> style_request -> occasion, checking ownership at the end."""
    run = await session.get(RecommendationRun, look.run_id)
    if run is None or run.style_request_id is None:
        raise NotFoundError("We could not find the occasion this look was built for.")
    request = await session.get(StyleRequest, run.style_request_id)
    if request is None:
        raise NotFoundError("We could not find the occasion this look was built for.")
    record = await session.get(OccasionRecord, request.occasion_id)
    if record is None or record.account_id != look.account_id:
        raise NotFoundError("We could not find the occasion this look was built for.")
    return record


async def _context_for_look(session: AsyncSession, look: Look) -> StyleContext:
    record = await _occasion_for_look(session, look)
    return await context_stage.gather(session, account_id=look.account_id, occasion_record=record)


async def _look_item_ids(session: AsyncSession, look: Look) -> List[uuid.UUID]:
    rows = (await session.execute(
        select(LookItem.inventory_item_id).where(LookItem.look_id == look.id, LookItem.inventory_item_id.is_not(None))
    )).scalars().all()
    return [row for row in rows if row is not None]


async def _replace_look_items(session: AsyncSession, look: Look, candidate: OutfitCandidate) -> None:
    rows = (await session.execute(select(LookItem).where(LookItem.look_id == look.id))).scalars().all()
    for row in rows:
        await session.delete(row)
    await session.flush()
    await service.write_look_items(session, look, candidate)


# --- Revise -----------------------------------------------------------------

REVISION_HINTS = {
    "too_formal": -1,
    "too_casual": 1,
    "too_warm": 0,
    "too_cold": 0,
    "not_my_style": 0,
    "want_bolder": 0,
    "want_simpler": 0,
}


async def revise_look(
    session: AsyncSession, *, look: Look, body: LookRevise, account_id_str: str
) -> Dict[str, Any]:
    """Rebuild one look, avoiding what the user pushed back on.

    A revision does not consume an allowance. It is a correction to something
    already produced, and charging for it would penalise the user for our first
    answer not landing.
    """
    context = await _context_for_look(session, look)
    if body.reason in REVISION_HINTS:
        shift = REVISION_HINTS[body.reason]
        context.target_formality = max(1, min(5, context.target_formality + shift))
    if body.reason == "too_warm" and not context.weather:
        context.weather = "hot"
    if body.reason == "too_cold" and not context.weather:
        context.weather = "cold"

    avoid = set(body.avoid_item_ids) | set(await _look_item_ids(session, look))
    buckets = candidate_stage.filter_candidates(context)
    pool = candidate_stage.build_candidates(context, buckets, exclude_ids=list(avoid))
    if not pool or not any(row.owned_items() for row in pool):
        # Falling back to the whole wardrobe is better than refusing, but the
        # user is told that is what happened.
        pool = candidate_stage.build_candidates(context, buckets, exclude_ids=list(body.avoid_item_ids))
        widened = True
    else:
        widened = False

    ranked = ranking_stage.rank(context, pool)
    if not ranked:
        raise ValidationFailedError(
            "There is not enough other confirmed inventory to build a different look for this occasion.",
            field="avoid_item_ids",
        )

    replacement = ranked[0]
    await service.record_adjustment(session, look, adjustment_type="revise", reason=body.reason or body.note)
    look.why_it_works = replacement.why_it_works
    look.weather_note = replacement.weather_note
    look.dress_code_note = replacement.dress_code_note
    look.preparation_steps = replacement.preparation_steps
    look.missing_information = replacement.missing_information
    look.factor_scores = replacement.candidate.factor_scores
    look.score = replacement.score
    look.confidence = replacement.confidence
    look.explanation_source = explanation_stage.SOURCE_DETERMINISTIC
    look.version += 1
    await _replace_look_items(session, look, replacement.candidate)

    narratives, _, source = await explanation_stage.explain_looks([replacement], context, account_id_str=account_id_str)
    narrative = narratives.get(replacement.variant)
    if narrative is not None:
        look.why_it_works = narrative.why_it_works
        look.weather_note = narrative.weather_note or look.weather_note
        look.dress_code_note = narrative.dress_code_note or look.dress_code_note
        if narrative.preparation_steps:
            look.preparation_steps = narrative.preparation_steps
        look.explanation_source = source
    await session.flush()

    body_out = await service.serialize_look(session, look, include_history=True)
    body_out["revision"] = {
        "widened_search": widened,
        "note": (
            "We could not build a different look without reusing some of the same pieces, so those were allowed back in."
            if widened else "Built from different pieces to the ones you asked to avoid."
        ),
    }
    return body_out


# --- Swap one item ----------------------------------------------------------


async def swap_item(session: AsyncSession, *, look: Look, body: LookSwapItem) -> Dict[str, Any]:
    """Replace one slot with a specific owned item the user chose."""
    context = await _context_for_look(session, look)
    category = SLOT_CATEGORY[body.slot]

    rows = list((await session.execute(
        select(LookItem).where(LookItem.look_id == look.id, LookItem.slot == body.slot).order_by(LookItem.position)
    )).scalars().all())
    target = None
    if body.from_item_id is not None:
        target = next((row for row in rows if row.inventory_item_id == body.from_item_id), None)
        if target is None:
            raise ValidationFailedError("That item is not part of this look.", field="from_item_id")
    else:
        target = next((row for row in rows if row.ownership == OWNERSHIP_OWNED), None)

    if body.to_item_id is None:
        if target is None:
            raise ValidationFailedError("There is nothing in that slot to remove.", field="slot")
        await service.record_adjustment(session, look, adjustment_type="swap_item", slot=body.slot, from_item_id=target.inventory_item_id, reason=body.note)
        await session.delete(target)
        look.version += 1
        await session.flush()
        return await service.serialize_look(session, look, include_history=True)

    # Ownership is verified here rather than trusted from the request body.
    replacement = (await session.execute(
        select(InventoryItem).where(
            InventoryItem.id == body.to_item_id,
            InventoryItem.account_id == look.account_id,
            InventoryItem.status == "active",
        )
    )).scalar_one_or_none()
    if replacement is None:
        raise NotFoundError("We could not find that item in your inventory.")
    if replacement.verification_state != "confirmed":
        raise ValidationFailedError(
            "That item is still an unconfirmed draft. Confirm it in your inventory first, then it can be used in a look.",
            field="to_item_id",
        )
    if replacement.category != category:
        raise ValidationFailedError(
            f"The {body.slot} slot takes {category} items, and that one is in {replacement.category}.",
            field="to_item_id",
        )

    owned = next((item for item in context.owned if item.id == replacement.id), None)
    scored = candidate_stage.score_item(owned, context) if owned else None

    if target is None:
        position = max((row.position for row in rows), default=0) + 1
        session.add(LookItem(
            look_id=look.id, slot=body.slot, ownership=OWNERSHIP_OWNED,
            inventory_item_id=replacement.id, display_name=replacement.display_name,
            role_note=scored.reasons.get("formality", "") if scored else "", score=scored.score if scored else 0.0,
            position=position, currency=replacement.currency,
        ))
        from_id = None
    else:
        from_id = target.inventory_item_id
        target.ownership = OWNERSHIP_OWNED
        target.inventory_item_id = replacement.id
        target.display_name = replacement.display_name
        target.role_note = scored.reasons.get("formality", "") if scored else ""
        target.score = scored.score if scored else 0.0
        target.currency = replacement.currency

    await service.record_adjustment(session, look, adjustment_type="swap_item", slot=body.slot, from_item_id=from_id, to_item_id=replacement.id, reason=body.note)
    look.version += 1
    look.explanation_source = explanation_stage.SOURCE_DETERMINISTIC
    await session.flush()
    await _rescore_look(session, look, context)
    return await service.serialize_look(session, look, include_history=True)


async def _rescore_look(session: AsyncSession, look: Look, context: StyleContext) -> None:
    """Recompute the look's own numbers after the user changed a piece."""
    rows = (await session.execute(
        select(LookItem).where(LookItem.look_id == look.id).order_by(LookItem.position)
    )).scalars().all()
    owned_by_id = {item.id: item for item in context.owned}
    colours: List[Any] = []
    scores: List[float] = []
    for row in rows:
        if row.ownership != OWNERSHIP_OWNED or row.inventory_item_id is None:
            continue
        item = owned_by_id.get(row.inventory_item_id)
        if item is None:
            continue
        colours.append(item.colour)
        scores.append(candidate_stage.score_item(item, context).score)
    cohesion_score, reasons = compat.cohesion(colours)
    item_score = round(sum(scores) / len(scores), 4) if scores else 0.0
    weights = candidate_stage.LOOK_WEIGHTS
    completeness = float(look.factor_scores.get("completeness", 1.0)) if look.factor_scores else 1.0
    look.factor_scores = {
        **(look.factor_scores or {}),
        "items": item_score, "cohesion": cohesion_score, "completeness": completeness,
        "weights": weights, "recomputed_after_swap": True,
    }
    look.score = round(item_score * weights["items"] + cohesion_score * weights["cohesion"] + completeness * weights["completeness"], 4)
    look.confidence = round(max(0.2, min(0.95, look.score - 0.05 * min(len(look.missing_information or []), 4))), 4)
    if reasons:
        look.why_it_works = (
            f"{look.why_it_works} You swapped a piece in, so this was rescored: {reasons[0]}"
        )
    await session.flush()


# --- Should I buy this? -----------------------------------------------------


async def evaluate_purchase(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    body: ShoppingEvaluateRequest,
) -> Dict[str, Any]:
    """Read the item if needed, score it, and return Buy, Wait or Skip."""
    started = time.perf_counter()

    # A retry must return the original verdict, not score the item again.
    replay = await service.replayed_evaluation(session, account_id, body.client_mutation_id)
    if replay is not None:
        payload = await service.serialize_evaluation(session, replay)
        payload["entitlement"] = service.serialize_entitlement(
            await service.entitlement_for(session, account_id, service.FEATURE_SHOPPING)
        )
        payload["replayed"] = True
        return payload

    run = await service.start_run(session, account_id, kind="shopping_evaluation")

    candidate_row, extraction_notes = await _resolve_shopping_candidate(session, account_id=account_id, account_id_str=account_id_str, body=body, run_id=run.id)

    owned, drafts = await context_stage.confirmed_inventory(session, account_id)
    attributes = await context_stage.confirmed_attributes(session, account_id)

    candidate = roi_stage.Candidate(
        category=candidate_row.category, display_name=candidate_row.display_name, brand=candidate_row.brand,
        subcategory=candidate_row.subcategory, colour=candidate_row.colour, size=candidate_row.size,
        fabric=candidate_row.fabric, fit=candidate_row.fit, formality=candidate_row.formality,
        occasion_tags=candidate_row.occasion_tags, season_tags=candidate_row.season_tags,
        price=candidate_row.price, currency=candidate_row.currency,
    )

    result = roi_stage.evaluate(
        candidate, owned,
        occasion_key=body.occasion_key,
        favourite_colours=attributes.get("favourite_colours") or [],
        disliked_colours=attributes.get("disliked_colours") or [],
        fit_preferences={key: attributes.get(key) for key in context_stage.FIT_ATTRIBUTES},
        climate=attributes.get("climate"),
    )
    if drafts:
        result.missing_information.append(
            f"{drafts} inventory draft{'s were' if drafts > 1 else ' was'} not counted, because drafts are not confirmed."
        )

    await service.record_inputs(session, run, [
        {"input_type": "shopping", "input_key": "candidate_id", "value": str(candidate_row.id), "source": candidate_row.source},
        {"input_type": "shopping", "input_key": "occasion_key", "value": body.occasion_key, "source": "user_declared"},
        {"input_type": "inventory", "input_key": "confirmed_item_count", "value": len(owned), "source": "inventory"},
        {"input_type": "roi", "input_key": "score", "value": result.score, "source": "derived"},
        {"input_type": "roi", "input_key": "verdict", "value": result.verdict, "source": "derived"},
        *[{"input_type": "profile", "input_key": key, "value": value, "source": "profile_confirmed"} for key, value in sorted(attributes.items())],
    ])

    entitlement = await service.consume_entitlement(session, account_id, service.FEATURE_SHOPPING)

    summary = roi_stage.deterministic_summary(result, candidate)
    ai_summary, ai_run_id, source = await explanation_stage.explain_purchase(result, candidate, account_id_str=account_id_str)
    if ai_summary:
        summary = ai_summary

    evaluation = PurchaseEvaluation(
        account_id=account_id, candidate_id=candidate_row.id, run_id=run.id,
        verdict=result.verdict, roi_score=result.score, roi_version=result.version,
        confidence=result.confidence, new_combinations=result.new_combinations,
        summary=summary, headline=roi_stage.VERDICT_HEADLINES[result.verdict],
        duplicate_item_ids=result.duplicate_item_ids, alternative_item_ids=result.alternative_item_ids,
        fit_risks=result.fit_risks, colour_risks=result.colour_risks,
        climate_notes=result.climate_notes, missing_information=result.missing_information + extraction_notes,
        explanation_source=source,
    )
    session.add(evaluation)
    await session.flush()

    for position, factor in enumerate(result.factors):
        session.add(PurchaseEvaluationFactor(
            evaluation_id=evaluation.id, factor_key=factor.key, label=factor.label,
            raw_value=factor.raw_value, weight=factor.weight, contribution=factor.contribution,
            explanation=factor.explanation, position=position,
        ))

    await service.finish_run(
        session, run, status="succeeded", considered=len(owned), ai_run_id=ai_run_id,
        explanation_source=source, latency_ms=int((time.perf_counter() - started) * 1000),
    )
    await session.flush()

    payload = await service.serialize_evaluation(session, evaluation)
    payload["entitlement"] = service.serialize_entitlement(entitlement)
    payload["disclaimer"] = "A transparent estimate from what you own and told us. Not financial advice."
    return payload


async def _resolve_shopping_candidate(
    session: AsyncSession, *, account_id: uuid.UUID, account_id_str: str, body: ShoppingEvaluateRequest, run_id: uuid.UUID
) -> tuple[ShoppingCandidate, List[str]]:
    """Build the candidate row, reading a screenshot when one was sent."""
    notes: List[str] = []
    if body.media_asset_id is not None:
        # Ownership of the image is checked against the media domain.
        asset = await media_service.get_owned_asset(session, account_id=account_id, asset_id=body.media_asset_id)
        if asset.purpose != MEDIA_PURPOSE_INVENTORY:
            raise ValidationFailedError("Only inventory photos can be used for a shopping check.", field="media_asset_id")
        data = await media_service.read_bytes(asset)
        extracted, ai_run_id, model, prompt_version, schema_version = await explanation_stage.extract_shopping_item(data, account_id_str=account_id_str)
        from app.domains.ai_gateway.models import AIRun

        linked = ai_run_id if await session.get(AIRun, ai_run_id) else None
        row = ShoppingCandidate(
            account_id=account_id, source=body.source if body.source != "manual" else "screenshot",
            media_asset_id=body.media_asset_id, ai_run_id=linked,
            category=extracted.category, subcategory=extracted.subcategory,
            display_name=extracted.display_name, brand=extracted.brand, colour=extracted.colour,
            size=extracted.size, fabric=extracted.fabric, fit=extracted.fit, formality=extracted.formality,
            occasion_tags=list(extracted.occasion_tags), season_tags=list(extracted.season_tags),
            # A price the user typed always beats one read off a picture.
            price=body.price if body.price is not None else extracted.price,
            currency=(body.currency or extracted.currency or "INR").upper(),
            product_url=body.product_url, extraction_confidence=extracted.confidence,
            client_mutation_id=body.client_mutation_id,
            uncertain_fields=list(extracted.uncertain_fields), verification_state="draft",
            model_version=model, prompt_version=prompt_version, schema_version=schema_version,
        )
        notes.append(f"Read from your screenshot: {extracted.photo_quality_notes}")
        if extracted.uncertain_fields:
            notes.append("Check these before deciding: " + ", ".join(extracted.uncertain_fields) + ".")
        if row.price is None:
            notes.append("No price was visible in the screenshot. Add it for a cost-per-wear estimate.")
    else:
        item = body.item
        assert item is not None  # guaranteed by the request schema
        row = ShoppingCandidate(
            account_id=account_id, source="manual", category=item.category, subcategory=item.subcategory,
            display_name=item.display_name, brand=item.brand, colour=item.colour, size=item.size,
            fabric=item.fabric, fit=item.fit, formality=item.formality,
            occasion_tags=list(item.occasion_tags), season_tags=list(item.season_tags),
            price=body.price if body.price is not None else item.price,
            currency=(body.currency or item.currency).upper(),
            product_url=body.product_url or item.product_url,
            verification_state="user_declared", client_mutation_id=body.client_mutation_id,
        )
    session.add(row)
    await session.flush()
    return row, notes
