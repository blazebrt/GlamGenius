"""Step 8F — governed personal decision explanation and source contract.

These tests drive the *real* governed chain end to end with synthetic
registries: a Step 8B result goes through the real Step 8C, Step 8D and
Step 8E functions before reaching Step 8F. That matters, because the whole
milestone rests on provenance surviving four hand-offs; a test that hand-built
a Step 8E result would prove the lookup works and nothing about the chain.

The adversarial cases are the point:

- a reviewed BUY with no reviewed explanation must come back with
  ``action=None``, not BUY;
- with two eligible sources on the claim, the one the reviewer named must win
  regardless of order, and removing it must block the decision rather than
  fall back to the other;
- changing prose, evidence strength, matched facts and source titles must
  change nothing about what was selected.
"""

from __future__ import annotations

import ast
import inspect
import re
import uuid
from datetime import date
from pathlib import Path

import pytest
from app.domains.personal_applicability import (
    ApplicableSubstancePersonalClaim,
    IngredientPersonalApplicability,
    LabelSnapshotPersonalApplicability,
    MatchedPersonalFact,
    PersonalApplicabilityCategory,
    PersonalApplicabilityStatus,
)
from app.domains.personal_applicability.service import PersonalApplicabilitySource
from app.domains.personal_decision_aggregation import (
    PersonalDecisionSignalOccurrence,
    aggregate_personal_decision_signals,
)
from app.domains.personal_decision_explanation import (
    PERSONAL_DECISION_EXPLANATION_RULES,
    PersonalDecisionExplanationRegistryError,
    PersonalDecisionExplanationRule,
    PersonalDecisionPresentation,
    PersonalDecisionPresentationInvariantError,
    PersonalDecisionPresentationReason,
    PersonalDecisionPresentationStatus,
    PersonalDecisionSourceCitation,
    build_explanation_index,
    present_personal_decision,
)
from app.domains.personal_decision_policy import (
    PERSONAL_DECISION_POLICY_RULES,
    PersonalDecisionAction,
    PersonalDecisionPolicyCategory,
    PersonalDecisionPolicyResult,
    PersonalDecisionPolicyRule,
    PersonalDecisionPolicyStatus,
    evaluate_personal_decision_policy,
)
from app.domains.personal_decision_semantics import (
    PERSONAL_DECISION_SEMANTIC_RULES,
    PersonalDecisionSemanticRule,
    PersonalDecisionSignal,
    project_personal_decision_semantics,
)
from app.domains.personal_lens.service import PersonalLensHandoff
from app.domains.substance_interpretation import ProjectedIdentityStatus

SERVICE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "domains" / "personal_decision_explanation"
)

SUBSTANCE_A = "substance.synthetic.a"
SUBSTANCE_B = "substance.synthetic.b"
CLAIM_A = "claim.synthetic.a"
CLAIM_VERSION = 2
SEMANTIC_A = "semantic.synthetic.a"
POLICY_A = "policy.synthetic.a"
EXPLANATION_A = "explanation.synthetic.a"
SOURCE_A = "source.synthetic.a"
SOURCE_B = "source.synthetic.b"
LOCATOR_A = "Section 3"
LOCATOR_B = "Table 2"
REASON_KEY = "for_you.reason.synthetic.a"

PRESENTABLE = PersonalDecisionPresentationStatus.DECISION_PRESENTABLE
NO_INFO = PersonalDecisionPresentationStatus.NOT_ENOUGH_INFORMATION
NO_POLICY = PersonalDecisionPresentationStatus.NOT_ENOUGH_DECISION_POLICY
NO_EXPLANATION = PersonalDecisionPresentationStatus.NOT_ENOUGH_EXPLANATION
HANDOFF = PersonalDecisionPresentationStatus.HANDOFF_REQUIRED


# ---------------------------------------------------------------------------
# Synthetic governed chain, built with the real upstream functions
# ---------------------------------------------------------------------------


def _source(
    *,
    source_key: str = SOURCE_A,
    locator: str | None = LOCATOR_A,
    title: str = "Synthetic reviewed reference",
    publisher: str = "Synthetic Publisher",
    canonical_url: str = "https://example.invalid/synthetic-a",
    publication_date: date | None = date(2024, 1, 1),
) -> PersonalApplicabilitySource:
    return PersonalApplicabilitySource(
        source_id=uuid.uuid4(),
        source_key=source_key,
        source_type="peer_reviewed_research",
        title=title,
        publisher=publisher,
        canonical_url=canonical_url,
        locator=locator,
        publication_date=publication_date,
        version_or_revision="1",
        jurisdiction="IN",
    )


def _claim(
    *,
    claim_key: str = CLAIM_A,
    claim_version: int = CLAIM_VERSION,
    sources: tuple[PersonalApplicabilitySource, ...] | None = None,
    summary: str = "synthetic summary",
    scope: str = "synthetic scope",
    evidence_strength: str = "moderate",
    evidence_tier: str = "clinically_studied",
    matched_facts: tuple[MatchedPersonalFact, ...] = (),
) -> ApplicableSubstancePersonalClaim:
    return ApplicableSubstancePersonalClaim(
        claim_id=uuid.uuid4(),
        claim_key=claim_key,
        claim_version=claim_version,
        summary=summary,
        scope=scope,
        evidence_strength=evidence_strength,
        evidence_tier=evidence_tier,
        matched_facts=matched_facts,
        sources=sources if sources is not None else (_source(),),
    )


def _ingredient(
    *,
    position: int = 0,
    substance_key: str | None = SUBSTANCE_A,
    status: PersonalApplicabilityStatus = PersonalApplicabilityStatus.PERSONAL_EVIDENCE_AVAILABLE,
    claims: tuple[ApplicableSubstancePersonalClaim, ...] = (),
) -> IngredientPersonalApplicability:
    return IngredientPersonalApplicability(
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


def _applicability(
    *,
    ingredients: tuple[IngredientPersonalApplicability, ...] | None = None,
    category: PersonalApplicabilityCategory = PersonalApplicabilityCategory.SKIN_CARE,
    context_status: object = "context_available",
    formula_status: str | None = "parsed",
    handoff: object | None = None,
) -> LabelSnapshotPersonalApplicability:
    if ingredients is None:
        ingredients = (_ingredient(claims=(_claim(),)),)
    return LabelSnapshotPersonalApplicability(
        provenance=None,
        category=category,
        formula_status=formula_status,
        profile_id=uuid.uuid4(),
        profile_version=3,
        context_status=context_status,
        ingredients=ingredients,
        handoff=handoff,
    )


def _semantic_rule(
    *,
    rule_id: str = SEMANTIC_A,
    rule_version: str = "1",
    category: PersonalApplicabilityCategory = PersonalApplicabilityCategory.SKIN_CARE,
    substance_key: str = SUBSTANCE_A,
    claim_key: str = CLAIM_A,
    claim_version: int = CLAIM_VERSION,
    signal: PersonalDecisionSignal = PersonalDecisionSignal.SUPPORTING,
) -> PersonalDecisionSemanticRule:
    return PersonalDecisionSemanticRule(
        rule_id=rule_id,
        rule_version=rule_version,
        category=category,
        substance_key=substance_key,
        claim_key=claim_key,
        claim_version=claim_version,
        signal=signal,
    )


def _policy_rule(
    *,
    policy_id: str = POLICY_A,
    policy_version: str = "1",
    action: PersonalDecisionAction = PersonalDecisionAction.BUY,
    identities: frozenset[tuple[str, str]] | None = None,
    signal_set: object = None,
    unresolved: bool = False,
    ambiguous: bool = False,
    evidence_gap: bool = False,
) -> PersonalDecisionPolicyRule:
    from app.domains.personal_decision_aggregation import PersonalSignalSet

    return PersonalDecisionPolicyRule(
        policy_id=policy_id,
        policy_version=policy_version,
        category=PersonalDecisionPolicyCategory.SKIN_CARE,
        semantic_rule_identities=(
            identities if identities is not None else frozenset({(SEMANTIC_A, "1")})
        ),
        signal_set=signal_set or PersonalSignalSet.SUPPORTING_ONLY,
        has_identity_unresolved=unresolved,
        has_identity_ambiguous=ambiguous,
        has_personal_evidence_gap=evidence_gap,
        action=action,
    )


def _explanation(
    *,
    explanation_id: str = EXPLANATION_A,
    explanation_version: str = "1",
    policy_id: str = POLICY_A,
    policy_version: str = "1",
    action: PersonalDecisionAction = PersonalDecisionAction.BUY,
    semantic_rule_id: str = SEMANTIC_A,
    semantic_rule_version: str = "1",
    substance_key: str = SUBSTANCE_A,
    claim_key: str = CLAIM_A,
    claim_version: int = CLAIM_VERSION,
    source_key: str = SOURCE_A,
    source_locator: str | None = LOCATOR_A,
    reason_key: str = REASON_KEY,
) -> PersonalDecisionExplanationRule:
    return PersonalDecisionExplanationRule(
        explanation_id=explanation_id,
        explanation_version=explanation_version,
        policy_id=policy_id,
        policy_version=policy_version,
        action=action,
        semantic_rule_id=semantic_rule_id,
        semantic_rule_version=semantic_rule_version,
        substance_key=substance_key,
        claim_key=claim_key,
        claim_version=claim_version,
        source_key=source_key,
        source_locator=source_locator,
        reason_key=reason_key,
    )


def _policy_result(
    *,
    applicability: LabelSnapshotPersonalApplicability | None = None,
    semantic_rules: tuple[PersonalDecisionSemanticRule, ...] | None = None,
    policy_rules: tuple[PersonalDecisionPolicyRule, ...] | None = None,
) -> PersonalDecisionPolicyResult:
    """Run the real 8B→8C→8D→8E chain over synthetic registries."""
    semantics = project_personal_decision_semantics(
        applicability if applicability is not None else _applicability(),
        rules=semantic_rules if semantic_rules is not None else (_semantic_rule(),),
    )
    aggregation = aggregate_personal_decision_signals(semantics)
    return evaluate_personal_decision_policy(
        aggregation,
        rules=policy_rules if policy_rules is not None else (_policy_rule(),),
    )


def _decided() -> PersonalDecisionPolicyResult:
    """A Step 8E result that really did decide BUY, through the real chain."""
    policy = _policy_result()
    assert policy.status is PersonalDecisionPolicyStatus.DECISION_AVAILABLE
    assert policy.action is PersonalDecisionAction.BUY
    return policy


def _handoff_policy(
    *, reason: str = "pregnancy", message: str = "Please speak to a professional."
) -> PersonalDecisionPolicyResult:
    policy = _policy_result(
        applicability=_applicability(handoff=PersonalLensHandoff(reason=reason, message=message))
    )
    assert policy.status is PersonalDecisionPolicyStatus.HANDOFF_REQUIRED
    return policy


def _no_information_policy(**kwargs: object) -> PersonalDecisionPolicyResult:
    policy = _policy_result(applicability=_applicability(**kwargs))  # type: ignore[arg-type]
    assert policy.status is PersonalDecisionPolicyStatus.NOT_ENOUGH_INFORMATION
    return policy


def _undecided_policy() -> PersonalDecisionPolicyResult:
    policy = _policy_result(policy_rules=())
    assert policy.status is PersonalDecisionPolicyStatus.NOT_ENOUGH_DECISION_POLICY
    return policy


def _corrupt(obj: object, **fields: object) -> object:
    """Force fields past a frozen dataclass, to build impossible input.

    The governed dataclasses refuse to construct these shapes, which is the
    point: the only way to reach Step 8F's invariant errors is to assemble an
    object no valid upstream chain ever produced.
    """
    for name, value in fields.items():
        object.__setattr__(obj, name, value)
    return obj


#: An explanation registry that cannot be built.
BROKEN_REGISTRY = (_explanation(explanation_id="   "),)


# ---------------------------------------------------------------------------
# Production stays inert
# ---------------------------------------------------------------------------


class TestProductionInert:
    def test_every_governed_registry_is_still_empty(self) -> None:
        assert PERSONAL_DECISION_SEMANTIC_RULES == ()
        assert PERSONAL_DECISION_POLICY_RULES == ()
        assert PERSONAL_DECISION_EXPLANATION_RULES == ()

    def test_the_real_chain_with_real_defaults_presents_nothing(self) -> None:
        semantics = project_personal_decision_semantics(_applicability())
        aggregation = aggregate_personal_decision_signals(semantics)
        policy = evaluate_personal_decision_policy(aggregation)
        result = present_personal_decision(policy)
        # The empty semantic registry stops the chain before policy is even
        # reached: nothing is mapped, so nothing is decidable.
        assert result.status is NO_INFO
        assert result.reason is PersonalDecisionPresentationReason.SEMANTIC_MAPPING_NOT_COMPLETE
        assert result.action is None
        assert result.citation is None


# ---------------------------------------------------------------------------
# Source continuity through the whole chain
# ---------------------------------------------------------------------------


class TestSourceContinuity:
    def test_step8b_object_is_reachable_from_a_step8e_result(self) -> None:
        applicability = _applicability()
        policy = _policy_result(applicability=applicability)
        assert (
            policy.source_aggregation.source_semantics.source_personal_applicability
            is applicability
        )

    def test_the_exact_source_objects_survive_untouched(self) -> None:
        source = _source()
        claim = _claim(sources=(source,))
        applicability = _applicability(ingredients=(_ingredient(claims=(claim,)),))
        policy = _policy_result(applicability=applicability)

        carried = policy.source_aggregation.source_semantics.source_personal_applicability
        assert carried is not None
        assert carried.ingredients[0].claims[0] is claim
        assert carried.ingredients[0].claims[0].sources[0] is source


# ---------------------------------------------------------------------------
# Blocked upstream states
# ---------------------------------------------------------------------------


class TestBlockedPaths:
    def test_handoff_passes_the_canonical_message_through(self) -> None:
        handoff = PersonalLensHandoff(reason="pregnancy", message="Please speak to a professional.")
        policy = _policy_result(applicability=_applicability(handoff=handoff))
        assert policy.status is PersonalDecisionPolicyStatus.HANDOFF_REQUIRED

        result = present_personal_decision(policy)
        assert result.status is HANDOFF
        assert result.action is None
        assert result.handoff_reason == "pregnancy"
        assert result.handoff_message == "Please speak to a professional."

    def test_a_broken_registry_cannot_suppress_a_handoff(self) -> None:
        handoff = PersonalLensHandoff(reason="pregnancy", message="Please speak to a professional.")
        policy = _policy_result(applicability=_applicability(handoff=handoff))
        result = present_personal_decision(policy, rules=BROKEN_REGISTRY)
        assert result.status is HANDOFF
        assert result.handoff_message == "Please speak to a professional."

    @pytest.mark.parametrize(
        ("kwargs", "reason"),
        [
            (
                {"context_status": "partial_context"},
                PersonalDecisionPresentationReason.PERSONAL_CONTEXT_NOT_COMPLETE,
            ),
            (
                {"context_status": "not_enough_personal_context"},
                PersonalDecisionPresentationReason.PERSONAL_CONTEXT_NOT_COMPLETE,
            ),
            (
                {"formula_status": "malformed"},
                PersonalDecisionPresentationReason.FORMULA_NOT_PROJECTABLE,
            ),
            (
                {"formula_status": "empty"},
                PersonalDecisionPresentationReason.FORMULA_NOT_PROJECTABLE,
            ),
        ],
    )
    def test_structural_blocks_stay_not_enough_information(
        self, kwargs: dict, reason: PersonalDecisionPresentationReason
    ) -> None:
        policy = _policy_result(applicability=_applicability(**kwargs))
        result = present_personal_decision(policy)
        assert result.status is NO_INFO
        assert result.reason is reason
        assert result.action is None

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"context_status": "partial_context"},
            {"formula_status": "malformed"},
        ],
    )
    def test_a_structural_block_never_becomes_wait(self, kwargs: dict) -> None:
        """A WAIT explanation is injected and must not be reachable."""
        policy = _policy_result(applicability=_applicability(**kwargs))
        result = present_personal_decision(
            policy,
            rules=(_explanation(action=PersonalDecisionAction.WAIT),),
        )
        assert result.status is NO_INFO
        assert result.action is None
        assert result.verdict_key is None

    def test_incomplete_semantic_mapping_stays_not_enough_information(self) -> None:
        applicability = _applicability(
            ingredients=(
                _ingredient(
                    claims=(_claim(), _claim(claim_key="claim.synthetic.unmapped")),
                ),
            )
        )
        policy = _policy_result(applicability=applicability)
        result = present_personal_decision(policy, rules=(_explanation(),))
        assert result.status is NO_INFO
        assert result.reason is PersonalDecisionPresentationReason.SEMANTIC_MAPPING_NOT_COMPLETE
        assert result.action is None

    def test_no_decision_policy_stays_a_non_decision(self) -> None:
        policy = _policy_result(policy_rules=())
        assert policy.status is PersonalDecisionPolicyStatus.NOT_ENOUGH_DECISION_POLICY
        result = present_personal_decision(policy, rules=(_explanation(),))
        assert result.status is NO_POLICY
        assert result.reason is PersonalDecisionPresentationReason.NO_EXACT_DECISION_POLICY
        assert result.action is None


# ---------------------------------------------------------------------------
# The decisive case: a reviewed action with no reviewed explanation
# ---------------------------------------------------------------------------


class TestDecisionWithoutExplanation:
    def test_a_real_buy_is_withheld_without_an_explanation(self) -> None:
        """Step 8F is stricter than Step 8E, and this is where it shows."""
        policy = _decided()
        assert policy.action is PersonalDecisionAction.BUY

        result = present_personal_decision(policy)
        assert result.status is NO_EXPLANATION
        assert result.reason is PersonalDecisionPresentationReason.NO_EXACT_EXPLANATION_RULE
        assert result.action is None
        assert result.verdict_key is None
        assert result.citation is None
        assert result.explanation_id is None

    def test_the_withheld_action_is_still_inspectable_internally(self) -> None:
        result = present_personal_decision(_decided())
        assert result.source_policy.action is PersonalDecisionAction.BUY
        assert result.action is None


# ---------------------------------------------------------------------------
# The exact successful case
# ---------------------------------------------------------------------------


class TestPresentableDecision:
    def test_exact_explanation_produces_a_presentable_decision(self) -> None:
        source = _source()
        claim = _claim(sources=(source,))
        applicability = _applicability(ingredients=(_ingredient(claims=(claim,)),))
        policy = _policy_result(applicability=applicability)
        rule = _explanation()

        result = present_personal_decision(policy, rules=(rule,))

        assert result.status is PRESENTABLE
        assert result.reason is PersonalDecisionPresentationReason.REVIEWED_EXPLANATION_AVAILABLE
        assert result.action is policy.action is PersonalDecisionAction.BUY
        assert result.verdict_key == "for_you.verdict.buy"
        assert result.reason_key == REASON_KEY
        assert result.explanation_id == EXPLANATION_A
        assert result.explanation_version == "1"

    def test_the_citation_comes_from_the_exact_step8b_source_object(self) -> None:
        source = _source(
            title="Exact reviewed title",
            publisher="Exact Publisher",
            canonical_url="https://example.invalid/exact",
            publication_date=date(2023, 7, 14),
        )
        claim = _claim(sources=(source,))
        applicability = _applicability(ingredients=(_ingredient(claims=(claim,)),))
        policy = _policy_result(applicability=applicability)

        citation = present_personal_decision(policy, rules=(_explanation(),)).citation
        assert citation == PersonalDecisionSourceCitation(
            source_key=source.source_key,
            title="Exact reviewed title",
            publisher="Exact Publisher",
            canonical_url="https://example.invalid/exact",
            locator=LOCATOR_A,
            publication_date=date(2023, 7, 14),
            version_or_revision=source.version_or_revision,
            jurisdiction=source.jurisdiction,
        )

    @pytest.mark.parametrize(
        ("action", "verdict_key"),
        [
            (PersonalDecisionAction.BUY, "for_you.verdict.buy"),
            (PersonalDecisionAction.WAIT, "for_you.verdict.wait"),
            (PersonalDecisionAction.SKIP, "for_you.verdict.skip"),
        ],
    )
    def test_the_verdict_key_labels_the_action_step8e_chose(
        self, action: PersonalDecisionAction, verdict_key: str
    ) -> None:
        policy = _policy_result(policy_rules=(_policy_rule(action=action),))
        result = present_personal_decision(policy, rules=(_explanation(action=action),))
        assert result.action is policy.action is action
        assert result.verdict_key == verdict_key

    def test_an_unopenable_source_url_fails_closed(self) -> None:
        source = _source(canonical_url="not-a-url")
        applicability = _applicability(
            ingredients=(_ingredient(claims=(_claim(sources=(source,)),)),)
        )
        policy = _policy_result(applicability=applicability)
        with pytest.raises(PersonalDecisionPresentationInvariantError, match="openable"):
            present_personal_decision(policy, rules=(_explanation(),))


# ---------------------------------------------------------------------------
# Every mismatch independently blocks presentation
# ---------------------------------------------------------------------------


class TestMismatches:
    @pytest.mark.parametrize(
        ("kwargs", "reason"),
        [
            ({"policy_id": "policy.synthetic.other"}, "no_rule"),
            ({"policy_version": "2"}, "no_rule"),
            ({"action": PersonalDecisionAction.SKIP}, "no_rule"),
            ({"semantic_rule_id": "semantic.synthetic.other"}, "anchor"),
            ({"semantic_rule_version": "2"}, "anchor"),
            ({"substance_key": SUBSTANCE_B}, "anchor"),
            ({"claim_key": "claim.synthetic.other"}, "anchor"),
            ({"claim_version": 99}, "anchor"),
            ({"source_key": SOURCE_B}, "anchor"),
            ({"source_locator": LOCATOR_B}, "anchor"),
        ],
    )
    def test_each_mismatch_blocks_presentation(self, kwargs: dict, reason: str) -> None:
        result = present_personal_decision(_decided(), rules=(_explanation(**kwargs),))
        assert result.status is NO_EXPLANATION
        assert result.action is None
        assert result.citation is None
        expected = (
            PersonalDecisionPresentationReason.NO_EXACT_EXPLANATION_RULE
            if reason == "no_rule"
            else PersonalDecisionPresentationReason.EXPLANATION_SOURCE_NOT_AVAILABLE
        )
        assert result.reason is expected

    def test_a_missing_source_continuity_object_blocks_presentation(self) -> None:
        policy = _decided()
        object.__setattr__(
            policy.source_aggregation.source_semantics, "source_personal_applicability", None
        )
        result = present_personal_decision(policy, rules=(_explanation(),))
        assert result.status is NO_EXPLANATION
        assert result.reason is (
            PersonalDecisionPresentationReason.EXPLANATION_SOURCE_NOT_AVAILABLE
        )


# ---------------------------------------------------------------------------
# Source selection is by reviewed identity, never by appearance
# ---------------------------------------------------------------------------


class TestSourceSelection:
    def _two_source_policy(self) -> PersonalDecisionPolicyResult:
        first = _source(source_key=SOURCE_A, locator=LOCATOR_A, title="Aaa first listed")
        second = _source(
            source_key=SOURCE_B,
            locator=LOCATOR_B,
            title="Zzz second listed",
            canonical_url="https://example.invalid/synthetic-b",
        )
        claim = _claim(sources=(first, second))
        return _policy_result(
            applicability=_applicability(ingredients=(_ingredient(claims=(claim,)),))
        )

    def test_the_named_source_wins_even_when_listed_second(self) -> None:
        result = present_personal_decision(
            self._two_source_policy(),
            rules=(_explanation(source_key=SOURCE_B, source_locator=LOCATOR_B),),
        )
        assert result.status is PRESENTABLE
        assert result.citation is not None
        assert result.citation.source_key == SOURCE_B
        assert result.citation.canonical_url == "https://example.invalid/synthetic-b"

    def test_the_named_source_wins_when_listed_first(self) -> None:
        result = present_personal_decision(
            self._two_source_policy(),
            rules=(_explanation(source_key=SOURCE_A, source_locator=LOCATOR_A),),
        )
        assert result.citation is not None
        assert result.citation.source_key == SOURCE_A

    def test_removing_the_named_source_blocks_rather_than_falls_back(self) -> None:
        """The other eligible source must not be substituted."""
        only_a = _source(source_key=SOURCE_A, locator=LOCATOR_A)
        claim = _claim(sources=(only_a,))
        policy = _policy_result(
            applicability=_applicability(ingredients=(_ingredient(claims=(claim,)),))
        )
        result = present_personal_decision(
            policy, rules=(_explanation(source_key=SOURCE_B, source_locator=LOCATOR_B),)
        )
        assert result.status is NO_EXPLANATION
        assert result.citation is None

    def test_the_locator_is_part_of_the_identity(self) -> None:
        source = _source(source_key=SOURCE_A, locator=LOCATOR_A)
        policy = _policy_result(
            applicability=_applicability(
                ingredients=(_ingredient(claims=(_claim(sources=(source,)),)),)
            )
        )
        result = present_personal_decision(
            policy, rules=(_explanation(source_key=SOURCE_A, source_locator="Section 4"),)
        )
        assert result.status is NO_EXPLANATION

    def test_a_none_locator_must_match_exactly(self) -> None:
        source = _source(locator=None)
        policy = _policy_result(
            applicability=_applicability(
                ingredients=(_ingredient(claims=(_claim(sources=(source,)),)),)
            )
        )
        assert present_personal_decision(
            policy, rules=(_explanation(source_locator=None),)
        ).status is PRESENTABLE
        assert present_personal_decision(
            policy, rules=(_explanation(source_locator=LOCATOR_A),)
        ).status is NO_EXPLANATION

    def test_source_keys_are_not_normalised(self) -> None:
        source = _source(source_key=SOURCE_A)
        policy = _policy_result(
            applicability=_applicability(
                ingredients=(_ingredient(claims=(_claim(sources=(source,)),)),)
            )
        )
        for near_miss in (SOURCE_A.upper(), f" {SOURCE_A}", SOURCE_A.replace(".", "-")):
            result = present_personal_decision(policy, rules=(_explanation(source_key=near_miss),))
            assert result.status is NO_EXPLANATION, near_miss


# ---------------------------------------------------------------------------
# Prose, strength and facts cannot change what was selected
# ---------------------------------------------------------------------------


class TestSelectionIndependence:
    def _present(self, **claim_kwargs: object) -> PersonalDecisionPresentation:
        claim = _claim(**claim_kwargs)  # type: ignore[arg-type]
        policy = _policy_result(
            applicability=_applicability(ingredients=(_ingredient(claims=(claim,)),))
        )
        return present_personal_decision(policy, rules=(_explanation(),))

    def test_claim_prose_does_not_change_the_outcome(self) -> None:
        flattering = self._present(summary="amazing perfect", scope="always")
        alarming = self._present(summary="avoid irritation adverse", scope="never")
        for result in (flattering, alarming):
            assert result.status is PRESENTABLE
            assert result.action is PersonalDecisionAction.BUY
            assert result.reason_key == REASON_KEY
            assert result.citation is not None
            assert result.citation.source_key == SOURCE_A

    @pytest.mark.parametrize("strength", ["strong", "moderate", "limited"])
    def test_evidence_strength_does_not_change_the_outcome(self, strength: str) -> None:
        result = self._present(evidence_strength=strength)
        assert result.status is PRESENTABLE
        assert result.reason_key == REASON_KEY
        assert result.citation is not None
        assert result.citation.source_key == SOURCE_A

    @pytest.mark.parametrize("tier", ["clinically_studied", "professional_consensus"])
    def test_evidence_tier_does_not_change_the_outcome(self, tier: str) -> None:
        result = self._present(evidence_tier=tier)
        assert result.status is PRESENTABLE
        assert result.reason_key == REASON_KEY

    def test_matched_facts_do_not_change_the_outcome(self) -> None:
        facts = (
            MatchedPersonalFact(
                fact_key="care_skin_sensitivity",
                profile_attribute_id=uuid.uuid4(),
                value="often_reactive",
            ),
        )
        with_facts = self._present(matched_facts=facts)
        without = self._present(matched_facts=())
        assert with_facts.action is without.action
        assert with_facts.reason_key == without.reason_key
        assert with_facts.citation is not None
        assert without.citation is not None
        assert with_facts.citation.source_key == without.citation.source_key

    def test_source_display_metadata_does_not_change_selection(self) -> None:
        """Retitling a source must not move the citation to another one."""
        renamed = _source(title="Completely different title", publisher="Another Publisher")
        policy = _policy_result(
            applicability=_applicability(
                ingredients=(_ingredient(claims=(_claim(sources=(renamed,)),)),)
            )
        )
        result = present_personal_decision(policy, rules=(_explanation(),))
        assert result.status is PRESENTABLE
        assert result.citation is not None
        assert result.citation.source_key == SOURCE_A
        # Metadata is copied through, having played no part in choosing it.
        assert result.citation.title == "Completely different title"


# ---------------------------------------------------------------------------
# Duplicate occurrences are one evidence chain
# ---------------------------------------------------------------------------


class TestDuplicateOccurrence:
    def test_the_same_claim_at_several_positions_is_one_chain(self) -> None:
        claim = _claim()
        applicability = _applicability(
            ingredients=tuple(
                _ingredient(position=position, claims=(claim,)) for position in (0, 3, 8)
            )
        )
        policy = _policy_result(applicability=applicability)
        assert len(policy.source_aggregation.rules) == 1
        assert len(policy.source_aggregation.rules[0].occurrences) == 3

        repeated = present_personal_decision(policy, rules=(_explanation(),))
        once = present_personal_decision(_decided(), rules=(_explanation(),))

        assert repeated.status is once.status is PRESENTABLE
        assert repeated.action is once.action
        assert repeated.reason_key == once.reason_key
        assert repeated.citation is not None
        assert once.citation is not None
        assert repeated.citation.source_key == once.citation.source_key


# ---------------------------------------------------------------------------
# Registry validation
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_empty_registry_builds(self) -> None:
        assert build_explanation_index(()) == {}

    def test_valid_registry_indexes(self) -> None:
        rule = _explanation()
        assert build_explanation_index((rule,))[rule.target] is rule

    @pytest.mark.parametrize(
        "field",
        [
            "explanation_id",
            "explanation_version",
            "policy_id",
            "policy_version",
            "semantic_rule_id",
            "semantic_rule_version",
            "substance_key",
            "claim_key",
            "source_key",
            "reason_key",
        ],
    )
    def test_blank_text_fields_are_rejected(self, field: str) -> None:
        with pytest.raises(PersonalDecisionExplanationRegistryError, match=f"blank {field}"):
            build_explanation_index((_explanation(**{field: "   "}),))

    def test_non_rule_object_rejected(self) -> None:
        with pytest.raises(PersonalDecisionExplanationRegistryError, match="not a Personal"):
            build_explanation_index(("nope",))  # type: ignore[arg-type]

    def test_invalid_action_rejected(self) -> None:
        rule = _explanation()
        object.__setattr__(rule, "action", "buy")
        with pytest.raises(PersonalDecisionExplanationRegistryError, match="invalid action"):
            build_explanation_index((rule,))

    @pytest.mark.parametrize("claim_version", [0, -1])
    def test_non_positive_claim_version_rejected(self, claim_version: int) -> None:
        with pytest.raises(PersonalDecisionExplanationRegistryError, match="versions start at 1"):
            build_explanation_index((_explanation(claim_version=claim_version),))

    def test_non_integer_claim_version_rejected(self) -> None:
        with pytest.raises(PersonalDecisionExplanationRegistryError, match="non-integer"):
            build_explanation_index((_explanation(claim_version="2"),))  # type: ignore[arg-type]

    @pytest.mark.parametrize("locator", ["   ", 5])
    def test_malformed_locator_rejected(self, locator: object) -> None:
        with pytest.raises(PersonalDecisionExplanationRegistryError, match="malformed source_locator"):
            build_explanation_index((_explanation(source_locator=locator),))  # type: ignore[arg-type]

    def test_duplicate_explanation_identity_rejected(self) -> None:
        first = _explanation(policy_id="policy.synthetic.one")
        second = _explanation(policy_id="policy.synthetic.two")
        with pytest.raises(PersonalDecisionExplanationRegistryError, match="duplicate explanation identity"):
            build_explanation_index((first, second))

    @pytest.mark.parametrize(
        "second_kwargs",
        [
            {"explanation_id": "explanation.synthetic.b"},
            {"explanation_id": "explanation.synthetic.b", "reason_key": "for_you.reason.other"},
            {"explanation_id": "explanation.synthetic.b", "source_key": SOURCE_B},
            {"explanation_id": "explanation.synthetic.b", "explanation_version": "2"},
        ],
    )
    def test_two_explanations_for_one_decision_are_rejected(self, second_kwargs: dict) -> None:
        with pytest.raises(PersonalDecisionExplanationRegistryError, match="both explain"):
            build_explanation_index((_explanation(), _explanation(**second_kwargs)))

    def test_declaration_order_never_resolves_a_conflict(self) -> None:
        first = _explanation(explanation_id="explanation.synthetic.a")
        second = _explanation(explanation_id="explanation.synthetic.b", explanation_version="2")
        for ordering in ((first, second), (second, first)):
            with pytest.raises(PersonalDecisionExplanationRegistryError):
                build_explanation_index(ordering)

    def test_a_broken_registry_surfaces_on_a_decided_policy(self) -> None:
        with pytest.raises(PersonalDecisionExplanationRegistryError):
            present_personal_decision(_decided(), rules=BROKEN_REGISTRY)


# ---------------------------------------------------------------------------
# Result invariants
# ---------------------------------------------------------------------------


def _presentation(
    policy: PersonalDecisionPolicyResult | None = None, **overrides: object
) -> PersonalDecisionPresentation:
    policy = policy if policy is not None else _decided()
    fields: dict = {
        "source_policy": policy,
        "status": PRESENTABLE,
        "reason": PersonalDecisionPresentationReason.REVIEWED_EXPLANATION_AVAILABLE,
        "action": policy.action,
        "verdict_key": "for_you.verdict.buy",
        "reason_key": REASON_KEY,
        "explanation_id": EXPLANATION_A,
        "explanation_version": "1",
        "citation": PersonalDecisionSourceCitation(
            source_key=SOURCE_A,
            title="t",
            publisher="p",
            canonical_url="https://example.invalid/x",
            locator=None,
            publication_date=None,
            version_or_revision=None,
            jurisdiction=None,
        ),
        "handoff_reason": None,
        "handoff_message": None,
    }
    fields.update(overrides)
    return PersonalDecisionPresentation(**fields)


class TestResultInvariants:
    @pytest.mark.parametrize(
        "missing", ["action", "verdict_key", "explanation_id", "explanation_version", "citation"]
    )
    def test_presentable_requires_the_whole_package(self, missing: str) -> None:
        with pytest.raises(ValueError, match="DECISION_PRESENTABLE requires"):
            _presentation(**{missing: None})

    def test_presentable_cannot_change_the_action(self) -> None:
        with pytest.raises(ValueError, match="must be the action Step 8E decided"):
            _presentation(action=PersonalDecisionAction.SKIP)

    @pytest.mark.parametrize(
        ("status", "policy_factory", "reason", "reason_key"),
        [
            (
                NO_EXPLANATION,
                _decided,
                PersonalDecisionPresentationReason.NO_EXACT_EXPLANATION_RULE,
                "for_you.not_enough.explanation",
            ),
            (
                NO_INFO,
                lambda: _no_information_policy(context_status="partial_context"),
                PersonalDecisionPresentationReason.PERSONAL_CONTEXT_NOT_COMPLETE,
                "for_you.not_enough.personal_context",
            ),
            (
                NO_POLICY,
                _undecided_policy,
                PersonalDecisionPresentationReason.NO_EXACT_DECISION_POLICY,
                "for_you.not_enough.decision_policy",
            ),
            (
                HANDOFF,
                _handoff_policy,
                PersonalDecisionPresentationReason.PROFESSIONAL_HANDOFF_REQUIRED,
                "for_you.handoff.required",
            ),
        ],
    )
    def test_a_blocked_status_cannot_carry_a_decision(
        self,
        status: PersonalDecisionPresentationStatus,
        policy_factory: object,
        reason: PersonalDecisionPresentationReason,
        reason_key: str,
    ) -> None:
        """Each blocked state, built over the upstream state that produces it."""
        with pytest.raises(ValueError, match="no action, explanation or citation"):
            _presentation(
                policy_factory(),  # type: ignore[operator]
                status=status,
                reason=reason,
                reason_key=reason_key,
                handoff_reason="pregnancy" if status is HANDOFF else None,
                handoff_message="see a professional" if status is HANDOFF else None,
            )

    def test_handoff_requires_its_upstream_text(self) -> None:
        with pytest.raises(ValueError, match="HANDOFF_REQUIRED requires"):
            _presentation(
                _handoff_policy(),
                status=HANDOFF,
                reason=PersonalDecisionPresentationReason.PROFESSIONAL_HANDOFF_REQUIRED,
                reason_key="for_you.handoff.required",
                action=None,
                verdict_key=None,
                explanation_id=None,
                explanation_version=None,
                citation=None,
            )

    def test_a_non_handoff_cannot_carry_handoff_text(self) -> None:
        with pytest.raises(ValueError, match="no handoff fields"):
            _presentation(handoff_reason="pregnancy", handoff_message="see a professional")

    def test_every_presentation_carries_a_reason_key(self) -> None:
        with pytest.raises(ValueError, match="reason key"):
            _presentation(reason_key="  ")

    def test_public_models_are_frozen_and_slotted(self) -> None:
        for model in (
            PersonalDecisionPresentation,
            PersonalDecisionSourceCitation,
            PersonalDecisionExplanationRule,
        ):
            assert model.__dataclass_params__.frozen, model
            assert getattr(model, "__slots__", None) is not None, model


class TestSourcePolicyPreservation:
    def test_the_policy_result_is_the_same_object(self) -> None:
        policy = _decided()
        assert present_personal_decision(policy, rules=(_explanation(),)).source_policy is policy

    def test_the_whole_chain_is_preserved_by_identity(self) -> None:
        applicability = _applicability()
        policy = _policy_result(applicability=applicability)
        result = present_personal_decision(policy, rules=(_explanation(),))
        chain = result.source_policy.source_aggregation
        assert chain is policy.source_aggregation
        assert chain.source_semantics is policy.source_aggregation.source_semantics
        assert chain.source_semantics.source_personal_applicability is applicability

    def test_the_policy_is_preserved_on_every_blocked_path(self) -> None:
        handoff = PersonalLensHandoff(reason="pregnancy", message="see a professional")
        for policy in (
            _policy_result(applicability=_applicability(handoff=handoff)),
            _policy_result(applicability=_applicability(context_status="partial_context")),
            _policy_result(policy_rules=()),
            _decided(),
        ):
            assert present_personal_decision(policy).source_policy is policy


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

#: The one function allowed to read a source's display metadata -- after the
#: source has already been chosen by reviewed identity.
_CITATION_BUILDER = "_citation"


class TestStaticGuards:
    def test_module_set_is_exact(self) -> None:
        assert {path.name for path in SERVICE_PATH.glob("*.py")} == {
            "__init__.py",
            "enums.py",
            "rules.py",
            "service.py",
        }

    def test_step8e_is_the_only_upstream_application_dependency(self) -> None:
        seen: set[str] = set()
        for _name, tree in _production_sources():
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                seen.update(
                    module.split(".")[2] for module in modules if module.startswith("app.domains.")
                )
        assert seen == {"personal_decision_policy", "personal_decision_explanation"}

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

    def test_no_async_functions(self) -> None:
        offenders = [
            f"{name}: {node.name}"
            for name, tree in _production_sources()
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
        ]
        assert offenders == [], offenders

    def test_claim_prose_and_strength_are_never_read(self) -> None:
        forbidden = {
            "summary",
            "scope",
            "evidence_strength",
            "evidence_tier",
            "matched_facts",
            "raw_name",
            "normalized_name",
            "candidate_substance_keys",
        }
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in forbidden:
                    offenders.append(f"{name}:{node.lineno} reads .{node.attr}")
        assert offenders == [], offenders

    def test_source_display_metadata_is_read_only_when_building_the_citation(self) -> None:
        """Title, publisher and date may be copied -- never consulted.

        Reading them anywhere but the citation builder would mean a source's
        own description could influence which source is cited.
        """
        display = {"title", "publisher", "publication_date"}
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef) or node.name == _CITATION_BUILDER:
                    continue
                offenders.extend(
                    f"{name}:{inner.lineno}: {node.name} reads .{inner.attr}"
                    for inner in ast.walk(node)
                    if isinstance(inner, ast.Attribute) and inner.attr in display
                )
        assert offenders == [], offenders

    def test_public_entry_point_takes_only_the_step8e_result(self) -> None:
        signature = inspect.signature(present_personal_decision)
        assert list(signature.parameters) == ["policy", "rules"]
        positional, seam = signature.parameters.values()
        assert positional.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert positional.default is inspect.Parameter.empty
        assert seam.kind is inspect.Parameter.KEYWORD_ONLY
        assert seam.default == ()
        assert not inspect.iscoroutinefunction(present_personal_decision)

    def test_no_action_member_is_ever_named_in_production(self) -> None:
        """An action may only be copied, never chosen.

        Naming a member is what a ``{SUPPORTING: BUY}`` table or an ``if``
        chain would have to do. The verdict-key mapping is deliberately keyed
        by the action's string value instead of the member, so this guard
        needs no exception for it.
        """
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                if isinstance(node.value, ast.Name) and node.value.id == "PersonalDecisionAction":
                    offenders.append(f"{name}:{node.lineno}: PersonalDecisionAction.{node.attr}")
        assert offenders == [], offenders

    def test_an_action_is_never_constructed_in_production(self) -> None:
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "PersonalDecisionAction"
                ):
                    offenders.append(f"{name}:{node.lineno}: PersonalDecisionAction(...)")
        assert offenders == [], offenders

    def test_action_is_only_ever_copied_from_the_policy(self) -> None:
        """Every non-None ``action=`` traces back to the input policy."""
        allowed_names = {"action"}
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
                    if isinstance(value, ast.Name) and value.id in allowed_names:
                        continue
                    offenders.append(f"{name}:{node.lineno}: action={ast.dump(value)[:60]}")
        assert offenders == [], offenders

    def test_no_scoring_or_ranking_vocabulary(self) -> None:
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
            "strongest",
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

    def test_no_ordering_comparison_on_the_selection_path(self) -> None:
        """Choosing needs no ``<`` or ``>``; a tie-break or a score would.

        Scoped to the module that selects an explanation, a claim and a
        source. ``rules.py`` legitimately bounds-checks that a claim version
        is positive, which is validation rather than a comparison between
        candidates.
        """
        offenders: list[str] = []
        for name, tree in _production_sources():
            if name != "service.py":
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                offenders.extend(
                    f"{name}:{node.lineno}: {type(op).__name__}"
                    for op in node.ops
                    if isinstance(op, ast.Lt | ast.LtE | ast.Gt | ast.GtE)
                )
        assert offenders == [], offenders

    def test_no_user_facing_sentence_is_written_in_production(self) -> None:
        """Copy lives behind keys, where a reviewer reads it all at once.

        A literal with spaces and sentence punctuation in an emitted field is
        the shape a hard-coded explanation would take.
        """
        offenders: list[str] = []
        for name, tree in _production_sources():
            for line, text in _executable_tokens(tree):
                if text.isidentifier() or "." not in text:
                    continue
                if " " in text.strip() and text.strip().endswith((".", "!", "?")):
                    offenders.append(f"{name}:{line}: {text!r}")
        assert offenders == [], offenders


def test_public_surface_is_stable() -> None:
    assert set(PersonalDecisionPresentationStatus) == {
        PRESENTABLE,
        NO_INFO,
        NO_POLICY,
        NO_EXPLANATION,
        HANDOFF,
    }
    assert isinstance(present_personal_decision(_decided()), PersonalDecisionPresentation)


# ---------------------------------------------------------------------------
# Exact governed claim identity (hardening correction 1)
# ---------------------------------------------------------------------------


class TestClaimIdentityContinuity:
    """The claim UUID minted by Step 8B must survive to the citation.

    Matching a claim by substance, key and version alone re-identifies the
    evidence by description. Two rows can share a description; only the id
    says it is the same reviewed row. These tests pin that the id travels and
    is checked, so a reason cannot be cited against a look-alike.
    """

    def test_the_governed_claim_id_is_carried_and_verified(self) -> None:
        claim = _claim()
        policy = _policy_result(
            applicability=_applicability(ingredients=(_ingredient(claims=(claim,)),))
        )
        # Step 8D really did record this exact id.
        assert policy.source_aggregation.rules[0].occurrences[0].claim_id == claim.claim_id

        result = present_personal_decision(policy, rules=(_explanation(),))
        assert result.status is PRESENTABLE

    def test_a_mutated_claim_id_fails_closed(self) -> None:
        """The chain disagreeing with itself must be loud, not quietly absent.

        Everything descriptive still matches -- substance, claim key, version,
        semantic rule, policy and source are untouched. Only the identity
        Step 8D recorded has drifted. Before the hardening this still
        presented, citing a row the governed chain never approved.
        """
        claim = _claim()
        policy = _policy_result(
            applicability=_applicability(ingredients=(_ingredient(claims=(claim,)),))
        )
        recorded = policy.source_aggregation.rules[0].occurrences[0].claim_id
        assert recorded == claim.claim_id

        _corrupt(claim, claim_id=uuid.uuid4())
        assert claim.claim_id != recorded

        with pytest.raises(
            PersonalDecisionPresentationInvariantError, match="no longer carries the claim identity"
        ):
            present_personal_decision(policy, rules=(_explanation(),))

    def test_the_descriptive_anchor_is_still_required(self) -> None:
        """Identity does not replace the description; both must agree."""
        claim = _claim()
        policy = _policy_result(
            applicability=_applicability(ingredients=(_ingredient(claims=(claim,)),))
        )
        _corrupt(claim, claim_key="claim.synthetic.renamed")
        result = present_personal_decision(policy, rules=(_explanation(),))
        assert result.status is NO_EXPLANATION
        assert result.action is None

    def test_one_claim_repeated_across_positions_is_one_chain(self) -> None:
        claim = _claim()
        applicability = _applicability(
            ingredients=tuple(
                _ingredient(position=position, claims=(claim,)) for position in (0, 3, 8)
            )
        )
        policy = _policy_result(applicability=applicability)
        occurrences = policy.source_aggregation.rules[0].occurrences
        assert [o.ingredient_position for o in occurrences] == [0, 3, 8]
        assert {o.claim_id for o in occurrences} == {claim.claim_id}

        result = present_personal_decision(policy, rules=(_explanation(),))
        assert result.status is PRESENTABLE
        assert result.citation is not None
        assert result.citation.source_key == SOURCE_A

    def test_occurrences_naming_two_claim_ids_fail_closed(self) -> None:
        """Never pick one of them -- not the first, newest or most common."""
        policy = _decided()
        rule = policy.source_aggregation.rules[0]
        original = rule.occurrences[0]
        second = PersonalDecisionSignalOccurrence(
            ingredient_position=3,
            substance_key=original.substance_key,
            claim_id=uuid.uuid4(),
            claim_key=original.claim_key,
            claim_version=original.claim_version,
        )
        _corrupt(rule, occurrences=(original, second))

        with pytest.raises(
            PersonalDecisionPresentationInvariantError,
            match="occurrences of several distinct reviewed claims",
        ):
            present_personal_decision(policy, rules=(_explanation(),))

    def test_a_rule_with_no_occurrence_fails_closed(self) -> None:
        policy = _decided()
        _corrupt(policy.source_aggregation.rules[0], occurrences=())
        with pytest.raises(
            PersonalDecisionPresentationInvariantError, match="records no claim occurrence"
        ):
            present_personal_decision(policy, rules=(_explanation(),))


# ---------------------------------------------------------------------------
# Handoff provenance (hardening correction 3)
# ---------------------------------------------------------------------------


class TestHandoffProvenance:
    """A handoff with no text is not a handoff we can present.

    Emitting empty strings would hand the screen a blank where the product's
    most important sentence belongs -- and it would look like success.
    """

    def test_a_handoff_status_with_no_handoff_object_fails_closed(self) -> None:
        policy = _policy_result(applicability=_applicability(context_status="handoff_required"))
        assert policy.status is PersonalDecisionPolicyStatus.HANDOFF_REQUIRED
        assert policy.source_aggregation.source_semantics.handoff is None

        with pytest.raises(
            PersonalDecisionPresentationInvariantError, match="carries no handoff object"
        ):
            present_personal_decision(policy)

    @pytest.mark.parametrize("reason", ["", "   "])
    def test_a_blank_handoff_reason_fails_closed(self, reason: str) -> None:
        policy = _handoff_policy(reason=reason, message="Please speak to a professional.")
        with pytest.raises(PersonalDecisionPresentationInvariantError, match="no reason"):
            present_personal_decision(policy)

    @pytest.mark.parametrize("message", ["", "   "])
    def test_a_blank_handoff_message_fails_closed(self, message: str) -> None:
        policy = _handoff_policy(reason="pregnancy", message=message)
        with pytest.raises(PersonalDecisionPresentationInvariantError, match="no message"):
            present_personal_decision(policy)

    def test_a_non_string_handoff_field_fails_closed(self) -> None:
        policy = _handoff_policy()
        _corrupt(policy.source_aggregation.source_semantics.handoff, reason=42)
        with pytest.raises(PersonalDecisionPresentationInvariantError, match="no reason"):
            present_personal_decision(policy)

    def test_a_malformed_handoff_is_not_downgraded(self) -> None:
        """It must not quietly become an ordinary information gap."""
        policy = _handoff_policy(reason="pregnancy", message="  ")
        with pytest.raises(PersonalDecisionPresentationInvariantError):
            present_personal_decision(policy)

    def test_valid_handoff_text_passes_through_byte_for_byte(self) -> None:
        """Validation may strip to test for blankness; output must not be trimmed."""
        reason = " pregnancy_or_breastfeeding "
        message = "  Please speak to a qualified professional about this product.  "
        policy = _handoff_policy(reason=reason, message=message)

        result = present_personal_decision(policy)
        assert result.status is HANDOFF
        assert result.handoff_reason == reason
        assert result.handoff_message == message
        assert result.action is None

    def test_valid_handoff_still_survives_a_broken_registry(self) -> None:
        result = present_personal_decision(_handoff_policy(), rules=BROKEN_REGISTRY)
        assert result.status is HANDOFF
        assert result.handoff_reason == "pregnancy"


# ---------------------------------------------------------------------------
# Status / reason / key coherence (hardening correction 2)
# ---------------------------------------------------------------------------


class TestPresentationCoherence:
    """A presentation must describe the governed state it actually came from.

    Without this, a hand-built result could relabel one upstream state as
    another -- most dangerously, show a verdict over a policy that never
    decided anything.
    """

    def test_presentable_cannot_carry_a_blocking_reason(self) -> None:
        with pytest.raises(ValueError, match="REVIEWED_EXPLANATION_AVAILABLE"):
            _presentation(reason=PersonalDecisionPresentationReason.NO_EXACT_DECISION_POLICY)

    def test_presentable_cannot_be_built_over_an_undecided_policy(self) -> None:
        with pytest.raises(ValueError, match="cannot represent an upstream"):
            _presentation(_undecided_policy())

    def test_presentable_cannot_be_built_over_a_handoff(self) -> None:
        with pytest.raises(ValueError, match="cannot represent an upstream"):
            _presentation(_handoff_policy())

    def test_no_policy_cannot_carry_a_formula_reason(self) -> None:
        with pytest.raises(ValueError, match="requires one of"):
            _presentation(
                _undecided_policy(),
                status=NO_POLICY,
                reason=PersonalDecisionPresentationReason.FORMULA_NOT_PROJECTABLE,
                reason_key="for_you.not_enough.decision_policy",
                action=None,
                verdict_key=None,
                explanation_id=None,
                explanation_version=None,
                citation=None,
            )

    def test_not_enough_information_cannot_carry_a_policy_reason(self) -> None:
        with pytest.raises(ValueError, match="requires"):
            _presentation(
                _no_information_policy(context_status="partial_context"),
                status=NO_INFO,
                reason=PersonalDecisionPresentationReason.NO_EXACT_DECISION_POLICY,
                reason_key="for_you.not_enough.personal_context",
                action=None,
                verdict_key=None,
                explanation_id=None,
                explanation_version=None,
                citation=None,
            )

    def test_not_enough_information_reason_must_match_the_upstream_block(self) -> None:
        """A formula failure may not be relabelled as a context failure."""
        with pytest.raises(ValueError, match="requires"):
            _presentation(
                _no_information_policy(formula_status="malformed"),
                status=NO_INFO,
                reason=PersonalDecisionPresentationReason.PERSONAL_CONTEXT_NOT_COMPLETE,
                reason_key="for_you.not_enough.personal_context",
                action=None,
                verdict_key=None,
                explanation_id=None,
                explanation_version=None,
                citation=None,
            )

    def test_no_explanation_cannot_be_built_over_an_information_gap(self) -> None:
        with pytest.raises(ValueError, match="cannot represent an upstream"):
            _presentation(
                _no_information_policy(context_status="partial_context"),
                status=NO_EXPLANATION,
                reason=PersonalDecisionPresentationReason.NO_EXACT_EXPLANATION_RULE,
                reason_key="for_you.not_enough.explanation",
                action=None,
                verdict_key=None,
                explanation_id=None,
                explanation_version=None,
                citation=None,
            )

    def test_no_explanation_cannot_carry_a_context_reason(self) -> None:
        with pytest.raises(ValueError, match="requires one of"):
            _presentation(
                status=NO_EXPLANATION,
                reason=PersonalDecisionPresentationReason.PERSONAL_CONTEXT_NOT_COMPLETE,
                reason_key="for_you.not_enough.explanation",
                action=None,
                verdict_key=None,
                explanation_id=None,
                explanation_version=None,
                citation=None,
            )

    def test_handoff_cannot_carry_the_wrong_reason(self) -> None:
        with pytest.raises(ValueError, match="requires one of"):
            _presentation(
                _handoff_policy(),
                status=HANDOFF,
                reason=PersonalDecisionPresentationReason.PERSONAL_CONTEXT_NOT_COMPLETE,
                reason_key="for_you.handoff.required",
                action=None,
                verdict_key=None,
                explanation_id=None,
                explanation_version=None,
                citation=None,
                handoff_reason="pregnancy",
                handoff_message="see a professional",
            )

    def test_handoff_cannot_carry_the_wrong_reason_key(self) -> None:
        with pytest.raises(ValueError, match="requires reason key"):
            _presentation(
                _handoff_policy(),
                status=HANDOFF,
                reason=PersonalDecisionPresentationReason.PROFESSIONAL_HANDOFF_REQUIRED,
                reason_key="for_you.not_enough.explanation",
                action=None,
                verdict_key=None,
                explanation_id=None,
                explanation_version=None,
                citation=None,
                handoff_reason="pregnancy",
                handoff_message="see a professional",
            )

    @pytest.mark.parametrize(
        ("status", "policy_factory"),
        [
            (NO_INFO, _undecided_policy),
            (NO_POLICY, lambda: _no_information_policy(context_status="partial_context")),
            (HANDOFF, _undecided_policy),
        ],
    )
    def test_one_upstream_state_cannot_be_relabelled_as_another(
        self, status: PersonalDecisionPresentationStatus, policy_factory: object
    ) -> None:
        with pytest.raises(ValueError, match="cannot represent an upstream"):
            _presentation(
                policy_factory(),  # type: ignore[operator]
                status=status,
                reason=PersonalDecisionPresentationReason.NO_EXACT_DECISION_POLICY,
                reason_key="for_you.not_enough.decision_policy",
                action=None,
                verdict_key=None,
                explanation_id=None,
                explanation_version=None,
                citation=None,
            )

    def test_every_real_path_satisfies_its_own_invariants(self) -> None:
        """The service's own outputs must pass the tightened contract."""
        for policy, rules in (
            (_handoff_policy(), ()),
            (_no_information_policy(context_status="partial_context"), ()),
            (_no_information_policy(formula_status="malformed"), ()),
            (_undecided_policy(), ()),
            (_decided(), ()),
            (_decided(), (_explanation(),)),
        ):
            assert isinstance(
                present_personal_decision(policy, rules=rules), PersonalDecisionPresentation
            )


def test_the_verdict_map_is_unchanged_and_names_no_action_member() -> None:
    """The reviewed presentation-only mapping stays keyed by value."""
    from app.domains.personal_decision_explanation import service as step8f

    assert {
        "buy": "for_you.verdict.buy",
        "wait": "for_you.verdict.wait",
        "skip": "for_you.verdict.skip",
    } == step8f._VERDICT_KEYS
