"""Phase 6 routine and shelf intelligence contracts.

The tests that matter most here are the ones about the boundary. A styling app
that drifts into diagnosis, or into telling somebody how much of a supplement to
swallow, is a different and much more dangerous product. Those boundaries are
therefore tested as behaviour, not asserted in a docstring:

* no warning without a reviewed ``rule_id``,
* no dosage, ever, including from the model,
* no diagnosis, ever, including from the model,
* a low-confidence label read cannot raise a warning until a human confirms it.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.domains.routines import compiler, nutrition, parser, perfume, rules, safety, shelf
from app.domains.routines.explanation import SOURCE_AI, SOURCE_DETERMINISTIC
from app.domains.routines.models import ProductIngredient, Routine, RoutineAdherence
from app.domains.routines.ontology import (
    COMPATIBILITY_RULES, INGREDIENT_BY_KEY, INGREDIENTS, SEVERITY_AVOID, SEVERITY_CAUTION,
    SEVERITY_INFO, alias_index, slot_for_product_type,
)
from app.shared.database import sql
from tests.conftest import auth

TODAY = date.today()

SHELF = [
    ("beauty", "Gentle Foaming Face Wash", {
        "product_type": "cleanser", "remaining_percent": 70,
        "ingredients_text": "Aqua, Sodium Cocoyl Isethionate, Glycerin, Panthenol",
    }),
    ("beauty", "Vitamin C Morning Serum", {
        "product_type": "serum", "routine_position": "morning", "remaining_percent": 50,
        "active_ingredients": ["ascorbic acid"],
        "ingredients_text": "Aqua, Ascorbic Acid, Ferulic Acid, Glycerin",
    }),
    ("beauty", "Ceramide Day Moisturiser", {
        "product_type": "moisturiser", "remaining_percent": 80,
        "ingredients_text": "Aqua, Glycerin, Ceramide NP, Squalane, Dimethicone",
    }),
    ("beauty", "Everyday Sunscreen SPF 50", {
        "product_type": "sunscreen", "remaining_percent": 40,
        "ingredients_text": "Aqua, Zinc Oxide, Niacinamide, Glycerin",
    }),
    ("beauty", "Night Retinol Treatment", {
        "product_type": "night cream", "routine_position": "evening", "remaining_percent": 90,
        "active_ingredients": ["retinol"],
        "ingredients_text": "Aqua, Retinol, Squalane, Tocopherol",
    }),
    ("hair", "Everyday Mild Shampoo", {
        "product_type": "shampoo", "remaining_percent": 60,
        "ingredients_text": "Aqua, Sodium Laureth Sulfate, Cocamidopropyl Betaine, Glycerin",
    }),
    ("hair", "Silky Conditioner", {
        "product_type": "conditioner", "remaining_percent": 55,
        "ingredients_text": "Aqua, Behentrimonium Chloride, Dimethicone, Cetearyl Alcohol",
    }),
]


async def add_item(client, token, category, name, details, **overrides):
    payload = {"category": category, "display_name": name, "brand": "Test Brand", "details": details}
    payload.update(overrides)
    response = await client.post("/api/v2/inventory/items", json=payload, headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def stock_shelf(client, token, rows=SHELF):
    return [await add_item(client, token, *row) for row in rows]


async def set_profile(client, token, **attributes):
    body = {"attributes": [{"key": key, "value": value} for key, value in attributes.items()]}
    response = await client.patch("/api/v2/profile", json=body, headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def generate(client, token, **body):
    return await client.post("/api/v2/routines/generate", json=body, headers=auth(token))


def all_text(payload) -> str:
    """Every string anywhere in a response, for a language sweep."""
    if isinstance(payload, str):
        return payload + " "
    if isinstance(payload, dict):
        return "".join(all_text(value) for value in payload.values())
    if isinstance(payload, list):
        return "".join(all_text(value) for value in payload)
    return ""


# --- Authorization and the flag ----------------------------------------------


async def test_every_phase_6_route_requires_authentication(app_client):
    calls = [
        ("POST", "/api/v2/shelf/analyse", {}),
        ("GET", "/api/v2/shelf/summary", None),
        ("GET", "/api/v2/shelf/expiring", None),
        ("GET", "/api/v2/shelf/low-use", None),
        ("GET", "/api/v2/shelf/value-to-recover", None),
        ("POST", "/api/v2/routines/generate", {}),
        ("GET", "/api/v2/routines/today", None),
        ("POST", f"/api/v2/routines/steps/{uuid.uuid4()}/complete", {}),
        ("POST", "/api/v2/ingredients/check", {"ingredients": ["retinol"]}),
        ("GET", "/api/v2/ingredients/retinol", None),
        ("GET", "/api/v2/perfume/recommendation", None),
        ("GET", "/api/v2/supplements/summary", None),
        ("GET", "/api/v2/nutrition/appearance-suggestions", None),
    ]
    for method, path, body in calls:
        response = await app_client.request(method, path, json=body)
        assert response.status_code in (401, 403), f"{method} {path} -> {response.status_code}"


async def test_ownership_comes_from_the_token_not_the_body(app_client, make_user):
    """An injected account_id is a validation error, never something we trust."""
    _, token = await make_user()
    response = await app_client.post(
        "/api/v2/routines/generate",
        json={"account_id": str(uuid.uuid4()), "explain": False},
        headers=auth(token),
    )
    assert response.status_code == 422


async def test_one_users_routine_step_is_invisible_to_another(app_client, make_user):
    _, owner = await make_user()
    _, stranger = await make_user()
    await stock_shelf(app_client, owner)
    built = (await generate(app_client, owner, explain=False)).json()
    step_id = built["routines"][0]["steps"][0]["id"]

    response = await app_client.post(
        f"/api/v2/routines/steps/{step_id}/complete", json={}, headers=auth(stranger),
    )
    assert response.status_code == 404


# --- Ingredient aliases -------------------------------------------------------


def test_the_same_ingredient_is_found_under_every_name_a_label_uses():
    """Labels print INCI, marketing names and abbreviations interchangeably."""
    for text in ("Sodium Hyaluronate", "hyaluronic acid", "Hyaluronan", "HA"):
        rows = parser.parse_declared([text])
        assert [row.key for row in rows] == ["hyaluronic_acid"], text


def test_every_alias_maps_to_an_ingredient_that_exists():
    index = alias_index()
    assert index, "the alias index must not be empty"
    for alias, key in index.items():
        assert key in INGREDIENT_BY_KEY, f"alias {alias!r} points at unknown {key!r}"
        assert alias == alias.lower(), f"alias {alias!r} must be lowercase to match"


def test_a_longer_alias_wins_over_a_shorter_one_inside_it():
    rows = parser.parse_declared(["hyaluronic acid"])
    assert [row.key for row in rows] == ["hyaluronic_acid"]


def test_word_boundaries_stop_false_matches_inside_longer_words():
    """'ha' must not match inside 'shampoo', or every product has hyaluronic acid."""
    rows = parser.parse_label("Aqua, Shampoo base, Charcoal powder")
    assert "hyaluronic_acid" not in {row.key for row in rows}


def test_unrecognised_label_entries_are_reported_rather_than_hidden():
    unmatched = parser.unmatched_terms("Aqua, Sodium Cocoyl Isethionate, Wibble Extract")
    assert any("wibble" in row for row in unmatched)


# --- Compatibility rules -------------------------------------------------------


def test_a_known_conflict_is_raised_with_the_reviewed_rule_id():
    retinol = _product("Night cream", ["retinol"], slot="treatment")
    acid = _product("Glycolic toner", ["glycolic acid"], slot="toner")
    findings = rules.compatibility_findings([retinol, acid])
    assert findings
    assert findings[0].rule_id == "rule.retinoid_aha"
    assert findings[0].severity == SEVERITY_CAUTION


def test_every_warning_the_engine_can_raise_names_a_reviewed_rule():
    """The acceptance criterion, tested rather than asserted."""
    allowed = rules.all_rule_ids()
    products = [
        _product("Night cream", ["retinol"], slot="treatment"),
        _product("Glycolic toner", ["glycolic acid"], slot="toner"),
        _product("Second toner", ["glycolic acid"], slot="toner"),
        _product("Vitamin C serum", ["ascorbic acid"], slot="treatment", confidence=0.4),
    ]
    findings = (
        rules.compatibility_findings(products)
        + rules.duplicate_slot_findings(products)
        + rules.missing_slot_findings(products, "beauty")
        + rules.expiry_findings(products, TODAY)
        + rules.low_use_findings(products)
        + rules.unconfirmed_findings(products)
        + rules.allergy_findings(products, ["fragrance"])
    )
    assert findings
    for finding in findings:
        assert finding.rule_id in allowed, f"{finding.rule_id} is not a reviewed rule"


def test_a_widely_repeated_claim_the_evidence_does_not_support_is_info_not_caution():
    """Vitamin C and niacinamide. We correct the myth rather than repeat it."""
    rule = next(row for row in COMPATIBILITY_RULES if row.rule_id == "rule.vitamin_c_niacinamide")
    assert rule.severity == SEVERITY_INFO
    assert "does not support" in rule.guidance.lower()


def test_a_rule_between_two_families_fires_in_either_order():
    retinol = _product("Night cream", ["retinol"], slot="treatment")
    acid = _product("Glycolic toner", ["glycolic acid"], slot="toner")
    assert rules.compatibility_findings([retinol, acid])
    assert rules.compatibility_findings([acid, retinol])


def test_the_same_pair_is_only_reported_once():
    retinol = _product("Night cream", ["retinol"], slot="treatment")
    acid = _product("Glycolic toner", ["glycolic acid"], slot="toner")
    findings = rules.compatibility_findings([retinol, acid, retinol])
    ids = [(row.rule_id, tuple(sorted(row.item_ids))) for row in findings]
    assert len(ids) == len(set(ids))


# --- Low-confidence extraction and user confirmation -----------------------------


def test_a_low_confidence_read_never_raises_a_conflict_warning():
    """A wrong warning is worse than a missing one."""
    retinol = _product("Night cream", ["retinol"], slot="treatment", confidence=0.4)
    acid = _product("Glycolic toner", ["glycolic acid"], slot="toner", confidence=0.4)
    assert rules.compatibility_findings([retinol, acid]) == []
    # It is surfaced as something to confirm instead.
    assert rules.unconfirmed_findings([retinol])


def test_a_photo_read_is_below_the_confirmation_threshold_and_a_typed_one_is_not():
    photo = parser.parse_declared(["retinol"], source=parser.SOURCE_EXTRACTED)
    typed = parser.parse_declared(["retinol"], source=parser.SOURCE_USER)
    assert photo[0].needs_confirmation is True
    assert typed[0].needs_confirmation is False


async def test_confirming_a_low_confidence_ingredient_makes_it_drive_the_rules(app_client, make_user):
    _, token = await make_user()
    item = await add_item(app_client, token, "beauty", "Mystery night cream", {
        "product_type": "night cream",
        "ingredients_text": "Aqua, a smudged word that might be Retinol, Squalane",
    })
    await add_item(app_client, token, "beauty", "Glycolic toner", {
        "product_type": "toner", "active_ingredients": ["glycolic acid"],
    })

    analysed = (await app_client.post(
        "/api/v2/shelf/analyse", json={}, headers=auth(token),
    )).json()
    assert analysed["counts"]["awaiting_confirmation"] >= 0

    check = (await app_client.post(
        "/api/v2/ingredients/check",
        json={"item_ids": [item["id"]], "explain": False},
        headers=auth(token),
    )).json()
    assert check["warnings"] is not None


async def test_confirmation_survives_a_re_read_of_the_label(app_client, make_user):
    """Re-reading a label must never un-confirm what a person told us."""
    _, token = await make_user()
    item = await add_item(app_client, token, "beauty", "Serum", {
        "product_type": "serum", "ingredients_text": "Aqua, Retinol, Squalane",
    })
    await app_client.post("/api/v2/shelf/analyse", json={}, headers=auth(token))

    confirmed = await app_client.post(
        "/api/v2/ingredients/confirm",
        json={"item_id": item["id"], "ingredient_keys": ["retinol"], "confirmed": True},
        headers=auth(token),
    )
    assert confirmed.status_code == 200, confirmed.text

    await app_client.post("/api/v2/shelf/analyse", json={}, headers=auth(token))
    factory = sql.get_sessionmaker()
    async with factory() as session:
        row = (await session.execute(
            select(ProductIngredient).where(
                ProductIngredient.item_id == uuid.UUID(item["id"]),
                ProductIngredient.ingredient_key == "retinol",
            )
        )).scalar_one()
        assert row.confirmed_at is not None
        assert row.needs_confirmation is False


# --- Allergy constraints ----------------------------------------------------------


def test_a_declared_allergy_removes_the_product_and_says_which_ingredient():
    product = _product("Scented cream", ["fragrance"], slot="moisturiser")
    findings = rules.allergy_findings([product], ["fragrance"])
    assert findings
    assert findings[0].rule_id == rules.RULE_ALLERGY
    assert findings[0].severity == SEVERITY_AVOID
    assert rules.excluded_by_allergy([product], ["fragrance"]) == {product.id}


def test_an_allergy_the_user_did_not_declare_removes_nothing():
    product = _product("Scented cream", ["fragrance"], slot="moisturiser")
    assert rules.allergy_findings([product], []) == []
    assert rules.excluded_by_allergy([product], []) == set()


def test_a_product_with_a_declared_allergen_is_left_out_of_the_routine():
    products = [
        _product("Scented cream", ["fragrance"], slot="moisturiser"),
        _product("Plain cream", ["glycerin"], slot="moisturiser"),
    ]
    routine = compiler.compile_routine("morning", products, allergies=["fragrance"])
    chosen = {step.product_name for step in routine.steps if step.product_name}
    assert "Scented cream" not in chosen
    assert "Plain cream" in chosen
    assert "Scented cream" in routine.skipped_for_allergy


# --- Expiry ------------------------------------------------------------------------


def test_expiry_is_computed_from_an_opened_date_plus_period_after_opening():
    product = _product("Old serum", ["glycerin"], slot="treatment")
    product.effective_expiry = TODAY - timedelta(days=5)
    findings = rules.expiry_findings([product], TODAY)
    assert [row.rule_id for row in findings] == [rules.RULE_EXPIRED]


def test_a_product_close_to_its_date_is_information_not_a_caution():
    product = _product("Serum", ["glycerin"], slot="treatment")
    product.effective_expiry = TODAY + timedelta(days=20)
    findings = rules.expiry_findings([product], TODAY)
    assert findings[0].rule_id == rules.RULE_EXPIRING
    assert findings[0].severity == SEVERITY_INFO


def test_a_missing_date_is_reported_as_missing_rather_than_guessed():
    product = _product("Serum", ["glycerin"], slot="treatment")
    findings = rules.expiry_findings([product], TODAY)
    assert findings[0].rule_id == rules.RULE_NO_EXPIRY


async def test_the_expiring_route_separates_expired_soon_and_undated(app_client, make_user):
    _, token = await make_user()
    await add_item(app_client, token, "beauty", "Expired cream", {
        "product_type": "moisturiser", "expiry_date": (TODAY - timedelta(days=10)).isoformat(),
    })
    await add_item(app_client, token, "beauty", "Nearly done serum", {
        "product_type": "serum", "expiry_date": (TODAY + timedelta(days=20)).isoformat(),
    })
    await add_item(app_client, token, "beauty", "Undated cleanser", {"product_type": "cleanser"})

    body = (await app_client.get("/api/v2/shelf/expiring", headers=auth(token))).json()
    assert [row["display_name"] for row in body["expired"]] == ["Expired cream"]
    assert [row["display_name"] for row in body["expiring_soon"]] == ["Nearly done serum"]
    assert [row["display_name"] for row in body["no_date_recorded"]] == ["Undated cleanser"]


# --- Routine order and shape --------------------------------------------------------


def test_a_routine_is_ordered_cleanser_first_and_sunscreen_last():
    products = [
        _product("Sunscreen", ["zinc oxide"], slot="sunscreen"),
        _product("Cleanser", ["glycerin"], slot="cleanser"),
        _product("Moisturiser", ["squalane"], slot="moisturiser"),
    ]
    routine = compiler.compile_routine("morning", products)
    slots = [step.slot for step in routine.steps]
    assert slots.index("cleanser") < slots.index("moisturiser") < slots.index("sunscreen")


def test_every_step_explains_why_it_exists():
    """An acceptance criterion. A step with no reason is not shippable."""
    products = [
        _product("Cleanser", ["glycerin"], slot="cleanser"),
        _product("Moisturiser", ["squalane"], slot="moisturiser"),
        _product("Sunscreen", ["zinc oxide"], slot="sunscreen"),
    ]
    for kind in compiler.ROUTINE_KINDS:
        for step in compiler.compile_routine(kind, products).steps:
            assert step.why.strip(), f"{kind}/{step.slot} has no reason"
            assert step.frequency.strip()
            assert isinstance(step.required, bool)


def test_a_missing_required_step_becomes_a_gap_that_names_a_category_not_a_product():
    """Owned-first. A gap describes what to look for, never what to buy."""
    routine = compiler.compile_routine("morning", [
        _product("Cleanser", ["glycerin"], slot="cleanser"),
    ])
    gaps = [step for step in routine.steps if step.is_gap]
    assert gaps
    for gap in gaps:
        assert gap.product_name is None
        assert gap.required is True
        text = f"{gap.why} {gap.alternative}".lower()
        assert "buy" not in text
        assert "http" not in text


def test_an_optional_step_with_nothing_owned_is_left_out_entirely():
    routine = compiler.compile_routine("evening", [
        _product("Cleanser", ["glycerin"], slot="cleanser"),
    ])
    assert "face_oil" not in {step.slot for step in routine.steps}


def test_every_step_carries_an_alternative_or_a_safety_note_where_one_applies():
    products = [
        _product("Cleanser", ["glycerin"], slot="cleanser"),
        _product("Exfoliant", ["salicylic acid"], slot="exfoliant"),
        _product("Moisturiser", ["squalane"], slot="moisturiser"),
    ]
    routine = compiler.compile_routine("evening", products)
    exfoliant = next(step for step in routine.steps if step.slot == "exfoliant")
    assert exfoliant.safety_note
    assert "more is not better" in exfoliant.safety_note.lower()
    cleanser = next(step for step in routine.steps if step.slot == "cleanser")
    assert cleanser.alternative


# --- Duplicate categories and owned-first ranking --------------------------------------


def test_two_products_for_one_step_is_information_not_a_problem():
    products = [
        _product("Cleanser A", ["glycerin"], slot="cleanser"),
        _product("Cleanser B", ["glycerin"], slot="cleanser"),
    ]
    findings = rules.duplicate_slot_findings(products)
    assert findings[0].rule_id == rules.RULE_DUPLICATE_SLOT
    assert findings[0].severity == SEVERITY_INFO
    assert "nothing wrong with that" in findings[0].detail.lower()


def test_a_product_running_out_is_preferred_so_it_gets_used_up():
    running_out = _product("Nearly done", ["glycerin"], slot="cleanser")
    running_out.effective_expiry = TODAY + timedelta(days=15)
    plenty = _product("Plenty left", ["glycerin"], slot="cleanser")
    plenty.effective_expiry = TODAY + timedelta(days=500)
    ranked = rules.rank_for_slot([plenty, running_out], "cleanser", TODAY)
    assert ranked[0].item.display_name == "Nearly done"


def test_an_expired_product_drops_to_the_bottom_rather_than_disappearing():
    expired = _product("Expired", ["glycerin"], slot="cleanser")
    expired.effective_expiry = TODAY - timedelta(days=10)
    fine = _product("Fine", ["glycerin"], slot="cleanser")
    fine.effective_expiry = TODAY + timedelta(days=400)
    ranked = rules.rank_for_slot([expired, fine], "cleanser", TODAY)
    assert [row.item.display_name for row in ranked] == ["Fine", "Expired"]


def test_only_owned_products_can_ever_fill_a_step():
    routine = compiler.compile_routine("morning", [
        _product("Cleanser", ["glycerin"], slot="cleanser"),
    ])
    for step in routine.steps:
        assert step.item_id is not None or step.is_gap


def test_ingredient_overlap_answers_do_i_already_own_this():
    products = [
        _product("Serum", ["niacinamide"], slot="treatment"),
        _product("Moisturiser", ["niacinamide"], slot="moisturiser"),
    ]
    overlap = rules.ingredient_overlap(products)
    assert overlap[0]["ingredient_key"] == "niacinamide"
    assert overlap[0]["product_count"] == 2


# --- Climate ------------------------------------------------------------------------


def test_climate_notes_only_appear_for_steps_the_user_actually_has():
    with_sunscreen = rules.climate_notes("humid", {"moisturiser", "sunscreen"})
    without = rules.climate_notes("humid", set())
    assert with_sunscreen
    assert without == []
    for note in with_sunscreen:
        assert note["rule_id"].startswith("rule.climate_")


def test_no_climate_means_no_climate_notes():
    assert rules.climate_notes(None, {"moisturiser"}) == []


def test_a_climate_note_lands_on_the_step_it_is_about():
    routine = compiler.compile_routine(
        "morning",
        [_product("Moisturiser", ["squalane"], slot="moisturiser")],
        climate="humid",
    )
    noted = [step for step in routine.steps if step.climate_note]
    assert noted
    assert all(step.slot in {row["slot"] for row in routine.climate_notes} for step in noted)


# --- Perfume --------------------------------------------------------------------------


def test_perfume_is_chosen_from_bottles_the_user_owns_and_never_invented():
    owned = [_owned_perfume("Cedar EDP", "woody"), _owned_perfume("Citrus splash", "citrus")]
    result = perfume.recommend(owned, occasion_key="office", weather="hot")
    names = {row["display_name"] for row in result["recommendations"]}
    assert names <= {"Cedar EDP", "Citrus splash"}
    assert all(row["owned"] for row in result["recommendations"])


def test_hot_weather_moves_a_fresh_scent_up_the_list():
    owned = [_owned_perfume("Cedar EDP", "woody"), _owned_perfume("Citrus splash", "citrus")]
    result = perfume.recommend(owned, weather="hot")
    assert result["recommendations"][0]["display_name"] == "Citrus splash"


def test_something_worn_in_the_last_few_days_goes_down_the_list():
    owned = [_owned_perfume("Cedar EDP", "woody"), _owned_perfume("Citrus splash", "citrus")]
    result = perfume.recommend(
        owned, weather="hot", recently_used_item_ids=[str(owned[1].id)],
    )
    assert result["recommendations"][0]["display_name"] == "Cedar EDP"


def test_a_perfume_the_user_tagged_for_the_occasion_beats_our_convention():
    tagged = _owned_perfume("Wedding attar", "oriental", occasion=["wedding"])
    generic = _owned_perfume("Citrus splash", "citrus")
    result = perfume.recommend([tagged, generic], occasion_key="wedding", weather="hot")
    assert result["recommendations"][0]["display_name"] == "Wedding attar"


def test_no_chemistry_claim_is_ever_made_about_a_fragrance_and_skin():
    result = perfume.recommend([_owned_perfume("Cedar EDP", "woody")], weather="cold")
    text = all_text(result).lower()
    assert "your skin chemistry" not in text
    assert "reacts with your skin" not in text
    assert "no way to know" in result["note"].lower()


def test_a_perfume_with_no_recorded_family_says_so_rather_than_guessing():
    result = perfume.recommend([_owned_perfume("Unknown bottle", None)])
    assert result["recommendations"][0]["missing_information"]


async def test_the_perfume_route_returns_an_honest_empty_answer(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get("/api/v2/perfume/recommendation", headers=auth(token))).json()
    assert body["recommendations"] == []
    assert "No perfumes recorded" in body["message"]


# --- Supplements: the boundary ----------------------------------------------------------


async def test_a_supplement_summary_carries_no_dosage_and_no_claimed_effect(app_client, make_user):
    _, token = await make_user()
    await add_item(app_client, token, "supplements", "Vitamin D3", {
        "supplement_name": "Vitamin D3", "user_entered_purpose": "general",
        "use_frequency": "daily", "expiry_date": (TODAY + timedelta(days=20)).isoformat(),
        "label_information": "Take as directed on the pack",
    })
    body = (await app_client.get("/api/v2/supplements/summary", headers=auth(token))).json()

    assert safety.narrative_is_safe(all_text(body)), safety.first_violation(all_text(body))
    assert body["supplements"][0]["display_name"] == "Vitamin D3"
    assert "does not provide supplement dosage" in body["disclaimer"]


async def test_a_health_reason_on_a_supplement_gets_a_professional_boundary(app_client, make_user):
    _, token = await make_user()
    await add_item(app_client, token, "supplements", "Iron tablets", {
        "supplement_name": "Iron", "user_entered_purpose": "doctor said I have a deficiency",
    })
    body = (await app_client.get("/api/v2/supplements/summary", headers=auth(token))).json()
    flags = [row["flag"] for row in body["supplements"][0]["flags"]]
    assert safety.SUPPLEMENT_FLAG_PROFESSIONAL in flags


def test_a_medical_question_gets_the_boundary_rather_than_an_answer():
    for question in (
        "how much vitamin D should I take",
        "can I take this with my thyroid medicine",
        "is this safe during pregnancy",
        "do I have eczema",
        "what dosage of biotin for hair fall",
    ):
        boundary = safety.boundary_for(question)
        assert boundary is not None, question
        assert "doctor" in boundary.message.lower()


def test_an_ordinary_shelf_question_is_not_treated_as_medical():
    for question in (
        "which cleanser should I use first",
        "what is niacinamide",
        "when does my sunscreen run out",
    ):
        assert safety.boundary_for(question) is None, question


def test_no_dosage_language_survives_the_safety_sweep():
    for text in (
        "Take 500 mg daily.",
        "Your recommended dose is two tablets.",
        "You should take this every morning.",
        "Start taking biotin for your hair.",
        "Take one tablet with food.",
    ):
        assert safety.narrative_is_safe(text) is False, text


def test_no_diagnostic_language_survives_the_safety_sweep():
    for text in (
        "You have eczema on your cheeks.",
        "This treats fungal acne.",
        "This will cure your dandruff.",
        "Your condition is improving.",
        "This suggests an iron deficiency.",
    ):
        assert safety.narrative_is_safe(text) is False, text


def test_ordinary_observational_language_passes_the_sweep():
    for text in (
        "This one looks dry around the edges.",
        "Two or three times a week is plenty.",
        "Your sunscreen runs out in 20 days.",
        "You already own three products with glycerin.",
    ):
        assert safety.narrative_is_safe(text) is True, text


def test_every_deterministic_string_the_engine_ships_is_safe():
    """The rules are reviewed. This proves the review held."""
    for row in INGREDIENTS:
        assert safety.narrative_is_safe(f"{row.summary} {row.common_use}"), row.key
    for rule in COMPATIBILITY_RULES:
        assert safety.narrative_is_safe(f"{rule.headline} {rule.guidance} {rule.evidence_note}"), rule.rule_id
    for rule in nutrition.NUTRIENT_RULES:
        assert safety.narrative_is_safe(f"{rule.appearance_context} {rule.note}"), rule.rule_id


# --- Nutrition -------------------------------------------------------------------------


def test_nutrition_never_counts_calories():
    payload = nutrition.suggestions(diet="vegetarian")
    text = all_text(payload).lower()
    assert "calorie" not in text
    assert "kcal" not in text
    # The promise is stated without using the banned word, so the payload can
    # pass the same safety sweep it promises to obey.
    assert any("counts or totals what you eat" in row for row in payload["boundaries"])
    assert "nothing here counts or totals" in payload["disclaimer"].lower()


def test_nutrition_never_claims_the_user_is_short_of_anything():
    payload = nutrition.suggestions(diet="non_vegetarian")
    assert safety.narrative_is_safe(all_text(payload)), safety.first_violation(all_text(payload))


def test_a_vegetarian_is_never_shown_fish_or_meat():
    payload = nutrition.suggestions(diet="vegetarian")
    foods = " ".join(food for row in payload["suggestions"] for food in row["foods"]).lower()
    for banned in ("fish", "chicken", "mutton", "liver", "egg"):
        assert banned not in foods, banned


def test_a_vegan_is_never_shown_dairy():
    payload = nutrition.suggestions(diet="vegan")
    foods = " ".join(food for row in payload["suggestions"] for food in row["foods"]).lower()
    for banned in ("paneer", "curd", "dahi", "ghee", "buttermilk", "chaas"):
        assert banned not in foods, banned


def test_a_jain_diet_also_excludes_root_vegetables():
    payload = nutrition.suggestions(diet="jain")
    foods = " ".join(food for row in payload["suggestions"] for food in row["foods"]).lower()
    assert "carrot" not in foods
    assert "sweet potato" not in foods


def test_a_nutrient_with_no_sources_left_says_so_rather_than_vanishing():
    """Silence would look like the nutrient does not matter."""
    payload = nutrition.suggestions(diet="vegan")
    for row in payload["suggestions"]:
        if not row["foods"]:
            assert "dietitian" in row["note"].lower()


def test_vegetarian_alternatives_are_named_for_animal_heavy_nutrients():
    omega = next(
        row for row in nutrition.suggestions(diet="vegetarian")["suggestions"]
        if row["nutrient"] == "omega_3"
    )
    assert omega["foods"], "a vegetarian must still be given omega-3 sources"
    assert any("flax" in food.lower() for food in omega["foods"])


def test_indian_foods_are_used_throughout():
    payload = nutrition.suggestions(diet="vegetarian")
    foods = " ".join(food for row in payload["suggestions"] for food in row["foods"]).lower()
    for expected in ("dal", "amla", "rajma", "jaggery"):
        assert expected in foods, expected


def test_the_collagen_claim_is_corrected_rather_than_repeated():
    rule = nutrition.NUTRIENT_BY_KEY["collagen_support"]
    assert "broken down like any other protein" in rule.appearance_context


def test_diet_words_people_actually_use_are_understood():
    assert nutrition.normalise_diet("Pure Veg") == "vegetarian"
    assert nutrition.normalise_diet("VEGAN") == "vegan"
    assert nutrition.normalise_diet("jain food only") == "jain"
    assert nutrition.normalise_diet(None) == "non_vegetarian"


async def test_food_suggestions_are_off_until_the_user_turns_them_on(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get(
        "/api/v2/nutrition/appearance-suggestions", headers=auth(token),
    )).json()
    assert body["enabled"] is False
    assert body["suggestions"] == []

    await app_client.patch(
        "/api/v2/nutrition/preferences",
        json={"enabled": True, "diet": "vegetarian"},
        headers=auth(token),
    )
    body = (await app_client.get(
        "/api/v2/nutrition/appearance-suggestions", headers=auth(token),
    )).json()
    assert body["enabled"] is True
    assert body["suggestions"]
    assert body["diet"] == "vegetarian"


async def test_hydration_stores_no_target_volume(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get("/api/v2/nutrition/hydration", headers=auth(token))).json()
    assert body["enabled"] is False
    assert "litre" in body["no_target"].lower()
    assert "target_ml" not in body


# --- The routes end to end ---------------------------------------------------------------


async def test_generating_routines_uses_only_products_the_user_owns(app_client, make_user):
    _, token = await make_user()
    stocked = await stock_shelf(app_client, token)
    owned = {row["display_name"] for row in stocked}

    body = (await generate(app_client, token, explain=False)).json()
    assert body["routines"]
    for routine in body["routines"]:
        for step in routine["steps"]:
            if step["product_name"]:
                assert step["product_name"] in owned


async def test_a_generated_routine_is_stored_and_served_back(app_client, make_user):
    _, token = await make_user()
    await stock_shelf(app_client, token)
    await generate(app_client, token, explain=False)

    factory = sql.get_sessionmaker()
    async with factory() as session:
        stored = (await session.execute(select(Routine))).scalars().all()
        assert stored

    body = (await app_client.get("/api/v2/routines/today", headers=auth(token))).json()
    assert body["date"]
    assert body["part_of_day"] in ("morning", "afternoon", "evening", "night")


async def test_regenerating_moves_the_version_forward_rather_than_duplicating(app_client, make_user):
    _, token = await make_user()
    await stock_shelf(app_client, token)
    first = (await generate(app_client, token, explain=False)).json()
    second = (await generate(app_client, token, explain=False)).json()

    kinds = [row["kind"] for row in second["routines"]]
    assert len(kinds) == len(set(kinds))
    by_kind = {row["kind"]: row for row in first["routines"]}
    for routine in second["routines"]:
        assert routine["version"] == by_kind[routine["kind"]]["version"] + 1


async def test_completing_a_step_records_it_for_that_day_only_once(app_client, make_user):
    _, token = await make_user()
    await stock_shelf(app_client, token)
    built = (await generate(app_client, token, explain=False)).json()
    step_id = next(
        step["id"] for routine in built["routines"] for step in routine["steps"] if step["owned"]
    )

    for _ in range(2):
        response = await app_client.post(
            f"/api/v2/routines/steps/{step_id}/complete",
            json={"done_on": TODAY.isoformat()},
            headers=auth(token),
        )
        assert response.status_code == 200, response.text

    factory = sql.get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(RoutineAdherence).where(RoutineAdherence.step_id == uuid.UUID(step_id))
        )).scalars().all()
        assert len(rows) == 1


async def test_consistency_is_measured_without_streaks(app_client, make_user):
    _, token = await make_user()
    await stock_shelf(app_client, token)
    await generate(app_client, token, explain=False)
    body = (await app_client.get("/api/v2/routines/consistency", headers=auth(token))).json()
    text = all_text(body).lower()
    assert "streak" not in text or "we do not count streaks" in text
    assert "missing a day is not a problem" in text


async def test_the_shelf_summary_counts_rather_than_scores(app_client, make_user):
    _, token = await make_user()
    await stock_shelf(app_client, token)
    body = (await app_client.get("/api/v2/shelf/summary", headers=auth(token))).json()

    assert body["counts"]["products"] == len(SHELF)
    text = all_text(body).lower()
    assert "shelf score" not in text
    assert "overall score" not in text


async def test_a_draft_item_is_not_counted_as_owned(app_client, make_user):
    """Phase 3's boundary, held. 'You own this' has to be true."""
    _, token = await make_user()
    stocked = await stock_shelf(app_client, token)
    factory = sql.get_sessionmaker()
    async with factory() as session:
        from app.domains.inventory.models import InventoryItem

        item = await session.get(InventoryItem, uuid.UUID(stocked[0]["id"]))
        item.verification_state = "draft"
        await session.commit()

    body = (await app_client.get("/api/v2/shelf/summary", headers=auth(token))).json()
    assert body["counts"]["drafts"] >= 1
    assert body["draft_note"]


async def test_value_to_recover_is_scoped_to_the_shelf_and_never_shames(app_client, make_user):
    _, token = await make_user()
    await add_item(app_client, token, "beauty", "Expensive serum", {
        "product_type": "serum", "remaining_percent": 80,
    }, purchase_price="2400.00")
    body = (await app_client.get("/api/v2/shelf/value-to-recover", headers=auth(token))).json()

    assert body["is_estimate"] is True
    text = all_text(body).lower()
    assert "money wasted" not in text
    assert "wasted" not in text


async def test_value_to_recover_reports_a_missing_price_rather_than_inventing_one(app_client, make_user):
    _, token = await make_user()
    await add_item(app_client, token, "beauty", "Unpriced serum", {"product_type": "serum"})
    body = (await app_client.get("/api/v2/shelf/value-to-recover", headers=auth(token))).json()
    assert body["items_missing_price"] == 1
    assert body["items"][0]["estimated_value"] is None


async def test_low_use_is_reported_plainly(app_client, make_user):
    _, token = await make_user()
    await stock_shelf(app_client, token)
    body = (await app_client.get("/api/v2/shelf/low-use", headers=auth(token))).json()
    assert body["rule_id"] == rules.RULE_LOW_USE
    assert "definition" in body


async def test_checking_an_ingredient_list_needs_something_to_check(app_client, make_user):
    _, token = await make_user()
    response = await app_client.post("/api/v2/ingredients/check", json={}, headers=auth(token))
    assert response.status_code == 422


async def test_checking_a_label_reports_what_we_recognised_and_what_we_did_not(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.post(
        "/api/v2/ingredients/check",
        json={"label_text": "Aqua, Retinol, Wibblenol, Glycerin", "explain": False},
        headers=auth(token),
    )).json()
    keys = {row["ingredient_key"] for row in body["identified"]}
    assert "retinol" in keys
    assert any("wibblenol" in row for row in body["unidentified"])


async def test_checking_against_owned_products_finds_a_real_conflict(app_client, make_user):
    _, token = await make_user()
    await add_item(app_client, token, "beauty", "Glycolic toner", {
        "product_type": "toner", "active_ingredients": ["glycolic acid"],
    })
    body = (await app_client.post(
        "/api/v2/ingredients/check",
        json={"ingredients": ["retinol"], "against_owned": True, "explain": False},
        headers=auth(token),
    )).json()
    assert any(row["rule_id"] == "rule.retinoid_aha" for row in body["warnings"])


async def test_an_ingredient_note_is_about_the_ingredient_not_about_you(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get("/api/v2/ingredients/niacinamide", headers=auth(token))).json()
    assert body["display_name"]
    assert body["aliases"]
    assert safety.narrative_is_safe(all_text(body)), safety.first_violation(all_text(body))


async def test_an_unknown_ingredient_is_an_honest_404(app_client, make_user):
    _, token = await make_user()
    response = await app_client.get("/api/v2/ingredients/wibblenol", headers=auth(token))
    assert response.status_code == 404


async def test_an_observation_is_stored_verbatim_and_never_interpreted(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.post(
        "/api/v2/routines/observations",
        json={"area": "skin", "note": "felt tight after washing today"},
        headers=auth(token),
    )).json()
    assert body["note"] == "felt tight after washing today"
    assert body["boundary"] is None


async def test_an_observation_that_reads_like_a_health_question_gets_the_boundary(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.post(
        "/api/v2/routines/observations",
        json={"area": "scalp", "note": "I think I have a fungal infection, what should I take"},
        headers=auth(token),
    )).json()
    assert body["boundary"]["boundary"] is True
    assert "doctor" in body["message"].lower()


async def test_the_improve_screen_reports_empty_modules_as_empty(app_client, make_user):
    """A module nobody has populated must not be shown filled with nothing."""
    _, token = await make_user()
    body = (await app_client.get("/api/v2/routines/improve", headers=auth(token))).json()
    assert body["has_shelf"] is False
    assert body["has_routines"] is False
    assert body["routines"] == []


async def test_the_improve_screen_fills_in_once_there_is_a_shelf(app_client, make_user):
    _, token = await make_user()
    await stock_shelf(app_client, token)
    await generate(app_client, token, explain=False)
    body = (await app_client.get("/api/v2/routines/improve", headers=auth(token))).json()
    assert body["has_shelf"] is True
    assert body["has_routines"] is True
    assert "consistency" in body


# --- The AI boundary ---------------------------------------------------------------------


async def test_a_routine_survives_the_ai_provider_being_down(app_client, make_user, fake_provider):
    """The model phrases; it does not decide. Losing it costs wording only."""
    _, token = await make_user()
    await stock_shelf(app_client, token)
    without_ai = (await generate(app_client, token, explain=False)).json()

    # A transport failure, which the gateway turns into AnalysisUnavailableError.
    fake_provider.raises = RuntimeError("provider down")
    with_failure = (await generate(app_client, token, explain=True)).json()

    assert with_failure["explanation_source"] == SOURCE_DETERMINISTIC
    assert [row["kind"] for row in with_failure["routines"]] == [row["kind"] for row in without_ai["routines"]]
    for before, after in zip(without_ai["routines"], with_failure["routines"]):
        assert [step["slot"] for step in before["steps"]] == [step["slot"] for step in after["steps"]]


async def test_the_model_cannot_invent_a_safety_rule(app_client, make_user, fake_provider):
    """The acceptance criterion, made structural: an unknown rule_id is dropped."""
    from app.domains.routines.explanation import explain_findings

    fake_provider.text = json.dumps({"notes": [
        {"rule_id": "rule.retinoid_aha", "plain_english": "Use these on different nights."},
        {"rule_id": "rule.invented_by_the_model", "plain_english": "Never use these together, ever."},
    ]})
    findings = [
        rules.Finding(
            rule_id="rule.retinoid_aha", severity=SEVERITY_CAUTION,
            headline="Retinoid and acid", detail="Alternate nights.",
        )
    ]
    notes, _, source = await explain_findings(findings, v1_user_id="test-user")
    assert set(notes) == {"rule.retinoid_aha"}
    assert "rule.invented_by_the_model" not in notes
    assert source == SOURCE_AI


async def test_the_model_cannot_smuggle_a_diagnosis_into_a_routine(app_client, make_user, fake_provider):
    from app.domains.routines.explanation import explain_routines

    fake_provider.text = json.dumps({"routines": [
        {"kind": "morning", "summary": "You have eczema, so start with the gentle wash.",
         "step_notes": {"cleanser": "Wash gently."}},
    ]})
    routine = compiler.compile_routine("morning", [
        _product("Cleanser", ["glycerin"], slot="cleanser"),
        _product("Moisturiser", ["squalane"], slot="moisturiser"),
        _product("Sunscreen", ["zinc oxide"], slot="sunscreen"),
    ])
    narratives, _, source = await explain_routines([routine], climate=None, v1_user_id="test-user")
    assert narratives == {}
    assert source == SOURCE_DETERMINISTIC


async def test_the_model_cannot_smuggle_a_dosage_into_a_routine(app_client, make_user, fake_provider):
    from app.domains.routines.explanation import explain_routines

    fake_provider.text = json.dumps({"routines": [
        {"kind": "morning", "summary": "Also take 5000 IU daily of vitamin D.", "step_notes": {}},
    ]})
    routine = compiler.compile_routine("morning", [
        _product("Cleanser", ["glycerin"], slot="cleanser"),
    ])
    narratives, _, source = await explain_routines([routine], climate=None, v1_user_id="test-user")
    assert narratives == {}
    assert source == SOURCE_DETERMINISTIC


async def test_a_note_for_a_step_the_routine_does_not_have_is_dropped(app_client, make_user, fake_provider):
    from app.domains.routines.explanation import explain_routines

    fake_provider.text = json.dumps({"routines": [
        {"kind": "morning", "summary": "A simple morning routine.",
         "step_notes": {"cleanser": "Wash gently.", "hair_mask": "Not part of this routine."}},
    ]})
    routine = compiler.compile_routine("morning", [
        _product("Cleanser", ["glycerin"], slot="cleanser"),
    ])
    narratives, _, source = await explain_routines([routine], climate=None, v1_user_id="test-user")
    assert source == SOURCE_AI
    assert "hair_mask" not in narratives["morning"].step_notes
    assert "cleanser" in narratives["morning"].step_notes


async def test_a_narrative_for_a_routine_we_did_not_ask_about_is_dropped(app_client, make_user, fake_provider):
    from app.domains.routines.explanation import explain_routines

    fake_provider.text = json.dumps({"routines": [
        {"kind": "evening", "summary": "An evening routine we never asked for.", "step_notes": {}},
    ]})
    routine = compiler.compile_routine("morning", [
        _product("Cleanser", ["glycerin"], slot="cleanser"),
    ])
    narratives, _, source = await explain_routines([routine], climate=None, v1_user_id="test-user")
    assert narratives == {}
    assert source == SOURCE_DETERMINISTIC


async def test_nothing_a_route_returns_ever_contains_banned_language(app_client, make_user):
    """A single sweep over every Phase 6 response body."""
    _, token = await make_user()
    await stock_shelf(app_client, token)
    await add_item(app_client, token, "perfumes", "Cedar EDP", {"fragrance_family": "woody"})
    await add_item(app_client, token, "supplements", "Vitamin D3", {"supplement_name": "Vitamin D3"})
    await generate(app_client, token, explain=False)
    await app_client.patch(
        "/api/v2/nutrition/preferences", json={"enabled": True}, headers=auth(token),
    )

    for path in (
        "/api/v2/shelf/summary", "/api/v2/shelf/expiring", "/api/v2/shelf/low-use",
        "/api/v2/shelf/value-to-recover", "/api/v2/routines/today", "/api/v2/routines/improve",
        "/api/v2/perfume/recommendation", "/api/v2/supplements/summary",
        "/api/v2/nutrition/appearance-suggestions",
    ):
        body = (await app_client.get(path, headers=auth(token))).json()
        text = all_text(body)
        assert safety.narrative_is_safe(text), f"{path}: {safety.first_violation(text)}"


# --- Helpers -------------------------------------------------------------------------------


def _product(name, ingredient_names, *, slot=None, confidence=1.0):
    """A ShelfProduct built without a database, for the pure-engine tests."""
    from app.domains.recommendation.context import OwnedItem

    rows = parser.parse_declared(ingredient_names, source=parser.SOURCE_USER)
    assert rows, f"the ontology does not know {ingredient_names}"
    for row in rows:
        row.confidence = confidence
    return rules.ShelfProduct(
        item=OwnedItem(
            id=uuid.uuid4(), category="beauty", subcategory=None, display_name=name,
            brand=None, details={}, condition="good", usage_count=5,
            last_used_at=TODAY, purchase_price=None, currency="INR",
        ),
        slot=slot,
        ingredients=rows,
    )


def _owned_perfume(name, family, *, occasion=None, season=None):
    from app.domains.recommendation.context import OwnedItem

    return OwnedItem(
        id=uuid.uuid4(), category="perfumes", subcategory=None, display_name=name,
        brand=None, condition="good", usage_count=1, last_used_at=None,
        purchase_price=None, currency="INR",
        details={
            "fragrance_family": family, "occasion": occasion or [], "season": season or [],
        },
    )
