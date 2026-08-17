"""Deterministic, evidence-gated, technique-only Home Care projection."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care.cadence import HairWashCadenceDecision, HairWashCadenceStatus
from app.domains.care.evidence_applicability import resolve_care_evidence_applicability
from app.domains.care.home_care_rules import HOME_CARE_RULES, HOME_CARE_RULESET_VERSION, HOME_CARE_VERSION, HomeCareRule
from app.domains.care.schemas import CareContext
from app.domains.evidence.service import assess_rule_evidence


@dataclass(frozen=True, slots=True)
class HomeCareItem:
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
class HomeCareSet:
    home_care_version: str
    ruleset_version: str
    items: tuple[HomeCareItem, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(sorted(self.items, key=lambda row: (row.priority, row.rule_id))))

    @property
    def fingerprint(self) -> str:
        return home_care_fingerprint(self)

    def as_payload(self) -> dict[str, Any]:
        return {
            "home_care_version": self.home_care_version,
            "ruleset_version": self.ruleset_version,
            "fingerprint": self.fingerprint,
            "items": [
                {
                    "domain": row.domain, "rule_id": row.rule_id,
                    "rule_version": row.rule_version, "priority": row.priority,
                    "title": row.title, "body": row.body,
                    "trigger_codes": list(row.trigger_codes),
                    "evidence_claim_ids": [str(value) for value in row.evidence_claim_ids],
                    "evidence_applicability_version": row.evidence_applicability_version,
                } for row in self.items
            ],
        }

    def audit_payload(self) -> dict[str, Any]:
        payload = self.as_payload()
        for item in payload["items"]:
            item.pop("priority", None)
            item.pop("title", None)
            item.pop("body", None)
        return payload


def home_care_fingerprint(home_care: HomeCareSet) -> str:
    material = {
        "home_care_version": home_care.home_care_version,
        "ruleset_version": home_care.ruleset_version,
        "items": [
            {
                "domain": row.domain, "rule_id": row.rule_id,
                "rule_version": row.rule_version, "priority": row.priority,
                "title": row.title, "body": row.body,
                "trigger_codes": list(row.trigger_codes),
                "evidence_claim_ids": sorted(str(value) for value in row.evidence_claim_ids),
                "evidence_applicability_version": row.evidence_applicability_version,
            } for row in home_care.items
        ],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _trigger(rule: HomeCareRule, context: CareContext, cadence: HairWashCadenceDecision) -> tuple[bool, tuple[str, ...]]:
    if rule.rule_id == "care.home.skin_gentle_bathing":
        fact = context.skin_facts.get("care_skin_usual_feel")
        return (
            fact is not None and not fact.explicit_unknown and fact.value == "often_dry_or_tight"
            and context.environment.moisture_regime == "dry",
            ("user_reports_dry_or_tight_skin", "observed_dry_air"),
        )
    if rule.rule_id == "care.home.hair_gentle_drying":
        return cadence.status is HairWashCadenceStatus.DUE, ("hair_wash_due_today",)
    raise ValueError(f"unknown V3-03.18 Home Care rule {rule.rule_id}")


async def build_home_care(
    session: AsyncSession,
    *,
    care_context: CareContext,
    hair_wash_cadence: HairWashCadenceDecision,
) -> HomeCareSet:
    items: list[HomeCareItem] = []
    for rule in HOME_CARE_RULES:
        triggered, trigger_codes = _trigger(rule, care_context, hair_wash_cadence)
        if not triggered:
            continue
        assessment = await assess_rule_evidence(
            session, domain=rule.domain, rule_kind=rule.rule_kind,
            rule_id=rule.rule_id, rule_version=rule.rule_version,
        )
        if not assessment.behavior_evidence_eligible:
            continue
        applicability = resolve_care_evidence_applicability(assessment, rule.applicability_signals)
        if not applicability.applicable:
            continue
        items.append(HomeCareItem(
            domain=rule.domain, rule_id=rule.rule_id, rule_version=rule.rule_version,
            priority=rule.priority, title=rule.title, body=rule.body,
            trigger_codes=trigger_codes, evidence_claim_ids=applicability.matching_claim_ids,
            evidence_applicability_version=applicability.applicability_version,
        ))
    return HomeCareSet(HOME_CARE_VERSION, HOME_CARE_RULESET_VERSION, tuple(items[:2]))


__all__ = ["HOME_CARE_VERSION", "HomeCareItem", "HomeCareSet", "build_home_care", "home_care_fingerprint"]
