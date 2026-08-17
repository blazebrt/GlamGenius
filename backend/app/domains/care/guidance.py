"""Evidence-gated, deterministic, advice-only Skin and Hair guidance."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care.evidence_applicability import resolve_care_evidence_applicability
from app.domains.care.guidance_rules import CARE_GUIDANCE_RULESET_VERSION, GUIDANCE_RULES, CareGuidanceRule
from app.domains.care.routine_plan import CareRoutinePlan
from app.domains.care.schemas import CareContext
from app.domains.evidence.service import assess_rule_evidence

CARE_GUIDANCE_VERSION = "v3-03.17"


@dataclass(frozen=True, slots=True)
class CareGuidanceItem:
    domain: str
    rule_id: str
    rule_version: str
    priority: int
    title: str
    body: str
    trigger_codes: tuple[str, ...]
    evidence_claim_ids: tuple[uuid.UUID, ...]
    evidence_applicability_version: str


@dataclass(frozen=True, slots=True)
class CareGuidanceSet:
    guidance_version: str
    ruleset_version: str
    items: tuple[CareGuidanceItem, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "items",
            tuple(sorted(self.items, key=lambda item: (item.priority, item.rule_id))),
        )

    @property
    def fingerprint(self) -> str:
        return guidance_fingerprint(self)

    def as_payload(self) -> dict[str, Any]:
        return {
            "guidance_version": self.guidance_version,
            "ruleset_version": self.ruleset_version,
            "fingerprint": self.fingerprint,
            "items": [
                {
                    "domain": item.domain,
                    "rule_id": item.rule_id,
                    "rule_version": item.rule_version,
                    "priority": item.priority,
                    "title": item.title,
                    "body": item.body,
                    "trigger_codes": list(item.trigger_codes),
                    "evidence_claim_ids": [str(claim_id) for claim_id in item.evidence_claim_ids],
                    "evidence_applicability_version": item.evidence_applicability_version,
                }
                for item in self.items
            ],
        }

    def audit_payload(self) -> dict[str, Any]:
        payload = self.as_payload()
        for item in payload["items"]:
            item.pop("priority", None)
            item.pop("title", None)
            item.pop("body", None)
        return payload


def guidance_fingerprint(guidance: CareGuidanceSet) -> str:
    material = {
        "guidance_version": guidance.guidance_version,
        "ruleset_version": guidance.ruleset_version,
        "items": [
            {
                "domain": item.domain,
                "rule_id": item.rule_id,
                "rule_version": item.rule_version,
                "priority": item.priority,
                "title": item.title,
                "body": item.body,
                "trigger_codes": list(item.trigger_codes),
                "evidence_claim_ids": sorted(str(claim_id) for claim_id in item.evidence_claim_ids),
                "evidence_applicability_version": item.evidence_applicability_version,
            }
            for item in guidance.items
        ],
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fact_value(context: CareContext, area: str, key: str) -> Any:
    facts = context.skin_facts if area == "skin" else context.hair_facts
    fact = facts.get(key)
    return fact.value if fact is not None and not fact.explicit_unknown else None


def _planned_moisturiser_exists(plan: CareRoutinePlan) -> bool:
    return any(
        row.slot == "moisturiser"
        and row.active
        and row.selected_item_id is not None
        and not row.is_gap
        for row in plan.skin_slots
    )


def _trigger(rule: CareGuidanceRule, context: CareContext, plan: CareRoutinePlan) -> tuple[bool, tuple[str, ...]]:
    if rule.rule_id == "care.skin.uv_protection_uvi_3":
        value = context.environment.uv_index
        return value is not None and value >= 3, ("uv_index_at_or_above_3",)
    if rule.rule_id == "care.skin.dry_air_moisture_support":
        return (
            _fact_value(context, "skin", "care_skin_usual_feel") == "often_dry_or_tight"
            and context.environment.moisture_regime == "dry"
            and _planned_moisturiser_exists(plan),
            (
                "user_reports_dry_or_tight_skin",
                "observed_dry_air",
                "owned_planned_moisturiser",
            ),
        )
    if rule.rule_id == "care.hair.frequent_heat_styling_protection":
        return _fact_value(context, "hair", "care_heat_styling_frequency") in {"frequent", "daily"}, (
            "user_reports_frequent_heat_styling",
        )
    raise ValueError(f"unknown V3-03.17 guidance rule {rule.rule_id}")


async def build_care_guidance(
    session: AsyncSession,
    *,
    care_context: CareContext,
    care_plan: CareRoutinePlan,
) -> CareGuidanceSet:
    items: list[CareGuidanceItem] = []
    for rule in GUIDANCE_RULES:
        triggered, trigger_codes = _trigger(rule, care_context, care_plan)
        if not triggered:
            continue
        assessment = await assess_rule_evidence(
            session,
            domain=rule.domain,
            rule_kind=rule.rule_kind,
            rule_id=rule.rule_id,
            rule_version=rule.rule_version,
        )
        if not assessment.behavior_evidence_eligible:
            continue
        applicability = resolve_care_evidence_applicability(assessment, rule.applicability_signals)
        if not applicability.applicable:
            continue
        items.append(
            CareGuidanceItem(
                domain=rule.domain,
                rule_id=rule.rule_id,
                rule_version=rule.rule_version,
                priority=rule.priority,
                title=rule.title,
                body=rule.body,
                trigger_codes=trigger_codes,
                evidence_claim_ids=applicability.matching_claim_ids,
                evidence_applicability_version=applicability.applicability_version,
            )
        )
    return CareGuidanceSet(
        guidance_version=CARE_GUIDANCE_VERSION,
        ruleset_version=CARE_GUIDANCE_RULESET_VERSION,
        items=tuple(items[:3]),
    )


__all__ = [
    "CARE_GUIDANCE_VERSION",
    "CareGuidanceItem",
    "CareGuidanceSet",
    "build_care_guidance",
    "guidance_fingerprint",
]
