"""Step 8C — governed personal decision semantics.

Every fixture here is synthetic. Production carries no rules, so these tests
inject their own; that is the only way to exercise a layer whose whole point is
to stay empty until a mapping has been reviewed.

The adversarial tests matter more than the happy path. A future contributor
looking at a claim that says "amazing, perfect, highly beneficial" will be
tempted to read the direction out of the words. The tests below pin a rule that
says the opposite and assert the rule wins, so that temptation fails loudly.
"""

from __future__ import annotations

import ast
import re
import uuid
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
from app.domains.personal_decision_semantics import (
    PERSONAL_DECISION_SEMANTIC_RULES,
    ClaimDecisionSemanticProjection,
    IngredientDecisionSemantics,
    LabelSnapshotPersonalDecisionSemantics,
    PersonalDecisionSemanticRegistryError,
    PersonalDecisionSemanticRule,
    PersonalDecisionSemanticStatus,
    PersonalDecisionSignal,
    build_rule_index,
    project_personal_decision_semantics,
)
from app.domains.substance_interpretation import ProjectedIdentityStatus

SERVICE_PATH = Path(__file__).resolve().parents[1] / "app" / "domains" / "personal_decision_semantics"

SUBSTANCE_A = "substance.synthetic.a"
SUBSTANCE_B = "substance.synthetic.b"
CLAIM_A = "claim.synthetic.a"
CLAIM_B = "claim.synthetic.b"


def _claim(
    *,
    claim_key: str = CLAIM_A,
    claim_version: int = 2,
    summary: str = "synthetic summary",
    scope: str = "synthetic scope",
    evidence_strength: str = "moderate",
    matched_facts: tuple[MatchedPersonalFact, ...] = (),
) -> ApplicableSubstancePersonalClaim:
    return ApplicableSubstancePersonalClaim(
        claim_id=uuid.uuid4(),
        claim_key=claim_key,
        claim_version=claim_version,
        summary=summary,
        scope=scope,
        evidence_strength=evidence_strength,
        evidence_tier="clinically_studied",
        matched_facts=matched_facts,
        sources=(),
    )


def _ingredient(
    *,
    position: int = 0,
    raw_name: str = "Synthetic A",
    substance_key: str | None = SUBSTANCE_A,
    identity_status: ProjectedIdentityStatus = ProjectedIdentityStatus.RESOLVED,
    status: PersonalApplicabilityStatus = PersonalApplicabilityStatus.PERSONAL_EVIDENCE_AVAILABLE,
    candidates: tuple[str, ...] = (),
    claims: tuple[ApplicableSubstancePersonalClaim, ...] = (),
) -> IngredientPersonalApplicability:
    return IngredientPersonalApplicability(
        position=position,
        raw_name=raw_name,
        normalized_name=raw_name.lower(),
        identity_status=identity_status,
        substance_key=substance_key,
        entity_kind="substance" if substance_key else None,
        candidate_substance_keys=candidates,
        personal_applicability_status=status,
        claims=claims,
    )


def _result(
    *,
    category: PersonalApplicabilityCategory = PersonalApplicabilityCategory.SKIN_CARE,
    ingredients: tuple[IngredientPersonalApplicability, ...] = (),
    context_status: object = "context_available",
    handoff: object | None = None,
) -> LabelSnapshotPersonalApplicability:
    return LabelSnapshotPersonalApplicability(
        provenance=None,
        category=category,
        formula_status="resolved",
        profile_id=uuid.uuid4(),
        profile_version=3,
        context_status=context_status,
        ingredients=ingredients,
        handoff=handoff,
    )


def _rule(
    *,
    rule_id: str = "rule.synthetic.support",
    rule_version: str = "1",
    category: PersonalApplicabilityCategory = PersonalApplicabilityCategory.SKIN_CARE,
    substance_key: str = SUBSTANCE_A,
    claim_key: str = CLAIM_A,
    claim_version: int = 2,
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


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_production_registry_is_empty(self) -> None:
        """No mapping ships until its evidence-to-policy review exists."""
        assert PERSONAL_DECISION_SEMANTIC_RULES == ()

    def test_valid_synthetic_registry_indexes(self) -> None:
        index = build_rule_index((_rule(),))
        assert list(index) == [("skin_care", SUBSTANCE_A, CLAIM_A, 2)]

    @pytest.mark.parametrize(
        ("kwargs", "fragment"),
        [
            ({"rule_id": ""}, "blank rule_id"),
            ({"rule_id": "   "}, "blank rule_id"),
            ({"rule_version": ""}, "blank rule_version"),
            ({"substance_key": ""}, "blank substance_key"),
            ({"claim_key": ""}, "blank claim_key"),
            ({"claim_version": 0}, "versions start at 1"),
            ({"claim_version": -1}, "versions start at 1"),
        ],
    )
    def test_malformed_rules_are_rejected(self, kwargs: dict, fragment: str) -> None:
        with pytest.raises(PersonalDecisionSemanticRegistryError, match=fragment):
            build_rule_index((_rule(**kwargs),))

    def test_invalid_category_rejected(self) -> None:
        bad = _rule()
        object.__setattr__(bad, "category", "skin_care")  # a bare string, not the enum
        with pytest.raises(PersonalDecisionSemanticRegistryError, match="invalid category"):
            build_rule_index((bad,))

    def test_invalid_signal_rejected(self) -> None:
        bad = _rule()
        object.__setattr__(bad, "signal", "supporting")
        with pytest.raises(PersonalDecisionSemanticRegistryError, match="invalid signal"):
            build_rule_index((bad,))

    def test_duplicate_rule_identity_rejected(self) -> None:
        first = _rule()
        second = _rule(claim_key=CLAIM_B)
        with pytest.raises(PersonalDecisionSemanticRegistryError, match="duplicate rule identity"):
            build_rule_index((first, second))

    def test_duplicate_target_rejected(self) -> None:
        first = _rule(rule_id="rule.synthetic.one")
        second = _rule(rule_id="rule.synthetic.two")
        with pytest.raises(PersonalDecisionSemanticRegistryError, match="at most one reviewed mapping"):
            build_rule_index((first, second))

    def test_conflicting_duplicate_target_fails_closed(self) -> None:
        """Two reviewers disagreeing is not resolved by a tie-break here."""
        support = _rule(rule_id="rule.synthetic.support", signal=PersonalDecisionSignal.SUPPORTING)
        caution = _rule(rule_id="rule.synthetic.caution", signal=PersonalDecisionSignal.CAUTIONARY)
        with pytest.raises(PersonalDecisionSemanticRegistryError, match="at most one reviewed mapping"):
            build_rule_index((support, caution))


# ---------------------------------------------------------------------------
# Exact matching
# ---------------------------------------------------------------------------


class TestExactMatching:
    def test_exact_match_yields_semantics(self) -> None:
        result = project_personal_decision_semantics(
            _result(ingredients=(_ingredient(claims=(_claim(),)),)),
            rules=(_rule(),),
        )
        projection = result.ingredients[0].claims[0]
        assert projection.status is PersonalDecisionSemanticStatus.SEMANTICS_AVAILABLE
        assert projection.rule_id == "rule.synthetic.support"
        assert projection.rule_version == "1"
        assert projection.signal is PersonalDecisionSignal.SUPPORTING

    def test_empty_production_registry_yields_no_semantics(self) -> None:
        result = project_personal_decision_semantics(
            _result(ingredients=(_ingredient(claims=(_claim(),)),))
        )
        projection = result.ingredients[0].claims[0]
        assert projection.status is PersonalDecisionSemanticStatus.NOT_ENOUGH_DECISION_SEMANTICS
        assert projection.rule_id is None
        assert projection.rule_version is None
        assert projection.signal is None

    def test_wrong_category_does_not_match(self) -> None:
        result = project_personal_decision_semantics(
            _result(
                category=PersonalApplicabilityCategory.COSMETICS,
                ingredients=(_ingredient(claims=(_claim(),)),),
            ),
            rules=(_rule(category=PersonalApplicabilityCategory.SKIN_CARE),),
        )
        assert (
            result.ingredients[0].claims[0].status
            is PersonalDecisionSemanticStatus.NOT_ENOUGH_DECISION_SEMANTICS
        )

    def test_wrong_substance_does_not_match(self) -> None:
        result = project_personal_decision_semantics(
            _result(ingredients=(_ingredient(substance_key=SUBSTANCE_B, claims=(_claim(),)),)),
            rules=(_rule(substance_key=SUBSTANCE_A),),
        )
        assert (
            result.ingredients[0].claims[0].status
            is PersonalDecisionSemanticStatus.NOT_ENOUGH_DECISION_SEMANTICS
        )

    def test_wrong_claim_key_does_not_match(self) -> None:
        result = project_personal_decision_semantics(
            _result(ingredients=(_ingredient(claims=(_claim(claim_key=CLAIM_B),)),)),
            rules=(_rule(claim_key=CLAIM_A),),
        )
        assert (
            result.ingredients[0].claims[0].status
            is PersonalDecisionSemanticStatus.NOT_ENOUGH_DECISION_SEMANTICS
        )

    def test_version_1_rule_does_not_apply_to_version_2_claim(self) -> None:
        """A revised claim needs a fresh review, not the old review's answer."""
        result = project_personal_decision_semantics(
            _result(ingredients=(_ingredient(claims=(_claim(claim_version=2),)),)),
            rules=(_rule(claim_version=1),),
        )
        assert (
            result.ingredients[0].claims[0].status
            is PersonalDecisionSemanticStatus.NOT_ENOUGH_DECISION_SEMANTICS
        )


# ---------------------------------------------------------------------------
# The direction comes from the rule and nothing else
# ---------------------------------------------------------------------------


class TestSignalProvenance:
    def test_flattering_prose_does_not_override_a_cautionary_rule(self) -> None:
        result = project_personal_decision_semantics(
            _result(
                ingredients=(
                    _ingredient(claims=(_claim(summary="amazing perfect highly beneficial"),)),
                )
            ),
            rules=(_rule(signal=PersonalDecisionSignal.CAUTIONARY),),
        )
        assert result.ingredients[0].claims[0].signal is PersonalDecisionSignal.CAUTIONARY

    def test_alarming_prose_does_not_override_a_supporting_rule(self) -> None:
        result = project_personal_decision_semantics(
            _result(
                ingredients=(
                    _ingredient(
                        claims=(_claim(summary="avoid irritation adverse concern", scope="avoid"),)
                    ),
                )
            ),
            rules=(_rule(signal=PersonalDecisionSignal.SUPPORTING),),
        )
        assert result.ingredients[0].claims[0].signal is PersonalDecisionSignal.SUPPORTING

    @pytest.mark.parametrize("strength", ["strong", "moderate", "limited"])
    def test_evidence_strength_does_not_change_the_signal(self, strength: str) -> None:
        result = project_personal_decision_semantics(
            _result(ingredients=(_ingredient(claims=(_claim(evidence_strength=strength),)),)),
            rules=(_rule(signal=PersonalDecisionSignal.CAUTIONARY),),
        )
        assert result.ingredients[0].claims[0].signal is PersonalDecisionSignal.CAUTIONARY

    def test_matched_facts_do_not_change_the_signal(self) -> None:
        """Step 8B already decided applicability; Step 8C must not redo it."""
        facts = (
            MatchedPersonalFact(
                fact_key="care_skin_sensitivity",
                profile_attribute_id=uuid.uuid4(),
                value="often_reactive",
            ),
        )
        with_facts = project_personal_decision_semantics(
            _result(ingredients=(_ingredient(claims=(_claim(matched_facts=facts),)),)),
            rules=(_rule(),),
        )
        without_facts = project_personal_decision_semantics(
            _result(ingredients=(_ingredient(claims=(_claim(matched_facts=()),)),)),
            rules=(_rule(),),
        )
        assert (
            with_facts.ingredients[0].claims[0].signal
            == without_facts.ingredients[0].claims[0].signal
            == PersonalDecisionSignal.SUPPORTING
        )


# ---------------------------------------------------------------------------
# Identity and upstream states are never reopened
# ---------------------------------------------------------------------------


class TestUpstreamStates:
    def test_unresolved_identity_gets_no_semantics(self) -> None:
        result = project_personal_decision_semantics(
            _result(
                ingredients=(
                    _ingredient(
                        substance_key=None,
                        identity_status=ProjectedIdentityStatus.UNRESOLVED,
                        status=PersonalApplicabilityStatus.IDENTITY_UNRESOLVED,
                        claims=(),
                    ),
                )
            ),
            rules=(_rule(),),
        )
        assert result.ingredients[0].claims == ()

    def test_ambiguous_identity_gets_no_semantics_even_when_candidates_have_rules(self) -> None:
        """Personal semantics must never settle a Step 7A ambiguity."""
        result = project_personal_decision_semantics(
            _result(
                ingredients=(
                    _ingredient(
                        substance_key=None,
                        identity_status=ProjectedIdentityStatus.AMBIGUOUS,
                        status=PersonalApplicabilityStatus.IDENTITY_AMBIGUOUS,
                        candidates=(SUBSTANCE_A, SUBSTANCE_B),
                        claims=(),
                    ),
                )
            ),
            rules=(_rule(substance_key=SUBSTANCE_A), _rule(rule_id="r2", substance_key=SUBSTANCE_B)),
        )
        ingredient = result.ingredients[0]
        assert ingredient.claims == ()
        assert ingredient.candidate_substance_keys == (SUBSTANCE_A, SUBSTANCE_B)

    def test_step7c_reference_role_does_not_become_personal_semantics(self) -> None:
        """NOT_ENOUGH_INFORMATION upstream stays empty, whatever the role was."""
        result = project_personal_decision_semantics(
            _result(
                ingredients=(
                    _ingredient(
                        raw_name="Glycerin",
                        status=PersonalApplicabilityStatus.NOT_ENOUGH_INFORMATION,
                        claims=(),
                    ),
                )
            ),
            rules=(_rule(),),
        )
        assert result.ingredients[0].claims == ()

    def test_handoff_is_preserved_with_zero_ingredients(self) -> None:
        sentinel = object()
        result = project_personal_decision_semantics(
            _result(
                ingredients=(_ingredient(claims=(_claim(),)),),
                context_status="handoff_required",
                handoff=sentinel,
            ),
            rules=(_rule(),),
        )
        assert result.handoff is sentinel
        assert result.ingredients == ()

    def test_missing_personal_context_is_preserved(self) -> None:
        result = project_personal_decision_semantics(
            _result(ingredients=(), context_status="not_enough_personal_context"),
            rules=(_rule(),),
        )
        assert result.ingredients == ()
        assert result.context_status == "not_enough_personal_context"


# ---------------------------------------------------------------------------
# Projection, not aggregation
# ---------------------------------------------------------------------------


class TestProjectionShape:
    def test_opposing_signals_are_returned_independently(self) -> None:
        """No winner, no net, no MIXED. Combining belongs to Step 8D."""
        result = project_personal_decision_semantics(
            _result(
                ingredients=(
                    _ingredient(claims=(_claim(claim_key=CLAIM_A), _claim(claim_key=CLAIM_B))),
                )
            ),
            rules=(
                _rule(rule_id="rule.synthetic.support", claim_key=CLAIM_A),
                _rule(
                    rule_id="rule.synthetic.caution",
                    claim_key=CLAIM_B,
                    signal=PersonalDecisionSignal.CAUTIONARY,
                ),
            ),
        )
        signals = [claim.signal for claim in result.ingredients[0].claims]
        assert signals == [PersonalDecisionSignal.SUPPORTING, PersonalDecisionSignal.CAUTIONARY]

    def test_duplicate_ingredients_are_not_deduplicated(self) -> None:
        result = project_personal_decision_semantics(
            _result(
                ingredients=(
                    _ingredient(position=0, claims=(_claim(),)),
                    _ingredient(position=1, claims=(_claim(),)),
                )
            ),
            rules=(_rule(),),
        )
        assert [i.position for i in result.ingredients] == [0, 1]
        assert all(i.claims[0].signal is PersonalDecisionSignal.SUPPORTING for i in result.ingredients)

    def test_ingredient_and_claim_order_are_preserved(self) -> None:
        result = project_personal_decision_semantics(
            _result(
                ingredients=(
                    _ingredient(position=0, raw_name="First", claims=(_claim(claim_key=CLAIM_B), _claim(claim_key=CLAIM_A))),
                    _ingredient(position=1, raw_name="Second", substance_key=SUBSTANCE_B),
                )
            ),
            rules=(_rule(),),
        )
        assert [i.raw_name for i in result.ingredients] == ["First", "Second"]
        assert [c.claim_key for c in result.ingredients[0].claims] == [CLAIM_B, CLAIM_A]

    def test_results_are_immutable(self) -> None:
        result = project_personal_decision_semantics(
            _result(ingredients=(_ingredient(claims=(_claim(),)),)),
            rules=(_rule(),),
        )
        assert isinstance(result.ingredients, tuple)
        assert isinstance(result.ingredients[0].claims, tuple)
        for frozen in (result, result.ingredients[0], result.ingredients[0].claims[0]):
            with pytest.raises((AttributeError, TypeError)):
                frozen.category = "mutated"  # type: ignore[misc]

    def test_status_and_rule_fields_cannot_disagree(self) -> None:
        with pytest.raises(ValueError, match="requires rule_id"):
            ClaimDecisionSemanticProjection(
                claim_id=uuid.uuid4(),
                claim_key=CLAIM_A,
                claim_version=1,
                status=PersonalDecisionSemanticStatus.SEMANTICS_AVAILABLE,
                rule_id=None,
                rule_version=None,
                signal=None,
            )
        with pytest.raises(ValueError, match="must carry no rule"):
            ClaimDecisionSemanticProjection(
                claim_id=uuid.uuid4(),
                claim_key=CLAIM_A,
                claim_version=1,
                status=PersonalDecisionSemanticStatus.NOT_ENOUGH_DECISION_SEMANTICS,
                rule_id="rule.synthetic.support",
                rule_version="1",
                signal=PersonalDecisionSignal.SUPPORTING,
            )

    def test_upstream_metadata_passes_through_unchanged(self) -> None:
        upstream = _result(ingredients=(_ingredient(claims=(_claim(),)),))
        result = project_personal_decision_semantics(upstream, rules=(_rule(),))
        assert result.category is upstream.category
        assert result.formula_status == upstream.formula_status
        assert result.profile_id == upstream.profile_id
        assert result.profile_version == upstream.profile_version
        assert result.context_status == upstream.context_status
        assert result.provenance is upstream.provenance


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

    This is what a scoring or verdict feature would actually be written in.
    Comments never reach the AST at all, and docstrings are dropped above, so
    prose that forbids a word cannot be mistaken for code that uses it.
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
        "evidence",
        "profile",
        "personal_lens",
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
        assert {p.name for p in SERVICE_PATH.glob("*.py")} == {
            "__init__.py",
            "enums.py",
            "rules.py",
            "service.py",
        }

    def test_no_forbidden_domain_imports(self) -> None:
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.ImportFrom):
                    module = node.module
                elif isinstance(node, ast.Import):
                    module = ",".join(alias.name for alias in node.names)
                if not module:
                    continue
                for part in module.split(","):
                    if not part.startswith("app.domains."):
                        continue
                    domain = part.split(".")[2]
                    if domain in self.FORBIDDEN_DOMAINS:
                        offenders.append(f"{name}: {part}")
        assert offenders == [], offenders

    def test_no_database_or_network_imports(self) -> None:
        banned = ("sqlalchemy", "httpx", "requests", "aiohttp", "asyncpg", "openai", "google")
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                elif isinstance(node, ast.Import):
                    names.extend(alias.name for alias in node.names)
                for candidate in names:
                    if candidate.split(".")[0] in banned:
                        offenders.append(f"{name}: {candidate}")
        assert offenders == [], offenders

    def test_service_never_reads_claim_prose(self) -> None:
        """`.summary` and `.scope` must not be touched at all in production."""
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in {"summary", "scope"}:
                    offenders.append(f"{name}:{node.lineno} reads .{node.attr}")
        assert offenders == [], offenders

    def test_service_never_reads_strength_tier_sources_or_facts(self) -> None:
        forbidden_attrs = {"evidence_strength", "evidence_tier", "sources", "matched_facts"}
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
                    offenders.append(f"{name}:{node.lineno} reads .{node.attr}")
        assert offenders == [], offenders

    def test_no_async_and_no_session_parameters(self) -> None:
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if isinstance(node, ast.AsyncFunctionDef):
                    offenders.append(f"{name}: async def {node.name}")
                if isinstance(node, ast.FunctionDef):
                    for arg in [*node.args.args, *node.args.kwonlyargs]:
                        if arg.arg in {"session", "db", "account_id", "snapshot", "safety"}:
                            offenders.append(f"{name}: {node.name}({arg.arg})")
        assert offenders == [], offenders

    def test_no_scoring_or_verdict_vocabulary(self) -> None:
        """No executable code may name a score, a verdict or a suitability.

        Docstrings and comments may -- and do -- name these words in order to
        forbid them, so only identifiers and live string literals are scanned.
        """
        banned = (
            "good_for_you",
            "bad_for_you",
            "unsafe",
            "suitable",
            "unsuitable",
            "buy",
            "skip",
            "verdict",
            "score",
            "ranking",
        )
        offenders: list[str] = []
        for name, tree in _production_sources():
            for line, text in _executable_tokens(tree):
                lowered = text.lower()
                for word in banned:
                    if word in lowered:
                        offenders.append(f"{name}:{line}: {word} in {text!r}")
        assert offenders == [], offenders

    def test_no_counting_or_aggregation_identifiers(self) -> None:
        """Combining signals is Step 8D's reviewed job, and it does not exist.

        Whole names are rejected, and so is any identifier built from a
        counting or weighting word, because ``supporting_total`` is the same
        unreviewed policy as ``supporting_count``.
        """
        banned_whole = {"supporting_count", "cautionary_count", "net_signal", "dominant_signal"}
        banned_words = {
            "aggregate",
            "aggregated",
            "aggregation",
            "average",
            "count",
            "counts",
            "dominant",
            "majority",
            "mean",
            "net",
            "outweigh",
            "rank",
            "ranked",
            "ranking",
            "score",
            "scores",
            "scoring",
            "subtotal",
            "sum",
            "sums",
            "tally",
            "total",
            "totals",
            "weight",
            "weighted",
            "weights",
        }
        offenders: list[str] = []
        for name, tree in _production_sources():
            for line, text in _executable_tokens(tree):
                if not text.isidentifier():
                    continue
                if text in banned_whole:
                    offenders.append(f"{name}:{line}: {text}")
                    continue
                for word in _WORD.findall(text):
                    if word.lower() in banned_words:
                        offenders.append(f"{name}:{line}: {word} in {text}")
        assert offenders == [], offenders


def test_public_surface_is_stable() -> None:
    assert isinstance(project_personal_decision_semantics(_result()), LabelSnapshotPersonalDecisionSemantics)
    assert IngredientDecisionSemantics.__dataclass_params__.frozen
    assert ClaimDecisionSemanticProjection.__dataclass_params__.frozen
    assert LabelSnapshotPersonalDecisionSemantics.__dataclass_params__.frozen


# ---------------------------------------------------------------------------
# Source continuity (added for Step 8F)
# ---------------------------------------------------------------------------


class TestSourceContinuity:
    """The exact Step 8B result must survive the projection, by identity.

    Step 8C reduces a claim to its identity and reviewed direction, which is
    right for deciding but leaves a later presentation layer with no way back
    to the named openable sources the claim carried. Carrying the whole
    Step 8B object forward closes that gap without copying a single URL into
    this domain -- a copy would be a second evidence record, free to drift
    from the one that was reviewed.
    """

    def test_ordinary_context_preserves_the_exact_input(self) -> None:
        upstream = _result(ingredients=(_ingredient(claims=(_claim(),)),))
        result = project_personal_decision_semantics(upstream)
        assert result.source_personal_applicability is upstream

    def test_handoff_preserves_the_exact_input(self) -> None:
        upstream = _result(handoff=object())
        result = project_personal_decision_semantics(upstream)
        assert result.ingredients == ()
        assert result.source_personal_applicability is upstream

    def test_handoff_by_status_preserves_the_exact_input(self) -> None:
        upstream = _result(context_status="handoff_required")
        result = project_personal_decision_semantics(upstream)
        assert result.source_personal_applicability is upstream

    def test_missing_personal_context_preserves_the_exact_input(self) -> None:
        upstream = _result(context_status="not_enough_personal_context")
        result = project_personal_decision_semantics(upstream)
        assert result.source_personal_applicability is upstream

    def test_no_claim_or_source_object_is_rebuilt(self) -> None:
        claim = _claim()
        ingredient = _ingredient(claims=(claim,))
        upstream = _result(ingredients=(ingredient,))
        result = project_personal_decision_semantics(upstream)

        carried = result.source_personal_applicability
        assert carried is not None
        assert carried.ingredients is upstream.ingredients
        assert carried.ingredients[0] is ingredient
        assert carried.ingredients[0].claims is ingredient.claims
        assert carried.ingredients[0].claims[0] is claim
        assert carried.ingredients[0].claims[0].sources is claim.sources

    def test_the_field_defaults_to_none_for_synthetic_construction(self) -> None:
        """Downstream synthetic fixtures may still omit it."""
        built = LabelSnapshotPersonalDecisionSemantics(
            provenance=None,
            category=PersonalApplicabilityCategory.SKIN_CARE,
            formula_status="parsed",
            profile_id=None,
            profile_version=None,
            context_status="context_available",
            ingredients=(),
            handoff=None,
        )
        assert built.source_personal_applicability is None
