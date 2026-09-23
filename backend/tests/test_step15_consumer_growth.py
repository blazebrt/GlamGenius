"""Step 15 — Consumer Growth: activation, referral, telemetry, metrics.

Against PostgreSQL 16 and the real routes. A referral code is redeemed through
the real ``/access/reserve`` and ``/access/register``; account deletion runs
through the real deletion state machine; telemetry is written through its real
endpoint. Letters group the suite:

* A — activation and referral eligibility
* R — referral issuance, ceiling, replacement, concurrency
* S — the referral code inside the unchanged invite security protocol
* D — account deletion and the referral capability
* E — growth telemetry: whitelist, idempotency, failure, retention
* P — privacy export and erasure
* M — admin growth metrics and their definitions
* G — guards: no ads, no marketing push, no Commerce, no Store A, no reward
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.api.v2 import growth as growth_api
from app.domains.analytics.models import AppEvent
from app.domains.audit.models import AuditEvent
from app.domains.beta_access import service as beta
from app.domains.beta_access.models import Invite, InviteRedemption, InviteRegistrationReservation
from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.growth import activation, analytics, metrics, referral
from app.domains.growth.models import PROGRAM_VERSION, ConsumerReferralInvite
from app.domains.identity.models import Account
from app.domains.privacy import REGISTRY, Classification, deletion_service
from app.domains.privacy import export as export_service
from app.domains.product.models import LabelSnapshot, ProductWatch, ScanEvent
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select, text, update

from tests.conftest import alembic_head_revision, auth

BACKEND = Path(__file__).resolve().parents[1]
GROWTH_DIR = BACKEND / "app" / "domains" / "growth"
GROWTH_API = BACKEND / "app" / "api" / "v2" / "growth.py"
AS_OF = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
USEFUL = ("found_local", "found_off", "label_captured")
REFERRAL_KEYS = {"program_version", "state", "referral"}
REFERRAL_DETAIL_KEYS = {"code", "expires_at", "remaining_uses"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _factory():
    return get_sessionmaker()


async def _scan(account_id, outcome="found_off", *, at: datetime | None = None, barcode="8901234567890"):
    """One scan event as the scan route records it, optionally backdated."""
    async with _factory()() as session:
        event = ScanEvent(
            account_id=account_id, barcode=barcode, outcome=outcome,
            client_scan_id=uuid.uuid4().hex,
        )
        if at is not None:
            event.created_at = at
            event.updated_at = at
        session.add(event)
        await session.commit()
        return event.id


async def _activated(registered_supabase_user, outcome="found_off"):
    token, account_id = await registered_supabase_user()
    await _scan(account_id, outcome)
    return token, account_id


async def _get(app_client, token) -> dict:
    response = await app_client.get("/api/v2/growth/referral", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def _ensure(app_client, token) -> dict:
    response = await app_client.post("/api/v2/growth/referral", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def _reserve(app_client, code: str, email: str):
    return await app_client.post("/api/v2/access/reserve", json={"invite_code": code, "email": email})


async def _admit(app_client, fake_supabase_user, code: str, email: str | None = None) -> uuid.UUID:
    """The whole unchanged protocol: reserve, Supabase sign-up, register."""
    email = email or f"invitee-{uuid.uuid4().hex[:8]}@example.com"
    reserved = await _reserve(app_client, code, email)
    assert reserved.status_code == 200, reserved.text
    token, uid = fake_supabase_user(email=email)
    registered = await app_client.post(
        "/api/v2/access/register", headers=auth(token),
        json={"registration_challenge": reserved.json()["challenge"]},
    )
    assert registered.status_code == 200, registered.text
    assert registered.json()["invite_redeemed"] is True
    return uid


async def _bound_invites(account_id) -> list[Invite]:
    async with _factory()() as session:
        return list((await session.execute(
            select(Invite)
            .join(ConsumerReferralInvite, ConsumerReferralInvite.invite_id == Invite.id)
            .where(ConsumerReferralInvite.inviter_account_id == account_id)
            .order_by(ConsumerReferralInvite.created_at)
        )).scalars().all())


async def _expire(invite_code: str) -> None:
    async with _factory()() as session:
        await session.execute(
            update(Invite).where(Invite.code == invite_code)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()


async def _count(model, *where) -> int:
    async with _factory()() as session:
        return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def _usable(invite: Invite) -> bool:
    now = datetime.now(UTC)
    return invite.active and (invite.expires_at is None or invite.expires_at > now) and invite.uses_count < invite.max_uses


async def _event(app_client, token, name, properties, client_event_id=None):
    return await app_client.post(
        "/api/v2/growth/events", headers=auth(token),
        json={"name": name, "client_event_id": str(client_event_id or uuid.uuid4()), "properties": properties},
    )


SHARE = {"surface": "product_result", "result": "shared", "referral_included": False}


# ---------------------------------------------------------------------------
# A — activation and referral eligibility
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_signup_alone_is_not_activation(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    assert (await _get(app_client, token))["state"] == "not_activated"
    body = await _ensure(app_client, token)
    assert body == {"program_version": PROGRAM_VERSION, "state": "not_activated", "referral": None}
    assert await _count(Invite) == 0
    assert await _count(ConsumerReferralInvite) == 0


@pytest.mark.asyncio
async def test_a_not_found_alone_does_not_activate(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    for _ in range(3):
        await _scan(account_id, "not_found")
    assert (await _ensure(app_client, token))["state"] == "not_activated"
    assert await _count(Invite) == 0


@pytest.mark.asyncio
async def test_a_a_not_found_scan_through_the_real_route_does_not_activate(
    app_client, db_clean, registered_supabase_user,
):
    from tests.test_step12c_product_watch import _customer

    token, account_id, device = await _customer(app_client, registered_supabase_user)
    recorded = await app_client.post(
        "/api/v2/scan/events", headers=device,
        json={"barcode": "8900000000017", "client_scan_id": uuid.uuid4().hex},
    )
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["product"]["outcome"] == "not_found"
    assert await _count(ScanEvent, ScanEvent.account_id == account_id) == 1
    assert (await _ensure(app_client, token))["state"] == "not_activated"


@pytest.mark.asyncio
async def test_a_anonymous_useful_scans_activate_nobody(app_client, db_clean, registered_supabase_user):
    token, _ = await registered_supabase_user()
    for outcome in USEFUL:
        await _scan(None, outcome)
    assert (await _ensure(app_client, token))["state"] == "not_activated"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", USEFUL)
async def test_a_one_useful_scan_activates_referral_eligibility(
    app_client, db_clean, registered_supabase_user, outcome,
):
    token, account_id = await _activated(registered_supabase_user, outcome)
    before = datetime.now(UTC)
    body = await _ensure(app_client, token)
    assert set(body) == REFERRAL_KEYS
    assert body["state"] == "available"
    assert body["program_version"] == "consumer-referral-v1"
    detail = body["referral"]
    assert set(detail) == REFERRAL_DETAIL_KEYS
    assert detail["remaining_uses"] == 3
    expires = datetime.fromisoformat(detail["expires_at"])
    assert before + timedelta(days=30) - timedelta(minutes=1) <= expires <= datetime.now(UTC) + timedelta(days=30)
    [invite] = await _bound_invites(account_id)
    assert invite.code == detail["code"]
    assert invite.max_uses == 3 and invite.uses_count == 0 and invite.active
    # The invite names the program, never the person.
    assert invite.created_by is None
    assert invite.label == PROGRAM_VERSION
    assert str(account_id) not in json.dumps({"label": invite.label, "code": invite.code})


@pytest.mark.asyncio
async def test_a_a_label_captured_through_the_real_confirm_route_activates(
    app_client, db_clean, off_clean, registered_supabase_user,
):
    from tests.test_step12c_product_watch import _confirm, _customer

    token, account_id, device = await _customer(app_client, registered_supabase_user)
    assert (await _get(app_client, token))["state"] == "not_activated"
    await _confirm(app_client, device, token, account_id)
    async with _factory()() as session:
        assert await activation.has_useful_scan(session, account_id)
    assert (await _ensure(app_client, token))["state"] == "available"


@pytest.mark.asyncio
async def test_a_useful_outcomes_are_exactly_the_three_answers():
    assert activation.USEFUL_SCAN_OUTCOMES == ("found_local", "found_off", "label_captured")
    assert "not_found" not in activation.USEFUL_SCAN_OUTCOMES


# ---------------------------------------------------------------------------
# R — issuance, ceiling, replacement, concurrency
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_r_get_is_read_only(app_client, db_clean, registered_supabase_user):
    token, account_id = await _activated(registered_supabase_user)
    ready = await _get(app_client, token)
    assert ready == {"program_version": PROGRAM_VERSION, "state": "ready", "referral": None}
    assert await _count(Invite) == 0 and await _count(ConsumerReferralInvite) == 0
    issued = await _ensure(app_client, token)
    assert await _get(app_client, token) == issued
    assert await _count(Invite) == 1


@pytest.mark.asyncio
async def test_r_ensure_is_idempotent(app_client, db_clean, registered_supabase_user):
    token, account_id = await _activated(registered_supabase_user)
    first = await _ensure(app_client, token)
    second = await _ensure(app_client, token)
    assert first == second
    assert await _count(Invite) == 1


@pytest.mark.asyncio
async def test_r_concurrent_ensures_converge_on_one_live_code(app_client, db_clean, registered_supabase_user):
    token, account_id = await _activated(registered_supabase_user)
    responses = await asyncio.gather(*[
        app_client.post("/api/v2/growth/referral", headers=auth(token)) for _ in range(8)
    ])
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    codes = {r.json()["referral"]["code"] for r in responses}
    assert len(codes) == 1
    invites = await _bound_invites(account_id)
    assert len(invites) == 1
    assert await _count(ConsumerReferralInvite) == 1


@pytest.mark.asyncio
async def test_r_concurrent_ensures_in_raw_sessions_serialise_on_the_account(db_clean, registered_supabase_user):
    """Below the HTTP layer: the second transaction waits for the first."""
    _, account_id = await _activated(registered_supabase_user)
    factory = _factory()
    first, second = factory(), factory()
    try:
        held = await referral.ensure_referral(first, account_id=account_id)
        waiting = asyncio.create_task(referral.ensure_referral(second, account_id=account_id))
        await asyncio.sleep(0.3)
        assert not waiting.done(), "the second ensure must wait on the account row"
        await first.commit()
        converged = await asyncio.wait_for(waiting, timeout=10)
        await second.commit()
    finally:
        await first.close()
        await second.close()
    assert held.code == converged.code
    assert len(await _bound_invites(account_id)) == 1


@pytest.mark.asyncio
async def test_r_the_lifetime_ceiling_is_three_admissions(
    app_client, db_clean, registered_supabase_user, fake_supabase_user,
):
    token, account_id = await _activated(registered_supabase_user)
    code = (await _ensure(app_client, token))["referral"]["code"]
    for expected_remaining in (2, 1):
        await _admit(app_client, fake_supabase_user, code)
        assert (await _get(app_client, token))["referral"]["remaining_uses"] == expected_remaining
    await _admit(app_client, fake_supabase_user, code)
    assert (await _get(app_client, token)) == {"program_version": PROGRAM_VERSION, "state": "exhausted", "referral": None}
    assert (await _ensure(app_client, token))["state"] == "exhausted"
    # A fourth person is refused with the uniform answer, and no code is minted.
    fourth = await _reserve(app_client, code, "fourth@example.com")
    assert fourth.status_code == 400 and fourth.json()["detail"]["code"] == "invite_invalid"
    invites = await _bound_invites(account_id)
    assert len(invites) == 1 and invites[0].uses_count == 3
    assert await _count(InviteRedemption, InviteRedemption.invite_id == invites[0].id) == 3


@pytest.mark.asyncio
async def test_r_an_expired_code_is_replaced_only_for_remaining_capacity(
    app_client, db_clean, registered_supabase_user, fake_supabase_user,
):
    token, account_id = await _activated(registered_supabase_user)
    first = (await _ensure(app_client, token))["referral"]["code"]
    await _admit(app_client, fake_supabase_user, first)
    await _expire(first)
    assert (await _get(app_client, token))["state"] == "ready"
    replacement = await _ensure(app_client, token)
    assert replacement["state"] == "available"
    second = replacement["referral"]["code"]
    assert second != first
    assert replacement["referral"]["remaining_uses"] == 2
    old, new = await _bound_invites(account_id)
    assert old.code == first and not old.active and old.uses_count == 1
    assert new.code == second and new.max_uses == 2 and new.active
    # The expired code admits nobody, even though it had room left.
    late = await _reserve(app_client, first, "late@example.com")
    assert late.status_code == 400 and late.json()["detail"]["code"] == "invite_invalid"
    await _admit(app_client, fake_supabase_user, second)
    await _admit(app_client, fake_supabase_user, second)
    assert (await _ensure(app_client, token))["state"] == "exhausted"
    assert sum(invite.uses_count for invite in await _bound_invites(account_id)) == 3


@pytest.mark.asyncio
async def test_r_exhausted_accounts_can_never_obtain_a_fourth_admission(
    app_client, db_clean, registered_supabase_user, fake_supabase_user,
):
    """Across two codes, and even after an invitee deletes their account."""
    token, account_id = await _activated(registered_supabase_user)
    first = (await _ensure(app_client, token))["referral"]["code"]
    invitees = [await _admit(app_client, fake_supabase_user, first) for _ in range(2)]
    await _expire(first)
    second = (await _ensure(app_client, token))["referral"]["code"]
    invitees.append(await _admit(app_client, fake_supabase_user, second))
    assert (await _ensure(app_client, token))["state"] == "exhausted"
    # An invitee leaves: their redemption row cascades away. The ceiling is the
    # invites' own monotonic counters, so it does not reopen.
    async with _factory()() as session:
        await session.execute(Account.__table__.delete().where(Account.id == invitees[0]))
        await session.commit()
    assert await _count(InviteRedemption, InviteRedemption.account_id == invitees[0]) == 0
    assert (await _ensure(app_client, token))["state"] == "exhausted"
    assert len(await _bound_invites(account_id)) == 2


@pytest.mark.asyncio
async def test_r_one_usable_code_at_a_time(app_client, db_clean, registered_supabase_user):
    token, account_id = await _activated(registered_supabase_user)
    for _ in range(3):
        code = (await _ensure(app_client, token))["referral"]["code"]
        await _ensure(app_client, token)
        await _expire(code)
    await _ensure(app_client, token)
    invites = await _bound_invites(account_id)
    assert len(invites) == 4
    assert sum(1 for invite in invites if _usable(invite)) == 1
    assert sum(1 for invite in invites if invite.active) == 1


@pytest.mark.asyncio
async def test_r_an_operator_withdrawal_is_not_undone_by_asking_again(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await _activated(registered_supabase_user)
    code = (await _ensure(app_client, token))["referral"]["code"]
    [invite] = await _bound_invites(account_id)
    async with _factory()() as session:
        await beta.deactivate_invite(session, invite.id)
        await session.commit()
    assert (await _ensure(app_client, token)) == {"program_version": PROGRAM_VERSION, "state": "withdrawn", "referral": None}
    assert len(await _bound_invites(account_id)) == 1
    # Once it would have expired anyway, the remaining capacity may be offered.
    await _expire(code)
    replaced = await _ensure(app_client, token)
    assert replaced["state"] == "available" and replaced["referral"]["code"] != code


@pytest.mark.asyncio
async def test_r_another_account_cannot_read_the_inviters_code(app_client, db_clean, registered_supabase_user):
    token, account_id = await _activated(registered_supabase_user)
    code = (await _ensure(app_client, token))["referral"]["code"]
    other_token, _ = await registered_supabase_user()
    for response in (
        await app_client.get("/api/v2/growth/referral", headers=auth(other_token)),
        await app_client.post("/api/v2/growth/referral", headers=auth(other_token)),
    ):
        assert response.status_code == 200
        assert code not in response.text
        assert response.json()["state"] == "not_activated"
    # No route takes an invite, account or code as a parameter.
    paths = {route.path for route in growth_api.router.routes}
    assert paths == {"/growth/referral", "/growth/events", "/admin/growth/metrics"}


@pytest.mark.asyncio
async def test_r_the_response_carries_no_internal_identity(
    app_client, db_clean, registered_supabase_user, fake_supabase_user,
):
    token, account_id = await _activated(registered_supabase_user)
    body = await _ensure(app_client, token)
    invitee = await _admit(app_client, fake_supabase_user, body["referral"]["code"])
    body = await _get(app_client, token)
    [invite] = await _bound_invites(account_id)
    text_body = json.dumps(body)
    for secret in (str(invite.id), str(account_id), str(invitee), "@example.com", "challenge", "reservation"):
        assert secret not in text_body
    assert set(body) == REFERRAL_KEYS and set(body["referral"]) == REFERRAL_DETAIL_KEYS


@pytest.mark.asyncio
async def test_r_anonymous_and_unregistered_callers_are_refused(app_client, db_clean, fake_supabase_user):
    assert (await app_client.get("/api/v2/growth/referral")).status_code in (401, 403)
    token, _ = fake_supabase_user()
    for method in ("get", "post"):
        response = await getattr(app_client, method)("/api/v2/growth/referral", headers=auth(token))
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "REGISTRATION_REQUIRED"


@pytest.mark.asyncio
async def test_r_no_code_is_issued_when_the_beta_is_not_invite_gated(
    monkeypatch, app_client, db_clean, registered_supabase_user,
):
    from app import config

    token, _ = await _activated(registered_supabase_user)
    monkeypatch.setattr(config, "INVITE_REQUIRED", False)
    assert (await _ensure(app_client, token))["state"] == "unavailable"
    assert (await _get(app_client, token))["state"] == "unavailable"
    assert await _count(Invite) == 0


@pytest.mark.asyncio
async def test_r_a_code_collision_is_retried_not_surfaced(monkeypatch, db_clean, registered_supabase_user):
    _, account_id = await _activated(registered_supabase_user)
    async with _factory()() as session:
        await beta.create_invite(session, code="TAKENCODE1")
        await session.commit()
    codes = iter(["TAKENCODE1", "FRESHCODE2"])
    monkeypatch.setattr(beta, "_generate_code", lambda length=10: next(codes))
    async with _factory()() as session:
        state = await referral.ensure_referral(session, account_id=account_id)
        await session.commit()
    assert state.code == "FRESHCODE2"


# ---------------------------------------------------------------------------
# S — the code inside the unchanged invite security protocol
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_s_a_referral_still_needs_reservation_challenge_and_finalisation(
    app_client, db_clean, registered_supabase_user, fake_supabase_user,
):
    token, _ = await _activated(registered_supabase_user)
    code = (await _ensure(app_client, token))["referral"]["code"]
    email = "friend@example.com"
    # Signing up without reserving creates no account, code or no code.
    lonely, lonely_id = fake_supabase_user(email=email)
    refused = await app_client.post("/api/v2/access/register", headers=auth(lonely), json={})
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == "registration_challenge_required"
    # The code itself is not a challenge.
    as_challenge = await app_client.post(
        "/api/v2/access/register", headers=auth(lonely), json={"registration_challenge": code},
    )
    assert as_challenge.status_code == 400 and as_challenge.json()["detail"]["code"] == "reservation_invalid"
    # Still no account: every protected route answers REGISTRATION_REQUIRED.
    gated = await app_client.get("/api/v2/growth/referral", headers=auth(lonely))
    assert gated.status_code == 403 and gated.json()["detail"]["code"] == "REGISTRATION_REQUIRED"
    assert await _count(Account, Account.id == lonely_id) == 0
    # A reservation is bound to its email.
    reserved = await _reserve(app_client, code, email)
    assert reserved.status_code == 200
    # A held place is still not an account: finalisation has not happened.
    held = await app_client.get("/api/v2/growth/referral", headers=auth(lonely))
    assert held.status_code == 403 and held.json()["detail"]["code"] == "REGISTRATION_REQUIRED"
    assert await _count(Account, Account.id == lonely_id) == 0
    stranger, _ = fake_supabase_user(email="someone-else@example.com")
    mismatch = await app_client.post(
        "/api/v2/access/register", headers=auth(stranger),
        json={"registration_challenge": reserved.json()["challenge"]},
    )
    assert mismatch.status_code == 400 and mismatch.json()["detail"]["code"] == "reservation_invalid"
    # The right person with the right challenge is admitted exactly once.
    admitted = await app_client.post(
        "/api/v2/access/register", headers=auth(lonely),
        json={"registration_challenge": reserved.json()["challenge"]},
    )
    assert admitted.status_code == 200 and admitted.json()["invite_redeemed"] is True
    async with _factory()() as session:
        reservation = await session.scalar(select(InviteRegistrationReservation))
    assert reservation.status == "consumed"
    assert reservation.challenge_hash != reserved.json()["challenge"]
    # The same challenge cannot admit anybody else.
    replay_token, _ = fake_supabase_user(email=email)
    replay = await app_client.post(
        "/api/v2/access/register", headers=auth(replay_token),
        json={"registration_challenge": reserved.json()["challenge"]},
    )
    assert replay.status_code == 400


@pytest.mark.asyncio
async def test_s_invalid_referral_codes_get_the_uniform_answer(
    app_client, db_clean, registered_supabase_user, fake_supabase_user,
):
    token, account_id = await _activated(registered_supabase_user)
    code = (await _ensure(app_client, token))["referral"]["code"]
    bodies = [(await _reserve(app_client, "NOSUCHCODE", "a@example.com")).json()]
    await _expire(code)
    bodies.append((await _reserve(app_client, code, "b@example.com")).json())
    fresh = (await _ensure(app_client, token))["referral"]["code"]
    [_, invite] = await _bound_invites(account_id)
    async with _factory()() as session:
        await beta.deactivate_invite(session, invite.id)
        await session.commit()
    bodies.append((await _reserve(app_client, fresh, "c@example.com")).json())
    assert len({json.dumps(body, sort_keys=True) for body in bodies}) == 1
    assert bodies[0]["detail"]["code"] == "invite_invalid"


@pytest.mark.asyncio
async def test_s_reservation_capacity_binds_referral_codes(
    app_client, db_clean, registered_supabase_user, fake_supabase_user,
):
    token, account_id = await _activated(registered_supabase_user)
    code = (await _ensure(app_client, token))["referral"]["code"]
    await _admit(app_client, fake_supabase_user, code)
    await _admit(app_client, fake_supabase_user, code)
    held = await _reserve(app_client, code, "held@example.com")
    assert held.status_code == 200
    # One place left and it is held: nobody else may reserve it.
    crowded = await _reserve(app_client, code, "crowded@example.com")
    assert crowded.status_code == 400 and crowded.json()["detail"]["code"] == "invite_invalid"


@pytest.mark.asyncio
async def test_s_ip_rate_limiting_applies_to_referral_codes(app_client, db_clean, registered_supabase_user):
    token, _ = await _activated(registered_supabase_user)
    code = (await _ensure(app_client, token))["referral"]["code"]
    statuses = [
        (await _reserve(app_client, code, f"flood{i}@example.com")).status_code for i in range(11)
    ]
    assert statuses[-1] == 429


@pytest.mark.asyncio
async def test_s_concurrent_finalisation_cannot_exceed_the_last_place(
    app_client, db_clean, registered_supabase_user, fake_supabase_user,
):
    token, account_id = await _activated(registered_supabase_user)
    code = (await _ensure(app_client, token))["referral"]["code"]
    await _admit(app_client, fake_supabase_user, code)
    await _admit(app_client, fake_supabase_user, code)
    email = "last@example.com"
    reserved = await _reserve(app_client, code, email)
    challenge = reserved.json()["challenge"]
    tokens = [fake_supabase_user(email=email)[0] for _ in range(4)]
    results = await asyncio.gather(*[
        app_client.post("/api/v2/access/register", headers=auth(t), json={"registration_challenge": challenge})
        for t in tokens
    ])
    assert sum(1 for r in results if r.status_code == 200 and r.json()["invite_redeemed"]) == 1
    [invite] = await _bound_invites(account_id)
    assert invite.uses_count == 3


def test_s_the_invite_protocol_has_no_referral_branch():
    """``/access`` and the invite service never ask whether a code is a referral."""
    for path in (
        BACKEND / "app" / "api" / "v2" / "access.py",
        BACKEND / "app" / "domains" / "beta_access" / "service.py",
        BACKEND / "app" / "domains" / "identity" / "service.py",
        BACKEND / "app" / "shared" / "security" / "deps.py",
    ):
        source = path.read_text().lower()
        assert "referral" not in source, path
        assert "growth" not in source, path


# ---------------------------------------------------------------------------
# D — account deletion and the referral capability
# ---------------------------------------------------------------------------
async def _delete_account(account_id) -> None:
    async with _factory()() as session:
        await deletion_service.request_deletion(session, account_id)
        await session.commit()
    async with _factory()() as session:
        assert await deletion_service.drain_all(session) >= 1
        await session.commit()


@pytest.mark.asyncio
async def test_d_deletion_switches_off_the_live_referral_invite(
    app_client, db_clean, registered_supabase_user, fake_supabase_user, media_root,
):
    token, account_id = await _activated(registered_supabase_user)
    code = (await _ensure(app_client, token))["referral"]["code"]
    invitee = await _admit(app_client, fake_supabase_user, code)
    await _delete_account(account_id)
    assert await _count(Account, Account.id == account_id) == 0
    assert await _count(ConsumerReferralInvite) == 0
    async with _factory()() as session:
        invite = await session.scalar(select(Invite).where(Invite.code == code))
    assert invite is not None and invite.active is False
    # No active invite anywhere was issued under the program to anybody gone.
    assert await _count(Invite, Invite.label == PROGRAM_VERSION, Invite.active.is_(True)) == 0
    refused = await _reserve(app_client, code, "after@example.com")
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == "invite_invalid"
    # The invitee's own admission is theirs and stays.
    assert await _count(InviteRedemption, InviteRedemption.account_id == invitee) == 1


@pytest.mark.asyncio
async def test_d_a_deletion_requested_account_is_issued_nothing(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await _activated(registered_supabase_user)
    async with _factory()() as session:
        await deletion_service.request_deletion(session, account_id)
        await session.commit()
    assert (await _ensure(app_client, token))["state"] == "unavailable"
    assert (await _get(app_client, token))["state"] == "unavailable"
    assert await _count(Invite) == 0


@pytest.mark.asyncio
async def test_d_issuance_that_wins_the_race_is_switched_off_by_the_deletion(
    db_clean, registered_supabase_user, media_root,
):
    _, account_id = await _activated(registered_supabase_user)
    factory = _factory()
    issuing = factory()
    try:
        state = await referral.ensure_referral(issuing, account_id=account_id)
        assert state.state == "available"
        deleting = asyncio.create_task(_delete_account(account_id))
        await asyncio.sleep(0.3)
        assert not deleting.done(), "deletion must wait behind the account row"
        await issuing.commit()
        await asyncio.wait_for(deleting, timeout=15)
    finally:
        await issuing.close()
    async with factory() as session:
        invite = await session.scalar(select(Invite).where(Invite.code == state.code))
    assert invite.active is False
    assert await _count(ConsumerReferralInvite) == 0


@pytest.mark.asyncio
async def test_d_deletion_that_wins_the_race_leaves_nothing_to_issue(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await _activated(registered_supabase_user)
    code = (await _ensure(app_client, token))["referral"]["code"]
    await _expire(code)  # so a new ensure would otherwise mint a replacement
    factory = _factory()
    deleting = factory()
    try:
        await deletion_service._deactivate_referral_invites(deleting, account_id)
        issuing_session = factory()
        issuing = asyncio.create_task(referral.ensure_referral(issuing_session, account_id=account_id))
        await asyncio.sleep(0.3)
        assert not issuing.done(), "issuance must wait behind the deletion's account lock"
        await deletion_service._delete_account_row(deleting, account_id)
        await deleting.commit()
        state = await asyncio.wait_for(issuing, timeout=10)
        await issuing_session.commit()
        await issuing_session.close()
    finally:
        await deleting.close()
    assert state.state == "unavailable"
    assert await _count(Invite, Invite.active.is_(True)) == 0
    assert await _count(Invite) == 1


def test_d_deletion_takes_the_account_before_the_invites():
    source = (GROWTH_DIR / "referral.py").read_text()
    body = source[source.index("async def deactivate_referral_invites_for_account"):]
    assert body.index("select(Account.id)") < body.index("update(Invite)")
    ensure = source[source.index("async def ensure_referral"):source.index("async def deactivate_referral")]
    assert ensure.index(".with_for_update()") < ensure.index("_bound_invites(session, account_id, lock=True)")
    worker = (BACKEND / "app" / "domains" / "privacy" / "deletion_service.py").read_text()
    stage = worker[worker.index("if job.state == STATE_DATABASE_DELETING"):]
    assert stage.index("_deactivate_referral_invites") < stage.index("_delete_account_row")


# ---------------------------------------------------------------------------
# E — growth telemetry
# ---------------------------------------------------------------------------
def test_e_only_the_two_growth_events_exist():
    assert analytics.EVENT_NAMES == ("growth.product_result_share", "growth.scan_again")
    assert analytics.EVENT_SCHEMAS["growth.product_result_share"] == {
        "surface": ("product_result",),
        "result": ("shared", "dismissed", "failed"),
        "referral_included": (True, False),
    }
    assert analytics.EVENT_SCHEMAS["growth.scan_again"] == {"surface": ("product_result",)}


def test_e_free_text_is_impossible_in_the_schema():
    for schema in analytics.EVENT_SCHEMAS.values():
        for allowed in schema.values():
            assert isinstance(allowed, tuple) and allowed
            for value in allowed:
                assert isinstance(value, bool) or (isinstance(value, str) and re.fullmatch(r"[a-z_]+", value))


@pytest.mark.asyncio
async def test_e_a_whitelisted_event_is_recorded_once(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    client_event_id = uuid.uuid4()
    first = await _event(app_client, token, "growth.product_result_share", SHARE, client_event_id)
    assert first.status_code == 202 and first.json() == {"recorded": True}
    retry = await _event(app_client, token, "growth.product_result_share", SHARE, client_event_id)
    assert retry.status_code == 202 and retry.json() == {"recorded": False}
    async with _factory()() as session:
        rows = (await session.execute(select(AppEvent))).scalars().all()
    assert len(rows) == 1
    [row] = rows
    assert row.account_id == account_id and row.properties == SHARE
    assert row.request_id is None and row.client_event_id == client_event_id
    # Another account's identical operation id is its own event.
    other, _ = await registered_supabase_user()
    assert (await _event(app_client, other, "growth.product_result_share", SHARE, client_event_id)).json() == {"recorded": True}
    assert await _count(AppEvent) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("name, properties", [
    ("growth.unknown", {"surface": "product_result"}),
    ("scan.completed", {"surface": "product_result"}),
    ("growth.scan_again", {}),
    ("growth.scan_again", {"surface": "product_result", "extra": "x"}),
    ("growth.scan_again", {"surface": "scanner"}),
    ("growth.product_result_share", {"surface": "product_result", "result": "shared"}),
    ("growth.product_result_share", {**SHARE, "result": "whatsapp"}),
    ("growth.product_result_share", {**SHARE, "referral_included": 1}),
    ("growth.product_result_share", {**SHARE, "referral_included": "true"}),
    ("growth.product_result_share", {**SHARE, "destination": "com.whatsapp"}),
    ("growth.product_result_share", {**SHARE, "recipient": "a friend"}),
])
async def test_e_anything_else_fails_closed(app_client, db_clean, registered_supabase_user, name, properties):
    token, _ = await registered_supabase_user()
    response = await _event(app_client, token, name, properties)
    assert response.status_code == 422, response.text
    assert await _count(AppEvent) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["barcode", "product_name", "email", "invite_code", "code", "subject_id", "household_id", "media_id", "ai_run_id", "ip"])
async def test_e_no_identifying_value_can_be_stored(app_client, db_clean, registered_supabase_user, key):
    token, _ = await registered_supabase_user()
    for value in ("8901234567890", "Tasty Oats", "a@example.com", "ABCDEFGH23", str(uuid.uuid4()), "10.0.0.1"):
        as_extra = await _event(app_client, token, "growth.scan_again", {"surface": "product_result", key: value})
        as_value = await _event(app_client, token, "growth.scan_again", {"surface": value})
        assert as_extra.status_code == 422 and as_value.status_code == 422
    assert await _count(AppEvent) == 0


@pytest.mark.asyncio
async def test_e_the_envelope_is_closed_too(app_client, db_clean, registered_supabase_user):
    token, _ = await registered_supabase_user()
    for body in (
        {"name": "growth.scan_again", "client_event_id": str(uuid.uuid4()), "properties": {"surface": "product_result"}, "barcode": "8901234567890"},
        {"name": "growth.scan_again", "client_event_id": "device-1234", "properties": {"surface": "product_result"}},
        {"name": "growth.scan_again", "properties": {"surface": "product_result"}},
    ):
        response = await app_client.post("/api/v2/growth/events", headers=auth(token), json=body)
        assert response.status_code == 422
    assert (await app_client.post("/api/v2/growth/events", json={})).status_code in (401, 403)
    assert await _count(AppEvent) == 0


@pytest.mark.asyncio
async def test_e_a_storage_failure_answers_not_recorded(monkeypatch, app_client, db_clean, registered_supabase_user):
    token, _ = await registered_supabase_user()

    async def _broken(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(analytics, "record_event", _broken)
    response = await _event(app_client, token, "growth.scan_again", {"surface": "product_result"})
    assert response.status_code == 202 and response.json() == {"recorded": False}
    # The referral read the share flow uses is unaffected.
    assert (await app_client.get("/api/v2/growth/referral", headers=auth(token))).status_code == 200


def test_e_product_routes_do_not_depend_on_telemetry():
    for name in ("product.py", "scan.py", "shopping.py", "access.py", "privacy.py"):
        tree = ast.parse((BACKEND / "app" / "api" / "v2" / name).read_text())
        imported = {
            (node.module or "") for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        } | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        assert not any("growth" in module or "analytics" in module for module in imported), name


@pytest.mark.asyncio
async def test_e_the_event_rate_limit_is_per_account(app_client, db_clean, registered_supabase_user):
    token, _ = await registered_supabase_user()
    statuses = [
        (await _event(app_client, token, "growth.scan_again", {"surface": "product_result"})).status_code
        for _ in range(31)
    ]
    assert statuses[:30] == [202] * 30 and statuses[30] == 429
    other, _ = await registered_supabase_user()
    assert (await _event(app_client, other, "growth.scan_again", {"surface": "product_result"})).status_code == 202


@pytest.mark.asyncio
async def test_e_the_raw_code_never_enters_telemetry_or_logs(
    app_client, db_clean, registered_supabase_user, caplog,
):
    caplog.set_level("DEBUG")
    token, _ = await _activated(registered_supabase_user)
    code = (await _ensure(app_client, token))["referral"]["code"]
    await _event(app_client, token, "growth.product_result_share", {**SHARE, "referral_included": True})
    refused = await _event(app_client, token, "growth.scan_again", {"surface": code})
    assert refused.status_code == 422 and code not in refused.text
    async with _factory()() as session:
        stored = json.dumps([row.properties for row in (await session.execute(select(AppEvent))).scalars()])
    assert code not in stored
    assert code not in caplog.text


# --- retention -------------------------------------------------------------
async def _backdated_event(account_id, days_old: int, now: datetime) -> None:
    async with _factory()() as session:
        await analytics.record_event(
            session, account_id=account_id, name="growth.scan_again", client_event_id=uuid.uuid4(),
            properties={"surface": "product_result"}, now=now - timedelta(days=days_old),
        )
        await session.commit()


@pytest.mark.asyncio
async def test_e_retention_removes_only_expired_telemetry(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    now = datetime.now(UTC)
    for days in (91, 120, 89, 1):
        await _backdated_event(account_id, days, now)
    await _scan(account_id, at=now - timedelta(days=200))
    async with _factory()() as session:
        session.add(AuditEvent(
            account_id=account_id, actor_type="user", action="old.audit",
            created_at=now - timedelta(days=400),
        ))
        await session.commit()
    before = {model: await _count(model) for model in (AuditEvent, ScanEvent, Account)}
    async with _factory()() as session:
        removed = await analytics.prune_expired_events(session, now=now)
        await session.commit()
    assert removed == 2
    async with _factory()() as session:
        ages = sorted((now - row.created_at).days for row in (await session.execute(select(AppEvent))).scalars())
    assert ages == [1, 89]
    assert {model: await _count(model) for model in (AuditEvent, ScanEvent, Account)} == before


@pytest.mark.asyncio
async def test_e_retention_is_bounded_and_throttled(monkeypatch, app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    now = datetime.now(UTC)
    for _ in range(5):
        await _backdated_event(account_id, 100, now)
    async with _factory()() as session:
        assert await analytics.prune_expired_events(session, now=now, batch=2) == 2
        await session.commit()
    assert await _count(AppEvent) == 3
    # Through the endpoint: the first write prunes, the next ones in the same
    # interval do not.
    analytics.reset_prune_throttle()
    await _event(app_client, token, "growth.scan_again", {"surface": "product_result"})
    assert await _count(AppEvent) == 1
    await _backdated_event(account_id, 100, now)
    await _event(app_client, token, "growth.scan_again", {"surface": "product_result"})
    assert await _count(AppEvent) == 3


@pytest.mark.asyncio
async def test_e_a_failing_prune_never_fails_the_write(monkeypatch, app_client, db_clean, registered_supabase_user):
    token, _ = await registered_supabase_user()

    async def _broken(*args, **kwargs):
        raise RuntimeError("lock timeout")

    monkeypatch.setattr(analytics, "prune_expired_events", _broken)
    response = await _event(app_client, token, "growth.scan_again", {"surface": "product_result"})
    assert response.status_code == 202 and response.json() == {"recorded": True}


def test_e_retention_names_only_app_events():
    tree = ast.parse((GROWTH_DIR / "analytics.py").read_text())
    [function] = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "prune_expired_events"
    ]
    # The code, not the docstring that names what it must leave alone.
    body = [node for node in function.body if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
    prune = "\n".join(ast.unparse(node) for node in body)
    assert "delete(AppEvent)" in prune
    for other in ("AuditEvent", "audit_events", "Decision", "official", "evidence", "Evidence"):
        assert other not in prune
    assert timedelta(days=90) == analytics.RETENTION


# ---------------------------------------------------------------------------
# P — privacy export and erasure
# ---------------------------------------------------------------------------
def test_p_the_registry_classifies_the_binding_as_account_owned():
    assert REGISTRY["consumer_referral_invites"] == Classification.INCLUDED
    assert REGISTRY["app_events"] == Classification.INCLUDED
    assert "growth" in export_service.DOMAIN_HANDLERS
    from app.domains.privacy import EXPORT_SCHEMA_VERSION

    assert EXPORT_SCHEMA_VERSION == "1.5"


@pytest.mark.asyncio
async def test_p_the_export_carries_the_accounts_own_growth_history(
    app_client, db_clean, registered_supabase_user, fake_supabase_user,
):
    token, account_id = await _activated(registered_supabase_user)
    code = (await _ensure(app_client, token))["referral"]["code"]
    invitee = await _admit(app_client, fake_supabase_user, code)
    client_event_id = uuid.uuid4()
    await _event(app_client, token, "growth.product_result_share", SHARE, client_event_id)
    other, _ = await registered_supabase_user()
    await _event(app_client, other, "growth.scan_again", {"surface": "product_result"})

    export = (await app_client.get("/api/v2/privacy/export", headers=auth(token))).json()
    assert export["schema_version"] == "1.5"
    growth = export["domains"]["growth"]
    assert [event["name"] for event in growth["analytics_events"]] == ["growth.product_result_share"]
    [event] = growth["analytics_events"]
    assert set(event) == {"name", "properties", "created_at"} and event["properties"] == SHARE
    assert growth["referral"]["lifetime_limit"] == 3
    assert growth["referral"]["lifetime_successful_admissions"] == 1
    [issued] = growth["referral"]["issued_codes"]
    assert issued["successful_admissions"] == 1 and issued["active"] is True
    serialised = json.dumps(growth)
    for secret in (code, str(invitee), str(client_event_id), "@example.com"):
        assert secret not in serialised


@pytest.mark.asyncio
async def test_p_erasure_removes_the_accounts_growth_telemetry_and_nobody_elses(
    app_client, db_clean, registered_supabase_user, media_root,
):
    token, account_id = await _activated(registered_supabase_user)
    await _ensure(app_client, token)
    await _event(app_client, token, "growth.scan_again", {"surface": "product_result"})
    other, other_id = await registered_supabase_user()
    await _event(app_client, other, "growth.scan_again", {"surface": "product_result"})
    await _delete_account(account_id)
    assert await _count(AppEvent, AppEvent.account_id == account_id) == 0
    assert await _count(AppEvent) == 1
    assert await _count(AppEvent, AppEvent.account_id == other_id) == 1
    assert await _count(ConsumerReferralInvite) == 0


# ---------------------------------------------------------------------------
# M — admin growth metrics
# ---------------------------------------------------------------------------
async def _account(created_at: datetime) -> uuid.UUID:
    from app.domains.identity import service as identity

    account_id = uuid.uuid4()
    async with _factory()() as session:
        await identity.register_account(session, account_id)
        await session.execute(update(Account).where(Account.id == account_id).values(created_at=created_at))
        await session.commit()
    return account_id


async def _metrics(window_days: int = 30) -> dict:
    async with _factory()() as session:
        return await metrics.growth_metrics(session, now=AS_OF, window_days=window_days)


@pytest.mark.asyncio
async def test_m_activation_counts_useful_scans_only(db_clean):
    found = await _account(AS_OF - timedelta(days=10))
    await _scan(found, "found_off", at=AS_OF - timedelta(days=9))
    missed = await _account(AS_OF - timedelta(days=10))
    await _scan(missed, "not_found", at=AS_OF - timedelta(days=9))
    await _account(AS_OF - timedelta(days=5))
    for outcome in USEFUL:
        await _scan(None, outcome, at=AS_OF - timedelta(days=2))
    older = await _account(AS_OF - timedelta(days=60))
    await _scan(older, "label_captured", at=AS_OF - timedelta(days=3))

    body = await _metrics()
    act = body["activation"]
    assert act["useful_scan_outcomes"] == list(USEFUL)
    assert act["accounts_created"] == 3
    assert act["signup_cohort_activated"] == {"numerator": 1, "denominator": 3, "rate": 0.3333}
    # First useful scans in the window: the new account and the older one.
    assert act["accounts_first_useful_scan_in_window"] == 2


@pytest.mark.asyncio
async def test_m_time_to_first_useful_scan(db_clean):
    for hours in (2, 10, 30):
        account = await _account(AS_OF - timedelta(days=5))
        await _scan(account, at=AS_OF - timedelta(days=5) + timedelta(hours=hours))
    # A scan attached from before sign-up is zero hours, never negative.
    early = await _account(AS_OF - timedelta(days=4))
    await _scan(early, at=AS_OF - timedelta(days=6))
    timing = (await _metrics())["activation"]["time_to_first_useful_scan_hours"]
    assert timing == {"accounts": 4, "median": 6.0, "p75": 15.0}


@pytest.mark.asyncio
async def test_m_the_seven_day_repeat_waits_for_a_mature_cohort(db_clean):
    returned = await _account(AS_OF - timedelta(days=40))
    await _scan(returned, at=AS_OF - timedelta(days=10))
    await _scan(returned, at=AS_OF - timedelta(days=8))
    same_trip = await _account(AS_OF - timedelta(days=40))
    await _scan(same_trip, at=AS_OF - timedelta(days=12))
    await _scan(same_trip, at=AS_OF - timedelta(days=12) + timedelta(hours=2))
    too_late = await _account(AS_OF - timedelta(days=40))
    await _scan(too_late, at=AS_OF - timedelta(days=20))
    await _scan(too_late, at=AS_OF - timedelta(days=11))
    # Three days old: it has not had seven days to come back, so it is not in
    # the denominator, even though it has already returned.
    young = await _account(AS_OF - timedelta(days=4))
    await _scan(young, at=AS_OF - timedelta(days=3))
    await _scan(young, at=AS_OF - timedelta(days=1))

    repeat = (await _metrics())["habit"]["repeat_useful_scan_7d"]
    assert repeat["horizon_days"] == 7
    assert repeat["cohort_end"] == (AS_OF - timedelta(days=7)).isoformat()
    assert repeat["cohort_start"] == (AS_OF - timedelta(days=37)).isoformat()
    assert (repeat["numerator"], repeat["denominator"]) == (1, 3)


@pytest.mark.asyncio
async def test_m_the_thirty_day_repeat_waits_for_a_mature_cohort(db_clean):
    mature = await _account(AS_OF - timedelta(days=80))
    await _scan(mature, at=AS_OF - timedelta(days=40))
    await _scan(mature, at=AS_OF - timedelta(days=25))
    idle = await _account(AS_OF - timedelta(days=80))
    await _scan(idle, at=AS_OF - timedelta(days=45))
    # Twenty days old: never in a thirty-day denominator.
    twenty = await _account(AS_OF - timedelta(days=21))
    await _scan(twenty, at=AS_OF - timedelta(days=20))
    await _scan(twenty, at=AS_OF - timedelta(days=5))

    repeat = (await _metrics())["habit"]["repeat_useful_scan_30d"]
    assert repeat["cohort_end"] == (AS_OF - timedelta(days=30)).isoformat()
    assert (repeat["numerator"], repeat["denominator"], repeat["rate"]) == (1, 2, 0.5)
    seven = (await _metrics())["habit"]["repeat_useful_scan_7d"]
    assert seven["denominator"] == 1  # only the twenty-day-old one is in the 7-day cohort


@pytest.mark.asyncio
async def test_m_an_empty_cohort_has_no_rate(db_clean):
    body = await _metrics()
    assert body["habit"]["repeat_useful_scan_7d"]["rate"] is None
    assert body["activation"]["signup_cohort_activated"] == {"numerator": 0, "denominator": 0, "rate": None}


@pytest.mark.asyncio
async def test_m_scans_per_active_scanner_and_decisions(db_clean):
    first = await _account(AS_OF - timedelta(days=50))
    second = await _account(AS_OF - timedelta(days=50))
    for _ in range(3):
        await _scan(first, at=AS_OF - timedelta(days=3))
    await _scan(second, at=AS_OF - timedelta(days=3))
    await _scan(second, "not_found", at=AS_OF - timedelta(days=3))
    await _scan(first, at=AS_OF - timedelta(days=45))
    habit = (await _metrics())["habit"]
    assert habit["useful_scans_per_active_scanner"] == {"numerator": 4, "denominator": 2, "ratio": 2.0}
    assert habit["accounts_recording_decision"] == 0


@pytest.mark.asyncio
async def test_m_sharing_and_scan_again(db_clean):
    first = await _account(AS_OF - timedelta(days=50))
    second = await _account(AS_OF - timedelta(days=50))
    async with _factory()() as session:
        for account, result, included, days in (
            (first, "shared", True, 2), (first, "shared", False, 3), (second, "dismissed", False, 3),
            (second, "failed", False, 4), (second, "shared", False, 45),
        ):
            await analytics.record_event(
                session, account_id=account, name="growth.product_result_share", client_event_id=uuid.uuid4(),
                properties={"surface": "product_result", "result": result, "referral_included": included},
                now=AS_OF - timedelta(days=days),
            )
        await analytics.record_event(
            session, account_id=first, name="growth.scan_again", client_event_id=uuid.uuid4(),
            properties={"surface": "product_result"}, now=AS_OF - timedelta(days=1),
        )
        await session.commit()
    body = await _metrics()
    assert body["sharing"] == {
        "share_sheet_results": {"shared": 2, "dismissed": 1, "failed": 1},
        "accounts_shared": 1,
        "shares_with_referral_code": 1,
    }
    assert body["habit"]["scan_again_taps"] == 1


@pytest.mark.asyncio
async def test_m_referral_issued_reserved_and_registered(
    app_client, db_clean, registered_supabase_user, fake_supabase_user,
):
    token, _ = await _activated(registered_supabase_user)
    code = (await _ensure(app_client, token))["referral"]["code"]
    await _admit(app_client, fake_supabase_user, code)
    await _reserve(app_client, code, "pending@example.com")
    async with _factory()() as session:
        admin_invite = await beta.create_invite(session, label="admin")
        await session.commit()
    await _admit(app_client, fake_supabase_user, admin_invite.code)
    async with _factory()() as session:
        body = await metrics.growth_metrics(session)
    assert body["referral"] == {
        "invites_issued": 1,
        "accounts_issued_invite": 1,
        "reservations": 2,
        "registrations_completed": 1,
    }


@pytest.mark.asyncio
async def test_m_household_and_product_watch_adoption(db_clean):
    solo = await _account(AS_OF - timedelta(days=50))
    family = await _account(AS_OF - timedelta(days=50))
    removed = await _account(AS_OF - timedelta(days=50))
    old_family = await _account(AS_OF - timedelta(days=90))
    async with _factory()() as session:
        for account, members in (
            (solo, []), (family, [("adult", True, 5)]), (removed, [("child", False, 6)]),
            (old_family, [("other", True, 70)]),
        ):
            circle = FamilyCircle(account_id=account)
            session.add(circle)
            await session.flush()
            # The account holder's own row, dated inside the window for the
            # self-only circle: a self row is never household adoption.
            holder = FamilyProfile(circle_id=circle.id, position=1, relation="self")
            holder.created_at = AS_OF - timedelta(days=5 if account == solo else 50)
            session.add(holder)
            for position, (relation, active, days) in enumerate(members, start=2):
                profile = FamilyProfile(circle_id=circle.id, position=position, relation=relation, active=active)
                profile.created_at = AS_OF - timedelta(days=days)
                session.add(profile)
        await session.commit()

    watcher = await _account(AS_OF - timedelta(days=50))
    stopped = await _account(AS_OF - timedelta(days=50))
    async with _factory()() as session:
        for account, active, days in ((watcher, True, 4), (stopped, False, 70)):
            event = ScanEvent(account_id=account, barcode="8901234567890", outcome="label_captured", client_scan_id=uuid.uuid4().hex)
            session.add(event)
            await session.flush()
            snapshot = LabelSnapshot(
                barcode=f"89{uuid.uuid4().int % 10**11:011d}", scan_event_id=event.id, facts={},
                content_fingerprint="f" * 64, version_number=1,
            )
            session.add(snapshot)
            await session.flush()
            watch = ProductWatch(
                account_id=account, barcode=snapshot.barcode, anchor_scan_event_id=event.id,
                anchor_label_snapshot_id=snapshot.id, anchor_label_version=1, active=active,
                started_at=AS_OF - timedelta(days=days),
                stopped_at=None if active else AS_OF - timedelta(days=days - 1),
            )
            watch.created_at = AS_OF - timedelta(days=days)
            session.add(watch)
        await session.commit()

    adoption = (await _metrics())["adoption"]
    assert adoption["household"] == {
        "accounts_with_household_member_now": 2,
        "accounts_first_household_member_in_window": 2,
    }
    assert adoption["product_watch"] == {
        "accounts_with_active_watch_now": 1,
        "accounts_first_watch_in_window": 1,
    }


@pytest.mark.asyncio
async def test_m_the_route_is_admin_only_and_carries_no_identity(
    app_client, db_clean, registered_supabase_user, fake_supabase_user,
):
    token, account_id = await _activated(registered_supabase_user)
    code = (await _ensure(app_client, token))["referral"]["code"]
    invitee = await _admit(app_client, fake_supabase_user, code)
    await _event(app_client, token, "growth.product_result_share", SHARE)
    customer = await app_client.get("/api/v2/admin/growth/metrics", headers=auth(token))
    assert customer.status_code == 404
    assert (await app_client.get("/api/v2/admin/growth/metrics")).status_code in (401, 403)
    admin_token, _ = fake_supabase_user(admin=True)
    response = await app_client.get("/api/v2/admin/growth/metrics?window_days=30", headers=auth(admin_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metrics_version"] == "growth-metrics-v1"
    for group, keys in metrics.METRIC_KEYS.items():
        assert set(body[group]) - {"useful_scan_outcomes"} == set(keys), group
    serialised = response.text
    uuid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    assert not uuid_pattern.search(serialised)
    for secret in (code, str(account_id), str(invitee), "@example.com", "8901234567890"):
        assert secret not in serialised
    for word in ("premium", "subscription", "revenue", "arpu", "ltv", "conversion", "paid"):
        assert word not in serialised.lower()
    for bad in (0, 91):
        refused = await app_client.get(f"/api/v2/admin/growth/metrics?window_days={bad}", headers=auth(admin_token))
        assert refused.status_code == 422


# ---------------------------------------------------------------------------
# G — guards
# ---------------------------------------------------------------------------
def _growth_sources() -> dict[Path, str]:
    files = sorted(GROWTH_DIR.glob("*.py")) + [GROWTH_API]
    return {path: path.read_text() for path in files}


def _imports(source: str) -> set[str]:
    """Every module a source imports, including ``from package import module``.

    The imported names count too: ``from app.domains.planning import
    notifications`` names the notification module in ``names``, not in
    ``module``, and a guard reading only ``module`` would miss it.
    """
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            modules.add(base)
            modules |= {f"{base}.{alias.name}" for alias in node.names}
        elif isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
    return modules


def test_g_growth_sends_no_notification_and_touches_no_store_a():
    for path, source in _growth_sources().items():
        for module in _imports(source):
            assert "notification" not in module, (path, module)
            assert "app.workers" not in module, (path, module)
            assert "app.domains.off" not in module, (path, module)
            assert "push" not in module, (path, module)


def test_g_the_notification_worker_has_no_growth_topic():
    from app.domains.planning import notifications as planning_notifications

    # The topics are exactly the governed ones that existed before Step 15.
    assert planning_notifications.NOTIFICATION_TOPICS == (
        "today_style", "care", "event_preparation", "maintenance", "product_watch",
    )
    paths = [*(BACKEND / "app" / "workers").rglob("*.py"), BACKEND / "app" / "domains" / "planning" / "notifications.py"]
    for path in paths:
        text_lower = path.read_text().lower()
        for phrase in ("growth", "referral", "invite your", "come back", "haven't opened", "you haven't"):
            assert phrase not in text_lower, (path, phrase)


def test_g_no_reward_commerce_or_ranking_vocabulary_in_growth():
    banned = re.compile(
        r"\b(reward|credit|coin|points|leaderboard|streak|discount|coupon|cashback|affiliate|"
        r"checkout|cart|seller|commission|price|payment|order_id|buy now)\b", re.I,
    )
    for path, source in _growth_sources().items():
        code_only = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        tree = ast.parse(source)
        docstrings = {
            ast.get_docstring(node) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        for doc in filter(None, docstrings):
            code_only = code_only.replace(doc, "")
        assert not banned.search(code_only), (path, banned.search(code_only))


def test_g_no_growth_or_ad_sdk_in_backend_dependencies():
    requirements = (BACKEND / "requirements.txt").read_text().lower()
    for vendor in ("branch", "appsflyer", "adjust", "mixpanel", "amplitude", "segment", "customerio",
                   "customer.io", "onesignal", "facebook", "google-ads", "googleads"):
        assert vendor not in requirements, vendor


def test_g_referral_never_reaches_a_decision_authority():
    decision_roots = [
        BACKEND / "app" / "domains" / name
        for name in ("nutrition", "alternatives", "official_records", "purchase", "evidence", "value", "product")
    ]
    for root in decision_roots:
        for path in root.rglob("*.py"):
            for module in _imports(path.read_text()):
                assert "growth" not in module, (path, module)


def test_g_growth_does_not_import_decision_authorities():
    for path, source in _growth_sources().items():
        for module in _imports(source):
            for authority in ("grading", "alternatives", "official_records", "evidence", "purchase", "value"):
                assert authority not in module, (path, module)


def test_g_one_step15_migration_from_the_step14_head():
    assert alembic_head_revision() == "l0m1n2o3p4"
    source = (BACKEND / "migrations" / "versions" / "l0m1n2o3p4_step15_consumer_growth.py").read_text()
    assert 'down_revision = "k9l0m1n2o3"' in source
    created = re.findall(r'create_table\(\s*"([a-z_]+)"', source)
    added = re.findall(r'add_column\(\s*"([a-z_]+)"', source)
    assert created == ["consumer_referral_invites"] and added == ["app_events"]


@pytest.mark.asyncio
async def test_g_the_live_schema_has_the_binding_rules(db_clean):
    async with _factory()() as session:
        rules = dict((await session.execute(text(
            """
            SELECT kcu.column_name, rc.delete_rule
            FROM information_schema.referential_constraints rc
            JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = rc.constraint_name
            WHERE kcu.table_name = 'consumer_referral_invites'
            """
        ))).all())
        unique = await session.scalar(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_app_events_account_name_client_event'"
        ))
    assert rules == {"inviter_account_id": "CASCADE", "invite_id": "RESTRICT"}
    assert "UNIQUE" in unique and "client_event_id IS NOT NULL" in unique


def test_g_observability_redacts_referral_and_invite_payloads():
    from app.shared.observability.sentry_privacy import scrub_event

    event = {
        "extra": {
            "referral": {"code": "ABCDEFGH23", "remaining_uses": 3},
            "invite_code": "ZYXWVUTS98",
            "status": "available",
        },
        "breadcrumbs": {"values": [{"data": {"referral": {"code": "ABCDEFGH23"}}}]},
    }
    scrubbed = json.dumps(scrub_event(event))
    assert "ABCDEFGH23" not in scrubbed and "ZYXWVUTS98" not in scrubbed
    assert "available" in scrubbed
