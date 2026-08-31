"""India's food grading engine, checked against the validation table.

The table below is the specification, not a sample of it. Nutri-Score fails
here in two directions at once: it flags ghee red because it reads a cooking
fat as a food, and it rates a low-fat biscuit above dal because a weighted
average lets a bad ingredient list buy its way back with a good number. Every
row exists because one of those two failures would produce a different answer.

The numbers in each fixture are realistic Indian label values. Where a product
sits near a band edge the comment says so, because a rule that only works on
comfortable inputs is not a rule.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest
from app.domains.nutrition.grading import (
    FOOD_GRADE_ENGINE_VERSION,
    Grade,
    GradeOutcome,
    ProductInput,
    grade_product,
    nova,
)
from app.domains.nutrition.grading.rules import NOVA_CEILINGS


def P(**kw) -> ProductInput:
    return ProductInput(**kw)


CASES = [
    # --- Culinary ingredients -> NOT_GRADED ------------------------------
    ("Ghee", P(name="Amul Pure Ghee", ingredients=("ghee",), energy_kcal=D("900"),
               total_fat_g=D("100"), saturated_fat_g=D("65")), "NOT_GRADED"),
    ("Sunflower oil", P(name="Fortune Sunlite Refined Sunflower Oil",
                        ingredients=("refined sunflower oil",), energy_kcal=D("900"),
                        total_fat_g=D("100"), saturated_fat_g=D("12")), "NOT_GRADED"),
    ("Mustard oil", P(name="Mustard Oil", ingredients=("mustard oil",), energy_kcal=D("900"),
                      total_fat_g=D("100"), saturated_fat_g=D("12")), "NOT_GRADED"),
    ("Coconut oil", P(name="Coconut Oil", ingredients=("coconut oil",), energy_kcal=D("900"),
                      total_fat_g=D("100"), saturated_fat_g=D("82")), "NOT_GRADED"),
    ("Salt", P(name="Tata Salt Iodised", ingredients=("iodised salt",),
               sodium_g=D("38"), energy_kcal=D("0")), "NOT_GRADED"),
    ("Sugar", P(name="Sugar", ingredients=("sugar",), energy_kcal=D("400"),
                total_sugar_g=D("100")), "NOT_GRADED"),
    ("Jaggery", P(name="Organic Jaggery", ingredients=("jaggery",), energy_kcal=D("383"),
                  total_sugar_g=D("85")), "NOT_GRADED"),

    # --- Whole foods -> A -------------------------------------------------
    ("Toor dal", P(name="Toor Dal", ingredients=("toor dal",), energy_kcal=D("343"),
                   protein_g=D("22"), total_fat_g=D("1.7"), saturated_fat_g=D("0.4"),
                   total_sugar_g=D("2.4"), fibre_g=D("15"), sodium_g=D("0.017"),
                   whole_food_pct=D("100")), "A"),
    ("Atta", P(name="Aashirvaad Whole Wheat Atta", ingredients=("whole wheat",),
               energy_kcal=D("341"), protein_g=D("12"), total_fat_g=D("1.7"),
               saturated_fat_g=D("0.3"), total_sugar_g=D("1.5"), fibre_g=D("11"),
               sodium_g=D("0.002"), whole_food_pct=D("100")), "A"),
    ("Full-fat curd", P(name="Full Cream Curd", ingredients=("pasteurised milk", "live cultures"),
                        energy_kcal=D("98"), protein_g=D("3.5"), total_fat_g=D("6.5"),
                        saturated_fat_g=D("4.2"), total_sugar_g=D("4.5"), sodium_g=D("0.05"),
                        fermented=True, whole_food_pct=D("100")), "A"),
    ("Paneer", P(name="Fresh Paneer", ingredients=("pasteurised milk", "citric acid"),
                 energy_kcal=D("296"), protein_g=D("18.3"), total_fat_g=D("22"),
                 saturated_fat_g=D("14"), total_sugar_g=D("1.2"), sodium_g=D("0.02"),
                 whole_food_pct=D("100")), "A"),
    ("Roasted chana", P(name="Roasted Chana", ingredients=("bengal gram",), energy_kcal=D("364"),
                        protein_g=D("20"), total_fat_g=D("5.6"), saturated_fat_g=D("0.6"),
                        total_sugar_g=D("2"), fibre_g=D("16"), sodium_g=D("0.02"),
                        whole_food_pct=D("100")), "A"),
    ("Poha", P(name="Poha", ingredients=("flattened rice",), energy_kcal=D("346"),
               protein_g=D("6.6"), total_fat_g=D("1.2"), saturated_fat_g=D("0.3"),
               total_sugar_g=D("0.5"), fibre_g=D("1.2"), sodium_g=D("0.003"),
               whole_food_pct=D("100")), "A"),
    ("Whole milk", P(name="Full Cream Milk", ingredients=("milk",), energy_kcal=D("67"),
                     protein_g=D("3.2"), total_fat_g=D("3.5"), saturated_fat_g=D("2.2"),
                     total_sugar_g=D("4.8"), sodium_g=D("0.045"), basis="drink",
                     whole_food_pct=D("100")), "A"),

    # --- Simple processed -------------------------------------------------
    ("Idli batter", P(name="Idli Batter", ingredients=("rice", "urad dal", "water", "salt"),
                      energy_kcal=D("158"), protein_g=D("5"), total_fat_g=D("0.5"),
                      saturated_fat_g=D("0.1"), total_sugar_g=D("0.4"), fibre_g=D("2"),
                      sodium_g=D("0.28"), fermented=True, whole_food_pct=D("95")), ("A", "B")),

    # --- Refined ----------------------------------------------------------
    ("Maida", P(name="Maida", ingredients=("refined wheat flour",), energy_kcal=D("348"),
                protein_g=D("10"), total_fat_g=D("1"), saturated_fat_g=D("0.2"),
                total_sugar_g=D("0.4"), fibre_g=D("2.7"), sodium_g=D("0.002")), "C"),
    ("White bread", P(name="White Bread", ingredients=(
        "refined wheat flour (maida)", "water", "sugar", "yeast", "iodised salt",
        "edible vegetable oil"), energy_kcal=D("265"), protein_g=D("8"),
        total_fat_g=D("3.2"), saturated_fat_g=D("0.7"), total_sugar_g=D("5"),
        fibre_g=D("2.7"), sodium_g=D("0.49")), ("C", "D")),

    # --- Biscuits ---------------------------------------------------------
    ("'Atta' biscuit, maida first", P(
        name="Digestive Atta Biscuit", name_promises="atta",
        declared_percentages={"atta": D("30")},
        ingredients=("refined wheat flour (maida)", "whole wheat flour (atta)", "sugar",
                     "edible vegetable oil (palm)", "invert syrup", "raising agents (ins 500(ii))",
                     "emulsifier (ins 322)", "iodised salt"),
        energy_kcal=D("480"), protein_g=D("7"), total_fat_g=D("19"), saturated_fat_g=D("9"),
        total_sugar_g=D("20"), fibre_g=D("3.5"), sodium_g=D("0.35")), "D"),
    ("Glucose biscuit", P(
        name="Glucose Biscuit",
        ingredients=("refined wheat flour (maida)", "sugar", "edible vegetable oil (palm)",
                     "invert syrup", "raising agents", "emulsifier (ins 322)", "iodised salt"),
        energy_kcal=D("450"), protein_g=D("7.2"), total_fat_g=D("13.4"),
        saturated_fat_g=D("6.5"), total_sugar_g=D("22.5"), fibre_g=D("1.4"),
        sodium_g=D("0.35")), "D"),

    # --- Drinks and snacks ------------------------------------------------
    ("Malt drink, high sugar", P(
        name="Malt Health Drink", marketed_to_children=True,
        ingredients=("sugar", "malt extract", "milk solids", "cocoa solids",
                     "emulsifier (ins 322)", "added flavour", "vitamins", "minerals"),
        energy_kcal=D("400"), protein_g=D("7"), total_fat_g=D("1.5"),
        saturated_fat_g=D("0.9"), total_sugar_g=D("70"), added_sugar_g=D("70"),
        fibre_g=D("1"), sodium_g=D("0.14"), whole_food_pct=D("0")), ("D", "E")),
    ("Instant noodles", P(
        name="Instant Noodles Masala",
        ingredients=("refined wheat flour (maida)", "edible vegetable oil (palm)",
                     "iodised salt", "thickener (ins 508)", "acidity regulator",
                     "flavour enhancer (ins 635)", "anticaking agent (ins 551)"),
        energy_kcal=D("450"), protein_g=D("9"), total_fat_g=D("17"),
        saturated_fat_g=D("8"), total_sugar_g=D("3"), fibre_g=D("2"),
        sodium_g=D("1.4")), ("D", "E")),
    ("Fried namkeen in palm oil", P(
        name="Aloo Bhujia Namkeen",
        ingredients=("gram flour (besan)", "edible vegetable oil (palm)", "potato",
                     "iodised salt", "spices", "acidity regulator (ins 330)"),
        energy_kcal=D("560"), protein_g=D("11"), total_fat_g=D("35"),
        saturated_fat_g=D("14"), total_sugar_g=D("2"), fibre_g=D("5"),
        sodium_g=D("0.85"), whole_food_pct=D("55")), "D"),
    ("'Real fruit' juice, 12% fruit", P(
        name="Real Mixed Fruit Juice Drink", name_promises="fruit",
        declared_percentages={"fruit": D("12")}, basis="drink",
        ingredients=("water", "sugar", "mixed fruit pulp and juice concentrate",
                     "acidity regulator (ins 330)", "added flavour",
                     "preservative (ins 211)"),
        energy_kcal=D("54"), protein_g=D("0.1"), total_fat_g=D("0"),
        saturated_fat_g=D("0"), total_sugar_g=D("12"), added_sugar_g=D("10.5"),
        fibre_g=D("0.2"), sodium_g=D("0.01"), whole_food_pct=D("12")), "D"),
    ("Cola", P(
        name="Cola", basis="drink",
        ingredients=("carbonated water", "sugar", "acidity regulator (ins 338)",
                     "caramel colour (ins 150d)", "added flavour", "caffeine"),
        energy_kcal=D("42"), protein_g=D("0"), total_fat_g=D("0"),
        saturated_fat_g=D("0"), total_sugar_g=D("10.6"), added_sugar_g=D("10.6"),
        fibre_g=D("0"), sodium_g=D("0.004"), whole_food_pct=D("0")), "E"),
    ("Partially hydrogenated oil product", P(
        name="Cream Filled Wafer",
        ingredients=("refined wheat flour (maida)", "sugar",
                     "partially hydrogenated vegetable oil", "cocoa solids",
                     "emulsifier (ins 322)"),
        energy_kcal=D("520"), protein_g=D("5"), total_fat_g=D("28"),
        saturated_fat_g=D("15"), trans_fat_g=D("2.5"), total_sugar_g=D("35"),
        fibre_g=D("1"), sodium_g=D("0.1")), "E"),
    ("Whey protein isolate", P(
        name="Whey Protein Isolate",
        ingredients=("whey protein isolate", "cocoa powder", "sucralose", "added flavour"),
        energy_kcal=D("370"), protein_g=D("80"), total_fat_g=D("1"),
        saturated_fat_g=D("0.5"), total_sugar_g=D("1"), fibre_g=D("1"),
        sodium_g=D("0.3")), "C"),
]


def _got(result) -> str:
    return result.grade.value if result.grade else result.outcome.value.upper()


@pytest.mark.parametrize(("label", "product", "expected"), CASES, ids=[row[0] for row in CASES])
def test_validation_table(label, product, expected):
    """Every row must pass. A failure here means the engine is wrong."""
    result = grade_product(product)
    allowed = (expected,) if isinstance(expected, str) else expected
    assert _got(result) in allowed, (
        f"{label}: expected {' or '.join(allowed)}, got {_got(result)}. "
        f"NOVA {result.nova_group}, ceiling {result.ceiling}. "
        + " | ".join(f"{e.rule_id}: {e.effect}" for e in result.trace if e.effect)
    )


def test_the_whole_table_passes_together():
    """The table as one assertion, so a regression names every row it broke."""
    broken = []
    for label, product, expected in CASES:
        result = grade_product(product)
        allowed = (expected,) if isinstance(expected, str) else expected
        if _got(result) not in allowed:
            broken.append(f"{label}: expected {' or '.join(allowed)}, got {_got(result)}")
    assert not broken, "validation table failures:\n" + "\n".join(broken)


# ---------------------------------------------------------------------------
# The gates, checked as gates
# ---------------------------------------------------------------------------
def test_every_result_carries_the_engine_version():
    for _, product, _ in CASES:
        assert grade_product(product).engine_version == FOOD_GRADE_ENGINE_VERSION


def test_every_trace_entry_names_a_rule_and_a_source():
    """A grade has to be readable backwards, from the letter to the pack."""
    for label, product, _ in CASES:
        for entry in grade_product(product).trace:
            assert entry.rule_id, label
            assert entry.source, f"{label}: {entry.rule_id} has no source"
            assert entry.finding, f"{label}: {entry.rule_id} has no finding"


def test_nova_4_can_never_exceed_c():
    """The gate that makes the whole thing work.

    A group 4 product with a nutrition panel any dietitian would sign off:
    no sugar, no salt, no saturated fat, high protein, high fibre.
    """
    perfect = P(
        name="Ultra-processed Meal Replacement",
        ingredients=("maltodextrin", "soy protein isolate", "emulsifier (ins 322)",
                     "added flavour"),
        energy_kcal=D("380"), protein_g=D("40"), total_fat_g=D("2"),
        saturated_fat_g=D("0.3"), total_sugar_g=D("0.5"), fibre_g=D("20"),
        sodium_g=D("0.05"), whole_food_pct=D("0"),
    )
    result = grade_product(perfect)
    assert result.nova_group == nova.NOVA_ULTRA_PROCESSED
    assert result.ceiling is Grade.C
    assert result.grade is Grade.C, "a perfect panel must not lift a group 4 product above C"


def test_the_ceilings_are_the_ones_specified():
    assert NOVA_CEILINGS[1] is Grade.A
    assert NOVA_CEILINGS[3] is Grade.B
    assert NOVA_CEILINGS[4] is Grade.C


def test_saturated_fat_is_not_penalised_at_nova_1():
    """Paneer at 14 g saturated fat is still an A. This is the Nutri-Score bug."""
    result = grade_product(next(p for label, p, _ in CASES if label == "Paneer"))
    sat = next(band for band in result.bands if band.nutrient == "saturated fat")
    assert sat.band == "high"
    assert sat.penalised is False
    assert result.grade is Grade.A


def test_saturated_fat_is_penalised_at_nova_4_and_names_its_source():
    """Where it counts, say where it came from rather than how much there is."""
    result = grade_product(next(p for label, p, _ in CASES if label == "Glucose biscuit"))
    sat = next(band for band in result.bands if band.nutrient == "saturated fat")
    assert sat.penalised is True
    assert sat.attribution is not None
    assert "palm" in sat.attribution
    assert "ingredient" in sat.attribution


def test_total_fat_is_never_a_penalty_on_its_own():
    """Penalising fat by itself is what ranks a low-fat biscuit above paneer."""
    for _, product, _ in CASES:
        result = grade_product(product)
        assert not any(band.nutrient == "total fat" for band in result.bands)
        assert not any("total_fat" in entry.rule_id for entry in result.trace)


def test_a_low_fat_biscuit_never_outranks_dal():
    """The failure that started this, stated as a test."""
    low_fat_biscuit = P(
        name="Low Fat Digestive Biscuit",
        ingredients=("refined wheat flour (maida)", "sugar", "invert syrup",
                     "raising agents", "emulsifier (ins 322)", "iodised salt"),
        energy_kcal=D("400"), protein_g=D("7"), total_fat_g=D("2"),
        saturated_fat_g=D("0.8"), total_sugar_g=D("20"), fibre_g=D("2"),
        sodium_g=D("0.4"),
    )
    dal = next(p for label, p, _ in CASES if label == "Toor dal")
    from app.domains.nutrition.grading.rules import GRADE_ORDER

    biscuit_grade = grade_product(low_fat_biscuit).grade
    dal_grade = grade_product(dal).grade
    assert GRADE_ORDER.index(biscuit_grade) > GRADE_ORDER.index(dal_grade)


def test_ghee_is_not_graded_and_gets_guidance_instead():
    result = grade_product(next(p for label, p, _ in CASES if label == "Ghee"))
    assert result.outcome is GradeOutcome.NOT_GRADED
    assert result.grade is None
    assert result.quantity_guidance
    assert result.purity_note
    assert "adulterated" in result.purity_note


def test_the_culinary_check_runs_before_everything_else():
    """Even a cooking fat with an ultra-processed ingredient list is NOT_GRADED."""
    result = grade_product(P(
        name="Vanaspati", ingredients=("hydrogenated vegetable oil",),
        energy_kcal=D("900"), total_fat_g=D("100"), saturated_fat_g=D("50"),
    ))
    assert result.outcome is GradeOutcome.NOT_GRADED
    assert [entry.step for entry in result.trace] == [0], (
        "step 0 returns before any other gate runs"
    )


def test_partially_hydrogenated_oil_is_automatic_e():
    result = grade_product(next(
        p for label, p, _ in CASES if label == "Partially hydrogenated oil product"
    ))
    assert result.grade is Grade.E
    assert any("partially_hydrogenated" in entry.rule_id for entry in result.trace)


def test_trans_fat_without_the_regulatory_oils_and_fats_denominator_is_not_automatic_e():
    result = grade_product(P(
        name="Bakery Shortening Cake",
        ingredients=("refined wheat flour (maida)", "sugar", "edible vegetable oil"),
        energy_kcal=D("400"), total_fat_g=D("20"), trans_fat_g=D("1.2"),
        saturated_fat_g=D("8"), total_sugar_g=D("18"), sodium_g=D("0.2"),
    ))
    assert result.grade is not Grade.E
    assert any(entry.rule_id == "grade.step2.trans_fat_denominator_missing" for entry in result.trace)
    assert any("trans_fat" in entry.rule_id for entry in result.trace)


def test_a_black_tier_additive_is_automatic_e():
    result = grade_product(P(
        name="Bakery Bread",
        ingredients=("refined wheat flour", "water", "potassium bromate", "iodised salt"),
        energy_kcal=D("260"), protein_g=D("8"), total_fat_g=D("2"),
        saturated_fat_g=D("0.5"), total_sugar_g=D("3"), sodium_g=D("0.4"),
    ))
    assert result.grade is Grade.E
    assert any(entry.rule_id == "grade.step3.black_tier" for entry in result.trace)


def test_a_red_tier_additive_caps_at_d():
    result = grade_product(P(
        name="Fried Snack With TBHQ",
        ingredients=("gram flour", "edible vegetable oil", "iodised salt",
                     "antioxidant (ins 319)"),
        energy_kcal=D("500"), protein_g=D("10"), total_fat_g=D("25"),
        saturated_fat_g=D("4"), total_sugar_g=D("1"), fibre_g=D("4"),
        sodium_g=D("0.2"),
    ))
    assert result.ceiling is Grade.D
    assert any(entry.rule_id == "grade.step3.red_tier" for entry in result.trace)


def test_a_synthetic_colour_in_a_child_product_is_flagged_and_caps():
    result = grade_product(P(
        name="Children's Fruit Candy", marketed_to_children=True,
        ingredients=("sugar", "glucose syrup", "acidity regulator",
                     "synthetic colour (ins 110)", "added flavour"),
        energy_kcal=D("390"), protein_g=D("0"), total_fat_g=D("0"),
        saturated_fat_g=D("0"), total_sugar_g=D("80"), added_sugar_g=D("80"),
        sodium_g=D("0.02"), whole_food_pct=D("0"),
    ))
    flagged = [e for e in result.trace if e.rule_id == "grade.step3.child_marketed_synthetic_colour"]
    assert flagged, "a synthetic colour sold to children must be flagged"
    assert result.grade is Grade.E


@pytest.mark.parametrize(
    ("declared", "expected_ceiling"),
    [(D("60"), None), (D("30"), None), (D("15"), Grade.C), (D("5"), Grade.D)],
)
def test_named_ingredient_integrity_bands(declared, expected_ceiling):
    """What the name promises, against what the label declares."""
    result = grade_product(P(
        name="Almond Cookie", name_promises="almond",
        declared_percentages={"almond": declared},
        ingredients=("whole wheat flour", "sugar", "almond", "butter"),
        energy_kcal=D("450"), protein_g=D("8"), total_fat_g=D("18"),
        saturated_fat_g=D("4"), total_sugar_g=D("18"), fibre_g=D("4"),
        sodium_g=D("0.2"),
    ))
    entry = next(e for e in result.trace if e.rule_id == "grade.step4.declared_percentage")
    if expected_ceiling is None:
        assert "No ceiling" in entry.effect
    else:
        assert expected_ceiling.value in entry.effect


def test_a_missing_nutrition_panel_shows_no_grade():
    result = grade_product(P(
        name="Unlabelled Namkeen", ingredients=("gram flour", "edible vegetable oil", "salt"),
        has_nutrition_panel=False,
    ))
    assert result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION
    assert result.grade is None
    assert "nutrition panel" in result.missing


def test_a_missing_ingredient_list_shows_no_grade():
    result = grade_product(P(
        name="Loose Biscuit", ingredients=(), has_ingredient_list=False,
        energy_kcal=D("450"), total_sugar_g=D("22"), saturated_fat_g=D("6"),
        sodium_g=D("0.3"),
    ))
    assert result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION
    assert result.grade is None
    assert "ingredient list" in result.missing


def test_nothing_is_guessed_when_information_is_missing():
    """No grade, no letter, no partial answer."""
    result = grade_product(P(name="Mystery Snack", ingredients=(), has_ingredient_list=False))
    payload = result.as_payload()
    assert payload["grade"] is None
    assert payload["outcome"] == "not_enough_information"


def test_the_steps_always_run_in_order():
    for label, product, _ in CASES:
        steps = [entry.step for entry in grade_product(product).trace]
        assert steps == sorted(steps), f"{label} ran its steps out of order: {steps}"


def test_sugar_is_charged_once_not_twice():
    """Grams and share-of-energy are two readings of the same sugar."""
    juice = next(p for label, p, _ in CASES if "juice" in label)
    result = grade_product(juice)
    sugar_entries = [e for e in result.trace if e.rule_id.startswith("grade.step2.sugar")]
    assert len(sugar_entries) == 1
    assert "charged once" in sugar_entries[0].effect


def test_positives_never_lift_past_the_ceiling():
    """Roasted salted nuts: real food, real salt, still capped by processing."""
    result = grade_product(P(
        name="Salted Roasted Almonds",
        ingredients=("almonds", "iodised salt"),
        energy_kcal=D("600"), protein_g=D("21"), total_fat_g=D("50"),
        saturated_fat_g=D("4"), total_sugar_g=D("4"), fibre_g=D("12"),
        sodium_g=D("0.7"), whole_food_pct=D("98"),
    ))
    assert result.ceiling is Grade.B, "salt added to a whole food is NOVA 3"
    assert result.grade is Grade.B, "positives may cancel a penalty, never beat the ceiling"


# ---------------------------------------------------------------------------
# What one screen is handed
# ---------------------------------------------------------------------------
def test_the_screen_payload_carries_four_components_and_a_colour():
    from app.domains.nutrition.grading.presentation import COMPONENT_KEYS, present

    product = next(p for label, p, _ in CASES if label == "Glucose biscuit")
    payload = present(product, grade_product(product))
    assert payload["grade"] == "D"
    assert payload["band"] == "red"
    assert [row["key"] for row in payload["components"]] == list(COMPONENT_KEYS)
    assert all(row["band"] in {"green", "yellow", "red"} for row in payload["components"])
    assert all(row["source"] for row in payload["components"])


def test_the_screen_payload_carries_every_ingredient_free():
    from app.domains.nutrition.grading.presentation import present

    product = next(p for label, p, _ in CASES if label == "Glucose biscuit")
    payload = present(product, grade_product(product))
    assert len(payload["ingredients"]) == len(product.ingredients)
    emulsifier = next(row for row in payload["ingredients"] if "emulsifier" in row["name"].lower())
    assert emulsifier["tier"] in {"plain", "green", "amber", "red", "black"}


def test_the_screen_payload_carries_no_english_copy():
    """The app owns its words; the wire carries keys, bands and sources."""
    from app.domains.nutrition.grading.presentation import present

    product = next(p for label, p, _ in CASES if label == "Cola")
    payload = present(product, grade_product(product))
    for component in payload["components"]:
        assert component["state"]
        # A state is a key the app maps to a sentence, never a sentence itself.
        assert " " not in component["state"]


@pytest.mark.parametrize(
    ("label", "expected_band"),
    [("Toor dal", "green"), ("Maida", "yellow"), ("Cola", "red")],
)
def test_the_colour_matches_the_letter(label, expected_band):
    from app.domains.nutrition.grading.presentation import present

    product = next(p for row_label, p, _ in CASES if row_label == label)
    assert present(product, grade_product(product))["band"] == expected_band


def test_an_ingredient_list_is_split_the_way_a_pack_reads():
    from app.domains.nutrition.grading.from_scan import split_ingredients

    parts = split_ingredients(
        "Refined wheat flour (maida), Sugar, Edible vegetable oil (palm, cottonseed), Salt"
    )
    assert parts == (
        "Refined wheat flour (maida)", "Sugar",
        "Edible vegetable oil (palm, cottonseed)", "Salt",
    )


def test_a_declared_percentage_is_read_off_the_ingredient_list():
    from app.domains.nutrition.grading.from_scan import declared_percentages

    assert declared_percentages(("Atta 30%", "Sugar", "Palm oil")) == {"atta": D("30")}


def test_a_missing_panel_reaches_the_screen_as_not_enough_information():
    from app.domains.nutrition.grading.from_scan import build
    from app.domains.nutrition.grading.presentation import present

    product = build(barcode="8901234567890", name="Unknown Snack", off_half=None)
    payload = present(product, grade_product(product))
    assert payload["outcome"] == "not_enough_information"
    assert payload["grade"] is None
    assert payload["missing"]
