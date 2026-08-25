"""Persistence and orchestration for routine and shelf intelligence.

The order of operations is the same everywhere and it is not accidental:

1. Gather confirmed facts.
2. Let the deterministic engine decide.
3. Write what it decided to the database.
4. *Then*, optionally, ask a model to phrase it.

Step 4 can fail, time out, be switched off or be rejected by the safety sweep
without step 3 changing at all. That is why the routine a user sees when the AI
provider is down is the same routine, in plainer words — never a worse one, and
never a different one.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care import cadence as care_cadence
from app.domains.care import decisions as care_decisions
from app.domains.care import guidance as care_guidance
from app.domains.care import home_care, product_preferences
from app.domains.care import routine_plan as care_routine_plan
from app.domains.care import service as care_service
from app.domains.care import simplification as care_simplification
from app.domains.care import snapshot as care_snapshot
from app.domains.inventory import service as inventory_service
from app.domains.inventory.models import InventoryAttribute, InventoryItem
from app.domains.planning import clock
from app.domains.planning import context as planning_context
from app.domains.profile import service as profile_service
from app.domains.routines import adherence, compiler, explanation, parser, perfume, selection, shelf
from app.domains.routines import rules as rules_engine
from app.domains.routines.models import (
    CARE_EXPERIENCE_FEEDBACK_VERSION,
    CareExperienceFeedback,
    ProductExpiryEvent,
    ProductIngredient,
    Routine,
    RoutineAdherence,
    RoutineRecommendationRun,
    RoutineStep,
    UserReportedObservation,
)
from app.domains.routines.ontology import INGREDIENT_BY_KEY, ONTOLOGY_VERSION
from app.domains.routines.rules import ShelfProduct
from app.domains.routines.safety import (
    PROFESSIONAL_BOUNDARY,
    ROUTINE_DISCLAIMER,
    boundary_for,
)
from app.domains.routines.schemas import (
    CareExperienceFeedbackInput,
    IngredientCheckRequest,
    IngredientConfirmRequest,
    ObservationInput,
    RoutineGenerateRequest,
    RoutineStepComplete,
    ShelfAnalyseRequest,
)
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError

CONSISTENCY_WINDOW_DAYS = 14
ROUTINE_ENGINE_VERSION = "care-v3-03.5"


async def _current_care_decisions(
    session: AsyncSession, account_id: uuid.UUID, plan_date: date | None
):
    """Assemble the single account-scoped Care safety truth for a day."""
    day_context = await planning_context.gather(
        session, account_id=account_id, plan_date=plan_date,
    )
    care_context = await care_service.build_care_context(
        session, account_id, day_context=day_context,
    )
    decisions = care_decisions.evaluate_care_context(care_context)
    return day_context, care_context, decisions


async def _current_hair_wash_cadence(
    session: AsyncSession, *, account_id: uuid.UUID, care_context,
) -> care_cadence.HairWashCadenceDecision:
    frequency_fact = care_context.hair_facts.get("care_hair_wash_frequency")
    last_wash_on = await adherence.last_completed_wash_on(
        session, account_id=account_id, through=care_context.plan_date,
    )
    return care_cadence.decide_hair_wash_cadence(
        frequency_fact.value if frequency_fact is not None else None,
        plan_date=care_context.plan_date,
        last_wash_on=last_wash_on,
    )


def _routine_eligibility(decisions: care_decisions.CareDecisionSet) -> compiler.RoutineEligibility:
    allergy_blocked = {
        str(row.item_id)
        for row in decisions.product_decisions
        if any(reason.code == care_decisions.CareDecisionReasonCode.CONFIRMED_ALLERGY_MATCH
               for reason in row.blocking_reasons)
    }
    return compiler.RoutineEligibility(
        eligible_item_ids=frozenset(str(value) for value in decisions.eligible_product_ids),
        allergy_blocked_item_ids=frozenset(allergy_blocked),
    )


def _routine_selection_plan(
    plan: care_routine_plan.CareRoutinePlan,
) -> selection.RoutineSelectionPlan:
    """Adapt Care's plan into the routines-owned compiler contract."""
    fingerprint = care_routine_plan.routine_plan_fingerprint(plan)
    directives = tuple(
        selection.RoutineSlotDirective(
            slot=row.slot,
            category=row.category,
            required=row.required,
            active=row.active,
            selected_item_id=str(row.selected_item_id) if row.selected_item_id else None,
            is_gap=row.is_gap,
        )
        for row in (*plan.skin_slots, *plan.hair_slots)
    )
    return selection.RoutineSelectionPlan(
        plan_version=plan.plan_version,
        plan_fingerprint=fingerprint,
        effort=plan.resolved_effort.value,
        effort_source=plan.effort_source.value,
        directives=directives,
    )


def _care_safety_payload(
    care_context, decisions: care_decisions.CareDecisionSet
) -> dict[str, Any]:
    names = {
        product.item.id: product.item.display_name
        for product in (*care_context.skin_products, *care_context.hair_products)
    }
    blocked = []
    confirmation = []
    for decision in decisions.product_decisions:
        safety_reasons = {
            care_decisions.CareDecisionReasonCode.CONFIRMED_ALLERGY_MATCH,
            care_decisions.CareDecisionReasonCode.PRODUCT_EXPIRED,
        }
        if not decision.eligible and any(
            reason.code in safety_reasons for reason in decision.blocking_reasons
        ):
            blocked.append({
                "inventory_item_id": str(decision.item_id),
                "display_name": names.get(decision.item_id, "Recorded product"),
                "reasons": [reason.code.value for reason in decision.blocking_reasons],
            })
        if any(reason.code == care_decisions.CareDecisionReasonCode.INGREDIENT_CONFIRMATION_NEEDED
               for reason in decision.advisory_reasons):
            confirmation.append({
                "inventory_item_id": str(decision.item_id),
                "display_name": names.get(decision.item_id, "Recorded product"),
            })
    return {
        "context_version": care_context.context_version,
        "decision_version": decisions.decision_version,
        "blocked_products": blocked,
        "ingredient_confirmation_needed": confirmation,
    }


def _care_run_inputs(
    day_context,
    care_context,
    decisions: care_decisions.CareDecisionSet,
    care_plan: care_routine_plan.CareRoutinePlan,
    guidance: care_guidance.CareGuidanceSet,
    hair_wash_cadence: care_cadence.HairWashCadenceDecision,
    home_care_set: home_care.HomeCareSet,
) -> dict[str, Any]:
    def count(code: care_decisions.CareDecisionReasonCode) -> int:
        return sum(
            1 for row in decisions.product_decisions
            if any(reason.code == code for reason in (*row.blocking_reasons, *row.advisory_reasons))
        )

    return {
        "care_context_version": care_context.context_version,
        "care_decision_version": decisions.decision_version,
        "blocked_product_count": len(decisions.blocked_product_ids),
        "expired_product_count": count(care_decisions.CareDecisionReasonCode.PRODUCT_EXPIRED),
        "confirmed_allergy_block_count": count(care_decisions.CareDecisionReasonCode.CONFIRMED_ALLERGY_MATCH),
        "ingredient_confirmation_advisory_count": count(care_decisions.CareDecisionReasonCode.INGREDIENT_CONFIRMATION_NEEDED),
        "care_routine_plan_version": care_plan.plan_version,
        "care_routine_plan_fingerprint": care_routine_plan.routine_plan_fingerprint(care_plan),
        "care_routine_effort": care_plan.resolved_effort.value,
        "care_routine_effort_source": care_plan.effort_source.value,
        "care_active_skin_slot_count": care_plan.active_skin_slot_count,
        "care_active_hair_slot_count": care_plan.active_hair_slot_count,
        "care_skin_gap_count": care_plan.skin_gap_count,
        "care_hair_gap_count": care_plan.hair_gap_count,
        "care_guidance_version": guidance.guidance_version,
        "care_guidance_ruleset_version": guidance.ruleset_version,
        "care_guidance_fingerprint": guidance.fingerprint,
        "care_guidance_item_count": len(guidance.items),
        "hair_wash_cadence_version": hair_wash_cadence.cadence_version,
        "hair_wash_cadence_fingerprint": care_cadence.hair_wash_cadence_fingerprint(hair_wash_cadence),
        "hair_wash_cadence_status": hair_wash_cadence.status.value,
        "home_care_version": home_care_set.home_care_version,
        "home_care_ruleset_version": home_care_set.ruleset_version,
        "home_care_fingerprint": home_care_set.fingerprint,
        "home_care_item_count": len(home_care_set.items),
        "weather_snapshot_id": str(day_context.weather_snapshot_id) if day_context.weather_snapshot_id else None,
        "air_quality_snapshot_id": str(day_context.air_quality_snapshot_id) if day_context.air_quality_snapshot_id else None,
    }


# --- Shelf analysis -----------------------------------------------------------


async def _store_ingredients(
    session: AsyncSession, account_id: uuid.UUID, products: Sequence[ShelfProduct]
) -> int:
    """Write what the parser read, preserving confirmations already made.

    A row the user has confirmed is left alone: re-reading a label must never
    quietly un-confirm something a person deliberately told us was right.
    """
    stored = 0
    for product in products:
        item_id = uuid.UUID(product.id)
        existing = {
            row.ingredient_key: row for row in (await session.execute(
                select(ProductIngredient).where(
                    ProductIngredient.account_id == account_id,
                    ProductIngredient.item_id == item_id,
                )
            )).scalars().all()
        }
        seen: set = set()
        for row in product.ingredients:
            seen.add(row.key)
            current = existing.get(row.key)
            if current is None:
                session.add(ProductIngredient(
                    account_id=account_id, item_id=item_id, ingredient_key=row.key,
                    matched_text=row.matched_text, position=row.position,
                    confidence=row.confidence, source=row.source,
                    needs_confirmation=row.needs_confirmation,
                ))
                stored += 1
                continue
            if current.confirmed_at is not None:
                continue
            current.matched_text = row.matched_text
            current.position = row.position
            current.confidence = row.confidence
            current.source = row.source
            current.needs_confirmation = row.needs_confirmation
            stored += 1
        # An ingredient no longer on the label is removed, unless the user
        # confirmed it themselves — their word beats our reading.
        for key, current in existing.items():
            if key not in seen and current.confirmed_at is None:
                await session.delete(current)
    return stored


async def _store_expiry_events(
    session: AsyncSession, account_id: uuid.UUID, products: Sequence[ShelfProduct], today: date
) -> None:
    """Record what the engine concluded about dates, with the rule behind it."""
    for product in products:
        days = product.days_to_expiry(today)
        if days is None:
            rule_id, status = rules_engine.RULE_NO_EXPIRY, "no_date_recorded"
        elif days < 0:
            rule_id, status = rules_engine.RULE_EXPIRED, "expired"
        elif days <= rules_engine.EXPIRING_SOON_DAYS:
            rule_id, status = rules_engine.RULE_EXPIRING, "expiring_soon"
        else:
            continue
        session.add(ProductExpiryEvent(
            account_id=account_id, item_id=uuid.UUID(product.id), rule_id=rule_id,
            status=status, effective_expiry=product.effective_expiry, days_to_expiry=days,
            detail=f"{product.item.display_name}: {status.replace('_', ' ')}.",
        ))


async def analyse_shelf(
    session: AsyncSession, *, account_id: uuid.UUID, body: ShelfAnalyseRequest
) -> dict[str, Any]:
    """Re-read the shelf, store what the engine concluded, return the summary."""
    context = await shelf.gather(
        session, account_id=account_id, climate=body.climate, today=body.as_of,
    )

    parsed = 0
    for category in body.categories:
        products = shelf.build_fresh(context, category)
        parsed += await _store_ingredients(session, account_id, products)
        await _store_expiry_events(session, account_id, products, context.today)

    await session.flush()
    refreshed = await shelf.gather(
        session, account_id=account_id, climate=body.climate, today=body.as_of,
    )
    result = shelf.summary(refreshed)
    result["analysed_at"] = utcnow().isoformat()
    result["ingredient_rows_written"] = parsed
    result["knowledge_version"] = ONTOLOGY_VERSION
    return result


async def shelf_summary(
    session: AsyncSession, *, account_id: uuid.UUID, climate: str | None = None
) -> dict[str, Any]:
    context = await shelf.gather(session, account_id=account_id, climate=climate)
    result = shelf.summary(context)
    result["knowledge_version"] = ONTOLOGY_VERSION
    return result


async def shelf_expiring(
    session: AsyncSession, *, account_id: uuid.UUID, days: int = 60
) -> dict[str, Any]:
    context = await shelf.gather(session, account_id=account_id)
    return shelf.expiring(context, days)


async def shelf_low_use(session: AsyncSession, *, account_id: uuid.UUID) -> dict[str, Any]:
    context = await shelf.gather(session, account_id=account_id)
    return shelf.low_use(context)


async def shelf_value_to_recover(session: AsyncSession, *, account_id: uuid.UUID) -> dict[str, Any]:
    context = await shelf.gather(session, account_id=account_id)
    items = (await session.execute(
        select(InventoryItem).where(
            InventoryItem.account_id == account_id,
            InventoryItem.status == "active",
            InventoryItem.category.in_(shelf.ROUTINE_CATEGORIES),
        )
    )).scalars().all()
    return shelf.value_to_recover(context, items)


# --- Routines -----------------------------------------------------------------


async def _replace_routines(
    session: AsyncSession, account_id: uuid.UUID, compiled: Sequence[compiler.CompiledRoutine],
    *, climate: str | None, explanation_source: str,
) -> list[Routine]:
    """Write compiled routines, keeping the version number moving forward."""
    existing = {
        row.kind: row for row in (await session.execute(
            select(Routine).where(Routine.account_id == account_id)
        )).scalars().all()
    }

    stored: list[Routine] = []
    for built in compiled:
        routine = existing.get(built.kind)
        if routine is None:
            # Label and frequency are set here rather than after the flush:
            # both are NOT NULL, so a bare Routine() cannot be flushed.
            routine = Routine(
                account_id=account_id, kind=built.kind,
                label=built.label, frequency=built.frequency,
            )
            session.add(routine)
            await session.flush()
            current_steps: dict[str, RoutineStep] = {}
        else:
            current_rows = (await session.execute(
                select(RoutineStep).where(RoutineStep.routine_id == routine.id)
            )).scalars().all()
            current_steps = {}
            for current in current_rows:
                if current.slot in current_steps:
                    raise ValueError(
                        f"Routine {routine.kind!r} has duplicate current step slot {current.slot!r}; refusing reconciliation"
                    )
                current_steps[current.slot] = current
            routine.version += 1

        routine.label = built.label
        routine.frequency = built.frequency
        routine.status = "active"
        routine.engine_version = ROUTINE_ENGINE_VERSION
        routine.climate = climate
        routine.explanation_source = explanation_source
        routine.warnings = [row.as_dict() for row in built.findings]
        routine.climate_notes = built.climate_notes
        routine.skipped_for_allergy = built.skipped_for_allergy

        for step in built.steps:
            current = current_steps.pop(step.slot, None)
            if current is None:
                current = RoutineStep(routine_id=routine.id, slot=step.slot)
                session.add(current)
            current.label = step.label
            current.position = step.order
            current.required = step.required
            current.why = step.why
            current.frequency = step.frequency
            current.inventory_item_id = uuid.UUID(step.item_id) if step.item_id else None
            current.product_name = step.product_name
            current.safety_note = step.safety_note
            current.alternative = step.alternative
            current.climate_note = step.climate_note
            current.is_gap = step.is_gap

        # A removed rendering row is safe to delete: the SET NULL FK keeps its
        # historical adherence, whose durable identity is routine + slot + day.
        for removed in current_steps.values():
            await session.delete(removed)
        stored.append(routine)

    # A routine that no longer makes sense — every product for it archived, say —
    # is retired rather than left showing stale steps.
    built_kinds = {row.kind for row in compiled}
    for kind, routine in existing.items():
        if kind not in built_kinds:
            routine.status = "retired"
    return stored


async def generate_routines(
    session: AsyncSession, *, account_id: uuid.UUID, account_id_str: str,
    body: RoutineGenerateRequest, care_adjustment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build every routine this person has the products for.

    The compiler decides; the model, if it is reachable and its wording passes
    the safety sweep, only rephrases.
    """
    day_context, care_context, decisions = await _current_care_decisions(
        session, account_id, body.as_of,
    )
    care_plan = care_routine_plan.plan_care_routine(care_context, decisions)
    guidance = await care_guidance.build_care_guidance(
        session, care_context=care_context, care_plan=care_plan,
    )
    hair_wash_cadence = await _current_hair_wash_cadence(
        session, account_id=account_id, care_context=care_context,
    )
    home_care_set = await home_care.build_home_care(
        session, care_context=care_context, hair_wash_cadence=hair_wash_cadence,
    )
    selection_plan = _routine_selection_plan(care_plan)
    beauty = list(care_context.skin_products)
    hair = list(care_context.hair_products)
    eligibility = _routine_eligibility(decisions)
    legacy_attributes = await shelf.shelf_attributes(session, account_id)
    legacy_climate = body.climate or legacy_attributes.get("climate")

    compiled = compiler.compile_all(
        beauty, hair, allergies=care_context.allergies, climate=legacy_climate,
        today=care_context.plan_date, eligibility=eligibility,
        selection_plan=selection_plan,
    )
    if body.kinds:
        compiled = [row for row in compiled if row.kind in body.kinds]

    # Capture deterministic Care material before any optional AI explanation
    # and before mutable Routine/RoutineStep rows are reconciled.
    audit_snapshot = care_snapshot.build_care_recommendation_snapshot(
        care_context=care_context,
        decisions=decisions,
        care_plan=care_plan,
        compiled_routines=compiled,
        requested_kinds=body.kinds,
        legacy_climate=legacy_climate,
        routine_engine_version=ROUTINE_ENGINE_VERSION,
        ontology_version=ONTOLOGY_VERSION,
        care_guidance=guidance,
        hair_wash_cadence=hair_wash_cadence,
        home_care=home_care_set,
    )

    narratives: dict[str, Any] = {}
    ai_run_id = None
    source = explanation.SOURCE_DETERMINISTIC
    if body.explain and compiled:
        narratives, ai_run_id, source = await explanation.explain_routines(
            compiled, climate=legacy_climate, account_id_str=account_id_str,
        )

    stored = await _replace_routines(
        session, account_id, compiled, climate=legacy_climate, explanation_source=source,
    )

    run_inputs = _care_run_inputs(
        day_context, care_context, decisions, care_plan, guidance,
        hair_wash_cadence, home_care_set,
    )
    if care_adjustment is not None:
        run_inputs["care_adjustment"] = dict(care_adjustment)

    session.add(RoutineRecommendationRun(
        account_id=account_id, status="succeeded", engine_version=ROUTINE_ENGINE_VERSION,
        explanation_source=source,
        ai_run_id=ai_run_id, products_considered=len(beauty) + len(hair),
        routines_built=len(compiled),
        warnings_raised=sum(len(row.findings) for row in compiled),
        inputs={
            "climate": legacy_climate,
            "allergies_declared": len(care_context.allergies),
            "beauty_products": len(beauty),
            "hair_products": len(hair),
            "draft_items_ignored": care_context.draft_product_count,
            "as_of": care_context.plan_date.isoformat(),
            **run_inputs,
            "care_snapshot": audit_snapshot,
        },
    ))
    await session.flush()

    # Serialised from the stored rows rather than from the compiler's objects,
    # so every step carries the id the completion route needs. The two shapes
    # would otherwise drift, and a caller would get steps it could not tick off.
    routines = [
        explanation.apply_to_routine(await _serialize_routine(session, row), narratives.get(row.kind))
        for row in stored
    ]

    return {
        "routines": routines,
        "explanation_source": source,
        "knowledge_version": ONTOLOGY_VERSION,
        "care_safety": _care_safety_payload(care_context, decisions),
        "care_guidance": guidance.as_payload(),
        "home_care": home_care_set.as_payload(),
        "products_considered": len(beauty) + len(hair),
        "drafts_ignored": care_context.draft_product_count,
        "disclaimer": ROUTINE_DISCLAIMER,
        "message": None if compiled else (
            "Nothing to build a routine from yet. Add a face wash, a moisturiser or a shampoo "
            "you already own and this starts working."
        ),
    }


def _effort_payload(plan: care_routine_plan.CareRoutinePlan) -> dict[str, Any]:
    decision = care_simplification.decide_care_simplification(plan.resolved_effort)
    return {
        "resolved": plan.resolved_effort.value,
        "source": plan.effort_source.value,
        "can_simplify": decision.target_effort is not None,
        "next_simpler": decision.target_effort.value if decision.target_effort else None,
    }


def _slot_material(plan: care_routine_plan.CareRoutinePlan) -> tuple[care_routine_plan.CareSlotPlan, ...]:
    return (*plan.skin_slots, *plan.hair_slots)


def _simplification_response(
    *,
    decision: care_simplification.CareSimplificationDecision,
    before: care_routine_plan.CareRoutinePlan,
    after: care_routine_plan.CareRoutinePlan | None,
) -> dict[str, Any]:
    if after is None:
        return {
            "simplification_version": care_simplification.CARE_SIMPLIFICATION_VERSION,
            "changed": False,
            "status": decision.status.value,
            "current_effort": decision.current_effort.value,
            "target_effort": None,
            "previous_plan_fingerprint": care_routine_plan.routine_plan_fingerprint(before),
            "new_plan_fingerprint": care_routine_plan.routine_plan_fingerprint(before),
            "removed_optional_slots": [],
            "preserved_required_slots": [],
            "message": "Your routine is already using the minimal effort setting.",
        }

    before_rows = {(row.category, row.slot): row for row in _slot_material(before)}
    after_rows = {(row.category, row.slot): row for row in _slot_material(after)}
    removed = [
        {
            "category": row.category,
            "slot": row.slot,
            "inventory_item_id": str(row.selected_item_id),
        }
        for key, row in before_rows.items()
        if row.active and not row.required and not after_rows[key].active and row.selected_item_id is not None
    ]
    preserved = [
        {
            "category": row.category,
            "slot": row.slot,
            "inventory_item_id": str(row.selected_item_id) if row.selected_item_id else None,
        }
        for row in _slot_material(after)
        if row.required and row.active
    ]
    removed.sort(key=lambda row: (row["category"], row["slot"], row["inventory_item_id"]))
    preserved.sort(key=lambda row: (row["category"], row["slot"], row["inventory_item_id"] or ""))
    message = (
        "Simplified. Your core Care steps stay in place and optional steps were reduced."
        if removed else
        "Your routine effort is now set lower. With what you currently use, the visible steps stay the same for now."
    )
    return {
        "simplification_version": care_simplification.CARE_SIMPLIFICATION_VERSION,
        "changed": True,
        "status": "applied",
        "previous_effort": decision.current_effort.value,
        "new_effort": after.resolved_effort.value,
        "previous_plan_fingerprint": care_routine_plan.routine_plan_fingerprint(before),
        "new_plan_fingerprint": care_routine_plan.routine_plan_fingerprint(after),
        "removed_optional_slots": removed,
        "preserved_required_slots": preserved,
        "message": message,
    }


async def simplify_care_routine(
    session: AsyncSession, *, account_id: uuid.UUID, account_id_str: str,
) -> dict[str, Any]:
    """Apply one explicit simplification atomically and regenerate routines."""
    plan_date = clock.local_today(clock.DEFAULT_TIMEZONE)
    _, care_context, decisions = await _current_care_decisions(session, account_id, plan_date)
    before = care_routine_plan.plan_care_routine(care_context, decisions)
    decision = care_simplification.decide_care_simplification(before.resolved_effort)
    if decision.target_effort is None:
        return _simplification_response(decision=decision, before=before, after=None)

    profile = await profile_service.get_or_create_profile(session, account_id)
    await profile_service.apply_attributes(
        session,
        profile,
        [{"key": "care_routine_effort", "value": decision.target_effort.value}],
        source="user_declared",
        confidence=1.0,
        verification_state="confirmed",
        reason="care_simplification_v3_03_10",
    )
    adjustment = {
        "version": care_simplification.CARE_SIMPLIFICATION_VERSION,
        "kind": "explicit_simplification",
        "from_effort": decision.current_effort.value,
        "to_effort": decision.target_effort.value,
        "profile_change_reason": "care_simplification_v3_03_10",
    }
    await generate_routines(
        session,
        account_id=account_id,
        account_id_str=account_id_str,
        body=RoutineGenerateRequest(as_of=plan_date, explain=False),
        care_adjustment=adjustment,
    )
    _, after_context, after_decisions = await _current_care_decisions(session, account_id, plan_date)
    after = care_routine_plan.plan_care_routine(after_context, after_decisions)
    return _simplification_response(decision=decision, before=before, after=after)


def _customer_category(category: str) -> str:
    return {"beauty": "skin_care", "hair": "hair_care"}[category]


def _changed_slots(
    before: care_routine_plan.CareRoutinePlan,
    after: care_routine_plan.CareRoutinePlan,
) -> list[dict[str, Any]]:
    before_rows = {(row.category, row.slot): row for row in _slot_material(before)}
    after_rows = {(row.category, row.slot): row for row in _slot_material(after)}
    changed: list[dict[str, Any]] = []
    for key, old in before_rows.items():
        new = after_rows[key]
        if (
            old.active, old.selected_item_id, old.is_gap
        ) == (
            new.active, new.selected_item_id, new.is_gap
        ):
            continue
        changed.append({
            "category": _customer_category(old.category),
            "slot": old.slot,
            "previous_item_id": str(old.selected_item_id) if old.selected_item_id else None,
            "new_item_id": str(new.selected_item_id) if new.selected_item_id else None,
            "became_gap": new.is_gap,
        })
    return sorted(changed, key=lambda row: (row["category"], row["slot"]))


async def _care_product_preference(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    item_id: uuid.UUID,
    pause: bool,
) -> dict[str, Any]:
    """Apply one explicit Care product pause/resume in the current transaction."""
    item = await inventory_service.owned_item(session, account_id, item_id)
    if item.category not in ("beauty", "hair"):
        raise ValidationFailedError(
            "Only Skin Care and Hair Care products can be paused from Care routines.",
            field="item_id",
        )
    if item.status != "active" or item.verification_state != "confirmed":
        raise ValidationFailedError(
            "Please confirm this product before changing its Care routine preference.",
            field="item_id",
        )

    row = (await session.execute(
        select(InventoryAttribute).where(
            InventoryAttribute.item_id == item.id,
            InventoryAttribute.key == product_preferences.CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY,
        )
    )).scalar_one_or_none()
    effective = row is not None and product_preferences.is_effective_user_pause(
        value=row.value, source=row.source, verification_state=row.verification_state,
    )
    if pause == effective:
        return {
            "product_preference_version": product_preferences.CARE_PRODUCT_PREFERENCE_VERSION,
            "changed": False,
            "status": "already_paused" if pause else "already_active",
            "inventory_item_id": str(item.id),
            "display_name": item.display_name,
            "category": _customer_category(item.category),
            "affected_slots": [],
            "message": "Paused from your Care routine." if pause else "Available to your Care routine again.",
        }

    plan_date = clock.local_today(clock.DEFAULT_TIMEZONE)
    _, before_context, before_decisions = await _current_care_decisions(session, account_id, plan_date)
    before_plan = care_routine_plan.plan_care_routine(before_context, before_decisions)
    previous_version = item.version

    if pause:
        if row is None:
            row = InventoryAttribute(
                item_id=item.id,
                key=product_preferences.CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY,
                value=True,
                source="user_declared",
                confidence=1.0,
                verification_state="confirmed",
            )
            session.add(row)
        else:
            row.value = True
            row.source = "user_declared"
            row.confidence = 1.0
            row.verification_state = "confirmed"
        event_type = "care_routine_paused"
        from_state, to_state = "active", "paused"
        status = "paused"
        message = "Paused from your Care routine. It stays in your inventory."
        kind = "explicit_product_pause"
    else:
        await session.delete(row)
        event_type = "care_routine_resumed"
        from_state, to_state = "paused", "active"
        status = "active"
        message = "Available to your Care routine again."
        kind = "explicit_product_resume"

    item.version += 1
    item.updated_at = utcnow()
    await inventory_service.record_event(
        session, item, event_type,
        {"version": item.version, "from_state": from_state, "to_state": to_state},
    )
    # The session deliberately disables autoflush.  Make the preference
    # visible to the immediately following Care assembly while keeping all
    # writes inside the caller's transaction.
    await session.flush()
    adjustment = {
        "version": product_preferences.CARE_PRODUCT_PREFERENCE_VERSION,
        "kind": kind,
        "item_id": str(item.id),
        "from_state": from_state,
        "to_state": to_state,
    }
    await generate_routines(
        session,
        account_id=account_id,
        account_id_str=account_id_str,
        body=RoutineGenerateRequest(as_of=plan_date, explain=False),
        care_adjustment=adjustment,
    )
    _, after_context, after_decisions = await _current_care_decisions(session, account_id, plan_date)
    after_plan = care_routine_plan.plan_care_routine(after_context, after_decisions)
    await session.flush()
    return {
        "product_preference_version": product_preferences.CARE_PRODUCT_PREFERENCE_VERSION,
        "changed": True,
        "status": status,
        "inventory_item_id": str(item.id),
        "display_name": item.display_name,
        "category": _customer_category(item.category),
        "previous_item_version": previous_version,
        "new_item_version": item.version,
        "previous_decision_fingerprint": care_decisions.decision_fingerprint(before_decisions),
        "new_decision_fingerprint": care_decisions.decision_fingerprint(after_decisions),
        "previous_plan_fingerprint": care_routine_plan.routine_plan_fingerprint(before_plan),
        "new_plan_fingerprint": care_routine_plan.routine_plan_fingerprint(after_plan),
        "affected_slots": _changed_slots(before_plan, after_plan),
        "message": message,
    }


async def pause_care_product(
    session: AsyncSession, *, account_id: uuid.UUID, account_id_str: str, item_id: uuid.UUID,
) -> dict[str, Any]:
    return await _care_product_preference(
        session, account_id=account_id, account_id_str=account_id_str, item_id=item_id, pause=True,
    )


async def resume_care_product(
    session: AsyncSession, *, account_id: uuid.UUID, account_id_str: str, item_id: uuid.UUID,
) -> dict[str, Any]:
    return await _care_product_preference(
        session, account_id=account_id, account_id_str=account_id_str, item_id=item_id, pause=False,
    )


def _care_product_row(care_context, item_id: uuid.UUID):
    return next(
        (
            product for product in (*care_context.skin_products, *care_context.hair_products)
            if product.item.id == item_id
        ),
        None,
    )


async def _selection_preference(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    item_id: uuid.UUID,
    prefer: bool,
) -> dict[str, Any]:
    """Apply an explicit product selection preference atomically."""
    item = await inventory_service.owned_item(session, account_id, item_id)
    if item.category not in ("beauty", "hair"):
        raise ValidationFailedError(
            "Only Skin Care and Hair Care products can be made preferred.", field="item_id",
        )
    if item.status != "active" or item.verification_state != "confirmed":
        raise ValidationFailedError(
            "Please confirm this product before making it preferred.", field="item_id",
        )

    plan_date = clock.local_today(clock.DEFAULT_TIMEZONE)
    _, before_context, before_decisions = await _current_care_decisions(session, account_id, plan_date)
    product = _care_product_row(before_context, item_id)
    if product is None or product.slot is None:
        raise ValidationFailedError(
            "This product does not have a confirmed Care routine role yet.", field="item_id",
        )
    target_decision = next(
        (row for row in before_decisions.product_decisions if row.item_id == item_id), None,
    )
    if target_decision is None:
        raise ValidationFailedError(
            "This product does not have a confirmed Care routine role yet.", field="item_id",
        )
    if prefer and item_id in before_context.paused_product_ids:
        raise ValidationFailedError(
            "Use this product in your routine again before making it preferred.", field="item_id",
        )
    if prefer and not target_decision.eligible:
        raise ValidationFailedError(
            "This product is not currently eligible for your Care routine.", field="item_id",
        )

    rows = (await session.execute(
        select(InventoryAttribute).where(
            InventoryAttribute.item_id.in_(
                product.item.id for product in (*before_context.skin_products, *before_context.hair_products)
            ),
            InventoryAttribute.key == product_preferences.CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY,
        )
    )).scalars().all()
    by_item = {row.item_id: row for row in rows}
    effective_ids = {
        row.item_id for row in rows
        if product_preferences.is_effective_user_preference(
            value=row.value, source=row.source, verification_state=row.verification_state,
        )
    }
    target_effective = item_id in effective_ids
    same_slot_ids = {
        candidate.item.id for candidate in (*before_context.skin_products, *before_context.hair_products)
        if candidate.slot == product.slot and candidate.item.category == product.item.category
    }
    conflicting_ids = sorted((effective_ids & same_slot_ids) - {item_id}, key=str)
    before_plan = care_routine_plan.plan_care_routine(before_context, before_decisions)
    before_slot = next(
        row for row in (*before_plan.skin_slots, *before_plan.hair_slots)
        if row.category == product.item.category and row.slot == product.slot
    )

    if prefer and target_effective and not conflicting_ids:
        return {
            "selection_preference_version": product_preferences.CARE_PRODUCT_SELECTION_PREFERENCE_VERSION,
            "changed": False, "status": "already_preferred", "inventory_item_id": str(item.id),
            "display_name": item.display_name, "category": _customer_category(item.category),
            "slot": product.slot, "cleared_preferred_item_ids": [],
            "selection_applied": before_slot.active and before_slot.selected_item_id == item.id,
            "message": "Preferred for this Care step.",
        }
    if not prefer and not target_effective:
        return {
            "selection_preference_version": product_preferences.CARE_PRODUCT_SELECTION_PREFERENCE_VERSION,
            "changed": False, "status": "already_standard", "inventory_item_id": str(item.id),
            "display_name": item.display_name, "category": _customer_category(item.category),
            "slot": product.slot, "cleared_preferred_item_ids": [],
            "selection_applied": False,
            "message": "Your routine will use its normal product choice for this step again.",
        }

    cleared_ids: list[str] = []
    if prefer:
        row = by_item.get(item_id)
        if not target_effective:
            if row is None:
                row = InventoryAttribute(
                    item_id=item.id,
                    key=product_preferences.CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY,
                    value=True, source="user_declared", confidence=1.0, verification_state="confirmed",
                )
                session.add(row)
            else:
                row.value = True
                row.source = "user_declared"
                row.confidence = 1.0
                row.verification_state = "confirmed"
            item.version += 1
            await inventory_service.record_event(
                session, item, "care_routine_preferred", {"version": item.version, "slot": product.slot},
            )
        for other_id in conflicting_ids:
            other = await session.get(InventoryItem, other_id)
            other_row = by_item[other_id]
            await session.delete(other_row)
            other.version += 1
            cleared_ids.append(str(other.id))
            await inventory_service.record_event(
                session, other, "care_routine_preference_cleared",
                {"version": other.version, "slot": product.slot, "replaced_by_item_id": str(item.id)},
            )
        kind = "explicit_product_preference"
        message = (
            "Preferred for this Care step."
            if before_slot.active else
            "Preferred for this Care step. It will be used when that step is active."
        )
    else:
        row = by_item[item_id]
        await session.delete(row)
        item.version += 1
        await inventory_service.record_event(
            session, item, "care_routine_preference_cleared",
            {"version": item.version, "slot": product.slot},
        )
        kind = "explicit_product_preference_clear"
        message = "Your routine will use its normal product choice for this step again."

    await session.flush()
    adjustment = {
        "version": product_preferences.CARE_PRODUCT_SELECTION_PREFERENCE_VERSION,
        "kind": kind,
        "item_id": str(item.id),
        "slot": product.slot,
        **({"cleared_preferred_item_ids": cleared_ids} if prefer else {}),
    }
    await generate_routines(
        session, account_id=account_id, account_id_str=account_id_str,
        body=RoutineGenerateRequest(as_of=plan_date, explain=False), care_adjustment=adjustment,
    )
    _, after_context, after_decisions = await _current_care_decisions(session, account_id, plan_date)
    after_plan = care_routine_plan.plan_care_routine(after_context, after_decisions)
    after_slot = next(
        row for row in (*after_plan.skin_slots, *after_plan.hair_slots)
        if row.category == product.item.category and row.slot == product.slot
    )
    await session.flush()
    return {
        "selection_preference_version": product_preferences.CARE_PRODUCT_SELECTION_PREFERENCE_VERSION,
        "changed": True, "status": "preferred" if prefer else "standard",
        "inventory_item_id": str(item.id), "display_name": item.display_name,
        "category": _customer_category(item.category), "slot": product.slot,
        "cleared_preferred_item_ids": cleared_ids,
        "previous_selected_item_id": str(before_slot.selected_item_id) if before_slot.selected_item_id else None,
        "new_selected_item_id": str(after_slot.selected_item_id) if after_slot.selected_item_id else None,
        "selection_applied": after_slot.selected_item_id == item.id,
        "previous_decision_fingerprint": care_decisions.decision_fingerprint(before_decisions),
        "new_decision_fingerprint": care_decisions.decision_fingerprint(after_decisions),
        "previous_plan_fingerprint": care_routine_plan.routine_plan_fingerprint(before_plan),
        "new_plan_fingerprint": care_routine_plan.routine_plan_fingerprint(after_plan),
        "message": message,
    }


async def prefer_care_product(
    session: AsyncSession, *, account_id: uuid.UUID, account_id_str: str, item_id: uuid.UUID,
) -> dict[str, Any]:
    return await _selection_preference(
        session, account_id=account_id, account_id_str=account_id_str, item_id=item_id, prefer=True,
    )


async def unprefer_care_product(
    session: AsyncSession, *, account_id: uuid.UUID, account_id_str: str, item_id: uuid.UUID,
) -> dict[str, Any]:
    return await _selection_preference(
        session, account_id=account_id, account_id_str=account_id_str, item_id=item_id, prefer=False,
    )


async def _serialize_routine(session: AsyncSession, routine: Routine) -> dict[str, Any]:
    steps = (await session.execute(
        select(RoutineStep).where(RoutineStep.routine_id == routine.id).order_by(RoutineStep.position)
    )).scalars().all()
    return {
        "id": str(routine.id),
        "kind": routine.kind,
        "label": routine.label,
        "frequency": routine.frequency,
        "status": routine.status,
        "version": routine.version,
        "engine_version": routine.engine_version,
        "explanation_source": routine.explanation_source,
        "warnings": list(routine.warnings or []),
        "climate_notes": list(routine.climate_notes or []),
        "skipped_for_allergy": list(routine.skipped_for_allergy or []),
        "steps": [{
            "id": str(step.id), "slot": step.slot, "label": step.label, "order": step.position,
            "required": step.required, "optional": not step.required, "why": step.why,
            "frequency": step.frequency,
            "inventory_item_id": str(step.inventory_item_id) if step.inventory_item_id else None,
            "product_name": step.product_name, "owned": step.inventory_item_id is not None,
            "safety_note": step.safety_note, "alternative": step.alternative,
            "climate_note": step.climate_note, "is_gap": step.is_gap,
        } for step in steps],
        "disclaimer": ROUTINE_DISCLAIMER,
    }


def _routine_products_for_kind(
    kind: str,
    beauty: Sequence[ShelfProduct],
    hair: Sequence[ShelfProduct],
) -> Sequence[ShelfProduct]:
    if kind == compiler.ROUTINE_WASH_DAY:
        return hair
    if kind in (compiler.ROUTINE_MORNING, compiler.ROUTINE_EVENING):
        return beauty
    return list(beauty) + list(hair)


def _routine_material_signature(routine: compiler.CompiledRoutine) -> dict[str, tuple[str | None, bool, bool]]:
    return {
        step.slot: (step.item_id, step.is_gap, step.required)
        for step in routine.steps
    }


async def _routine_plan_drifted(
    session: AsyncSession,
    routine_row: Routine,
    *,
    kind: str,
    care_context,
    decisions: care_decisions.CareDecisionSet,
    care_plan: care_routine_plan.CareRoutinePlan,
) -> bool:
    """Compare only material plan output; never write while serving GET."""
    if routine_row.engine_version != ROUTINE_ENGINE_VERSION:
        return True

    selection_plan = _routine_selection_plan(care_plan)
    eligibility = _routine_eligibility(decisions)
    current = compiler.compile_routine(
        kind,
        _routine_products_for_kind(kind, care_context.skin_products, care_context.hair_products),
        allergies=care_context.allergies,
        today=care_context.plan_date,
        eligibility=eligibility,
        selection_plan=selection_plan,
    )
    expected = _routine_material_signature(current)
    stored_steps = (await session.execute(
        select(RoutineStep)
        .where(RoutineStep.routine_id == routine_row.id)
        .order_by(RoutineStep.position)
    )).scalars().all()
    stored = {
        step.slot: (
            str(step.inventory_item_id) if step.inventory_item_id else None,
            step.is_gap,
            step.required,
        )
        for step in stored_steps
    }
    return stored != expected


async def routines_today(
    session: AsyncSession, *, account_id: uuid.UUID, on: date | None = None
) -> dict[str, Any]:
    """The routines that are actually relevant right now.

    Morning before the evening, evening after it, and the weekly extras only on
    the day they are due. Showing all five at once is how a routine feature
    turns into noise nobody opens.
    """
    today = on or clock.local_today(clock.DEFAULT_TIMEZONE)
    part = clock.part_of_day(clock.local_now(clock.DEFAULT_TIMEZONE))

    _, care_context, decisions = await _current_care_decisions(session, account_id, today)
    care_plan = care_routine_plan.plan_care_routine(care_context, decisions)
    guidance = await care_guidance.build_care_guidance(
        session, care_context=care_context, care_plan=care_plan,
    )
    hair_wash_cadence = await _current_hair_wash_cadence(
        session, account_id=account_id, care_context=care_context,
    )
    home_care_set = await home_care.build_home_care(
        session, care_context=care_context, hair_wash_cadence=hair_wash_cadence,
    )
    blocked_ids = {str(value) for value in decisions.blocked_product_ids}

    rows = (await session.execute(
        select(Routine).where(Routine.account_id == account_id, Routine.status == "active")
    )).scalars().all()
    by_kind = {row.kind: row for row in rows}

    wanted: list[str] = []
    if part in ("morning", "afternoon"):
        wanted.append(compiler.ROUTINE_MORNING)
    if part in ("afternoon", "evening", "night"):
        wanted.append(compiler.ROUTINE_EVENING)
    # Weekly extras surface on a weekend day, which is when people have time.
    if clock.is_weekend(today):
        wanted.append(compiler.ROUTINE_WEEKLY)
    if hair_wash_cadence.status is care_cadence.HairWashCadenceStatus.DUE:
        wanted.append(compiler.ROUTINE_WASH_DAY)

    routines: list[dict[str, Any]] = []
    refresh_required_kinds: list[str] = []
    for kind in wanted:
        routine_row = by_kind.get(kind)
        if routine_row is None:
            continue
        routine = await _serialize_routine(session, routine_row)
        routine_item_ids = {
            step["inventory_item_id"] for step in routine["steps"]
            if step["inventory_item_id"]
        }
        if routine_item_ids & blocked_ids or await _routine_plan_drifted(
            session, routine_row, kind=kind, care_context=care_context,
            decisions=decisions, care_plan=care_plan,
        ):
            refresh_required_kinds.append(kind)
            continue
        routines.append(routine)

    done = {
        (row.routine_id, row.slot) for row in (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.account_id == account_id, RoutineAdherence.done_on == today,
            )
        )).scalars().all() if row.completed
    }
    for routine in routines:
        routine_id = uuid.UUID(routine["id"])
        for step in routine["steps"]:
            step["completed_today"] = (routine_id, step["slot"]) in done

    refresh_required = bool(refresh_required_kinds)
    return {
        "date": today.isoformat(),
        "part_of_day": part,
        "routines": routines,
        "refresh_required": refresh_required,
        "refresh_required_kinds": refresh_required_kinds,
        "care_safety": _care_safety_payload(care_context, decisions),
        "care_guidance": guidance.as_payload(),
        "hair_wash_cadence": hair_wash_cadence.as_payload(),
        "home_care": home_care_set.as_payload(),
        "message": (
            "Your saved Care routine needs a refresh before we show those steps because its Care plan or safety facts changed."
            if refresh_required else (None if routines else (
                "Complete a wash routine once and GlamGenius can use that date as the starting point for the wash rhythm you recorded."
                if hair_wash_cadence.status is care_cadence.HairWashCadenceStatus.NEEDS_ANCHOR
                else "Nothing due right now."
            ))
        ),
        "disclaimer": ROUTINE_DISCLAIMER,
    }


async def complete_step(
    session: AsyncSession, *, account_id: uuid.UUID, step_id: uuid.UUID, body: RoutineStepComplete
) -> dict[str, Any]:
    """Mark a step done. Ownership is checked from the token, not the request."""
    row = (await session.execute(
        select(RoutineStep, Routine)
        .join(Routine, Routine.id == RoutineStep.routine_id)
        .where(RoutineStep.id == step_id, Routine.account_id == account_id)
    )).first()
    if row is None:
        raise NotFoundError("We could not find that routine step.")
    step, routine = row

    done_on = body.done_on or clock.local_today(clock.DEFAULT_TIMEZONE)
    existing = (await session.execute(
        select(RoutineAdherence).where(
            RoutineAdherence.account_id == account_id,
            RoutineAdherence.routine_id == routine.id,
            RoutineAdherence.slot == step.slot,
            RoutineAdherence.done_on == done_on,
        )
    )).scalar_one_or_none()
    if existing is None:
        existing = RoutineAdherence(
            account_id=account_id, routine_id=routine.id, slot=step.slot,
            step_id=step_id, done_on=done_on,
        )
        session.add(existing)
    else:
        # The UUID is provenance for the current rendering row, never the
        # logical identity of this historical completion.
        existing.step_id = step_id
    existing.completed = body.completed
    existing.note = body.note
    await session.flush()

    return {
        "step_id": str(step_id),
        "routine_id": str(routine.id),
        "done_on": done_on.isoformat(),
        "completed": existing.completed,
        "note": (
            "Marked done." if existing.completed else "Unmarked. Missing one is not a problem."
        ),
    }


async def consistency(
    session: AsyncSession, *, account_id: uuid.UUID, days: int = CONSISTENCY_WINDOW_DAYS
) -> dict[str, Any]:
    today = clock.local_today(clock.DEFAULT_TIMEZONE)
    since = today - timedelta(days=days - 1)
    rows = (await session.execute(
        select(RoutineAdherence).where(
            RoutineAdherence.account_id == account_id,
            RoutineAdherence.done_on >= since,
            RoutineAdherence.done_on <= today,
        )
    )).scalars().all()
    expected = (await session.execute(
        select(RoutineStep.id)
        .join(Routine, Routine.id == RoutineStep.routine_id)
        .where(Routine.account_id == account_id, Routine.status == "active")
    )).scalars().all()
    return shelf.consistency(rows, len(expected), days=days)


# --- Ingredients ---------------------------------------------------------------


async def check_ingredients(
    session: AsyncSession, *, account_id: uuid.UUID, account_id_str: str, body: IngredientCheckRequest
) -> dict[str, Any]:
    """Check a label, a list, or products you own against the reviewed rules.

    Nothing here is stored: this is the "is it okay to use these together"
    question, and answering it should not silently edit somebody's shelf.
    """
    if not body.has_input():
        raise ValidationFailedError(
            "Give us a label to read, some ingredients to check, or products you own.",
            field="label_text",
        )

    context = await shelf.gather(session, account_id=account_id)
    owned_by_id = {str(item.id): item for item in context.owned}

    checked: list[ShelfProduct] = []
    if body.item_ids:
        for item_id in body.item_ids:
            item = owned_by_id.get(str(item_id))
            if item is None:
                raise NotFoundError("We could not find one of those products in your inventory.")
            category = item.category if item.category in shelf.ROUTINE_CATEGORIES else "beauty"
            match = [row for row in shelf.build(context, category) if row.id == str(item_id)]
            checked.extend(match)

    typed: list[parser.ParsedIngredient] = []
    if body.ingredients:
        typed.extend(parser.parse_declared(body.ingredients, source=body.source))
    if body.label_text:
        typed.extend(parser.parse_label(body.label_text, source=body.source))

    if typed:
        # The thing being checked is treated as one more product on the shelf,
        # so it is compared against what is already there by the same rules.
        by_key = {row.key: row for row in typed}
        checked.append(ShelfProduct(
            item=_pseudo_item(), slot=None, ingredients=list(by_key.values()),
        ))

    against = list(checked)
    if body.against_owned:
        owned_ids = {row.id for row in checked}
        for category in shelf.ROUTINE_CATEGORIES:
            against.extend(row for row in shelf.build(context, category) if row.id not in owned_ids)

    findings = (
        rules_engine.allergy_findings(checked, context.allergies)
        + rules_engine.compatibility_findings(against)
        + rules_engine.unconfirmed_findings(checked)
    )
    # Only report conflicts that actually involve what was asked about.
    asked_ids = {row.id for row in checked}
    findings = [
        row for row in findings
        if not row.item_ids or asked_ids & set(row.item_ids)
    ]

    plain: dict[str, str] = {}
    source = explanation.SOURCE_DETERMINISTIC
    if body.explain and findings:
        plain, _, source = await explanation.explain_findings(findings, account_id_str=account_id_str)

    warnings = []
    for row in findings:
        entry = row.as_dict()
        if plain.get(row.rule_id):
            entry["plain_english"] = plain[row.rule_id]
        warnings.append(entry)

    identified = [row.as_dict() for row in typed]
    return {
        "identified": identified,
        "unidentified": parser.unmatched_terms(body.label_text) if body.label_text else [],
        "warnings": warnings,
        "explanation_source": source,
        "checked_against_owned": body.against_owned,
        "needs_confirmation": [row for row in identified if row["needs_confirmation"]],
        "knowledge_version": ONTOLOGY_VERSION,
        "note": (
            "Only ingredients we have a reviewed note for are recognised. "
            "Anything we did not recognise is listed so you can see what we missed."
        ),
    }


def _pseudo_item():
    """A stand-in for something not in inventory, so the engine can score it."""
    from app.domains.recommendation.context import OwnedItem

    return OwnedItem(
        id=uuid.uuid4(), category="beauty", subcategory=None,
        display_name="What you are checking", brand=None, details={}, condition="good",
        usage_count=0, last_used_at=None, purchase_price=None, currency="INR",
    )


def ingredient_detail(key: str) -> dict[str, Any]:
    row = INGREDIENT_BY_KEY.get(key)
    if row is None:
        raise NotFoundError("We have no reviewed note for that ingredient yet.")
    related = [
        {
            "rule_id": rule.rule_id, "severity": rule.severity, "headline": rule.headline,
            "guidance": rule.guidance, "evidence_note": rule.evidence_note,
            "other_family": rule.family_b if rule.family_a == row.family else rule.family_a,
        }
        for rule in rules_engine.COMPATIBILITY_RULES
        if row.family in (rule.family_a, rule.family_b)
    ]
    return {
        "ingredient_key": row.key,
        "display_name": row.display_name,
        "inci_name": row.inci_name,
        "family": row.family,
        "summary": row.summary,
        "common_use": row.common_use,
        "aliases": sorted(row.aliases),
        "rules": related,
        "knowledge_version": ONTOLOGY_VERSION,
        "note": "A reviewed note about what this ingredient is. Not advice about your skin.",
    }


async def confirm_ingredients(
    session: AsyncSession, *, account_id: uuid.UUID, body: IngredientConfirmRequest
) -> dict[str, Any]:
    """Confirm a low-confidence read, so it starts driving the rules."""
    rows = (await session.execute(
        select(ProductIngredient).where(
            ProductIngredient.account_id == account_id,
            ProductIngredient.item_id == body.item_id,
            ProductIngredient.ingredient_key.in_(body.ingredient_keys),
        )
    )).scalars().all()
    if not rows:
        raise NotFoundError("We could not find those ingredients on that product.")

    for row in rows:
        if body.confirmed:
            row.confirmed_at = utcnow()
            row.needs_confirmation = False
            row.confidence = 1.0
            row.source = parser.SOURCE_USER
        else:
            await session.delete(row)

    return {
        "item_id": str(body.item_id),
        "updated": len(rows),
        "confirmed": body.confirmed,
        "note": (
            "Confirmed. We will use these when checking your routine."
            if body.confirmed else "Removed. We will not use those."
        ),
    }


# --- Perfume -------------------------------------------------------------------


async def perfume_recommendation(
    session: AsyncSession, *, account_id: uuid.UUID,
    occasion_key: str | None = None, weather: str | None = None,
    time_of_day: str | None = None, season: str | None = None,
) -> dict[str, Any]:
    context = await shelf.gather(session, account_id=account_id)
    perfumes = context.by_category("perfumes")

    recent = [
        str(item.id) for item in perfumes
        if item.last_used_at and (context.today - item.last_used_at).days <= perfume.RECENT_DAYS
    ]
    attributes = await shelf.shelf_attributes(session, account_id)

    return perfume.recommend(
        perfumes,
        occasion_key=occasion_key,
        weather=weather or context.climate,
        time_of_day=time_of_day or clock.part_of_day(clock.local_now(clock.DEFAULT_TIMEZONE)),
        season=season or clock.season_for(context.today),
        preferred_style=attributes.get("preferred_style"),
        recently_used_item_ids=recent,
        today=context.today,
    )


def supplement_question(text: str) -> dict[str, Any]:
    """Anything that reads like a health question gets the boundary, not an answer."""
    boundary = boundary_for(text)
    if boundary is None:
        return {
            "boundary": False,
            "message": "We track supplements as inventory — name, brand, dates and how often you take them.",
        }
    return boundary.as_dict()


# --- Observations -----------------------------------------------------------------


async def record_observation(
    session: AsyncSession, *, account_id: uuid.UUID, body: ObservationInput
) -> dict[str, Any]:
    """Store what the user noticed, verbatim, and never interpret it.

    If it reads like a health question, the response carries the professional
    boundary. The note is still saved — it is theirs — but the app does not
    pretend to have an answer.
    """
    boundary = boundary_for(body.note)
    row = UserReportedObservation(
        account_id=account_id,
        observed_on=body.observed_on or clock.local_today(clock.DEFAULT_TIMEZONE),
        area=body.area, note=body.note, item_id=body.item_id,
        routed_to_professional=boundary is not None,
    )
    session.add(row)
    await session.flush()

    return {
        "id": str(row.id),
        "observed_on": row.observed_on.isoformat(),
        "area": row.area,
        "note": row.note,
        "boundary": boundary.as_dict() if boundary else None,
        "message": (
            PROFESSIONAL_BOUNDARY if boundary
            else "Saved. We keep your note as you wrote it and do not interpret it."
        ),
    }


# --- Explicit Care experience feedback --------------------------------------


def _serialize_care_experience_feedback(row: CareExperienceFeedback) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "feedback_version": row.feedback_version,
        "subject_type": row.subject_type,
        "subject_id": str(row.subject_id),
        "routine_kind": row.routine_kind,
        "routine_slot": row.routine_slot,
        "dimension": row.dimension,
        "sentiment": row.sentiment,
        "note": row.note,
        "experienced_on": row.experienced_on.isoformat(),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _validate_care_feedback_subject(
    session: AsyncSession, *, account_id: uuid.UUID, body: CareExperienceFeedbackInput
) -> tuple[str | None, str | None]:
    """Validate ownership and return durable routine provenance."""
    if body.subject_type == "product":
        item = (await session.execute(
            select(InventoryItem).where(
                InventoryItem.id == body.subject_id,
                InventoryItem.account_id == account_id,
            )
        )).scalar_one_or_none()
        if item is None or item.category not in {"beauty", "hair"}:
            raise NotFoundError("We could not find that Care product in your inventory.")
        return None, None

    row = (await session.execute(
        select(RoutineStep, Routine)
        .join(Routine, Routine.id == RoutineStep.routine_id)
        .where(RoutineStep.id == body.subject_id, Routine.account_id == account_id)
    )).first()
    if row is None:
        raise NotFoundError("We could not find that routine step.")
    step, routine = row
    return routine.kind, step.slot


async def record_care_experience_feedback(
    session: AsyncSession, *, account_id: uuid.UUID, body: CareExperienceFeedbackInput
) -> dict[str, Any]:
    """Persist explicit subjective feedback without invoking any Care engine."""
    experienced_on = body.experienced_on or clock.local_today(clock.DEFAULT_TIMEZONE)
    if experienced_on > clock.local_today(clock.DEFAULT_TIMEZONE):
        raise ValidationFailedError("Experience date cannot be in the future.", field="experienced_on")

    routine_kind, routine_slot = await _validate_care_feedback_subject(
        session, account_id=account_id, body=body,
    )
    row = CareExperienceFeedback(
        account_id=account_id,
        subject_type=body.subject_type,
        subject_id=body.subject_id,
        dimension=body.dimension,
        sentiment=body.sentiment,
        note=body.note,
        experienced_on=experienced_on,
        feedback_version=CARE_EXPERIENCE_FEEDBACK_VERSION,
        routine_kind=routine_kind,
        routine_slot=routine_slot,
    )
    session.add(row)
    await session.flush()
    payload = _serialize_care_experience_feedback(row)
    payload.update({
        "affects_recommendations": False,
        "creates_memory": False,
        "changes_care_safety": False,
        "message": "Saved as your Care experience feedback. This does not change your routine automatically.",
    })
    return payload


async def list_care_experience_feedback(
    session: AsyncSession, *, account_id: uuid.UUID, subject_type: str | None = None,
    subject_id: uuid.UUID | None = None, limit: int = 50,
) -> dict[str, Any]:
    if subject_type is not None and subject_type not in {"product", "routine_step"}:
        raise ValidationFailedError("Unknown feedback subject type.", field="subject_type")
    bounded_limit = max(1, min(limit, 100))
    query = select(CareExperienceFeedback).where(CareExperienceFeedback.account_id == account_id)
    if subject_type is not None:
        query = query.where(CareExperienceFeedback.subject_type == subject_type)
    if subject_id is not None:
        query = query.where(CareExperienceFeedback.subject_id == subject_id)
    rows = (await session.execute(
        query.order_by(CareExperienceFeedback.created_at.desc(), CareExperienceFeedback.id.desc()).limit(bounded_limit)
    )).scalars().all()
    return {"feedback": [_serialize_care_experience_feedback(row) for row in rows]}


async def delete_care_experience_feedback(
    session: AsyncSession, *, account_id: uuid.UUID, feedback_id: uuid.UUID
) -> dict[str, Any]:
    result = await session.execute(delete(CareExperienceFeedback).where(
        CareExperienceFeedback.id == feedback_id,
        CareExperienceFeedback.account_id == account_id,
    ))
    if result.rowcount != 1:
        raise NotFoundError("We could not find that Care experience feedback.")
    return {"deleted": True, "id": str(feedback_id)}


async def list_observations(
    session: AsyncSession, *, account_id: uuid.UUID, limit: int = 50
) -> dict[str, Any]:
    rows = (await session.execute(
        select(UserReportedObservation)
        .where(UserReportedObservation.account_id == account_id)
        .order_by(UserReportedObservation.observed_on.desc())
        .limit(limit)
    )).scalars().all()
    return {
        "observations": [{
            "id": str(row.id), "observed_on": row.observed_on.isoformat(), "area": row.area,
            "note": row.note, "item_id": str(row.item_id) if row.item_id else None,
            "routed_to_professional": row.routed_to_professional,
        } for row in rows],
        "note": "Your own notes, kept as you wrote them. We do not turn these into a diagnosis.",
    }


# --- The You → Improve overview ----------------------------------------------------


async def improve_overview(session: AsyncSession, *, account_id: uuid.UUID) -> dict[str, Any]:
    """Everything the Improve screen shows, in one call.

    Modules with nothing in them are reported as empty rather than filled with
    placeholder content — the brief is explicit that a user who has not
    populated a module should not be shown it.
    """
    context = await shelf.gather(session, account_id=account_id)
    summary = shelf.summary(context)

    rows = (await session.execute(
        select(Routine).where(Routine.account_id == account_id, Routine.status == "active")
    )).scalars().all()
    routines = [await _serialize_routine(session, row) for row in rows]
    plan_date = clock.local_today(clock.DEFAULT_TIMEZONE)
    _, care_context, care_decisions = await _current_care_decisions(session, account_id, plan_date)
    care_plan = care_routine_plan.plan_care_routine(care_context, care_decisions)

    decisions_by_item = {row.item_id: row for row in care_decisions.product_decisions}
    care_product_controls = [
        {
            "inventory_item_id": str(product.item.id),
            "display_name": product.item.display_name,
            "category": _customer_category(product.item.category),
            "slot": product.slot,
            "paused": product.item.id in care_context.paused_product_ids,
            "preferred": product.item.id in care_context.preferred_product_ids,
            "eligible": decisions_by_item.get(product.item.id).eligible
            if product.item.id in decisions_by_item else False,
        }
        for product in sorted(
            (*care_context.skin_products, *care_context.hair_products),
            key=lambda row: (row.item.category, row.slot or "", str(row.item.id)),
        )
    ]

    missing = [
        row for report in summary["reports"].values() for row in report["warnings"]
        if row["rule_id"] == rules_engine.RULE_MISSING_SLOT
    ]

    return {
        "has_shelf": summary["counts"]["products"] > 0,
        "has_routines": bool(routines),
        "routines": routines,
        "consistency": await consistency(session, account_id=account_id),
        "needs_attention": summary["needs_attention"],
        "expiring": shelf.expiring(context),
        "low_use": shelf.low_use(context),
        "missing_categories": missing,
        "counts": summary["counts"],
        "routine_effort": _effort_payload(care_plan),
        "care_product_controls": care_product_controls,
        "disclaimer": ROUTINE_DISCLAIMER,
    }
