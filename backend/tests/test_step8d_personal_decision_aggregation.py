"""Step 8D — deterministic personal decision signal aggregation.

Every identity here is synthetic. The tests that matter most are the ones that
try to make the layer vote: ten rules on one side and one on the other, the
same rule seen three times, two versions of one rule disagreeing. Each of
those looks like a place where a reasonable person would want an answer, and
each must come back with both directions simply reported.

Corrupted upstream objects are built with ``object.__setattr__`` because
Step 8C's frozen dataclasses refuse to construct them normally. That is the
point: the only way to reach Step 8D's invariant errors is to assemble an
object no valid Step 8C ever produced, and Step 8D must still refuse it rather
than launder it into something that reads as reviewed.
"""

from __future__ import annotations

import ast
import inspect
import re
import uuid
from pathlib import Path

import pytest
from app.domains.personal_applicability import (
    PersonalApplicabilityCategory,
    PersonalApplicabilityStatus,
)
from app.domains.personal_decision_aggregation import (
    AggregatedPersonalDecisionRule,
    PersonalDecisionAggregation,
    PersonalDecisionAggregationInvariantError,
    PersonalDecisionSignalOccurrence,
    PersonalSemanticMappingCoverage,
    PersonalSignalSet,
    UnmappedPersonalDecisionClaim,
    aggregate_personal_decision_signals,
)
from app.domains.personal_decision_semantics import (
    ClaimDecisionSemanticProjection,
    IngredientDecisionSemantics,
    LabelSnapshotPersonalDecisionSemantics,
    PersonalDecisionSemanticStatus,
    PersonalDecisionSignal,
)
from app.domains.substance_interpretation import ProjectedIdentityStatus

SERVICE_PATH = Path(__file__).resolve().parents[1] / "app" / "domains" / "personal_decision_aggregation"

SUBSTANCE_A = "substance.synthetic.a"
SUBSTANCE_B = "substance.synthetic.b"
CLAIM_A = "claim.synthetic.a"
CLAIM_B = "claim.synthetic.b"
RULE_A = "rule.synthetic.a"
RULE_B = "rule.synthetic.b"

SUPPORTING = PersonalDecisionSignal.SUPPORTING
CAUTIONARY = PersonalDecisionSignal.CAUTIONARY
MAPPED = PersonalDecisionSemanticStatus.SEMANTICS_AVAILABLE
UNMAPPED = PersonalDecisionSemanticStatus.NOT_ENOUGH_DECISION_SEMANTICS


# ---------------------------------------------------------------------------
# Synthetic Step 8C fixtures
# ---------------------------------------------------------------------------


def _mapped(
    *,
    rule_id: str = RULE_A,
    rule_version: str = "1",
    signal: PersonalDecisionSignal = SUPPORTING,
    claim_key: str = CLAIM_A,
    claim_version: int = 2,
    claim_id: uuid.UUID | None = None,
) -> ClaimDecisionSemanticProjection:
    return ClaimDecisionSemanticProjection(
        claim_id=claim_id or uuid.uuid4(),
        claim_key=claim_key,
        claim_version=claim_version,
        status=MAPPED,
        rule_id=rule_id,
        rule_version=rule_version,
        signal=signal,
    )


def _unmapped(
    *,
    claim_key: str = CLAIM_B,
    claim_version: int = 1,
    claim_id: uuid.UUID | None = None,
) -> ClaimDecisionSemanticProjection:
    return ClaimDecisionSemanticProjection(
        claim_id=claim_id or uuid.uuid4(),
        claim_key=claim_key,
        claim_version=claim_version,
        status=UNMAPPED,
        rule_id=None,
        rule_version=None,
        signal=None,
    )


def _ingredient(
    *,
    position: int = 0,
    substance_key: str | None = SUBSTANCE_A,
    claims: tuple[ClaimDecisionSemanticProjection, ...] = (),
) -> IngredientDecisionSemantics:
    return IngredientDecisionSemantics(
        position=position,
        raw_name=f"Synthetic {position}",
        normalized_name=f"synthetic {position}",
        identity_status=ProjectedIdentityStatus.RESOLVED,
        substance_key=substance_key,
        entity_kind="substance" if substance_key else None,
        candidate_substance_keys=(),
        personal_applicability_status=PersonalApplicabilityStatus.PERSONAL_EVIDENCE_AVAILABLE,
        claims=claims,
    )


def _semantics(
    *,
    ingredients: tuple[IngredientDecisionSemantics, ...] = (),
    context_status: object = "context_available",
    handoff: object | None = None,
) -> LabelSnapshotPersonalDecisionSemantics:
    return LabelSnapshotPersonalDecisionSemantics(
        provenance=None,
        category=PersonalApplicabilityCategory.SKIN_CARE,
        formula_status="resolved",
        profile_id=uuid.uuid4(),
        profile_version=3,
        context_status=context_status,
        ingredients=ingredients,
        handoff=handoff,
    )


def _one(*claims: ClaimDecisionSemanticProjection) -> LabelSnapshotPersonalDecisionSemantics:
    """One ingredient carrying the given claim projections."""
    return _semantics(ingredients=(_ingredient(claims=claims),))


def _corrupt(obj: object, **fields: object) -> object:
    """Force fields past a frozen dataclass, to build impossible input."""
    for name, value in fields.items():
        object.__setattr__(obj, name, value)
    return obj


# ---------------------------------------------------------------------------
# Mapping coverage
# ---------------------------------------------------------------------------


class TestMappingCoverage:
    def test_no_claim_projections(self) -> None:
        result = aggregate_personal_decision_signals(_semantics())
        assert result.mapping_coverage is PersonalSemanticMappingCoverage.NO_CLAIM_PROJECTIONS
        assert result.signal_set is PersonalSignalSet.NONE
        assert result.rules == ()
        assert result.unmapped_claims == ()

    def test_ingredients_present_but_no_claims_is_still_no_projections(self) -> None:
        result = aggregate_personal_decision_signals(
            _semantics(ingredients=(_ingredient(position=0), _ingredient(position=1)))
        )
        assert result.mapping_coverage is PersonalSemanticMappingCoverage.NO_CLAIM_PROJECTIONS
        assert result.signal_set is PersonalSignalSet.NONE

    def test_all_projections_unmapped(self) -> None:
        result = aggregate_personal_decision_signals(
            _one(_unmapped(claim_key=CLAIM_A), _unmapped(claim_key=CLAIM_B))
        )
        assert result.mapping_coverage is PersonalSemanticMappingCoverage.NO_MAPPED_SEMANTICS
        assert result.signal_set is PersonalSignalSet.NONE
        assert result.rules == ()
        assert len(result.unmapped_claims) == 2

    def test_complete_supporting(self) -> None:
        result = aggregate_personal_decision_signals(
            _one(
                _mapped(rule_id=RULE_A, signal=SUPPORTING),
                _mapped(rule_id=RULE_B, signal=SUPPORTING, claim_key=CLAIM_B),
            )
        )
        assert result.mapping_coverage is PersonalSemanticMappingCoverage.COMPLETE_SEMANTIC_MAPPING
        assert result.signal_set is PersonalSignalSet.SUPPORTING_ONLY
        assert result.unmapped_claims == ()

    def test_complete_cautionary(self) -> None:
        result = aggregate_personal_decision_signals(
            _one(
                _mapped(rule_id=RULE_A, signal=CAUTIONARY),
                _mapped(rule_id=RULE_B, signal=CAUTIONARY, claim_key=CLAIM_B),
            )
        )
        assert result.mapping_coverage is PersonalSemanticMappingCoverage.COMPLETE_SEMANTIC_MAPPING
        assert result.signal_set is PersonalSignalSet.CAUTIONARY_ONLY

    def test_complete_mixed_has_no_winner(self) -> None:
        result = aggregate_personal_decision_signals(
            _one(
                _mapped(rule_id=RULE_A, signal=SUPPORTING),
                _mapped(rule_id=RULE_B, signal=CAUTIONARY, claim_key=CLAIM_B),
            )
        )
        assert result.mapping_coverage is PersonalSemanticMappingCoverage.COMPLETE_SEMANTIC_MAPPING
        assert result.signal_set is PersonalSignalSet.MIXED
        assert {rule.signal for rule in result.rules} == {SUPPORTING, CAUTIONARY}

    def test_partial_supporting(self) -> None:
        result = aggregate_personal_decision_signals(
            _one(_mapped(signal=SUPPORTING), _unmapped())
        )
        assert result.mapping_coverage is PersonalSemanticMappingCoverage.PARTIAL_SEMANTIC_MAPPING
        assert result.signal_set is PersonalSignalSet.SUPPORTING_ONLY

    def test_partial_cautionary(self) -> None:
        result = aggregate_personal_decision_signals(
            _one(_mapped(signal=CAUTIONARY), _unmapped())
        )
        assert result.mapping_coverage is PersonalSemanticMappingCoverage.PARTIAL_SEMANTIC_MAPPING
        assert result.signal_set is PersonalSignalSet.CAUTIONARY_ONLY

    def test_partial_mixed(self) -> None:
        result = aggregate_personal_decision_signals(
            _one(
                _mapped(rule_id=RULE_A, signal=SUPPORTING),
                _mapped(rule_id=RULE_B, signal=CAUTIONARY, claim_key=CLAIM_B),
                _unmapped(claim_key="claim.synthetic.c"),
            )
        )
        assert result.mapping_coverage is PersonalSemanticMappingCoverage.PARTIAL_SEMANTIC_MAPPING
        assert result.signal_set is PersonalSignalSet.MIXED

    def test_one_unmapped_claim_is_enough_for_partial(self) -> None:
        """No threshold, no percentage: a single gap keeps it PARTIAL."""
        mapped = tuple(
            _mapped(rule_id=f"{RULE_A}.{index}", claim_key=f"{CLAIM_A}.{index}")
            for index in range(20)
        )
        result = aggregate_personal_decision_signals(_one(*mapped, _unmapped()))
        assert result.mapping_coverage is PersonalSemanticMappingCoverage.PARTIAL_SEMANTIC_MAPPING
        assert len(result.rules) == 20


# ---------------------------------------------------------------------------
# The layer must never vote
# ---------------------------------------------------------------------------


class TestNoVoting:
    def test_ten_supporting_and_one_cautionary_is_mixed(self) -> None:
        claims = [
            _mapped(rule_id=f"{RULE_A}.{index}", signal=SUPPORTING, claim_key=f"{CLAIM_A}.{index}")
            for index in range(10)
        ]
        claims.append(_mapped(rule_id=RULE_B, signal=CAUTIONARY, claim_key=CLAIM_B))
        result = aggregate_personal_decision_signals(_one(*claims))
        assert len(result.rules) == 11
        assert result.signal_set is PersonalSignalSet.MIXED

    def test_one_supporting_and_ten_cautionary_is_mixed(self) -> None:
        claims = [_mapped(rule_id=RULE_A, signal=SUPPORTING, claim_key=CLAIM_A)]
        claims.extend(
            _mapped(rule_id=f"{RULE_B}.{index}", signal=CAUTIONARY, claim_key=f"{CLAIM_B}.{index}")
            for index in range(10)
        )
        result = aggregate_personal_decision_signals(_one(*claims))
        assert len(result.rules) == 11
        assert result.signal_set is PersonalSignalSet.MIXED

    def test_lopsided_and_even_splits_agree(self) -> None:
        """Ten-to-one and one-to-one are the same answer. That is the point."""
        lopsided = aggregate_personal_decision_signals(
            _one(
                *[
                    _mapped(rule_id=f"{RULE_A}.{index}", claim_key=f"{CLAIM_A}.{index}")
                    for index in range(10)
                ],
                _mapped(rule_id=RULE_B, signal=CAUTIONARY, claim_key=CLAIM_B),
            )
        )
        even = aggregate_personal_decision_signals(
            _one(
                _mapped(rule_id=RULE_A, signal=SUPPORTING),
                _mapped(rule_id=RULE_B, signal=CAUTIONARY, claim_key=CLAIM_B),
            )
        )
        assert lopsided.signal_set is even.signal_set is PersonalSignalSet.MIXED


# ---------------------------------------------------------------------------
# Distinct rule identity
# ---------------------------------------------------------------------------


class TestRuleIdentity:
    def test_repeated_occurrence_is_provenance_not_weight(self) -> None:
        claim_ids = [uuid.uuid4() for _ in range(3)]
        repeated = _semantics(
            ingredients=tuple(
                _ingredient(
                    position=position,
                    claims=(_mapped(claim_id=claim_ids[index]),),
                )
                for index, position in enumerate((0, 3, 8))
            )
        )
        result = aggregate_personal_decision_signals(repeated)

        assert len(result.rules) == 1
        assert len(result.rules[0].occurrences) == 3
        assert [o.ingredient_position for o in result.rules[0].occurrences] == [0, 3, 8]
        assert [o.claim_id for o in result.rules[0].occurrences] == claim_ids
        assert result.signal_set is PersonalSignalSet.SUPPORTING_ONLY

        once = aggregate_personal_decision_signals(_one(_mapped()))
        assert result.signal_set is once.signal_set
        assert result.mapping_coverage is once.mapping_coverage
        assert len(result.rules) == len(once.rules) == 1

    def test_same_rule_id_different_version_stays_distinct(self) -> None:
        result = aggregate_personal_decision_signals(
            _one(
                _mapped(rule_id=RULE_A, rule_version="1", signal=SUPPORTING),
                _mapped(rule_id=RULE_A, rule_version="2", signal=CAUTIONARY, claim_key=CLAIM_B),
            )
        )
        assert len(result.rules) == 2
        assert {(r.rule_id, r.rule_version) for r in result.rules} == {(RULE_A, "1"), (RULE_A, "2")}
        assert result.signal_set is PersonalSignalSet.MIXED

    def test_same_identity_same_target_same_signal_aggregates(self) -> None:
        result = aggregate_personal_decision_signals(_one(_mapped(), _mapped()))
        assert len(result.rules) == 1
        assert len(result.rules[0].occurrences) == 2

    def test_same_identity_conflicting_signal_fails_closed(self) -> None:
        with pytest.raises(PersonalDecisionAggregationInvariantError, match="two different reviewed directions"):
            aggregate_personal_decision_signals(
                _one(
                    _mapped(rule_id=RULE_A, rule_version="1", signal=SUPPORTING),
                    _mapped(rule_id=RULE_A, rule_version="1", signal=CAUTIONARY),
                )
            )

    def test_same_identity_different_claim_target_fails_closed(self) -> None:
        with pytest.raises(PersonalDecisionAggregationInvariantError, match="two different evidence identities"):
            aggregate_personal_decision_signals(
                _one(
                    _mapped(rule_id=RULE_A, rule_version="1", claim_key=CLAIM_A, claim_version=1),
                    _mapped(rule_id=RULE_A, rule_version="1", claim_key=CLAIM_B, claim_version=2),
                )
            )

    def test_same_identity_different_substance_target_fails_closed(self) -> None:
        semantics = _semantics(
            ingredients=(
                _ingredient(position=0, substance_key=SUBSTANCE_A, claims=(_mapped(),)),
                _ingredient(position=1, substance_key=SUBSTANCE_B, claims=(_mapped(),)),
            )
        )
        with pytest.raises(PersonalDecisionAggregationInvariantError, match="two different evidence identities"):
            aggregate_personal_decision_signals(semantics)

    def test_aggregated_rule_carries_its_exact_target(self) -> None:
        result = aggregate_personal_decision_signals(
            _one(_mapped(claim_key=CLAIM_B, claim_version=7))
        )
        rule = result.rules[0]
        assert (rule.substance_key, rule.claim_key, rule.claim_version) == (SUBSTANCE_A, CLAIM_B, 7)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_unmapped_claim_provenance_is_exact_and_bounded(self) -> None:
        claim_id = uuid.uuid4()
        result = aggregate_personal_decision_signals(
            _semantics(
                ingredients=(
                    _ingredient(
                        position=4,
                        claims=(_unmapped(claim_key=CLAIM_B, claim_version=5, claim_id=claim_id),),
                    ),
                )
            )
        )
        (claim,) = result.unmapped_claims
        assert claim == UnmappedPersonalDecisionClaim(
            ingredient_position=4,
            substance_key=SUBSTANCE_A,
            claim_id=claim_id,
            claim_key=CLAIM_B,
            claim_version=5,
        )
        fields = set(UnmappedPersonalDecisionClaim.__dataclass_fields__)
        assert fields == {
            "ingredient_position",
            "substance_key",
            "claim_id",
            "claim_key",
            "claim_version",
        }

    def test_occurrence_fields_are_exact_and_bounded(self) -> None:
        fields = set(PersonalDecisionSignalOccurrence.__dataclass_fields__)
        assert fields == {
            "ingredient_position",
            "substance_key",
            "claim_id",
            "claim_key",
            "claim_version",
        }

    def test_no_public_field_names_a_score_or_weight(self) -> None:
        banned = {
            "weight",
            "score",
            "points",
            "magnitude",
            "rank",
            "priority",
            "confidence",
            "contribution",
        }
        for model in (
            AggregatedPersonalDecisionRule,
            PersonalDecisionSignalOccurrence,
            UnmappedPersonalDecisionClaim,
            PersonalDecisionAggregation,
        ):
            assert banned.isdisjoint(model.__dataclass_fields__), model


# ---------------------------------------------------------------------------
# Source preservation
# ---------------------------------------------------------------------------


class TestSourcePreservation:
    def test_source_semantics_is_the_same_object(self) -> None:
        semantics = _one(_mapped())
        result = aggregate_personal_decision_signals(semantics)
        assert result.source_semantics is semantics

    def test_upstream_structures_are_not_rebuilt(self) -> None:
        semantics = _semantics(
            ingredients=(
                _ingredient(position=0, claims=(_mapped(),)),
                _ingredient(position=1, claims=(_unmapped(),)),
            )
        )
        result = aggregate_personal_decision_signals(semantics)
        assert result.source_semantics.ingredients is semantics.ingredients
        for original, carried in zip(
            semantics.ingredients, result.source_semantics.ingredients, strict=True
        ):
            assert carried is original
            assert carried.claims is original.claims

    def test_opaque_metadata_survives_untouched(self) -> None:
        marker = object()
        semantics = _semantics(context_status=marker)
        object.__setattr__(semantics, "provenance", marker)
        result = aggregate_personal_decision_signals(semantics)
        assert result.source_semantics.context_status is marker
        assert result.source_semantics.provenance is marker
        assert result.source_semantics.category is semantics.category
        assert result.source_semantics.profile_id == semantics.profile_id
        assert result.source_semantics.profile_version == semantics.profile_version


# ---------------------------------------------------------------------------
# Ordering and independence
# ---------------------------------------------------------------------------


class TestOrdering:
    def test_rules_follow_first_encounter_order(self) -> None:
        result = aggregate_personal_decision_signals(
            _one(
                _mapped(rule_id="rule.synthetic.z", claim_key="claim.synthetic.z"),
                _mapped(rule_id="rule.synthetic.a", claim_key="claim.synthetic.a"),
                _mapped(rule_id="rule.synthetic.m", claim_key="claim.synthetic.m"),
                _mapped(rule_id="rule.synthetic.z", claim_key="claim.synthetic.z"),
            )
        )
        assert [rule.rule_id for rule in result.rules] == [
            "rule.synthetic.z",
            "rule.synthetic.a",
            "rule.synthetic.m",
        ]

    def test_unmapped_claims_follow_encounter_order(self) -> None:
        keys = ("claim.synthetic.z", "claim.synthetic.a", "claim.synthetic.m")
        result = aggregate_personal_decision_signals(
            _one(*[_unmapped(claim_key=key) for key in keys])
        )
        assert tuple(claim.claim_key for claim in result.unmapped_claims) == keys

    def test_claim_order_does_not_change_the_outcome(self) -> None:
        forward = _one(
            _mapped(rule_id=RULE_A, signal=SUPPORTING, claim_key=CLAIM_A),
            _mapped(rule_id=RULE_B, signal=CAUTIONARY, claim_key=CLAIM_B),
            _unmapped(claim_key="claim.synthetic.c"),
        )
        reverse = _one(
            _unmapped(claim_key="claim.synthetic.c"),
            _mapped(rule_id=RULE_B, signal=CAUTIONARY, claim_key=CLAIM_B),
            _mapped(rule_id=RULE_A, signal=SUPPORTING, claim_key=CLAIM_A),
        )
        first = aggregate_personal_decision_signals(forward)
        second = aggregate_personal_decision_signals(reverse)
        assert first.signal_set is second.signal_set
        assert first.mapping_coverage is second.mapping_coverage
        assert {(r.rule_id, r.signal) for r in first.rules} == {
            (r.rule_id, r.signal) for r in second.rules
        }
        # Order itself still tracks encounter order, so it does differ.
        assert [r.rule_id for r in first.rules] == [RULE_A, RULE_B]
        assert [r.rule_id for r in second.rules] == [RULE_B, RULE_A]

    def test_ingredient_positions_do_not_change_the_outcome(self) -> None:
        def build(positions: tuple[int, ...]) -> PersonalDecisionAggregation:
            return aggregate_personal_decision_signals(
                _semantics(
                    ingredients=tuple(
                        _ingredient(
                            position=position,
                            claims=(
                                _mapped(
                                    rule_id=f"{RULE_A}.{index}",
                                    claim_key=f"{CLAIM_A}.{index}",
                                ),
                            ),
                        )
                        for index, position in enumerate(positions)
                    )
                )
            )

        near = build((0, 1, 2))
        far = build((10, 50, 100))
        assert near.signal_set is far.signal_set
        assert near.mapping_coverage is far.mapping_coverage
        assert len(near.rules) == len(far.rules) == 3
        assert [o.ingredient_position for r in far.rules for o in r.occurrences] == [10, 50, 100]


# ---------------------------------------------------------------------------
# Upstream states pass through structurally
# ---------------------------------------------------------------------------


class TestUpstreamStates:
    def test_handoff_produces_no_projections_and_keeps_the_source(self) -> None:
        handoff = object()
        semantics = _semantics(context_status="handoff_required", handoff=handoff)
        result = aggregate_personal_decision_signals(semantics)
        assert result.mapping_coverage is PersonalSemanticMappingCoverage.NO_CLAIM_PROJECTIONS
        assert result.signal_set is PersonalSignalSet.NONE
        assert result.source_semantics is semantics
        assert result.source_semantics.handoff is handoff

    def test_missing_personal_context_produces_no_projections(self) -> None:
        semantics = _semantics(context_status="not_enough_personal_context")
        result = aggregate_personal_decision_signals(semantics)
        assert result.mapping_coverage is PersonalSemanticMappingCoverage.NO_CLAIM_PROJECTIONS
        assert result.signal_set is PersonalSignalSet.NONE
        assert result.source_semantics.context_status == "not_enough_personal_context"

    def test_no_projections_is_indistinguishable_without_the_source(self) -> None:
        """Handoff and plain absence give the same structure on purpose.

        Step 8D must not guess why nothing came through. The reason lives in
        the preserved source object for a later governed layer to read.
        """
        handoff = aggregate_personal_decision_signals(
            _semantics(context_status="handoff_required", handoff=object())
        )
        absent = aggregate_personal_decision_signals(_semantics())
        assert handoff.mapping_coverage is absent.mapping_coverage
        assert handoff.signal_set is absent.signal_set
        assert handoff.source_semantics.handoff is not absent.source_semantics.handoff

    def test_ambiguous_ingredient_candidates_are_never_inspected(self) -> None:
        ambiguous = IngredientDecisionSemantics(
            position=0,
            raw_name="Synthetic ambiguous",
            normalized_name="synthetic ambiguous",
            identity_status=ProjectedIdentityStatus.AMBIGUOUS,
            substance_key=None,
            entity_kind=None,
            candidate_substance_keys=(SUBSTANCE_A, SUBSTANCE_B),
            personal_applicability_status=PersonalApplicabilityStatus.IDENTITY_AMBIGUOUS,
            claims=(),
        )
        result = aggregate_personal_decision_signals(_semantics(ingredients=(ambiguous,)))
        assert result.mapping_coverage is PersonalSemanticMappingCoverage.NO_CLAIM_PROJECTIONS
        assert result.signal_set is PersonalSignalSet.NONE
        assert result.rules == ()
        assert result.source_semantics.ingredients[0].candidate_substance_keys == (
            SUBSTANCE_A,
            SUBSTANCE_B,
        )


# ---------------------------------------------------------------------------
# Impossible input fails closed
# ---------------------------------------------------------------------------


class TestImpossibleInput:
    def test_mapped_without_rule_id(self) -> None:
        projection = _corrupt(_mapped(), rule_id=None)
        with pytest.raises(PersonalDecisionAggregationInvariantError, match="reviewed rule identity"):
            aggregate_personal_decision_signals(_one(projection))

    def test_mapped_without_rule_version(self) -> None:
        projection = _corrupt(_mapped(), rule_version=None)
        with pytest.raises(PersonalDecisionAggregationInvariantError, match="reviewed rule identity"):
            aggregate_personal_decision_signals(_one(projection))

    def test_mapped_without_signal(self) -> None:
        projection = _corrupt(_mapped(), signal=None)
        with pytest.raises(PersonalDecisionAggregationInvariantError, match="reviewed direction"):
            aggregate_personal_decision_signals(_one(projection))

    def test_mapped_with_a_signal_outside_the_vocabulary(self) -> None:
        projection = _corrupt(_mapped(), signal="strongly_supporting")
        with pytest.raises(PersonalDecisionAggregationInvariantError, match="reviewed direction"):
            aggregate_personal_decision_signals(_one(projection))

    def test_mapped_on_ingredient_without_substance_key(self) -> None:
        semantics = _semantics(
            ingredients=(_ingredient(substance_key=None, claims=(_mapped(),)),)
        )
        with pytest.raises(PersonalDecisionAggregationInvariantError, match="without a resolved substance key"):
            aggregate_personal_decision_signals(semantics)

    def test_unmapped_on_ingredient_without_substance_key(self) -> None:
        """Provenance types a substance key as required, so this fails too."""
        semantics = _semantics(
            ingredients=(_ingredient(substance_key=None, claims=(_unmapped(),)),)
        )
        with pytest.raises(PersonalDecisionAggregationInvariantError, match="without a resolved substance key"):
            aggregate_personal_decision_signals(semantics)

    @pytest.mark.parametrize(
        "fields",
        [
            {"rule_id": RULE_A},
            {"rule_version": "1"},
            {"signal": SUPPORTING},
        ],
    )
    def test_unmapped_carrying_rule_provenance(self, fields: dict) -> None:
        projection = _corrupt(_unmapped(), **fields)
        with pytest.raises(PersonalDecisionAggregationInvariantError, match="carries reviewed rule provenance"):
            aggregate_personal_decision_signals(_one(projection))

    def test_unrecognised_semantic_status(self) -> None:
        projection = _corrupt(_mapped(), status="semantics_probably_available")
        with pytest.raises(PersonalDecisionAggregationInvariantError, match="unrecognised semantic status"):
            aggregate_personal_decision_signals(_one(projection))

    def test_invariant_error_is_a_value_error(self) -> None:
        assert issubclass(PersonalDecisionAggregationInvariantError, ValueError)


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_public_models_are_frozen_and_slotted(self) -> None:
        for model in (
            AggregatedPersonalDecisionRule,
            PersonalDecisionSignalOccurrence,
            UnmappedPersonalDecisionClaim,
            PersonalDecisionAggregation,
        ):
            assert model.__dataclass_params__.frozen, model
            assert getattr(model, "__slots__", None) is not None, model

    def test_collections_are_tuples(self) -> None:
        result = aggregate_personal_decision_signals(_one(_mapped(), _unmapped()))
        assert isinstance(result.rules, tuple)
        assert isinstance(result.unmapped_claims, tuple)
        assert isinstance(result.rules[0].occurrences, tuple)

    def test_result_cannot_be_mutated(self) -> None:
        result = aggregate_personal_decision_signals(_one(_mapped()))
        with pytest.raises(Exception):
            result.signal_set = PersonalSignalSet.NONE  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Static guards
# ---------------------------------------------------------------------------


def _production_sources() -> list[tuple[str, ast.Module]]:
    return [
        (path.name, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(SERVICE_PATH.glob("*.py"))
    ]


def _docstring_node_ids(tree: ast.Module) -> set[int]:
    """Identify docstrings so a guard can exclude them structurally.

    A docstring is allowed -- required, even -- to name the things this domain
    must never do. Scanning raw lines cannot tell that apart from code, because
    a docstring's second line does not begin with a quote.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _executable_tokens(tree: ast.Module) -> list[tuple[int, str]]:
    """Every identifier and non-docstring string literal, with its line.

    Comments never reach the AST and docstrings are dropped above, so prose
    that forbids a word cannot be mistaken for code that uses it.
    """
    skip = _docstring_node_ids(tree)
    tokens: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Name):
            tokens.append((line, node.id))
        elif isinstance(node, ast.Attribute):
            tokens.append((line, node.attr))
        elif isinstance(node, ast.arg):
            tokens.append((line, node.arg))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            tokens.append((line, node.name))
        elif isinstance(node, ast.keyword) and node.arg:
            tokens.append((line, node.arg))
        elif isinstance(node, ast.alias):
            tokens.append((line, node.name))
            if node.asname:
                tokens.append((line, node.asname))
        elif isinstance(node, ast.ImportFrom) and node.module:
            tokens.append((line, node.module))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            tokens.append((line, node.value))
    return tokens


#: Split an identifier into lowercase words: snake_case and CamelCase alike.
_WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+")


class TestStaticGuards:
    FORBIDDEN_DOMAINS = frozenset({
        "personal_applicability",
        "personal_lens",
        "profile",
        "evidence",
        "product",
        "substance_interpretation",
        "substances",
        "formulas",
        "routines",
        "off",
        "ai_gateway",
        "alternatives",
        "recommendation",
        "family",
        "purchase",
        "payments",
    })

    def test_module_set_is_exact(self) -> None:
        assert {path.name for path in SERVICE_PATH.glob("*.py")} == {
            "__init__.py",
            "enums.py",
            "service.py",
        }

    def test_step8c_is_the_only_application_domain_dependency(self) -> None:
        seen: set[str] = set()
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                for module in modules:
                    if not module.startswith("app.domains."):
                        continue
                    domain = module.split(".")[2]
                    seen.add(domain)
                    if domain in self.FORBIDDEN_DOMAINS:
                        offenders.append(f"{name}: {module}")
        assert offenders == [], offenders
        assert seen == {"personal_decision_semantics", "personal_decision_aggregation"}

    def test_no_database_or_network_imports(self) -> None:
        banned = ("sqlalchemy", "httpx", "requests", "aiohttp", "asyncpg", "openai", "google", "fastapi")
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                offenders.extend(
                    f"{name}: {module}" for module in modules if module.split(".")[0] in banned
                )
        assert offenders == [], offenders

    def test_never_reads_evidence_prose_or_ambiguity(self) -> None:
        forbidden = {
            "summary",
            "scope",
            "evidence_strength",
            "evidence_tier",
            "sources",
            "matched_facts",
            "candidate_substance_keys",
        }
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in forbidden:
                    offenders.append(f"{name}:{node.lineno} reads .{node.attr}")
        assert offenders == [], offenders

    def test_no_async_and_no_upstream_parameters(self) -> None:
        forbidden_args = {"session", "db", "account_id", "snapshot", "safety"}
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if isinstance(node, ast.AsyncFunctionDef):
                    offenders.append(f"{name}: async def {node.name}")
                if isinstance(node, ast.FunctionDef):
                    for arg in [*node.args.args, *node.args.kwonlyargs]:
                        if arg.arg in forbidden_args:
                            offenders.append(f"{name}: {node.name}({arg.arg})")
        assert offenders == [], offenders

    def test_public_entry_point_takes_only_the_step8c_result(self) -> None:
        """One parameter, and it is the Step 8C object.

        Pinning the exact signature is stricter than blacklisting argument
        names: no session, account, snapshot, category, safety input or rules
        seam can be added without this failing, whatever it gets called.
        """
        signature = inspect.signature(aggregate_personal_decision_signals)
        assert list(signature.parameters) == ["semantics"]
        (parameter,) = signature.parameters.values()
        assert parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert parameter.default is inspect.Parameter.empty
        assert not inspect.iscoroutinefunction(aggregate_personal_decision_signals)

    def test_step8c_construction_helpers_are_never_called(self) -> None:
        """Step 8D consumes a Step 8C result; it never produces one."""
        forbidden = {"build_rule_index", "project_personal_decision_semantics"}
        offenders: list[str] = []
        for name, tree in _production_sources():
            for line, text in _executable_tokens(tree):
                if text in forbidden:
                    offenders.append(f"{name}:{line}: {text}")
        assert offenders == [], offenders

    def test_no_scoring_or_verdict_vocabulary(self) -> None:
        """Docstrings may name these words to forbid them; code may not."""
        banned = (
            "good_for_you",
            "bad_for_you",
            "unsafe",
            "suitable",
            "unsuitable",
            "verdict",
            "score",
            "rating",
            "grade",
            "ranking",
            "recommendation",
            "buy",
            "wait",
            "skip",
            "avoid",
            "winner",
        )
        offenders: list[str] = []
        for name, tree in _production_sources():
            for line, text in _executable_tokens(tree):
                lowered = text.lower()
                offenders.extend(f"{name}:{line}: {word} in {text!r}" for word in banned if word in lowered)
        assert offenders == [], offenders

    def test_no_voting_or_weighting_identifiers(self) -> None:
        """Counting is how this layer would quietly become a vote."""
        banned_whole = {
            "supporting_count",
            "cautionary_count",
            "supporting_total",
            "cautionary_total",
            "signal_weight",
            "score_sum",
            "net_signal",
            "majority_signal",
        }
        banned_words = {
            "average",
            "count",
            "counts",
            "dominant",
            "fraction",
            "magnitude",
            "majority",
            "mean",
            "net",
            "outweigh",
            "percentage",
            "points",
            "priority",
            "rank",
            "ranked",
            "ranking",
            "ratio",
            "score",
            "scores",
            "scoring",
            "strength",
            "subtotal",
            "sum",
            "sums",
            "tally",
            "threshold",
            "total",
            "totals",
            "weight",
            "weighted",
            "weights",
            "winner",
        }
        offenders: list[str] = []
        for name, tree in _production_sources():
            for line, text in _executable_tokens(tree):
                if not text.isidentifier():
                    continue
                if text in banned_whole:
                    offenders.append(f"{name}:{line}: {text}")
                    continue
                offenders.extend(
                    f"{name}:{line}: {word} in {text}"
                    for word in _WORD.findall(text)
                    if word.lower() in banned_words
                )
        assert offenders == [], offenders

    def test_no_comparison_between_signal_groups(self) -> None:
        """No ordering comparison may appear in production at all.

        A vote needs ``>`` or ``<`` somewhere. Step 8D's logic is set
        membership and boolean state, so it needs neither.
        """
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                for op in node.ops:
                    if isinstance(op, ast.Lt | ast.LtE | ast.Gt | ast.GtE):
                        offenders.append(f"{name}:{node.lineno}: {type(op).__name__}")
        assert offenders == [], offenders


def test_public_surface_is_stable() -> None:
    result = aggregate_personal_decision_signals(_semantics())
    assert isinstance(result, PersonalDecisionAggregation)
    assert set(PersonalSignalSet) == {
        PersonalSignalSet.NONE,
        PersonalSignalSet.SUPPORTING_ONLY,
        PersonalSignalSet.CAUTIONARY_ONLY,
        PersonalSignalSet.MIXED,
    }
    assert set(PersonalSemanticMappingCoverage) == {
        PersonalSemanticMappingCoverage.NO_CLAIM_PROJECTIONS,
        PersonalSemanticMappingCoverage.NO_MAPPED_SEMANTICS,
        PersonalSemanticMappingCoverage.PARTIAL_SEMANTIC_MAPPING,
        PersonalSemanticMappingCoverage.COMPLETE_SEMANTIC_MAPPING,
    }
