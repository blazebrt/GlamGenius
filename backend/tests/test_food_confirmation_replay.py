"""Food replay identity, including real independent PostgreSQL transactions."""
from __future__ import annotations

import asyncio
import uuid
from copy import deepcopy

import pytest
from app.domains.ai_gateway.models import VERIFICATION_USER_CONFIRMED, AIRunOutput
from app.domains.product import service
from app.domains.product.models import LabelSnapshot, ProductRecord, ScanEvent
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select, text

from tests import test_product_scan
from tests.conftest import auth
from tests.test_product_scan import _seed_label_run

device = test_product_scan.device
pytestmark = pytest.mark.asyncio
URL = "/api/v2/scan/label/confirm"
BARCODE = "8909999999901"
OTHER = "8909999999902"
FACTS = {
    "product_name": "Replay oats", "ingredients_text": "Oats",
    "nutrition_basis": "per_100g", "nutrition_per_100g": {"sugars_g": "1", "energy_kcal": "370"},
}


async def _confirm(client, device, token, run_id, key, barcode=BARCODE):
    return await client.post(URL, headers={**device, **auth(token)}, json={
        "barcode": barcode, "ai_run_id": str(run_id), "client_scan_id": key,
    })


async def _state():
    async with get_sessionmaker()() as session:
        events = (await session.scalars(select(ScanEvent))).all()
        snapshots = (await session.scalars(select(LabelSnapshot))).all()
        records = (await session.scalars(select(ProductRecord))).all()
        outputs = (await session.scalars(select(AIRunOutput))).all()
        return {
            "events": {row.id: (row.account_id, row.barcode, row.ai_run_id, deepcopy(row.label_facts)) for row in events},
            "snapshots": {row.id: deepcopy(row.facts) for row in snapshots},
            "records": {row.barcode: (row.confirmation_count, row.confidence, row.fssai_licence) for row in records},
            "outputs": {row.ai_run_id: row.verification_status for row in outputs},
        }


async def test_identical_replay_is_order_independent_and_does_not_write_again(
    db_clean, app_client, device, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    run_id = await _seed_label_run(FACTS, account_id)
    key = uuid.uuid4().hex
    first = await _confirm(app_client, device, token, run_id, key)
    assert first.status_code == 201, first.text
    before = await _state()
    async with get_sessionmaker()() as session:
        output = await session.scalar(select(AIRunOutput).where(AIRunOutput.ai_run_id == run_id))
        output.payload = dict(reversed(list(output.payload.items())))
        await session.commit()
    replay = await _confirm(app_client, device, token, run_id, key)
    assert replay.status_code == 201, replay.text
    assert replay.json() == first.json()
    assert await _state() == before
    assert len(before["events"]) == len(before["snapshots"]) == 1


@pytest.mark.parametrize("mismatch", ["barcode", "ai_run_id", "label_facts", "account_id", "outcome"])
async def test_mismatched_replay_is_rejected_before_any_confidence_or_output_change(
    db_clean, app_client, device, registered_supabase_user, mismatch,
):
    token, account_id = await registered_supabase_user()
    run_id = await _seed_label_run(FACTS, account_id)
    key = uuid.uuid4().hex
    first = await _confirm(app_client, device, token, run_id, key)
    assert first.status_code == 201, first.text
    barcode = BARCODE
    if mismatch == "barcode":
        barcode = OTHER
        # Existing Store-B data must not make an unrelated barcode look like a
        # truthful replay merely because the product happens to be known.
        async with get_sessionmaker()() as session:
            session.add(ProductRecord(barcode=OTHER, confidence="unverified"))
            await session.commit()
    elif mismatch == "ai_run_id":
        run_id = await _seed_label_run(FACTS, account_id)
    elif mismatch == "account_id":
        token, account_id = await registered_supabase_user()
        run_id = await _seed_label_run(FACTS, account_id)
    elif mismatch == "label_facts":
        async with get_sessionmaker()() as session:
            output = await session.scalar(select(AIRunOutput).where(AIRunOutput.ai_run_id == run_id))
            output.payload = {**FACTS, "nutrition_per_100g": {"sugars_g": "99"}}
            await session.commit()
    elif mismatch == "outcome":
        async with get_sessionmaker()() as session:
            event = await session.scalar(select(ScanEvent).where(ScanEvent.client_scan_id == key))
            event.outcome = service.OUTCOME_NOT_FOUND
            await session.commit()
    before = await _state()
    replay = await _confirm(app_client, device, token, run_id, key, barcode)
    assert replay.status_code == 409, replay.text
    assert replay.json()["detail"]["conflicting_field"] == mismatch
    assert replay.json()["detail"]["retryable"] is False
    assert await _state() == before


async def test_concurrent_identical_confirmation_creates_one_event_and_snapshot(
    db_clean, app_client, device, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    run_id = await _seed_label_run(FACTS, account_id)
    key = uuid.uuid4().hex
    barrier = asyncio.Barrier(2)
    real_lock = service.lock_label_version
    sessions = set()
    backend_pids = set()

    async def concurrent_lock(session, barcode):
        if id(session) not in sessions:
            sessions.add(id(session))
            backend_pids.add(await session.scalar(text("SELECT pg_backend_pid()")))
            # Rendezvous BEFORE the first same-barcode lock. Snapshot storage
            # re-enters that lock in the same session and needs no partner.
            await asyncio.wait_for(barrier.wait(), timeout=10)
        await real_lock(session, barcode)

    monkeypatch.setattr(service, "lock_label_version", concurrent_lock)
    responses = await asyncio.wait_for(asyncio.gather(
        _confirm(app_client, device, token, run_id, key),
        _confirm(app_client, device, token, run_id, key),
        return_exceptions=True,
    ), timeout=20)
    assert len(sessions) == len(backend_pids) == 2
    assert all(not isinstance(response, BaseException) for response in responses), responses
    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json() == responses[1].json()
    state = await _state()
    assert len(state["events"]) == len(state["snapshots"]) == 1
    assert state["records"][BARCODE][0] == responses[0].json()["confirmations"]


async def test_concurrent_different_confirmation_checks_the_unique_constraint_winner(
    db_clean, app_client, device, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    run_a = await _seed_label_run(FACTS, account_id)
    run_b = await _seed_label_run({**FACTS, "product_name": "Other oats"}, account_id)
    key = uuid.uuid4().hex
    async with get_sessionmaker()() as session:
        session.add_all([ProductRecord(barcode=code, confidence="unverified") for code in (BARCODE, OTHER)])
        await session.commit()
    real_lookup = service._existing_scan_event
    barrier = asyncio.Barrier(2)
    initial_sessions = set()
    backend_pids = set()
    recovered = []

    async def rendezvous(session, **kwargs):
        row = await real_lookup(session, **kwargs)
        if id(session) not in initial_sessions:
            initial_sessions.add(id(session))
            backend_pids.add(await session.scalar(text("SELECT pg_backend_pid()")))
            assert row is None  # Both actual READ COMMITTED lookups missed.
            await asyncio.wait_for(barrier.wait(), timeout=10)
        else:
            recovered.append(row)
        return row

    monkeypatch.setattr(service, "_existing_scan_event", rendezvous)
    responses = await asyncio.wait_for(asyncio.gather(
        _confirm(app_client, device, token, run_a, key, BARCODE),
        _confirm(app_client, device, token, run_b, key, OTHER),
        return_exceptions=True,
    ), timeout=20)
    assert len(initial_sessions) == len(backend_pids) == 2
    assert all(not isinstance(response, BaseException) for response in responses), responses
    assert sorted(response.status_code for response in responses) == [201, 409], [row.text for row in responses]
    assert len(recovered) == 1 and recovered[0] is not None
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.json()["detail"]["conflicting_field"] == "barcode"
    state = await _state()
    assert len(state["events"]) == len(state["snapshots"]) == 1
    assert list(state["outputs"].values()).count(VERIFICATION_USER_CONFIRMED) == 1
    winner = next(response for response in responses if response.status_code == 201)
    assert state["records"][winner.json()["barcode"]][0] == winner.json()["confirmations"]
