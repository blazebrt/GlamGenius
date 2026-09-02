"""Official records as the customer's phone actually receives them.

Service-level proof is not enough: the promise is that a real Product Result
response carries the right recall and nothing else, and that adding an official
record changes no part of the scientific verdict.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun, AIRunOutput
from app.domains.off.models import OffBase, OffProduct
from app.domains.off.store import create_off_schema, get_off_engine, get_off_sessionmaker
from app.domains.official_records import service as official_records
from app.domains.official_records.models import OfficialRecord, OfficialSourceFetch
from app.domains.product.models import LabelSnapshot
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth
from tests.test_official_records import LICENCE, data_row, make_export

BARCODE = "8901058000191"
OFF_ONLY = "8901058000207"
RECALL_ID = "901"
BRAND = "Northstar"
PRODUCT = "Synthetic Oat Cereal"
BATCH = "B-123"
# The operator read the public register on 1 August. The rows land in our
# database whenever the import happens to run, which is a different fact.
SOURCE_CHECKED_AT = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def off_clean():
    """Store A has its own cleanup — see the ODbL wall."""
    from sqlalchemy import text

    await create_off_schema()
    async with get_off_engine().begin() as conn:
        names = ", ".join(f'"{t.schema}"."{t.name}"' for t in reversed(OffBase.metadata.sorted_tables))
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield


@pytest_asyncio.fixture
async def device(app_client):
    response = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert response.status_code == 201, response.text
    return {"X-Device-Token": response.json()["token"]}


def label_facts(**changes):
    return {
        "product_name": PRODUCT, "brand": BRAND,
        "ingredients_text": "oats, sugar, salt",
        "nutrition_per_100g": {"sugars_g": "12", "saturated_fat_g": "3", "salt_g": "0.8"},
        "nutrition_basis": "per_100g", "net_quantity": "180 g",
        "fssai_licence": LICENCE, "batch_number": BATCH, **changes,
    }


async def confirm_label(app_client, device, token, account_id, barcode, facts):
    factory = get_sessionmaker()
    async with factory() as session:
        run = AIRun(
            account_id=account_id, feature="product_label_transcribe", provider="test",
            model="test-model", prompt_version="scan-label.v1", schema_version="scan-label.v1",
            status=AI_STATUS_SUCCEEDED, validation_passed=True,
        )
        session.add(run)
        await session.flush()
        session.add(AIRunOutput(ai_run_id=run.id, schema_version="scan-label.v1", payload=facts))
        await session.commit()
        run_id = run.id
    response = await app_client.post(
        "/api/v2/scan/label/confirm",
        headers={**device, **auth(token)},
        json={"barcode": barcode, "ai_run_id": str(run_id), "client_scan_id": uuid.uuid4().hex},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def ingest_official_record(tmp_path: Path, *, batch: str = BATCH, checked_at=SOURCE_CHECKED_AT):
    path = make_export(
        tmp_path / f"foscos-{uuid.uuid4().hex}.xlsx",
        rows=[data_row(recall_id=int(RECALL_ID), batch=batch, brand=BRAND, product=PRODUCT,
                       status="Initiated", termination="NA")],
    )
    factory = get_sessionmaker()
    async with factory() as session:
        fetch, counts = await official_records.ingest_recall_xlsx(session, path, source_checked_at=checked_at)
        await session.commit()
        return fetch, counts


async def verdict(app_client, device, barcode=BARCODE):
    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_confirmed_pack_receives_the_exact_matching_official_record(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,
):
    """A. Valid licence plus a meaningful exact batch is what earns a recall row."""
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE, label_facts())
    fetch, counts = await ingest_official_record(tmp_path)
    assert counts["records_created"] == 1

    body = await verdict(app_client, device)
    envelope = body["official_records"]
    assert [row["recall_id"] for row in envelope["records"]] == [RECALL_ID]
    assert envelope["records"][0]["match_state"] == "matched"
    assert envelope["records"][0]["batch_lot"] == BATCH
    assert envelope["records"][0]["licence"] == LICENCE
    assert envelope["authority"] == "FSSAI / FoSCoS"
    assert envelope["record_type"] == "food_recall"

    # E. The Product Result contract is unchanged by the additive envelope.
    assert body["result_contract_version"] == "v1"
    assert body["facts_provenance"] == "confirmed_label_snapshot"

    # F. The public payload carries the register's own Recall Id, never our
    #    database primary key. An internal UUID would leak a join handle and
    #    means nothing to a person reading a government record.
    factory = get_sessionmaker()
    async with factory() as session:
        record = (await session.execute(select(OfficialRecord))).scalar_one()
    serialized = str(body)
    assert str(record.id) not in serialized
    assert str(fetch.id) not in serialized
    assert set(envelope["records"][0]) == {
        "recall_id", "fbo_name", "brand_name", "product_name", "batch_lot", "licence", "reason",
        "recall_status", "recall_start_date", "recall_termination_date", "nature_of_recall",
        "source_url", "match_state", "source_last_seen_at", "seen_in_latest_successful_check",
    }


@pytest.mark.asyncio
async def test_a_superseded_label_version_does_not_carry_its_recall_forward(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,
):
    """B. The match belongs to the pack in the customer's hand, not to its history."""
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE, label_facts())
    await ingest_official_record(tmp_path)
    matched = await verdict(app_client, device)
    assert [row["recall_id"] for row in matched["official_records"]["records"]] == [RECALL_ID]

    # A reformulated pack from a different lot. Step 3 decides what a new label
    # version is — batch is an observation, not canonical content — so the
    # ingredients move too, exactly as a real repack would.
    await confirm_label(
        app_client, device, token, account_id, BARCODE,
        label_facts(batch_number="B-999", ingredients_text="oats, jaggery, salt"),
    )
    current = await verdict(app_client, device)
    assert current["label_version"]["version_number"] == 2
    assert current["official_records"]["records"] == []
    # The register still holds the record, and so does our label history; the
    # pack in the customer's hand is simply not the recalled one.
    assert current["official_records"]["last_successful_check_at"] is not None
    factory = get_sessionmaker()
    async with factory() as session:
        versions = (await session.execute(
            select(LabelSnapshot.version_number).where(LabelSnapshot.barcode == BARCODE)
            .order_by(LabelSnapshot.version_number)
        )).scalars().all()
        assert versions == [1, 2]
        assert (await session.execute(select(OfficialRecord))).scalar_one().external_record_id == RECALL_ID


@pytest.mark.asyncio
async def test_open_food_facts_identity_alone_never_produces_an_official_match(
    db_clean, off_clean, app_client, device, tmp_path,
):
    """C. Store A cannot supply a licence or a batch, so it cannot match a recall."""
    factory = get_off_sessionmaker()
    async with factory() as session:
        session.add(OffProduct(
            barcode=OFF_ONLY, product_name=PRODUCT, brands=BRAND,
            ingredients_text="oats, sugar, salt",
            nutriments={"sugars_100g": 12.0, "salt_100g": 0.8},
            fetched_at=datetime.now(UTC),
        ))
        await session.commit()
    await ingest_official_record(tmp_path)

    body = await verdict(app_client, device, OFF_ONLY)
    assert body["facts_provenance"] == "open_food_facts"
    assert body["product_name"] == PRODUCT
    assert body["brand"] == BRAND
    # Brand and product corroborate; they never establish a match on their own.
    assert body["official_records"]["records"] == []


@pytest.mark.asyncio
async def test_an_official_record_changes_no_part_of_the_scientific_verdict(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,
):
    """D. The grade is about the food. A recall is a separate, additive fact."""
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE, label_facts())
    before = await verdict(app_client, device)
    assert before["official_records"]["records"] == []
    assert before["official_records"]["last_successful_check_at"] is None

    await ingest_official_record(tmp_path)
    after = await verdict(app_client, device)
    assert [row["recall_id"] for row in after["official_records"]["records"]] == [RECALL_ID]

    assert after["grade"] == before["grade"]
    assert after["band"] == before["band"]
    assert after["outcome"] == before["outcome"]
    assert after["decision"]["action"] == before["decision"]["action"]
    assert after["decision"]["reason_key"] == before["decision"]["reason_key"]
    assert after["negatives"] == before["negatives"]
    assert after["positives"] == before["positives"]
    assert after["components"] == before["components"]
    assert after["evidence"] == before["evidence"]
    assert after["trace"] == before["trace"]
    assert after["result_contract_version"] == before["result_contract_version"] == "v1"
    # Only the additive envelope moved.
    assert {key for key in after if after[key] != before.get(key)} == {"official_records"}


@pytest.mark.asyncio
async def test_freshness_reports_the_operator_source_check_not_the_database_import(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,
):
    """G and FIX 12. ``created_at`` is when we wrote a row; it is not a check of the register."""
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE, label_facts())
    fetch, _ = await ingest_official_record(tmp_path)

    factory = get_sessionmaker()
    async with factory() as session:
        stored = (await session.execute(
            select(OfficialSourceFetch).where(OfficialSourceFetch.status == "succeeded")
        )).scalar_one()
    assert stored.created_at > SOURCE_CHECKED_AT
    assert stored.source_checked_at == SOURCE_CHECKED_AT

    body = await verdict(app_client, device)
    reported = body["official_records"]["last_successful_check_at"]
    assert datetime.fromisoformat(reported) == SOURCE_CHECKED_AT
    assert datetime.fromisoformat(reported) != stored.created_at


# ---------------------------------------------------------------------------
# Ambiguity and historical provenance, through the route the phone calls
# ---------------------------------------------------------------------------

async def ingest_rows(tmp_path: Path, rows, *, checked_at=SOURCE_CHECKED_AT):
    path = make_export(tmp_path / f"foscos-{uuid.uuid4().hex}.xlsx", rows=rows)
    factory = get_sessionmaker()
    async with factory() as session:
        fetch, counts = await official_records.ingest_recall_xlsx(session, path, source_checked_at=checked_at)
        await session.commit()
        return fetch, counts


def shared_lot_rows():
    """Two recalls filed under one licence and one lot, for different products."""
    return [
        data_row(recall_id=901, batch=BATCH, brand="Alpha", product="Alpha Oats"),
        data_row(recall_id=902, batch=BATCH, brand="Beta", product="Beta Juice"),
    ]


@pytest.mark.asyncio
async def test_a_pack_that_cannot_be_told_apart_receives_no_official_record(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,
):
    """The licence and lot fit two different products, and the pack names neither."""
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE,
                        label_facts(product_name=None, brand=None))
    _, counts = await ingest_rows(tmp_path, shared_lot_rows())
    assert counts["records_created"] == 2

    body = await verdict(app_client, device)
    assert body["official_records"]["records"] == []
    # The check itself still happened, and the screen may still say so.
    assert body["official_records"]["last_successful_check_at"] is not None


@pytest.mark.asyncio
async def test_an_exactly_identified_pack_receives_only_its_own_record(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,
):
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE,
                        label_facts(brand="Beta", product_name="Beta Juice"))
    await ingest_rows(tmp_path, shared_lot_rows())

    records = (await verdict(app_client, device))["official_records"]["records"]
    assert [row["recall_id"] for row in records] == ["902"]
    assert records[0]["brand_name"] == "Beta"


@pytest.mark.asyncio
async def test_a_malformed_licence_cannot_manufacture_an_official_match(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,
):
    """Deleting characters until fourteen digits appear is not reading a licence."""
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE,
                        label_facts(fssai_licence=f"FSSAI {LICENCE}"))
    await ingest_official_record(tmp_path)

    assert (await verdict(app_client, device))["official_records"]["records"] == []


@pytest.mark.asyncio
async def test_a_retained_record_states_its_own_last_observation_not_the_latest_check(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,
):
    """Absence from the newest export is not withdrawal, correction or resolution.

    So the record stays, and the payload separates "when this record was last
    observed" from "when the register was last checked". Collapsing them would
    claim an observation that never happened.
    """
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE, label_facts())
    second_checked = datetime(2026, 8, 4, 10, 0, tzinfo=UTC)
    await ingest_rows(tmp_path, [data_row(recall_id=901, batch=BATCH, brand=BRAND, product=PRODUCT)],
                      checked_at=SOURCE_CHECKED_AT)
    # A later valid export that simply does not list recall 901.
    await ingest_rows(tmp_path, [data_row(recall_id=902, batch="Z-999", brand="Other Foods",
                                          product="Other product")], checked_at=second_checked)

    body = await verdict(app_client, device)
    envelope = body["official_records"]
    assert [row["recall_id"] for row in envelope["records"]] == ["901"]
    record = envelope["records"][0]
    assert datetime.fromisoformat(record["source_last_seen_at"]) == SOURCE_CHECKED_AT
    assert record["seen_in_latest_successful_check"] is False
    # The global freshness is separately visible, and is the later check.
    assert datetime.fromisoformat(envelope["last_successful_check_at"]) == second_checked
    # Nothing in the payload characterises the absence.
    assert not [word for word in ("withdrawn", "removed", "no longer", "resolved", "cleared")
                if word in str(body).casefold()]

    factory = get_sessionmaker()
    async with factory() as session:
        assert len((await session.execute(select(OfficialRecord))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_a_record_in_the_latest_export_says_so_and_dates_itself_to_it(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,
):
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE, label_facts())
    await ingest_official_record(tmp_path)
    later = datetime(2026, 8, 4, 10, 0, tzinfo=UTC)
    await ingest_official_record(tmp_path, checked_at=later)

    record = (await verdict(app_client, device))["official_records"]["records"][0]
    assert record["seen_in_latest_successful_check"] is True
    assert datetime.fromisoformat(record["source_last_seen_at"]) == later


@pytest.mark.asyncio
async def test_ambiguity_and_provenance_leave_the_scientific_verdict_untouched(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,
):
    """Whether a recall resolves or not, the grade is about the food."""
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE,
                        label_facts(brand="Alpha", product_name="Alpha Oats"))
    before = await verdict(app_client, device)
    await ingest_rows(tmp_path, shared_lot_rows())
    after = await verdict(app_client, device)

    assert [row["recall_id"] for row in after["official_records"]["records"]] == ["901"]
    assert after["grade"] == before["grade"]
    assert after["band"] == before["band"]
    assert after["decision"] == before["decision"]
    assert after["negatives"] == before["negatives"]
    assert after["positives"] == before["positives"]
    assert after["result_contract_version"] == before["result_contract_version"] == "v1"
    assert {key for key in after if after[key] != before.get(key)} == {"official_records"}

    # No internal identity is published: not the record's, not the fetch's.
    factory = get_sessionmaker()
    async with factory() as session:
        ids = [str(row.id) for row in (await session.execute(select(OfficialRecord))).scalars().all()]
        ids += [str(row.id) for row in (await session.execute(select(OfficialSourceFetch))).scalars().all()]
    assert not [value for value in ids if value in str(after)]


@pytest.mark.asyncio
async def test_open_food_facts_identity_cannot_resolve_a_shared_lot(
    db_clean, off_clean, app_client, device, tmp_path,
):
    """Store A has no licence and no lot, so it never enters the candidate set."""
    factory = get_off_sessionmaker()
    async with factory() as session:
        session.add(OffProduct(
            barcode=OFF_ONLY, product_name="Alpha Oats", brands="Alpha",
            ingredients_text="oats, sugar, salt",
            nutriments={"sugars_100g": 12.0, "salt_100g": 0.8}, fetched_at=datetime.now(UTC),
        ))
        await session.commit()
    await ingest_rows(tmp_path, shared_lot_rows())

    body = await verdict(app_client, device, OFF_ONLY)
    assert body["facts_provenance"] == "open_food_facts"
    assert body["official_records"]["records"] == []
