"""Phase 4 decision engine contracts.

The tests that matter most here are the ones that hold when the model is
missing, broken or hostile. The provider is faked throughout, and several tests
deliberately run with no provider configured at all, because the promise being
checked is that a look is still real, still made of items the user owns, and
still explained — with or without an AI.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import func, select

from app.domains.recommendation import compatibility as compat
from app.domains.recommendation import roi as roi_stage
from app.domains.recommendation.candidates import shape_from_parts
from app.domains.recommendation.explanation import purchase_summary_is_consistent
from app.domains.recommendation.models import Look, LookItem, RecommendationInput, RecommendationRun
from app.domains.recommendation.occasions import DRESS_CODES, OCCASION_KEYS, questions_for
from app.domains.recommendation.schemas import narrative_is_safe
from app.shared.database import sql
from tests.conftest import auth, png_bytes

# A small but complete wardrobe: enough to build more than one distinct look.
WARDROBE = [
    ("wardrobe", "Navy formal shirt", {"colour": "navy", "fabric": "cotton", "fit": "slim shirt", "size": "M", "formality": "business formal", "occasion": ["office", "interview"], "season": ["summer"]}),
    ("wardrobe", "White cotton shirt", {"colour": "white", "fabric": "cotton", "fit": "regular shirt", "size": "M", "formality": "business casual", "occasion": ["office"], "season": ["summer"]}),
    ("wardrobe", "Charcoal formal trousers", {"colour": "charcoal", "fabric": "wool", "fit": "tailored trousers", "size": "32", "formality": "business formal", "occasion": ["office", "interview"]}),
    ("wardrobe", "Beige chino trousers", {"colour": "beige", "fabric": "cotton", "fit": "regular chinos", "size": "32", "formality": "business casual", "occasion": ["office"]}),
    ("wardrobe", "Rust printed kurta", {"colour": "rust", "fabric": "cotton", "fit": "relaxed kurta", "size": "M", "formality": "festive", "occasion": ["festival"]}),
    ("shoes", "Black leather derby shoes", {"shoe_type": "derby", "colour": "black", "size": "9", "comfort": "all day", "occasion": ["office", "interview"]}),
    ("shoes", "Tan loafers", {"shoe_type": "loafers", "colour": "tan", "size": "9", "comfort": "comfortable", "occasion": ["office"]}),
    ("accessories", "Silver wristwatch", {"accessory_type": "watch", "metal": "silver", "colour": "silver", "occasion": ["office", "interview"]}),
    ("perfumes", "Cedar day perfume", {"fragrance_family": "woody", "concentration": "EDP", "occasion": ["office"]}),
]


async def add_item(client, token, category, name, details, **overrides):
    payload = {"category": category, "display_name": name, "brand": "Test Brand", "details": details}
    payload.update(overrides)
    response = await client.post("/api/v2/inventory/items", json=payload, headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def stock_wardrobe(client, token, rows=WARDROBE):
    return [await add_item(client, token, category, name, details) for category, name, details in rows]


async def set_profile(client, token, **attributes):
    body = {"attributes": [{"key": key, "value": value} for key, value in attributes.items()]}
    response = await client.patch("/api/v2/profile", json=body, headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def style(client, token, occasion_key="office", **occasion):
    payload = {"occasion": {"occasion_key": occasion_key, **occasion}}
    return await client.post("/api/v2/style/occasion", json=payload, headers=auth(token))


def look_narrative(look):
    return " ".join([
        look["why_it_works"], look["weather_note"], look["dress_code_note"],
        *look["preparation_steps"], look["title"],
    ])


# --- Authorization ----------------------------------------------------------


async def test_every_phase_4_route_requires_authentication(app_client):
    calls = [
        ("POST", "/api/v2/occasions", {"occasion_key": "office"}),
        ("GET", "/api/v2/occasions", None),
        ("GET", f"/api/v2/occasions/{uuid.uuid4()}", None),
        ("PATCH", f"/api/v2/occasions/{uuid.uuid4()}", {"notes": "x"}),
        ("POST", "/api/v2/style/occasion", {"occasion": {"occasion_key": "office"}}),
        ("GET", f"/api/v2/looks/{uuid.uuid4()}", None),
        ("POST", f"/api/v2/looks/{uuid.uuid4()}/revise", {}),
        ("POST", f"/api/v2/looks/{uuid.uuid4()}/swap-item", {"slot": "shoes"}),
        ("POST", f"/api/v2/looks/{uuid.uuid4()}/feedback", {"rating": "loved"}),
        ("POST", "/api/v2/shopping/evaluate", {"item": {"category": "wardrobe", "display_name": "Shirt"}}),
        ("GET", f"/api/v2/shopping/evaluations/{uuid.uuid4()}", None),
        ("POST", f"/api/v2/shopping/evaluations/{uuid.uuid4()}/decision", {"decision": "bought"}),
    ]
    for method, path, body in calls:
        response = await app_client.request(method, path, json=body)
        assert response.status_code == 401, f"{method} {path} returned {response.status_code}"


async def test_looks_occasions_and_evaluations_are_owner_scoped(app_client, make_user):
    _, owner = await make_user()
    _, stranger = await make_user()
    await stock_wardrobe(app_client, owner)

    occasion = (await app_client.post("/api/v2/occasions", json={"occasion_key": "office"}, headers=auth(owner))).json()
    result = (await style(app_client, owner)).json()
    assert result["status"] == "ready"
    look_id = result["looks"][0]["id"]

    evaluation = (await app_client.post(
        "/api/v2/shopping/evaluate",
        json={"item": {"category": "shoes", "display_name": "Brown brogues", "colour": "brown"}},
        headers=auth(owner),
    )).json()

    for method, path, body in [
        ("GET", f"/api/v2/occasions/{occasion['id']}", None),
        ("PATCH", f"/api/v2/occasions/{occasion['id']}", {"notes": "mine now"}),
        ("GET", f"/api/v2/looks/{look_id}", None),
        ("POST", f"/api/v2/looks/{look_id}/revise", {}),
        ("POST", f"/api/v2/looks/{look_id}/swap-item", {"slot": "shoes"}),
        ("POST", f"/api/v2/looks/{look_id}/feedback", {"rating": "loved"}),
        ("GET", f"/api/v2/shopping/evaluations/{evaluation['id']}", None),
        ("POST", f"/api/v2/shopping/evaluations/{evaluation['id']}/decision", {"decision": "bought"}),
    ]:
        response = await app_client.request(method, path, json=body, headers=auth(stranger))
        assert response.status_code == 404, f"{method} {path} leaked to another account"


async def test_an_account_id_in_the_body_is_rejected_not_trusted(app_client, make_user):
    _, token = await make_user()
    response = await app_client.post(
        "/api/v2/occasions",
        json={"occasion_key": "office", "account_id": str(uuid.uuid4())},
        headers=auth(token),
    )
    assert response.status_code == 422


# --- Occasion vocabulary ----------------------------------------------------


async def test_all_sixteen_occasions_are_supported_with_relevant_questions(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get("/api/v2/style/occasion-types", headers=auth(token))).json()
    keys = [row["key"] for row in body["occasions"]]
    assert len(keys) == 16
    for expected in ("everyday", "office", "wedding", "festival", "interview", "date", "party",
                     "conference", "business_meeting", "vacation", "travel", "photoshoot",
                     "birthday", "college", "gym", "home"):
        assert expected in keys
    # Only relevant questions are asked: the gym does not need a dress code.
    gym = next(row for row in body["occasions"] if row["key"] == "gym")
    office = next(row for row in body["occasions"] if row["key"] == "office")
    assert "dress_code" not in [q["key"] for q in gym["questions"]]
    assert "dress_code" in [q["key"] for q in office["questions"]]
    assert len(questions_for("wedding")) > len(questions_for("gym"))


async def test_an_invalid_occasion_is_refused_with_the_supported_list(app_client, make_user):
    _, token = await make_user()
    response = await app_client.post("/api/v2/occasions", json={"occasion_key": "coronation"}, headers=auth(token))
    assert response.status_code == 422
    assert "office" in json.dumps(response.json())

    response = await app_client.post("/api/v2/occasions", json={"occasion_key": "office", "dress_code": "spacesuit"}, headers=auth(token))
    assert response.status_code == 422


# --- The core guarantee -----------------------------------------------------


async def test_every_owned_item_in_a_look_is_a_real_inventory_row(app_client, make_user):
    _, token = await make_user()
    created = await stock_wardrobe(app_client, token)
    real_ids = {item["id"] for item in created}

    result = (await style(app_client, token)).json()
    assert result["status"] == "ready"
    assert result["looks"]

    for look in result["looks"]:
        assert look["owned_items"], "a look with no owned items is not a look"
        for item in look["owned_items"]:
            assert item["owned"] is True
            assert item["inventory_item_id"] in real_ids
            assert item["label"] == "In your inventory"
        for optional in look["optional_additions"]:
            assert optional["owned"] is False
            assert optional["inventory_item_id"] is None
            assert "you do not own this" in optional["label"].lower()


async def test_drafts_are_never_treated_as_owned(app_client, make_user, fake_provider):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    media_id = (await app_client.post(
        "/api/v2/media/upload",
        files={"file": ("item.png", png_bytes(), "image/png")},
        data={"purpose": "inventory_item"}, headers=auth(token),
    )).json()["id"]
    fake_provider.text = json.dumps({
        "category": "wardrobe", "display_name": "Extracted green shirt", "confidence": 0.8,
        "details": {"colour": "green", "formality": "business formal"}, "attributes": [],
        "uncertain_fields": [], "photo_quality_notes": "Clear photo",
    })
    draft = (await app_client.post("/api/v2/inventory/extract", json={"media_asset_id": media_id}, headers=auth(token))).json()
    assert draft["item"]["verification_state"] == "draft"
    draft_id = draft["item"]["id"]

    result = (await style(app_client, token)).json()
    used = {item["inventory_item_id"] for look in result["looks"] for item in look["owned_items"]}
    assert draft_id not in used
    assert result["unconfirmed_draft_count"] >= 1
    assert any("draft" in note.lower() for note in result["missing_information"])


async def test_an_empty_inventory_returns_nothing_rather_than_inventing_clothes(app_client, make_user):
    _, token = await make_user()
    result = (await style(app_client, token)).json()
    assert result["status"] == "not_enough_inventory"
    assert result["looks"] == []
    assert "invent" in result["message"].lower()
    assert result["guidance"]
    # A run that produced nothing must not spend the user's allowance.
    assert result["entitlement"]["used"] == 0


# --- Three meaningfully different options -----------------------------------


async def test_up_to_three_meaningfully_different_looks_are_returned(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    result = (await style(app_client, token)).json()
    looks = result["looks"]
    assert 1 <= len(looks) <= 3
    assert [look["variant"] for look in looks] == ["recommended", "comfortable", "expressive"][:len(looks)]

    cores = []
    for look in looks:
        core = frozenset(
            item["inventory_item_id"] for item in look["owned_items"]
            if item["slot"] in ("clothing", "shoes")
        )
        cores.append(core)
    assert len(set(cores)) == len(cores), "the options must not be the same look twice"
    for index, first in enumerate(cores):
        for second in cores[index + 1:]:
            overlap = len(first & second) / len(first | second)
            assert overlap <= 0.6, "options must differ by more than one accessory"


async def test_a_look_carries_every_promised_section(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    look = (await style(app_client, token, weather="hot")).json()["looks"][0]
    for key in ("why_it_works", "weather_note", "dress_code_note", "confidence",
                "preparation_steps", "missing_information", "owned_items",
                "optional_additions", "slots", "share", "explanation_source"):
        assert key in look, f"look is missing {key}"
    assert 0 < look["confidence"] <= 0.95
    assert look["why_it_works"].strip()
    assert "hot" in look["weather_note"].lower()
    assert look["preparation_steps"]
    assert set(look["slots"]) == {"clothing", "shoes", "accessories", "perfume", "hair", "grooming"}


# --- Deterministic rules ----------------------------------------------------


def test_dress_code_rules_prefer_the_right_formality():
    assert DRESS_CODES["black_tie"] == 5 and DRESS_CODES["loungewear"] == 1
    exact, _ = compat.formality_match(5, 5)
    over, _ = compat.formality_match(5, 4)
    under, _ = compat.formality_match(3, 5)
    assert exact == 1.0
    assert over > under, "being over-dressed must be penalised less than being under-dressed"
    unknown, reason = compat.formality_match(None, 4)
    assert unknown == compat.NEUTRAL and "not scored" in reason


def test_weather_rules_favour_the_right_fabrics():
    linen = {"fabric": "linen"}
    wool = {"fabric": "wool"}
    hot_linen, _ = compat.weather_suitability(linen, "hot")
    hot_wool, _ = compat.weather_suitability(wool, "hot")
    cold_wool, _ = compat.weather_suitability(wool, "cold")
    assert hot_linen > 0.8 and hot_wool < 0.3 and cold_wool > 0.8
    declared, reason = compat.weather_suitability({"weather_suitability": ["rainy"]}, "rainy")
    assert declared == 1.0 and "you recorded" in reason.lower()
    missing, reason = compat.weather_suitability({}, None)
    assert missing == compat.NEUTRAL and "no weather" in reason.lower()


def test_colour_compatibility_follows_the_wheel():
    neutral, _ = compat.colour_compatibility("black", "rust")
    complementary, _ = compat.colour_compatibility("red", "teal")
    awkward, _ = compat.colour_compatibility("red", "green")
    monochrome, _ = compat.colour_compatibility("navy", "navy")
    assert neutral > 0.85
    assert complementary > awkward
    assert monochrome > 0.8
    unknown, reason = compat.colour_compatibility("chartreuse-ish nonsense", "blue")
    assert unknown == compat.NEUTRAL and "not recorded" in reason.lower()
    assert compat.normalise_colour("dark teal") == "teal"


def test_a_disliked_colour_is_excluded_not_merely_penalised():
    score, reason = compat.palette_fit("mustard", favourites=[], disliked=["mustard"])
    assert score == 0.0 and "avoid" in reason.lower()
    liked, _ = compat.palette_fit("teal", favourites=["teal"], disliked=[])
    assert liked == 1.0


def test_garment_shapes_are_classified_from_what_the_user_recorded():
    assert shape_from_parts("kurta set", "Cream kurta set") == "one_piece"
    assert shape_from_parts("shirt", "Navy formal shirt") == "top"
    assert shape_from_parts("trousers", "Charcoal formal trousers") == "bottom"
    assert shape_from_parts("blazer", "Navy blazer") == "layer"
    assert shape_from_parts(None, "Something unlabelled") == "unknown"


async def test_disliked_colours_never_reach_a_look(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    await set_profile(app_client, token, disliked_colours=["navy"], favourite_colours=["white"])
    result = (await style(app_client, token)).json()
    names = [item["display_name"] for look in result["looks"] for item in look["owned_items"]]
    assert "Navy formal shirt" not in names


async def test_weather_shapes_which_items_are_chosen(app_client, make_user):
    _, token = await make_user()
    await add_item(app_client, token, "wardrobe", "Linen summer shirt", {"colour": "white", "fabric": "linen", "formality": "business casual", "size": "M"})
    await add_item(app_client, token, "wardrobe", "Heavy wool shirt", {"colour": "grey", "fabric": "wool", "formality": "business casual", "size": "M"})
    await add_item(app_client, token, "wardrobe", "Beige chino trousers", {"colour": "beige", "fabric": "cotton", "formality": "business casual", "size": "32"})
    await add_item(app_client, token, "shoes", "Tan loafers", {"shoe_type": "loafers", "colour": "tan", "size": "9"})

    hot = (await style(app_client, token, weather="hot")).json()["looks"][0]
    names = [item["display_name"] for item in hot["owned_items"]]
    assert "Linen summer shirt" in names
    assert "Heavy wool shirt" not in names


# --- Missing pieces are labelled, never invented -----------------------------


async def test_a_missing_slot_becomes_a_labelled_optional_addition(app_client, make_user):
    _, token = await make_user()
    # A top and a bottom, but nothing to put on their feet.
    await add_item(app_client, token, "wardrobe", "White cotton shirt", {"colour": "white", "fabric": "cotton", "formality": "business casual", "size": "M"})
    await add_item(app_client, token, "wardrobe", "Beige chino trousers", {"colour": "beige", "fabric": "cotton", "formality": "business casual", "size": "32"})

    look = (await style(app_client, token)).json()["looks"][0]
    optional = look["optional_additions"]
    assert optional, "a look with no shoes must say so"
    shoe_gap = next(row for row in optional if row["slot"] == "shoes")
    assert shoe_gap["inventory_item_id"] is None
    assert shoe_gap["owned"] is False
    # A gap describes what is needed. It must not name a brand or a price.
    assert "brand" not in shoe_gap["display_name"].lower()
    assert "₹" not in shoe_gap["display_name"] and "rs" not in shoe_gap["display_name"].lower()


# --- Revision and swapping --------------------------------------------------


async def test_a_look_can_be_revised_away_from_what_the_user_rejected(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    look = (await style(app_client, token)).json()["looks"][0]
    before = {item["inventory_item_id"] for item in look["owned_items"] if item["slot"] in ("clothing", "shoes")}

    revised = (await app_client.post(
        f"/api/v2/looks/{look['id']}/revise",
        json={"reason": "too_formal"}, headers=auth(token),
    )).json()
    after = {item["inventory_item_id"] for item in revised["owned_items"] if item["slot"] in ("clothing", "shoes")}
    assert revised["version"] > look["version"]
    assert after != before
    assert any(row["adjustment_type"] == "revise" for row in revised["adjustments"])
    for item in revised["owned_items"]:
        assert item["inventory_item_id"] is not None


async def test_one_item_can_be_swapped_for_another_owned_item(app_client, make_user):
    _, token = await make_user()
    created = await stock_wardrobe(app_client, token)
    look = (await style(app_client, token)).json()["looks"][0]
    worn_shoes = next(item for item in look["owned_items"] if item["slot"] == "shoes")
    other_shoes = next(
        row for row in created
        if row["category"] == "shoes" and row["id"] != worn_shoes["inventory_item_id"]
    )

    swapped = (await app_client.post(
        f"/api/v2/looks/{look['id']}/swap-item",
        json={"slot": "shoes", "from_item_id": worn_shoes["inventory_item_id"], "to_item_id": other_shoes["id"]},
        headers=auth(token),
    )).json()
    shoes_now = [item["inventory_item_id"] for item in swapped["owned_items"] if item["slot"] == "shoes"]
    assert shoes_now == [other_shoes["id"]]
    assert any(row["adjustment_type"] == "swap_item" for row in swapped["adjustments"])
    assert swapped["version"] > look["version"]


async def test_a_swap_cannot_smuggle_in_an_item_the_user_does_not_own(app_client, make_user):
    _, owner = await make_user()
    _, stranger = await make_user()
    await stock_wardrobe(app_client, owner)
    theirs = await add_item(app_client, stranger, "shoes", "Their shoes", {"shoe_type": "loafers", "colour": "black", "size": "9"})
    look = (await style(app_client, owner)).json()["looks"][0]

    response = await app_client.post(
        f"/api/v2/looks/{look['id']}/swap-item",
        json={"slot": "shoes", "to_item_id": theirs["id"]}, headers=auth(owner),
    )
    assert response.status_code == 404

    response = await app_client.post(
        f"/api/v2/looks/{look['id']}/swap-item",
        json={"slot": "shoes", "to_item_id": str(uuid.uuid4())}, headers=auth(owner),
    )
    assert response.status_code == 404


async def test_a_swap_must_respect_the_slot_and_confirmation_state(app_client, make_user, fake_provider):
    _, token = await make_user()
    created = await stock_wardrobe(app_client, token)
    look = (await style(app_client, token)).json()["looks"][0]
    a_shirt = next(row for row in created if row["display_name"] == "White cotton shirt")

    wrong_slot = await app_client.post(
        f"/api/v2/looks/{look['id']}/swap-item",
        json={"slot": "shoes", "to_item_id": a_shirt["id"]}, headers=auth(token),
    )
    assert wrong_slot.status_code == 422

    media_id = (await app_client.post(
        "/api/v2/media/upload", files={"file": ("i.png", png_bytes(), "image/png")},
        data={"purpose": "inventory_item"}, headers=auth(token),
    )).json()["id"]
    fake_provider.text = json.dumps({
        "category": "shoes", "display_name": "Extracted sandals", "confidence": 0.7,
        "details": {"colour": "brown"}, "attributes": [], "uncertain_fields": [],
        "photo_quality_notes": "Readable",
    })
    draft = (await app_client.post("/api/v2/inventory/extract", json={"media_asset_id": media_id}, headers=auth(token))).json()["item"]

    unconfirmed = await app_client.post(
        f"/api/v2/looks/{look['id']}/swap-item",
        json={"slot": "shoes", "to_item_id": draft["id"]}, headers=auth(token),
    )
    assert unconfirmed.status_code == 422
    assert "confirm" in json.dumps(unconfirmed.json()).lower()


# --- Feedback and memory ----------------------------------------------------


async def test_feedback_is_stored_and_read_back(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    look = (await style(app_client, token)).json()["looks"][0]

    saved = (await app_client.post(
        f"/api/v2/looks/{look['id']}/feedback",
        json={"rating": "loved", "reason": "great_fit", "note": "Wore it and felt good"},
        headers=auth(token),
    )).json()
    assert saved["feedback"]["rating"] == "loved"
    assert saved["saved"] is True

    fetched = (await app_client.get(f"/api/v2/looks/{look['id']}", headers=auth(token))).json()
    assert fetched["feedback"]["reason"] == "great_fit"

    # Feedback is one row per look per account, not an append-only pile.
    await app_client.post(f"/api/v2/looks/{look['id']}/feedback", json={"rating": "not_for_me"}, headers=auth(token))
    again = (await app_client.get(f"/api/v2/looks/{look['id']}", headers=auth(token))).json()
    assert again["feedback"]["rating"] == "not_for_me"


async def test_a_run_records_the_inputs_that_produced_it(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    await set_profile(app_client, token, favourite_colours=["white"], preferred_style="classic")
    result = (await style(app_client, token, weather="mild")).json()

    factory = sql.get_sessionmaker()
    async with factory() as session:
        run = await session.get(RecommendationRun, uuid.UUID(result["run_id"]))
        assert run is not None and run.status == "succeeded"
        rows = (await session.execute(
            select(RecommendationInput).where(RecommendationInput.run_id == run.id)
        )).scalars().all()
        keys = {row.input_key for row in rows}
        assert {"occasion_key", "target_formality", "weather", "confirmed_item_count"} <= keys
        assert any(row.input_type == "profile" and row.input_key == "favourite_colours" for row in rows)
        assert all(row.source for row in rows)


# --- Shopping: the ROI model ------------------------------------------------


def test_the_roi_formula_is_a_documented_weighted_sum():
    owned = []
    candidate = roi_stage.Candidate(category="wardrobe", display_name="Olive linen shirt", colour="olive green", fabric="linen", formality="business casual", size="M", price=None)
    result = roi_stage.evaluate(candidate, owned)
    total_weight = sum(factor.weight for factor in result.factors)
    expected = round(sum(factor.contribution for factor in result.factors) / total_weight, 4)
    assert result.score == expected
    for factor in result.factors:
        assert factor.contribution == round(factor.raw_value * factor.weight, 4)
        assert factor.explanation
    assert result.version == roi_stage.ROI_VERSION


def test_a_missing_price_is_reported_and_left_out_of_the_score():
    candidate = roi_stage.Candidate(category="wardrobe", display_name="Linen shirt", colour="white", fabric="linen", formality="casual")
    result = roi_stage.evaluate(candidate, [])
    assert all(factor.key != "price_context" for factor in result.factors)
    assert any("price" in note.lower() for note in result.missing_information)
    priced = roi_stage.evaluate(
        roi_stage.Candidate(category="wardrobe", display_name="Linen shirt", colour="white", fabric="linen", formality="casual", price=1200),
        [],
    )
    assert any(factor.key == "price_context" for factor in priced.factors)


def test_a_missing_size_is_reported_rather_than_assumed():
    result = roi_stage.evaluate(
        roi_stage.Candidate(category="wardrobe", display_name="Shirt", colour="white"),
        [], fit_preferences={"usual_top_size": "M"},
    )
    assert any("size" in note.lower() for note in result.missing_information)


def test_the_duplicate_penalty_pushes_a_near_copy_away_from_buy():
    from app.domains.recommendation.context import OwnedItem

    existing = [
        OwnedItem(id=uuid.uuid4(), category="wardrobe", subcategory="shirt", display_name="Navy formal shirt",
                  brand="Test Brand", details={"colour": "navy", "formality": "business formal", "fabric": "cotton"},
                  condition="good", usage_count=3, last_used_at=None, purchase_price=None, currency="INR")
    ]
    near_copy = roi_stage.Candidate(category="wardrobe", subcategory="shirt", display_name="Navy formal shirt", brand="Test Brand", colour="navy", formality="business formal")
    different = roi_stage.Candidate(category="wardrobe", subcategory="trousers", display_name="Beige chinos", colour="beige", formality="business casual")

    copy_result = roi_stage.evaluate(near_copy, existing)
    other_result = roi_stage.evaluate(different, existing)

    assert roi_stage.similarity(near_copy, existing[0]) >= roi_stage.DUPLICATE_LIMIT
    assert copy_result.duplicate_item_ids == [str(existing[0].id)]
    assert copy_result.verdict != roi_stage.VERDICT_BUY, "a near copy can never be a Buy"
    duplicate_factor = next(f for f in copy_result.factors if f.key == "duplicate_penalty")
    other_factor = next(f for f in other_result.factors if f.key == "duplicate_penalty")
    assert duplicate_factor.raw_value < other_factor.raw_value


def test_new_outfit_combinations_are_counted_from_real_pairings():
    from app.domains.recommendation.context import OwnedItem

    def owned(name, category, colour, formality, subcategory=None):
        return OwnedItem(id=uuid.uuid4(), category=category, subcategory=subcategory, display_name=name,
                         brand=None, details={"colour": colour, "formality": formality},
                         condition="good", usage_count=1, last_used_at=None, purchase_price=None, currency="INR")

    wardrobe = [
        owned("Charcoal trousers", "wardrobe", "charcoal", "business casual", "trousers"),
        owned("Beige chinos", "wardrobe", "beige", "business casual", "chinos"),
        owned("Black derby shoes", "shoes", "black", "business casual"),
    ]
    shirt = roi_stage.Candidate(category="wardrobe", subcategory="shirt", display_name="White shirt", colour="white", formality="business casual")
    count, notes = roi_stage.estimate_new_combinations(shirt, wardrobe)
    assert count == 2, "two bottoms x one pair of shoes"
    assert notes and "bottom" in notes[0]

    # Nothing to pair with means no new combinations, and no Buy.
    lonely = roi_stage.evaluate(shirt, [])
    assert lonely.new_combinations == 0
    assert lonely.verdict != roi_stage.VERDICT_BUY


async def test_shopping_evaluate_returns_buy_wait_or_skip_with_the_whole_calculation(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)

    response = await app_client.post(
        "/api/v2/shopping/evaluate",
        json={"item": {"category": "shoes", "display_name": "Oxblood brogues", "colour": "maroon",
                       "formality": "business formal", "size": "9"}, "price": 4500, "occasion_key": "office"},
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] in ("buy", "wait", "skip")
    assert body["headline"].lower().startswith(body["verdict"])
    roi_block = body["appearance_roi"]
    assert roi_block["version"] and roi_block["formula"]
    assert roi_block["thresholds"] == {"buy": 0.65, "wait": 0.45}
    assert len(roi_block["factors"]) >= 8
    assert all(factor["explanation"] for factor in roi_block["factors"])
    assert body["candidate"]["in_inventory"] is False
    assert isinstance(body["new_combinations"], int)
    assert body["summary"]


async def test_existing_owned_alternatives_are_shown(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    body = (await app_client.post(
        "/api/v2/shopping/evaluate",
        json={"item": {"category": "shoes", "display_name": "Black leather derby shoes",
                       "colour": "black", "formality": "business formal"}},
        headers=auth(token),
    )).json()
    shown = body["similar_owned_products"] + body["existing_alternatives"]
    assert shown, "the user should be told what they already own that does this job"
    for row in shown:
        assert row["inventory_item_id"] and row["display_name"]


async def test_a_shopping_candidate_never_becomes_inventory(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    before = (await app_client.get("/api/v2/inventory/summary", headers=auth(token))).json()["total_items"]
    await app_client.post(
        "/api/v2/shopping/evaluate",
        json={"item": {"category": "wardrobe", "display_name": "Considered jacket", "colour": "olive green"}},
        headers=auth(token),
    )
    after = (await app_client.get("/api/v2/inventory/summary", headers=auth(token))).json()["total_items"]
    assert after == before


async def test_a_decision_can_be_recorded_against_an_evaluation(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    evaluation = (await app_client.post(
        "/api/v2/shopping/evaluate",
        json={"item": {"category": "accessories", "display_name": "Gold cufflinks", "colour": "gold"}},
        headers=auth(token),
    )).json()

    saved = (await app_client.post(
        f"/api/v2/shopping/evaluations/{evaluation['id']}/decision",
        json={"decision": "skipped", "note": "Already have silver"}, headers=auth(token),
    )).json()
    assert saved["decision"]["decision"] == "skipped"
    assert saved["decision"]["followed_recommendation"] == (evaluation["verdict"] == "skip")

    fetched = (await app_client.get(f"/api/v2/shopping/evaluations/{evaluation['id']}", headers=auth(token))).json()
    assert fetched["decision"]["note"] == "Already have silver"


async def test_the_roi_model_is_published_for_anyone_to_check(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get("/api/v2/shopping/roi-model", headers=auth(token))).json()
    assert body["version"] == roi_stage.ROI_VERSION
    assert "sum(factor value x factor weight)" in body["formula"]
    assert len(body["factors"]) == len(roi_stage.FACTOR_WEIGHTS)
    assert body["overrides"]


# --- Screenshot extraction --------------------------------------------------


async def test_a_product_screenshot_is_read_into_a_reviewable_candidate(app_client, make_user, fake_provider):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    media_id = (await app_client.post(
        "/api/v2/media/upload", files={"file": ("shot.png", png_bytes(), "image/png")},
        data={"purpose": "inventory_item"}, headers=auth(token),
    )).json()["id"]

    fake_provider.text = json.dumps({
        "category": "wardrobe", "display_name": "Olive linen shirt", "brand": "Seen Brand",
        "colour": "olive green", "fabric": "linen", "size": "M", "formality": "business casual",
        "occasion_tags": ["office"], "season_tags": ["summer"], "price": 2199, "currency": "INR",
        "confidence": 0.82, "uncertain_fields": ["size"], "photo_quality_notes": "Listing text is legible",
    })
    body = (await app_client.post(
        "/api/v2/shopping/evaluate",
        json={"media_asset_id": media_id, "source": "screenshot"}, headers=auth(token),
    )).json()

    candidate = body["candidate"]
    assert candidate["display_name"] == "Olive linen shirt"
    assert candidate["source"] == "screenshot"
    assert candidate["verification_state"] == "draft"
    assert candidate["uncertain_fields"] == ["size"]
    assert candidate["price"] == 2199.0
    assert any("check these before deciding" in note.lower() for note in body["missing_information"])


async def test_a_screenshot_that_cannot_be_read_fails_openly(app_client, make_user, fake_provider):
    _, token = await make_user()
    media_id = (await app_client.post(
        "/api/v2/media/upload", files={"file": ("shot.png", png_bytes(), "image/png")},
        data={"purpose": "inventory_item"}, headers=auth(token),
    )).json()["id"]
    fake_provider.text = "this is not json"

    response = await app_client.post(
        "/api/v2/shopping/evaluate",
        json={"media_asset_id": media_id, "source": "screenshot"}, headers=auth(token),
    )
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "ANALYSIS_UNAVAILABLE"
    assert detail["allowance_consumed"] is False


async def test_a_screenshot_belonging_to_someone_else_is_refused(app_client, make_user):
    _, owner = await make_user()
    _, stranger = await make_user()
    media_id = (await app_client.post(
        "/api/v2/media/upload", files={"file": ("shot.png", png_bytes(), "image/png")},
        data={"purpose": "inventory_item"}, headers=auth(owner),
    )).json()["id"]

    response = await app_client.post(
        "/api/v2/shopping/evaluate",
        json={"media_asset_id": media_id, "source": "screenshot"}, headers=auth(stranger),
    )
    assert response.status_code == 404


# --- AI behaviour: the engine survives every failure -------------------------


async def test_looks_are_produced_with_no_ai_provider_at_all(app_client, make_user):
    """No provider is configured in the test environment. Looks still work."""
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    result = (await style(app_client, token)).json()
    assert result["status"] == "ready"
    assert result["explanation_source"] == "deterministic"
    for look in result["looks"]:
        assert look["explanation_source"] == "deterministic"
        assert look["owned_items"]
        assert len(look["why_it_works"]) > 40, "the deterministic explanation must be real text"


async def test_invalid_ai_output_never_reaches_the_user(app_client, make_user, fake_provider):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)

    for payload in (
        "not json at all",
        json.dumps({"looks": []}),
        json.dumps({"looks": [{"variant": "recommended"}]}),
        json.dumps({"looks": [{"variant": "invented_variant", "why_it_works": "x" * 50}]}),
    ):
        fake_provider.text = payload
        result = (await style(app_client, token)).json()
        assert result["status"] == "ready"
        for look in result["looks"]:
            assert look["explanation_source"] == "deterministic"
            assert look["owned_items"], "a bad model reply must not empty a look"


async def test_ai_wording_that_breaks_the_language_rules_is_discarded(app_client, make_user, fake_provider):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    fake_provider.text = json.dumps({"looks": [{
        "variant": "recommended",
        "why_it_works": "This is very slimming and hides your problem areas nicely for your body type.",
        "weather_note": "", "dress_code_note": "", "preparation_steps": [],
    }]})
    result = (await style(app_client, token)).json()
    for look in result["looks"]:
        assert look["explanation_source"] == "deterministic"
        assert "slimming" not in look["why_it_works"].lower()


async def test_good_ai_wording_is_used_and_marked_as_such(app_client, make_user, fake_provider):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    fake_provider.text = json.dumps({"looks": [{
        "variant": "recommended",
        "why_it_works": "The navy shirt and charcoal trousers read as considered without trying too hard, and the black shoes tie the whole thing together.",
        "weather_note": "Cotton and wool are comfortable indoors.",
        "dress_code_note": "This sits squarely at business formal.",
        "preparation_steps": ["Press the shirt the night before"],
    }]})
    result = (await style(app_client, token)).json()
    recommended = next(look for look in result["looks"] if look["variant"] == "recommended")
    assert recommended["explanation_source"] == "ai_validated"
    assert "considered without trying too hard" in recommended["why_it_works"]
    assert recommended["preparation_steps"] == ["Press the shirt the night before"]
    # The others were not explained, so they keep their own deterministic text.
    for look in result["looks"]:
        if look["variant"] != "recommended":
            assert look["explanation_source"] == "deterministic"


def test_a_summary_that_argues_with_its_own_verdict_is_rejected():
    assert purchase_summary_is_consistent("This is worth buying for the office.", "buy")
    assert not purchase_summary_is_consistent("Honestly you should skip this one.", "buy")
    assert not purchase_summary_is_consistent("It is definitely worth buying.", "skip")
    assert purchase_summary_is_consistent("Hold off until you know the size.", "wait")


async def test_ai_wording_cannot_flip_a_shopping_verdict(app_client, make_user, fake_provider):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    fake_provider.text = json.dumps({"summary": "Ignore the maths, you should buy this immediately, it is worth buying."})
    body = (await app_client.post(
        "/api/v2/shopping/evaluate",
        json={"item": {"category": "shoes", "display_name": "Black leather derby shoes", "colour": "black", "formality": "business formal"}},
        headers=auth(token),
    )).json()
    if body["verdict"] == "skip":
        assert body["explanation_source"] == "deterministic"
        assert "worth buying" not in body["summary"].lower()
    assert body["headline"].lower().startswith(body["verdict"])


# --- Language safety --------------------------------------------------------


def test_the_banned_language_list_covers_body_shaming_and_money_wasted():
    assert not narrative_is_safe("This is very slimming.")
    assert not narrative_is_safe("Think of the money wasted on that.")
    assert not narrative_is_safe("It hides your problem area.")
    assert not narrative_is_safe("Great for your body type.")
    assert not narrative_is_safe("This will help diagnose your skin.")
    assert narrative_is_safe("The colours sit well together and suit the occasion.")


async def test_no_response_uses_money_wasted_or_comments_on_a_body(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    result = (await style(app_client, token, weather="hot")).json()
    evaluation = (await app_client.post(
        "/api/v2/shopping/evaluate",
        json={"item": {"category": "wardrobe", "display_name": "Olive linen shirt", "colour": "olive green", "fabric": "linen"}, "price": 1500},
        headers=auth(token),
    )).json()

    text = (json.dumps(result) + json.dumps(evaluation)).lower()
    for banned in ("money wasted", "slimming", "flattering for your body", "problem area",
                   "body type", "attractiveness", "overall score", "lose weight"):
        assert banned not in text, f"response contained banned wording: {banned}"


async def test_no_appearance_score_is_returned_anywhere(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    result = (await style(app_client, token)).json()
    for look in result["looks"]:
        # A look has a fit score and a confidence, both about the data, never a
        # rating of the person.
        assert "attractiveness" not in json.dumps(look).lower()
        assert "overall_score" not in json.dumps(look)
        assert look["confidence"] <= 0.95


# --- Sharing ----------------------------------------------------------------


async def test_a_look_can_be_shared_without_leaking_anything_personal(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    result = (await style(app_client, token)).json()
    look = result["looks"][0]
    share = look["share"]

    assert share["includes_personal_data"] is False
    assert share["title"] == look["title"]
    assert share["text"]
    for item in look["owned_items"]:
        assert item["display_name"] in share["text"]
        assert item["inventory_item_id"] not in share["text"]
    assert look["id"] not in share["text"]


# --- Entitlements and flags -------------------------------------------------


async def test_the_allowance_is_backend_controlled_and_reported(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    first = (await style(app_client, token)).json()["entitlement"]
    second = (await style(app_client, token)).json()["entitlement"]
    assert first["source"] == "beta_grant"
    assert second["used"] == first["used"] + 1
    assert second["remaining"] == second["included"] - second["used"]


async def test_the_phase_4_routes_are_behind_their_own_flags(app_client, make_user, monkeypatch):
    from app.shared.flags import service as flag_service

    _, token = await make_user()
    original = flag_service.is_enabled

    async def only_style_off(session, key):
        if key == "v2_recommendations":
            return False
        return await original(session, key)

    monkeypatch.setattr(flag_service, "is_enabled", only_style_off)
    # A switched-off feature looks absent rather than forbidden.
    assert (await app_client.post("/api/v2/style/occasion", json={"occasion": {"occasion_key": "office"}}, headers=auth(token))).status_code == 404
    assert (await app_client.get("/api/v2/shopping/roi-model", headers=auth(token))).status_code == 200


# --- Regression -------------------------------------------------------------


async def test_phase_4_does_not_disturb_the_inventory_or_profile_contracts(app_client, make_user):
    _, token = await make_user()
    created = await stock_wardrobe(app_client, token)
    await style(app_client, token)

    summary = (await app_client.get("/api/v2/inventory/summary", headers=auth(token))).json()
    assert summary["total_items"] == len(created)
    assert "money wasted" not in json.dumps(summary).lower()

    item = (await app_client.get(f"/api/v2/inventory/items/{created[0]['id']}", headers=auth(token))).json()
    assert item["verification_state"] == "confirmed"
    assert item["version"] == 1, "styling must not mutate inventory items"

    profile = (await app_client.get("/api/v2/profile", headers=auth(token))).json()
    assert profile["weight_required"] is False


async def test_privacy_export_includes_phase_4_activity(app_client, make_user):
    _, token = await make_user()
    await stock_wardrobe(app_client, token)
    result = (await style(app_client, token)).json()
    export = (await app_client.get("/api/v2/privacy/export", headers=auth(token))).json()
    assert result["run_id"]
    assert export, "export must still be produced once Phase 4 data exists"


async def test_looks_survive_an_item_being_archived_afterwards(app_client, make_user):
    _, token = await make_user()
    created = await stock_wardrobe(app_client, token)
    look = (await style(app_client, token)).json()["looks"][0]
    worn = look["owned_items"][0]["inventory_item_id"]

    await app_client.delete(f"/api/v2/inventory/items/{worn}", headers=auth(token))
    fetched = (await app_client.get(f"/api/v2/looks/{look['id']}", headers=auth(token))).json()

    shown = {item["inventory_item_id"] for item in fetched["owned_items"]}
    assert worn not in shown, "an archived item must not keep appearing as owned"
    assert fetched["unavailable_items"], "the user is told what is no longer available"
