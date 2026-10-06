"""Step 13 — the supplement label photo bridge: photo in, drafts out, nothing more.

Against PostgreSQL 16 and the real routes, with the AI provider stubbed (no live
model call). The bridge may only transcribe printed text into drafts on the
customer's own supplement; everything else it could be tempted into — trusting
a model's chemistry, confirming on the customer's behalf, duplicating on retry,
reading another account's photo, touching the food scan stack — is refused here.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.domains.media.models import MediaAsset
from app.domains.media.storage import get_storage
from app.domains.off.models import OffProduct
from app.domains.off.store import get_off_sessionmaker
from app.domains.privacy import deletion_service
from app.domains.product.models import LabelSnapshot, ProductRecord, ProductWatch, ScanDecisionEvent, ScanEvent
from app.domains.supplements import photo
from app.domains.supplements import strings as supplement_copy
from app.domains.supplements.knowledge_loader import load
from app.domains.supplements.models import SupplementLabelComponent
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import delete, func, select

from tests.conftest import auth
from tests.image_fixtures import distinct_png
from tests.journey import ok
from tests.test_step13_supplements import _publish_form

TRANSCRIPTION = {
    "components": [
        {"raw_name": "Magnesium oxide", "amount": "500", "unit": "mg", "serving_text": "Each tablet contains"},
        {"raw_name": "Zinc", "amount": "10", "unit": "mg"},
        {"raw_name": "Vitamin D3"},
    ],
    "confidence": 0.82,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _supplement(client, headers, name: str = "Night magnesium") -> str:
    return ok(await client.post(
        "/api/v2/inventory/items", headers=headers,
        json={"category": "supplements", "display_name": name, "details": {"supplement_name": name}},
    ))["id"]


async def _upload(client, headers, data: bytes | None = None) -> str:
    response = await client.post(
        "/api/v2/media/upload", headers=headers,
        files={"file": ("label.png", data if data is not None else distinct_png(), "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def _transcribe(client, headers, item_id: str, media_id: str, request_id: str | None = None):
    return await client.post(
        f"/api/v2/supplements/items/{item_id}/label-photo/transcribe", headers=headers,
        json={"media_asset_id": media_id, "client_request_id": request_id or uuid.uuid4().hex},
    )


async def _rows(account_id) -> list[SupplementLabelComponent]:
    async with get_sessionmaker()() as session:
        return list((await session.execute(
            select(SupplementLabelComponent).where(SupplementLabelComponent.account_id == account_id)
            .order_by(SupplementLabelComponent.client_mutation_id)
        )).scalars().all())


def _say(provider, payload) -> None:
    provider.text = payload if isinstance(payload, str) else json.dumps(payload)


async def _detail(client, headers, item_id: str) -> dict:
    return ok(await client.get(f"/api/v2/supplements/items/{item_id}", headers=headers))


def _component(detail: dict, name: str) -> dict:
    return next(row for row in detail["components"] if row["printed"]["name"] == name)


# ---------------------------------------------------------------------------
# 1, 7, 8. Owned supplement + owned photo -> drafts, as printed, never confirmed
# ---------------------------------------------------------------------------
async def test_an_owned_photo_becomes_drafts_with_server_owned_provenance(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers)
    media = await _upload(app_client, headers)
    _say(fake_provider, TRANSCRIPTION)

    body = ok(await _transcribe(app_client, headers, item, media))
    assert body["status"] == "created"
    assert [row["raw_name"] for row in body["label_facts"]] == ["Magnesium oxide", "Zinc", "Vitamin D3"]
    for row in body["label_facts"]:
        assert row["source"] == "photo_extracted" and row["verification_state"] == "draft"
    # Missing stays missing: nothing was guessed for Zinc's serving text or D3's amount.
    by_name = {row["raw_name"]: row for row in body["label_facts"]}
    oxide = by_name["Magnesium oxide"]
    assert (oxide["amount"], oxide["unit"], oxide["serving_text"]) == ("500", "mg", "Each tablet contains")
    assert by_name["Zinc"]["serving_text"] is None
    assert by_name["Vitamin D3"]["amount"] is None and by_name["Vitamin D3"]["unit"] is None

    rows = await _rows(account)
    assert len(rows) == 3
    for row in rows:
        assert row.source == "photo_extracted" and row.verification_state == "draft"
        assert row.source_ai_run_id is not None
        assert row.model_version == "fake-model"
        assert row.prompt_version == photo.PROMPT_VERSION and row.schema_version == photo.SCHEMA_VERSION
        assert row.confidence == pytest.approx(0.82)
        assert row.client_mutation_id.startswith(photo.KEY_PREFIX)
    # The server, not the model, decided identity — from the printed name only.
    assert {row.raw_name: row.canonical_component_key for row in rows} == {
        "Magnesium oxide": "magnesium", "Zinc": "zinc", "Vitamin D3": "vitamin d",
    }

    rendered = json.dumps(body)
    for internal in (media, str(rows[0].source_ai_run_id), "storage_key", "ai_run_id", "model_version", "account_id"):
        assert internal not in rendered, internal


async def test_confidence_never_confirms_anything(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers)
    _say(fake_provider, {"components": [{"raw_name": "Zinc oxide", "amount": "15", "unit": "mg"}], "confidence": 1.0})
    ok(await _transcribe(app_client, headers, item, await _upload(app_client, headers)))
    assert [row.verification_state for row in await _rows(account)] == ["draft"]


@pytest.mark.parametrize(("printed", "stored"), [
    ("500", Decimal("500")), ("0.5", Decimal("0.5")), ("1,000", None), ("1.000,5", None),
    ("500mg", None), ("~200", None), ("", None), (None, None),
])
def test_only_a_plain_printed_number_becomes_an_amount(printed, stored):
    assert photo.printed_amount(printed) == stored


# ---------------------------------------------------------------------------
# 2, 3, 4. Ownership and media
# ---------------------------------------------------------------------------
async def test_another_accounts_supplement_cannot_be_targeted(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    item_a = await _supplement(app_client, auth(token_a))
    media_b = await _upload(app_client, auth(token_b))
    _say(fake_provider, TRANSCRIPTION)
    response = await _transcribe(app_client, auth(token_b), item_a, media_b)
    assert response.status_code == 404
    assert fake_provider.calls == 0
    assert await _rows(account_a) == [] and await _rows(account_b) == []


async def test_another_accounts_photo_cannot_be_read(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    token_a, account_a = await registered_supabase_user()
    token_b, _account_b = await registered_supabase_user()
    item_a = await _supplement(app_client, auth(token_a))
    media_b = await _upload(app_client, auth(token_b))
    _say(fake_provider, TRANSCRIPTION)
    response = await _transcribe(app_client, auth(token_a), item_a, media_b)
    assert response.status_code == 404
    assert fake_provider.calls == 0
    assert await _rows(account_a) == []


async def test_a_deleted_photo_cannot_be_read(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers)
    media = await _upload(app_client, headers)
    assert (await app_client.delete(f"/api/v2/media/{media}", headers=headers)).status_code in (200, 202, 204)
    response = await _transcribe(app_client, headers, item, media)
    assert response.status_code == 404
    assert fake_provider.calls == 0 and await _rows(account) == []


async def test_bytes_that_are_not_an_image_are_refused_before_any_model_call(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers)
    data = b"%PDF-1.7 definitely not an image"
    key = f"test/{uuid.uuid4().hex}.png"
    await get_storage().put(key, data, "image/png")
    async with get_sessionmaker()() as session:
        asset = MediaAsset(
            account_id=account, storage_backend="local", storage_key=key, content_type="image/png",
            byte_size=len(data), sha256=hashlib.sha256(data).hexdigest(), purpose="inventory_item",
        )
        session.add(asset)
        await session.commit()
        media = str(asset.id)
    response = await _transcribe(app_client, headers, item, media)
    assert response.status_code == 415
    assert fake_provider.calls == 0 and await _rows(account) == []


@pytest.mark.parametrize("state", ["archived", "not_a_supplement"])
async def test_an_archived_or_non_supplement_item_cannot_use_the_route(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root, state,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    if state == "archived":
        item = await _supplement(app_client, headers)
        assert (await app_client.delete(f"/api/v2/inventory/items/{item}", headers=headers)).status_code == 200
    else:
        item = ok(await app_client.post(
            "/api/v2/inventory/items", headers=headers, json={"category": "beauty", "display_name": "Serum"},
        ))["id"]
    response = await _transcribe(app_client, headers, item, await _upload(app_client, headers))
    assert response.status_code == 404
    assert fake_provider.calls == 0 and await _rows(account) == []


# ---------------------------------------------------------------------------
# 5, 6. Malformed output and model judgement are refused whole
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("payload", [
    "not json at all",
    {"components": "Magnesium oxide 500 mg"},
    {"components": [{"amount": "500", "unit": "mg"}]},                                   # no name
    {"components": [{"raw_name": "Magnesium", "compound_form": "magnesium citrate"}]},   # a form the model chose
    {"components": [{"raw_name": "Magnesium", "canonical_nutrient": "magnesium"}]},
    {"components": [{"raw_name": "Ferrous sulphate", "hydration": "heptahydrate"}]},
    {"components": [{"raw_name": "Zinc", "amount": "10", "unit": "mg", "dose_recommendation": "1 daily"}]},
    {"components": [{"raw_name": "Zinc"}], "benefit": "immunity"},
    {"components": [{"raw_name": "Zinc"}], "safety": "safe for adults"},
    {"components": [{"raw_name": "Zinc"}], "take_with_food": True},
    {"components": [{"raw_name": "Zinc"}], "confidence": 3},
])
async def test_malformed_or_judgemental_output_writes_nothing(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root, payload,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers)
    _say(fake_provider, payload)
    response = await _transcribe(app_client, headers, item, await _upload(app_client, headers))
    assert response.status_code == 503
    assert await _rows(account) == []
    # The refusal names no internal AI run, even though the gateway recorded one.
    from app.domains.ai_gateway.models import AIRun

    async with get_sessionmaker()() as session:
        run_ids = [str(run_id) for run_id in (await session.execute(select(AIRun.id))).scalars().all()]
    assert run_ids, "the gateway records failed runs"
    assert not any(run_id in response.text for run_id in run_ids)


async def test_a_mixed_valid_and_invalid_transcription_is_not_partially_kept(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers)
    _say(fake_provider, {"components": [
        {"raw_name": "Zinc", "amount": "10", "unit": "mg"},
        {"raw_name": "Magnesium", "compound_form": "magnesium citrate"},
    ]})
    assert (await _transcribe(app_client, headers, item, await _upload(app_client, headers))).status_code == 503
    assert await _rows(account) == []


# ---------------------------------------------------------------------------
# 9, 10, 11, 13. Drafts drive nothing; confirmation keeps photo provenance
# ---------------------------------------------------------------------------
async def test_drafts_drive_nothing_until_confirmed_and_confirmation_keeps_the_source(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    async with get_sessionmaker()() as session:
        await load(session)
        await session.commit()
    await _publish_form("magnesium oxide")

    token, _account = await registered_supabase_user()
    headers = auth(token)
    photographed = await _supplement(app_client, headers, "Photographed")
    typed = await _supplement(app_client, headers, "Typed")
    ok(await app_client.post(
        f"/api/v2/supplements/items/{typed}/label-facts", headers=headers,
        json={"raw_name": "Magnesium oxide", "amount": "250", "unit": "mg"},
    ))
    _say(fake_provider, {"components": [{"raw_name": "Magnesium oxide", "amount": "500", "unit": "mg"}]})
    created = ok(await _transcribe(app_client, headers, photographed, await _upload(app_client, headers)))
    fact_id = created["label_facts"][0]["id"]

    detail = await _detail(app_client, headers, photographed)
    draft = _component(detail, "Magnesium oxide")
    assert draft["provenance"] == "read_from_photo_not_confirmed"
    for block in ("nutrient", "form", "package_chemistry", "published_knowledge"):
        assert draft[block] == {"status": "awaiting_confirmation"}
    assert detail["overlaps"] == []
    assert (await _detail(app_client, headers, typed))["overlaps"] == []
    assert ok(await app_client.get("/api/v2/supplements/summary", headers=headers))["overlaps"] == []

    # A customer edit keeps the photo provenance and still needs confirmation.
    edited = ok(await app_client.patch(
        f"/api/v2/supplements/items/{photographed}/label-facts/{fact_id}", headers=headers, json={"amount": "400"},
    ))
    assert edited["source"] == "photo_extracted" and edited["verification_state"] == "draft"
    assert (await _detail(app_client, headers, photographed))["overlaps"] == []

    confirmed = ok(await app_client.post(
        f"/api/v2/supplements/items/{photographed}/label-facts/{fact_id}/confirm", headers=headers,
    ))
    assert confirmed["source"] == "photo_extracted" and confirmed["verification_state"] == "confirmed"
    detail = await _detail(app_client, headers, photographed)
    row = _component(detail, "Magnesium oxide")
    assert row["provenance"] == "read_from_photo_confirmed_by_you"
    assert row["published_knowledge"]["status"] == "published"
    assert [group["product_count"] for group in detail["overlaps"]] == [2]

    # Edited again after confirmation: still the customer's confirmed photo fact.
    again = ok(await app_client.patch(
        f"/api/v2/supplements/items/{photographed}/label-facts/{fact_id}", headers=headers, json={"unit": "mg"},
    ))
    assert again["source"] == "photo_extracted" and again["verification_state"] == "confirmed"


# ---------------------------------------------------------------------------
# 12. Replay never duplicates
# ---------------------------------------------------------------------------
async def test_a_retry_returns_the_same_drafts_without_a_second_model_call(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers)
    media = await _upload(app_client, headers)
    _say(fake_provider, TRANSCRIPTION)
    first = ok(await _transcribe(app_client, headers, item, media, "retry-key-0001"))
    second = ok(await _transcribe(app_client, headers, item, media, "retry-key-0001"))
    assert second["status"] == "replayed"
    assert [row["id"] for row in second["label_facts"]] == [row["id"] for row in first["label_facts"]]
    assert fake_provider.calls == 1
    assert len(await _rows(account)) == 3

    # Confirmed, the replayed drafts still count once per product.
    for row in first["label_facts"]:
        ok(await app_client.post(f"/api/v2/supplements/items/{item}/label-facts/{row['id']}/confirm", headers=headers))
    ok(await _transcribe(app_client, headers, item, media, "retry-key-0001"))
    assert len(await _rows(account)) == 3


async def test_concurrent_identical_requests_create_one_set(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers)
    media = await _upload(app_client, headers)
    _say(fake_provider, TRANSCRIPTION)
    results = await asyncio.gather(*(
        _transcribe(app_client, headers, item, media, "concurrent-key-01") for _ in range(3)
    ))
    assert all(response.status_code == 200 for response in results)
    ids = {tuple(row["id"] for row in response.json()["label_facts"]) for response in results}
    assert len(ids) == 1
    assert len(await _rows(account)) == 3


async def test_a_manual_entry_cannot_borrow_a_photo_retry_key(app_client, db_clean, registered_supabase_user):
    token, account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers)
    response = await app_client.post(
        f"/api/v2/supplements/items/{item}/label-facts", headers=headers,
        json={"raw_name": "Zinc", "client_mutation_id": f"{photo.KEY_PREFIX}forged:00"},
    )
    assert response.status_code == 422
    assert await _rows(account) == []


async def test_a_new_photo_is_a_new_operation(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers)
    _say(fake_provider, {"components": [{"raw_name": "Zinc", "amount": "10", "unit": "mg"}]})
    ok(await _transcribe(app_client, headers, item, await _upload(app_client, headers), "same-request-01"))
    ok(await _transcribe(app_client, headers, item, await _upload(app_client, headers), "same-request-01"))
    assert fake_provider.calls == 2 and len(await _rows(account)) == 2


# ---------------------------------------------------------------------------
# P2. Nothing usable is a retryable refusal, never an idempotent success
# ---------------------------------------------------------------------------
def _assert_no_label_details(response) -> None:
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "VALIDATION_FAILED"
    assert detail["reason"] == photo.NO_LABEL_DETAILS == "no_label_details"
    assert detail["retryable"] is True
    assert detail["message"] == supplement_copy.text("supplement.photo.no_label_details")
    assert "created" not in response.text and "label_facts" not in response.text


@pytest.mark.parametrize("payload", [
    {"components": [], "confidence": 0.4},
    {"components": [{"raw_name": "   "}, {"raw_name": "\t", "amount": "10", "unit": "mg"}], "confidence": 0.4},
])
async def test_a_transcription_with_nothing_usable_is_a_retryable_refusal_not_created(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root, payload,
):
    from app.domains.ai_gateway.models import AIRun
    from app.domains.beta_access.models import BetaUsageEvent

    token, account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers)
    _say(fake_provider, payload)
    _assert_no_label_details(await _transcribe(app_client, headers, item, await _upload(app_client, headers)))
    assert await _rows(account) == []
    # The provider call happened and was valid: the ledger and the hourly count
    # keep it exactly as the gateway recorded it.
    async with get_sessionmaker()() as session:
        runs = (await session.execute(select(AIRun).where(AIRun.feature == photo.FEATURE))).scalars().all()
        usage = (await session.execute(select(BetaUsageEvent).where(
            BetaUsageEvent.account_id == account, BetaUsageEvent.feature == "ai.request",
        ))).scalars().all()
    assert len(runs) == 1 and runs[0].failure_type is None and runs[0].validation_passed is True
    assert [event.idempotency_key for event in usage] == [str(runs[0].id)]


async def test_a_no_details_attempt_is_not_a_completed_operation_so_its_retry_reads_again(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers)
    media = await _upload(app_client, headers)
    _say(fake_provider, {"components": [], "confidence": 0.4})
    _assert_no_label_details(await _transcribe(app_client, headers, item, media, "lost-response-01"))
    _assert_no_label_details(await _transcribe(app_client, headers, item, media, "lost-response-01"))
    assert fake_provider.calls == 2, "no receipt exists, so the retry reads the photo again"
    # Once a read yields details, that attempt is the completed one and replays.
    _say(fake_provider, TRANSCRIPTION)
    created = ok(await _transcribe(app_client, headers, item, media, "lost-response-01"))
    assert created["status"] == "created" and len(created["label_facts"]) == 3
    replayed = ok(await _transcribe(app_client, headers, item, media, "lost-response-01"))
    assert replayed["status"] == "replayed"
    assert [row["id"] for row in replayed["label_facts"]] == [row["id"] for row in created["label_facts"]]
    assert fake_provider.calls == 3 and len(await _rows(account)) == 3


async def test_a_successful_answer_carries_a_state_and_rows_and_no_prose(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    token, _account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers)
    _say(fake_provider, TRANSCRIPTION)
    body = ok(await _transcribe(app_client, headers, item, await _upload(app_client, headers)))
    assert set(body) == {"status", "label_facts"} and body["status"] == "created" and body["label_facts"]


# ---------------------------------------------------------------------------
# 14, 15, 16. No Open Food Facts, no food scan stack, no watch
# ---------------------------------------------------------------------------
async def test_the_bridge_touches_no_scan_stack_and_no_store_a(
    app_client, db_clean, off_clean, registered_supabase_user, fake_provider, media_root,
):
    async with get_off_sessionmaker()() as off_session:
        off_session.add(OffProduct(
            barcode="8901234567800", product_name="Night magnesium", brands="OFF Brand",
            ingredients_text="Magnesium citrate 900 mg, Melatonin 5 mg", fetched_at=datetime.now(UTC),
        ))
        await off_session.commit()
    token, _account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers, "Night magnesium")
    _say(fake_provider, {"components": [{"raw_name": "Magnesium oxide", "amount": "500", "unit": "mg"}]})
    body = ok(await _transcribe(app_client, headers, item, await _upload(app_client, headers)))
    for row in body["label_facts"]:
        ok(await app_client.post(f"/api/v2/supplements/items/{item}/label-facts/{row['id']}/confirm", headers=headers))
    detail = await _detail(app_client, headers, item)
    rendered = json.dumps([body, detail])
    assert "Magnesium citrate 900 mg" not in rendered and "Melatonin" not in rendered and "OFF Brand" not in rendered
    async with get_sessionmaker()() as session:
        for model in (ScanEvent, LabelSnapshot, ProductRecord, ScanDecisionEvent, ProductWatch):
            count = (await session.execute(select(func.count()).select_from(model))).scalar_one()
            assert count == 0, model.__tablename__


def test_the_supplement_domain_imports_nothing_from_the_product_scan_stack():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "domains" / "supplements"
    for path in sorted(root.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = [node.module] if isinstance(node, ast.ImportFrom) and node.module else (
                [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            )
            for module in modules:
                assert not module.startswith(("app.domains.product", "app.domains.off", "app.domains.scan")), (
                    f"{path.name} imports {module}"
                )


# ---------------------------------------------------------------------------
# 17, 18. Privacy export and deletion
# ---------------------------------------------------------------------------
async def test_photo_drafts_export_with_provenance_and_without_internal_ids(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers)
    _say(fake_provider, TRANSCRIPTION)
    ok(await _transcribe(app_client, headers, item, await _upload(app_client, headers)))
    exported = ok(await app_client.get("/api/v2/privacy/export", headers=headers))
    rows = exported["domains"]["routines"]["supplement_label_components"]
    assert len(rows) == 3
    for row in rows:
        assert row["source"] == "photo_extracted" and row["verification_state"] == "draft"
        for internal in ("account_id", "source_ai_run_id", "model_version", "prompt_version", "client_mutation_id"):
            assert internal not in row


async def test_deleting_the_item_or_the_account_removes_photo_drafts(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root, monkeypatch,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    _say(fake_provider, TRANSCRIPTION)
    item_a1 = await _supplement(app_client, auth(token_a), "One")
    item_a2 = await _supplement(app_client, auth(token_a), "Two")
    item_b = await _supplement(app_client, auth(token_b), "B")
    ok(await _transcribe(app_client, auth(token_a), item_a1, await _upload(app_client, auth(token_a))))
    ok(await _transcribe(app_client, auth(token_a), item_a2, await _upload(app_client, auth(token_a))))
    ok(await _transcribe(app_client, auth(token_b), item_b, await _upload(app_client, auth(token_b))))

    from app.domains.inventory.models import InventoryItem

    async with get_sessionmaker()() as session:
        await session.execute(delete(InventoryItem).where(InventoryItem.id == uuid.UUID(item_a1)))
        await session.commit()
    assert {row.item_id for row in await _rows(account_a)} == {uuid.UUID(item_a2)}

    class _Admin:
        class auth:
            class admin:
                @staticmethod
                def delete_user(_uid):
                    return None

    monkeypatch.setattr(deletion_service, "get_supabase_admin", lambda: _Admin())
    assert (await app_client.delete("/api/v2/privacy/account", headers=auth(token_a))).status_code == 202
    async with get_sessionmaker()() as session:
        await deletion_service.drain_all(session)
        await session.commit()
    assert await _rows(account_a) == []
    assert len(await _rows(account_b)) == 3
