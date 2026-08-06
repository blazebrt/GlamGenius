"""Admin reservation-stats endpoint.

Verifies that the ``GET /api/v2/access/admin/reservations/stats`` endpoint:

* Refuses non-admin callers with 403.
* Counts live vs consumed vs expired reservations correctly across state
  transitions.
* Includes a stale ``active`` reservation whose ``expires_at`` has passed
  in the ``expired`` bucket, so operators don't under-count between
  housekeeping sweeps.
* Surfaces the top invites currently holding live reservations with the
  correct ``uses_count`` / ``max_uses`` state.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import auth


@pytest.mark.asyncio
async def test_non_admin_cannot_read_reservation_stats(
    app_client, db_clean, registered_supabase_user
):
    token, _ = await registered_supabase_user()
    resp = await app_client.get(
        "/api/v2/access/admin/reservations/stats", headers=auth(token)
    )
    # Admin surfaces return 404 (not 403) so their existence isn't disclosed
    # to non-admins — see ``get_current_admin`` in supabase_auth.
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reservation_stats_counts_all_states(
    app_client, db_clean, fake_supabase_user
):
    from app.domains.beta_access import service as beta
    from app.domains.beta_access.models import (
        InviteRegistrationReservation,
    )
    from app.shared.database.sql import get_sessionmaker

    admin_token, _ = fake_supabase_user(admin=True)

    factory = get_sessionmaker()
    async with factory() as s:
        invite = await beta.create_invite(s, label="stats-test", max_uses=10)
        await s.commit()
        code = invite.code

    # 1× live reservation (not yet finalised)
    r_live = await app_client.post(
        "/api/v2/access/reserve",
        json={"invite_code": code, "email": "live@example.com"},
    )
    assert r_live.status_code == 200

    # 1× consumed reservation
    r_consumed = await app_client.post(
        "/api/v2/access/reserve",
        json={"invite_code": code, "email": "consumed@example.com"},
    )
    challenge = r_consumed.json()["challenge"]
    user_token, _ = fake_supabase_user(email="consumed@example.com")
    r_reg = await app_client.post(
        "/api/v2/access/register",
        headers=auth(user_token),
        json={"registration_challenge": challenge},
    )
    assert r_reg.status_code == 200

    # 1× reservation whose expires_at has passed but was never swept — this
    # is the "stale active" case that must count as expired in the tile.
    async with factory() as s:
        r_stale = InviteRegistrationReservation(
            invite_id=invite.id,
            email_normalised="stale@example.com",
            challenge_hash="a" * 64,
            status=beta.RESERVATION_STATUS_ACTIVE,
            expires_at=beta._now() - timedelta(hours=1),
        )
        s.add(r_stale)
        await s.commit()

    # 1× explicitly swept expired reservation.
    async with factory() as s:
        r_swept = InviteRegistrationReservation(
            invite_id=invite.id,
            email_normalised="swept@example.com",
            challenge_hash="b" * 64,
            status=beta.RESERVATION_STATUS_EXPIRED,
            expires_at=beta._now() - timedelta(hours=2),
        )
        s.add(r_swept)
        await s.commit()

    resp = await app_client.get(
        "/api/v2/access/admin/reservations/stats", headers=auth(admin_token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    totals = body["totals"]
    assert totals["active"] == 1  # only the fresh live one
    assert totals["consumed"] == 1
    # stale-active + explicitly expired both count as expired.
    assert totals["expired"] == 2
    assert totals["total"] == 4

    # Top invites: the invite must appear with 1 live reservation.
    top = body["active_by_invite"]
    assert any(row["code"] == code and row["live_reservations"] == 1 for row in top)
    row = next(row for row in top if row["code"] == code)
    assert row["uses_count"] == 1  # one consumed → uses_count bumped
    assert row["max_uses"] == 10


@pytest.mark.asyncio
async def test_reservation_stats_is_zero_on_empty_project(
    app_client, db_clean, fake_supabase_user
):
    admin_token, _ = fake_supabase_user(admin=True)
    resp = await app_client.get(
        "/api/v2/access/admin/reservations/stats", headers=auth(admin_token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"] == {"total": 0, "active": 0, "consumed": 0, "expired": 0}
    assert body["active_by_invite"] == []
    assert body["generated_at"]
