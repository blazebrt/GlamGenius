"""Pure deterministic Care safety decisions for V3-03.2.

This module deliberately sits after :mod:`care.service` and before any future
Care behaviour.  It consumes the already assembled immutable ``CareContext``
and does not know about SQLAlchemy, providers, HTTP, AI, or Evidence.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.domains.care.schemas import CareContext
from app.domains.routines import rules as routine_rules
from app.domains.routines.ontology import HAIR_SLOTS, SKIN_SLOTS
from app.domains.routines.rules import ShelfProduct

CARE_DECISION_VERSION = "v3-03.11"


class CareDecisionReasonCode(StrEnum):
    PRODUCT_EXPIRED = "product_expired"
    CONFIRMED_ALLERGY_MATCH = "confirmed_allergy_match"
    INGREDIENT_CONFIRMATION_NEEDED = "ingredient_confirmation_needed"
    PRODUCT_EXPIRING_SOON = "product_expiring_soon"
    USER_PAUSED_FOR_ROUTINE = "user_paused_for_routine"


class CareDecisionAuthority(StrEnum):
    USER_CONSTRAINT = "user_constraint"
    SYSTEM_POLICY = "system_policy"
    LEGACY_CURATED_RULE = "legacy_curated_rule"


@dataclass(frozen=True, slots=True)
class CareDecisionReason:
    code: CareDecisionReasonCode
    authority: CareDecisionAuthority


@dataclass(frozen=True, slots=True)
class ProductCareDecision:
    item_id: uuid.UUID
    category: str
    slot: str | None
    eligible: bool
    blocking_reasons: tuple[CareDecisionReason, ...]
    advisory_reasons: tuple[CareDecisionReason, ...]


@dataclass(frozen=True, slots=True)
class CoreSlotDecision:
    category: str
    slot: str
    filled: bool
    eligible_item_ids: tuple[uuid.UUID, ...]
    blocked_item_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class CareDecisionSet:
    decision_version: str
    account_id: uuid.UUID
    plan_date: date
    product_decisions: tuple[ProductCareDecision, ...]
    skin_core_slots: tuple[CoreSlotDecision, ...]
    hair_core_slots: tuple[CoreSlotDecision, ...]

    @property
    def blocked_product_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(row.item_id for row in self.product_decisions if not row.eligible)

    @property
    def eligible_product_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(row.item_id for row in self.product_decisions if row.eligible)

    @property
    def skin_core_gap_count(self) -> int:
        return sum(not row.filled for row in self.skin_core_slots)

    @property
    def hair_core_gap_count(self) -> int:
        return sum(not row.filled for row in self.hair_core_slots)


def _reason(code: CareDecisionReasonCode, authority: CareDecisionAuthority) -> CareDecisionReason:
    return CareDecisionReason(code=code, authority=authority)


def _product_decision(
    product: ShelfProduct,
    context: CareContext,
) -> ProductCareDecision:
    blocking: list[CareDecisionReason] = []
    advisory: list[CareDecisionReason] = []

    days_to_expiry = product.days_to_expiry(context.plan_date)
    if days_to_expiry is not None and days_to_expiry < 0:
        blocking.append(_reason(CareDecisionReasonCode.PRODUCT_EXPIRED, CareDecisionAuthority.SYSTEM_POLICY))
    elif days_to_expiry is not None and days_to_expiry <= routine_rules.EXPIRING_SOON_DAYS:
        advisory.append(_reason(CareDecisionReasonCode.PRODUCT_EXPIRING_SOON, CareDecisionAuthority.SYSTEM_POLICY))

    match = routine_rules.allergy_product_matches((product,), context.allergies)[0]
    if match.confirmed_ingredient_keys:
        blocking.append(_reason(CareDecisionReasonCode.CONFIRMED_ALLERGY_MATCH, CareDecisionAuthority.USER_CONSTRAINT))
    if match.unconfirmed_ingredient_keys:
        advisory.append(_reason(CareDecisionReasonCode.INGREDIENT_CONFIRMATION_NEEDED, CareDecisionAuthority.USER_CONSTRAINT))
    if product.item.id in context.paused_product_ids:
        blocking.append(_reason(CareDecisionReasonCode.USER_PAUSED_FOR_ROUTINE, CareDecisionAuthority.USER_CONSTRAINT))

    return ProductCareDecision(
        item_id=product.item.id,
        category=product.item.category,
        slot=product.slot,
        eligible=not blocking,
        blocking_reasons=tuple(blocking),
        advisory_reasons=tuple(advisory),
    )


def _core_slots(
    slots,
    decisions: tuple[ProductCareDecision, ...],
) -> tuple[CoreSlotDecision, ...]:
    output: list[CoreSlotDecision] = []
    for slot in slots:
        if not slot.required:
            continue
        candidates = [
            row for row in decisions
            if row.category == slot.category and row.slot == slot.key
        ]
        eligible_ids = tuple(row.item_id for row in candidates if row.eligible)
        blocked_ids = tuple(row.item_id for row in candidates if not row.eligible)
        output.append(CoreSlotDecision(
            category=slot.category,
            slot=slot.key,
            filled=bool(eligible_ids),
            eligible_item_ids=eligible_ids,
            blocked_item_ids=blocked_ids,
        ))
    return tuple(output)


def evaluate_care_context(context: CareContext) -> CareDecisionSet:
    """Evaluate only V3-03.2 hard safety and required-slot facts."""
    products = tuple(sorted(
        (*context.skin_products, *context.hair_products),
        key=lambda row: (row.item.category, row.slot or "", str(row.item.id)),
    ))
    decisions = tuple(_product_decision(product, context) for product in products)
    return CareDecisionSet(
        decision_version=CARE_DECISION_VERSION,
        account_id=context.account_id,
        plan_date=context.plan_date,
        product_decisions=decisions,
        skin_core_slots=_core_slots(SKIN_SLOTS, decisions),
        hair_core_slots=_core_slots(HAIR_SLOTS, decisions),
    )


def decision_fingerprint(decisions: CareDecisionSet) -> str:
    """Hash the material, deterministic Care decision fields only."""
    def reason_payload(reason: CareDecisionReason) -> dict[str, str]:
        return {"code": reason.code.value, "authority": reason.authority.value}

    payload = {
        "decision_version": decisions.decision_version,
        "plan_date": decisions.plan_date.isoformat(),
        "products": [
            {
                "item_id": str(row.item_id),
                "eligible": row.eligible,
                "blocking_reasons": [reason_payload(reason) for reason in row.blocking_reasons],
                "advisory_reasons": [reason_payload(reason) for reason in row.advisory_reasons],
            }
            for row in sorted(decisions.product_decisions, key=lambda row: str(row.item_id))
        ],
        "skin_core_slots": [
            {
                "category": row.category,
                "slot": row.slot,
                "filled": row.filled,
                "eligible_item_ids": sorted(str(value) for value in row.eligible_item_ids),
                "blocked_item_ids": sorted(str(value) for value in row.blocked_item_ids),
            }
            for row in sorted(decisions.skin_core_slots, key=lambda row: (row.category, row.slot))
        ],
        "hair_core_slots": [
            {
                "category": row.category,
                "slot": row.slot,
                "filled": row.filled,
                "eligible_item_ids": sorted(str(value) for value in row.eligible_item_ids),
                "blocked_item_ids": sorted(str(value) for value in row.blocked_item_ids),
            }
            for row in sorted(decisions.hair_core_slots, key=lambda row: (row.category, row.slot))
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CARE_DECISION_VERSION",
    "CareDecisionAuthority",
    "CareDecisionReason",
    "CareDecisionReasonCode",
    "CareDecisionSet",
    "CoreSlotDecision",
    "ProductCareDecision",
    "decision_fingerprint",
    "evaluate_care_context",
]
