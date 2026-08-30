"""Shared helpers that walk an account through the product over the real API.

Both the API-level critical journey (``test_critical_journey_api.py``) and the
privacy/deletion integration tests (``test_domain_privacy_integration.py``) need
an account whose data spans every active domain. Building that twice would mean
two versions of "what a populated account looks like", and the second one would
rot. So it lives here once, expressed only in terms of HTTP calls against the V2
routes — no direct table writes, because a row written by a test proves nothing
about the route that is supposed to write it.

Nothing here asserts product behaviour beyond "the call succeeded". The
behavioural assertions belong in the tests that use these helpers.
"""
from __future__ import annotations

import base64
from datetime import UTC, date, datetime, timedelta
from typing import Any

from tests.conftest import auth, png_bytes

# A Monday, so the weekly planner's week starts on it.
JOURNEY_DATE = date(2026, 2, 16)


# The seven canonical inventory categories, with a valid detail payload each.
# ``beauty`` is the Beauty Shelf and ``hair`` the Hair Shelf; those are the
# internal keys for the two shelf categories.
SEVEN_CATEGORY_ITEMS: list[dict[str, Any]] = [
    {
        "category": "wardrobe", "display_name": "Charcoal Blazer", "subcategory": "blazer",
        "brand": "Fable",
        "details": {"colour": "charcoal", "fabric": "wool", "formality": "smart_casual", "season": ["all"]},
    },
    {
        "category": "wardrobe", "display_name": "White Cotton Shirt", "subcategory": "shirt",
        "details": {"colour": "white", "fabric": "cotton", "formality": "smart_casual", "season": ["all"]},
    },
    {
        "category": "wardrobe", "display_name": "Navy Chinos", "subcategory": "trousers",
        "details": {"colour": "navy", "fabric": "cotton", "formality": "smart_casual", "season": ["all"]},
    },
    {
        "category": "shoes", "display_name": "Brown Leather Derbies", "subcategory": "derby",
        "details": {"colour": "brown", "shoe_type": "derby", "occasion": ["office"]},
    },
    {
        "category": "accessories", "display_name": "Tan Leather Belt", "subcategory": "belt",
        "details": {"colour": "tan", "accessory_type": "belt", "material": "leather"},
    },
    {
        "category": "beauty", "display_name": "Gentle Foaming Cleanser", "subcategory": "cleanser",
        "details": {
            "product_type": "cleanser", "purpose": "cleansing",
            "routine_position": "cleanse", "use_frequency": "twice_daily",
            "active_ingredients": ["glycerin"],
        },
    },
    {
        "category": "hair", "display_name": "Hydrating Shampoo", "subcategory": "shampoo",
        "details": {
            "product_type": "shampoo", "purpose": "cleansing",
            "routine_position": "cleanse", "use_frequency": "twice_weekly",
        },
    },
    {
        "category": "perfumes", "display_name": "Citrus Eau de Toilette", "subcategory": "edt",
        "details": {"fragrance_family": "citrus", "concentration": "edt", "occasion": ["office"]},
    },
    {
        "category": "supplements", "display_name": "Vitamin D3", "subcategory": "vitamin",
        "details": {"supplement_name": "Vitamin D3", "use_frequency": "daily"},
    },
]

SEVEN_CATEGORIES = ["wardrobe", "shoes", "accessories", "beauty", "hair", "perfumes", "supplements"]


def ok(resp, *allowed: int):
    """Assert the call succeeded and return the decoded body."""
    allowed = allowed or (200, 201)
    assert resp.status_code in allowed, (
        f"{resp.request.method} {resp.request.url.path} -> {resp.status_code}: {resp.text}"
    )
    return resp.json() if resp.content else None


async def register_through_invite(client, fake_supabase_user, *, email: str, admin_token: str):
    """Reserve an invite and finish registration, the way the app does.

    Returns ``(token, account_uuid, invite_code)``.
    """
    invite = ok(await client.post(
        "/api/v2/access/admin/invites",
        headers=auth(admin_token),
        json={"label": "journey", "max_uses": 1},
    ))
    code = invite["code"]

    reserved = ok(await client.post(
        "/api/v2/access/reserve", json={"invite_code": code, "email": email}
    ))
    token, account_id = fake_supabase_user(email=email)
    ok(await client.post(
        "/api/v2/access/register",
        headers=auth(token),
        json={"registration_challenge": reserved["challenge"]},
    ))
    return token, account_id, code


async def complete_profile_and_onboarding(client, token) -> dict[str, Any]:
    ok(await client.get("/api/v2/profile", headers=auth(token)))
    ok(await client.patch(
        "/api/v2/profile",
        headers=auth(token),
        json={"attributes": [
            {"key": "skin_tone", "value": "medium"},
            {"key": "undertone", "value": "warm"},
            {"key": "hair_type", "value": "wavy"},
            {"key": "climate", "value": "hot_humid"},
            {"key": "favourite_colours", "value": ["deep teal", "ivory"]},
        ]},
    ))
    status = ok(await client.get("/api/v2/onboarding/status", headers=auth(token)))
    # A partial save: the goal step now, the rest later. Resuming must pick up
    # exactly here.
    ok(await client.post(
        "/api/v2/onboarding/step",
        headers=auth(token),
        json={
            "step": "goal",
            "data": {"current_goal": "look_polished_at_work",
                     "appearance_goals": ["even_tone"]},
        },
    ))
    ok(await client.post(
        "/api/v2/onboarding/step",
        headers=auth(token),
        json={"step": "lifestyle", "data": {"climate": "hot_humid", "city": "Mumbai"}},
    ))
    return status


async def grant_photo_consent(client, token) -> dict[str, Any]:
    return ok(await client.post(
        "/api/v2/consent",
        headers=auth(token),
        json={"consent_type": "photo_analysis", "granted": True},
    ))


async def stock_seven_categories(client, token) -> dict[str, list[str]]:
    """Add at least one item in each of the seven categories."""
    created: dict[str, list[str]] = {}
    for body in SEVEN_CATEGORY_ITEMS:
        item = ok(await client.post("/api/v2/inventory/items", headers=auth(token), json=body))
        created.setdefault(body["category"], []).append(item["id"])
    assert set(created) == set(SEVEN_CATEGORIES)
    return created


async def upload_inventory_image(client, token, item_id: str) -> str:
    asset = ok(await client.post(
        "/api/v2/media/upload",
        headers=auth(token),
        files={"file": ("item.png", png_bytes(), "image/png")},
    ))
    ok(await client.patch(
        f"/api/v2/inventory/items/{item_id}",
        headers=auth(token),
        json={"image_ids": [asset["id"]]},
    ))
    return asset["id"]


async def run_scan(client, token, *, scan_type: str = "face") -> dict[str, Any]:
    image = base64.b64encode(png_bytes()).decode("ascii")
    return ok(await client.post(
        "/api/v2/scan/analyse",
        headers=auth(token),
        json={"image_base64": image, "scan_type": scan_type},
    ), 201)


# The style vibe quiz is a rejected product surface (PRODUCT_CONSTITUTION.md) and
# v2_quiz is off by default, so the quiz is deliberately not part of what a
# populated account looks like. The quiz backend and its export path still
# exist; test_domain_quiz_style.py covers them directly.


async def create_occasion_and_style(client, token) -> dict[str, Any]:
    occasion = ok(await client.post(
        "/api/v2/occasions",
        headers=auth(token),
        json={
            "occasion_key": "office",
            "title": "Monday at the office",
            "event_date": JOURNEY_DATE.isoformat(),
            "time_of_day": "morning",
        },
    ))
    styling = ok(await client.post(
        "/api/v2/style/occasion",
        headers=auth(token),
        json={"occasion_id": occasion["id"]},
    ))
    return {"occasion": occasion, "styling": styling}


async def evaluate_a_purchase(client, token) -> dict[str, Any]:
    return ok(await client.post(
        "/api/v2/shopping/evaluate",
        headers=auth(token),
        json={
            "source": "manual",
            "price": "2400.00",
            "currency": "INR",
            "item": {
                "category": "wardrobe", "display_name": "Another White Shirt",
                "subcategory": "shirt", "colour": "white", "fabric": "cotton",
                "formality": "smart_casual",
            },
        },
    ))


async def plan_the_day(client, token) -> dict[str, Any]:
    ok(await client.post(
        "/api/v2/today/weather",
        headers=auth(token),
        json={"for_date": JOURNEY_DATE.isoformat(), "condition": "humid", "humidity": 78},
    ))
    ok(await client.post(
        "/api/v2/today/events",
        headers=auth(token),
        json={
            "title": "Team review",
            "starts_at": datetime(2026, 2, 16, 10, 0, tzinfo=UTC).isoformat(),
            "occasion_key": "office",
        },
    ))
    today = ok(await client.get(
        f"/api/v2/today?plan_date={JOURNEY_DATE.isoformat()}", headers=auth(token)
    ))
    week = ok(await client.post(
        "/api/v2/planner/week/generate",
        headers=auth(token),
        json={"week_start": JOURNEY_DATE.isoformat()},
    ))
    return {"today": today, "week": week}


async def build_routines(client, token) -> dict[str, Any]:
    return ok(await client.post(
        "/api/v2/routines/generate",
        headers=auth(token),
        json={"kinds": ["morning", "wash_day"], "climate": "humid"},
    ))


async def set_a_goal(client, token) -> dict[str, Any]:
    return ok(await client.post(
        "/api/v2/goals",
        headers=auth(token),
        json={
            "kind": "routine",
            "title": "Stay consistent this month",
            "metric_key": "routine_consistency",
            "target_value": 0.8,
            "starts_on": JOURNEY_DATE.isoformat(),
            "target_date": (JOURNEY_DATE + timedelta(days=30)).isoformat(),
        },
    ))


async def teach_memory_a_fact(client, token) -> dict[str, Any]:
    """Give feedback, which is how the app learns a controlled-memory fact."""
    return ok(await client.post(
        "/api/v2/memory/feedback",
        headers=auth(token),
        json={
            "subject_type": "colour",
            "signal": "liked",
            "subject_label": "deep teal",
        },
    ))


async def populate_every_domain(client, token) -> dict[str, Any]:
    """Walk one account through every active product domain over the API."""
    created: dict[str, Any] = {}
    await complete_profile_and_onboarding(client, token)
    created["consent"] = await grant_photo_consent(client, token)
    created["inventory"] = await stock_seven_categories(client, token)
    created["media"] = await upload_inventory_image(
        client, token, created["inventory"]["wardrobe"][0]
    )
    ok(await client.post(
        f"/api/v2/inventory/items/{created['inventory']['wardrobe'][0]}/usage",
        headers=auth(token),
        json={"used_on": JOURNEY_DATE.isoformat()},
    ))
    created["scan"] = await run_scan(client, token)
    created["styling"] = await create_occasion_and_style(client, token)
    created["shopping"] = await evaluate_a_purchase(client, token)
    created["planning"] = await plan_the_day(client, token)
    created["routines"] = await build_routines(client, token)
    created["goal"] = await set_a_goal(client, token)
    created["memory"] = await teach_memory_a_fact(client, token)
    return created
