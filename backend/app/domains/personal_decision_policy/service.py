"""Step 8E: decide whether a decision is permitted, then look one up.

Everything above this layer has been careful not to decide anything. Step 8E
is where a product action could finally be emitted, so it is where the
strictest refusals live.

The central one is the difference between two kinds of "no":

- ``NOT_ENOUGH_INFORMATION`` means the system was not *entitled* to decide.
  Personal context was incomplete, the formula did not parse, or Step 8C left
  claims unmapped.
- ``WAIT`` means a reviewer looked at this exact governed state and decided
  waiting is the right product action.

Turning the first into the second is the most tempting mistake available
here, and the most damaging: it dresses an epistemic gap up as advice, and the
customer cannot tell the difference. So no gate in this module ever produces
an action, and no action exists that a reviewed policy rule did not specify.

The second refusal is inferring an action from a direction. There is no
mapping from ``SUPPORTING_ONLY`` to BUY anywhere in this code, and a static
test proves the action words are never even named outside their enum. A policy
matches the exact set of Step 8C rule identities and versions, so two products
whose evidence merely points the same way do not share a policy.

Evaluation order, and why:

1. **Hard handoff** -- before anything, including building the policy
   registry. A corrupt or conflicting registry must never be able to turn a
   safety handoff into an error.
2. **Category** -- converted by value into this domain's own vocabulary.
3. **Personal context completeness** -- only ``context_available`` proceeds.
4. **Formula projectability** -- only ``parsed`` proceeds.
5. **Step 8D structural validation, then mapping completeness.**
6. **Derive the exact policy target.**
7. **Build and validate the registry, then look up exactly.**

Registry construction sits last so that a registry problem cannot surface as
noise on a request where no decision was structurally permitted anyway.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.domains.personal_decision_aggregation import (
    PersonalDecisionAggregation,
    PersonalSemanticMappingCoverage,
    PersonalSignalSet,
)
from app.domains.personal_decision_policy.enums import (
    PersonalDecisionAction,
    PersonalDecisionPolicyCategory,
    PersonalDecisionPolicyReason,
    PersonalDecisionPolicyStatus,
)
from app.domains.personal_decision_policy.rules import (
    PERSONAL_DECISION_POLICY_RULES,
    PersonalDecisionPolicyRule,
    PolicyTarget,
    SemanticRuleIdentity,
    build_policy_index,
)

#: Step 8A's context vocabulary, compared by value so nothing is imported from
#: personal_lens. Step 8A owns these states; Step 8E only reads them.
_CONTEXT_HANDOFF = "handoff_required"
_CONTEXT_AVAILABLE = "context_available"
_KNOWN_CONTEXT_STATUSES = frozenset({
    _CONTEXT_AVAILABLE,
    "partial_context",
    "not_enough_personal_context",
    _CONTEXT_HANDOFF,
})

#: Step 7B's formula vocabulary, likewise compared by value.
_FORMULA_PARSED = "parsed"
_KNOWN_FORMULA_STATUSES = frozenset({
    _FORMULA_PARSED,
    "empty",
    "malformed",
    "ambiguous_boundary",
    "too_long",
    "too_many_items",
})

#: Step 8B's per-ingredient applicability vocabulary, compared by value.
_APPLICABILITY_EVIDENCE_GAP = "not_enough_information"
_APPLICABILITY_UNRESOLVED = "identity_unresolved"
_APPLICABILITY_AMBIGUOUS = "identity_ambiguous"
_KNOWN_APPLICABILITY_STATUSES = frozenset({
    "personal_evidence_available",
    _APPLICABILITY_EVIDENCE_GAP,
    _APPLICABILITY_UNRESOLVED,
    _APPLICABILITY_AMBIGUOUS,
})

#: Step 8C's direction vocabulary, compared by value.
_SIGNAL_SUPPORTING = "supporting"
_SIGNAL_CAUTIONARY = "cautionary"

#: The direction set implied by a set of exact reviewed directions. Used only
#: to verify that Step 8D's stored summary matches its own rules.
_SIGNAL_SETS: dict[frozenset[str], PersonalSignalSet] = {
    frozenset(): PersonalSignalSet.NONE,
    frozenset({_SIGNAL_SUPPORTING}): PersonalSignalSet.SUPPORTING_ONLY,
    frozenset({_SIGNAL_CAUTIONARY}): PersonalSignalSet.CAUTIONARY_ONLY,
    frozenset({_SIGNAL_SUPPORTING, _SIGNAL_CAUTIONARY}): PersonalSignalSet.MIXED,
}

#: Which Step 8D coverage states imply mapped rules and unmapped claims.
#: (expects_rules, expects_unmapped)
_COVERAGE_SHAPES: dict[PersonalSemanticMappingCoverage, tuple[bool, bool]] = {
    PersonalSemanticMappingCoverage.NO_CLAIM_PROJECTIONS: (False, False),
    PersonalSemanticMappingCoverage.NO_MAPPED_SEMANTICS: (False, True),
    PersonalSemanticMappingCoverage.PARTIAL_SEMANTIC_MAPPING: (True, True),
    PersonalSemanticMappingCoverage.COMPLETE_SEMANTIC_MAPPING: (True, False),
}

_BLOCKING_REASONS = frozenset({
    PersonalDecisionPolicyReason.PERSONAL_CONTEXT_NOT_COMPLETE,
    PersonalDecisionPolicyReason.FORMULA_NOT_PROJECTABLE,
    PersonalDecisionPolicyReason.SEMANTIC_MAPPING_NOT_COMPLETE,
})


class PersonalDecisionPolicyInvariantError(ValueError):
    """The upstream object is not a shape a valid Step 8D could produce.

    Reaching this means the aggregation was assembled or mutated outside the
    governed path. Deciding from it would launder a corrupted chain into a
    product action, so it fails closed instead.
    """


@dataclass(frozen=True, slots=True)
class PersonalDecisionPolicyResult:
    """Whether a decision was permitted, and the reviewed action if so.

    The shape is constrained so a contradictory result cannot exist: an action
    without a policy behind it, a decision that also carries a blocking
    reason, or a blocked state that smuggles an action through, all raise on
    construction rather than travelling onward.
    """

    source_aggregation: PersonalDecisionAggregation
    status: PersonalDecisionPolicyStatus
    reason: PersonalDecisionPolicyReason | None
    action: PersonalDecisionAction | None
    policy_id: str | None
    policy_version: str | None

    def __post_init__(self) -> None:
        decided = (self.action, self.policy_id, self.policy_version)

        if self.status is PersonalDecisionPolicyStatus.DECISION_AVAILABLE:
            if any(part is None for part in decided):
                raise ValueError(
                    "DECISION_AVAILABLE requires action, policy_id and policy_version"
                )
            if self.reason is not None:
                raise ValueError("DECISION_AVAILABLE carries no blocking reason")
            return

        if any(part is not None for part in decided):
            raise ValueError(f"{self.status} must carry no action and no policy provenance")

        if self.status is PersonalDecisionPolicyStatus.HANDOFF_REQUIRED:
            expected = PersonalDecisionPolicyReason.PROFESSIONAL_HANDOFF_REQUIRED
            if self.reason is not expected:
                raise ValueError("HANDOFF_REQUIRED requires PROFESSIONAL_HANDOFF_REQUIRED")
        elif self.status is PersonalDecisionPolicyStatus.NOT_ENOUGH_INFORMATION:
            if self.reason not in _BLOCKING_REASONS:
                raise ValueError("NOT_ENOUGH_INFORMATION requires a structural blocking reason")
        elif self.status is PersonalDecisionPolicyStatus.NOT_ENOUGH_DECISION_POLICY:
            expected = PersonalDecisionPolicyReason.NO_EXACT_POLICY_RULE
            if self.reason is not expected:
                raise ValueError("NOT_ENOUGH_DECISION_POLICY requires NO_EXACT_POLICY_RULE")
        else:
            raise ValueError(f"unrecognised policy status {self.status}")


def _blocked(
    aggregation: PersonalDecisionAggregation,
    status: PersonalDecisionPolicyStatus,
    reason: PersonalDecisionPolicyReason,
) -> PersonalDecisionPolicyResult:
    """A result that permits nothing. The only way to produce a non-decision."""
    return PersonalDecisionPolicyResult(
        source_aggregation=aggregation,
        status=status,
        reason=reason,
        action=None,
        policy_id=None,
        policy_version=None,
    )


def _category_of(source: object) -> PersonalDecisionPolicyCategory:
    raw = str(getattr(source, "category", None))
    try:
        return PersonalDecisionPolicyCategory(raw)
    except ValueError as error:
        raise PersonalDecisionPolicyInvariantError(
            f"upstream category {raw!r} is outside the governed policy vocabulary"
        ) from error


def _validate_structure(aggregation: PersonalDecisionAggregation) -> None:
    """Reject a Step 8D object whose own parts disagree with each other."""
    coverage = aggregation.mapping_coverage
    shape = _COVERAGE_SHAPES.get(coverage)
    if shape is None:
        raise PersonalDecisionPolicyInvariantError(
            f"upstream mapping coverage {coverage!r} is unrecognised"
        )

    expects_rules, expects_unmapped = shape
    if bool(aggregation.rules) is not expects_rules:
        raise PersonalDecisionPolicyInvariantError(
            f"{coverage} disagrees with the presence of aggregated rules"
        )
    if bool(aggregation.unmapped_claims) is not expects_unmapped:
        raise PersonalDecisionPolicyInvariantError(
            f"{coverage} disagrees with the presence of unmapped claims"
        )

    directions: set[str] = set()
    identities: set[SemanticRuleIdentity] = set()
    for rule in aggregation.rules:
        signal = str(rule.signal)
        if signal not in (_SIGNAL_SUPPORTING, _SIGNAL_CAUTIONARY):
            raise PersonalDecisionPolicyInvariantError(
                f"aggregated rule {rule.rule_id} carries direction {signal!r}, which is outside "
                "the reviewed vocabulary"
            )
        directions.add(signal)

        if not isinstance(rule.rule_id, str) or not rule.rule_id.strip():
            raise PersonalDecisionPolicyInvariantError("an aggregated rule has a blank rule id")
        if not isinstance(rule.rule_version, str) or not rule.rule_version.strip():
            raise PersonalDecisionPolicyInvariantError(
                f"aggregated rule {rule.rule_id} has a blank rule version"
            )

        identity = (rule.rule_id, rule.rule_version)
        if identity in identities:
            raise PersonalDecisionPolicyInvariantError(
                f"aggregated rule {rule.rule_id}@{rule.rule_version} appears more than once; "
                "Step 8D represents one reviewed rule exactly once"
            )
        identities.add(identity)

    expected_signal_set = _SIGNAL_SETS[frozenset(directions)]
    if aggregation.signal_set is not expected_signal_set:
        raise PersonalDecisionPolicyInvariantError(
            f"stored direction set {aggregation.signal_set} disagrees with the directions its "
            f"own rules carry ({expected_signal_set})"
        )


def _semantic_rule_identities(
    aggregation: PersonalDecisionAggregation,
) -> frozenset[SemanticRuleIdentity]:
    return frozenset((rule.rule_id, rule.rule_version) for rule in aggregation.rules)


def _gap_flags(source: object) -> tuple[bool, bool, bool]:
    """The three upstream epistemic gaps, read only from exact status values."""
    unresolved = False
    ambiguous = False
    evidence_gap = False

    for ingredient in getattr(source, "ingredients", ()):
        status = str(ingredient.personal_applicability_status)
        if status not in _KNOWN_APPLICABILITY_STATUSES:
            raise PersonalDecisionPolicyInvariantError(
                f"ingredient applicability status {status!r} is unrecognised"
            )
        if status == _APPLICABILITY_UNRESOLVED:
            unresolved = True
        elif status == _APPLICABILITY_AMBIGUOUS:
            ambiguous = True
        elif status == _APPLICABILITY_EVIDENCE_GAP:
            evidence_gap = True

    return unresolved, ambiguous, evidence_gap


def evaluate_personal_decision_policy(
    aggregation: PersonalDecisionAggregation,
    *,
    rules: Iterable[PersonalDecisionPolicyRule] = PERSONAL_DECISION_POLICY_RULES,
) -> PersonalDecisionPolicyResult:
    """Evaluate one exact Step 8D aggregation against reviewed policy.

    Pure and synchronous: no session, no account, no snapshot, no category
    argument, no personal-context argument, no formula argument, no safety
    input and no query of any kind. The Step 8D result is the complete input
    and is returned untouched on ``source_aggregation``.

    ``rules`` is a deterministic injection seam for tests. Production callers
    use the default registry, which is empty, so production emits no action.
    """
    source = aggregation.source_semantics

    # 1. Safety first, before the registry is even looked at. A conflicting or
    #    corrupt policy registry must never be able to suppress a handoff.
    if source.handoff is not None:
        return _blocked(
            aggregation,
            PersonalDecisionPolicyStatus.HANDOFF_REQUIRED,
            PersonalDecisionPolicyReason.PROFESSIONAL_HANDOFF_REQUIRED,
        )

    context_status = str(source.context_status)
    if context_status == _CONTEXT_HANDOFF:
        return _blocked(
            aggregation,
            PersonalDecisionPolicyStatus.HANDOFF_REQUIRED,
            PersonalDecisionPolicyReason.PROFESSIONAL_HANDOFF_REQUIRED,
        )
    if context_status not in _KNOWN_CONTEXT_STATUSES:
        raise PersonalDecisionPolicyInvariantError(
            f"upstream context status {context_status!r} is unrecognised"
        )

    category = _category_of(source)

    # 2. Personal context must be complete. Step 8B selects evidence from the
    #    trusted facts that happen to be present, so a partial context can
    #    hide an applicable path -- and a personalised action taken on it
    #    would be pretending to know the person better than we do.
    if context_status != _CONTEXT_AVAILABLE:
        return _blocked(
            aggregation,
            PersonalDecisionPolicyStatus.NOT_ENOUGH_INFORMATION,
            PersonalDecisionPolicyReason.PERSONAL_CONTEXT_NOT_COMPLETE,
        )

    # 3. The formula must have parsed. A parser failure is a gap in what we
    #    read off the label, never a product judgement.
    formula_status = source.formula_status
    if formula_status is not None:
        formula_value = str(formula_status)
        if formula_value not in _KNOWN_FORMULA_STATUSES:
            raise PersonalDecisionPolicyInvariantError(
                f"upstream formula status {formula_value!r} is unrecognised"
            )
    if formula_status is None or str(formula_status) != _FORMULA_PARSED:
        return _blocked(
            aggregation,
            PersonalDecisionPolicyStatus.NOT_ENOUGH_INFORMATION,
            PersonalDecisionPolicyReason.FORMULA_NOT_PROJECTABLE,
        )

    # 4. Step 8D must be internally consistent, and every claim it emitted
    #    must carry a reviewed mapping.
    _validate_structure(aggregation)
    if aggregation.mapping_coverage is not PersonalSemanticMappingCoverage.COMPLETE_SEMANTIC_MAPPING:
        return _blocked(
            aggregation,
            PersonalDecisionPolicyStatus.NOT_ENOUGH_INFORMATION,
            PersonalDecisionPolicyReason.SEMANTIC_MAPPING_NOT_COMPLETE,
        )

    # 5. Derive the exact governed state, then consult reviewed policy.
    unresolved, ambiguous, evidence_gap = _gap_flags(source)
    target: PolicyTarget = (
        category,
        _semantic_rule_identities(aggregation),
        aggregation.signal_set,
        unresolved,
        ambiguous,
        evidence_gap,
    )

    matched = build_policy_index(rules).get(target)
    if matched is None:
        return _blocked(
            aggregation,
            PersonalDecisionPolicyStatus.NOT_ENOUGH_DECISION_POLICY,
            PersonalDecisionPolicyReason.NO_EXACT_POLICY_RULE,
        )

    return PersonalDecisionPolicyResult(
        source_aggregation=aggregation,
        status=PersonalDecisionPolicyStatus.DECISION_AVAILABLE,
        reason=None,
        action=matched.action,
        policy_id=matched.policy_id,
        policy_version=matched.policy_version,
    )
