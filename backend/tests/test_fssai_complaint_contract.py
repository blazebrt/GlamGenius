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


# ---------------------------------------------------------------------------
# Field-level pack-text validity
#
# The top-level question — "is this stored ``facts`` value a fact object at
# all?" — is not asked here. It belongs to
# ``service.readable_label_facts()``, which both routes already call before
# reaching this layer, and asking it twice would create a second authority
# that could drift from the real one.
#
# What is left is narrower and is this layer's own: a perfectly readable
# mapping can still hold ``{"batch_number": 12345}``. A number is not
# something a pack printed, and rendering it as text would put a string nobody
# read onto a document a person is about to take to a regulator.
# ---------------------------------------------------------------------------
#: Values a JSONB object can legitimately hold that are not printed text. The
#: sentinel makes any accidental ``str()`` coercion visible in a response body.
NON_TEXT_VALUES = {
    "list": ["SENTINEL-7788"],
    "dict": {"name": "SENTINEL-7788"},
    "int": 7788,
    "float": 77.88,
    "bool": True,
    "null": None,
}

#: A readable mapping whose every required pack field is unusable.
FIELD_LEVEL_MALFORMED = {
    "product_name": ["SENTINEL-NAME"],
    "brand": {"name": "SENTINEL-BRAND"},
    "batch_number": 778899,
    "fssai_licence": ["SENTINEL-LICENCE"],
}


async def _make_field_malformed(barcode: str) -> None:
    """Keep the facts a readable object; make the pack values non-text.

    Deliberately not the same corruption as ``_make_unreadable``. That one
    breaks the top level and is already refused upstream. This row sails
    through ``readable_label_facts()`` exactly as it should — it *is* a fact
    object — and only the field boundary can catch it.
    """
    import json

    async with get_sessionmaker()() as session:
        await session.execute(
            text(
                "UPDATE product_label_snapshots SET facts = CAST(:facts AS jsonb) "
                "WHERE barcode = :barcode"
            ),
            {"facts": json.dumps({**FIELD_LEVEL_MALFORMED, "ingredients_text": "Water"}),
             "barcode": barcode},
        )
        await session.commit()


@pytest.mark.parametrize("kind", list(NON_TEXT_VALUES))
def test_non_text_values_never_become_prepared_pack_fields(kind):
    """A. No JSON type other than a string may survive, and none is coerced."""
    value = NON_TEXT_VALUES[kind]
    fields = prepared_fields(
        {"product_name": value, "brand": value, "batch_number": value,
         "fssai_licence": value},
        "photo-id",
    )

    assert fields == {
        "product_name": None, "brand": None, "batch_number": None,
        "fssai_licence": None, "photo_asset_id": "photo-id",
    }
    # Not "falsy, therefore reported missing" — actually absent, with no
    # str() of the original anywhere in the prepared output.
    assert "SENTINEL" not in repr(fields)
    assert "7788" not in repr(fields)
    assert missing_preparation_fields(fields) == [
        "product_name", "brand", "batch_number", "fssai_licence",
    ]


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n  \n"])
def test_blank_text_is_missing(blank):
    """B. A field that states nothing is absent, not present-and-empty."""
    fields = prepared_fields(
        {"product_name": blank, "brand": blank, "batch_number": blank,
         "fssai_licence": blank},
        None,
    )

    assert all(value is None for key, value in fields.items() if key != "photo_asset_id")
    assert missing_preparation_fields(fields) == [
        "product_name", "brand", "batch_number", "fssai_licence", "photo_asset_id",
    ]


def test_printed_text_is_normalized_not_altered():
    """C. Trimming transcription whitespace is the only change permitted."""
    fields = prepared_fields(
        {
            "product_name": "  Pack Name  ",
            "brand": " Brand ",
            "batch_number": " B-1 ",
            "fssai_licence": " 10012345678901 ",
        },
        None,
    )

    assert fields["product_name"] == "Pack Name"
    assert fields["brand"] == "Brand"
    assert fields["batch_number"] == "B-1"
    assert fields["fssai_licence"] == "10012345678901"
    # Inner spacing, case and punctuation are the pack's, not ours.
    assert prepared_fields({"brand": "  Dr.  Oetker  "}, None)["brand"] == "Dr.  Oetker"


def test_the_product_name_alias_survives_an_unusable_preferred_key():
    """D. A malformed ``product_name`` must not mask a good ``name`` beside it.

    The alias is reached on *unusable* text, not merely on an absent key, so
    the documented fallback keeps working through this new boundary.
    """
    assert prepared_fields(
        {"product_name": ["SENTINEL"], "name": " Real Pack Name "}, None,
    )["product_name"] == "Real Pack Name"
    assert prepared_fields({"product_name": "   ", "name": "Real"}, None)["product_name"] == "Real"
    assert prepared_fields({"name": "Only Alias"}, None)["product_name"] == "Only Alias"
    # And a malformed alias does not resurrect a malformed preferred key.
    assert prepared_fields({"product_name": 1, "name": ["x"]}, None)["product_name"] is None


async def test_preview_fails_soft_on_field_level_malformed_values(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """E. The row is readable; only its values are not. The page stays up."""
    barcode = "8900000000546"
    token, account_id = await registered_supabase_user()
    await _confirm(app_client, device, token, account_id, barcode,
                   {**COMPLETE_PACK, "ingredients_text": "Water", **NUTRITION})
    await _make_field_malformed(barcode)

    response = await app_client.post(
        "/api/v2/reports/fssai/preview", headers={**device, **auth(token)},
        json={"barcode": barcode, "reason": "label_information"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready_for_official_handoff"] is False
    assert set(body["missing_fields"]) == {
        "product_name", "brand", "batch_number", "fssai_licence", "photo_asset_id",
    }
    assert all(value is None for value in body["pack_fields"].values())
    # Nothing was stringified into a document a person may take to a regulator.
    assert "SENTINEL" not in response.text
    assert "778899" not in response.text
    # And nothing was filled in from the catalogue instead.
    assert "open_food_facts" not in response.text
    assert body["filing_status"] == "not_filed"


async def test_confirm_refuses_field_level_malformed_values(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """F. A real owned photo is not enough when no pack field is readable text."""
    barcode = "8900000000553"
    token, account_id = await registered_supabase_user()
    await _confirm(app_client, device, token, account_id, barcode,
                   {**COMPLETE_PACK, "ingredients_text": "Water", **NUTRITION})
    photo = await _owned_photo(app_client, token)
    await _make_field_malformed(barcode)

    response = await app_client.post(
        "/api/v2/reports/fssai/confirm", headers={**device, **auth(token)},
        json={"barcode": barcode, "reason": "label_information", "photo_asset_id": photo},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "pack_fields_missing"
    assert set(detail["fields"]) == {
        "product_name", "brand", "batch_number", "fssai_licence",
    }
    assert "SENTINEL" not in response.text
    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(FssaiComplaintHandoff))).scalars().all()
    assert rows == []
