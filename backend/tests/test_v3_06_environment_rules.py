"""The environment acts: the NAQI fix, the ten rules, and the precedence.

Two things are load-bearing here. The first is that an Indian location gets an
Indian category — a European AQI saturates above 100 and cannot tell NAQI 150
from NAQI 450, which is the whole range that matters in a North Indian winter.
The second is that only one decision reaches the person on a day when four
rules are true.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from app.domains.care import environment_service
from app.domains.care.environment_decision import (
    EnvironmentAction,
    EnvironmentDay,
    EnvironmentWindow,
    evaluate_environment,
)
from app.domains.care.environment_rules import (
    ENVIRONMENT_RULES,
    PRECEDENCE_ORDER,
)
from app.domains.identity import service as identity
from app.domains.planning.environment import (
    determine_naqi_category,
    naqi_at_least,
    naqi_category,
    naqi_from_particulates,
    naqi_sub_index,
)
from app.domains.planning.models import AirQualitySnapshot, WeatherSnapshot
from app.shared.database.sql import get_sessionmaker

TODAY = date(2026, 11, 15)


def _day(offset: int = 0, *, aqi=None, category=None, humidity=None, temp=None,
         uv=None, precipitation=None, condition=None) -> EnvironmentDay:
    return EnvironmentDay(
        for_date=TODAY - timedelta(days=offset),
        aqi=aqi,
        index_system="india_naqi" if category is not None else None,
        category=category,
        humidity=humidity,
        temp_max_c=temp,
        uv_index=uv,
        precipitation_chance=precipitation,
        condition=condition,
    )


def _run(days: int, category: str, aqi: int) -> tuple[EnvironmentDay, ...]:
    """``days`` history entries at one category, oldest first, ending yesterday."""
    return tuple(_day(offset, aqi=aqi, category=category) for offset in range(days, 0, -1))


# ---------------------------------------------------------------------------
# The correctness bug
# ---------------------------------------------------------------------------
def test_naqi_340_is_very_poor_not_satisfactory():
    """The acceptance case. A European scale calls 340 nothing useful at all."""
    assert naqi_category(340) == "Very Poor"
    assert naqi_category(340) != "Satisfactory"
    assert determine_naqi_category(340, "india_naqi") == "Very Poor"


@pytest.mark.parametrize(
    ("aqi", "expected"),
    [
        (0, "Good"), (50, "Good"), (51, "Satisfactory"), (100, "Satisfactory"),
        (101, "Moderate"), (200, "Moderate"), (201, "Poor"), (300, "Poor"),
        (301, "Very Poor"), (400, "Very Poor"), (401, "Severe"), (500, "Severe"),
        (999, "Severe"),
    ],
)
def test_every_published_cpcb_band_boundary(aqi, expected):
    assert naqi_category(aqi) == expected


def test_an_unknown_reading_says_unknown_rather_than_good():
    assert naqi_category(None) == "unknown"
    assert naqi_category(-1) == "unknown"


@pytest.mark.parametrize(
    ("pm2_5", "pm10", "expected_index", "expected_category"),
    [
        # CPCB breakpoint anchors: each concentration sits on a published edge.
        (30.0, None, 50, "Good"),
        (60.0, None, 100, "Satisfactory"),
        (90.0, None, 200, "Moderate"),
        (120.0, None, 300, "Poor"),
        (250.0, None, 400, "Very Poor"),
        (None, 50.0, 50, "Good"),
        (None, 250.0, 200, "Moderate"),
        (None, 430.0, 400, "Very Poor"),
        # A Delhi winter morning. The European index would have said "very poor"
        # and stopped; this distinguishes it from a merely bad day.
        (170.0, 300.0, 339, "Very Poor"),
    ],
)
def test_cpcb_breakpoints_produce_the_published_index(pm2_5, pm10, expected_index, expected_category):
    index, _ = naqi_from_particulates(pm2_5, pm10)
    assert index == expected_index
    assert naqi_category(index) == expected_category


def test_the_index_is_the_worst_of_the_two_particulates():
    """CPCB takes the maximum sub-index, not an average."""
    index, prominent = naqi_from_particulates(20.0, 300.0)
    assert prominent == "pm10"
    assert index == naqi_sub_index(300.0, __import__(
        "app.domains.planning.environment", fromlist=["CPCB_PM10_BREAKPOINTS"]
    ).CPCB_PM10_BREAKPOINTS)


def test_no_particulate_reading_produces_no_index():
    """Better to say nothing than to invent an index from nothing."""
    assert naqi_from_particulates(None, None) == (None, None)


def test_a_non_indian_index_is_never_relabelled_as_an_indian_category():
    """A European category is not an Indian one, whatever its number."""
    assert determine_naqi_category(75, "european_aqi", "Fair") == "Fair"
    assert determine_naqi_category(75, "us_aqi", "Moderate") == "Moderate"


@pytest.mark.asyncio
async def test_an_indian_location_returns_an_indian_category(monkeypatch):
    """End to end through the provider: Delhi air comes back on the Indian scale."""
    import httpx
    from app.domains.planning.providers import open_meteo

    monkeypatch.setattr(open_meteo, "OPEN_METEO_MODE", "evaluation")
    day = date(2026, 11, 15)
    monkeypatch.setattr(open_meteo.clock, "local_today", lambda timezone_name: day)

    def handler(request: httpx.Request) -> httpx.Response:
        if "geocoding" in str(request.url):
            return httpx.Response(200, json={"results": [
                {"country_code": "IN", "latitude": 28.61, "longitude": 77.21, "name": "Delhi"}
            ]})
        return httpx.Response(200, json={"hourly": {
            "time": [f"{day.isoformat()}T00:00"],
            # The European index is pinned at its ceiling and says nothing more.
            "european_aqi": [100],
            "european_aqi_pm2_5": [100], "european_aqi_pm10": [90],
            "european_aqi_nitrogen_dioxide": [15], "european_aqi_ozone": [10],
            "european_aqi_sulphur_dioxide": [5],
            "pm2_5": [170.0], "pm10": [300.0],
        }})

    provider = open_meteo.OpenMeteoProvider(transport=httpx.MockTransport(handler))
    air = await provider.air_quality(location="Delhi", dates=[day], timezone_name="Asia/Kolkata")

    assert air[0].index_system == "india_naqi"
    assert air[0].category == "Very Poor"
    assert air[0].aqi == 339
    # The European reading is kept, but never as the category shown.
    assert air[0].raw["european_aqi"] == 100
    assert air[0].raw["naqi_basis"] == "pm2_5_pm10_only"
    assert air[0].raw["naqi_source"].startswith("CPCB")


# ---------------------------------------------------------------------------
# Ten rules, each with an id and a source
# ---------------------------------------------------------------------------
def test_there_are_ten_rules_each_with_a_stable_id():
    assert len(ENVIRONMENT_RULES) == 10
    ids = [rule.rule_id for rule in ENVIRONMENT_RULES]
    assert len(set(ids)) == 10
    assert all(rule_id.startswith("care.env.") for rule_id in ids)


def test_precedence_is_a_total_order_with_no_ties():
    values = [rule.precedence for rule in ENVIRONMENT_RULES]
    assert len(set(values)) == len(values)
    assert len(PRECEDENCE_ORDER) == 10


def test_every_rule_has_a_reviewed_source_in_the_seed():
    from app.domains.evidence.environment_seed import CLAIM_DEFS, LINK_DEFS

    assert len(CLAIM_DEFS) == len(ENVIRONMENT_RULES)
    linked_claims = {claim_key for claim_key, _, _ in LINK_DEFS}
    for claim in CLAIM_DEFS:
        assert claim["claim_key"] in linked_claims, f"{claim['claim_key']} has no source"
        assert claim["review_status"] == "approved"


def test_no_rule_claims_a_health_outcome():
    """LEGAL_RULES.md: state the reading, never the effect on a body."""
    forbidden = (
        "damag", "protect your skin", "prevent", "harm", "heal", "cure",
        "treat", "safe from", "detox",
    )
    for rule in ENVIRONMENT_RULES:
        text = " ".join([rule.headline, rule.reason_template, rule.note]).lower()
        for word in forbidden:
            assert word not in text, f"{rule.rule_id} says {word!r}"


def test_every_air_rule_states_the_reading_and_the_published_category():
    air_rules = [
        rule for rule in ENVIRONMENT_RULES
        if "{aqi}" in rule.reason_template or "{category}" in rule.reason_template
    ]
    assert air_rules, "no rule reports an air reading"
    for rule in air_rules:
        assert "{aqi}" in rule.reason_template, rule.rule_id
        assert "CPCB category {category}" in rule.reason_template, rule.rule_id


# ---------------------------------------------------------------------------
# Each rule fires on its own condition
# ---------------------------------------------------------------------------
def test_rule_1_poor_air_defers_strong_actives():
    decision = evaluate_environment(EnvironmentWindow(today=_day(aqi=250, category="Poor")))
    assert decision.rule_id == "care.env.poor_defer_strong_actives"
    assert decision.action is EnvironmentAction.DEFER_ACTIVE
    assert "NAQI 250" in decision.reason
    assert "CPCB category Poor" in decision.reason


def test_rule_2_very_poor_air_adds_a_post_exposure_cleanse():
    decision = evaluate_environment(EnvironmentWindow(today=_day(aqi=340, category="Very Poor")))
    assert decision.rule_id == "care.env.very_poor_post_exposure_cleanse"
    assert decision.action is EnvironmentAction.ADD_STEP


def test_rule_3_very_poor_air_never_increases_hair_wash_frequency():
    window = EnvironmentWindow(today=_day(aqi=340, category="Very Poor"))
    decision = evaluate_environment(
        window, allowed_rule_ids=frozenset({"care.env.very_poor_hair_hold_cadence"}),
    )
    assert decision.rule_id == "care.env.very_poor_hair_hold_cadence"
    assert decision.action is EnvironmentAction.HOLD_CADENCE
    lowered = f"{decision.headline} {decision.reason}".lower()
    assert "wash" in lowered
    # The intuitive answer is "wash more". This rule exists to say the opposite.
    for phrase in ("wash more", "increase", "extra wash", "more often"):
        assert phrase not in lowered


def test_rule_4_five_consecutive_poor_days_adds_antioxidant_support():
    window = EnvironmentWindow(
        today=_day(aqi=250, category="Poor"), history=_run(4, "Poor", 250),
    )
    decision = evaluate_environment(
        window, allowed_rule_ids=frozenset({"care.env.sustained_poor_antioxidant_am"}),
    )
    assert decision.rule_id == "care.env.sustained_poor_antioxidant_am"
    assert "5 days" in decision.reason


def test_rule_4_does_not_fire_on_four_days():
    window = EnvironmentWindow(
        today=_day(aqi=250, category="Poor"), history=_run(3, "Poor", 250),
    )
    assert evaluate_environment(
        window, allowed_rule_ids=frozenset({"care.env.sustained_poor_antioxidant_am"}),
    ) is None


def test_rule_5_one_clean_day_is_not_yet_a_resumption():
    window = EnvironmentWindow(
        today=_day(aqi=80, category="Satisfactory"), history=_run(5, "Poor", 250),
    )
    decision = evaluate_environment(window)
    assert decision.rule_id == "care.env.resume_needs_two_clean_days"
    assert decision.action is EnvironmentAction.DEFER_ACTIVE
    assert "two clean days" in decision.reason


def test_rule_6_two_clean_days_gives_the_deferred_act_back():
    window = EnvironmentWindow(
        today=_day(aqi=70, category="Satisfactory"),
        history=(*_run(5, "Poor", 250)[:-1], _day(1, aqi=80, category="Satisfactory")),
    )
    decision = evaluate_environment(window)
    assert decision.rule_id == "care.env.air_cleared_resume_actives"
    assert decision.action is EnvironmentAction.RESTORE
    assert "available again" in decision.headline


def test_rule_6_stays_quiet_when_nothing_was_ever_deferred():
    """Two clean days after two clean days is not news."""
    window = EnvironmentWindow(
        today=_day(aqi=40, category="Good"), history=_run(5, "Good", 40),
    )
    assert evaluate_environment(window) is None


def test_rule_7_humid_heat_asks_for_lighter_formulations():
    window = EnvironmentWindow(today=_day(humidity=80, temp=34.0))
    decision = evaluate_environment(window)
    assert decision.rule_id == "care.env.humid_heat_occlusion"
    lowered = f"{decision.headline} {decision.reason}".lower()
    assert "light" in lowered
    assert "friction" in lowered
    assert "wash frequency stays" in lowered


def test_rule_8_outranks_rules_1_and_7_when_all_three_apply():
    """The stated precedence requirement, checked directly."""
    window = EnvironmentWindow(today=_day(aqi=250, category="Poor", humidity=30, temp=34.0))
    decision = evaluate_environment(window)
    assert decision.rule_id == "care.env.dry_air_and_poor_naqi"
    assert "care.env.poor_defer_strong_actives" in decision.fired_rule_ids
    assert "care.env.humid_heat_occlusion" not in decision.fired_rule_ids
    # Rule 1's note would only repeat the primary, so it is not used.
    assert decision.note_rule_id != "care.env.poor_defer_strong_actives"


def test_rule_9_high_uv_moves_photosensitising_actives_to_the_evening():
    decision = evaluate_environment(EnvironmentWindow(today=_day(uv=9.0)))
    assert decision.rule_id == "care.env.high_uv_photosensitivity"
    lowered = f"{decision.headline} {decision.reason}".lower()
    assert "evening" in lowered
    assert "sunscreen" in lowered
    # Mandatory, not optional.
    assert "not an optional extra" in lowered


def test_rule_9_does_not_fire_below_the_published_threshold():
    assert evaluate_environment(EnvironmentWindow(today=_day(uv=2.0))) is None


def test_rule_10_rain_after_a_poor_stretch_opens_a_recovery_window():
    window = EnvironmentWindow(
        today=_day(aqi=150, category="Moderate", precipitation=90, condition="rainy"),
        history=_run(3, "Poor", 260),
    )
    decision = evaluate_environment(window)
    assert decision.rule_id == "care.env.rain_recovery_window"
    assert decision.action is EnvironmentAction.RESTORE
    assert "available again" in decision.headline


def test_rule_10_needs_the_poor_stretch_not_just_rain():
    window = EnvironmentWindow(
        today=_day(aqi=60, category="Satisfactory", precipitation=90, condition="rainy"),
        history=_run(3, "Good", 40),
    )
    assert evaluate_environment(window) is None


def test_rule_10_needs_the_rain_to_have_actually_cleared_the_air():
    """Rain on a day that is still Very Poor is not a window to resume.

    Both rules would otherwise be true at once, and because a restore is
    preferred as the supporting note the day would defer strong actives in its
    headline and invite them back underneath.
    """
    window = EnvironmentWindow(
        today=_day(aqi=340, category="Very Poor", precipitation=90, condition="rainy"),
        history=_run(3, "Poor", 260),
    )
    decision = evaluate_environment(window)
    assert decision is not None
    assert decision.rule_id != "care.env.rain_recovery_window"
    assert decision.action is not EnvironmentAction.RESTORE
    if decision.note is not None:
        assert "available again" not in decision.note


# ---------------------------------------------------------------------------
# Precedence: exactly one primary decision
# ---------------------------------------------------------------------------
def test_only_one_primary_decision_when_five_rules_fire():
    """A Delhi November morning. Never four instructions in one day."""
    window = EnvironmentWindow(
        today=_day(aqi=340, category="Very Poor", humidity=28, temp=24.0, uv=6.0),
        history=_run(6, "Very Poor", 330),
    )
    decision = evaluate_environment(window)
    assert len(decision.fired_rule_ids) >= 4
    assert decision.rule_id == "care.env.dry_air_and_poor_naqi"
    # One decision, and at most one extra line.
    assert isinstance(decision.headline, str)
    assert decision.note is None or isinstance(decision.note, str)
    payload = decision.as_payload()
    assert isinstance(payload["headline"], str)
    assert payload["note"] is None or isinstance(payload["note"], str)


def test_a_restoration_is_preferred_as_the_supporting_note():
    """When a day both restricts and gives back, the giving back is said."""
    window = EnvironmentWindow(
        today=_day(aqi=70, category="Satisfactory", uv=9.0),
        history=(*_run(4, "Poor", 250)[:-1], _day(1, aqi=80, category="Satisfactory")),
    )
    decision = evaluate_environment(window)
    assert decision.rule_id == "care.env.high_uv_photosensitivity"
    assert decision.note_rule_id == "care.env.air_cleared_resume_actives"
    assert "available again" in decision.note


def test_a_quiet_day_says_nothing_at_all():
    window = EnvironmentWindow(today=_day(aqi=40, category="Good", humidity=50, temp=22.0, uv=2.0))
    assert evaluate_environment(window) is None


def test_the_evidence_gate_silences_a_rule_with_no_reviewed_source():
    window = EnvironmentWindow(today=_day(aqi=340, category="Very Poor"))
    assert evaluate_environment(window, allowed_rule_ids=frozenset()) is None


def test_a_missing_reading_breaks_a_streak_rather_than_filling_it_in():
    """An unknown day is unknown; it is not quietly treated as clean or dirty."""
    history = (
        *_run(5, "Poor", 250)[:2],
        _day(3),                      # no reading at all
        *_run(2, "Poor", 250),
    )
    window = EnvironmentWindow(today=_day(aqi=250, category="Poor"), history=history)
    decision = evaluate_environment(
        window, allowed_rule_ids=frozenset({"care.env.sustained_poor_antioxidant_am"}),
    )
    assert decision is None


def test_a_european_reading_never_drives_an_indian_rule():
    """The bug this whole change exists to fix, checked at the rules boundary."""
    european = EnvironmentDay(
        for_date=TODAY, aqi=95, index_system="european_aqi", category="Poor",
    )
    assert evaluate_environment(EnvironmentWindow(today=european)) is None


def test_naqi_ordering_helper_matches_the_published_scale():
    assert naqi_at_least("Severe", "Poor") is True
    assert naqi_at_least("Moderate", "Poor") is False
    assert naqi_at_least(None, "Poor") is False
    assert naqi_at_least("Fair", "Poor") is False   # not an Indian category


# ---------------------------------------------------------------------------
# Wired in
# ---------------------------------------------------------------------------
async def _seed_environment(account_id, rows, *, anchor: date | None = None):
    """Write air-quality and weather snapshots the way planning stores them.

    ``anchor`` is the day the readings end on. The wired-in tests anchor on the
    planner's real local today, because that is the day Today compiles for.
    """
    factory = get_sessionmaker()
    anchor = anchor or TODAY
    async with factory() as session:
        for offset, row in rows:
            for_date = anchor - timedelta(days=offset)
            if row.get("category"):
                session.add(AirQualitySnapshot(
                    account_id=account_id, for_date=for_date, aqi=row["aqi"],
                    index_system="india_naqi", category=row["category"],
                    provider="manual", source="user_declared",
                ))
            session.add(WeatherSnapshot(
                account_id=account_id, for_date=for_date,
                condition=row.get("condition", "clear"),
                humidity=row.get("humidity"), temp_max_c=row.get("temp"),
                uv_index=row.get("uv"), precipitation_chance=row.get("precipitation"),
                provider="manual", source="user_declared",
            ))
        await session.commit()


@pytest.mark.asyncio
async def test_a_severe_air_day_changes_the_daily_view(db_clean, app_client, registered_supabase_user):
    """The acceptance criterion: severe air visibly changes what Today says."""
    from app.bootstrap import run as run_seed

    from tests.conftest import auth

    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)

    token, account_id = await registered_supabase_user()
    from app.domains.planning import clock

    plan_date = clock.local_today("Asia/Kolkata")
    await _seed_environment(account_id, [
        (offset, {"aqi": 340, "category": "Very Poor", "humidity": 28, "temp": 24.0, "uv": 4.0})
        for offset in range(3, -1, -1)
    ], anchor=plan_date)

    response = await app_client.get("/api/v2/today", headers=auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    actions = [*body["primary"], *body["optional_modules"]]
    environment = [row for row in actions if row["action_type"] == "environment_decision"]
    assert len(environment) == 1, "exactly one environment decision reaches the day"
    assert "NAQI 340" in environment[0]["body"]
    assert "CPCB category Very Poor" in environment[0]["body"]


@pytest.mark.asyncio
async def test_the_daily_view_shows_no_environment_row_on_a_quiet_day(
    db_clean, app_client, registered_supabase_user
):
    from app.bootstrap import run as run_seed

    from tests.conftest import auth

    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)

    token, account_id = await registered_supabase_user()
    from app.domains.planning import clock

    plan_date = clock.local_today("Asia/Kolkata")
    await _seed_environment(account_id, [
        (offset, {"aqi": 40, "category": "Good", "humidity": 50, "temp": 22.0, "uv": 2.0})
        for offset in range(3, -1, -1)
    ], anchor=plan_date)

    response = await app_client.get("/api/v2/today", headers=auth(token))
    body = response.json()
    actions = [*body["primary"], *body["optional_modules"]]
    assert [row for row in actions if row["action_type"] == "environment_decision"] == []


@pytest.mark.asyncio
async def test_the_service_reads_the_stored_snapshots(db_clean):
    from app.bootstrap import run as run_seed

    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await session.commit()

    await _seed_environment(account_id, [
        (offset, {"aqi": 260, "category": "Poor", "humidity": 55, "temp": 22.0, "uv": 2.0})
        for offset in range(5, -1, -1)
    ])
    async with factory() as session:
        window = await environment_service.load_window(
            session, account_id=account_id, plan_date=TODAY,
        )
        assert window.consecutive_at_least("Poor") == 6
        decision = await environment_service.decide_for_day(
            session, account_id=account_id, plan_date=TODAY,
        )
    assert decision is not None
    assert decision.rule_id == "care.env.poor_defer_strong_actives"


@pytest.mark.asyncio
async def test_every_rule_passes_the_evidence_gate_once_seeded(db_clean):
    """All ten have a complete reviewed path after the seed runs."""
    from app.bootstrap import run as run_seed

    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        allowed = await environment_service.allowed_environment_rule_ids(session)
    assert allowed == {rule.rule_id for rule in ENVIRONMENT_RULES}


@pytest.mark.asyncio
async def test_the_notification_fires_on_a_crossing_not_every_day(db_clean):
    """A daily "the air is bad" message trains people to ignore it."""
    from app.bootstrap import run as run_seed
    from app.domains.planning import notifications

    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await session.commit()

    # Day one of a poor stretch: yesterday was Moderate, today is Poor.
    await _seed_environment(account_id, [
        (2, {"aqi": 150, "category": "Moderate"}),
        (1, {"aqi": 150, "category": "Moderate"}),
        (0, {"aqi": 260, "category": "Poor"}),
    ])
    async with factory() as session:
        crossing = await notifications.queue_for_environment_crossing(
            session, account_id=account_id, plan_date=TODAY, timezone_name="Asia/Kolkata",
        )
        assert crossing is not None
        assert "NAQI 260" in crossing.body
        await session.rollback()

    # The middle of the same stretch: nothing crossed, so nothing is sent.
    async with factory() as session:
        quiet = await notifications.queue_for_environment_crossing(
            session, account_id=account_id, plan_date=TODAY - timedelta(days=1),
            timezone_name="Asia/Kolkata",
        )
    assert quiet is None


@pytest.mark.asyncio
async def test_the_notification_also_fires_when_the_air_clears(db_clean):
    """Coming out of a stretch is worth saying too, not only going into one."""
    from app.bootstrap import run as run_seed
    from app.domains.planning import notifications

    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await session.commit()

    await _seed_environment(account_id, [
        (3, {"aqi": 260, "category": "Poor"}),
        (2, {"aqi": 260, "category": "Poor"}),
        (1, {"aqi": 260, "category": "Poor"}),
        (0, {"aqi": 80, "category": "Satisfactory"}),
    ])
    async with factory() as session:
        crossing = await notifications.queue_for_environment_crossing(
            session, account_id=account_id, plan_date=TODAY, timezone_name="Asia/Kolkata",
        )
    assert crossing is not None
    assert "Satisfactory" in crossing.body


@pytest.mark.asyncio
async def test_a_strong_active_verdict_carries_the_recent_air(db_clean):
    """Buying a retinoid during a Very Poor stretch is worth qualifying."""
    from app.bootstrap import run as run_seed
    from app.domains.purchase.verdict_service import _environment_context

    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await session.commit()
    await _seed_environment(account_id, [
        (offset, {"aqi": 340, "category": "Very Poor"}) for offset in range(3, -1, -1)
    ])

    async with factory() as session:
        strong = await _environment_context(
            session, account_id=account_id, plan_date=TODAY, families=("retinoid", "humectant"),
        )
        gentle = await _environment_context(
            session, account_id=account_id, plan_date=TODAY, families=("humectant", "ceramide"),
        )
    assert strong is not None
    assert strong["currently_deferred"] is True
    assert strong["strong_active_families"] == ["retinoid"]
    assert "NAQI 340" in strong["note"]
    assert "CPCB category Very Poor" in strong["note"]
    # A moisturiser carries no environmental noise at all.
    assert gentle is None


@pytest.mark.asyncio
async def test_event_ready_names_a_sustained_stretch_before_the_event(db_clean):
    from app.bootstrap import run as run_seed
    from app.domains.planning.event_ready import _environment_actions

    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await session.commit()
    await _seed_environment(account_id, [
        (offset, {"aqi": 320, "category": "Very Poor"}) for offset in range(4, -1, -1)
    ])

    async with factory() as session:
        actions = await _environment_actions(
            session, account_id=account_id, plan_date=TODAY, days_until=3,
        )
        far_off = await _environment_actions(
            session, account_id=account_id, plan_date=TODAY, days_until=30,
        )
    assert len(actions) == 1
    assert actions[0]["action_key"] == "care:environment_before_event"
    assert "5 days running" in actions[0]["body"]
    assert "CPCB category Very Poor" in actions[0]["body"]
    # An event a month away is not a reason to talk about today's air.
    assert far_off == []
