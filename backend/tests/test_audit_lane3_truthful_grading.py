"""F06/F07: unsafe label numbers and candidate rules never publish a grade."""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import replace
from decimal import Decimal, InvalidOperation

import pytest
from app.domains.b2b import truth as b2b_truth
from app.domains.nutrition.grading import from_scan, presentation, production_rules
from app.domains.nutrition.grading.engine import ProductInput, grade_product
from app.domains.nutrition.grading.production_rules import (
    STATUS_PUBLISHED,
    ProductionRuleset,
    candidate_ruleset,
)
from app.domains.nutrition.grading.rules import GradeOutcome
from app.domains.product import truth as product_truth


def _published(*, unpublished: tuple[str, ...] = (), claim_offset: int = 0) -> ProductionRuleset:
    provenance = {}
    for index, (rule_id, row) in enumerate(sorted(candidate_ruleset().provenance.items())):
        provenance[rule_id] = row if rule_id in unpublished else replace(
            row, status=STATUS_PUBLISHED,
            claim_ids=(uuid.UUID(int=index + 1 + claim_offset),), claim_version=1,
        )
    return ProductionRuleset(provenance=provenance)


def _facts(**nutrition: object) -> dict:
    return {
        "product_name": "Cereal", "ingredients_text": "whole oats, sugar",
        "nutrition_basis": "per_100g",
        "nutrition_per_100g": {
            "energy_kcal": "400 kcal", "total_sugar_g": "24 g",
            "saturated_fat_g": "2 g", **nutrition,
        },
    }


def _off(**nutriments: object) -> dict:
    return {
        "ingredients_text": "whole oats, sugar",
        "nutriments": {
            "energy-kcal_100g": 400, "sugars_100g": 24,
            "saturated-fat_100g": 2, **nutriments,
        },
    }


def _adapt(source: str, value: object, *, field: str = "sodium") -> ProductInput:
    if source == "confirmed":
        return from_scan.build_confirmed_label(
            barcode="8901000000001", facts=_facts(**{f"{field}_g": value}),
        )
    return from_scan.build(
        barcode="8901000000001", name="Cereal",
        off_half=_off(**{f"{field}_100g": value}),
    )


@pytest.mark.parametrize("source", ["confirmed", "off"])
@pytest.mark.parametrize("raw,expected", [
    ("50 mg", Decimal("0.05")),
    ("0.05 g", Decimal("0.05")),
    (0.05, Decimal("0.05")),
    ("0.05", Decimal("0.05")),
])
def test_mass_units_are_canonicalised_without_losing_the_declared_unit(source, raw, expected):
    product = _adapt(source, raw)
    assert product.sodium_g == expected
    assert product.invalid_nutrition_fields == ()


@pytest.mark.parametrize("source", ["confirmed", "off"])
@pytest.mark.parametrize("raw", [
    "50 kcal", "12 bananas", "50 mg garbage", "50 mystery-units",
    "NaN", "Infinity", "+Infinity", "-Infinity",
    float("nan"), float("inf"), Decimal("NaN"), Decimal("-Infinity"),
])
def test_present_but_invalid_nutrient_fails_closed_through_product_truth(source, raw):
    product = _adapt(source, raw)
    assert product.sodium_g is None
    assert product.invalid_nutrition_fields == ("sodium_g",)
    assert product.total_sugar_g == Decimal("24")
    graded = product_truth.grade(product, _published())
    assert graded.result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION
    assert graded.result.grade is None and graded.result.ceiling is None
    assert "invalid nutrition values" in graded.result.missing
    assert graded.payload["grade"] is None
    json.dumps(graded.payload, allow_nan=False)
    assert graded.payload["nutrition"]["salt_g"] is None


@pytest.mark.parametrize("source", ["confirmed", "off"])
def test_explicit_energy_units_are_canonical_kcal(source):
    if source == "confirmed":
        kcal = from_scan.build_confirmed_label(
            barcode="x", facts=_facts(energy_kcal="100 kcal"),
        )
        kj = from_scan.build_confirmed_label(
            barcode="x", facts=_facts(energy_kcal="418.4 kJ"),
        )
    else:
        kcal = from_scan.build(barcode="x", name="Cereal", off_half=_off(**{
            "energy-kcal_100g": "100 kcal",
        }))
        kj = from_scan.build(barcode="x", name="Cereal", off_half=_off(**{
            "energy-kj_100g": "418.4 kJ", "energy-kcal_100g": None,
        }))
    assert kcal.energy_kcal == kj.energy_kcal == Decimal("100")
    assert kcal.invalid_nutrition_fields == kj.invalid_nutrition_fields == ()


def test_ambiguous_off_energy_is_never_guessed_as_kcal():
    product = from_scan.build(barcode="x", name="Cereal", off_half=_off(**{
        "energy-kcal_100g": None, "energy_100g": 4184,
    }))
    assert product.energy_kcal is None
    assert product.invalid_nutrition_fields == ()


def test_valid_alias_cannot_hide_an_invalid_provided_alias():
    product = from_scan.build(barcode="x", name="Cereal", off_half=_off(fiber_100g="4 g", fibre_100g="NaN"))
    assert product.fibre_g is None
    assert product.invalid_nutrition_fields == ("fibre_g",)
    assert product_truth.grade(product, _published()).result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION


def test_valid_kj_cannot_hide_an_invalid_explicit_kcal_value():
    product = from_scan.build(barcode="x", name="Cereal", off_half=_off(**{
        "energy-kcal_100g": "12 mystery-units", "energy-kj_100g": "418.4 kJ",
    }))
    assert product.energy_kcal is None
    assert product.invalid_nutrition_fields == ("energy_kcal",)
    assert product_truth.grade(product, _published()).result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION


@pytest.mark.parametrize("unsafe", [float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity")])
def test_direct_nonfinite_product_input_cannot_reach_arithmetic_or_json(unsafe):
    product = ProductInput(
        name="Cereal", ingredients=("whole oats", "sugar"),
        energy_kcal=Decimal("400"), total_sugar_g=Decimal("24"),
        saturated_fat_g=Decimal("2"), sodium_g=unsafe,
    )
    graded = product_truth.grade(product, _published())
    assert graded.result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION
    assert graded.result.grade is None and graded.result.ceiling is None
    json.dumps(graded.payload, allow_nan=False)


def _assert_finite_payload(value):
    """Check the entire published payload, not only its nutrition envelope."""
    if isinstance(value, float):
        assert math.isfinite(value)
    elif isinstance(value, dict):
        for child in value.values():
            _assert_finite_payload(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_finite_payload(child)
    elif isinstance(value, str):
        assert value not in {"NaN", "Infinity", "+Infinity", "-Infinity"}
    json.dumps(value, allow_nan=False)


@pytest.mark.parametrize("field", ["protein_g", "fibre_g"])
@pytest.mark.parametrize("unsafe", [Decimal("NaN"), Decimal("Infinity")])
def test_nonfinite_positive_label_fact_never_escapes_product_truth(field, unsafe):
    product = replace(_bread(), **{field: unsafe})
    graded = product_truth.grade(product, _published())
    assert graded.result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION
    assert graded.result.grade is None
    _assert_finite_payload(graded.payload)
    assert not any(row["key"] == field.removesuffix("_g") for row in graded.payload["positives"])
    # Defensive presentation of mismatched caller data must also stay finite.
    presented = presentation.present(product, grade_product(_bread()), _published())
    positive = next(row for row in presented["positives"] if row["key"] == field.removesuffix("_g"))
    assert positive["quantity"] is None
    _assert_finite_payload(presented)


@pytest.mark.parametrize("unsafe", [Decimal("NaN"), Decimal("Infinity")])
def test_nonfinite_declared_percentage_never_escapes_product_truth(unsafe):
    product = _bread(promised="wheat", declared=unsafe)
    graded = product_truth.grade(product, _published())
    assert graded.result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION
    assert graded.result.grade is None
    _assert_finite_payload(graded.payload)
    naming = next(row for row in graded.payload["components"] if row["key"] == "naming")
    assert naming["declared_percent"] is None


def test_finite_declared_percentage_presentation_is_unchanged():
    graded = product_truth.grade(_bread(promised="wheat", declared=Decimal("10")), _published())
    assert graded.result.outcome is GradeOutcome.GRADED
    naming = next(row for row in graded.payload["components"] if row["key"] == "naming")
    assert (naming["state"], naming["band"], naming["declared_percent"]) == ("low", "red", 10.0)
    factor = next(row for row in graded.payload["negatives"] if row["key"] == "naming")
    assert factor["quantity"] == {"value": 10.0, "unit": "%", "basis": "of_product"}
    _assert_finite_payload(graded.payload)


def test_named_factor_path_omits_an_unreadable_percentage():
    valid = _bread(promised="wheat", declared=Decimal("10"))
    invalid = _bread(promised="wheat", declared=Decimal("NaN"))
    # Even if a caller accidentally pairs an old trace with new invalid facts,
    # presentation must not compare or serialize the unsafe number.
    payload = presentation.present(invalid, grade_product(valid), _published())
    _assert_finite_payload(payload)
    assert not any(row["key"] == "naming" for row in payload["negatives"])
    naming = next(row for row in payload["components"] if row["key"] == "naming")
    assert naming["declared_percent"] is None


def _bread(*, promised: str | None = None, declared: Decimal | None = None) -> ProductInput:
    return ProductInput(
        name="Bread", ingredients=("refined wheat flour", "water", "salt"),
        energy_kcal=Decimal("250"), total_sugar_g=Decimal("2"),
        saturated_fat_g=Decimal("1"), sodium_g=Decimal("0.2"),
        name_promises=promised,
        declared_percentages={promised: declared} if promised and declared is not None else {},
    )


def _skipped_gates_product() -> ProductInput:
    return ProductInput(
        name="Cocoa Crunch", ingredients=(
            "maltodextrin", "Potassium bromate", "cocoa 10%", "sugar",
        ),
        energy_kcal=Decimal("400"), total_sugar_g=Decimal("24"),
        saturated_fat_g=Decimal("2"), sodium_g=Decimal("NaN"),
        name_promises="cocoa", declared_percentages={"cocoa": Decimal("10")},
    )


def _assert_skipped_components(payload: dict) -> None:
    assert (payload["outcome"], payload["grade"]) == ("not_enough_information", None)
    components = {row["key"]: row for row in payload["components"]}
    assert set(components) == set(presentation.COMPONENT_KEYS)
    for row in components.values():
        assert (row["state"], row["band"]) == ("not_enough_information", "yellow")
        assert (row["rule"], row["finding"], row["source"], row["source_url"], row["sources"]) == (
            None, None, None, None, [],
        )
    assert (components["nutrients"]["high"], components["nutrients"]["exempt"]) == ([], [])
    assert (components["naming"]["ingredient"], components["naming"]["declared_percent"]) == (
        None, None,
    )


def test_early_invalid_numeric_data_does_not_present_unevaluated_gates_as_safe():
    product = _skipped_gates_product()
    # The ingredients are consequential if those gates run, but invalid
    # nutrition makes the engine return before evaluating any of them.
    valid = replace(product, sodium_g=Decimal("0.2"))
    evaluated = grade_product(valid)
    assert evaluated.nova_group == 4
    assert any(entry.rule_id == "grade.step3.black_tier" for entry in evaluated.trace)
    assert any(entry.rule_id == "grade.step4.declared_percentage" for entry in evaluated.trace)

    graded = product_truth.grade(product, _published())
    assert graded.result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION
    assert {entry.step for entry in graded.result.trace} == {0, 5}
    _assert_skipped_components(graded.payload)
    _assert_finite_payload(graded.payload)


def test_late_step5_missing_data_keeps_the_gates_that_actually_ran():
    product = replace(_bread(), has_nutrition_panel=False)
    graded = product_truth.grade(product, _published())
    assert graded.result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION
    assert "nutrition panel" in graded.result.missing
    assert {1, 3, 4, 5}.issubset({entry.step for entry in graded.result.trace})
    assert graded.result.bands
    assert [(row["key"], row["state"], row["band"]) for row in graded.payload["components"]] == [
        ("processing", "nova3", "yellow"),
        ("nutrients", "clear", "green"),
        ("additives", "none", "green"),
        ("naming", "not_promised", "green"),
    ]


def _unknown_basis_product(**changes: object) -> ProductInput:
    return replace(_bread(), basis="unknown", **changes)


def _assert_unknown_nutrients_with_step2_trace(product: ProductInput) -> dict:
    graded = product_truth.grade(product, _published())
    assert graded.result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION
    assert "nutrition basis" in graded.result.missing
    assert graded.result.bands == ()
    assert any(entry.step == 2 for entry in graded.result.trace)
    nutrients = next(row for row in graded.payload["components"] if row["key"] == "nutrients")
    assert (nutrients["state"], nutrients["band"]) == ("not_enough_information", "yellow")
    assert (nutrients["high"], nutrients["exempt"]) == ([], [])
    assert not any(
        row["key"] in {"protein", "fibre"} for row in graded.payload["positives"]
    )
    assert not any(
        row["quantity"] and row["quantity"]["basis"] in {"per_100_g", "per_100_ml"}
        for row in (*graded.payload["positives"], *graded.payload["negatives"])
    )
    return graded.payload


def test_unknown_basis_positive_trace_cannot_certify_nutrient_bands():
    product = _unknown_basis_product(protein_g=Decimal("12"), fibre_g=Decimal("8"))
    payload = _assert_unknown_nutrients_with_step2_trace(product)
    assert any(row["rule_id"] == "grade.step2.positives" for row in payload["trace"])


def test_unknown_basis_pho_trace_keeps_factual_negative_but_not_clear_nutrients():
    product = _unknown_basis_product(
        ingredients=("refined wheat flour", "partially hydrogenated oil", "salt"),
        trans_fat_g=Decimal("1"),
    )
    payload = _assert_unknown_nutrients_with_step2_trace(product)
    assert any(row["rule_id"] == "grade.step2.partially_hydrogenated_oil"
               for row in payload["trace"])
    pho = next(row for row in payload["negatives"] if row["key"] == "trans_fat")
    assert pho["rule"] == "grade.step2.partially_hydrogenated_oil"
    assert pho["quantity"] is None


def test_unknown_basis_trans_fat_denominator_trace_does_not_certify_nutrients():
    product = _unknown_basis_product(trans_fat_g=Decimal("1"))
    payload = _assert_unknown_nutrients_with_step2_trace(product)
    assert any(row["rule_id"] == "grade.step2.trans_fat_denominator_missing"
               for row in payload["trace"])


@pytest.mark.parametrize("basis,expected_basis", [
    ("solid", "per_100_g"), ("drink", "per_100_ml"),
])
def test_known_basis_without_high_bands_still_shows_clear_nutrients(basis, expected_basis):
    product = replace(_bread(), basis=basis, protein_g=Decimal("12"))
    graded = product_truth.grade(product, _published())
    assert graded.result.bands
    nutrients = next(row for row in graded.payload["components"] if row["key"] == "nutrients")
    assert (nutrients["state"], nutrients["band"]) == ("clear", "green")
    protein = next(row for row in graded.payload["positives"] if row["key"] == "protein")
    assert protein["quantity"]["basis"] == expected_basis


def test_culinary_not_graded_presentation_contract_remains_unchanged():
    product = ProductInput(name="Ghee", ingredients=("ghee",), protein_g=Decimal("1"))
    graded = product_truth.grade(product, _published())
    assert graded.result.outcome is GradeOutcome.NOT_GRADED
    assert graded.result.bands == ()
    assert [(row["key"], row["state"]) for row in graded.payload["components"]] == [
        ("processing", "nova2"), ("nutrients", "clear"),
        ("additives", "none"), ("naming", "not_promised"),
    ]
    assert next(row for row in graded.payload["positives"] if row["key"] == "protein")[
        "quantity"
    ]["basis"] == "per_100_g"


def test_f07_publication_block_preserves_evaluated_component_facts():
    product = _bread()
    published = product_truth.grade(product, _published())
    blocked = product_truth.grade(product, _published(unpublished=("grade.step1.refined_grain",)))
    assert (published.result.outcome, blocked.result.outcome) == (
        GradeOutcome.GRADED, GradeOutcome.NOT_ENOUGH_INFORMATION,
    )
    assert blocked.result.grade is None
    assert blocked.payload["components"] == published.payload["components"]
    assert all(row["state"] != "not_enough_information" for row in blocked.payload["components"])


def test_valid_graded_component_payload_retains_its_existing_states():
    graded = product_truth.grade(_bread(), _published())
    assert graded.result.outcome is GradeOutcome.GRADED
    assert [(row["key"], row["state"], row["band"]) for row in graded.payload["components"]] == [
        ("processing", "nova3", "yellow"),
        ("nutrients", "clear", "green"),
        ("additives", "none", "green"),
        ("naming", "not_promised", "green"),
    ]


def test_fired_optional_candidate_blocks_but_published_rule_keeps_existing_grade():
    product = _bread()
    baseline = product_truth.grade(product, _published())
    assert baseline.result.outcome is GradeOutcome.GRADED
    trace = next(row for row in baseline.result.trace if row.rule_id == "grade.step1.refined_grain")
    assert trace.grade_affecting
    blocked = product_truth.grade(product, _published(unpublished=("grade.step1.refined_grain",)))
    assert blocked.result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION
    assert blocked.result.grade is None and blocked.result.ceiling is None
    assert "grade.step1.refined_grain" in blocked.result.missing
    assert baseline.result.grade == grade_product(product).grade


def test_unfired_optional_candidate_does_not_block():
    result = product_truth.grade(_bread(), _published(unpublished=("grade.step3.red_tier",)))
    assert result.result.outcome is GradeOutcome.GRADED
    assert result.result.grade is not None


def test_informational_optional_trace_does_not_become_globally_required():
    product = _bread(promised="wheat")
    result = product_truth.grade(product, _published(unpublished=("grade.step4.percentage_not_declared",)))
    assert any(row.rule_id == "grade.step4.percentage_not_declared" for row in result.result.trace)
    assert result.result.outcome is GradeOutcome.GRADED


def test_declared_percentage_blocks_only_when_its_ceiling_actually_applies():
    rule_id = "grade.step4.declared_percentage"
    high = product_truth.grade(
        _bread(promised="wheat", declared=Decimal("60")),
        _published(unpublished=(rule_id,)),
    )
    low = product_truth.grade(
        _bread(promised="wheat", declared=Decimal("10")),
        _published(unpublished=(rule_id,)),
    )
    assert high.result.outcome is GradeOutcome.GRADED
    assert low.result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION
    assert rule_id in low.result.missing


def test_unpublished_required_still_blocks_and_claim_identity_does_not_move_grade():
    product = _bread()
    required = product_truth.grade(product, _published(unpublished=("grade.step1.nova",)))
    assert required.result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION
    assert "grade.step1.nova" in required.result.missing
    assert product_truth.grade(product, _published()).result.grade == product_truth.grade(
        product, _published(claim_offset=100),
    ).result.grade


# Mutation battery: each mutant is executed in a temporary monkeypatch context.
# The oracle is first proved against the unmodified implementation, then must
# raise AssertionError (or a hard failure) with the deliberately bad behavior.
@pytest.mark.parametrize("name,raw", [
    ("leading-number", "50 mg garbage"),
    ("mg-as-g", "50 mg"),
    ("incompatible-unit", "50 kcal"),
])
def test_mutation_prefix_and_unit_stripping_is_killed(monkeypatch, name, raw):
    def oracle():
        parsed = from_scan._quantity(raw, kind="mass", default_unit="g")
        assert parsed.invalid or parsed.value == Decimal("0.05")

    oracle()

    def prefix_mutant(value, *, kind, default_unit):
        match = re.match(r"\s*(\d+)", str(value))
        return from_scan._ParsedQuantity(value=Decimal(match.group(1)))

    with monkeypatch.context() as patch:
        patch.setattr(from_scan, "_quantity", prefix_mutant)
        with pytest.raises(AssertionError):
            oracle()


def test_mutation_removed_finite_check_is_killed(monkeypatch):
    def oracle():
        assert from_scan._quantity("NaN", kind="mass", default_unit="g").invalid

    oracle()
    with monkeypatch.context() as patch:
        patch.setattr(from_scan, "_quantity", lambda *_args, **_kwargs: from_scan._ParsedQuantity(
            value=Decimal("NaN"),
        ))
        with pytest.raises(AssertionError):
            oracle()


def test_mutation_present_invalid_as_absent_is_killed(monkeypatch):
    def oracle():
        assert product_truth.grade(_adapt("confirmed", "50 mystery-units"), _published()).result.outcome is (
            GradeOutcome.NOT_ENOUGH_INFORMATION
        )

    oracle()
    original = from_scan._read_quantity

    def drop_invalid(*args, **kwargs):
        parsed = original(*args, **kwargs)
        return from_scan._ParsedQuantity() if parsed.invalid else parsed

    with monkeypatch.context() as patch:
        patch.setattr(from_scan, "_read_quantity", drop_invalid)
        with pytest.raises(AssertionError):
            oracle()


def test_mutation_guess_ambiguous_off_energy_is_killed(monkeypatch):
    def oracle():
        product = from_scan.build(barcode="x", name="Cereal", off_half=_off(**{
            "energy-kcal_100g": None, "energy_100g": 4184,
        }))
        assert product.energy_kcal is None

    oracle()
    original = from_scan._read_quantity

    def guess_energy(values, keys, *, kind, default_units):
        if kind == "energy" and values.get("energy_100g") is not None:
            return from_scan._ParsedQuantity(value=Decimal(str(values["energy_100g"])))
        return original(values, keys, kind=kind, default_units=default_units)

    with monkeypatch.context() as patch:
        patch.setattr(from_scan, "_read_quantity", guess_energy)
        with pytest.raises(AssertionError):
            oracle()


def test_mutation_nonfinite_presentation_leak_is_killed(monkeypatch):
    product = ProductInput(name="Cereal", ingredients=("oats",), sodium_g=Decimal("NaN"))

    def oracle():
        json.dumps(product_truth.grade(product, _published()).payload, allow_nan=False)

    oracle()
    with monkeypatch.context() as patch:
        patch.setattr(presentation, "_safe_salt_float", lambda item: float(item.sodium_g * Decimal("2.5")))
        with pytest.raises(ValueError, match="Out of range float"):
            oracle()


def test_mutation_raw_quantity_float_is_killed(monkeypatch):
    product = replace(_bread(), protein_g=Decimal("NaN"))
    evaluated = grade_product(_bread())

    def oracle():
        _assert_finite_payload(presentation.present(product, evaluated, _published()))

    oracle()
    with monkeypatch.context() as patch:
        patch.setattr(presentation, "_quantity", lambda value, unit: {
            "value": float(value), "unit": presentation._unit_symbol(unit),
            "basis": presentation._basis_for_unit(unit),
        } if value is not None else None)
        with pytest.raises(AssertionError):
            oracle()


def test_mutation_positive_factor_nonfinite_quantity_is_killed(monkeypatch):
    product = replace(_bread(), fibre_g=Decimal("Infinity"))
    evaluated = grade_product(_bread())

    def oracle():
        payload = presentation.present(product, evaluated, _published())
        factor = next(row for row in payload["positives"] if row["key"] == "fibre")
        assert factor["quantity"] is None
        _assert_finite_payload(payload)

    oracle()
    with monkeypatch.context() as patch:
        patch.setattr(presentation, "_safe_float", lambda value: float(value) if value is not None else None)
        with pytest.raises(AssertionError):
            oracle()


def test_mutation_raw_named_ingredient_comparison_is_killed(monkeypatch):
    product = _bread(promised="wheat", declared=Decimal("NaN"))
    evaluated = grade_product(_bread(promised="wheat", declared=Decimal("10")))

    def oracle():
        _assert_finite_payload(presentation.present(product, evaluated, _published()))

    oracle()
    original = presentation._finite_decimal
    with monkeypatch.context() as patch:
        patch.setattr(presentation, "_finite_decimal", lambda value: value if isinstance(value, Decimal)
                      else original(value))
        with pytest.raises(InvalidOperation):
            oracle()


@pytest.mark.parametrize("name", ["required-only", "fired-optional-passthrough"])
def test_mutation_unpublished_fired_optional_passthrough_is_killed(monkeypatch, name):
    product = _bread()
    ruleset = _published(unpublished=("grade.step1.refined_grain",))

    def oracle():
        assert product_truth.grade(product, ruleset).result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION

    oracle()
    original = production_rules.enforce_published_required_rules

    def required_only(result, active_ruleset):
        return original(result, active_ruleset) if active_ruleset.unpublished_required else result

    def lost_grade_affecting_flag(item):
        result = grade_product(item)
        return replace(result, trace=tuple(
            replace(entry, grade_affecting=False)
            if entry.rule_id == "grade.step1.refined_grain" else entry
            for entry in result.trace
        ))

    with monkeypatch.context() as patch:
        if name == "required-only":
            patch.setattr(product_truth, "enforce_published_required_rules", required_only)
        else:
            patch.setattr(product_truth, "grade_product", lost_grade_affecting_flag)
        with pytest.raises(AssertionError):
            oracle()


def test_mutation_all_optional_globally_required_is_killed(monkeypatch):
    product = _bread()
    ruleset = _published(unpublished=("grade.step3.red_tier",))

    def oracle():
        assert product_truth.grade(product, ruleset).result.outcome is GradeOutcome.GRADED

    oracle()

    def all_optional(result, active_ruleset):
        return replace(result, outcome=GradeOutcome.NOT_ENOUGH_INFORMATION, grade=None) if (
            active_ruleset.unpublished
        ) else result

    with monkeypatch.context() as patch:
        patch.setattr(product_truth, "enforce_published_required_rules", all_optional)
        with pytest.raises(AssertionError):
            oracle()


def test_mutation_english_effect_parsing_is_killed():
    ruleset = _published(unpublished=("grade.step1.refined_grain",))
    candidate = grade_product(_bread())
    paraphrased = replace(candidate, trace=tuple(
        replace(entry, effect="No ceiling.") if entry.rule_id == "grade.step1.refined_grain" else entry
        for entry in candidate.trace
    ))
    assert production_rules.enforce_published_required_rules(paraphrased, ruleset).outcome is (
        GradeOutcome.NOT_ENOUGH_INFORMATION
    )

    def prose_mutant(result):
        fired = any(
            entry.effect and entry.effect.startswith("Ceiling ") and
            (row := ruleset.for_rule(entry.rule_id)) is not None and not row.published
            for entry in result.trace
        )
        return GradeOutcome.NOT_ENOUGH_INFORMATION if fired else result.outcome

    with pytest.raises(AssertionError):
        assert prose_mutant(paraphrased) is GradeOutcome.NOT_ENOUGH_INFORMATION


def test_mutation_wrong_b2b_reason_is_killed(monkeypatch):
    blocked = product_truth.grade(_bread(), _published(unpublished=("grade.step1.refined_grain",)))

    def oracle():
        assert b2b_truth.project("8901000000001", None, blocked)["reason"] == "evidence_unpublished"

    oracle()
    original = b2b_truth._not_enough

    def wrong_reason(barcode, reason):
        return original(barcode, "label_facts_insufficient" if reason == "evidence_unpublished" else reason)

    with monkeypatch.context() as patch:
        patch.setattr(b2b_truth, "_not_enough", wrong_reason)
        with pytest.raises(AssertionError):
            oracle()


@pytest.mark.parametrize("step", [
    pytest.param(1, id="processing-nova1-fallback"),
    pytest.param(2, id="nutrient-clear-fallback"),
    pytest.param(3, id="additive-none-fallback"),
])
def test_mutation_skipped_gate_safe_fallback_is_killed(monkeypatch, step):
    product = _skipped_gates_product()
    ruleset = _published()

    def oracle():
        _assert_skipped_components(product_truth.grade(product, ruleset).payload)

    oracle()
    original = presentation._gate_was_evaluated
    with monkeypatch.context() as patch:
        patch.setattr(
            presentation, "_gate_was_evaluated",
            lambda result, candidate: candidate == step or original(result, candidate),
        )
        if step == 1:
            # The historical fallback converted nova_group=None into NOVA 1.
            patch.setattr(presentation, "_processing_component", lambda _result: {
                "key": "processing", "band": "green", "state": "nova1",
                "rule": None, "finding": None, "source": None,
                "source_url": None, "sources": [],
            })
        with pytest.raises(AssertionError):
            oracle()


def test_mutation_blanket_unknown_for_every_ungraded_result_is_killed(monkeypatch):
    product = _bread()
    ruleset = _published(unpublished=("grade.step1.refined_grain",))

    def oracle():
        graded = product_truth.grade(product, ruleset)
        assert graded.result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION
        assert all(row["state"] != "not_enough_information" for row in graded.payload["components"])

    oracle()
    original = presentation._gate_was_evaluated
    with monkeypatch.context() as patch:
        patch.setattr(
            presentation, "_gate_was_evaluated",
            lambda result, step: False if result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION
            else original(result, step),
        )
        with pytest.raises(AssertionError):
            oracle()


def test_mutation_any_step2_trace_misread_as_nutrient_evaluation_is_killed(monkeypatch):
    product = _unknown_basis_product(protein_g=Decimal("12"), fibre_g=Decimal("8"))
    ruleset = _published()

    def oracle():
        _assert_unknown_nutrients_with_step2_trace(product)

    oracle()
    original = presentation._gate_was_evaluated
    with monkeypatch.context() as patch:
        patch.setattr(
            presentation, "_gate_was_evaluated",
            lambda result, step: any(entry.step == 2 for entry in result.trace)
            if step == 2 else original(result, step),
        )
        with pytest.raises(AssertionError):
            oracle()
