"""Community-report persistence is evidence collection, never product truth."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.domains.identity.models import Account
from app.domains.media.models import MediaAsset
from app.domains.product import community_reporting, devices
from app.domains.product.community_signals import SignalScope, evaluate_signal
from app.domains.product.models import CommunityObservationReport
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import ConflictError, ValidationFailedError
from sqlalchemy import func, select

from tests.conftest import auth

BARCODE = "8901058000191"


async def _device(session, *, account_id: uuid.UUID | None = None):
    if account_id is not None and await session.get(Account, account_id) is None:
        session.add(Account(id=account_id))
        await session.flush()
    device, _ = await devices.register(session, device_key=uuid.uuid4().hex, platform="test")
    if account_id is not None:
        await devices.claim(session, device=device, account_id=account_id)
    return device


async def _submit(session, device, *, code="barcode_mismatch", client_id=None, batch=None, context=None, photo=None, observed_at=None):
    return await community_reporting.submit(
        session,
        device=device,
        client_report_id=client_id or uuid.uuid4().hex,
        barcode=BARCODE,
        observation_code=code,
        batch_number=batch,
        condition_context=context,
        photo_asset_id=photo,
        observed_at=observed_at,
    )


@pytest.mark.asyncio
async def test_anonymous_submission_is_idempotent_and_rejects_unstructured_input(db_clean, app_client):
    registered = await app_client.post("/api/v2/scan/device", json={"device_key": uuid.uuid4().hex})
    headers = {"X-Device-Token": registered.json()["token"]}
    payload = {"client_report_id": "report-0001", "barcode": BARCODE, "observation_code": "barcode_mismatch"}

    first = await app_client.post("/api/v2/products/community-observations", headers=headers, json=payload)
    second = await app_client.post("/api/v2/products/community-observations", headers=headers, json=payload)
    assert first.status_code == 201, first.text
    assert first.json()["created"] is True
    assert second.status_code == 201, second.text
    assert second.json() == {**first.json(), "status": "already_received", "created": False}

    rejected = await app_client.post(
        "/api/v2/products/community-observations",
        headers=headers,
        json={**payload, "client_report_id": "report-0002", "comment": "this product is dangerous"},
    )
    assert rejected.status_code == 422

    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(select(CommunityObservationReport))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_registry_drives_validation_and_condition_context_is_closed(db_clean, app_client):
    registered = await app_client.post("/api/v2/scan/device", json={"device_key": uuid.uuid4().hex})
    headers = {"X-Device-Token": registered.json()["token"]}
    base = {"client_report_id": "report-0003", "barcode": BARCODE, "observation_code": "not_in_policy"}
    assert (await app_client.post("/api/v2/products/community-observations", headers=headers, json=base)).status_code == 422

    conditional = {**base, "client_report_id": "report-0004", "observation_code": "did_not_solidify_as_expected"}
    assert (await app_client.post("/api/v2/products/community-observations", headers=headers, json=conditional)).status_code == 422
    valid = {
        **conditional,
        "condition_context": {
            "storage_condition": "ambient",
            "observation_timing": "on_opening",
            "preparation_or_use_condition": "as_packaged",
        },
    }
    assert (await app_client.post("/api/v2/products/community-observations", headers=headers, json=valid)).status_code == 201


@pytest.mark.asyncio
async def test_independence_uses_claimed_account_otherwise_device_and_keeps_repeat_rows(db_clean):
    account_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        first = await _device(session, account_id=account_id)
        second = await _device(session, account_id=account_id)
        anonymous = await _device(session)
        await _submit(session, first, client_id="first-report")
        await _submit(session, first, client_id="first-repeat")
        await _submit(session, second, client_id="same-account-device")
        await _submit(session, anonymous, client_id="anonymous-report")
        await session.commit()
        evidence = await community_reporting.aggregate_evidence(session, barcode=BARCODE, observation_code="barcode_mismatch")

    # Four durable rows, but only the claimed account is public-eligible.
    assert evidence.active.independent_reporters == 1


@pytest.mark.asyncio
async def test_active_and_historical_windows_and_batch_distribution_are_independent(db_clean):
    now = datetime.now(UTC)
    factory = get_sessionmaker()
    async with factory() as session:
        old = await _device(session, account_id=uuid.uuid4())
        fresh = await _device(session, account_id=uuid.uuid4())
        await _submit(
            session, old, code="pack_leaking", client_id="old-batch", batch="OLD-1",
            observed_at=now - timedelta(days=91),
        )
        await _submit(session, fresh, code="pack_leaking", client_id="fresh-batch", batch="NEW-1", observed_at=now)
        await session.commit()
        evidence = await community_reporting.aggregate_evidence(
            session, barcode=BARCODE, observation_code="pack_leaking", now=now,
        )

    assert evidence.active.independent_reporters == 1
    assert dict(evidence.active.reporters_by_batch) == {"NEW-1": 1}
    assert evidence.historical.independent_reporters == 1
    assert dict(evidence.historical.reporters_by_batch) == {"OLD-1": 1}


@pytest.mark.asyncio
async def test_photo_and_complete_context_are_counted_only_when_associated_to_a_reporter(db_clean):
    account_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        device = await _device(session, account_id=account_id)
        unknown_device = await _device(session)
        asset = MediaAsset(
            account_id=account_id, storage_backend="local", storage_key="test/photo.png", content_type="image/png",
            byte_size=1, sha256="0" * 64, purpose="photo_analysis",
        )
        session.add(asset)
        await session.flush()
        await _submit(session, device, code="did_not_solidify_as_expected", client_id="photo-context", batch="LOT-1", photo=asset.id, context={
            "storage_condition": "ambient", "observation_timing": "on_opening",
            "preparation_or_use_condition": "as_packaged",
        })
        await _submit(session, unknown_device, code="did_not_solidify_as_expected", client_id="unknown-context", batch="LOT-2", context={
            "storage_condition": "unknown", "observation_timing": "unknown",
            "preparation_or_use_condition": "unknown",
        })
        await session.commit()
        evidence = await community_reporting.aggregate_evidence(
            session, barcode=BARCODE, observation_code="did_not_solidify_as_expected",
        )

    assert evidence.active.independent_reporters == 2
    assert evidence.active.photo_reporters == 1
    assert evidence.active.condition_context_reporters == 1


@pytest.mark.asyncio
async def test_public_api_returns_aggregate_policy_only_and_cannot_change_product_truth(db_clean, app_client):
    factory = get_sessionmaker()
    async with factory() as session:
        for number in range(5):
            device = await _device(session, account_id=uuid.uuid4())
            await _submit(session, device, code="barcode_mismatch", client_id=f"public-{number}")
        await session.commit()

    response = await app_client.get(f"/api/v2/products/{BARCODE}/community-signals")
    assert response.status_code == 200
    body = response.json()
    assert body["signals"][0]["observation_code"] == "barcode_mismatch"
    assert body["signals"][0]["scope"] == SignalScope.PRODUCT.value
    serialized = repr(body)
    for private_name in ("device_id", "account_id", "client_report_id", "photo_asset_id", "observed_at", "status"):
        assert private_name not in serialized

    # The aggregate policy keeps both causal boundaries explicitly false.
    factory = get_sessionmaker()
    async with factory() as session:
        evidence = await community_reporting.aggregate_evidence(session, barcode=BARCODE, observation_code="barcode_mismatch")
    decision = evaluate_signal("barcode_mismatch", evidence)
    assert decision.analysis_score_eligible is False
    assert decision.official_finding is False


@pytest.mark.asyncio
async def test_unclaimed_devices_are_collected_but_never_mature_public_evidence(db_clean, app_client):
    factory = get_sessionmaker()
    async with factory() as session:
        for number in range(15):
            device = await _device(session)
            await _submit(session, device, client_id=f"anonymous-{number}")
        await session.commit()
        stored = await session.scalar(select(func.count(CommunityObservationReport.id)))
        evidence = await community_reporting.aggregate_evidence(session, barcode=BARCODE, observation_code="barcode_mismatch")
    assert stored == 15
    assert evidence.active.independent_reporters == 0
    assert (await app_client.get(f"/api/v2/products/{BARCODE}/community-signals")).json()["signals"] == []


@pytest.mark.asyncio
async def test_claim_backfills_reports_export_and_preserves_one_person_identity(
    db_clean, app_client, registered_supabase_user,
):
    device_response = await app_client.post("/api/v2/scan/device", json={"device_key": uuid.uuid4().hex})
    device_headers = {"X-Device-Token": device_response.json()["token"]}
    first = {"client_report_id": "claim-first", "barcode": BARCODE, "observation_code": "barcode_mismatch"}
    assert (await app_client.post("/api/v2/products/community-observations", headers=device_headers, json=first)).status_code == 201

    token, account_id = await registered_supabase_user()
    claimed = await app_client.post("/api/v2/scan/device/claim", headers={**device_headers, **auth(token)})
    assert claimed.status_code == 200
    assert claimed.json()["community_reports_attached"] == 1
    assert (await app_client.post(
        "/api/v2/products/community-observations", headers=device_headers,
        json={**first, "client_report_id": "claim-second"},
    )).status_code == 201

    from app.domains.privacy import export as export_service

    factory = get_sessionmaker()
    async with factory() as session:
        evidence = await community_reporting.aggregate_evidence(session, barcode=BARCODE, observation_code="barcode_mismatch")
        exported = await export_service.build_export(session, account_id)
    assert evidence.active.independent_reporters == 1
    reports = exported["domains"]["product_scans"]["community_observation_reports"]
    assert len(reports) == 2
    assert all("device_id" not in row for row in reports)


@pytest.mark.asyncio
async def test_idempotency_key_collision_and_rate_limit_are_rejected(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        device = await _device(session)
        await _submit(session, device, client_id="same-report")
        with pytest.raises(ConflictError):
            await _submit(session, device, client_id="same-report", code="ingredients_changed")
        for number in range(community_reporting.MAX_REPORTS_PER_DEVICE_PER_HOUR - 1):
            await _submit(session, device, client_id=f"rate-{number}")
        with pytest.raises(ValidationFailedError):
            await _submit(session, device, client_id="over-the-limit")


@pytest.mark.asyncio
async def test_account_deletion_cascades_claimed_reports_but_keeps_never_claimed_anonymous_evidence(db_clean):
    account_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        claimed_device = await _device(session, account_id=account_id)
        anonymous_device = await _device(session)
        claimed, _ = await _submit(session, claimed_device, client_id="deleted-account-report")
        anonymous, _ = await _submit(session, anonymous_device, client_id="anonymous-retained-report")
        await session.commit()
        account = await session.get(Account, account_id)
        await session.delete(account)
        await session.commit()
        assert await session.get(CommunityObservationReport, claimed.id) is None
        retained = await session.get(CommunityObservationReport, anonymous.id)
    assert retained is not None
    assert retained.account_id is None


def test_observed_at_is_timezone_aware_utc_canonical_idempotency_data():
    utc_time = community_reporting.normalize_observed_at(datetime(2026, 9, 1, 10, tzinfo=UTC))
    offset_time = community_reporting.normalize_observed_at(
        datetime.fromisoformat("2026-09-01T15:30:00+05:30")
    )
    assert utc_time == offset_time == datetime(2026, 9, 1, 10, tzinfo=UTC)
    with pytest.raises(ValidationFailedError):
        community_reporting.normalize_observed_at(datetime(2026, 9, 1, 10))


def test_concurrent_winner_resolution_rejects_different_observed_at():
    observed_at = datetime(2026, 9, 1, 10, tzinfo=UTC)
    winner = CommunityObservationReport(
        device_id=uuid.uuid4(), client_report_id="race-report", barcode=BARCODE,
        observation_code="barcode_mismatch", observed_at=observed_at,
    )
    with pytest.raises(ConflictError):
        community_reporting.assert_same_submission_or_conflict(
            winner, barcode=BARCODE, observation_code="barcode_mismatch", batch_number=None,
            context=None, photo_asset_id=None, observed_at=observed_at + timedelta(seconds=1),
        )
    with pytest.raises(ConflictError):
        community_reporting.assert_same_submission_or_conflict(
            winner, barcode="8909999999999", observation_code="barcode_mismatch", batch_number=None,
            context=None, photo_asset_id=None, observed_at=observed_at,
        )
