"""Pure, deterministic audit snapshots for Care routine generation.

The snapshot is deliberately a projection of the already assembled Care
contracts.  It does not query the database, call providers, or consult an AI
model.  ``RoutineRecommendationRun.inputs['care_snapshot']`` is its durable
home; current Routine rows remain mutable rendering state.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any

from app.domains.care.decisions import CareDecisionSet, decision_fingerprint
from app.domains.care.routine_plan import CareRoutinePlan, routine_plan_fingerprint
from app.domains.care.schemas import CareContext, CareFact
from app.domains.routines.compiler import CompiledRoutine

CARE_RECOMMENDATION_SNAPSHOT_VERSION = "v3-03.12"


def _primitive(value: Any) -> Any:
    """Convert supported contract values into JSON-compatible primitives."""
    if isinstance(value, Enum):
        return _primitive(value.value)
    if isinstance(value, (uuid.UUID, date, datetime)):
        return value.isoformat() if not isinstance(value, uuid.UUID) else str(value)
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return [_primitive(item) for item in sorted(value, key=lambda item: str(item))]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_primitive(item) for item in value]
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return _primitive(value.as_dict())
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return {
            key: _primitive(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return value


def _fact_payload(fact: CareFact) -> dict[str, Any]:
    return {
        "key": fact.key,
        "value": _primitive(fact.value),
        "fact_source": fact.fact_source,
        "record_source": fact.record_source,
        "confidence": fact.confidence,
        "verification_state": fact.verification_state,
        "profile_attribute_id": _primitive(fact.profile_attribute_id),
        "explicit_unknown": fact.explicit_unknown,
    }


def _facts_payload(facts: Mapping[str, CareFact]) -> list[dict[str, Any]]:
    return [
        _fact_payload(facts[key])
        for key in sorted(facts)
    ]


def _product_payload(product: Any) -> dict[str, Any]:
    item = product.item
    return {
        "item_id": _primitive(item.id),
        "category": item.category,
        "slot": product.slot,
        "display_name": item.display_name,
        "usage_count": item.usage_count,
        "last_used_at": _primitive(item.last_used_at),
        "effective_expiry": _primitive(product.effective_expiry),
        "low_use": product.low_use,
    }


def _ingredient_payload(product: Any) -> list[dict[str, Any]]:
    rows = [
        {
            "key": ingredient.key,
            "family": ingredient.family,
            "confidence": ingredient.confidence,
            "source": ingredient.source,
            "needs_confirmation": ingredient.needs_confirmation,
        }
        for ingredient in product.ingredients
    ]
    return sorted(rows, key=lambda row: (row["key"], row["source"]))


def _reason_payload(reason: Any) -> dict[str, str]:
    return {"code": reason.code.value, "authority": reason.authority.value}


def _decision_payload(decisions: CareDecisionSet) -> dict[str, Any]:
    products = []
    for row in sorted(decisions.product_decisions, key=lambda item: (item.category, item.slot or "", str(item.item_id))):
        products.append({
            "item_id": str(row.item_id),
            "category": row.category,
            "slot": row.slot,
            "eligible": row.eligible,
            "blocking_reasons": sorted(
                (_reason_payload(reason) for reason in row.blocking_reasons),
                key=lambda reason: (reason["code"], reason["authority"]),
            ),
            "advisory_reasons": sorted(
                (_reason_payload(reason) for reason in row.advisory_reasons),
                key=lambda reason: (reason["code"], reason["authority"]),
            ),
        })

    def core(rows: Sequence[Any]) -> list[dict[str, Any]]:
        return [
            {
                "category": row.category,
                "slot": row.slot,
                "filled": row.filled,
                "eligible_item_ids": sorted(str(item_id) for item_id in row.eligible_item_ids),
                "blocked_item_ids": sorted(str(item_id) for item_id in row.blocked_item_ids),
            }
            for row in sorted(rows, key=lambda item: (item.category, item.slot))
        ]

    return {
        "decision_fingerprint": decision_fingerprint(decisions),
        "product_decisions": products,
        "skin_core_slots": core(decisions.skin_core_slots),
        "hair_core_slots": core(decisions.hair_core_slots),
    }


def _slot_payload(row: Any) -> dict[str, Any]:
    return {
        "category": row.category,
        "slot": row.slot,
        "required": row.required,
        "active": row.active,
        "selected_item_id": _primitive(row.selected_item_id),
        "candidate_item_ids": sorted(str(item_id) for item_id in row.candidate_item_ids),
        "alternative_item_ids": sorted(str(item_id) for item_id in row.alternative_item_ids),
        "is_gap": row.is_gap,
        "inclusion_reason": row.inclusion_reason.value,
        "selection_basis": row.selection_basis.value if row.selection_basis else None,
    }


def _plan_payload(plan: CareRoutinePlan) -> dict[str, Any]:
    slots = sorted(
        (_slot_payload(row) for row in (*plan.skin_slots, *plan.hair_slots)),
        key=lambda row: (row["category"], row["slot"]),
    )
    return {
        "routine_plan_fingerprint": routine_plan_fingerprint(plan),
        "resolved_effort": plan.resolved_effort.value,
        "effort_source": plan.effort_source.value,
        "slots": slots,
    }


def _rendered_payload(compiled: Sequence[CompiledRoutine]) -> list[dict[str, Any]]:
    routines: list[dict[str, Any]] = []
    for routine in sorted(compiled, key=lambda row: row.kind):
        routines.append({
            "kind": routine.kind,
            "label": routine.label,
            "frequency": routine.frequency,
            "steps": [
                {
                    "slot": step.slot,
                    "order": step.order,
                    "required": step.required,
                    "inventory_item_id": step.item_id,
                    "is_gap": step.is_gap,
                }
                for step in sorted(routine.steps, key=lambda row: (row.order, row.slot))
            ],
            "findings": sorted(
                [
                    {
                        "rule_id": finding.rule_id,
                        "severity": finding.severity,
                        "item_ids": sorted(str(item_id) for item_id in finding.item_ids),
                        "slot": finding.slot,
                    }
                    for finding in routine.findings
                ],
                key=lambda row: (row["rule_id"], row["slot"] or "", tuple(row["item_ids"])),
            ),
            "climate_notes": _primitive(routine.climate_notes),
            "skipped_for_allergy": sorted(routine.skipped_for_allergy),
        })
    return routines


def _environment_payload(environment: Any) -> dict[str, Any]:
    fields = (
        "weather_snapshot_id", "air_quality_snapshot_id", "condition", "temp_min_c",
        "temp_max_c", "humidity", "precipitation_chance", "uv_index", "aqi",
        "aqi_index_system", "aqi_category", "climate_region", "calendar_prior", "season",
        "temperature_band", "moisture_regime", "daily_regime", "climate_confidence",
        "climate_reason", "weather_unavailable_reason",
    )
    return {field: _primitive(getattr(environment, field)) for field in fields}


def _event_payload(event: Any | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "id": _primitive(event.id),
        "starts_at": _primitive(event.starts_at),
        "ends_at": _primitive(event.ends_at),
        "all_day": event.all_day,
        "occasion_key": event.occasion_key,
        "confidence": event.confidence,
        "user_confirmed": event.user_confirmed,
    }


def build_care_recommendation_snapshot(
    *,
    care_context: CareContext,
    decisions: CareDecisionSet,
    care_plan: CareRoutinePlan,
    compiled_routines: Sequence[CompiledRoutine],
    requested_kinds: Sequence[str] | None,
    legacy_climate: str | None,
    routine_engine_version: str,
    ontology_version: str,
) -> dict[str, Any]:
    """Build the complete, immutable audit material for one generation."""
    contracts = (decisions, care_plan)
    if any(row.account_id != care_context.account_id for row in contracts):
        raise ValueError("Care snapshot contracts must share account_id")
    if any(row.plan_date != care_context.plan_date for row in contracts):
        raise ValueError("Care snapshot contracts must share plan_date")

    products = sorted(
        (*care_context.skin_products, *care_context.hair_products),
        key=lambda row: (row.item.category, row.slot or "", str(row.item.id)),
    )
    snapshot: dict[str, Any] = {
        "snapshot_version": CARE_RECOMMENDATION_SNAPSHOT_VERSION,
        "account_id": str(care_context.account_id),
        "plan_date": care_context.plan_date.isoformat(),
        "care_context_version": care_context.context_version,
        "care_decision_version": decisions.decision_version,
        "care_routine_plan_version": care_plan.plan_version,
        "routine_engine_version": routine_engine_version,
        "ontology_version": ontology_version,
        "skin_facts": _facts_payload(care_context.skin_facts),
        "hair_facts": _facts_payload(care_context.hair_facts),
        "preferences": _facts_payload(care_context.preferences),
        "allergies": sorted(care_context.allergies, key=lambda value: str(value).casefold()),
        "missing_information": [
            {"area": row.area, "key": row.key, "reason": row.reason}
            for row in sorted(care_context.missing_information, key=lambda row: (row.area, row.key, row.reason))
        ],
        "environment": _environment_payload(care_context.environment),
        "primary_event": _event_payload(care_context.primary_event),
        "products": [_product_payload(product) for product in products],
        "ingredients": [
            {"item_id": str(product.item.id), "ingredients": _ingredient_payload(product)}
            for product in products
        ],
        "decisions": _decision_payload(decisions),
        "routine_plan": _plan_payload(care_plan),
        "rendered_routines": _rendered_payload(compiled_routines),
        "requested_kinds": sorted({str(kind) for kind in (requested_kinds or ())}),
        "legacy_climate": legacy_climate,
        "product_preferences": {
            "paused_product_ids": sorted(str(item_id) for item_id in care_context.paused_product_ids),
            "preferred_product_ids": sorted(str(item_id) for item_id in care_context.preferred_product_ids),
        },
    }
    snapshot["fingerprint"] = care_recommendation_snapshot_fingerprint(snapshot)
    return snapshot


def care_recommendation_snapshot_fingerprint(snapshot: Mapping[str, Any]) -> str:
    """Hash every snapshot field except ``fingerprint`` itself."""
    payload = {key: value for key, value in snapshot.items() if key != "fingerprint"}
    canonical = json.dumps(_primitive(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "CARE_RECOMMENDATION_SNAPSHOT_VERSION",
    "build_care_recommendation_snapshot",
    "care_recommendation_snapshot_fingerprint",
]
