"""The Product Result contract, with shopper observations added to it.

Community is the fourth epistemic layer and the lowest-privilege one. These
tests are mostly about what it must leave alone: the grade, the decision, the
official record, the label version, and the identities of everyone involved.
"""
from __future__ import annotations

import uuid

import pytest
from app import config
from app.domains.community.models import CommunityObservationReport
from app.domains.community.observations import (
    OBSERVATION_INGREDIENTS_DIFFER,
    OBSERVATION_SEAL_BROKEN,
)
from app.domains.media.models import MediaAsset
from app.domains.off.models import OffProduct
from app.domains.off.store import get_off_sessionmaker
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import COMMUNITY_BRAND_REPLY_URL
from tests.test_community_reporting import (
    BARCODE,
    label_facts,
    make_shopper,
    report,
    three_reporters,
    verdict,
)

# The keys the scientific and official layers own. Community may move none.
PROTECTED_KEYS = (
    "grade", "band", "outcome", "decision", "negatives", "positives", "lowers", "helps",
    "components", "evidence", "trace", "nutrition", "taxonomy", "ingredients",
    "official_records", "label_version", "confidence", "facts_provenance",
    "result_contract_version", "quantity_guidance", "purity_note", "missing",
)


@pytest.mark.asyncio
async def test_community_is_additive_and_silent_by_default(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    """Collection works with display switched off, and says nothing either way."""
    shoppers = await three_reporters(app_client, registered_supabase_user)
    body = await verdict(app_client, shoppers[0][0].headers())

    assert body["result_contract_version"] == "v1"
    envelope = body["community_observations"]
    assert envelope["policy_version"] == "community-observations-v1"
    assert envelope["active_window_days"] == 90
    assert envelope["public_enabled"] is False
    assert envelope["brand_reply_url"] is None
    assert envelope["signals"] == []

    # The reports were still collected. Publication is the only thing gated.
    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(select(CommunityObservationReport))).scalars().all()
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_public_display_needs_the_flag_and_a_usable_right_of_reply(
    db_clean, off_clean, app_client, registered_supabase_user, monkeypatch,
):
    """Fail closed. Publishing a claim about a brand without giving the brand a
    visible way to answer is the thing the Constitution forbids."""
    shoppers = await three_reporters(app_client, registered_supabase_user)
    headers = shoppers[0][0].headers()

    async def signals_now() -> list:
        return (await verdict(app_client, headers))["community_observations"]["signals"]

    # Threshold met, but display off.
    monkeypatch.setattr(config, "COMMUNITY_PUBLIC_SIGNALS_ENABLED", False)
    monkeypatch.setattr(config, "COMMUNITY_BRAND_REPLY_URL", COMMUNITY_BRAND_REPLY_URL)
    assert await signals_now() == []

    # Display on, but no address a brand could answer at.
    monkeypatch.setattr(config, "COMMUNITY_PUBLIC_SIGNALS_ENABLED", True)
    monkeypatch.setattr(config, "COMMUNITY_BRAND_REPLY_URL", None)
    assert await signals_now() == []

    # Display on, but the address is not openable.
    for malformed in ("not-a-url", "mailto:someone@example.org", "http://example.org/reply"):
        monkeypatch.setattr(config, "COMMUNITY_BRAND_REPLY_URL", malformed)
        assert await signals_now() == [], malformed

    monkeypatch.setattr(config, "COMMUNITY_BRAND_REPLY_URL", COMMUNITY_BRAND_REPLY_URL)
    body = await verdict(app_client, headers)
    assert body["community_observations"]["public_enabled"] is True
    assert body["community_observations"]["brand_reply_url"] == COMMUNITY_BRAND_REPLY_URL
    assert len(body["community_observations"]["signals"]) == 1


@pytest.mark.asyncio
async def test_a_public_signal_moves_no_part_of_the_verdict(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """Absolute invariant: shoppers do not grade food and do not find products."""
    first = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    before = await verdict(app_client, first.headers())
    assert before["community_observations"]["signals"] == []

    shoppers = await three_reporters(app_client, registered_supabase_user)
    after = await verdict(app_client, first.headers())
    assert len(after["community_observations"]["signals"]) == 1

    for key in PROTECTED_KEYS:
        assert after.get(key) == before.get(key), key
    # Community is the only thing that moved, anywhere in the payload.
    assert {key for key in after if after[key] != before.get(key)} == {"community_observations"}
    # And it never claims to be one of the other layers.
    signal = after["community_observations"]["signals"][0]
    assert signal["analysis_score_eligible"] is False
    assert signal["official_finding"] is False
    assert after["official_records"]["records"] == []


@pytest.mark.asyncio
async def test_the_public_payload_names_nobody_and_nothing_internal(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """A signal is a count. It is not a list of people, photographs or rows."""
    shoppers = await three_reporters(app_client, registered_supabase_user)
    body = await verdict(app_client, shoppers[0][0].headers())
    signal = body["community_observations"]["signals"][0]

    assert set(signal) == {
        "observation_code", "scope", "batch_number", "independent_reporters",
        "first_reported_at", "last_reported_at", "analysis_score_eligible", "official_finding",
    }
    assert signal["independent_reporters"] == 3

    factory = get_sessionmaker()
    async with factory() as session:
        reports = (await session.execute(select(CommunityObservationReport))).scalars().all()
        assets = (await session.execute(select(MediaAsset))).scalars().all()
    rendered = str(body)
    for report_row in reports:
        assert str(report_row.id) not in rendered
        assert str(report_row.account_id) not in rendered
        assert str(report_row.device_id) not in rendered
    for asset in assets:
        assert str(asset.id) not in rendered
        assert asset.sha256 not in rendered
        assert asset.storage_key not in rendered
    for internal in ("moderation_reason", "status", "photo_asset_id", "sha256", "storage_key",
                     "client_report_id", "label_snapshot_id"):
        assert internal not in str(body["community_observations"])


@pytest.mark.asyncio
async def test_no_signal_is_never_rendered_as_a_clean_bill_of_health(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """Absence of a public signal is not evidence of absence.

    It equally means below threshold, outside the window, display off, or a
    batch signal about a lot this shopper is not holding — so the payload
    carries no phrasing a screen could turn into "no problems reported".
    """
    lonely = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    assert (await report(app_client, lonely)).status_code == 201
    envelope = (await verdict(app_client, lonely.headers()))["community_observations"]

    assert envelope["signals"] == []
    rendered = str(envelope).casefold()
    for reassurance in ("no reports", "no complaints", "no issues", "no concerns",
                        "verified", "clean", "safe", "all clear", "none reported"):
        assert reassurance not in rendered


@pytest.mark.asyncio
async def test_public_signals_are_ordered_deterministically_not_by_severity(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """Product-data first, then the viewer's batch. No danger ranking anywhere."""
    await three_reporters(app_client, registered_supabase_user, code=OBSERVATION_SEAL_BROKEN)
    shoppers = await three_reporters(
        app_client, registered_supabase_user, code=OBSERVATION_INGREDIENTS_DIFFER,
    )
    signals = (await verdict(app_client, shoppers[0][0].headers()))["community_observations"]["signals"]

    assert [signal["scope"] for signal in signals] == ["product", "batch"]
    assert [signal["observation_code"] for signal in signals] == [
        OBSERVATION_INGREDIENTS_DIFFER, OBSERVATION_SEAL_BROKEN,
    ]
    # An insect sighting does not outrank a pack-size mismatch, or vice versa:
    # there is no rank to carry. ``analysis_score_eligible`` is the opposite
    # kind of field — a standing false, saying this never feeds the grade.
    assert not [key for key in signals[0] if key in
                ("severity", "risk", "rank", "priority", "danger_score", "safety_score")]
    assert all(signal["analysis_score_eligible"] is False for signal in signals)


@pytest.mark.asyncio
async def test_a_shopper_with_no_confirmed_pack_still_reads_product_scoped_signals(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    await three_reporters(app_client, registered_supabase_user, code=OBSERVATION_INGREDIENTS_DIFFER)
    onlooker = await make_shopper(app_client, registered_supabase_user)
    signals = (await verdict(app_client, onlooker.headers()))["community_observations"]["signals"]
    assert [signal["observation_code"] for signal in signals] == [OBSERVATION_INGREDIENTS_DIFFER]
    assert signals[0]["batch_number"] is None


@pytest.mark.asyncio
async def test_community_persistence_stays_out_of_the_open_food_facts_store(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """The ODbL wall. A report knows a barcode because a person scanned it.

    Nothing from Store A is copied into the report, and nothing about the report
    is written into Store A. The two halves meet in one response and are
    discarded with it.
    """
    off_factory = get_off_sessionmaker()
    async with off_factory() as session:
        session.add(OffProduct(
            barcode=BARCODE, product_name="Cached OFF name", brands="Cached OFF brand",
            ingredients_text="oats, sugar", nutriments={"sugars_100g": 12.0},
            fetched_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        ))
        await session.commit()

    shoppers = await three_reporters(app_client, registered_supabase_user)
    body = await verdict(app_client, shoppers[0][0].headers())
    assert len(body["community_observations"]["signals"]) == 1

    # Store B holds no Open Food Facts field on the report.
    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(select(CommunityObservationReport))).scalars().all()
    columns = {column.name for column in CommunityObservationReport.__table__.columns}
    for off_field in ("product_name", "brands", "brand", "ingredients_text", "nutriments",
                      "categories", "image_url"):
        assert off_field not in columns
    for row in rows:
        assert row.barcode == BARCODE

    # Store A is untouched: no community column, no community row, no write.
    async with off_factory() as session:
        cached = await session.get(OffProduct, BARCODE)
    assert cached is not None
    assert cached.product_name == "Cached OFF name"
    off_columns = {column.name for column in OffProduct.__table__.columns}
    assert not [name for name in off_columns if "community" in name or "observation" in name]


@pytest.mark.asyncio
async def test_an_unclaimed_devices_verdict_still_renders(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """Viewing stays anonymous. Only submitting needs an account."""
    await three_reporters(app_client, registered_supabase_user, code=OBSERVATION_INGREDIENTS_DIFFER)
    response = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    anonymous = {"X-Device-Token": response.json()["token"]}
    body = await verdict(app_client, anonymous)
    assert body["result_contract_version"] == "v1"
    assert [s["observation_code"] for s in body["community_observations"]["signals"]] == [
        OBSERVATION_INGREDIENTS_DIFFER,
    ]
