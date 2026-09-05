"""Step 8C: exact claim versions to reviewed decision direction, and nothing else.

Step 8B answers which reviewed published claims apply to this user. It
deliberately stops there, because an applicable claim does not say which way it
should push a later decision. Reading that direction out of the claim's own
prose, its evidence strength, or how many claims there are would be inventing
policy from data that was never reviewed for policy. Step 8C is the seam that
makes that impossible: direction comes from an explicit reviewed rule keyed to
an exact claim version, or it does not come at all.

What this module must never grow into:

- reading ``summary`` or ``scope`` to guess a direction. It does not touch
  those fields at all, and a static test enforces that.
- turning STRONG/MODERATE/LIMITED into weights or points.
- counting supporting against cautionary, or deciding which wins. Both are
  returned, independently, for a later governed layer to combine.
- treating ingredient position as concentration, dose or importance.

Pass-through metadata (provenance, context status, handoff, identity status) is
annotated as ``object`` on purpose. Those values belong to Step 8A, Step 7C and
the product domain, and Step 8C carries them without inspecting them; typing
them loosely is what lets the dependency boundary be absolute, with no
runtime import of any domain other than Step 8B.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domains.personal_applicability import (
    IngredientPersonalApplicability,
    LabelSnapshotPersonalApplicability,
    PersonalApplicabilityCategory,
    PersonalApplicabilityStatus,
)
from app.domains.personal_decision_semantics.enums import (
    PersonalDecisionSemanticStatus,
    PersonalDecisionSignal,
)
from app.domains.personal_decision_semantics.rules import (
    PERSONAL_DECISION_SEMANTIC_RULES,
    PersonalDecisionSemanticRule,
    RuleTarget,
    build_rule_index,
)

#: Step 8A's handoff status, compared by value so this module imports nothing
#: from personal_lens. Step 8A owns the safety boundary; Step 8C only obeys it.
_HANDOFF_STATUS_VALUE = "handoff_required"

#: Identity states whose claims must never be looked at. Step 8B already
#: returns no claims for them; the guard is explicit so a future change to
#: Step 8B cannot quietly let ambiguous candidates reach a rule lookup.
_NO_SEMANTICS_STATUSES = frozenset({
    PersonalApplicabilityStatus.IDENTITY_UNRESOLVED,
    PersonalApplicabilityStatus.IDENTITY_AMBIGUOUS,
})


@dataclass(frozen=True, slots=True)
class ClaimDecisionSemanticProjection:
    """What a reviewed rule says about one already-applicable claim."""

    claim_id: uuid.UUID
    claim_key: str
    claim_version: int
    status: PersonalDecisionSemanticStatus
    rule_id: str | None
    rule_version: str | None
    signal: PersonalDecisionSignal | None

    def __post_init__(self) -> None:
        mapped = self.status is PersonalDecisionSemanticStatus.SEMANTICS_AVAILABLE
        provided = (self.rule_id, self.rule_version, self.signal)
        if mapped and any(part is None for part in provided):
            raise ValueError("SEMANTICS_AVAILABLE requires rule_id, rule_version and signal")
        if not mapped and any(part is not None for part in provided):
            raise ValueError("NOT_ENOUGH_DECISION_SEMANTICS must carry no rule and no signal")


@dataclass(frozen=True, slots=True)
class IngredientDecisionSemantics:
    """One Step 8B ingredient, with its claims mapped where a rule exists."""

    position: int
    raw_name: str
    normalized_name: str | None
    identity_status: object
    substance_key: str | None
    entity_kind: str | None
    candidate_substance_keys: tuple[str, ...]
    personal_applicability_status: PersonalApplicabilityStatus
    claims: tuple[ClaimDecisionSemanticProjection, ...]


@dataclass(frozen=True, slots=True)
class LabelSnapshotPersonalDecisionSemantics:
    """Step 8B's result, carried forward with reviewed directions attached."""

    provenance: object | None
    category: PersonalApplicabilityCategory
    formula_status: str | None
    profile_id: uuid.UUID | None
    profile_version: int | None
    context_status: object
    ingredients: tuple[IngredientDecisionSemantics, ...]
    handoff: object | None


def _project_claims(
    ingredient: IngredientPersonalApplicability,
    category: PersonalApplicabilityCategory,
    index: Mapping[RuleTarget, PersonalDecisionSemanticRule],
) -> tuple[ClaimDecisionSemanticProjection, ...]:
    if ingredient.personal_applicability_status in _NO_SEMANTICS_STATUSES:
        return ()
    substance_key = ingredient.substance_key
    if substance_key is None:
        return ()

    projections: list[ClaimDecisionSemanticProjection] = []
    for claim in ingredient.claims:
        target = (str(category), substance_key, claim.claim_key, claim.claim_version)
        rule = index.get(target)
        if rule is None:
            projections.append(
                ClaimDecisionSemanticProjection(
                    claim_id=claim.claim_id,
                    claim_key=claim.claim_key,
                    claim_version=claim.claim_version,
                    status=PersonalDecisionSemanticStatus.NOT_ENOUGH_DECISION_SEMANTICS,
                    rule_id=None,
                    rule_version=None,
                    signal=None,
                )
            )
            continue
        projections.append(
            ClaimDecisionSemanticProjection(
                claim_id=claim.claim_id,
                claim_key=claim.claim_key,
                claim_version=claim.claim_version,
                status=PersonalDecisionSemanticStatus.SEMANTICS_AVAILABLE,
                rule_id=rule.rule_id,
                rule_version=rule.rule_version,
                signal=rule.signal,
            )
        )
    return tuple(projections)


def project_personal_decision_semantics(
    personal_applicability: LabelSnapshotPersonalApplicability,
    *,
    rules: Iterable[PersonalDecisionSemanticRule] = PERSONAL_DECISION_SEMANTIC_RULES,
) -> LabelSnapshotPersonalDecisionSemantics:
    """Attach reviewed decision directions to an existing Step 8B result.

    Pure and synchronous: no session, no account, no snapshot, no safety input,
    and no query of any kind. Everything it needs has already been decided
    upstream. ``rules`` exists so tests can inject synthetic rules without
    touching the production registry, which stays empty.

    Ingredient order, duplicate ingredients and claim order are preserved
    exactly. Nothing is deduplicated, reordered, counted or combined.
    """
    index = build_rule_index(rules)

    if personal_applicability.handoff is not None or (
        str(personal_applicability.context_status) == _HANDOFF_STATUS_VALUE
    ):
        # Step 8A stopped this request. Carry the handoff and match nothing.
        return LabelSnapshotPersonalDecisionSemantics(
            provenance=personal_applicability.provenance,
            category=personal_applicability.category,
            formula_status=personal_applicability.formula_status,
            profile_id=personal_applicability.profile_id,
            profile_version=personal_applicability.profile_version,
            context_status=personal_applicability.context_status,
            ingredients=(),
            handoff=personal_applicability.handoff,
        )

    ingredients = tuple(
        IngredientDecisionSemantics(
            position=ingredient.position,
            raw_name=ingredient.raw_name,
            normalized_name=ingredient.normalized_name,
            identity_status=ingredient.identity_status,
            substance_key=ingredient.substance_key,
            entity_kind=ingredient.entity_kind,
            candidate_substance_keys=ingredient.candidate_substance_keys,
            personal_applicability_status=ingredient.personal_applicability_status,
            claims=_project_claims(ingredient, personal_applicability.category, index),
        )
        for ingredient in personal_applicability.ingredients
    )

    return LabelSnapshotPersonalDecisionSemantics(
        provenance=personal_applicability.provenance,
        category=personal_applicability.category,
        formula_status=personal_applicability.formula_status,
        profile_id=personal_applicability.profile_id,
        profile_version=personal_applicability.profile_version,
        context_status=personal_applicability.context_status,
        ingredients=ingredients,
        handoff=personal_applicability.handoff,
    )
