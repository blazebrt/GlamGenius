"""Pure minimum-effective Care routine planning for V3-03.4.

This module deliberately sits beside, rather than inside, the existing routine
compiler. It turns authoritative Care eligibility plus trusted inventory usage
facts into a recomputable, immutable plan. No database, HTTP, provider, AI, or
Evidence access belongs here.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from app.domains.care.decisions import CareDecisionSet
from app.domains.care.schemas import CareContext
from app.domains.routines.ontology import HAIR_SLOTS, SKIN_SLOTS, StepSlot
from app.domains.routines.rules import ShelfProduct

CARE_ROUTINE_PLAN_VERSION = "v3-03.12"


class CareRoutineEffort(StrEnum):
    MINIMAL = "minimal"
    BALANCED = "balanced"
    DETAILED = "detailed"


class CareEffortSource(StrEnum):
    USER_DECLARED = "user_declared"
    SYSTEM_DEFAULT_MISSING = "system_default_missing"
    SYSTEM_DEFAULT_NOT_SURE = "system_default_not_sure"


class CareInclusionReason(StrEnum):
    REQUIRED = "required"
    MINIMAL_EFFORT_EXCLUDED = "minimal_effort_excluded"
    BALANCED_ESTABLISHED_USE = "balanced_established_use"
    BALANCED_NO_ESTABLISHED_USE = "balanced_no_established_use"
    DETAILED_OWNED = "detailed_owned"
    NO_ELIGIBLE_OWNED_PRODUCT = "no_eligible_owned_product"


class CareSelectionBasis(StrEnum):
    USER_PREFERRED = "user_preferred"
    RECENT_USE = "recent_use"
    USAGE_COUNT = "usage_count"
    STABLE_FALLBACK = "stable_fallback"


@dataclass(frozen=True, slots=True)
class CareSlotPlan:
    category: str
    slot: str
    required: bool
    active: bool
    selected_item_id: uuid.UUID | None
    candidate_item_ids: tuple[uuid.UUID, ...]
    alternative_item_ids: tuple[uuid.UUID, ...]
    is_gap: bool
    inclusion_reason: CareInclusionReason
    selection_basis: CareSelectionBasis | None


@dataclass(frozen=True, slots=True)
class CareRoutinePlan:
    plan_version: str
    account_id: uuid.UUID
    plan_date: date
    resolved_effort: CareRoutineEffort
    effort_source: CareEffortSource
    skin_slots: tuple[CareSlotPlan, ...]
    hair_slots: tuple[CareSlotPlan, ...]

    @property
    def active_skin_slot_count(self) -> int:
        return sum(row.active for row in self.skin_slots)

    @property
    def active_hair_slot_count(self) -> int:
        return sum(row.active for row in self.hair_slots)

    @property
    def skin_gap_count(self) -> int:
        return sum(row.is_gap for row in self.skin_slots)

    @property
    def hair_gap_count(self) -> int:
        return sum(row.is_gap for row in self.hair_slots)

    @property
    def selected_item_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(
            row.selected_item_id
            for row in (*self.skin_slots, *self.hair_slots)
            if row.selected_item_id is not None
        )

    @property
    def alternative_item_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(
            item_id
            for row in (*self.skin_slots, *self.hair_slots)
            for item_id in row.alternative_item_ids
        )


def _effort(context: CareContext) -> tuple[CareRoutineEffort, CareEffortSource]:
    fact = context.preferences.get("care_routine_effort")
    value: Any = getattr(fact, "value", fact)
    if value in (CareRoutineEffort.MINIMAL.value, CareRoutineEffort.BALANCED.value, CareRoutineEffort.DETAILED.value):
        return CareRoutineEffort(value), CareEffortSource.USER_DECLARED
    if value == "not_sure":
        return CareRoutineEffort.BALANCED, CareEffortSource.SYSTEM_DEFAULT_NOT_SURE
    return CareRoutineEffort.BALANCED, CareEffortSource.SYSTEM_DEFAULT_MISSING


def _selection(
    candidates: tuple[uuid.UUID, ...], products: dict[uuid.UUID, ShelfProduct],
) -> tuple[uuid.UUID, CareSelectionBasis]:
    rows = [products[item_id] for item_id in candidates]
    with_recent = [row for row in rows if row.item.last_used_at is not None]
    if with_recent:
        latest = max(row.item.last_used_at for row in with_recent)
        recent = [row for row in with_recent if row.item.last_used_at == latest]
        if len(recent) == 1:
            return recent[0].item.id, CareSelectionBasis.RECENT_USE
        rows = recent
        if len({row.item.usage_count for row in rows}) > 1:
            highest = max(row.item.usage_count for row in rows)
            rows = [row for row in rows if row.item.usage_count == highest]
            if len(rows) == 1:
                return rows[0].item.id, CareSelectionBasis.USAGE_COUNT
    elif len({row.item.usage_count for row in rows}) > 1:
        highest = max(row.item.usage_count for row in rows)
        rows = [row for row in rows if row.item.usage_count == highest]
        if len(rows) == 1:
            return rows[0].item.id, CareSelectionBasis.USAGE_COUNT

    chosen = min(rows, key=lambda row: (row.item.display_name.casefold(), str(row.item.id)))
    return chosen.item.id, CareSelectionBasis.STABLE_FALLBACK


def _selection_with_preference(
    candidates: tuple[uuid.UUID, ...],
    products: dict[uuid.UUID, ShelfProduct],
    preferred_product_ids: frozenset[uuid.UUID],
) -> tuple[uuid.UUID, CareSelectionBasis]:
    preferred = tuple(item_id for item_id in candidates if item_id in preferred_product_ids)
    if preferred:
        selected, _ = _selection(preferred, products)
        return selected, CareSelectionBasis.USER_PREFERRED
    return _selection(candidates, products)


def _slot_plan(
    slot: StepSlot,
    *,
    effort: CareRoutineEffort,
    eligible: dict[str, tuple[uuid.UUID, ...]],
    products: dict[uuid.UUID, ShelfProduct],
    preferred_product_ids: frozenset[uuid.UUID],
) -> CareSlotPlan:
    candidates = eligible.get(slot.key, ())
    if slot.required:
        active = True
        reason = CareInclusionReason.REQUIRED
    elif not candidates:
        return CareSlotPlan(
            category=slot.category, slot=slot.key, required=False, active=False,
            selected_item_id=None, candidate_item_ids=(), alternative_item_ids=(),
            is_gap=False, inclusion_reason=CareInclusionReason.NO_ELIGIBLE_OWNED_PRODUCT,
            selection_basis=None,
        )
    elif effort is CareRoutineEffort.MINIMAL:
        return CareSlotPlan(
            category=slot.category, slot=slot.key, required=False, active=False,
            selected_item_id=None, candidate_item_ids=candidates,
            alternative_item_ids=candidates, is_gap=False,
            inclusion_reason=CareInclusionReason.MINIMAL_EFFORT_EXCLUDED,
            selection_basis=None,
        )
    elif effort is CareRoutineEffort.BALANCED:
        established = any(
            products[item_id].item.last_used_at is not None or products[item_id].item.usage_count > 0
            for item_id in candidates
        )
        if not established:
            return CareSlotPlan(
                category=slot.category, slot=slot.key, required=False, active=False,
                selected_item_id=None, candidate_item_ids=candidates,
                alternative_item_ids=candidates, is_gap=False,
                inclusion_reason=CareInclusionReason.BALANCED_NO_ESTABLISHED_USE,
                selection_basis=None,
            )
        active = True
        reason = CareInclusionReason.BALANCED_ESTABLISHED_USE
    else:
        active = True
        reason = CareInclusionReason.DETAILED_OWNED

    selected, basis = (
        _selection_with_preference(candidates, products, preferred_product_ids)
        if candidates else (None, None)
    )
    alternatives = tuple(item_id for item_id in candidates if item_id != selected)
    return CareSlotPlan(
        category=slot.category, slot=slot.key, required=slot.required, active=active,
        selected_item_id=selected, candidate_item_ids=candidates,
        alternative_item_ids=alternatives,
        is_gap=slot.required and selected is None,
        inclusion_reason=reason,
        selection_basis=basis,
    )


def plan_care_routine(context: CareContext, decisions: CareDecisionSet) -> CareRoutinePlan:
    """Build the deterministic minimum-effective plan for one account and day."""
    if context.account_id != decisions.account_id:
        raise ValueError("CareContext and CareDecisionSet account_id must match")
    if context.plan_date != decisions.plan_date:
        raise ValueError("CareContext and CareDecisionSet plan_date must match")

    effort, effort_source = _effort(context)
    products = {
        product.item.id: product
        for product in (*context.skin_products, *context.hair_products)
    }
    eligible: dict[str, list[uuid.UUID]] = {}
    for decision in decisions.product_decisions:
        if decision.eligible and decision.item_id in products and decision.slot is not None:
            eligible.setdefault(decision.slot, []).append(decision.item_id)
    canonical = {
        key: tuple(sorted(set(values), key=str))
        for key, values in eligible.items()
    }

    return CareRoutinePlan(
        plan_version=CARE_ROUTINE_PLAN_VERSION,
        account_id=context.account_id,
        plan_date=context.plan_date,
        resolved_effort=effort,
        effort_source=effort_source,
        skin_slots=tuple(_slot_plan(
            slot, effort=effort, eligible=canonical, products=products,
            preferred_product_ids=context.preferred_product_ids,
        ) for slot in SKIN_SLOTS),
        hair_slots=tuple(_slot_plan(
            slot, effort=effort, eligible=canonical, products=products,
            preferred_product_ids=context.preferred_product_ids,
        ) for slot in HAIR_SLOTS),
    )


def routine_plan_fingerprint(plan: CareRoutinePlan) -> str:
    """Hash only material plan fields, never unused context."""
    slots = []
    for row in (*plan.skin_slots, *plan.hair_slots):
        slots.append({
            "category": row.category,
            "slot": row.slot,
            "required": row.required,
            "active": row.active,
            "selected_item_id": str(row.selected_item_id) if row.selected_item_id else None,
            "candidate_item_ids": sorted(str(value) for value in row.candidate_item_ids),
            "alternative_item_ids": sorted(str(value) for value in row.alternative_item_ids),
            "is_gap": row.is_gap,
            "inclusion_reason": row.inclusion_reason.value,
            "selection_basis": row.selection_basis.value if row.selection_basis else None,
        })
    payload = {
        "plan_version": plan.plan_version,
        "plan_date": plan.plan_date.isoformat(),
        "resolved_effort": plan.resolved_effort.value,
        "effort_source": plan.effort_source.value,
        "slots": sorted(slots, key=lambda row: (row["category"], row["slot"])),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CARE_ROUTINE_PLAN_VERSION",
    "CareEffortSource",
    "CareInclusionReason",
    "CareRoutineEffort",
    "CareRoutinePlan",
    "CareSelectionBasis",
    "CareSlotPlan",
    "plan_care_routine",
    "routine_plan_fingerprint",
]
