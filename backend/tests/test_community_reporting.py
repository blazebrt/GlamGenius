"""Who may report, on what, and what it takes for three reports to become one sentence.

Written against the API a phone actually calls, because the boundaries being
defended here — an account, a claimed phone, a real scan, an owned photo, the
reporter's own lot — are all things a request either has or does not.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun, AIRunOutput
from app.domains.community import service as community
from app.domains.community.models import (
    REPORT_STATUS_ACCEPTED,
    REPORT_STATUS_INVALID,
    CommunityObservationReport,
)
from app.domains.community.observations import (
    OBSERVATION_INGREDIENTS_DIFFER,
    OBSERVATION_SEAL_BROKEN,
)
from app.domains.community.service import MEDIA_PURPOSE_COMMUNITY_OBSERVATION
from app.domains.media.models import MEDIA_STATUS_DELETED, MediaAsset
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth, png_bytes

BARCODE = "8901058000191"
BATCH = "B-123"


async def register_device(app_client) -> dict[str, str]:
    response = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert response.status_code == 201, response.text
    return {"X-Device-Token": response.json()["token"]}


def label_facts(**changes):
    return {
        "product_name": "Synthetic Oat Cereal", "brand": "Northstar",
        "ingredients_text": "oats, sugar, salt",
        "nutrition_per_100g": {"sugars_g": "12", "saturated_fat_g": "3", "salt_g": "0.8"},
        "nutrition_basis": "per_100g", "net_quantity": "180 g",
        "fssai_licence": "10012345678901", "batch_number": BATCH, **changes,
    }


class Shopper:
    """One person, on one claimed phone, who has scanned the pack."""

    def __init__(self, token, account_id, device):
        self.token, self.account_id, self.device = token, account_id, device

    def headers(self):
        return {**self.device, **auth(self.token)}


async def make_shopper(app_client, registered_supabase_user, *, barcode=BARCODE, facts=None) -> Shopper:
    token, account_id = await registered_supabase_user()
    device = await register_device(app_client)
    claimed = await app_client.post("/api/v2/scan/device/claim", headers={**device, **auth(token)})
    assert claimed.status_code == 200, claimed.text
    shopper = Shopper(token, account_id, device)
    if facts is not None:
        await confirm_label(app_client, shopper, barcode, facts)
    else:
        await scan(app_client, shopper, barcode)
    return shopper


async def scan(app_client, shopper: Shopper, barcode: str) -> None:
    response = await app_client.post(
        "/api/v2/scan/events", headers=shopper.headers(),
        json={"barcode": barcode, "client_scan_id": uuid.uuid4().hex},
    )
    assert response.status_code in (200, 201), response.text


async def confirm_label(app_client, shopper: Shopper, barcode: str, facts: dict) -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        run = AIRun(
            account_id=shopper.account_id, feature="product_label_transcribe", provider="test",
            model="test-model", prompt_version="scan-label.v1", schema_version="scan-label.v1",
            status=AI_STATUS_SUCCEEDED, validation_passed=True,
        )
        session.add(run)
        await session.flush()
        session.add(AIRunOutput(ai_run_id=run.id, schema_version="scan-label.v1", payload=facts))
        await session.commit()
        run_id = run.id
    response = await app_client.post(
        "/api/v2/scan/label/confirm", headers=shopper.headers(),
        json={"barcode": barcode, "ai_run_id": str(run_id), "client_scan_id": uuid.uuid4().hex},
    )
    assert response.status_code == 201, response.text


async def upload_photo(app_client, shopper: Shopper, *, payload: bytes | None = None,
                       purpose: str = MEDIA_PURPOSE_COMMUNITY_OBSERVATION) -> str:
    """A distinct image per call unless the caller insists on the same bytes."""
    data = payload if payload is not None else png_bytes() + uuid.uuid4().bytes
    response = await app_client.post(
        "/api/v2/media/upload", headers=auth(shopper.token),
        files={"file": ("pack.png", data, "image/png")}, data={"purpose": purpose},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def report(app_client, shopper: Shopper, *, code=OBSERVATION_SEAL_BROKEN, barcode=BARCODE,
                 photo=None, client_report_id=None):
    return await app_client.post(
        "/api/v2/community/observations", headers=shopper.headers(),
        json={
            "client_report_id": client_report_id or uuid.uuid4().hex,
            "barcode": barcode,
            "observation_code": code,
            "photo_asset_id": photo or await upload_photo(app_client, shopper),
        },
    )


async def verdict(app_client, headers, barcode=BARCODE):
    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def three_reporters(app_client, registered_supabase_user, *, code=OBSERVATION_SEAL_BROKEN,
                          facts=None, same_photo_bytes=False):
    """Three separate people who each confirmed the pack and reported the same thing."""
    shared = png_bytes() + uuid.uuid4().bytes
    shoppers = []
    for _ in range(3):
        shopper = await make_shopper(
            app_client, registered_supabase_user, facts=facts if facts is not None else label_facts(),
        )
        photo = await upload_photo(
            app_client, shopper, payload=shared if same_photo_bytes else None,
        )
        response = await report(app_client, shopper, code=code, photo=photo)
        assert response.status_code == 201, response.text
        shoppers.append((shopper, response.json()))
    return shoppers


# ---------------------------------------------------------------------------
# Who may report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reporting_needs_an_account_a_claimed_phone_and_a_real_scan(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    """Scanning stays anonymous. Naming a brand in public does not."""
    shopper = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    photo = await upload_photo(app_client, shopper)

    # No account: a device token alone cannot create public influence.
    anonymous = await app_client.post(
        "/api/v2/community/observations", headers=shopper.device,
        json={"client_report_id": uuid.uuid4().hex, "barcode": BARCODE,
              "observation_code": OBSERVATION_SEAL_BROKEN, "photo_asset_id": photo},
    )
    assert anonymous.status_code in (401, 403)

    # An account on a phone it has never claimed is not this phone's reporter.
    stranger_device = await register_device(app_client)
    unclaimed = await app_client.post(
        "/api/v2/community/observations", headers={**stranger_device, **auth(shopper.token)},
        json={"client_report_id": uuid.uuid4().hex, "barcode": BARCODE,
              "observation_code": OBSERVATION_SEAL_BROKEN, "photo_asset_id": photo},
    )
    assert unclaimed.status_code == 422
    assert unclaimed.json()["detail"]["reason"] == community.REASON_DEVICE_NOT_CLAIMED

    # A barcode this person never passed a scan over.
    never_scanned = await report(app_client, shopper, barcode="8909999999999")
    assert never_scanned.status_code == 422
    assert never_scanned.json()["detail"]["reason"] == community.REASON_NO_SCAN

    accepted = await report(app_client, shopper, photo=photo)
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["status"] == REPORT_STATUS_ACCEPTED


@pytest.mark.asyncio
async def test_the_body_cannot_name_its_own_reporter(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    """Identity comes from the session and the device token, never the payload."""
    shopper = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    photo = await upload_photo(app_client, shopper)
    base = {"client_report_id": uuid.uuid4().hex, "barcode": BARCODE,
            "observation_code": OBSERVATION_SEAL_BROKEN, "photo_asset_id": photo}
    for injected in ({"account_id": str(uuid.uuid4())}, {"device_id": str(uuid.uuid4())},
                     {"status": REPORT_STATUS_ACCEPTED}, {"batch_number": "Z-999"},
                     {"comment": "anything at all"}):
        response = await app_client.post(
            "/api/v2/community/observations", headers=shopper.headers(),
            json={**base, **injected, "client_report_id": uuid.uuid4().hex},
        )
        assert response.status_code == 422, injected

    unknown_code = await report(app_client, shopper, code="product_is_fake", photo=photo)
    assert unknown_code.status_code == 422
    oversized = await app_client.post(
        "/api/v2/community/observations", headers=shopper.headers(),
        json={**base, "client_report_id": "x" * 200},
    )
    assert oversized.status_code == 422


@pytest.mark.asyncio
async def test_the_photo_must_be_this_account_s_own_live_observation_image(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    shopper = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    stranger = await make_shopper(app_client, registered_supabase_user, facts=label_facts())

    missing = await report(app_client, shopper, photo=str(uuid.uuid4()))
    assert missing.status_code == 404

    # Somebody else's photo is somebody else's.
    theirs = await upload_photo(app_client, stranger)
    assert (await report(app_client, shopper, photo=theirs)).status_code == 404

    # An inventory picture was not offered as evidence about a pack.
    inventory = await upload_photo(app_client, shopper, purpose="inventory_item")
    wrong_purpose = await report(app_client, shopper, photo=inventory)
    assert wrong_purpose.status_code == 422
    assert wrong_purpose.json()["detail"]["reason"] == community.REASON_PHOTO_REQUIRED

    deleted = await upload_photo(app_client, shopper)
    assert (await app_client.delete(f"/api/v2/media/{deleted}", headers=auth(shopper.token))).status_code in (200, 204)
    assert (await report(app_client, shopper, photo=deleted)).status_code == 404


@pytest.mark.asyncio
async def test_a_pack_condition_report_needs_the_reporter_s_own_captured_lot(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    """Refused, not silently stored: the person deserves to know it would go nowhere."""
    scanned_only = await make_shopper(app_client, registered_supabase_user)
    refused = await report(app_client, scanned_only, code=OBSERVATION_SEAL_BROKEN)
    assert refused.status_code == 422
    assert refused.json()["detail"]["reason"] == community.REASON_BATCH_CAPTURE_REQUIRED

    # A product-data observation is about the catalogue, so it needs no lot.
    allowed = await report(app_client, scanned_only, code=OBSERVATION_INGREDIENTS_DIFFER)
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["batch_number"] is None
    assert allowed.json()["scope"] == "product"

    # A pack whose printed lot is a placeholder still has no lot.
    placeholder = await make_shopper(
        app_client, registered_supabase_user, facts=label_facts(batch_number="NA"),
    )
    assert (await report(app_client, placeholder)).status_code == 422

    captured = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    accepted = await report(app_client, captured)
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["batch_number"] == BATCH.casefold()
    assert accepted.json()["scope"] == "batch"


# ---------------------------------------------------------------------------
# Retries and limits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_retried_submission_is_the_same_report_and_a_changed_one_is_a_conflict(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    shopper = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    photo = await upload_photo(app_client, shopper)
    key = uuid.uuid4().hex

    first = await report(app_client, shopper, photo=photo, client_report_id=key)
    assert first.status_code == 201 and first.json()["created"] is True
    again = await report(app_client, shopper, photo=photo, client_report_id=key)
    assert again.status_code == 201
    assert again.json()["created"] is False
    assert again.json()["id"] == first.json()["id"]

    changed = await report(
        app_client, shopper, code=OBSERVATION_INGREDIENTS_DIFFER, photo=photo, client_report_id=key,
    )
    assert changed.status_code == 409

    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(select(CommunityObservationReport))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_concurrent_retries_of_one_key_create_one_row(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    """An offline queue flushing twice must not double a person's voice."""
    shopper = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    photo = await upload_photo(app_client, shopper)
    key = uuid.uuid4().hex
    body = {"client_report_id": key, "barcode": BARCODE,
            "observation_code": OBSERVATION_SEAL_BROKEN, "photo_asset_id": photo}

    responses = await asyncio.gather(*(
        app_client.post("/api/v2/community/observations", headers=shopper.headers(), json=body)
        for _ in range(2)
    ))
    assert [r.status_code for r in responses] == [201, 201], [r.text for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1

    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(select(CommunityObservationReport))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_rate_limits_are_deterministic_and_a_retry_costs_nothing(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    shopper = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    key = uuid.uuid4().hex
    photo = await upload_photo(app_client, shopper)
    assert (await report(app_client, shopper, photo=photo, client_report_id=key)).status_code == 201
    for _ in range(5):
        # The same key again is the same report; it must not eat the quota.
        assert (await report(app_client, shopper, photo=photo, client_report_id=key)).status_code == 201

    for _ in range(community.MAX_REPORTS_PER_ACCOUNT_PER_HOUR - 1):
        assert (await report(app_client, shopper)).status_code == 201
    limited = await report(app_client, shopper)
    assert limited.status_code == 429
    assert limited.json()["detail"]["reason"] == "account_hourly_limit"


# ---------------------------------------------------------------------------
# Reporter and photograph independence
# ---------------------------------------------------------------------------

async def signals(app_client, headers, barcode=BARCODE):
    return (await verdict(app_client, headers, barcode))["community_observations"]["signals"]


@pytest.mark.asyncio
async def test_one_person_is_one_reporter_however_many_reports_or_phones(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """Counting rows, uploads or devices would let one voice sound like a crowd."""
    shopper = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    for _ in range(5):
        assert (await report(app_client, shopper)).status_code == 201

    # The same account, claiming two more phones, each capturing the same pack.
    for _ in range(2):
        device = await register_device(app_client)
        claimed = await app_client.post(
            "/api/v2/scan/device/claim", headers={**device, **auth(shopper.token)},
        )
        assert claimed.status_code == 200
        another = Shopper(shopper.token, shopper.account_id, device)
        await confirm_label(app_client, another, BARCODE, label_facts())
        assert (await report(app_client, another)).status_code == 201

    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(select(CommunityObservationReport))).scalars().all()
    assert len(rows) == 7
    # Seven rows, three devices, one person — and so nothing public.
    assert await signals(app_client, shopper.headers()) == []


@pytest.mark.asyncio
async def test_three_people_with_three_photographs_become_one_public_signal(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    shoppers = await three_reporters(app_client, registered_supabase_user)
    viewer = shoppers[0][0]
    published = await signals(app_client, viewer.headers())
    assert len(published) == 1
    signal = published[0]
    assert signal["observation_code"] == OBSERVATION_SEAL_BROKEN
    assert signal["scope"] == "batch"
    assert signal["batch_number"] == BATCH.casefold()
    assert signal["independent_reporters"] == 3
    assert signal["analysis_score_eligible"] is False
    assert signal["official_finding"] is False


@pytest.mark.asyncio
async def test_two_people_are_never_public(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    first = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    second = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    for shopper in (first, second):
        assert (await report(app_client, shopper)).status_code == 201
    assert await signals(app_client, first.headers()) == []


@pytest.mark.asyncio
async def test_three_people_sharing_one_photograph_are_not_three_photographs(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """One image passed around is one observation wearing three coats."""
    shoppers = await three_reporters(app_client, registered_supabase_user, same_photo_bytes=True)
    factory = get_sessionmaker()
    async with factory() as session:
        hashes = (await session.execute(select(MediaAsset.sha256).where(
            MediaAsset.purpose == MEDIA_PURPOSE_COMMUNITY_OBSERVATION
        ))).scalars().all()
    assert len(set(hashes)) == 1
    assert await signals(app_client, shoppers[0][0].headers()) == []


# ---------------------------------------------------------------------------
# A signal is only as current as the rows behind it
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_withdrawal_removes_the_signal_immediately(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    shoppers = await three_reporters(app_client, registered_supabase_user)
    viewer = shoppers[0][0]
    assert len(await signals(app_client, viewer.headers())) == 1

    withdrawer, body = shoppers[2]
    response = await app_client.delete(
        f"/api/v2/community/observations/{body['id']}", headers=auth(withdrawer.token),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "withdrawn"
    assert await signals(app_client, viewer.headers()) == []


@pytest.mark.asyncio
async def test_nobody_can_withdraw_somebody_else_s_report(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    shoppers = await three_reporters(app_client, registered_supabase_user)
    stranger, _ = shoppers[0]
    _, victim_body = shoppers[1]
    response = await app_client.delete(
        f"/api/v2/community/observations/{victim_body['id']}", headers=auth(stranger.token),
    )
    assert response.status_code == 404
    assert len(await signals(app_client, stranger.headers())) == 1


@pytest.mark.asyncio
async def test_an_administrator_can_stop_one_bad_report_contributing(
    db_clean, off_clean, app_client, registered_supabase_user, fake_supabase_user, public_display,
):
    shoppers = await three_reporters(app_client, registered_supabase_user)
    viewer, _ = shoppers[0]
    _, target = shoppers[2]

    # A shopper is not a moderator.
    refused = await app_client.post(
        f"/api/v2/admin/community/observations/{target['id']}/moderate",
        headers=auth(viewer.token), json={"status": "invalid", "moderation_reason": "policy_violation"},
    )
    assert refused.status_code == 403

    admin_token, admin_id = await registered_supabase_user(admin=True)
    free_text = await app_client.post(
        f"/api/v2/admin/community/observations/{target['id']}/moderate",
        headers=auth(admin_token),
        json={"status": "invalid", "moderation_reason": "he seemed dishonest"},
    )
    assert free_text.status_code == 422

    moderated = await app_client.post(
        f"/api/v2/admin/community/observations/{target['id']}/moderate",
        headers=auth(admin_token),
        json={"status": "invalid", "moderation_reason": "duplicate_evidence"},
    )
    assert moderated.status_code == 200
    assert moderated.json()["status"] == REPORT_STATUS_INVALID
    assert await signals(app_client, viewer.headers()) == []


@pytest.mark.asyncio
async def test_deleting_a_supporting_photograph_withdraws_its_support(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """The report may stay for audit; it stops being photographic support."""
    shoppers = await three_reporters(app_client, registered_supabase_user)
    viewer, _ = shoppers[0]
    assert len(await signals(app_client, viewer.headers())) == 1

    owner, body = shoppers[2]
    factory = get_sessionmaker()
    async with factory() as session:
        report_row = await session.get(CommunityObservationReport, uuid.UUID(body["id"]))
        asset_id = report_row.photo_asset_id
    deleted = await app_client.delete(f"/api/v2/media/{asset_id}", headers=auth(owner.token))
    assert deleted.status_code in (200, 204)

    assert await signals(app_client, viewer.headers()) == []
    async with factory() as session:
        surviving = await session.get(CommunityObservationReport, uuid.UUID(body["id"]))
        asset = await session.get(MediaAsset, asset_id)
    assert surviving is not None and surviving.status == REPORT_STATUS_ACCEPTED
    assert asset.status == MEDIA_STATUS_DELETED


@pytest.mark.asyncio
async def test_a_report_outside_the_active_window_stops_counting(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """Display maturity runs on server time. A client cannot revive a signal."""
    shoppers = await three_reporters(app_client, registered_supabase_user)
    viewer, stale = shoppers[0][0], shoppers[2][1]
    factory = get_sessionmaker()
    async with factory() as session:
        row = await session.get(CommunityObservationReport, uuid.UUID(stale["id"]))
        row.created_at = datetime.now(UTC) - timedelta(days=community.ACTIVE_WINDOW_DAYS + 1)
        await session.commit()
    assert await signals(app_client, viewer.headers()) == []


@pytest.mark.asyncio
async def test_the_foreign_key_takes_a_departing_account_s_reports_with_it(
    db_clean, off_clean, app_client, registered_supabase_user, public_display,
):
    """The schema-level half of the guarantee, isolated from the worker."""
    shoppers = await three_reporters(app_client, registered_supabase_user)
    viewer = shoppers[0][0]
    assert len(await signals(app_client, viewer.headers())) == 1

    leaving = shoppers[2][0]
    from app.domains.identity.models import Account
    from sqlalchemy import delete

    factory = get_sessionmaker()
    async with factory() as session:
        await session.execute(delete(Account).where(Account.id == leaving.account_id))
        await session.commit()

    async with factory() as session:
        remaining = (await session.execute(select(CommunityObservationReport))).scalars().all()
    assert len(remaining) == 2
    assert await signals(app_client, viewer.headers()) == []


@pytest.mark.asyncio
async def test_the_real_deletion_lifecycle_removes_a_reporter_from_the_public_count(
    db_clean, off_clean, app_client, registered_supabase_user, public_display, media_root,
):
    """Through the actual deletion state machine, not a hand-written DELETE.

    A foreign key proves the rows can go. It does not prove the job that
    customers actually trigger reaches the same state — and a contribution that
    outlived its owner would be a secret identifying trace of somebody who
    asked to be forgotten.
    """
    from app.domains.privacy import deletion_service

    shoppers = await three_reporters(app_client, registered_supabase_user)
    viewer = shoppers[0][0]
    assert len(await signals(app_client, viewer.headers())) == 1

    leaving = shoppers[2][0]
    factory = get_sessionmaker()
    async with factory() as session:
        job = await deletion_service.request_deletion(session, leaving.account_id)
        await session.commit()
    assert job.state == "requested"

    async with factory() as session:
        processed = await deletion_service.drain_all(session)
        await session.commit()
    assert processed >= 1

    async with factory() as session:
        remaining = (await session.execute(select(CommunityObservationReport))).scalars().all()
        gone = (await session.execute(select(CommunityObservationReport).where(
            CommunityObservationReport.account_id == leaving.account_id
        ))).scalars().all()
    assert gone == []
    assert len(remaining) == 2
    # Three reporters became two, so the signal is no longer public.
    assert await signals(app_client, viewer.headers()) == []


# ---------------------------------------------------------------------------
# The reporter's own row, and nobody else's
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_privacy_export_carries_the_person_s_own_observations_only(
    db_clean, off_clean, app_client, registered_supabase_user,
):
    from app.domains.privacy.export import build_export

    mine = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    theirs = await make_shopper(app_client, registered_supabase_user, facts=label_facts())
    assert (await report(app_client, mine)).status_code == 201
    assert (await report(app_client, theirs)).status_code == 201

    factory = get_sessionmaker()
    async with factory() as session:
        export = await build_export(session, mine.account_id)
    reports = export["domains"]["community"]["observation_reports"]
    assert len(reports) == 1
    assert reports[0]["observation_code"] == OBSERVATION_SEAL_BROKEN
    assert reports[0]["barcode"] == BARCODE
    assert reports[0]["batch_number"] == BATCH.casefold()
    assert reports[0]["status"] == REPORT_STATUS_ACCEPTED
    assert str(theirs.account_id) not in str(export["domains"]["community"])
    assert "community_observation_reports" in export["registry_summary"]["included_tables"]


@pytest.mark.asyncio
async def test_the_report_row_has_nowhere_to_put_free_text(db_clean):
    """The Constitution forbids it, so the schema does not offer the option."""
    columns = {column.name for column in CommunityObservationReport.__table__.columns}
    for forbidden in ("comment", "description", "notes", "caption", "title", "review_text",
                      "reason_text", "message", "body", "text"):
        assert forbidden not in columns
    text_like = [
        column.name for column in CommunityObservationReport.__table__.columns
        if str(column.type).upper().startswith("TEXT")
    ]
    assert text_like == []
