import uuid

import pytest
from app.api.v2.media import ALLOWED_PURPOSES
from app.domains.product.complaints import (
    FSSAI_CONSUMER_GRIEVANCE_URL,
    REQUEST_TEMPLATES,
    missing_preparation_fields,
    prepared_fields,
)
from app.domains.product.models import FssaiComplaintHandoff
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select, text

from tests.conftest import auth
from tests.test_step12a_label_change_authority import NUTRITION, _confirm


def test_fssai_handoff_is_a_structured_request_not_an_accusation():
    assert "request" in REQUEST_TEMPLATES["food_safety"].lower()
    assert "unsafe" not in " ".join(REQUEST_TEMPLATES.values()).lower()
    assert FSSAI_CONSUMER_GRIEVANCE_URL.startswith("https://foscos.fssai.gov.in/")


def test_fssai_handoff_never_invents_missing_pack_facts():
    fields = prepared_fields({"product_name": "Pack", "brand": "Brand"}, None)

    assert missing_preparation_fields(fields) == [
        "batch_number",
        "fssai_licence",
        "photo_asset_id",
    ]


# ---------------------------------------------------------------------------
# The live routes. The two tests above cover ``prepared_fields`` as a function;
# these cover the customer-facing paths that call it, which had no route-level
# coverage and which a single unreadable stored row turned into a public 500.
# ---------------------------------------------------------------------------
#: Shapes JSONB can hold that are not fact objects. Each reaches ``.get()`` in
#: a different frame, so both are exercised everywhere.
UNREADABLE_FACTS = {
    "json-array": "'[\"not\",\"an\",\"object\"]'",
    "json-string": '\'"broken"\'',
}

#: Everything a handoff needs before a person may be sent to the FSSAI portal.
COMPLETE_PACK = {
    "product_name": "Observed Biscuit",
    "brand": "Observed Brand",
    "batch_number": "B-2291",
    "fssai_licence": "10012345678901",
}


@pytest.fixture
async def device(app_client):
    response = await app_client.post(
        "/api/v2/scan/device",
        json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert response.status_code == 201, response.text
    return {"X-Device-Token": response.json()["token"]}


async def _owned_photo(app_client, token) -> str:
    """A real MediaAsset owned by this caller, through the real upload route."""
    from tests.conftest import PNG_1PX

    response = await app_client.post(
        "/api/v2/media/upload", headers=auth(token),
        files={"file": ("pack.png", PNG_1PX + uuid.uuid4().bytes, "image/png")},
        data={"purpose": sorted(ALLOWED_PURPOSES)[0]},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def _make_unreadable(barcode: str, kind: str) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            text(  # noqa: S608 - fixed literals, not interpolated input
                f"UPDATE product_label_snapshots SET facts = CAST({UNREADABLE_FACTS[kind]} AS jsonb) "
                "WHERE barcode = :barcode"
            ),
            {"barcode": barcode},
        )
        await session.commit()


@pytest.mark.parametrize("kind", list(UNREADABLE_FACTS))
async def test_preview_states_the_pack_facts_are_missing_rather_than_failing(
    db_clean, off_clean, app_client, device, registered_supabase_user, kind,
):
    """An unreadable observation supplies no pack facts, and says so.

    ``prepared_fields`` calls ``.get()``. Handed a JSONB array it raised, and
    the raise reached the customer as a 500 on a route whose whole purpose is
    to show a person what is and is not known before they decide to complain.
    """
    barcode = "8900000000508"
    token, account_id = await registered_supabase_user()
    await _confirm(app_client, device, token, account_id, barcode,
                   {**COMPLETE_PACK, "ingredients_text": "Water", **NUTRITION})
    await _make_unreadable(barcode, kind)

    response = await app_client.post(
        "/api/v2/reports/fssai/preview", headers={**device, **auth(token)},
        json={"barcode": barcode, "reason": "food_safety"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready_for_official_handoff"] is False
    assert set(body["missing_fields"]) == {
        "product_name", "brand", "batch_number", "fssai_licence", "photo_asset_id",
    }
    assert all(value is None for value in body["pack_fields"].values())
    # Never filled from the catalogue: Open Food Facts cannot state a batch, a
    # licence, or what this particular pack said.
    assert "open_food_facts" not in response.text
    for invented in ("Northstar", "Catalogue"):
        assert invented not in response.text
    # Still a request, still not filed, still not an accusation.
    assert body["filing_status"] == "not_filed"
    assert "request" in body["request_text"].lower()


@pytest.mark.parametrize("kind", list(UNREADABLE_FACTS))
async def test_confirm_refuses_in_the_controlled_way_and_records_nothing(
    db_clean, off_clean, app_client, device, registered_supabase_user, kind,
):
    """A valid owned photo is not enough when the pack facts cannot be read.

    The refusal must be the existing governed one — not a 500, and not a
    handoff row assembled from facts nobody can read.
    """
    barcode = "8900000000515"
    token, account_id = await registered_supabase_user()
    await _confirm(app_client, device, token, account_id, barcode,
                   {**COMPLETE_PACK, "ingredients_text": "Water", **NUTRITION})
    photo = await _owned_photo(app_client, token)
    await _make_unreadable(barcode, kind)

    response = await app_client.post(
        "/api/v2/reports/fssai/confirm", headers={**device, **auth(token)},
        json={"barcode": barcode, "reason": "food_safety", "photo_asset_id": photo},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "pack_fields_missing"
    assert set(response.json()["detail"]["fields"]) == {
        "product_name", "brand", "batch_number", "fssai_licence",
    }
    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(FssaiComplaintHandoff))).scalars().all()
    assert rows == []


async def test_a_readable_pack_still_prepares_and_confirms(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """The happy path, end to end, so the refusals above are not the only story."""
    barcode = "8900000000522"
    token, account_id = await registered_supabase_user()
    await _confirm(app_client, device, token, account_id, barcode,
                   {**COMPLETE_PACK, "ingredients_text": "Water", **NUTRITION})
    photo = await _owned_photo(app_client, token)

    preview = await app_client.post(
        "/api/v2/reports/fssai/preview", headers={**device, **auth(token)},
        json={"barcode": barcode, "reason": "label_information", "photo_asset_id": photo},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["missing_fields"] == []
    assert preview.json()["ready_for_official_handoff"] is True
    assert preview.json()["pack_fields"]["product_name"] == COMPLETE_PACK["product_name"]
    assert preview.json()["filing_status"] == "not_filed"

    confirmed = await app_client.post(
        "/api/v2/reports/fssai/confirm", headers={**device, **auth(token)},
        json={"barcode": barcode, "reason": "label_information", "photo_asset_id": photo},
    )
    assert confirmed.status_code == 201, confirmed.text
    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(FssaiComplaintHandoff))).scalars().all()
    assert len(rows) == 1
    assert rows[0].product_name == COMPLETE_PACK["product_name"]


async def test_a_product_with_no_observation_at_all_behaves_as_before(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """No snapshot and an unreadable snapshot are the same answer, by design.

    Both mean the pack facts are unavailable. Neither is an error, and neither
    is filled from anywhere else.
    """
    token, _account_id = await registered_supabase_user()

    response = await app_client.post(
        "/api/v2/reports/fssai/preview", headers={**device, **auth(token)},
        json={"barcode": "8900000000539", "reason": "packaging"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["ready_for_official_handoff"] is False
    assert set(response.json()["missing_fields"]) == {
        "product_name", "brand", "batch_number", "fssai_licence", "photo_asset_id",
    }
