"""Pure invariants for observed label content identity."""
from app.domains.product.service import (
    canonical_label_facts,
    label_changed_fields,
    label_content_fingerprint,
)

BASE = {
    "product_name": " Oats  ", "brand": "Acme", "ingredients_text": "oats, salt",
    "nutrition_per_100g": {"sugars_g": "1", "energy_kcal": "370"},
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

    assert label_completeness({}) == "identity_only"
    assert label_completeness({"product_name": "Oats"}) == "incomplete_for_grading"
    assert label_completeness({"brand": "Acme", "ingredients_text": "oats", "nutrition_per_100g": {"energy": "370"}}) == "complete_for_grading"

def test_ingredient_order_and_content_changes_are_meaningful():
    reordered = {**BASE, "ingredients_text": "salt, oats"}
    altered = {**BASE, "ingredients_text": "oats, sugar"}
    assert label_content_fingerprint(BASE) != label_content_fingerprint(reordered)
    assert label_content_fingerprint(BASE) != label_content_fingerprint(altered)

def test_structural_diff_has_closed_fields_and_excludes_batch():
    assert label_changed_fields(BASE, {**BASE, "batch_number": "B-2"}) == []
    assert label_changed_fields(BASE, {**BASE, "allergen_text": "contains nuts"}) == ["allergen_text"]
    assert label_changed_fields(BASE, {**BASE, "nutrition_per_100g": {"sugars_g": "2"}}) == ["nutrition"]
    assert set(canonical_label_facts(BASE)) <= {"product_name", "brand", "ingredients_text", "nutrition_per_100g", "allergen_text"}
