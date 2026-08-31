"""The customer contract for "what lowers it".

A grade nobody can interrogate is a number with a colour on it. Every row that
lowers somebody's verdict has to answer four questions without being tapped:
what thing, how much of it, how bad, and who says so. These tests hold that
contract to the exact shape the screen renders, because the failure mode is
not a crash — it is a screen that says "Flagged" four times and means nothing.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest
from app.domains.nutrition.food_reference import ADDITIVES
from app.domains.nutrition.grading import ProductInput, grade_product
from app.domains.nutrition.grading.presentation import (
    STATUSES,
    present,
)
from app.domains.nutrition.grading.production_rules import (
    GRADING_RULES,
    candidate_ruleset,
    enforce_published_required_rules,
)

# ---------------------------------------------------------------------------
# The fixture: an ordinary Indian glucose biscuit
# ---------------------------------------------------------------------------
BISCUIT = ProductInput(
    name="Glucose Biscuit",
    categories="Biscuits",
    ingredients=(
        "refined wheat flour (maida)",
        "sugar",
        "edible vegetable oil (palm)",
        "invert syrup",
        "raising agent (ins 500(ii))",
        "antioxidant (ins 319)",
        "emulsifier (ins 322)",
        "iodised salt",
    ),
    energy_kcal=D("470"),
    protein_g=D("7"),
    total_fat_g=D("18"),
    saturated_fat_g=D("9"),
    # Above the FSA high line for sugars, which is "more than 22.5 g".
    total_sugar_g=D("26.4"),
    fibre_g=D("1.4"),
    sodium_g=D("0.48"),
)


@pytest.fixture
def payload():
    return present(BISCUIT, grade_product(BISCUIT), candidate_ruleset())


def _row(payload, key):
    return next((row for row in payload["lowers"] if row["key"] == key), None)


# ---------------------------------------------------------------------------
# The headline
# ---------------------------------------------------------------------------
def test_the_biscuit_is_a_packaged_food_and_a_skip(payload):
    assert payload["taxonomy"]["category"] == "packaged_food"
    assert payload["taxonomy"]["subcategory"] == "biscuit"
    assert payload["grade"] in {"D", "E"}
    assert payload["decision"]["action"] == "skip"


# ---------------------------------------------------------------------------
# Sugar: the row this whole contract exists for
# ---------------------------------------------------------------------------
def test_sugar_appears_as_a_named_thing(payload):
    """It must be "Sugar", not a rule id, and not absent."""
    sugar = _row(payload, "sugar")
    assert sugar is not None, "sugar did not appear under what lowers it"
    assert sugar["label"] == "sugar"
    assert not sugar["label"].startswith("grade."), "a rule id reached the screen as a label"


def test_sugar_carries_the_quantity_that_caused_the_rule(payload):
    """A bare "Flagged" with no number is the failure this test exists for."""
    sugar = _row(payload, "sugar")
    assert sugar["quantity"] is not None, "sugar rendered with no quantity"
    assert sugar["quantity"]["value"] == 26.4
    assert sugar["quantity"]["unit"] == "g"
    # Per 100 g, stated as such. No pack size is invented.
    assert sugar["quantity"]["basis"] == "per_100_g"


def test_sugar_is_high_rather_than_generically_flagged(payload):
    sugar = _row(payload, "sugar")
    assert sugar["status"] == "high"
    assert sugar["explanation"] == "high_sugar"
    assert sugar["band"] == "red"


def test_sugar_names_the_exact_rule_and_an_openable_source(payload):
    sugar = _row(payload, "sugar")
    assert sugar["rule"] == "grade.step2.sugar"
    assert sugar["sources"], "sugar lowered the grade with no source behind it"
    source = sugar["sources"][0]
    assert source["url"], "the sugar source has nothing to open"
    assert source["publisher"]
    assert source["identifier"]


# ---------------------------------------------------------------------------
# The other three rows the screen promises
# ---------------------------------------------------------------------------
def test_processing_appears_with_its_finding(payload):
    processing = _row(payload, "processing")
    assert processing is not None
    assert processing["status"] == "flagged"
    assert processing["detail"]["nova_group"] == 4
    assert processing["sources"][0]["url"]


def test_the_flagged_additive_is_named_the_way_the_pack_names_it(payload):
    additive = next(
        (row for row in payload["lowers"] if row["key"].startswith("additive:")), None,
    )
    assert additive is not None, "the flagged additive did not appear"
    assert "319" in additive["label"], "the additive row does not name the INS number"
    assert additive["detail"]["function"], "the row does not say what the additive does"
    assert additive["sources"][0]["url"]


def test_protein_is_a_declared_label_fact_with_its_quantity(payload):
    protein = next((row for row in payload["helps"] if row["key"] == "protein"), None)
    assert protein is not None
    assert protein["status"] == "declared"
    assert protein["quantity"]["value"] == 7.0
    assert protein["quantity"]["basis"] == "per_100_g"


# ---------------------------------------------------------------------------
# The properties that must hold for every row, on every product
# ---------------------------------------------------------------------------
def test_every_lowering_row_is_complete(payload):
    assert payload["lowers"], "a grade E product produced no reasons"
    for row in payload["lowers"]:
        assert row["key"], row
        assert row["label"] and not row["label"].startswith("grade."), row
        assert row["status"] in STATUSES, row
        assert row["band"] in {"green", "yellow", "red"}, row
        assert row["explanation"], row
        assert row["evidence"]["status"], row


def test_statuses_do_not_all_collapse_to_one_word(payload):
    statuses = {row["status"] for row in payload["lowers"]}
    assert len(statuses) > 1, f"every lowering row reads the same: {statuses}"


def test_a_prohibited_additive_does_not_look_like_a_high_nutrient():
    """Two different things must not arrive wearing the same word."""
    black = next((a for a in ADDITIVES if a.tier == "black"), None)
    assert black is not None, "no black-tier additive to test with"
    product = ProductInput(
        name="Test Rusk",
        ingredients=("refined wheat flour (maida)", black.name, "sugar"),
        energy_kcal=D("400"), total_sugar_g=D("30"), saturated_fat_g=D("8"),
        sodium_g=D("0.5"), protein_g=D("6"),
    )
    payload = present(product, grade_product(product), candidate_ruleset())
    prohibited = next(
        (r for r in payload["lowers"] if r["status"] == "not_permitted"), None,
    )
    high = next((r for r in payload["lowers"] if r["status"] == "high"), None)
    assert prohibited is not None, "a black-tier additive did not read as not permitted"
    assert high is not None, "no high nutrient in a product that has one"
    assert prohibited["status"] != high["status"]


# ---------------------------------------------------------------------------
# Every negative claim owes the customer something they can open
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("label", "product"),
    [
        (
            "nutrient threshold",
            ProductInput(
                name="Salty Namkeen", ingredients=("besan", "iodised salt", "palm oil"),
                energy_kcal=D("520"), total_fat_g=D("32"), saturated_fat_g=D("14"),
                sodium_g=D("1.4"), total_sugar_g=D("2"), protein_g=D("12"),
            ),
        ),
        (
            "additive",
            ProductInput(
                name="Packaged Cake", energy_kcal=D("400"),
                ingredients=("refined wheat flour (maida)", "sugar", "antioxidant (ins 319)"),
                total_sugar_g=D("30"), saturated_fat_g=D("8"), sodium_g=D("0.3"),
            ),
        ),
        (
            "trans fat rule",
            ProductInput(
                name="Vanaspati Rusk", energy_kcal=D("450"),
                ingredients=("refined wheat flour (maida)", "vanaspati", "sugar"),
                total_sugar_g=D("18"), saturated_fat_g=D("12"), sodium_g=D("0.4"),
                trans_fat_g=D("3"),
            ),
        ),
        (
            "named-ingredient rule",
            ProductInput(
                name="Mango Drink", categories="beverage", basis="drink",
                ingredients=("water", "sugar", "mango pulp 10%", "acidity regulator (ins 330)"),
                energy_kcal=D("60"), total_sugar_g=D("14"), sodium_g=D("0.01"),
                name_promises="mango pulp", declared_percentages={"mango pulp": D("10")},
            ),
        ),
        (
            "processing rule",
            ProductInput(
                name="Instant Noodles", energy_kcal=D("450"),
                ingredients=("refined wheat flour (maida)", "palm oil", "flavour enhancer (ins 621)"),
                total_fat_g=D("18"), saturated_fat_g=D("9"), sodium_g=D("1.2"),
                total_sugar_g=D("2"), protein_g=D("9"),
            ),
        ),
    ],
)
def test_no_negative_factor_renders_without_an_openable_source(label, product):
    payload = present(product, grade_product(product), candidate_ruleset())
    for row in payload["lowers"]:
        assert row["sources"], f"{label}: {row['key']} lowered the grade with no source"
        for source in row["sources"]:
            assert source["url"], f"{label}: {row['key']} has a source with nothing to open"
            assert source["publisher"], f"{label}: {row['key']} has a source with no publisher"


def test_every_registered_grading_rule_has_an_openable_candidate_source():
    """A rule with nothing to open cannot support a negative claim, ever."""
    for rule in GRADING_RULES:
        assert rule.candidate_source.url, f"{rule.rule_id} has no openable source"
        assert rule.candidate_source.identifier, f"{rule.rule_id} has no source identifier"
        assert rule.candidate_source.publisher, f"{rule.rule_id} has no publisher"


# ---------------------------------------------------------------------------
# The candidate boundary
# ---------------------------------------------------------------------------
def test_no_rule_is_presented_as_published_until_it_is(payload):
    """Nothing here marks the static catalogue as reviewed."""
    for row in payload["lowers"]:
        assert row["evidence"]["status"] == "candidate", row["key"]
    assert payload["evidence"]["unpublished_rules"], (
        "the payload does not say which of its rules are still candidates"
    )


def test_the_candidate_ruleset_names_every_required_rule_it_is_missing():
    ruleset = candidate_ruleset()
    assert ruleset.unpublished, "a candidate ruleset reported nothing unpublished"
    assert ruleset.unpublished_required, "required rules were not distinguished"


def test_candidate_required_rules_cannot_issue_a_customer_grade():
    """Candidate constants are authoring input, never a production verdict."""
    result = grade_product(BISCUIT)
    assert result.grade is not None

    bounded = enforce_published_required_rules(result, candidate_ruleset())

    assert bounded.outcome.value == "not_enough_information"
    assert bounded.grade is None
    assert set(candidate_ruleset().unpublished_required).issubset(bounded.missing)
