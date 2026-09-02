"""Which shopper sees which batch signal, and what community may never touch.

Section 9 of the brief is the sharpest rule in Step 5: a batch signal belongs to
the lot in *this* person's hand, established by *this* device's own capture. The
global latest label version is not that, and showing a warning about a lot
somebody else photographed is the false positive the whole scope exists to stop.
"""
from __future__ import annotations

import pytest
from app.domains.community.models import CommunityObservationReport
from app.domains.community.observations import (
    OBSERVATION_INGREDIENTS_DIFFER,
    OBSERVATION_SEAL_BROKEN,
)
from app.domains.product.models import LabelSnapshot, ProductRecord, ScanDevice, ScanEvent
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.test_community_reporting import (
    BARCODE,
    label_facts,
    make_shopper,
    report,
    signals,
    three_reporters,
    verdict,
)


async def reporters_for_batch(app_client, registered_supabase_user, batch: str, *, code=OBSERVATION_SEAL_BROKEN):
    """Three separate people who each captured this lot and reported the same thing."""
    shoppers = []
    for _ in range(3):
        shopper = await make_shopper(
            app_client, registered_supabase_user, facts=label_facts(batch_number=batch),
        )
        response = await report(app_client, shopper, code=code)
        assert response.status_code == 201, response.text
        shoppers.append(shopper)
    return shoppers


@pytest.mark.asyncio
async def test_a_batch_signal_reaches_only_a_shopper_holding_that_batch(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """Cases C, D and E of the batch matrix, in one story."""
    holders = await reporters_for_batch(app_client, registered_supabase_user, "B1")

    # C. Someone holding B1 sees the B1 signal.
    published = await signals(app_client, holders[0].headers())
    assert [(s["observation_code"], s["batch_number"]) for s in published] == [(OBSERVATION_SEAL_BROKEN, "b1")]

    # D. Someone holding a different lot does not.
    other_lot = await make_shopper(
        app_client, registered_supabase_user, facts=label_facts(batch_number="C456"),
    )
    assert await signals(app_client, other_lot.headers()) == []

    # E. Someone who has scanned but never captured their pack has no lot at
    #    all, so no batch signal can be about the pack they are holding.
    no_capture = await make_shopper(app_client, registered_supabase_user)
    assert await signals(app_client, no_capture.headers()) == []


@pytest.mark.asyncio
async def test_another_phone_s_capture_does_not_lend_a_shopper_its_batch(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """Case F, the critical one.

    Three people report on lot B1, so the newest label version for this barcode
    carries B1. A fourth shopper scans the same barcode and never captures their
    own pack. The global latest snapshot says B1 — but it is somebody else's
    photograph of somebody else's packet, and it must lend this person nothing.
    """
    holders = await reporters_for_batch(app_client, registered_supabase_user, "B1")
    assert len(await signals(app_client, holders[0].headers())) == 1

    factory = get_sessionmaker()
    async with factory() as session:
        latest = (await session.execute(
            select(LabelSnapshot).where(LabelSnapshot.barcode == BARCODE)
            .order_by(LabelSnapshot.version_number.desc()).limit(1)
        )).scalar_one()
    # The global newest label version really does carry the reported lot.
    assert latest.facts["batch_number"] == "B1"

    onlooker = await make_shopper(app_client, registered_supabase_user)
    async with factory() as session:
        onlooker_device = (await session.execute(
            select(ScanDevice).where(ScanDevice.claimed_by_account_id == onlooker.account_id)
        )).scalar_one()
        captures_by_onlooker = (await session.execute(
            select(ScanEvent).where(
                ScanEvent.barcode == BARCODE,
                ScanEvent.device_id == onlooker_device.id,
                func.jsonb_typeof(ScanEvent.label_facts) == "object",
            )
        )).scalars().all()
    # They scanned, but never captured their own pack — so they have no lot.
    assert captures_by_onlooker == []
    assert await signals(app_client, onlooker.headers()) == []


@pytest.mark.asyncio
async def test_reports_spread_across_three_lots_never_become_a_product_claim(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """Case B. Three different lots is three ones, not one three.

    Promoting them into "this product has this problem" is an inference Step 5
    deliberately does not make: three packets from three production runs may
    have three unrelated causes, and the brand would be answering a claim
    nobody's evidence supports.
    """
    for lot in ("B1", "B2", "B3"):
        shopper = await make_shopper(
            app_client, registered_supabase_user, facts=label_facts(batch_number=lot),
        )
        assert (await report(app_client, shopper)).status_code == 201

    factory = get_sessionmaker()
    async with factory() as session:
        lots = (await session.execute(select(CommunityObservationReport.batch_number))).scalars().all()
    assert sorted(lots) == ["b1", "b2", "b3"]

    for lot in ("B1", "B2", "B3"):
        viewer = await make_shopper(
            app_client, registered_supabase_user, facts=label_facts(batch_number=lot),
        )
        # One reporter per lot is one reporter, and no lot borrows another's.
        assert await signals(app_client, viewer.headers()) == []


@pytest.mark.asyncio
async def test_a_product_data_signal_reaches_every_shopper_of_that_barcode(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """Case A of the product-data matrix: these are about our catalogue, not a lot."""
    await three_reporters(app_client, registered_supabase_user, code=OBSERVATION_INGREDIENTS_DIFFER)
    onlooker = await make_shopper(app_client, registered_supabase_user)

    published = await signals(app_client, onlooker.headers())
    assert len(published) == 1
    assert published[0]["observation_code"] == OBSERVATION_INGREDIENTS_DIFFER
    assert published[0]["scope"] == "product"
    assert published[0]["batch_number"] is None
    assert published[0]["independent_reporters"] == 3


@pytest.mark.asyncio
async def test_a_product_data_signal_corrects_nothing_it_only_reports(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """Three shoppers saying the ingredients look different is not an ingredient list.

    Step 3 label capture remains the only path by which what the app believes
    about a product actually changes.
    """
    shoppers = await three_reporters(
        app_client, registered_supabase_user, code=OBSERVATION_INGREDIENTS_DIFFER,
    )
    viewer = shoppers[0][0]

    factory = get_sessionmaker()
    async with factory() as session:
        before_snapshots = (await session.execute(
            select(LabelSnapshot).where(LabelSnapshot.barcode == BARCODE)
            .order_by(LabelSnapshot.version_number)
        )).scalars().all()
        before_product = (await session.execute(
            select(ProductRecord).where(ProductRecord.barcode == BARCODE)
        )).scalar_one_or_none()

    body = await verdict(app_client, viewer.headers())
    assert len(body["community_observations"]["signals"]) == 1

    async with factory() as session:
        after_snapshots = (await session.execute(
            select(LabelSnapshot).where(LabelSnapshot.barcode == BARCODE)
            .order_by(LabelSnapshot.version_number)
        )).scalars().all()
        after_product = (await session.execute(
            select(ProductRecord).where(ProductRecord.barcode == BARCODE)
        )).scalar_one_or_none()

    # No new label version, no rewritten facts, no promoted confidence.
    assert [row.version_number for row in after_snapshots] == [row.version_number for row in before_snapshots]
    assert [row.facts for row in after_snapshots] == [row.facts for row in before_snapshots]
    assert [row.confidence for row in after_snapshots] == [row.confidence for row in before_snapshots]
    if before_product is not None:
        assert after_product.confidence == before_product.confidence
        assert after_product.confirmation_count == before_product.confirmation_count
        assert after_product.verified_at == before_product.verified_at
    # The label version the screen reports is the one Step 3 allocated.
    assert body["label_version"]["version_number"] == after_snapshots[-1].version_number
