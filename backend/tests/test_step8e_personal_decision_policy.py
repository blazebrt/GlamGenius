"""Step 8E — governed personal decision policy.

Every identity is synthetic. The tests that matter most are adversarial in two
directions.

First, they try to make an epistemic gap come out as advice. Partial context,
an unparsed formula and incomplete semantic mapping each get a synthetic WAIT
policy injected that would match if policy were ever consulted, and each must
still return NOT_ENOUGH_INFORMATION without the registry being touched.

Second, they try to catch an implicit direction-to-action mapping hiding in
the infrastructure. A synthetic policy maps SUPPORTING_ONLY to SKIP and
another maps MIXED to BUY; both must be obeyed exactly. Those rules would be
absurd as production policy, and that is the point -- if the code contained
any private notion of what a direction "should" mean, obeying them would be
impossible.

Corrupted upstream objects use ``object.__setattr__`` because Step 8C and
Step 8D's frozen dataclasses refuse to construct them.
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
    PersonalDecisionSignalOccurrence,
    PersonalSemanticMappingCoverage,
    PersonalSignalSet,
    UnmappedPersonalDecisionClaim,
    aggregate_personal_decision_signals,
)
from app.domains.personal_decision_policy import (
    PERSONAL_DECISION_POLICY_RULES,
    PersonalDecisionAction,
    PersonalDecisionPolicyCategory,
    PersonalDecisionPolicyInvariantError,
    PersonalDecisionPolicyReason,
    PersonalDecisionPolicyRegistryError,
    PersonalDecisionPolicyResult,
    PersonalDecisionPolicyRule,
    PersonalDecisionPolicyStatus,
    build_policy_index,
    evaluate_personal_decision_policy,
)
from app.domains.personal_decision_semantics import (
    ClaimDecisionSemanticProjection,
    IngredientDecisionSemantics,
    LabelSnapshotPersonalDecisionSemantics,
    PersonalDecisionSemanticStatus,
    PersonalDecisionSignal,
)
from app.domains.substance_interpretation import ProjectedIdentityStatus

SERVICE_PATH = Path(__file__).resolve().parents[1] / "app" / "domains" / "personal_decision_policy"

SUBSTANCE_A = "substance.synthetic.a"
CLAIM_A = "claim.synthetic.a"
RULE_A = "rule.synthetic.a"
RULE_B = "rule.synthetic.b"
POLICY_A = "policy.synthetic.a"

SUPPORTING = PersonalDecisionSignal.SUPPORTING
CAUTIONARY = PersonalDecisionSignal.CAUTIONARY
MAPPED = PersonalDecisionSemanticStatus.SEMANTICS_AVAILABLE
UNMAPPED = PersonalDecisionSemanticStatus.NOT_ENOUGH_DECISION_SEMANTICS

AVAILABLE = PersonalDecisionPolicyStatus.DECISION_AVAILABLE
NOT_ENOUGH_INFO = PersonalDecisionPolicyStatus.NOT_ENOUGH_INFORMATION
NO_POLICY = PersonalDecisionPolicyStatus.NOT_ENOUGH_DECISION_POLICY
HANDOFF = PersonalDecisionPolicyStatus.HANDOFF_REQUIRED

R_HANDOFF = PersonalDecisionPolicyReason.PROFESSIONAL_HANDOFF_REQUIRED
R_CONTEXT = PersonalDecisionPolicyReason.PERSONAL_CONTEXT_NOT_COMPLETE
R_FORMULA = PersonalDecisionPolicyReason.FORMULA_NOT_PROJECTABLE
R_MAPPING = PersonalDecisionPolicyReason.SEMANTIC_MAPPING_NOT_COMPLETE
R_NO_RULE = PersonalDecisionPolicyReason.NO_EXACT_POLICY_RULE


# ---------------------------------------------------------------------------
# Synthetic upstream fixtures
# ---------------------------------------------------------------------------


def _mapped(
    *,
    rule_id: str = RULE_A,
    rule_version: str = "1",
    signal: PersonalDecisionSignal = SUPPORTING,
    claim_key: str = CLAIM_A,
    claim_version: int = 2,
) -> ClaimDecisionSemanticProjection:
    return ClaimDecisionSemanticProjection(
        claim_id=uuid.uuid4(),
        claim_key=claim_key,
        claim_version=claim_version,
        status=MAPPED,
        rule_id=rule_id,
        rule_version=rule_version,
        signal=signal,
    )


def _unmapped(*, claim_key: str = "claim.synthetic.z") -> ClaimDecisionSemanticProjection:
    return ClaimDecisionSemanticProjection(
        claim_id=uuid.uuid4(),
        claim_key=claim_key,
        claim_version=1,
        status=UNMAPPED,
        rule_id=None,
        rule_version=None,
        signal=None,
    )


def _ingredient(
    *,
    position: int = 0,
    substance_key: str | None = SUBSTANCE_A,
    status: PersonalApplicabilityStatus = PersonalApplicabilityStatus.PERSONAL_EVIDENCE_AVAILABLE,
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
        personal_applicability_status=status,
        claims=claims,
    )


def _semantics(
    *,
    ingredients: tuple[IngredientDecisionSemantics, ...] = (),
    category: PersonalApplicabilityCategory = PersonalApplicabilityCategory.SKIN_CARE,
    context_status: object = "context_available",
    formula_status: str | None = "parsed",
    handoff: object | None = None,
) -> LabelSnapshotPersonalDecisionSemantics:
    return LabelSnapshotPersonalDecisionSemantics(
        provenance=None,
        category=category,
        formula_status=formula_status,
        profile_id=uuid.uuid4(),
        profile_version=3,
        context_status=context_status,
        ingredients=ingredients,
        handoff=handoff,
    )


def _aggregation(
    *,
    claims: tuple[ClaimDecisionSemanticProjection, ...] = (),
    ingredients: tuple[IngredientDecisionSemantics, ...] | None = None,
    **semantics_kwargs: object,
) -> PersonalDecisionAggregation:
    """A real Step 8D aggregation built by the real Step 8D function."""
    if ingredients is None:
        ingredients = (_ingredient(claims=claims),)
    semantics = _semantics(ingredients=ingredients, **semantics_kwargs)  # type: ignore[arg-type]
    return aggregate_personal_decision_signals(semantics)


def _eligible(
    *,
    signal: PersonalDecisionSignal = SUPPORTING,
    rule_id: str = RULE_A,
    rule_version: str = "1",
) -> PersonalDecisionAggregation:
    """A fully eligible aggregation: complete mapping, one distinct rule."""
    return _aggregation(claims=(_mapped(rule_id=rule_id, rule_version=rule_version, signal=signal),))


def _policy(
    *,
    policy_id: str = POLICY_A,
    policy_version: str = "1",
    category: PersonalDecisionPolicyCategory = PersonalDecisionPolicyCategory.SKIN_CARE,
    identities: frozenset[tuple[str, str]] | None = None,
    signal_set: PersonalSignalSet = PersonalSignalSet.SUPPORTING_ONLY,
    unresolved: bool = False,
    ambiguous: bool = False,
    evidence_gap: bool = False,
    action: PersonalDecisionAction = PersonalDecisionAction.BUY,
) -> PersonalDecisionPolicyRule:
    return PersonalDecisionPolicyRule(
        policy_id=policy_id,
        policy_version=policy_version,
        category=category,
        semantic_rule_identities=(
            identities if identities is not None else frozenset({(RULE_A, "1")})
        ),
        signal_set=signal_set,
        has_identity_unresolved=unresolved,
        has_identity_ambiguous=ambiguous,
        has_personal_evidence_gap=evidence_gap,
        action=action,
    )


def _corrupt(obj: object, **fields: object) -> object:
    for name, value in fields.items():
        object.__setattr__(obj, name, value)
    return obj


#: A registry that cannot be built. Used to prove gates run before it does.
BROKEN_REGISTRY = (_policy(policy_id="   "),)

#: A WAIT policy broad enough to match if policy were ever wrongly consulted.
WAIT_POLICY = _policy(action=PersonalDecisionAction.WAIT)


# ---------------------------------------------------------------------------
# Handoff is absolute
# ---------------------------------------------------------------------------


class TestHandoff:
    def test_handoff_context_status(self) -> None:
        aggregation = _aggregation(context_status="handoff_required", ingredients=())
        result = evaluate_personal_decision_policy(aggregation)
        assert result.status is HANDOFF
        assert result.reason is R_HANDOFF
        assert result.action is None

    def test_handoff_object_wins_even_with_a_non_handoff_status(self) -> None:
        """A handoff object is authoritative whatever the status says."""
        aggregation = _aggregation(context_status="context_available", handoff=object())
        result = evaluate_personal_decision_policy(aggregation)
        assert result.status is HANDOFF
        assert result.reason is R_HANDOFF

    def test_handoff_object_wins_even_with_a_corrupted_status(self) -> None:
        aggregation = _aggregation(handoff=object())
        _corrupt(aggregation.source_semantics, context_status="nonsense_status")
        result = evaluate_personal_decision_policy(aggregation)
        assert result.status is HANDOFF

    def test_broken_registry_cannot_suppress_a_handoff(self) -> None:
        """Safety must not be blocked by a policy registry that will not build."""
        aggregation = _aggregation(context_status="handoff_required", ingredients=())
        result = evaluate_personal_decision_policy(aggregation, rules=BROKEN_REGISTRY)
        assert result.status is HANDOFF
        assert result.reason is R_HANDOFF

    def test_handoff_is_never_not_enough_information(self) -> None:
        aggregation = _aggregation(context_status="handoff_required", ingredients=())
        result = evaluate_personal_decision_policy(aggregation)
        assert result.status is not NOT_ENOUGH_INFO


# ---------------------------------------------------------------------------
# Eligibility gates, and the refusal to turn a gap into WAIT
# ---------------------------------------------------------------------------


class TestContextGate:
    @pytest.mark.parametrize("context", ["partial_context", "not_enough_personal_context"])
    def test_incomplete_context_blocks(self, context: str) -> None:
        result = evaluate_personal_decision_policy(
            _aggregation(claims=(_mapped(),), context_status=context)
        )
        assert result.status is NOT_ENOUGH_INFO
        assert result.reason is R_CONTEXT

    @pytest.mark.parametrize("context", ["partial_context", "not_enough_personal_context"])
    def test_incomplete_context_never_becomes_wait(self, context: str) -> None:
        result = evaluate_personal_decision_policy(
            _aggregation(claims=(_mapped(),), context_status=context),
            rules=(WAIT_POLICY,),
        )
        assert result.status is NOT_ENOUGH_INFO
        assert result.action is None

    def test_context_gate_runs_before_the_registry(self) -> None:
        result = evaluate_personal_decision_policy(
            _aggregation(claims=(_mapped(),), context_status="partial_context"),
            rules=BROKEN_REGISTRY,
        )
        assert result.status is NOT_ENOUGH_INFO
        assert result.reason is R_CONTEXT


class TestFormulaGate:
    @pytest.mark.parametrize(
        "formula_status",
        ["empty", "malformed", "ambiguous_boundary", "too_long", "too_many_items"],
    )
    def test_each_non_parsed_status_blocks(self, formula_status: str) -> None:
        result = evaluate_personal_decision_policy(
            _aggregation(claims=(_mapped(),), formula_status=formula_status)
        )
        assert result.status is NOT_ENOUGH_INFO
        assert result.reason is R_FORMULA

    @pytest.mark.parametrize(
        "formula_status",
        ["empty", "malformed", "ambiguous_boundary", "too_long", "too_many_items"],
    )
    def test_a_parser_failure_never_becomes_wait(self, formula_status: str) -> None:
        result = evaluate_personal_decision_policy(
            _aggregation(claims=(_mapped(),), formula_status=formula_status),
            rules=(WAIT_POLICY,),
        )
        assert result.status is NOT_ENOUGH_INFO
        assert result.reason is R_FORMULA
        assert result.action is None

    def test_absent_formula_status_blocks(self) -> None:
        result = evaluate_personal_decision_policy(
            _aggregation(claims=(_mapped(),), formula_status=None)
        )
        assert result.status is NOT_ENOUGH_INFO
        assert result.reason is R_FORMULA

    def test_parsed_formula_proceeds(self) -> None:
        result = evaluate_personal_decision_policy(_eligible())
        assert result.status is NO_POLICY


class TestMappingGate:
    def test_no_claim_projections_blocks(self) -> None:
        result = evaluate_personal_decision_policy(_aggregation(ingredients=(_ingredient(),)))
        assert result.status is NOT_ENOUGH_INFO
        assert result.reason is R_MAPPING

    def test_no_mapped_semantics_blocks(self) -> None:
        result = evaluate_personal_decision_policy(_aggregation(claims=(_unmapped(),)))
        assert result.status is NOT_ENOUGH_INFO
        assert result.reason is R_MAPPING

    def test_partial_mapping_blocks(self) -> None:
        result = evaluate_personal_decision_policy(
            _aggregation(claims=(_mapped(), _unmapped()))
        )
        assert result.status is NOT_ENOUGH_INFO
        assert result.reason is R_MAPPING

    def test_partial_mapping_with_mixed_never_becomes_wait(self) -> None:
        aggregation = _aggregation(
            claims=(
                _mapped(rule_id=RULE_A, signal=SUPPORTING),
                _mapped(rule_id=RULE_B, signal=CAUTIONARY, claim_key="claim.synthetic.b"),
                _unmapped(),
            )
        )
        assert aggregation.signal_set is PersonalSignalSet.MIXED
        result = evaluate_personal_decision_policy(
            aggregation,
            rules=(_policy(signal_set=PersonalSignalSet.MIXED, action=PersonalDecisionAction.WAIT),),
        )
        assert result.status is NOT_ENOUGH_INFO
        assert result.reason is R_MAPPING
        assert result.action is None

    def test_incomplete_mapping_is_not_reported_as_missing_policy(self) -> None:
        """The two kinds of "no" must stay distinguishable."""
        result = evaluate_personal_decision_policy(_aggregation(claims=(_mapped(), _unmapped())))
        assert result.status is NOT_ENOUGH_INFO
        assert result.status is not NO_POLICY


# ---------------------------------------------------------------------------
# Exact policy matching
# ---------------------------------------------------------------------------


class TestExactPolicyMatching:
    def test_production_registry_is_empty(self) -> None:
        assert PERSONAL_DECISION_POLICY_RULES == ()

    def test_eligible_state_with_the_production_registry_yields_no_action(self) -> None:
        result = evaluate_personal_decision_policy(_eligible())
        assert result.status is NO_POLICY
        assert result.reason is R_NO_RULE
        assert result.action is None
        assert result.policy_id is None
        assert result.policy_version is None

    def test_exact_match_yields_the_reviewed_action(self) -> None:
        result = evaluate_personal_decision_policy(_eligible(), rules=(_policy(),))
        assert result.status is AVAILABLE
        assert result.reason is None
        assert result.action is PersonalDecisionAction.BUY
        assert result.policy_id == POLICY_A
        assert result.policy_version == "1"

    def test_same_direction_different_evidence_does_not_match(self) -> None:
        """The mandatory adversarial case: SUPPORTING_ONLY is not an identity."""
        policy_for_a = _policy(identities=frozenset({(RULE_A, "1")}))

        matched = evaluate_personal_decision_policy(
            _eligible(rule_id=RULE_A), rules=(policy_for_a,)
        )
        unmatched = evaluate_personal_decision_policy(
            _eligible(rule_id=RULE_B), rules=(policy_for_a,)
        )

        assert matched.status is AVAILABLE
        assert unmatched.status is NO_POLICY
        assert unmatched.reason is R_NO_RULE

    def test_semantic_rule_version_change_invalidates_the_policy(self) -> None:
        policy_for_v1 = _policy(identities=frozenset({(RULE_A, "1")}))
        result = evaluate_personal_decision_policy(
            _eligible(rule_version="2"), rules=(policy_for_v1,)
        )
        assert result.status is NO_POLICY

    def test_wrong_category_does_not_match(self) -> None:
        aggregation = _aggregation(
            claims=(_mapped(),), category=PersonalApplicabilityCategory.HAIR_CARE
        )
        result = evaluate_personal_decision_policy(
            aggregation, rules=(_policy(category=PersonalDecisionPolicyCategory.SKIN_CARE),)
        )
        assert result.status is NO_POLICY

    def test_identity_set_order_does_not_matter(self) -> None:
        forward = _aggregation(
            claims=(
                _mapped(rule_id=RULE_A, rule_version="1"),
                _mapped(rule_id=RULE_B, rule_version="2", claim_key="claim.synthetic.b"),
            )
        )
        reverse = _aggregation(
            claims=(
                _mapped(rule_id=RULE_B, rule_version="2", claim_key="claim.synthetic.b"),
                _mapped(rule_id=RULE_A, rule_version="1"),
            )
        )
        policy = _policy(identities=frozenset({(RULE_A, "1"), (RULE_B, "2")}))

        assert [r.rule_id for r in forward.rules] != [r.rule_id for r in reverse.rules]
        for aggregation in (forward, reverse):
            result = evaluate_personal_decision_policy(aggregation, rules=(policy,))
            assert result.status is AVAILABLE
            assert result.action is PersonalDecisionAction.BUY

    def test_an_extra_semantic_rule_prevents_the_match(self) -> None:
        aggregation = _aggregation(
            claims=(
                _mapped(rule_id=RULE_A),
                _mapped(rule_id=RULE_B, claim_key="claim.synthetic.b"),
            )
        )
        result = evaluate_personal_decision_policy(
            aggregation, rules=(_policy(identities=frozenset({(RULE_A, "1")})),)
        )
        assert result.status is NO_POLICY

    def test_a_missing_semantic_rule_prevents_the_match(self) -> None:
        result = evaluate_personal_decision_policy(
            _eligible(rule_id=RULE_A),
            rules=(_policy(identities=frozenset({(RULE_A, "1"), (RULE_B, "1")})),),
        )
        assert result.status is NO_POLICY

    def test_duplicate_occurrences_do_not_change_the_match(self) -> None:
        """One rule at three positions is one identity, so policy is unchanged."""
        repeated = _aggregation(
            ingredients=tuple(
                _ingredient(position=position, claims=(_mapped(),)) for position in (0, 3, 8)
            )
        )
        assert len(repeated.rules) == 1
        assert len(repeated.rules[0].occurrences) == 3

        once = evaluate_personal_decision_policy(_eligible(), rules=(_policy(),))
        thrice = evaluate_personal_decision_policy(repeated, rules=(_policy(),))
        assert once.status is thrice.status is AVAILABLE
        assert once.action is thrice.action

    @pytest.mark.parametrize(
        ("flag", "status"),
        [
            ("unresolved", PersonalApplicabilityStatus.IDENTITY_UNRESOLVED),
            ("ambiguous", PersonalApplicabilityStatus.IDENTITY_AMBIGUOUS),
            ("evidence_gap", PersonalApplicabilityStatus.NOT_ENOUGH_INFORMATION),
        ],
    )
    def test_gap_flag_mismatch_prevents_the_match(
        self, flag: str, status: PersonalApplicabilityStatus
    ) -> None:
        """A gap present upstream but absent from the policy target blocks it."""
        with_gap = _aggregation(
            ingredients=(
                _ingredient(position=0, claims=(_mapped(),)),
                _ingredient(position=1, status=status),
            )
        )
        clean_policy = _policy()
        assert evaluate_personal_decision_policy(with_gap, rules=(clean_policy,)).status is NO_POLICY

        gap_policy = _policy(**{flag: True})
        matched = evaluate_personal_decision_policy(with_gap, rules=(gap_policy,))
        assert matched.status is AVAILABLE

    def test_gap_policy_does_not_match_a_clean_state(self) -> None:
        result = evaluate_personal_decision_policy(_eligible(), rules=(_policy(unresolved=True),))
        assert result.status is NO_POLICY

    def test_eleven_rule_mixed_set_does_not_match_a_two_rule_policy(self) -> None:
        """Ten-vs-one and one-vs-one are both MIXED; they are not one state."""
        claims = [
            _mapped(rule_id=f"{RULE_A}.{index}", claim_key=f"{CLAIM_A}.{index}")
            for index in range(10)
        ]
        claims.append(_mapped(rule_id=RULE_B, signal=CAUTIONARY, claim_key="claim.synthetic.b"))
        big = _aggregation(claims=tuple(claims))
        assert big.signal_set is PersonalSignalSet.MIXED

        small_policy = _policy(
            identities=frozenset({(RULE_A, "1"), (RULE_B, "1")}),
            signal_set=PersonalSignalSet.MIXED,
        )
        assert evaluate_personal_decision_policy(big, rules=(small_policy,)).status is NO_POLICY


# ---------------------------------------------------------------------------
# No implicit direction-to-action semantics
# ---------------------------------------------------------------------------


class TestNoImplicitActionSemantics:
    def test_supporting_only_can_map_to_skip(self) -> None:
        """Deliberately strange, and obeyed exactly. Synthetic only."""
        result = evaluate_personal_decision_policy(
            _eligible(signal=SUPPORTING),
            rules=(
                _policy(
                    signal_set=PersonalSignalSet.SUPPORTING_ONLY,
                    action=PersonalDecisionAction.SKIP,
                ),
            ),
        )
        assert result.status is AVAILABLE
        assert result.action is PersonalDecisionAction.SKIP

    def test_mixed_can_map_to_buy(self) -> None:
        aggregation = _aggregation(
            claims=(
                _mapped(rule_id=RULE_A, signal=SUPPORTING),
                _mapped(rule_id=RULE_B, signal=CAUTIONARY, claim_key="claim.synthetic.b"),
            )
        )
        assert aggregation.signal_set is PersonalSignalSet.MIXED
        result = evaluate_personal_decision_policy(
            aggregation,
            rules=(
                _policy(
                    identities=frozenset({(RULE_A, "1"), (RULE_B, "1")}),
                    signal_set=PersonalSignalSet.MIXED,
                    action=PersonalDecisionAction.BUY,
                ),
            ),
        )
        assert result.status is AVAILABLE
        assert result.action is PersonalDecisionAction.BUY

    def test_cautionary_only_can_map_to_buy(self) -> None:
        result = evaluate_personal_decision_policy(
            _eligible(signal=CAUTIONARY),
            rules=(
                _policy(
                    signal_set=PersonalSignalSet.CAUTIONARY_ONLY,
                    action=PersonalDecisionAction.BUY,
                ),
            ),
        )
        assert result.status is AVAILABLE
        assert result.action is PersonalDecisionAction.BUY

    @pytest.mark.parametrize("signal", [SUPPORTING, CAUTIONARY])
    def test_a_direction_alone_yields_no_action(self, signal: PersonalDecisionSignal) -> None:
        result = evaluate_personal_decision_policy(_eligible(signal=signal))
        assert result.status is NO_POLICY
        assert result.action is None

    def test_mixed_alone_yields_no_action(self) -> None:
        aggregation = _aggregation(
            claims=(
                _mapped(rule_id=RULE_A, signal=SUPPORTING),
                _mapped(rule_id=RULE_B, signal=CAUTIONARY, claim_key="claim.synthetic.b"),
            )
        )
        result = evaluate_personal_decision_policy(aggregation)
        assert result.status is NO_POLICY
        assert result.action is None

    def test_a_policy_id_that_reads_like_an_action_is_just_a_name(self) -> None:
        result = evaluate_personal_decision_policy(
            _eligible(),
            rules=(
                _policy(
                    policy_id="policy.synthetic.caution.severe.avoid",
                    action=PersonalDecisionAction.BUY,
                ),
            ),
        )
        assert result.action is PersonalDecisionAction.BUY


# ---------------------------------------------------------------------------
# Registry validation
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_empty_registry_builds(self) -> None:
        assert build_policy_index(()) == {}

    def test_valid_registry_indexes(self) -> None:
        rule = _policy()
        index = build_policy_index((rule,))
        assert index[rule.target] is rule

    @pytest.mark.parametrize(
        ("kwargs", "fragment"),
        [
            ({"policy_id": "   "}, "blank policy_id"),
            ({"policy_version": ""}, "blank policy_version"),
            ({"identities": frozenset()}, "empty semantic identity set"),
        ],
    )
    def test_malformed_rules_are_rejected(self, kwargs: dict, fragment: str) -> None:
        with pytest.raises(PersonalDecisionPolicyRegistryError, match=fragment):
            build_policy_index((_policy(**kwargs),))

    def test_non_rule_object_rejected(self) -> None:
        with pytest.raises(PersonalDecisionPolicyRegistryError, match="not a PersonalDecisionPolicyRule"):
            build_policy_index(("not a rule",))  # type: ignore[arg-type]

    def test_invalid_category_rejected(self) -> None:
        rule = _corrupt(_policy(), category="skin_care")
        with pytest.raises(PersonalDecisionPolicyRegistryError, match="invalid category"):
            build_policy_index((rule,))  # type: ignore[arg-type]

    def test_invalid_signal_set_rejected(self) -> None:
        rule = _corrupt(_policy(), signal_set="supporting_only")
        with pytest.raises(PersonalDecisionPolicyRegistryError, match="invalid signal_set"):
            build_policy_index((rule,))  # type: ignore[arg-type]

    def test_none_signal_set_rejected(self) -> None:
        with pytest.raises(PersonalDecisionPolicyRegistryError, match="empty direction set"):
            build_policy_index((_policy(signal_set=PersonalSignalSet.NONE),))

    def test_invalid_action_rejected(self) -> None:
        rule = _corrupt(_policy(), action="buy")
        with pytest.raises(PersonalDecisionPolicyRegistryError, match="invalid action"):
            build_policy_index((rule,))  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "flag",
        ["has_identity_unresolved", "has_identity_ambiguous", "has_personal_evidence_gap"],
    )
    def test_non_boolean_gap_flags_rejected(self, flag: str) -> None:
        rule = _corrupt(_policy(), **{flag: 1})
        with pytest.raises(PersonalDecisionPolicyRegistryError, match=f"non-boolean {flag}"):
            build_policy_index((rule,))  # type: ignore[arg-type]

    def test_identity_collection_must_be_a_frozenset(self) -> None:
        rule = _corrupt(_policy(), semantic_rule_identities={(RULE_A, "1")})
        with pytest.raises(PersonalDecisionPolicyRegistryError, match="frozenset"):
            build_policy_index((rule,))  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("identity", "fragment"),
        [
            ((RULE_A,), "malformed semantic identity"),
            ((RULE_A, "1", "extra"), "malformed semantic identity"),
            (("  ", "1"), "blank semantic rule id"),
            ((RULE_A, ""), "blank semantic rule version"),
            ((RULE_A, 1), "blank semantic rule version"),
        ],
    )
    def test_malformed_semantic_identities_rejected(self, identity: tuple, fragment: str) -> None:
        with pytest.raises(PersonalDecisionPolicyRegistryError, match=fragment):
            build_policy_index((_policy(identities=frozenset({identity})),))

    def test_duplicate_policy_identity_rejected(self) -> None:
        first = _policy(identities=frozenset({(RULE_A, "1")}))
        second = _policy(identities=frozenset({(RULE_B, "1")}))
        with pytest.raises(PersonalDecisionPolicyRegistryError, match="duplicate policy identity"):
            build_policy_index((first, second))

    def test_duplicate_target_with_different_action_rejected(self) -> None:
        first = _policy(policy_id="policy.synthetic.one", action=PersonalDecisionAction.BUY)
        second = _policy(policy_id="policy.synthetic.two", action=PersonalDecisionAction.SKIP)
        with pytest.raises(PersonalDecisionPolicyRegistryError, match="same governed state"):
            build_policy_index((first, second))

    def test_duplicate_target_with_identical_action_still_rejected(self) -> None:
        first = _policy(policy_id="policy.synthetic.one", action=PersonalDecisionAction.BUY)
        second = _policy(policy_id="policy.synthetic.two", action=PersonalDecisionAction.BUY)
        with pytest.raises(PersonalDecisionPolicyRegistryError, match="same governed state"):
            build_policy_index((first, second))

    def test_declaration_order_never_resolves_a_conflict(self) -> None:
        first = _policy(policy_id="policy.synthetic.one", action=PersonalDecisionAction.BUY)
        second = _policy(policy_id="policy.synthetic.two", action=PersonalDecisionAction.SKIP)
        for ordering in ((first, second), (second, first)):
            with pytest.raises(PersonalDecisionPolicyRegistryError):
                build_policy_index(ordering)

    def test_a_later_policy_version_does_not_supersede_an_earlier_one(self) -> None:
        version_one = _policy(policy_version="1", action=PersonalDecisionAction.BUY)
        version_two = _policy(policy_version="2", action=PersonalDecisionAction.SKIP)
        with pytest.raises(PersonalDecisionPolicyRegistryError, match="same governed state"):
            build_policy_index((version_one, version_two))

    def test_distinct_policy_versions_on_distinct_targets_coexist(self) -> None:
        version_one = _policy(policy_version="1", identities=frozenset({(RULE_A, "1")}))
        version_two = _policy(policy_version="2", identities=frozenset({(RULE_B, "1")}))
        assert len(build_policy_index((version_one, version_two))) == 2

    def test_a_broken_registry_surfaces_on_an_eligible_state(self) -> None:
        with pytest.raises(PersonalDecisionPolicyRegistryError):
            evaluate_personal_decision_policy(_eligible(), rules=BROKEN_REGISTRY)


# ---------------------------------------------------------------------------
# Result invariants
# ---------------------------------------------------------------------------


class TestResultInvariants:
    def test_decision_requires_full_policy_provenance(self) -> None:
        with pytest.raises(ValueError, match="requires action, policy_id and policy_version"):
            PersonalDecisionPolicyResult(
                source_aggregation=_eligible(),
                status=AVAILABLE,
                reason=None,
                action=PersonalDecisionAction.BUY,
                policy_id=None,
                policy_version="1",
            )

    def test_decision_carries_no_blocking_reason(self) -> None:
        with pytest.raises(ValueError, match="carries no blocking reason"):
            PersonalDecisionPolicyResult(
                source_aggregation=_eligible(),
                status=AVAILABLE,
                reason=R_NO_RULE,
                action=PersonalDecisionAction.BUY,
                policy_id=POLICY_A,
                policy_version="1",
            )

    @pytest.mark.parametrize("status", [NOT_ENOUGH_INFO, NO_POLICY, HANDOFF])
    def test_a_blocked_status_cannot_smuggle_an_action(
        self, status: PersonalDecisionPolicyStatus
    ) -> None:
        with pytest.raises(ValueError, match="no action and no policy provenance"):
            PersonalDecisionPolicyResult(
                source_aggregation=_eligible(),
                status=status,
                reason=R_NO_RULE,
                action=PersonalDecisionAction.WAIT,
                policy_id=POLICY_A,
                policy_version="1",
            )

    def test_handoff_requires_its_own_reason(self) -> None:
        with pytest.raises(ValueError, match="PROFESSIONAL_HANDOFF_REQUIRED"):
            PersonalDecisionPolicyResult(
                source_aggregation=_eligible(),
                status=HANDOFF,
                reason=R_CONTEXT,
                action=None,
                policy_id=None,
                policy_version=None,
            )

    @pytest.mark.parametrize("reason", [R_HANDOFF, R_NO_RULE, None])
    def test_not_enough_information_requires_a_structural_reason(
        self, reason: PersonalDecisionPolicyReason | None
    ) -> None:
        with pytest.raises(ValueError, match="structural blocking reason"):
            PersonalDecisionPolicyResult(
                source_aggregation=_eligible(),
                status=NOT_ENOUGH_INFO,
                reason=reason,
                action=None,
                policy_id=None,
                policy_version=None,
            )

    def test_no_policy_requires_no_exact_policy_rule(self) -> None:
        with pytest.raises(ValueError, match="NO_EXACT_POLICY_RULE"):
            PersonalDecisionPolicyResult(
                source_aggregation=_eligible(),
                status=NO_POLICY,
                reason=R_CONTEXT,
                action=None,
                policy_id=None,
                policy_version=None,
            )

    def test_results_are_frozen_and_slotted(self) -> None:
        assert PersonalDecisionPolicyResult.__dataclass_params__.frozen
        assert PersonalDecisionPolicyRule.__dataclass_params__.frozen
        assert getattr(PersonalDecisionPolicyResult, "__slots__", None) is not None
        assert getattr(PersonalDecisionPolicyRule, "__slots__", None) is not None


# ---------------------------------------------------------------------------
# Corrupted upstream input fails closed
# ---------------------------------------------------------------------------


def _occurrence() -> PersonalDecisionSignalOccurrence:
    return PersonalDecisionSignalOccurrence(
        ingredient_position=0,
        substance_key=SUBSTANCE_A,
        claim_id=uuid.uuid4(),
        claim_key=CLAIM_A,
        claim_version=2,
    )


def _aggregated_rule(
    *, rule_id: str = RULE_A, rule_version: str = "1", signal: PersonalDecisionSignal = SUPPORTING
) -> AggregatedPersonalDecisionRule:
    return AggregatedPersonalDecisionRule(
        rule_id=rule_id,
        rule_version=rule_version,
        signal=signal,
        substance_key=SUBSTANCE_A,
        claim_key=CLAIM_A,
        claim_version=2,
        occurrences=(_occurrence(),),
    )


def _unmapped_claim() -> UnmappedPersonalDecisionClaim:
    return UnmappedPersonalDecisionClaim(
        ingredient_position=0,
        substance_key=SUBSTANCE_A,
        claim_id=uuid.uuid4(),
        claim_key="claim.synthetic.z",
        claim_version=1,
    )


class TestCorruptedUpstream:
    @pytest.mark.parametrize(
        ("coverage", "fields", "fragment"),
        [
            (
                PersonalSemanticMappingCoverage.NO_CLAIM_PROJECTIONS,
                {"rules": (_aggregated_rule(),)},
                "aggregated rules",
            ),
            (
                PersonalSemanticMappingCoverage.NO_CLAIM_PROJECTIONS,
                {"rules": (), "unmapped_claims": (_unmapped_claim(),)},
                "unmapped claims",
            ),
            (
                PersonalSemanticMappingCoverage.NO_MAPPED_SEMANTICS,
                {"rules": (_aggregated_rule(),), "unmapped_claims": (_unmapped_claim(),)},
                "aggregated rules",
            ),
            (
                PersonalSemanticMappingCoverage.PARTIAL_SEMANTIC_MAPPING,
                {"rules": (), "unmapped_claims": (_unmapped_claim(),)},
                "aggregated rules",
            ),
            (
                PersonalSemanticMappingCoverage.PARTIAL_SEMANTIC_MAPPING,
                {"rules": (_aggregated_rule(),), "unmapped_claims": ()},
                "unmapped claims",
            ),
            (
                PersonalSemanticMappingCoverage.COMPLETE_SEMANTIC_MAPPING,
                {"rules": ()},
                "aggregated rules",
            ),
            (
                PersonalSemanticMappingCoverage.COMPLETE_SEMANTIC_MAPPING,
                {"rules": (_aggregated_rule(),), "unmapped_claims": (_unmapped_claim(),)},
                "unmapped claims",
            ),
        ],
    )
    def test_coverage_shape_contradictions(
        self, coverage: PersonalSemanticMappingCoverage, fields: dict, fragment: str
    ) -> None:
        aggregation = _eligible()
        _corrupt(aggregation, mapping_coverage=coverage, **fields)
        with pytest.raises(PersonalDecisionPolicyInvariantError, match=fragment):
            evaluate_personal_decision_policy(aggregation)

    def test_complete_with_no_direction_set(self) -> None:
        aggregation = _eligible()
        _corrupt(aggregation, signal_set=PersonalSignalSet.NONE)
        with pytest.raises(PersonalDecisionPolicyInvariantError, match="disagrees with the directions"):
            evaluate_personal_decision_policy(aggregation)

    def test_stored_direction_set_disagreeing_with_its_rules(self) -> None:
        aggregation = _eligible(signal=SUPPORTING)
        _corrupt(aggregation, signal_set=PersonalSignalSet.CAUTIONARY_ONLY)
        with pytest.raises(PersonalDecisionPolicyInvariantError, match="disagrees with the directions"):
            evaluate_personal_decision_policy(aggregation)

    def test_duplicate_aggregated_rule_identity(self) -> None:
        aggregation = _eligible()
        _corrupt(aggregation, rules=(_aggregated_rule(), _aggregated_rule()))
        with pytest.raises(PersonalDecisionPolicyInvariantError, match="appears more than once"):
            evaluate_personal_decision_policy(aggregation)

    def test_direction_outside_the_vocabulary(self) -> None:
        rule = _corrupt(_aggregated_rule(), signal="strongly_supporting")
        aggregation = _eligible()
        _corrupt(aggregation, rules=(rule,))
        with pytest.raises(PersonalDecisionPolicyInvariantError, match="outside"):
            evaluate_personal_decision_policy(aggregation)

    @pytest.mark.parametrize(("field", "value"), [("rule_id", "  "), ("rule_version", "")])
    def test_blank_aggregated_rule_identity(self, field: str, value: str) -> None:
        rule = _corrupt(_aggregated_rule(), **{field: value})
        aggregation = _eligible()
        _corrupt(aggregation, rules=(rule,))
        with pytest.raises(PersonalDecisionPolicyInvariantError, match="blank"):
            evaluate_personal_decision_policy(aggregation)

    def test_unrecognised_mapping_coverage(self) -> None:
        aggregation = _eligible()
        _corrupt(aggregation, mapping_coverage="mostly_mapped")
        with pytest.raises(PersonalDecisionPolicyInvariantError, match="unrecognised"):
            evaluate_personal_decision_policy(aggregation)

    def test_unknown_category(self) -> None:
        aggregation = _eligible()
        _corrupt(aggregation.source_semantics, category="beverages")
        with pytest.raises(PersonalDecisionPolicyInvariantError, match="outside the governed policy vocabulary"):
            evaluate_personal_decision_policy(aggregation)

    def test_unknown_context_status(self) -> None:
        aggregation = _eligible()
        _corrupt(aggregation.source_semantics, context_status="mostly_available")
        with pytest.raises(PersonalDecisionPolicyInvariantError, match="context status"):
            evaluate_personal_decision_policy(aggregation)

    def test_unknown_formula_status(self) -> None:
        aggregation = _eligible()
        _corrupt(aggregation.source_semantics, formula_status="partially_parsed")
        with pytest.raises(PersonalDecisionPolicyInvariantError, match="formula status"):
            evaluate_personal_decision_policy(aggregation)

    def test_unknown_applicability_status(self) -> None:
        aggregation = _eligible()
        _corrupt(aggregation.source_semantics.ingredients[0], personal_applicability_status="maybe")
        with pytest.raises(PersonalDecisionPolicyInvariantError, match="applicability status"):
            evaluate_personal_decision_policy(aggregation)


# ---------------------------------------------------------------------------
# Source preservation
# ---------------------------------------------------------------------------


class TestSourcePreservation:
    def test_the_aggregation_is_the_same_object(self) -> None:
        aggregation = _eligible()
        result = evaluate_personal_decision_policy(aggregation)
        assert result.source_aggregation is aggregation

    def test_the_whole_upstream_chain_is_reachable_unchanged(self) -> None:
        aggregation = _eligible()
        result = evaluate_personal_decision_policy(aggregation)
        assert result.source_aggregation.source_semantics is aggregation.source_semantics
        assert result.source_aggregation.rules is aggregation.rules
        assert (
            result.source_aggregation.source_semantics.ingredients
            is aggregation.source_semantics.ingredients
        )

    def test_source_is_preserved_on_every_blocked_path(self) -> None:
        for aggregation in (
            _aggregation(context_status="handoff_required", ingredients=()),
            _aggregation(claims=(_mapped(),), context_status="partial_context"),
            _aggregation(claims=(_mapped(),), formula_status="malformed"),
            _aggregation(claims=(_unmapped(),)),
            _eligible(),
        ):
            result = evaluate_personal_decision_policy(aggregation)
            assert result.source_aggregation is aggregation


# ---------------------------------------------------------------------------
# Static guards
# ---------------------------------------------------------------------------


def _production_sources() -> list[tuple[str, ast.Module]]:
    return [
        (path.name, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(SERVICE_PATH.glob("*.py"))
    ]


def _docstring_node_ids(tree: ast.Module) -> set[int]:
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
    """Identifiers and non-docstring string literals: what actually runs.

    Comments never reach the AST and docstrings are dropped, so the prose that
    explains these prohibitions cannot be mistaken for code that breaks them.
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


_WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+")


class TestStaticGuards:
    def test_module_set_is_exact(self) -> None:
        assert {path.name for path in SERVICE_PATH.glob("*.py")} == {
            "__init__.py",
            "enums.py",
            "rules.py",
            "service.py",
        }

    def test_step8d_is_the_only_upstream_application_dependency(self) -> None:
        seen: set[str] = set()
        for _name, tree in _production_sources():
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                seen.update(
                    module.split(".")[2]
                    for module in modules
                    if module.startswith("app.domains.")
                )
        # An allowlist, so a brand-new lower-domain import fails even though no
        # denylist mentions it.
        assert seen == {"personal_decision_aggregation", "personal_decision_policy"}

    def test_no_database_or_network_imports(self) -> None:
        banned = (
            "sqlalchemy", "asyncpg", "httpx", "requests", "aiohttp", "fastapi", "openai", "google",
        )
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

    def test_forbidden_upstream_attributes_are_never_read(self) -> None:
        forbidden = {
            "summary",
            "scope",
            "evidence_strength",
            "evidence_tier",
            "sources",
            "matched_facts",
            "candidate_substance_keys",
            "raw_name",
            "normalized_name",
            "identity_status",
            "entity_kind",
            "claims",
        }
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in forbidden:
                    offenders.append(f"{name}:{node.lineno} reads .{node.attr}")
        assert offenders == [], offenders

    def test_no_async_functions(self) -> None:
        offenders: list[str] = []
        for name, tree in _production_sources():
            offenders.extend(
                f"{name}: async def {node.name}"
                for node in ast.walk(tree)
                if isinstance(node, ast.AsyncFunctionDef)
            )
        assert offenders == [], offenders

    def test_public_entry_point_takes_only_the_step8d_result(self) -> None:
        signature = inspect.signature(evaluate_personal_decision_policy)
        assert list(signature.parameters) == ["aggregation", "rules"]
        positional, seam = signature.parameters.values()
        assert positional.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert positional.default is inspect.Parameter.empty
        assert seam.kind is inspect.Parameter.KEYWORD_ONLY
        assert seam.default == ()
        assert not inspect.iscoroutinefunction(evaluate_personal_decision_policy)

    def test_no_action_member_is_ever_named_in_production(self) -> None:
        """The strongest form of "no implicit direction-to-action mapping".

        Production never writes ``PersonalDecisionAction.BUY`` or its siblings
        anywhere, because the only source of an action is ``rule.action`` read
        off a matched policy. A lookup table keyed on a direction, or an ``if``
        returning an action, would both have to name a member to exist.
        """
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                if isinstance(node.value, ast.Name) and node.value.id == "PersonalDecisionAction":
                    offenders.append(f"{name}:{node.lineno}: PersonalDecisionAction.{node.attr}")
        assert offenders == [], offenders

    def test_action_is_only_ever_taken_from_a_matched_policy_rule(self) -> None:
        """Every non-None ``action=`` argument comes from a ``.action`` read."""
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "action":
                        continue
                    value = keyword.value
                    if isinstance(value, ast.Constant) and value.value is None:
                        continue
                    if isinstance(value, ast.Attribute) and value.attr == "action":
                        continue
                    offenders.append(f"{name}:{node.lineno}: action={ast.dump(value)[:60]}")
        assert offenders == [], offenders

    def test_an_action_is_never_constructed_in_production(self) -> None:
        """Closes the last route to an action that is not a policy's own.

        Naming a member is already banned above, so the only remaining way to
        conjure one -- and the way a ``{"supporting_only": "buy"}`` table built
        from strings would have to do it -- is calling the enum. Production
        never does; it reads ``.action`` off a matched rule and nothing else.
        """
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                callee = node.func
                if isinstance(callee, ast.Name) and callee.id == "PersonalDecisionAction":
                    offenders.append(f"{name}:{node.lineno}: PersonalDecisionAction(...)")
        assert offenders == [], offenders

    def test_no_scoring_or_voting_vocabulary(self) -> None:
        banned_words = {
            "average",
            "confidence",
            "count",
            "counts",
            "dominant",
            "grade",
            "magnitude",
            "majority",
            "net",
            "percentage",
            "points",
            "precedence",
            "priority",
            "rank",
            "ranked",
            "ranking",
            "rating",
            "ratio",
            "score",
            "scores",
            "scoring",
            "specificity",
            "sum",
            "tally",
            "threshold",
            "total",
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
                offenders.extend(
                    f"{name}:{line}: {word} in {text}"
                    for word in _WORD.findall(text)
                    if word.lower() in banned_words
                )
        assert offenders == [], offenders

    def test_no_ordering_comparison_in_production(self) -> None:
        """Exact lookup needs no ``<`` or ``>``; a score or a tie-break would."""
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                offenders.extend(
                    f"{name}:{node.lineno}: {type(op).__name__}"
                    for op in node.ops
                    if isinstance(op, ast.Lt | ast.LtE | ast.Gt | ast.GtE)
                )
        assert offenders == [], offenders


def test_public_surface_is_stable() -> None:
    assert set(PersonalDecisionAction) == {
        PersonalDecisionAction.BUY,
        PersonalDecisionAction.WAIT,
        PersonalDecisionAction.SKIP,
    }
    assert set(PersonalDecisionPolicyStatus) == {AVAILABLE, NOT_ENOUGH_INFO, NO_POLICY, HANDOFF}
    assert set(PersonalDecisionPolicyReason) == {
        R_HANDOFF,
        R_CONTEXT,
        R_FORMULA,
        R_MAPPING,
        R_NO_RULE,
    }
    assert set(PersonalDecisionPolicyCategory) == {
        PersonalDecisionPolicyCategory.PACKAGED_FOOD,
        PersonalDecisionPolicyCategory.SKIN_CARE,
        PersonalDecisionPolicyCategory.HAIR_CARE,
        PersonalDecisionPolicyCategory.COSMETICS,
    }
    assert isinstance(evaluate_personal_decision_policy(_eligible()), PersonalDecisionPolicyResult)
