"""Fix 13: every rule id the engine can emit is documented in
docs/stabilisation/INGREDIENT_COVERAGE.md.

The test parses the coverage doc as plain text and asserts each
reviewed rule id appears somewhere in it. That is deliberately loose
matching — the doc has flexibility in how it renders each entry — but
it is tight enough to catch a rule that was added to code without an
evidence row.
"""
from __future__ import annotations

from pathlib import Path

import pytest


COVERAGE_PATH = Path(__file__).resolve().parents[2] / "docs" / "stabilisation" / "INGREDIENT_COVERAGE.md"


@pytest.fixture(scope="module")
def coverage_text() -> str:
    assert COVERAGE_PATH.is_file(), f"missing {COVERAGE_PATH}"
    return COVERAGE_PATH.read_text(encoding="utf-8")


def test_engine_rules_are_documented(coverage_text):
    from app.domains.routines.rules import ENGINE_RULES

    missing = [rid for rid in ENGINE_RULES if rid not in coverage_text]
    assert not missing, (
        f"engine rules missing from INGREDIENT_COVERAGE.md: {missing}"
    )


def test_compatibility_rules_are_documented(coverage_text):
    from app.domains.routines.ontology import COMPATIBILITY_RULES

    missing = [rule.rule_id for rule in COMPATIBILITY_RULES if rule.rule_id not in coverage_text]
    assert not missing, (
        f"compatibility rules missing from INGREDIENT_COVERAGE.md: {missing}"
    )


def test_climate_rules_are_documented(coverage_text):
    from app.domains.routines.ontology import CLIMATE_RULES

    missing = [rule.rule_id for rule in CLIMATE_RULES if rule.rule_id not in coverage_text]
    assert not missing, (
        f"climate rules missing from INGREDIENT_COVERAGE.md: {missing}"
    )


def test_all_rule_ids_are_documented(coverage_text):
    """Master check: every id `all_rule_ids()` can emit is in the doc."""
    from app.domains.routines.rules import all_rule_ids

    missing = [rid for rid in all_rule_ids() if rid not in coverage_text]
    assert not missing, (
        f"rule ids emitted by the engine but not in INGREDIENT_COVERAGE.md: {missing}"
    )


def test_coverage_doc_labels_uncovered_ingredients_correctly(coverage_text):
    """Fix 13 acceptance: uncovered ingredients are labelled 'not covered',
    not 'safe'. The doc must contain the phrase to signal the policy is
    articulated, and must not contain 'safe by default' as a claim about
    the uncovered set."""
    assert "not covered" in coverage_text.lower(), (
        "INGREDIENT_COVERAGE.md must articulate that uncovered ingredients "
        "are labelled 'not covered'."
    )
    # A cheeky regression: the doc must not claim uncovered ingredients are
    # safe as a fallback policy.
    lowered = coverage_text.lower()
    forbidden_phrases = ("safe by default", "assumed safe", "presumed safe")
    for phrase in forbidden_phrases:
        assert phrase not in lowered, (
            f"INGREDIENT_COVERAGE.md contains {phrase!r} — uncovered "
            "ingredients must be labelled 'not covered', not 'safe'."
        )


def test_no_llm_authored_rule_declaration(coverage_text):
    """Fix 13 acceptance: rules are not authored by an LLM. The doc must
    explicitly state this, and must not contain telltale prompt-boilerplate
    strings that a copy-pasted model completion tends to leave behind."""
    assert "No rule in this document was authored by an LLM" in coverage_text, (
        "INGREDIENT_COVERAGE.md must explicitly assert that no rule was authored by an LLM."
    )
    lowered = coverage_text.lower()
    llm_boilerplate = (
        "as an ai language model",
        "i am unable to browse",
        "i cannot access the internet",
        "here is the information you requested",
    )
    hits = [phrase for phrase in llm_boilerplate if phrase in lowered]
    assert not hits, f"INGREDIENT_COVERAGE.md contains LLM-boilerplate phrases: {hits}"
