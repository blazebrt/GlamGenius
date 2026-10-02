"""Step 17 — the consumer Product Result, pinned byte for byte across the extraction.

Step 17 moves the canonical grading sequence (grade, publication boundary,
presentation) out of the Product Result route into one shared domain function,
:func:`app.domains.product.truth.grade`, so the B2B Product Truth API can call
the very same authority without a scanning device. That is a refactor of the
most important consumer surface in the product, and "the code looks the same"
is not evidence that nothing changed.

The golden file next to this module was captured on the exact pre-Step-17
``main`` (``261b157c``), before any route code was touched, by running the
scenarios below through the real routes. This module replays the same
scenarios on the current code and requires the same answers: every field of
the Product Result and of the Purchase OS scan check, for an ordinary graded
scan, a physical pack the device itself confirmed, reference mode, an Open
Food Facts-only product, an unknown barcode, an identity-only label, a
culinary ingredient and a ruleset whose evidence is not yet published.

Only identifiers and timestamps minted per run are normalised. Nothing about
the decision, the grade, the factors, the evidence, the pack authority, the
official records, the community layer, the alternative or the value envelope
is.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import auth
from tests.test_step6a_comparable_alternative import (
    CANDIDATE_B,
    CURRENT,
    INGREDIENTS_B,
    PANEL_A,
    PANEL_B,
    confirm_label_through_api,
    label_facts,
    no_off_network,  # noqa: F401 - re-exported fixture
    off_clean,  # noqa: F401 - re-exported fixture
    published_rules,  # noqa: F401 - re-exported fixture
    register_device,
    seed_candidate,
    seed_current,
    seed_label,
    seed_off,
)

pytestmark = pytest.mark.asyncio

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "step17_consumer_product_result_golden.json"

OWN_PACK = "8901000000020"
OFF_ONLY = "8901000000021"
UNKNOWN = "8901000000022"
IDENTITY_ONLY = "8901000000023"
GHEE = "8901000000024"
UNPUBLISHED = "8901000000025"

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def normalise(value: Any) -> Any:
    """Replace what a run mints (row ids, clock readings) and nothing else."""
    if isinstance(value, dict):
        return {key: normalise(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalise(item) for item in value]
    if isinstance(value, str):
        if _UUID.match(value):
            return "<uuid>"
        if _TIMESTAMP.match(value):
            return "<timestamp>"
    return value


async def _get(app_client, url: str, headers: dict, params: dict | None = None) -> dict:
    response = await app_client.get(url, headers=headers, params=params or {})
    assert response.status_code == 200, response.text
    return response.json()


async def _both(app_client, barcode: str, headers: dict, *, physical: bool) -> dict:
    params = {} if physical else {"physical_pack_context": "false"}
    return {
        "product_result": await _get(app_client, f"/api/v2/scan/verdict/{barcode}", headers, params),
        "purchase_check": await _get(
            app_client, f"/api/v2/scan/verdict/{barcode}/purchase-check", headers, params,
        ),
    }


async def capture_published(app_client, registered_supabase_user) -> dict[str, Any]:
    """Every scenario that runs under a fully published ruleset."""
    stranger = await register_device(app_client)
    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oats", ingredients=INGREDIENTS_B, panel=PANEL_B,
    )
    token, account_id = await registered_supabase_user()
    own_device = await register_device(app_client)
    await confirm_label_through_api(
        app_client, own_device, token, account_id, OWN_PACK,
        label_facts(product_name="Own Pack Oats", ingredients="whole grain oats", panel=PANEL_A),
    )
    await seed_off(OFF_ONLY, name="Catalogue Only", brands="Catalogue Brand")
    await seed_label(IDENTITY_ONLY, {"product_name": "Name Only", "brand": "Label Brand"})
    await seed_label(GHEE, label_facts(
        product_name="Ghee", brand="Dairy Co", ingredients="ghee",
        panel={"energy_kcal": "900", "saturated_fat_g": "60", "protein_g": "0"},
    ))
    own_headers = {**own_device, **auth(token)}
    return {
        "graded_reference_capture": await _both(app_client, CURRENT, stranger, physical=True),
        "graded_reference_mode": await _both(app_client, CURRENT, stranger, physical=False),
        "own_pack_physical": await _both(app_client, OWN_PACK, own_headers, physical=True),
        "own_pack_reference_mode": await _both(app_client, OWN_PACK, own_headers, physical=False),
        "off_only": await _both(app_client, OFF_ONLY, stranger, physical=True),
        "unknown": await _both(app_client, UNKNOWN, stranger, physical=True),
        "identity_only_label": await _both(app_client, IDENTITY_ONLY, stranger, physical=True),
        "culinary_ingredient": await _both(app_client, GHEE, stranger, physical=True),
    }


async def capture_unpublished(app_client) -> dict[str, Any]:
    """The production default before any grading rule finishes its lifecycle."""
    stranger = await register_device(app_client)
    await seed_label(UNPUBLISHED, label_facts(
        product_name="Unpublished Oats", ingredients="whole grain oats", panel=PANEL_A,
    ))
    return {"unpublished_ruleset": await _both(app_client, UNPUBLISHED, stranger, physical=True)}


def _golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


async def test_the_consumer_product_result_is_unchanged_under_a_published_ruleset(
    app_client, db_clean, off_clean, published_rules, no_off_network, registered_supabase_user,  # noqa: F811
):
    captured = normalise(await capture_published(app_client, registered_supabase_user))
    golden = _golden()
    for name, answer in captured.items():
        assert answer == golden[name], f"consumer answer for {name!r} drifted from the pre-Step-17 capture"


async def test_the_consumer_product_result_is_unchanged_under_an_unpublished_ruleset(
    app_client, db_clean, off_clean, no_off_network,  # noqa: F811
):
    captured = normalise(await capture_unpublished(app_client))
    assert captured["unpublished_ruleset"] == _golden()["unpublished_ruleset"]


def test_the_golden_capture_covers_every_scenario_and_was_not_hand_edited():
    """Every scenario is present, each with both surfaces, and none is empty.

    The capture is the evidence; a scenario missing from it would pass the two
    replays above vacuously.
    """
    golden = _golden()
    assert set(golden) == {
        "graded_reference_capture", "graded_reference_mode", "own_pack_physical",
        "own_pack_reference_mode", "off_only", "unknown", "identity_only_label",
        "culinary_ingredient", "unpublished_ruleset",
    }
    for name, answer in golden.items():
        assert set(answer) == {"product_result", "purchase_check"}, name
        assert answer["product_result"] and answer["purchase_check"], name
    # The scenarios mean what they say: these facts were true on the base.
    assert golden["graded_reference_capture"]["product_result"]["grade"] == "C"
    assert golden["graded_reference_capture"]["product_result"]["physical_pack_context"] is False
    assert golden["own_pack_physical"]["product_result"]["physical_pack_context"] is True
    assert golden["own_pack_reference_mode"]["product_result"]["physical_pack_context"] is False
    assert golden["off_only"]["product_result"]["facts_provenance"] == "open_food_facts"
    assert golden["culinary_ingredient"]["product_result"]["outcome"] == "not_graded"
    assert golden["unpublished_ruleset"]["product_result"]["grade"] is None
    assert golden["graded_reference_capture"]["product_result"]["alternative"]["status"] == "available"
    assert golden["graded_reference_capture"]["purchase_check"]["decision"]["verdict"] == "wait"
    # Nothing per-run survived into the file.
    text = GOLDEN.read_text(encoding="utf-8")
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)
    assert str(uuid.UUID(int=0)) not in text
