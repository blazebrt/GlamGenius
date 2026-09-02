"""Pure invariants for observed label content identity."""
from decimal import Decimal

from app.domains.nutrition.grading import (
    GradeOutcome,
    from_scan,
    grade_product,
    required_grading_data_missing,
)
from app.domains.product.extraction import ExtractedLabel
from app.domains.product.service import (
    canonical_label_facts,
    label_changed_fields,
    label_content_fingerprint,
)

BASE = {
    "product_name": " Oats  ", "brand": "Acme", "ingredients_text": "oats, salt",
    "nutrition_per_100g": {"sugars_g": "1", "energy_kcal": "370"},
    "nutrition_basis": "per_100g",
    "allergen_text": "",
    "batch_number": "B-1", "confidence": 0.3, "device_id": "device-a",
}

def test_canonical_fingerprint_ignores_observation_metadata_and_key_order():
    changed = {**BASE, "batch_number": "B-2", "confidence": 0.9, "device_id": "device-b"}
    changed["nutrition_per_100g"] = {"energy_kcal": "370", "sugars_g": "1"}
    assert label_content_fingerprint(BASE) == label_content_fingerprint(changed)


def test_harmless_whitespace_is_canonicalised_but_facts_are_not_coerced():
    spaced = {**BASE, "product_name": "Oats", "brand": "  Acme  "}
    assert label_content_fingerprint(BASE) == label_content_fingerprint(spaced)
    numeric_text = {**BASE, "nutrition_per_100g": {"sugars_g": "1.0"}}
    assert label_content_fingerprint(BASE) != label_content_fingerprint(numeric_text)


def test_extraction_quality_metadata_does_not_define_content_identity():
    changed = {
        **BASE,
        "uncertain_fields": ["nutrition_per_100g"],
        "photo_quality_notes": "blurred corner",
        "model": "new-model",
        "prompt_version": "scan-label.v2",
        "captured_at": "2026-01-01T00:00:00Z",
        "user_id": "not-content",
    }
    assert label_content_fingerprint(BASE) == label_content_fingerprint(changed)


def test_supported_content_fields_and_closed_diff_vocabulary():
    changes = {
        "product_name": "New oats",
        "brand": "New brand",
        "ingredients_text": "oats, sugar",
        "nutrition_per_100g": {"sugars_g": "2"},
        "serving_size": "40 g",
        "net_quantity": "400 g",
        "fssai_licence": "10012345678901",
        "veg_mark": "green",
        "allergen_text": "contains nuts",
    }
    for field, value in changes.items():
        assert label_content_fingerprint(BASE) != label_content_fingerprint({**BASE, field: value})
    assert label_changed_fields(BASE, {**BASE, **changes}) == [
        "product_name", "brand", "ingredients", "nutrition", "serving_size",
        "net_quantity", "fssai_licence", "veg_mark", "allergen_text",
    ]


def test_completeness_is_closed_and_does_not_invent_missing_facts():
    from app.domains.product.service import label_completeness

    assert label_completeness({}) == "incomplete_for_grading"
    assert label_completeness({"product_name": "Oats"}) == "identity_only"
    assert label_completeness(
        {"product_name": "Oats", "ingredients_text": "oats"}
    ) == "incomplete_for_grading"
    assert label_completeness({
        "product_name": "Oats",
        "ingredients_text": "oats",
        "nutrition_per_100g": {"energy_kcal": "370"},
    }) == "incomplete_for_grading"
    assert label_completeness({
        "product_name": "Oats",
            "ingredients_text": "oats",
            "nutrition_per_100g": {"energy_kcal": "370", "sugars_g": "1"},
            "nutrition_basis": "per_100g",
        }) == "complete_for_grading"


def test_confirmed_label_adapter_maps_declared_values_without_inference():
    product = from_scan.build_confirmed_label(
        barcode="8900000000001",
        facts={
            "product_name": "Seed mix",
            "ingredients_text": "seeds, salt",
            "nutrition_per_100g": {
                "energy_kcal": "410 kcal",
                "protein_g": "18 g",
                "total_fat_g": "22 g",
                "saturated_fat_g": "3 g",
                "trans_fat_g": "0 g",
                "total_sugar_g": "2 g",
                "added_sugar_g": "0 g",
                "fibre_g": "11 g",
                "sodium_g": "0.2 g",
                "salt_g": "0.5 g",
            },
            "net_quantity": "250 g",
            "serving_size": "25 g",
        },
    )

    assert product.name == "Seed mix"
    assert product.ingredients == ("seeds", "salt")
    assert product.energy_kcal == Decimal("410")
    assert product.protein_g == Decimal("18")
    assert product.total_fat_g == Decimal("22")
    assert product.saturated_fat_g == Decimal("3")
    assert product.trans_fat_g == Decimal("0")
    assert product.total_sugar_g == Decimal("2")
    assert product.added_sugar_g == Decimal("0")
    assert product.fibre_g == Decimal("11")
    assert product.sodium_g == Decimal("0.2")
    assert product.salt_g == Decimal("0.5")


def test_extracted_label_uses_a_closed_explicit_nutrition_basis():
    assert ExtractedLabel(nutrition_basis="per_100g").nutrition_basis == "per_100g"
    assert ExtractedLabel(nutrition_basis="per_100ml").nutrition_basis == "per_100ml"
    assert ExtractedLabel().nutrition_basis is None
    import pytest
    with pytest.raises(ValueError):
        ExtractedLabel(nutrition_basis="from_name")


def test_confirmed_basis_is_not_inferred_and_changes_grading_identity():
    facts = {
        "product_name": "Fruit drink",
        "ingredients_text": "water, sugar",
        "nutrition_per_100g": {"energy_kcal": "40", "sugars_g": "10"},
    }
    missing = from_scan.build_confirmed_label(barcode="x", facts=facts)
    assert missing.basis == "unknown"
    assert required_grading_data_missing(missing) == ("nutrition basis",)
    per_100g = {**facts, "nutrition_basis": "per_100g"}
    per_100ml = {**facts, "nutrition_basis": "per_100ml"}
    assert from_scan.build_confirmed_label(barcode="x", facts=per_100g).basis == "solid"
    assert from_scan.build_confirmed_label(barcode="x", facts=per_100ml).basis == "drink"
    from app.domains.product.service import label_changed_fields
    assert label_content_fingerprint(per_100g) != label_content_fingerprint(per_100ml)
    assert "nutrition_basis" in label_changed_fields(per_100g, per_100ml)


def test_completeness_and_grader_share_required_data_semantics():
    from app.domains.product.service import label_completeness

    cases = (
        {
            "product_name": "Energy only",
            "ingredients_text": "wheat flour",
            "nutrition_per_100g": {"energy_kcal": "400"},
        },
        {
            "product_name": "Declared sugar",
            "ingredients_text": "wheat flour, sugar",
            "nutrition_per_100g": {"energy_kcal": "400", "sugars_g": "20"},
        },
    )
    for facts in cases:
        product = from_scan.build_confirmed_label(barcode="test", facts=facts)
        missing = required_grading_data_missing(product)
        result = grade_product(product)
        assert (label_completeness(facts) == "complete_for_grading") is (not missing)
        assert (result.outcome is GradeOutcome.NOT_ENOUGH_INFORMATION) is bool(missing)

def test_ingredient_order_and_content_changes_are_meaningful():
    reordered = {**BASE, "ingredients_text": "salt, oats"}
    altered = {**BASE, "ingredients_text": "oats, sugar"}
    assert label_content_fingerprint(BASE) != label_content_fingerprint(reordered)
    assert label_content_fingerprint(BASE) != label_content_fingerprint(altered)

def test_structural_diff_has_closed_fields_and_excludes_batch():
    assert label_changed_fields(BASE, {**BASE, "batch_number": "B-2"}) == []
    assert label_changed_fields(BASE, {**BASE, "allergen_text": "contains nuts"}) == ["allergen_text"]
    assert label_changed_fields(BASE, {**BASE, "nutrition_per_100g": {"sugars_g": "2"}}) == ["nutrition"]
    assert set(canonical_label_facts(BASE)) <= {"product_name", "brand", "ingredients_text", "nutrition_per_100g", "nutrition_basis", "allergen_text"}
