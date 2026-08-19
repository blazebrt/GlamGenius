"""Pure deterministic assessment of a trusted prospective Care candidate.

This module compares a prospective candidate with the authoritative Care state
without making a purchase decision.  It deliberately has no database, HTTP,
AI, evidence, or entitlement dependencies.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.domains.care.decisions import CareDecisionSet, decision_fingerprint
from app.domains.care.routine_plan import CareRoutinePlan, routine_plan_fingerprint
from app.domains.care.schemas import CareContext
from app.domains.purchase.candidate_truth import CarePurchaseCandidateTruth
from app.domains.purchase.contract import (
    CARE_PURCHASE_ASSESSMENT_SCHEMA_VERSION,
    CARE_PURCHASE_ASSESSMENT_VERSION,
)
from app.domains.routines import rules
from app.domains.routines.ontology import (
    COMPATIBILITY_RULES,
    INGREDIENT_BY_KEY,
    SLOT_BY_KEY,
)

BOUNDARY = (
    "This assessment explains the candidate's role, owned alternatives and "
    "reviewed Care rules. It is informational context, not a purchase recommendation."
)


@dataclass(frozen=True, slots=True)
class CarePurchaseAssessment:
    assessment_version: str
    schema_version: str
    account_id: uuid.UUID
    candidate_id: uuid.UUID
    category: str
    customer_category_label: str
    plan_date: date
    candidate_truth_version: str
    care_context_version: str
    care_decision_version: str
    care_routine_plan_version: str
    identity_confidence: Mapping[str, Any]
    role_utility: Mapping[str, Any]
    redundancy: Mapping[str, Any]
    compatibility: Mapping[str, Any]
    user_constraints: Mapping[str, Any]
    same_slot_ingredient_overlap: tuple[Mapping[str, Any], ...]
    evidence_support: Mapping[str, Any]
    value_context: Mapping[str, Any]
    assessment_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "care_purchase_assessment_version": self.assessment_version,
            "care_purchase_assessment_schema_version": self.schema_version,
            "strategy": "care_purchase",
            "account_id": str(self.account_id),
            "candidate_id": str(self.candidate_id),
            "category": self.category,
            "category_label": self.customer_category_label,
            "plan_date": self.plan_date.isoformat(),
            "candidate_truth_version": self.candidate_truth_version,
            "care_context_version": self.care_context_version,
            "care_decision_version": self.care_decision_version,
            "care_routine_plan_version": self.care_routine_plan_version,
            "assessment_fingerprint": self.assessment_fingerprint,
            "dimensions": {
                "identity_confidence": dict(self.identity_confidence),
                "role_utility": dict(self.role_utility),
                "redundancy": dict(self.redundancy),
                "compatibility": dict(self.compatibility),
                "evidence_support": dict(self.evidence_support),
                "value_context": dict(self.value_context),
            },
            "user_constraints": dict(self.user_constraints),
            "same_slot_ingredient_overlap": [
                dict(row) for row in self.same_slot_ingredient_overlap
            ],
            "boundary": BOUNDARY,
        }


def _product_payload(product) -> dict[str, Any]:
    return {
        "owned_item_id": str(product.item.id),
        "display_name": product.item.display_name,
        "category": product.item.category,
        "slot": product.slot,
    }


def _slot_plan(plan: CareRoutinePlan, category: str, slot: str):
    rows = plan.skin_slots if category == "beauty" else plan.hair_slots
    return next((row for row in rows if row.slot == slot), None)


def _assessment_relevant_missing_information(
    truth: CarePurchaseCandidateTruth,
) -> tuple[str, ...]:
    """Keep only missing facts that can affect this assessment.

    Candidate Truth intentionally records marketing-purpose completeness.  That
    metadata is not an authority for Care role, safety, or compatibility and is
    therefore excluded here while every other missing-information marker stays
    visible and material.
    """
    return tuple(
        value for value in truth.missing_information if value != "purpose"
    )


def _owned_same_slot(
    truth: CarePurchaseCandidateTruth,
    context: CareContext,
    decisions: CareDecisionSet,
    plan: CareRoutinePlan,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Any]:
    slot = truth.care_slot
    if slot is None:
        return [], [], None
    products = {
        product.item.id: product
        for product in (*context.skin_products, *context.hair_products)
    }
    decision_rows = {
        row.item_id: row
        for row in decisions.product_decisions
        if row.category == truth.category and row.slot == slot
    }
    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for item_id, decision in sorted(decision_rows.items(), key=lambda pair: str(pair[0])):
        product = products.get(item_id)
        if product is None:
            continue
        if decision.eligible:
            eligible.append(_product_payload(product))
        else:
            blocked.append({
                **_product_payload(product),
                "reason_codes": sorted(reason.code.value for reason in decision.blocking_reasons),
            })
    return eligible, blocked, _slot_plan(plan, truth.category, slot)


def _overlap(
    truth: CarePurchaseCandidateTruth,
    context: CareContext,
    eligible_same_slot: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    keys = set(truth.recognised_ingredient_keys)
    if not keys:
        return ()
    products = {
        product.item.id: product
        for product in (*context.skin_products, *context.hair_products)
    }
    found: dict[str, set[str]] = {}
    for row in eligible_same_slot:
        product = products.get(uuid.UUID(row["owned_item_id"]))
        if product is None:
            continue
        for ingredient in product.ingredients:
            if ingredient.key in keys and not ingredient.needs_confirmation:
                found.setdefault(ingredient.key, set()).add(str(product.item.id))
    return tuple(
        {
            "ingredient_key": key,
            "display_name": INGREDIENT_BY_KEY[key].display_name,
            "owned_item_ids": sorted(item_ids),
        }
        for key, item_ids in sorted(found.items())
    )


def _user_constraints(
    truth: CarePurchaseCandidateTruth, context: CareContext
) -> dict[str, Any]:
    if not truth.recognised_ingredient_keys:
        return {
            "status": "insufficient_ingredient_information",
            "matched_ingredient_keys": [],
            "reason_codes": ["candidate_ingredients_not_recognised"],
        }
    declared = rules.declared_allergy_ingredient_keys(context.allergies)
    matched = sorted(set(truth.recognised_ingredient_keys) & declared)
    if matched:
        return {
            "status": "confirmed_user_constraint_match",
            "matched_ingredient_keys": matched,
            "message": "You told GlamGenius to avoid this ingredient.",
        }
    return {
        "status": "no_match_on_recognised_ingredients",
        "matched_ingredient_keys": [],
        "recognised_ingredient_keys": sorted(truth.recognised_ingredient_keys),
    }


def _compatibility(
    truth: CarePurchaseCandidateTruth,
    context: CareContext,
    plan: CareRoutinePlan,
) -> dict[str, Any]:
    keys = set(truth.recognised_ingredient_keys)
    if not keys or not truth.recognised_ingredient_families:
        return {
            "status": "insufficient_ingredient_information",
            "findings": [],
            "compared_owned_item_ids": [],
            "coverage": "insufficient",
        }
    products = {
        product.item.id: product
        for product in (*context.skin_products, *context.hair_products)
    }
    selected_ids = sorted(
        {
            row.selected_item_id
            for row in (*plan.skin_slots, *plan.hair_slots)
            if row.category == truth.category and row.selected_item_id is not None
        },
        key=str,
    )
    selected = [products[item_id] for item_id in selected_ids if item_id in products]
    coverage = "partial" if any(
        value.startswith("unrecognised_ingredient:")
        for value in truth.missing_information
    ) else "recognised_ingredients_only"
    if not selected:
        return {
            "status": "no_selected_owned_products_to_compare",
            "findings": [],
            "compared_owned_item_ids": [],
            "coverage": coverage,
        }
    candidate_families = set(truth.recognised_ingredient_families)
    findings: list[dict[str, Any]] = []
    for product in selected:
        for rule in COMPATIBILITY_RULES:
            if not rules.compatibility_rule_applies_to_families(
                rule, candidate_families, product.confirmed_families
            ):
                continue
            findings.append({
                "rule_id": rule.rule_id,
                "severity": rule.severity,
                "headline": rule.headline,
                "guidance": rule.guidance,
                "owned_item_id": str(product.item.id),
                "owned_item_display_name": product.item.display_name,
                "owned_item_slot": product.slot,
            })
    findings.sort(key=lambda row: (row["rule_id"], row["owned_item_id"]))
    return {
        "status": "reviewed_rule_matches" if findings else "no_reviewed_rule_match_on_recognised_ingredients",
        "findings": findings,
        "compared_owned_item_ids": [str(item_id) for item_id in selected_ids],
        "coverage": coverage,
    }


def _fingerprint_material(
    truth: CarePurchaseCandidateTruth,
    context: CareContext,
    decisions: CareDecisionSet,
    plan: CareRoutinePlan,
    identity_status: str,
    missing_information: tuple[str, ...],
    role_utility: Mapping[str, Any],
    redundancy: Mapping[str, Any],
    constraints: Mapping[str, Any],
    compatibility: Mapping[str, Any],
    overlap: tuple[Mapping[str, Any], ...],
) -> str:
    payload = {
        "assessment_version": CARE_PURCHASE_ASSESSMENT_VERSION,
        "candidate_id": str(truth.candidate_id),
        "candidate_truth_version": truth.truth_version,
        "candidate_ingredients": {
            "keys": sorted(truth.recognised_ingredient_keys),
            "families": sorted(truth.recognised_ingredient_families),
        },
        "care_slot": truth.care_slot,
        "verification_state": truth.verification_state,
        "identity_confidence": {
            "status": identity_status,
            "missing_information": sorted(missing_information),
        },
        "plan_date": context.plan_date.isoformat(),
        "care_context_version": context.context_version,
        "care_decision_fingerprint": decision_fingerprint(decisions),
        "care_routine_plan_fingerprint": routine_plan_fingerprint(plan),
        "role_status": role_utility.get("status"),
        "role_required": role_utility.get("required"),
        "redundancy": {
            "status": redundancy.get("status"),
            "selected_owned_item_id": redundancy.get("selected_owned_item_id"),
            "eligible": sorted(row["owned_item_id"] for row in redundancy.get("eligible_owned_same_slot", [])),
            "blocked": [
                {
                    "owned_item_id": row["owned_item_id"],
                    "reason_codes": sorted(row.get("reason_codes", [])),
                }
                for row in sorted(
                    redundancy.get("blocked_owned_same_slot", []),
                    key=lambda row: row["owned_item_id"],
                )
            ],
        },
        "user_constraints": {
            "status": constraints.get("status"),
            "matched": sorted(constraints.get("matched_ingredient_keys", [])),
        },
        "compatibility": {
            "status": compatibility.get("status"),
            "coverage": compatibility.get("coverage"),
            "findings": [
                {
                    "rule_id": row["rule_id"],
                    "severity": row["severity"],
                    "owned_item_id": row["owned_item_id"],
                }
                for row in sorted(
                    compatibility.get("findings", []),
                    key=lambda row: (row["rule_id"], row["owned_item_id"]),
                )
            ],
            "compared_owned_item_ids": sorted(compatibility.get("compared_owned_item_ids", [])),
        },
        "same_slot_ingredient_overlap": [
            {
                "ingredient_key": row["ingredient_key"],
                "owned_item_ids": sorted(row.get("owned_item_ids", [])),
            }
            for row in sorted(
                overlap,
                key=lambda row: row["ingredient_key"],
            )
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def assess_care_purchase(
    truth: CarePurchaseCandidateTruth,
    context: CareContext,
    decisions: CareDecisionSet,
    plan: CareRoutinePlan,
) -> CarePurchaseAssessment:
    """Assess one trusted candidate beside authoritative owned Care state."""
    if not truth.facts_trusted:
        raise ValueError("Only trusted Care candidate facts can be assessed.")
    if truth.category not in {"beauty", "hair"}:
        raise ValueError("Care purchase assessment category must be beauty or hair.")
    if truth.care_slot is not None:
        slot = SLOT_BY_KEY.get(truth.care_slot)
        if slot is None or slot.category != truth.category:
            raise ValueError("Care candidate slot does not match its category.")
    if context.account_id != decisions.account_id or context.account_id != plan.account_id:
        raise ValueError("Care assessment authorities must share one account.")
    if context.plan_date != decisions.plan_date or context.plan_date != plan.plan_date:
        raise ValueError("Care assessment authorities must share one plan date.")

    missing_information = _assessment_relevant_missing_information(truth)
    identity_status = (
        "trusted_with_missing_information"
        if missing_information
        else "trusted"
    )
    identity = {
        "status": identity_status,
        "verification_state": truth.verification_state,
        "facts_trusted": True,
        "missing_information": list(missing_information),
    }
    eligible, blocked, slot_plan = _owned_same_slot(truth, context, decisions, plan)
    if truth.care_slot is None or slot_plan is None:
        role = {
            "status": "role_unresolved",
            "care_slot": truth.care_slot,
            "required": False,
        }
        redundancy = {
            "status": "role_unresolved",
            "selected_owned_item_id": None,
            "eligible_owned_same_slot": [],
            "blocked_owned_same_slot": [],
            "eligible_owned_same_slot_count": 0,
        }
    else:
        role_status = (
            "addresses_required_gap"
            if slot_plan.required and slot_plan.is_gap
            else "required_role_already_covered"
            if slot_plan.required
            else "optional_role_not_required"
        )
        role = {
            "status": role_status,
            "care_slot": truth.care_slot,
            "required": slot_plan.required,
            "is_gap": slot_plan.is_gap,
        }
        count = len(eligible)
        redundancy = {
            "status": (
                "none_eligible_owned_same_slot" if count == 0
                else "one_eligible_owned_same_slot" if count == 1
                else "multiple_eligible_owned_same_slot"
            ),
            "selected_owned_item_id": str(slot_plan.selected_item_id) if slot_plan.selected_item_id else None,
            "eligible_owned_same_slot": eligible,
            "blocked_owned_same_slot": blocked,
            "eligible_owned_same_slot_count": count,
        }
    overlap = _overlap(truth, context, eligible)
    constraints = _user_constraints(truth, context)
    compatibility = _compatibility(truth, context, plan)
    fingerprint = _fingerprint_material(
        truth,
        context,
        decisions,
        plan,
        identity_status,
        missing_information,
        role,
        redundancy,
        constraints,
        compatibility,
        overlap,
    )
    return CarePurchaseAssessment(
        assessment_version=CARE_PURCHASE_ASSESSMENT_VERSION,
        schema_version=CARE_PURCHASE_ASSESSMENT_SCHEMA_VERSION,
        account_id=context.account_id,
        candidate_id=truth.candidate_id,
        category=truth.category,
        customer_category_label=truth.customer_category_label,
        plan_date=context.plan_date,
        candidate_truth_version=truth.truth_version,
        care_context_version=context.context_version,
        care_decision_version=decisions.decision_version,
        care_routine_plan_version=plan.plan_version,
        identity_confidence=identity,
        role_utility=role,
        redundancy=redundancy,
        compatibility=compatibility,
        user_constraints=constraints,
        same_slot_ingredient_overlap=overlap,
        evidence_support={
            "status": "not_assessed",
            "reason_codes": ["care_purchase_evidence_not_assessed"],
        },
        value_context={
            "status": "not_assessed",
            "reason_codes": ["care_purchase_value_not_assessed"],
        },
        assessment_fingerprint=fingerprint,
    )


__all__ = ["BOUNDARY", "CarePurchaseAssessment", "assess_care_purchase"]
