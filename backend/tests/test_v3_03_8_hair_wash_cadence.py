"""Pure contract coverage for user-grounded Hair wash cadence."""
from datetime import date

import pytest
from app.domains.care.cadence import (
    CARE_CADENCE_VERSION,
    HairWashCadenceReason,
    HairWashCadenceStatus,
    decide_hair_wash_cadence,
    hair_wash_cadence_fingerprint,
)
from app.domains.planning import clock

from tests.conftest import auth
from tests.test_domain_routines_api import _generate, _seeded_shelf

PLAN = date(2026, 8, 15)


@pytest.mark.parametrize(
    ("frequency", "status", "reason", "interval"),
    [
        ("daily", HairWashCadenceStatus.DUE, HairWashCadenceReason.DAILY_DECLARATION, 1),
        ("less_than_weekly", HairWashCadenceStatus.UNSCHEDULED, HairWashCadenceReason.FREQUENCY_IMPRECISE, None),
        ("variable", HairWashCadenceStatus.UNSCHEDULED, HairWashCadenceReason.FREQUENCY_VARIABLE, None),
        ("not_sure", HairWashCadenceStatus.UNSCHEDULED, HairWashCadenceReason.FREQUENCY_NOT_SURE, None),
        (None, HairWashCadenceStatus.UNSCHEDULED, HairWashCadenceReason.FREQUENCY_MISSING, None),
    ],
)
def test_unambiguous_and_ambiguous_frequency_contract(frequency, status, reason, interval):
    decision = decide_hair_wash_cadence(frequency, plan_date=PLAN, last_wash_on=None)
    assert decision.cadence_version == CARE_CADENCE_VERSION
    assert decision.status is status
    assert decision.reason is reason
    assert decision.interval_days == interval


def test_daily_history_is_due_yesterday_but_not_same_day():
    assert decide_hair_wash_cadence(
        "daily", plan_date=PLAN, last_wash_on=date(2026, 8, 14),
    ).status is HairWashCadenceStatus.DUE
    same_day = decide_hair_wash_cadence("daily", plan_date=PLAN, last_wash_on=PLAN)
    assert same_day.status is HairWashCadenceStatus.NOT_DUE
    assert same_day.next_due_on == date(2026, 8, 16)


def test_future_history_is_not_used_as_an_anchor():
    decision = decide_hair_wash_cadence(
        "weekly", plan_date=PLAN, last_wash_on=date(2026, 8, 20),
    )
    assert decision.status is HairWashCadenceStatus.NEEDS_ANCHOR
    assert decision.last_wash_on is None


@pytest.mark.parametrize(
    ("frequency", "last_wash", "plan_date", "status", "next_due"),
    [
        ("several_times_week", date(2026, 8, 10), date(2026, 8, 11), HairWashCadenceStatus.NOT_DUE, date(2026, 8, 12)),
        ("several_times_week", date(2026, 8, 10), date(2026, 8, 12), HairWashCadenceStatus.DUE, date(2026, 8, 12)),
        ("weekly", date(2026, 8, 3), date(2026, 8, 9), HairWashCadenceStatus.NOT_DUE, date(2026, 8, 10)),
        ("weekly", date(2026, 8, 3), date(2026, 8, 11), HairWashCadenceStatus.DUE, date(2026, 8, 10)),
    ],
)
def test_interval_boundaries(frequency, last_wash, plan_date, status, next_due):
    decision = decide_hair_wash_cadence(frequency, plan_date=plan_date, last_wash_on=last_wash)
    assert decision.status is status
    assert decision.next_due_on == next_due


@pytest.mark.parametrize("frequency", ["several_times_week", "weekly"])
def test_history_is_required_as_anchor_for_non_daily(frequency):
    decision = decide_hair_wash_cadence(frequency, plan_date=PLAN, last_wash_on=None)
    assert decision.status is HairWashCadenceStatus.NEEDS_ANCHOR
    assert decision.reason is HairWashCadenceReason.NO_WASH_HISTORY


def test_fingerprint_is_deterministic_and_semantic():
    decision = decide_hair_wash_cadence("weekly", plan_date=PLAN, last_wash_on=date(2026, 8, 8))
    assert hair_wash_cadence_fingerprint(decision) == hair_wash_cadence_fingerprint(decision)
    changed = decide_hair_wash_cadence("weekly", plan_date=PLAN, last_wash_on=date(2026, 8, 9))
    assert hair_wash_cadence_fingerprint(decision) != hair_wash_cadence_fingerprint(changed)


@pytest.mark.asyncio
async def test_daily_route_surfaces_wash_then_hides_it_after_completion(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "part_of_day", lambda _: "morning")
    token, _ = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    profile = await app_client.patch(
        "/api/v2/profile", headers=auth(token),
        json={"attributes": [{"key": "care_hair_wash_frequency", "value": "daily"}]},
    )
    assert profile.status_code == 200, profile.text
    generated = (await _generate(app_client, token)).json()
    wash = next(row for row in generated["routines"] if row["kind"] == "wash_day")
    shampoo = next(step for step in wash["steps"] if step["slot"] == "shampoo")
    plan_date = "2026-08-17"

    before = (await app_client.get(
        f"/api/v2/routines/today?on={plan_date}", headers=auth(token),
    )).json()
    assert before["hair_wash_cadence"]["status"] == "due"
    assert "wash_day" in {row["kind"] for row in before["routines"]}

    completed = await app_client.post(
        f"/api/v2/routines/steps/{shampoo['id']}/complete", headers=auth(token),
        json={"done_on": plan_date, "completed": True},
    )
    assert completed.status_code == 200, completed.text
    after = (await app_client.get(
        f"/api/v2/routines/today?on={plan_date}", headers=auth(token),
    )).json()
    assert after["hair_wash_cadence"]["status"] == "not_due"
    assert "wash_day" not in {row["kind"] for row in after["routines"]}
    assert after["hair_wash_cadence"]["last_wash_on"] == plan_date
