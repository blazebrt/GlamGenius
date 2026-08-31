"""Which grading rules are allowed to speak to a customer.

``food_reference.py`` is a *candidate* catalogue. Its thresholds, additive
tiers and citations were assembled as authoring input, and a value sitting in a
Python constant has been reviewed by nobody. The evidence domain is where a
value becomes something we are willing to say out loud, and it has a lifecycle:

    candidate  ->  evidence draft  ->  approved  ->  publication verified
               ->  published       ->  production ruleset

This module is the last arrow. It asks the evidence domain which grading rules
have actually completed that path, and hands back a ruleset that the customer
path consults before letting a rule lower somebody's grade.

The distinction it protects is narrow and important: a candidate constant and a
published rule may hold the identical number, and are still not the same claim.
One is a note somebody typed; the other has a named reviewer, a source that was
opened and read, and a version that can be cited when a customer disputes the
result. Treating them as interchangeable is how an unreviewed number ends up
presented as a fact about somebody's food.

Nothing here marks the existing constants as verified. They stay candidates
until a person walks them through the authoring tool.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from app.domains.evidence.enums import EvidenceDomain, ReviewStatus, RuleKind
from app.domains.evidence.models import EvidenceClaim
from app.domains.evidence.service import assess_rule_evidence
from app.domains.nutrition.food_reference import (
    FSA_FOP,
    FSSAI_ADDITIVES,
    FSSAI_LABELLING,
    FSSAI_TRANSFAT,
    ICMR_NIN_2024,
    MONTEIRO_NOVA_2019,
    Source,
)
from app.domains.nutrition.grading.engine import GradeResult
from app.domains.nutrition.grading.rules import GradeOutcome

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

#: The evidence coordinates every food grading rule is registered under.
FOOD_DOMAIN = EvidenceDomain.NUTRITION.value
FOOD_RULE_KIND = RuleKind.NUTRITION_CONTEXT.value

#: Bumped when a rule's meaning changes, so an old published claim does not
#: silently carry over to a rule that no longer says the same thing.
FOOD_RULE_VERSION = "v1"

#: How far a rule got. Only ``published`` may lower a customer's grade.
STATUS_PUBLISHED = "published"
STATUS_CANDIDATE = "candidate"


@dataclass(frozen=True)
class GradingRuleSpec:
    """One grading rule, and what it rests on while it is still a candidate.

    ``required`` marks a rule the verdict cannot be issued without. A missing
    optional rule can be dropped and the remaining answer is still true as far
    as it goes; a missing required one means we do not know enough to grade.
    """

    rule_id: str
    #: The candidate publication behind it. Not evidence — the thing a reviewer
    #: would open on the way to making it evidence.
    candidate_source: Source
    required: bool = False
    rule_version: str = FOOD_RULE_VERSION


#: Every rule that can lower a grade, and therefore every rule that owes the
#: customer a source. Informational trace steps are deliberately absent: they
#: change nothing and claim nothing.
GRADING_RULES: tuple[GradingRuleSpec, ...] = (
    GradingRuleSpec("grade.step1.nova", MONTEIRO_NOVA_2019, required=True),
    GradingRuleSpec("grade.step1.refined_grain", ICMR_NIN_2024),
    GradingRuleSpec("grade.step2.high_total_sugars", FSA_FOP, required=True),
    GradingRuleSpec("grade.step2.high_salt", FSA_FOP, required=True),
    GradingRuleSpec("grade.step2.high_saturated_fat", FSA_FOP, required=True),
    GradingRuleSpec("grade.step2.high_total_fat", FSA_FOP),
    GradingRuleSpec("grade.step2.high_sodium", FSA_FOP),
    GradingRuleSpec("grade.step2.sugar", FSA_FOP),
    GradingRuleSpec("grade.step2.added_sugar_dominates", FSSAI_LABELLING),
    GradingRuleSpec("grade.step2.partially_hydrogenated_oil", FSSAI_TRANSFAT, required=True),
    GradingRuleSpec("grade.step2.trans_fat_denominator_missing", FSSAI_TRANSFAT),
    GradingRuleSpec("grade.step3.black_tier", FSSAI_ADDITIVES, required=True),
    GradingRuleSpec("grade.step3.red_tier", FSSAI_ADDITIVES),
    GradingRuleSpec("grade.step3.child_marketed_synthetic_colour", FSSAI_ADDITIVES),
    GradingRuleSpec("grade.step4.percentage_not_declared", FSSAI_LABELLING),
    GradingRuleSpec("grade.step4.declared_percentage", FSSAI_LABELLING),
)

RULES_BY_ID: dict[str, GradingRuleSpec] = {rule.rule_id: rule for rule in GRADING_RULES}


@dataclass(frozen=True)
class RuleProvenance:
    """What a screen and an error report need to identify one decision basis."""

    rule_id: str
    rule_version: str
    status: str
    required: bool
    source: Source
    claim_ids: tuple[uuid.UUID, ...] = ()
    claim_version: int | None = None

    @property
    def published(self) -> bool:
        return self.status == STATUS_PUBLISHED

    def as_payload(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "status": self.status,
            "required": self.required,
            "evidence_claim_ids": [str(claim_id) for claim_id in self.claim_ids],
            "evidence_claim_version": self.claim_version,
            "source_id": self.source.identifier,
            "source_url": self.source.url,
        }


@dataclass(frozen=True)
class ProductionRuleset:
    """The rules that finished the lifecycle, and the ones that have not."""

    provenance: dict[str, RuleProvenance] = field(default_factory=dict)

    def for_rule(self, rule_id: str) -> RuleProvenance | None:
        """Provenance for one rule id, matching the longest registered prefix.

        Trace ids are more specific than rule ids — ``grade.step1.nova_group_4``
        belongs to ``grade.step1.nova`` — so the lookup walks prefixes rather
        than demanding an exact string.
        """
        if rule_id in self.provenance:
            return self.provenance[rule_id]
        candidates = [key for key in self.provenance if rule_id.startswith(key)]
        if not candidates:
            return None
        return self.provenance[max(candidates, key=len)]

    def is_published(self, rule_id: str) -> bool:
        found = self.for_rule(rule_id)
        return bool(found and found.published)

    @property
    def unpublished(self) -> tuple[str, ...]:
        """Every registered rule still resting on a candidate constant."""
        return tuple(sorted(
            rule_id for rule_id, row in self.provenance.items() if not row.published
        ))

    @property
    def unpublished_required(self) -> tuple[str, ...]:
        return tuple(sorted(
            rule_id for rule_id, row in self.provenance.items()
            if not row.published and row.required
        ))


def candidate_ruleset() -> ProductionRuleset:
    """The ruleset before any evidence lookup: everything a candidate.

    This is what a caller with no database gets. It is deliberately not an
    empty ruleset and deliberately not a published one — it states, for every
    rule, that the basis is an unreviewed constant.
    """
    return ProductionRuleset(provenance={
        rule.rule_id: RuleProvenance(
            rule_id=rule.rule_id,
            rule_version=rule.rule_version,
            status=STATUS_CANDIDATE,
            required=rule.required,
            source=rule.candidate_source,
        )
        for rule in GRADING_RULES
    })


async def resolve_production_ruleset(session: AsyncSession) -> ProductionRuleset:
    """Ask the evidence domain which grading rules may speak to a customer.

    Fail-closed throughout: a rule with no link, an incomplete support path, or
    a claim that has not reached the final ``published`` lifecycle stage stays
    a candidate. Behaviour eligibility deliberately permits approved claims in
    other domains; this production boundary is stricter and never promotes one
    of those interim claims into a customer-facing verdict rule.
    """
    resolved: dict[str, RuleProvenance] = {}
    for rule in GRADING_RULES:
        assessment = await assess_rule_evidence(
            session,
            domain=FOOD_DOMAIN,
            rule_kind=FOOD_RULE_KIND,
            rule_id=rule.rule_id,
            rule_version=rule.rule_version,
        )
        published_claims: list[EvidenceClaim] = []
        for path in assessment.behavior_eligible_paths:
            claim = await session.get(EvidenceClaim, path.claim_id)
            if claim is not None and claim.review_status == ReviewStatus.PUBLISHED.value:
                published_claims.append(claim)
        published_claims.sort(key=lambda claim: str(claim.id))
        published = bool(published_claims)
        resolved[rule.rule_id] = RuleProvenance(
            rule_id=rule.rule_id,
            rule_version=rule.rule_version,
            status=STATUS_PUBLISHED if published else STATUS_CANDIDATE,
            required=rule.required,
            source=rule.candidate_source,
            claim_ids=tuple(claim.id for claim in published_claims),
            claim_version=published_claims[0].claim_version if len(published_claims) == 1 else None,
        )
    return ProductionRuleset(provenance=resolved)


def enforce_published_required_rules(
    result: GradeResult, ruleset: ProductionRuleset,
) -> GradeResult:
    """Prevent candidate constants from producing a customer grade.

    The deterministic engine remains useful for authoring and review, but a
    production letter is only truthful when every required lowering rule has
    completed its evidence lifecycle.  This is intentionally an outcome
    boundary rather than a presentation hint: a candidate rule must not still
    decide D/E while merely being labelled ``candidate`` in the response.
    """
    missing_rules = ruleset.unpublished_required
    if result.outcome is not GradeOutcome.GRADED or not missing_rules:
        return result
    return replace(
        result,
        outcome=GradeOutcome.NOT_ENOUGH_INFORMATION,
        grade=None,
        ceiling=None,
        headline="Not enough published evidence to grade this.",
        detail=(
            "Required production grading rules are not yet published. "
            "We do not turn candidate reference constants into a customer grade."
        ),
        missing=tuple(sorted(set(result.missing).union(missing_rules))),
    )


__all__ = [
    "FOOD_DOMAIN",
    "FOOD_RULE_KIND",
    "FOOD_RULE_VERSION",
    "GRADING_RULES",
    "RULES_BY_ID",
    "STATUS_CANDIDATE",
    "STATUS_PUBLISHED",
    "GradingRuleSpec",
    "ProductionRuleset",
    "RuleProvenance",
    "candidate_ruleset",
    "enforce_published_required_rules",
    "resolve_production_ruleset",
]
