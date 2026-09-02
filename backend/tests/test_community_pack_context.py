"""Which physical packet is in this person's hand, and what the report is anchored to.

The hardest thing about a batch signal is that it goes stale silently. Somebody
captures a packet in August, finishes it, buys another in September and scans
it. Nothing in the database changes — and if the lot is read from "the newest
capture" rather than "the newest scan", the app keeps talking to them about the
packet they threw away.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from app.domains.community import service as community
from app.domains.community.models import CommunityObservationReport
from app.domains.community.observations import (
    OBSERVATION_INGREDIENTS_DIFFER,
)
from app.domains.product.models import LabelSnapshot, ScanEvent
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_community_reporting import (
    BARCODE,
    Shopper,
    confirm_label,
    label_facts,
    make_shopper,
    report,
    scan,
    signals,
    upload_photo,
)


async def context(app_client, shopper: Shopper, barcode: str = BARCODE) -> dict:
    response = await app_client.get(
        f"/api/v2/community/observations/context/{barcode}", headers=shopper.device,
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# The stale batch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_new_plain_scan_retires_the_batch_the_last_capture_established(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """T1 capture B1, T2 plain scan, T3 capture B2 — the required regression."""
    # T1: three shoppers capture B1, so a B1 signal exists and our viewer sees it.
    viewer = await make_shopper(
        app_client, registered_supabase_user, facts=label_facts(batch_number="B1"),
    )
    assert (await report(app_client, viewer)).status_code == 201
    for _ in range(2):
        other = await make_shopper(
            app_client, registered_supabase_user, facts=label_facts(batch_number="B1"),
        )
        assert (await report(app_client, other)).status_code == 201

    assert [s["batch_number"] for s in await signals(app_client, viewer.headers())] == ["b1"]
    assert (await context(app_client, viewer))["batch_number"] == "b1"

    # T2: the same phone scans another packet of the same product. No capture,
    # so which lot it is is simply unknown — and the old lot is not it.
    await scan(app_client, viewer, BARCODE)

    assert await signals(app_client, viewer.headers()) == []
    current = await context(app_client, viewer)
    assert current["has_current_scan_context"] is True
    assert current["batch_context_available"] is False
    assert current["batch_number"] is None

    refused = await report(app_client, viewer)
    assert refused.status_code == 422
    assert refused.json()["detail"]["reason"] == community.REASON_BATCH_CAPTURE_REQUIRED

    factory = get_sessionmaker()
    async with factory() as session:
        mine = (await session.execute(select(func.count()).select_from(CommunityObservationReport).where(
            CommunityObservationReport.account_id == viewer.account_id
        ))).scalar_one()
    assert mine == 1  # the T1 report only; nothing new was stored against B1

    # T3: they capture the new packet. Now the context is B2, and B1 stays gone.
    await confirm_label(app_client, viewer, BARCODE, label_facts(batch_number="B2"))
    assert (await context(app_client, viewer))["batch_number"] == "b2"
    assert await signals(app_client, viewer.headers()) == []

    accepted = await report(app_client, viewer)
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["batch_number"] == "b2"


@pytest.mark.asyncio
async def test_a_product_data_observation_needs_a_scan_but_not_a_lot(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    shopper = await make_shopper(app_client, registered_supabase_user)
    current = await context(app_client, shopper)
    assert current["has_current_scan_context"] is True
    assert current["batch_context_available"] is False

    accepted = await report(app_client, shopper, code=OBSERVATION_INGREDIENTS_DIFFER)
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["batch_number"] is None


@pytest.mark.asyncio
async def test_a_device_that_never_scanned_this_barcode_has_no_context(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    shopper = await make_shopper(app_client, registered_supabase_user)
    current = await context(app_client, shopper, "8909999999999")
    assert current["has_current_scan_context"] is False
    assert current["batch_context_available"] is False
    refused = await report(app_client, shopper, barcode="8909999999999",
                           code=OBSERVATION_INGREDIENTS_DIFFER)
    assert refused.status_code == 422
    assert refused.json()["detail"]["reason"] == community.REASON_NO_SCAN


# ---------------------------------------------------------------------------
# What the report is anchored to
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_report_is_anchored_to_the_exact_scan_that_gave_it_context(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    shopper = await make_shopper(
        app_client, registered_supabase_user, facts=label_facts(batch_number="B7"),
    )
    body = (await report(app_client, shopper)).json()

    factory = get_sessionmaker()
    async with factory() as session:
        row = await session.get(CommunityObservationReport, uuid.UUID(body["id"]))
        event = await session.get(ScanEvent, row.scan_event_id)
    assert event is not None
    assert event.barcode == BARCODE
    assert event.account_id == shopper.account_id
    assert event.device_id == row.device_id
    assert isinstance(event.label_facts, dict)
    assert event.label_facts["batch_number"] == "B7"
    assert row.batch_number == "b7"


@pytest.mark.asyncio
async def test_provenance_never_borrows_a_snapshot_that_shares_a_fingerprint(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    """Step 3 excludes the lot from its semantic fingerprint, on purpose.

    So two packets from lots B1 and B2 with the same printed label have the same
    fingerprint and share one snapshot. Matching on that would let a B2 report
    claim provenance from somebody else's B1 capture — a physical claim the data
    does not support. Only a snapshot allocated for this exact scan counts.
    """
    first = await make_shopper(
        app_client, registered_supabase_user, facts=label_facts(batch_number="B1"),
    )
    second = await make_shopper(
        app_client, registered_supabase_user, facts=label_facts(batch_number="B2"),
    )

    factory = get_sessionmaker()
    async with factory() as session:
        snapshots = (await session.execute(
            select(LabelSnapshot).where(LabelSnapshot.barcode == BARCODE)
            .order_by(LabelSnapshot.version_number)
        )).scalars().all()
    # Step 3 deduplicated: one semantic version for both packets. Unchanged.
    assert len(snapshots) == 1
    first_snapshot = snapshots[0]

    body = (await report(app_client, second)).json()
    async with factory() as session:
        row = await session.get(CommunityObservationReport, uuid.UUID(body["id"]))
        event = await session.get(ScanEvent, row.scan_event_id)
    assert row.batch_number == "b2"
    # The scan event is this shopper's own B2 confirmation.
    assert event.label_facts["batch_number"] == "B2"
    assert event.account_id == second.account_id
    # And it claims no snapshot at all, because Step 3 allocated none for this
    # scan. Null is the honest answer; the first shopper's row is not ours.
    assert row.label_snapshot_id is None
    assert first_snapshot.scan_event_id != row.scan_event_id


@pytest.mark.asyncio
async def test_a_snapshot_allocated_for_this_very_scan_is_recorded(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    """When Step 3 does allocate a row for this capture, the report names it."""
    shopper = await make_shopper(
        app_client, registered_supabase_user, facts=label_facts(batch_number="B9"),
    )
    body = (await report(app_client, shopper)).json()
    factory = get_sessionmaker()
    async with factory() as session:
        row = await session.get(CommunityObservationReport, uuid.UUID(body["id"]))
        snapshot = await session.get(LabelSnapshot, row.label_snapshot_id)
    assert snapshot is not None
    assert snapshot.scan_event_id == row.scan_event_id


# ---------------------------------------------------------------------------
# The shopper's own rows
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_shopper_can_find_and_withdraw_their_own_report_after_reopening(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    mine = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    theirs = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    submitted = (await report(app_client, mine)).json()
    assert (await report(app_client, theirs)).status_code == 201

    listing = await app_client.get(
        f"/api/v2/community/observations/mine/{BARCODE}", headers=auth(mine.token),
    )
    assert listing.status_code == 200
    rows = listing.json()["reports"]
    # Only their own, and enough to act on — never anybody else's.
    assert [row["id"] for row in rows] == [submitted["id"]]
    assert set(rows[0]) == {
        "id", "barcode", "observation_code", "scope", "batch_number",
        "status", "created_at", "withdrawn_at",
    }

    withdrawn = await app_client.delete(
        f"/api/v2/community/observations/{rows[0]['id']}", headers=auth(mine.token),
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "withdrawn"

    after = (await app_client.get(
        f"/api/v2/community/observations/mine/{BARCODE}", headers=auth(mine.token),
    )).json()["reports"]
    assert after[0]["status"] == "withdrawn"


# ---------------------------------------------------------------------------
# Rate limits under concurrency
# ---------------------------------------------------------------------------

async def _submit_in_own_session(app_client, shopper: Shopper, photo: str, key: str):
    return await app_client.post(
        "/api/v2/community/observations", headers=shopper.headers(),
        json={"client_report_id": key, "barcode": BARCODE,
              "observation_code": OBSERVATION_INGREDIENTS_DIFFER, "photo_asset_id": photo},
    )


@pytest.mark.asyncio
async def test_a_concurrent_burst_cannot_spend_the_same_final_quota_slot_twice(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    """Count-then-insert is not a limit: five requests can all read nine."""
    shopper = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    for _ in range(community.MAX_REPORTS_PER_ACCOUNT_PER_HOUR - 1):
        assert (await report(app_client, shopper, code=OBSERVATION_INGREDIENTS_DIFFER)).status_code == 201

    photos = [await upload_photo(app_client, shopper) for _ in range(5)]
    responses = await asyncio.gather(*(
        _submit_in_own_session(app_client, shopper, photo, uuid.uuid4().hex) for photo in photos
    ))
    codes = sorted(response.status_code for response in responses)
    assert codes.count(201) <= 1, [r.text for r in responses]
    assert set(codes) <= {201, 429}
    assert all(
        response.json()["detail"]["reason"].endswith("_limit")
        for response in responses if response.status_code == 429
    )

    factory = get_sessionmaker()
    async with factory() as session:
        total = (await session.execute(select(func.count()).select_from(CommunityObservationReport))).scalar_one()
    assert total <= community.MAX_REPORTS_PER_ACCOUNT_PER_HOUR


@pytest.mark.asyncio
async def test_a_retry_arriving_at_the_final_slot_is_still_a_retry(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    """The second copy must not be refused for the quota its own twin consumed."""
    shopper = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    for _ in range(community.MAX_REPORTS_PER_ACCOUNT_PER_HOUR - 1):
        assert (await report(app_client, shopper, code=OBSERVATION_INGREDIENTS_DIFFER)).status_code == 201

    photo = await upload_photo(app_client, shopper)
    key = uuid.uuid4().hex
    responses = await asyncio.gather(*(
        _submit_in_own_session(app_client, shopper, photo, key) for _ in range(3)
    ))
    assert [r.status_code for r in responses] == [201, 201, 201], [r.text for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1

    factory = get_sessionmaker()
    async with factory() as session:
        total = (await session.execute(select(func.count()).select_from(CommunityObservationReport))).scalar_one()
    assert total == community.MAX_REPORTS_PER_ACCOUNT_PER_HOUR


@pytest.mark.asyncio
async def test_a_burst_across_one_account_s_devices_cannot_exceed_the_device_limit(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    shopper = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    for _ in range(community.MAX_REPORTS_PER_DEVICE_PER_HOUR - 1):
        assert (await report(app_client, shopper, code=OBSERVATION_INGREDIENTS_DIFFER)).status_code == 201

    photos = [await upload_photo(app_client, shopper) for _ in range(4)]
    responses = await asyncio.gather(*(
        _submit_in_own_session(app_client, shopper, photo, uuid.uuid4().hex) for photo in photos
    ))
    assert sorted(r.status_code for r in responses).count(201) <= 1

    factory = get_sessionmaker()
    async with factory() as session:
        on_device = (await session.execute(select(func.count()).select_from(CommunityObservationReport).where(
            CommunityObservationReport.device_id.isnot(None)
        ))).scalar_one()
    assert on_device <= community.MAX_REPORTS_PER_DEVICE_PER_HOUR
