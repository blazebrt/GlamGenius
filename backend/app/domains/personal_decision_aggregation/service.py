"""Step 8D: gather Step 8C's reviewed directions without deciding anything.

Step 8C answers, for one exact claim version, which direction a reviewer
allowed it to contribute. That leaves a scattered set of answers across
ingredients and claims. Step 8D gathers them into one shape: which distinct
reviewed rules are represented, where each was encountered, which claims got
no mapping at all, and whether both directions appear.

The temptation this module exists to refuse is arithmetic. Ten supporting
rules against one cautionary rule looks like an answer, and it is not one: the
number of published claims about a substance reflects how much research
happened to be done and how many rules happened to be reviewed, not how
strongly anything acts on a person. So the direction summary is computed from
*set membership* over distinct rules -- never a tally, a majority, a ratio, a
net, or a winner. Both directions present means both directions present.

The second refusal is subtler. ``COMPLETE_SEMANTIC_MAPPING`` says every claim
projection Step 8C produced has a reviewed mapping. It does not say the
formula was fully read, that identities resolved, that enough evidence exists,
or that anything may be told to the customer. Step 8D can only see what
Step 8C emitted; a snapshot where nothing resolved and no claim applied
produces zero projections, and zero projections are not a clean bill of
health. That is why the entire Step 8C result is carried through untouched on
``source_semantics``: the epistemic state a real decision needs lives there,
and a later governed policy layer must read it rather than trusting this
layer's structural summary.

What this module must never grow into: a score, a weight, a rank, a
confidence number, a conflict resolution, a recommendation, or a product
verdict. Static tests enforce the vocabulary as well as the behaviour.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.domains.personal_decision_aggregation.enums import (
    PersonalSemanticMappingCoverage,
    PersonalSignalSet,
)
from app.domains.personal_decision_semantics import (
    ClaimDecisionSemanticProjection,
    IngredientDecisionSemantics,
    LabelSnapshotPersonalDecisionSemantics,
    PersonalDecisionSemanticStatus,
    PersonalDecisionSignal,
)

#: (rule_id, rule_version) -- the identity a reviewed rule aggregates under.
RuleIdentity = tuple[str, str]

#: (substance_key, claim_key, claim_version) -- the evidence a rule may match.
#: Category is not part of this: one Step 8C result carries exactly one.
RuleEvidenceTarget = tuple[str, str, int]


class PersonalDecisionAggregationInvariantError(ValueError):
    """The upstream object is not a shape a valid Step 8C could produce.

    Step 8C's own frozen dataclasses already reject most of these, so reaching
    this error means the object was assembled or mutated outside that path.
    Aggregating it anyway would launder corrupted provenance into something
    that reads like a reviewed result, so it fails closed instead.
    """


#: Every direction summary Step 8D may produce, keyed by the exact set of
#: directions present among distinct rules. A lookup rather than a comparison
#: chain, so there is nowhere for a tie-break or a preference to be inserted.
_SIGNAL_SETS: dict[frozenset[PersonalDecisionSignal], PersonalSignalSet] = {
    frozenset(): PersonalSignalSet.NONE,
    frozenset({PersonalDecisionSignal.SUPPORTING}): PersonalSignalSet.SUPPORTING_ONLY,
    frozenset({PersonalDecisionSignal.CAUTIONARY}): PersonalSignalSet.CAUTIONARY_ONLY,
    frozenset(
        {PersonalDecisionSignal.SUPPORTING, PersonalDecisionSignal.CAUTIONARY}
    ): PersonalSignalSet.MIXED,
}


@dataclass(frozen=True, slots=True)
class PersonalDecisionSignalOccurrence:
    """Where one reviewed rule was encountered. Provenance only.

    An occurrence exists so a later layer can say *where* a rule came from.
    It is never a unit of strength: three occurrences of one rule are three
    places to point at, not three times the direction.
    """

    ingredient_position: int
    substance_key: str
    claim_id: uuid.UUID
    claim_key: str
    claim_version: int


@dataclass(frozen=True, slots=True)
class UnmappedPersonalDecisionClaim:
    """One Step 8C claim projection that no reviewed rule covers.

    Kept visible on purpose. A gap in reviewed mappings is a fact about what
    has been reviewed, and hiding it would let a partial picture read as a
    whole one. Claim prose, evidence strength and sources are deliberately
    absent: Step 8D has no business seeing them.
    """

    ingredient_position: int
    substance_key: str
    claim_id: uuid.UUID
    claim_key: str
    claim_version: int


@dataclass(frozen=True, slots=True)
class AggregatedPersonalDecisionRule:
    """One distinct reviewed rule, with every place it was encountered.

    Identity is ``(rule_id, rule_version)``. Two versions of one rule id are
    two distinct rules: Step 8D has no recency policy and never decides that a
    later version supersedes an earlier one.

    The evidence target is carried because Step 8C's registry guarantees one
    rule identity maps to exactly one target; carrying it lets that guarantee
    be checked here rather than assumed.
    """

    rule_id: str
    rule_version: str
    signal: PersonalDecisionSignal
    substance_key: str
    claim_key: str
    claim_version: int
    occurrences: tuple[PersonalDecisionSignalOccurrence, ...]


@dataclass(frozen=True, slots=True)
class PersonalDecisionAggregation:
    """Step 8C's directions, gathered. Not a decision about anything.

    ``source_semantics`` is the exact object that was passed in -- the same
    instance, not a copy or a rebuild. Everything Step 8D declines to
    interpret (provenance, category, formula status, profile version, context
    status, handoff, identity states, ambiguity candidates) stays reachable
    through it, so a later policy layer inherits the full epistemic picture
    instead of this layer's summary of it.
    """

    source_semantics: LabelSnapshotPersonalDecisionSemantics
    mapping_coverage: PersonalSemanticMappingCoverage
    signal_set: PersonalSignalSet
    rules: tuple[AggregatedPersonalDecisionRule, ...]
    unmapped_claims: tuple[UnmappedPersonalDecisionClaim, ...]


@dataclass(slots=True)
class _RuleInProgress:
    """Mutable scratch for one rule identity. Never returned or exposed."""

    signal: PersonalDecisionSignal
    substance_key: str
    claim_key: str
    claim_version: int
    occurrences: list[PersonalDecisionSignalOccurrence] = field(default_factory=list)

    @property
    def target(self) -> RuleEvidenceTarget:
        return (self.substance_key, self.claim_key, self.claim_version)


def _substance_key_of(ingredient: IngredientDecisionSemantics) -> str:
    """The ingredient's resolved key, which any projection here implies.

    Step 8C emits no claim projection for an ingredient without a resolved
    substance key, so a projection sitting on one is a corrupted object rather
    than an unmapped case.
    """
    substance_key = ingredient.substance_key
    if substance_key is None:
        raise PersonalDecisionAggregationInvariantError(
            f"ingredient at position {ingredient.position} carries claim projections "
            "without a resolved substance key"
        )
    return substance_key


def _absorb_mapped(
    rules_in_progress: dict[RuleIdentity, _RuleInProgress],
    ingredient: IngredientDecisionSemantics,
    projection: ClaimDecisionSemanticProjection,
) -> None:
    rule_id = projection.rule_id
    rule_version = projection.rule_version
    signal = projection.signal
    where = f"claim {projection.claim_key} v{projection.claim_version}"

    if rule_id is None or rule_version is None:
        raise PersonalDecisionAggregationInvariantError(
            f"mapped {where} is missing its reviewed rule identity"
        )
    if not isinstance(signal, PersonalDecisionSignal):
        raise PersonalDecisionAggregationInvariantError(
            f"mapped {where} is missing a reviewed direction"
        )

    substance_key = _substance_key_of(ingredient)
    occurrence = PersonalDecisionSignalOccurrence(
        ingredient_position=ingredient.position,
        substance_key=substance_key,
        claim_id=projection.claim_id,
        claim_key=projection.claim_key,
        claim_version=projection.claim_version,
    )

    identity = (rule_id, rule_version)
    in_progress = rules_in_progress.get(identity)
    if in_progress is None:
        rules_in_progress[identity] = _RuleInProgress(
            signal=signal,
            substance_key=substance_key,
            claim_key=projection.claim_key,
            claim_version=projection.claim_version,
            occurrences=[occurrence],
        )
        return

    # One reviewed rule identity carries one reviewed direction against one
    # evidence identity. Either mismatch means two different reviewed things
    # are wearing the same name, and merging them would erase that.
    if in_progress.signal is not signal:
        raise PersonalDecisionAggregationInvariantError(
            f"rule {rule_id}@{rule_version} carries two different reviewed directions"
        )
    if in_progress.target != (substance_key, projection.claim_key, projection.claim_version):
        raise PersonalDecisionAggregationInvariantError(
            f"rule {rule_id}@{rule_version} targets two different evidence identities"
        )

    in_progress.occurrences.append(occurrence)


def _absorb_unmapped(
    ingredient: IngredientDecisionSemantics,
    projection: ClaimDecisionSemanticProjection,
) -> UnmappedPersonalDecisionClaim:
    where = f"claim {projection.claim_key} v{projection.claim_version}"
    if (
        projection.rule_id is not None
        or projection.rule_version is not None
        or projection.signal is not None
    ):
        raise PersonalDecisionAggregationInvariantError(
            f"unmapped {where} carries reviewed rule provenance"
        )
    return UnmappedPersonalDecisionClaim(
        ingredient_position=ingredient.position,
        substance_key=_substance_key_of(ingredient),
        claim_id=projection.claim_id,
        claim_key=projection.claim_key,
        claim_version=projection.claim_version,
    )


def _coverage(
    *, any_projection: bool, mapped_seen: bool, unmapped_seen: bool
) -> PersonalSemanticMappingCoverage:
    """Structural booleans only -- no fraction, threshold or percentage.

    One unmapped claim among a hundred mapped ones is PARTIAL. There is no
    level of coverage at which the remainder stops mattering.
    """
    if not any_projection:
        return PersonalSemanticMappingCoverage.NO_CLAIM_PROJECTIONS
    if unmapped_seen and not mapped_seen:
        return PersonalSemanticMappingCoverage.NO_MAPPED_SEMANTICS
    if mapped_seen and unmapped_seen:
        return PersonalSemanticMappingCoverage.PARTIAL_SEMANTIC_MAPPING
    return PersonalSemanticMappingCoverage.COMPLETE_SEMANTIC_MAPPING


def _signal_set(rules: tuple[AggregatedPersonalDecisionRule, ...]) -> PersonalSignalSet:
    present = frozenset(rule.signal for rule in rules)
    summary = _SIGNAL_SETS.get(present)
    if summary is None:
        raise PersonalDecisionAggregationInvariantError(
            "aggregated rules carry a direction outside the reviewed vocabulary"
        )
    return summary


def aggregate_personal_decision_signals(
    semantics: LabelSnapshotPersonalDecisionSemantics,
) -> PersonalDecisionAggregation:
    """Gather one exact Step 8C result into distinct rules and coverage.

    Pure and synchronous: no session, no account, no snapshot, no category
    argument, no safety input, no registry, and no query of any kind. The
    Step 8C result is the complete input, and it is returned untouched on
    ``source_semantics``.

    Traversal follows Step 8C's own order -- ingredients, then claims within
    each ingredient. Distinct rules come back in the order their identity was
    first encountered, occurrences and unmapped claims in encounter order.
    Nothing is sorted, ranked, deduplicated across identities, or reordered.
    """
    rules_in_progress: dict[RuleIdentity, _RuleInProgress] = {}
    unmapped_claims: list[UnmappedPersonalDecisionClaim] = []
    any_projection = False
    mapped_seen = False
    unmapped_seen = False

    for ingredient in semantics.ingredients:
        for projection in ingredient.claims:
            any_projection = True
            status = projection.status
            if status is PersonalDecisionSemanticStatus.SEMANTICS_AVAILABLE:
                mapped_seen = True
                _absorb_mapped(rules_in_progress, ingredient, projection)
            elif status is PersonalDecisionSemanticStatus.NOT_ENOUGH_DECISION_SEMANTICS:
                unmapped_seen = True
                unmapped_claims.append(_absorb_unmapped(ingredient, projection))
            else:
                raise PersonalDecisionAggregationInvariantError(
                    f"claim {projection.claim_key} v{projection.claim_version} carries an "
                    "unrecognised semantic status"
                )

    rules = tuple(
        AggregatedPersonalDecisionRule(
            rule_id=rule_id,
            rule_version=rule_version,
            signal=in_progress.signal,
            substance_key=in_progress.substance_key,
            claim_key=in_progress.claim_key,
            claim_version=in_progress.claim_version,
            occurrences=tuple(in_progress.occurrences),
        )
        for (rule_id, rule_version), in_progress in rules_in_progress.items()
    )

    return PersonalDecisionAggregation(
        source_semantics=semantics,
        mapping_coverage=_coverage(
            any_projection=any_projection,
            mapped_seen=mapped_seen,
            unmapped_seen=unmapped_seen,
        ),
        signal_set=_signal_set(rules),
        rules=rules,
        unmapped_claims=tuple(unmapped_claims),
    )
