"""Phase 7 progress, memory and milestone contracts.

Three promises carry this phase, and all three are tested as behaviour rather
than asserted in prose:

* there is no overall attractiveness score, and one cannot be built,
* a deleted memory fact stops influencing recommendations immediately,
* a photo comparison is refused unless the conditions genuinely allow one.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.domains.planning import clock
from app.domains.progress import comparison, memory, metrics, milestones, registry
from app.domains.progress.models import (
    GamificationEvent, MemoryFact, MetricEvent, Milestone, ProgressGoal,
)
from app.shared.database import sql
from tests.conftest import auth, png_bytes

# The app always resolves "today" in Asia/Kolkata (see clock.local_today).
# Using date.today() here would silently disagree with the app for five and a
# half hours out of every twenty-four, so every test that reasons about a
# calendar date has to use this instead.
TODAY = clock.local_today(None)


async def add_item(client, token, category, name, details=None, **overrides):
    payload = {
        "category": category, "display_name": name, "brand": "Test Brand",
        "details": details or {},
    }
    payload.update(overrides)
    response = await client.post("/api/v2/inventory/items", json=payload, headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def upload_image(client, token, *, width=1080, height=1080):
    response = await client.post(
        "/api/v2/media/upload",
        files={"file": ("photo.png", png_bytes(), "image/png")},
        data={"purpose": "inventory_item"},
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def remember(client, token, **body):
    return await client.post("/api/v2/memory/feedback", json=body, headers=auth(token))


async def account_id_for(client, user, token):
    """This user's V2 account link.

    Resolved from the user's own id rather than by taking the newest row: the
    test database is shared, so "the last account link" is somebody else's.
    """
    from app.domains.identity.models import AccountLink

    await client.get("/api/v2/me", headers=auth(token))
    factory = sql.get_sessionmaker()
    async with factory() as session:
        row = (await session.execute(
            select(AccountLink).where(AccountLink.v1_user_id == str(user["id"]))
        )).scalar_one()
        return row.id


def all_text(payload) -> str:
    if isinstance(payload, str):
        return payload + " "
    if isinstance(payload, dict):
        return "".join(all_text(value) for value in payload.values())
    if isinstance(payload, list):
        return "".join(all_text(value) for value in payload)
    return ""


# --- Authorization -----------------------------------------------------------------


async def test_every_phase_7_route_requires_authentication(app_client):
    calls = [
        ("GET", "/api/v2/progress", None),
        ("GET", "/api/v2/progress/metrics/wardrobe_utilisation", None),
        ("GET", "/api/v2/progress/comparisons", None),
        ("POST", "/api/v2/progress/photos", {"media_id": str(uuid.uuid4()), "body_area": "face",
                                             "lighting": "daylight_window", "angle": "front",
                                             "framing": "face_close"}),
        ("GET", "/api/v2/goals", None),
        ("POST", "/api/v2/goals", {"kind": "no_buy", "title": "x"}),
        ("PATCH", f"/api/v2/goals/{uuid.uuid4()}", {"title": "x"}),
        ("GET", "/api/v2/memory", None),
        ("PATCH", f"/api/v2/memory/{uuid.uuid4()}", {"fact": "x"}),
        ("DELETE", f"/api/v2/memory/{uuid.uuid4()}", None),
        ("GET", "/api/v2/memory/export", None),
        ("GET", "/api/v2/milestones", None),
        ("POST", f"/api/v2/milestones/{uuid.uuid4()}/acknowledge", None),
    ]
    for method, path, body in calls:
        response = await app_client.request(method, path, json=body)
        assert response.status_code in (401, 403), f"{method} {path} -> {response.status_code}"


async def test_ownership_comes_from_the_token_not_the_body(app_client, make_user):
    _, token = await make_user()
    response = await app_client.post(
        "/api/v2/goals",
        json={"account_id": str(uuid.uuid4()), "kind": "no_buy", "title": "No buying"},
        headers=auth(token),
    )
    assert response.status_code == 422


async def test_one_users_memory_is_invisible_to_another(app_client, make_user):
    _, owner = await make_user()
    _, stranger = await make_user()
    await remember(app_client, owner, subject_type="colour", signal="rejected", subject_label="mustard")

    mine = (await app_client.get("/api/v2/memory", headers=auth(owner))).json()
    fact_id = mine["facts"][0]["id"]

    for method in ("PATCH", "DELETE"):
        response = await app_client.request(
            method, f"/api/v2/memory/{fact_id}",
            json={"fact": "changed"} if method == "PATCH" else None,
            headers=auth(stranger),
        )
        assert response.status_code == 404

    theirs = (await app_client.get("/api/v2/memory", headers=auth(stranger))).json()
    assert theirs["facts"] == []


async def test_one_users_goal_is_invisible_to_another(app_client, make_user):
    _, owner = await make_user()
    _, stranger = await make_user()
    goal = (await app_client.post(
        "/api/v2/goals", json={"kind": "no_buy", "title": "No buying"}, headers=auth(owner),
    )).json()
    response = await app_client.patch(
        f"/api/v2/goals/{goal['id']}", json={"title": "hijacked"}, headers=auth(stranger),
    )
    assert response.status_code == 404


# --- No overall score ------------------------------------------------------------------


def test_no_metric_reads_another_metric():
    """The structural guarantee. A composite score is not expressible."""
    for metric in registry.METRICS:
        for source in metric.inputs:
            assert source not in registry.METRIC_BY_KEY, (
                f"{metric.key} reads {source}, which would allow an overall score"
            )
            assert source in registry.INPUT_DOMAINS


def test_the_registry_rejects_a_metric_built_from_other_metrics():
    """Proves the guard fires rather than merely existing."""
    original = registry.METRICS
    bad = registry.MetricDefinition(
        key="overall", label="Overall", unit=registry.UNIT_RATIO,
        direction=registry.DIRECTION_HIGHER,
        formula="The average of every other metric.", formula_version="v1",
        inputs=("wardrobe_readiness", "outfit_variety"),
        missing_data_behaviour=registry.MISSING_UNAVAILABLE,
        explanation="A single number.", update_frequency=registry.FREQUENCY_ON_READ,
        not_a_measure_of="",
    )
    registry.METRICS = original + (bad,)
    registry.METRIC_BY_KEY[bad.key] = bad
    try:
        with pytest.raises(AssertionError, match="reads metric"):
            registry.validate_registry()
    finally:
        registry.METRICS = original
        registry.METRIC_BY_KEY.pop(bad.key, None)


def test_no_metric_describes_itself_as_a_judgement_of_a_person():
    registry.validate_registry()
    for metric in registry.METRICS:
        text = f"{metric.label} {metric.formula} {metric.explanation}".lower()
        for word in registry.FORBIDDEN_METRIC_WORDS:
            assert word not in text, f"{metric.key} says {word!r}"


async def test_the_progress_response_says_plainly_there_is_no_overall_score(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get("/api/v2/progress", headers=auth(token))).json()
    assert body["no_overall_score"] is True
    assert "no single number here summing you up" in body["no_overall_score_note"]
    keys = {row["key"] for row in body["metrics"]}
    for banned in ("overall", "total", "attractiveness", "beauty", "score"):
        assert banned not in keys


# --- Every metric has a documented formula -------------------------------------------------


def test_every_metric_documents_all_six_required_things():
    for metric in registry.METRICS:
        assert metric.formula.strip(), metric.key
        assert metric.formula_version.strip(), metric.key
        assert metric.inputs, metric.key
        assert metric.missing_data_behaviour.strip(), metric.key
        assert metric.explanation.strip(), metric.key
        assert metric.update_frequency.strip(), metric.key
        assert metric.not_a_measure_of.strip(), metric.key


def test_every_registered_metric_has_a_formula_that_runs():
    assert set(metrics.COMPUTERS) == set(registry.METRIC_KEYS)


async def test_the_metrics_route_publishes_every_formula(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get("/api/v2/progress/metrics", headers=auth(token))).json()
    assert len(body["metrics"]) == len(registry.METRICS)
    for row in body["metrics"]:
        assert row["formula"]
        assert row["formula_version"]


async def test_a_metric_detail_shows_how_it_was_worked_out(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get(
        "/api/v2/progress/metrics/wardrobe_utilisation", headers=auth(token),
    )).json()
    assert body["how_it_is_worked_out"]
    assert body["what_it_is_not"]
    assert body["definition"]["formula_version"] == "v1"


async def test_an_unknown_metric_is_an_honest_404(app_client, make_user):
    _, token = await make_user()
    response = await app_client.get("/api/v2/progress/metrics/overall_score", headers=auth(token))
    assert response.status_code == 404


# --- Missing data --------------------------------------------------------------------------


async def test_a_metric_with_no_data_reports_unavailable_rather_than_zero(app_client, make_user):
    """Zero would read as a real, bad score. It is not the same as 'we do not know'."""
    _, token = await make_user()
    body = (await app_client.get("/api/v2/progress", headers=auth(token))).json()
    unavailable = [row for row in body["metrics"] if row["status"] == "unavailable"]
    assert unavailable, "a brand-new account should have unavailable metrics"
    for row in unavailable:
        assert row["value"] is None
        assert row["missing_inputs"]
        assert row["note"], f"{row['key']} does not say why it is unavailable"


async def test_an_unavailable_metric_says_what_it_is_waiting_for(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get(
        "/api/v2/progress/metrics/user_reported_confidence", headers=auth(token),
    )).json()
    assert body["current"]["status"] == "unavailable"
    assert "never guess" in body["current"]["note"]


async def test_a_partial_metric_names_what_is_missing(app_client, make_user):
    _, token = await make_user()
    await add_item(app_client, token, "beauty", "Serum", {
        "product_type": "serum", "expiry_date": (TODAY + timedelta(days=200)).isoformat(),
    })
    await add_item(app_client, token, "beauty", "Undated cream", {"product_type": "moisturiser"})
    body = (await app_client.get(
        "/api/v2/progress/metrics/product_expiry_risk", headers=auth(token),
    )).json()
    assert body["current"]["status"] == "partial"
    assert body["current"]["inputs"]["products_without_a_date"] == 1


# --- Reproducibility and duplicate events ------------------------------------------------------


async def test_a_metric_event_stores_the_inputs_it_used(app_client, make_user):
    """Reproducible means checkable by hand, which needs the actual counts."""
    _, token = await make_user()
    await add_item(app_client, token, "wardrobe", "Navy shirt", {"colour": "navy"})
    await app_client.get("/api/v2/progress", headers=auth(token))

    factory = sql.get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(MetricEvent).where(MetricEvent.metric_key == "inventory_balance")
        )).scalars().all()
        assert rows
        row = rows[-1]
        assert row.formula_version == "v1"
        assert "category_counts" in row.inputs
        assert row.inputs["total_items"] == 1


async def test_computing_the_same_metric_twice_records_one_event(app_client, make_user):
    """A job that runs twice must not inflate a history."""
    _, token = await make_user()
    await add_item(app_client, token, "wardrobe", "Navy shirt", {"colour": "navy"})

    for _ in range(3):
        await app_client.get("/api/v2/progress", headers=auth(token))

    factory = sql.get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(MetricEvent).where(MetricEvent.metric_key == "inventory_balance")
        )).scalars().all()
        digests = [row.dedup_hash for row in rows]
        assert len(digests) == len(set(digests))


def test_the_dedup_hash_changes_when_the_inputs_change():
    account = uuid.uuid4()
    first = metrics.MetricResult(key="inventory_balance", value=1.0, status="ok", inputs={"total_items": 1})
    second = metrics.MetricResult(key="inventory_balance", value=2.0, status="ok", inputs={"total_items": 2})
    assert first.dedup_hash(account, "week", TODAY) != second.dedup_hash(account, "week", TODAY)
    # And is stable for identical data.
    again = metrics.MetricResult(key="inventory_balance", value=1.0, status="ok", inputs={"total_items": 1})
    assert first.dedup_hash(account, "week", TODAY) == again.dedup_hash(account, "week", TODAY)


def test_the_dedup_hash_separates_accounts_and_periods():
    result = metrics.MetricResult(key="inventory_balance", value=1.0, status="ok", inputs={})
    a, b = uuid.uuid4(), uuid.uuid4()
    assert result.dedup_hash(a, "week", TODAY) != result.dedup_hash(b, "week", TODAY)
    assert result.dedup_hash(a, "week", TODAY) != result.dedup_hash(a, "month", TODAY)


# --- Individual formulas -----------------------------------------------------------------------


async def test_wardrobe_utilisation_excludes_items_too_new_to_have_been_worn(app_client, make_user):
    _, token = await make_user()
    for index in range(6):
        await add_item(app_client, token, "wardrobe", f"Shirt {index}", {"colour": "navy"})
    body = (await app_client.get(
        "/api/v2/progress/metrics/wardrobe_utilisation", headers=auth(token),
    )).json()
    # Everything was just added, so nothing is eligible and the metric says so
    # rather than reporting 0% used.
    assert body["current"]["status"] == "unavailable"
    assert "90 days" in body["current"]["note"]


async def test_inventory_balance_counts_and_refuses_to_score(app_client, make_user):
    _, token = await make_user()
    await add_item(app_client, token, "wardrobe", "Navy shirt", {"colour": "navy"})
    await add_item(app_client, token, "shoes", "Derby shoes", {"shoe_type": "derby"})
    body = (await app_client.get(
        "/api/v2/progress/metrics/inventory_balance", headers=auth(token),
    )).json()
    assert body["current"]["inputs"]["category_counts"]["wardrobe"] == 1
    assert body["definition"]["direction"] == "neutral"
    assert "no target shape" in body["current"]["note"].lower()


async def test_purchase_efficiency_leaves_recent_purchases_alone(app_client, make_user):
    _, token = await make_user()
    # Bought yesterday — inside the grace window, so not judged yet.
    await add_item(app_client, token, "wardrobe", "New shirt", {"colour": "navy"},
                   purchase_date=(TODAY - timedelta(days=1)).isoformat())
    body = (await app_client.get(
        "/api/v2/progress/metrics/purchase_efficiency", headers=auth(token),
    )).json()
    assert body["current"]["status"] == "unavailable"


async def test_no_buy_progress_counts_days_and_records_a_reset_plainly(app_client, make_user):
    _, token = await make_user()
    started = TODAY - timedelta(days=40)
    await app_client.post(
        "/api/v2/goals",
        json={"kind": "no_buy", "title": "No buying for a while", "starts_on": started.isoformat()},
        headers=auth(token),
    )

    clean = (await app_client.get(
        "/api/v2/progress/metrics/no_buy_progress", headers=auth(token),
    )).json()
    assert clean["current"]["value"] == 40

    await add_item(app_client, token, "wardrobe", "A purchase", {"colour": "navy"},
                   purchase_date=(TODAY - timedelta(days=10)).isoformat())
    after = (await app_client.get(
        "/api/v2/progress/metrics/no_buy_progress", headers=auth(token),
    )).json()
    assert after["current"]["value"] == 10
    text = all_text(after).lower()
    assert "failed" not in text
    assert "broke" not in text


async def test_no_buy_progress_is_unavailable_without_a_goal(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get(
        "/api/v2/progress/metrics/no_buy_progress", headers=auth(token),
    )).json()
    assert body["current"]["status"] == "unavailable"


async def test_user_reported_confidence_only_ever_comes_from_the_user(app_client, make_user):
    _, token = await make_user()
    for rating in (4, 5):
        response = await app_client.post(
            "/api/v2/progress/self-report", json={"rating": rating}, headers=auth(token),
        )
        assert response.status_code == 200, response.text

    body = (await app_client.get(
        "/api/v2/progress/metrics/user_reported_confidence", headers=auth(token),
    )).json()
    assert body["current"]["value"] == 4.5
    assert "Nothing else feeds this" in body["current"]["note"]


async def test_a_self_report_outside_one_to_five_is_refused(app_client, make_user):
    _, token = await make_user()
    for rating in (0, 6, -1):
        response = await app_client.post(
            "/api/v2/progress/self-report", json={"rating": rating}, headers=auth(token),
        )
        assert response.status_code == 422


def test_the_season_helper_maps_indian_months():
    assert metrics.season_for(date(2026, 1, 15)) == "winter"
    assert metrics.season_for(date(2026, 5, 15)) == "summer"
    assert metrics.season_for(date(2026, 8, 15)) == "monsoon"
    assert metrics.season_for(date(2026, 10, 15)) == "autumn"


# --- Time series -------------------------------------------------------------------------------


async def test_metric_history_is_returned_oldest_first(app_client, make_user):
    _, token = await make_user()
    await add_item(app_client, token, "wardrobe", "Navy shirt", {"colour": "navy"})
    await app_client.get("/api/v2/progress", headers=auth(token))
    await add_item(app_client, token, "shoes", "Derby shoes", {"shoe_type": "derby"})
    await app_client.get("/api/v2/progress", headers=auth(token))

    body = (await app_client.get(
        "/api/v2/progress/metrics/inventory_balance", headers=auth(token),
    )).json()
    dates = [row["period_start"] for row in body["history"]]
    assert dates == sorted(dates)


async def test_a_monthly_view_uses_month_boundaries(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get(
        "/api/v2/progress", params={"period": "month"}, headers=auth(token),
    )).json()
    assert body["period"] == "month"
    assert body["period_start"].endswith("-01")


# --- Photo comparability -------------------------------------------------------------------------


def _conditions(**overrides):
    base = dict(
        taken_on=TODAY, body_area="face", lighting="daylight_window",
        angle="front", framing="face_close", time_of_day="morning",
        width=1080, height=1080,
    )
    base.update(overrides)
    return comparison.PhotoConditions(**base)


def test_two_photos_in_the_same_conditions_are_comparable():
    baseline = _conditions(taken_on=TODAY - timedelta(days=30))
    assert comparison.compare(baseline, _conditions()).comparable is True


def test_different_lighting_blocks_a_comparison_and_says_so():
    baseline = _conditions(taken_on=TODAY - timedelta(days=30), lighting="indoor_warm")
    result = comparison.compare(baseline, _conditions())
    assert result.comparable is False
    assert any("light" in reason.lower() for reason in result.blocking_reasons)
    assert result.guidance


def test_a_different_angle_blocks_a_comparison():
    baseline = _conditions(taken_on=TODAY - timedelta(days=30), angle="left_profile")
    result = comparison.compare(baseline, _conditions())
    assert result.comparable is False
    assert any("angle" in reason.lower() for reason in result.blocking_reasons)


def test_different_framing_blocks_a_comparison():
    baseline = _conditions(taken_on=TODAY - timedelta(days=30), framing="full_body")
    assert comparison.compare(baseline, _conditions()).comparable is False


def test_a_low_resolution_photo_blocks_a_comparison():
    baseline = _conditions(taken_on=TODAY - timedelta(days=30), width=200, height=200)
    result = comparison.compare(baseline, _conditions())
    assert result.comparable is False
    assert any("camera" in reason.lower() for reason in result.blocking_reasons)


def test_a_big_resolution_gap_blocks_a_comparison():
    """One sharper photo reads as better skin to any human eye."""
    baseline = _conditions(taken_on=TODAY - timedelta(days=30), width=600, height=600)
    result = comparison.compare(baseline, _conditions(width=2400, height=2400))
    assert result.comparable is False


def test_photos_too_close_together_are_refused():
    baseline = _conditions(taken_on=TODAY - timedelta(days=2))
    result = comparison.compare(baseline, _conditions())
    assert result.comparable is False
    assert any("day" in reason.lower() for reason in result.blocking_reasons)


def test_photos_too_far_apart_are_refused():
    baseline = _conditions(taken_on=TODAY - timedelta(days=800))
    assert comparison.compare(baseline, _conditions()).comparable is False


def test_photos_of_different_areas_are_never_compared():
    baseline = _conditions(taken_on=TODAY - timedelta(days=30), body_area="hair")
    assert comparison.compare(baseline, _conditions(body_area="face")).comparable is False


def test_a_different_time_of_day_is_mentioned_but_does_not_block():
    baseline = _conditions(taken_on=TODAY - timedelta(days=30), time_of_day="evening")
    result = comparison.compare(baseline, _conditions(time_of_day="morning"))
    assert result.comparable is True
    time_check = next(row for row in result.checks if row.key == "time_of_day")
    assert time_check.passed is False
    assert time_check.blocking is False


def test_every_check_runs_so_the_user_sees_every_problem():
    baseline = _conditions(taken_on=TODAY - timedelta(days=1), lighting="low_light", angle="back")
    result = comparison.compare(baseline, _conditions())
    assert len(result.blocking_reasons) >= 3


async def test_the_comparison_route_refuses_and_explains(app_client, make_user, media_root):
    _, token = await make_user()
    first = await upload_image(app_client, token)
    second = await upload_image(app_client, token)

    await app_client.post("/api/v2/progress/photos", json={
        "media_id": first["id"], "body_area": "face", "lighting": "daylight_window",
        "angle": "front", "framing": "face_close",
        "taken_on": (TODAY - timedelta(days=30)).isoformat(),
    }, headers=auth(token))
    # Same day, different light: two reasons to refuse.
    await app_client.post("/api/v2/progress/photos", json={
        "media_id": second["id"], "body_area": "face", "lighting": "low_light",
        "angle": "front", "framing": "face_close", "taken_on": TODAY.isoformat(),
    }, headers=auth(token))

    body = (await app_client.get(
        "/api/v2/progress/comparisons", params={"body_area": "face"}, headers=auth(token),
    )).json()
    assert body["comparable"] is False
    assert body["rejected"]
    assert body["guidance"]
    assert "lighting rather than you" in body["message"]


async def test_a_single_photo_cannot_produce_a_comparison(app_client, make_user, media_root):
    _, token = await make_user()
    asset = await upload_image(app_client, token)
    await app_client.post("/api/v2/progress/photos", json={
        "media_id": asset["id"], "body_area": "face", "lighting": "daylight_window",
        "angle": "front", "framing": "face_close",
    }, headers=auth(token))

    body = (await app_client.get("/api/v2/progress/comparisons", headers=auth(token))).json()
    assert body["comparable"] is False
    assert body["photos_held"] == 1


async def test_a_progress_photo_requires_its_conditions(app_client, make_user, media_root):
    """A photo we would have to guess about cannot be honestly compared."""
    _, token = await make_user()
    asset = await upload_image(app_client, token)
    response = await app_client.post(
        "/api/v2/progress/photos",
        json={"media_id": asset["id"], "body_area": "face"},
        headers=auth(token),
    )
    assert response.status_code == 422


async def test_a_photo_belonging_to_someone_else_is_refused(app_client, make_user, media_root):
    _, owner = await make_user()
    _, stranger = await make_user()
    asset = await upload_image(app_client, owner)
    response = await app_client.post("/api/v2/progress/photos", json={
        "media_id": asset["id"], "body_area": "face", "lighting": "daylight_window",
        "angle": "front", "framing": "face_close",
    }, headers=auth(stranger))
    assert response.status_code == 404


# --- Memory -----------------------------------------------------------------------------------------


async def test_a_remembered_fact_carries_everything_the_brief_requires(app_client, make_user):
    _, token = await make_user()
    await remember(app_client, token, subject_type="colour", signal="rejected", subject_label="mustard")

    body = (await app_client.get("/api/v2/memory", headers=auth(token))).json()
    fact = body["facts"][0]
    for field in (
        "fact", "source", "confidence", "created_at", "last_reinforced_at",
        "verification_state", "linked_evidence", "deletion_state",
    ):
        assert field in fact, f"a memory fact must carry {field}"
    assert fact["linked_evidence"], "a fact must say what it was learned from"
    assert fact["why_we_remember"]


async def test_memory_says_where_each_fact_came_from(app_client, make_user):
    _, token = await make_user()
    await remember(app_client, token, subject_type="look", signal="liked", subject_label="the navy shirt")
    body = (await app_client.get("/api/v2/memory", headers=auth(token))).json()
    fact = body["facts"][0]
    assert fact["source"] == memory.SOURCE_FEEDBACK
    assert fact["source_label"] == "From feedback you gave"
    assert fact["linked_evidence"][0]["evidence"]["signal"] == "liked"


async def test_seeing_the_same_thing_twice_reinforces_rather_than_duplicates(app_client, make_user):
    _, token = await make_user()
    for _ in range(3):
        await remember(app_client, token, subject_type="colour", signal="rejected", subject_label="mustard")

    body = (await app_client.get("/api/v2/memory", headers=auth(token))).json()
    mustard = [row for row in body["facts"] if "mustard" in row["fact"]]
    assert len(mustard) == 1
    assert mustard[0]["reinforcement_count"] == 3
    assert len(mustard[0]["linked_evidence"]) == 3


async def test_a_user_correction_outranks_what_we_inferred(app_client, make_user):
    _, token = await make_user()
    await remember(app_client, token, subject_type="colour", signal="rejected", subject_label="mustard")
    listing = (await app_client.get("/api/v2/memory", headers=auth(token))).json()
    fact_id = listing["facts"][0]["id"]

    body = (await app_client.patch(
        f"/api/v2/memory/{fact_id}",
        json={"fact": "I like mustard, just not for the office."},
        headers=auth(token),
    )).json()
    assert body["fact"] == "I like mustard, just not for the office."
    assert body["verification_state"] == "corrected"
    assert body["confidence"] == 1.0
    assert body["source"] == memory.SOURCE_USER_STATED


async def test_confirming_a_fact_takes_it_to_full_confidence(app_client, make_user):
    _, token = await make_user()
    await remember(app_client, token, subject_type="look", signal="liked", subject_label="navy")
    listing = (await app_client.get("/api/v2/memory", headers=auth(token))).json()
    fact_id = listing["facts"][0]["id"]

    body = (await app_client.patch(
        f"/api/v2/memory/{fact_id}", json={"verification_state": "confirmed"}, headers=auth(token),
    )).json()
    assert body["confidence"] == 1.0
    assert body["influences_recommendations"] is True


async def test_a_deleted_fact_stops_influencing_recommendations(app_client, make_user):
    """The acceptance criterion, tested through the accessor everything uses."""
    _, token = await make_user()
    await remember(app_client, token, subject_type="colour", signal="rejected", subject_label="mustard")
    listing = (await app_client.get("/api/v2/memory", headers=auth(token))).json()
    fact_id = listing["facts"][0]["id"]

    response = await app_client.delete(f"/api/v2/memory/{fact_id}", headers=auth(token))
    assert response.status_code == 200

    factory = sql.get_sessionmaker()
    async with factory() as session:
        row = await session.get(MemoryFact, uuid.UUID(fact_id))
        account_id = row.account_id
        # The one door every consumer must use.
        influencing = await memory.active_facts(session, account_id)
        assert all(str(fact.id) != fact_id for fact in influencing)
        # And belt and braces: the text is gone, so even a consumer that
        # bypassed the accessor would find nothing to act on.
        assert row.fact == ""
        assert row.deletion_state == memory.DELETION_DELETED


async def test_a_rejected_fact_stops_influencing_without_being_deleted(app_client, make_user):
    _, token = await make_user()
    await remember(app_client, token, subject_type="colour", signal="rejected", subject_label="mustard")
    listing = (await app_client.get("/api/v2/memory", headers=auth(token))).json()
    fact_id = listing["facts"][0]["id"]

    await app_client.patch(
        f"/api/v2/memory/{fact_id}", json={"verification_state": "rejected"}, headers=auth(token),
    )
    factory = sql.get_sessionmaker()
    async with factory() as session:
        row = await session.get(MemoryFact, uuid.UUID(fact_id))
        influencing = await memory.active_facts(session, row.account_id)
        assert all(str(fact.id) != fact_id for fact in influencing)
        assert row.fact != "", "rejecting is not deleting"


async def test_a_rejected_fact_is_not_quietly_relearned(app_client, make_user):
    _, token = await make_user()
    await remember(app_client, token, subject_type="colour", signal="rejected", subject_label="mustard")
    listing = (await app_client.get("/api/v2/memory", headers=auth(token))).json()
    fact_id = listing["facts"][0]["id"]
    await app_client.patch(
        f"/api/v2/memory/{fact_id}", json={"verification_state": "rejected"}, headers=auth(token),
    )

    await remember(app_client, token, subject_type="colour", signal="rejected", subject_label="mustard")
    factory = sql.get_sessionmaker()
    async with factory() as session:
        row = await session.get(MemoryFact, uuid.UUID(fact_id))
        assert row.verification_state == "rejected", "their answer stands until they change it"


async def test_editing_a_deleted_fact_is_refused(app_client, make_user):
    _, token = await make_user()
    await remember(app_client, token, subject_type="colour", signal="rejected", subject_label="mustard")
    listing = (await app_client.get("/api/v2/memory", headers=auth(token))).json()
    fact_id = listing["facts"][0]["id"]
    await app_client.delete(f"/api/v2/memory/{fact_id}", headers=auth(token))

    response = await app_client.patch(
        f"/api/v2/memory/{fact_id}", json={"fact": "bring it back"}, headers=auth(token),
    )
    assert response.status_code == 422


async def test_a_low_confidence_hunch_does_not_influence_anything(app_client, make_user):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    factory = sql.get_sessionmaker()
    async with factory() as session:
        await memory.record(
            session, account_id, category=memory.CATEGORY_PREFERENCE,
            fact="Might prefer loose fits.", source=memory.SOURCE_INFERRED, confidence=0.2,
        )
        await session.commit()
        influencing = await memory.active_facts(session, account_id)
        assert all("loose fits" not in row.fact for row in influencing)


# --- Disabled categories ---------------------------------------------------------------------------


async def test_switching_a_category_off_stops_it_being_used(app_client, make_user):
    user, token = await make_user()
    await remember(app_client, token, subject_type="colour", signal="rejected", subject_label="mustard")

    response = await app_client.patch(
        f"/api/v2/memory/categories/{memory.CATEGORY_REJECTION}",
        json={"category": memory.CATEGORY_REJECTION, "enabled": False},
        headers=auth(token),
    )
    assert response.status_code == 200

    account_id = await account_id_for(app_client, user, token)
    factory = sql.get_sessionmaker()
    async with factory() as session:
        influencing = await memory.active_facts(session, account_id)
        assert all(fact.category != memory.CATEGORY_REJECTION for fact in influencing)


async def test_switching_a_category_off_destroys_nothing(app_client, make_user):
    _, token = await make_user()
    await remember(app_client, token, subject_type="colour", signal="rejected", subject_label="mustard")
    await app_client.patch(
        f"/api/v2/memory/categories/{memory.CATEGORY_REJECTION}",
        json={"category": memory.CATEGORY_REJECTION, "enabled": False},
        headers=auth(token),
    )
    body = (await app_client.patch(
        f"/api/v2/memory/categories/{memory.CATEGORY_REJECTION}",
        json={"category": memory.CATEGORY_REJECTION, "enabled": True},
        headers=auth(token),
    )).json()
    assert body["enabled"] is True

    listing = (await app_client.get("/api/v2/memory", headers=auth(token))).json()
    assert any("mustard" in row["fact"] for row in listing["facts"])


async def test_a_disabled_category_is_not_written_to_at_all(app_client, make_user):
    _, token = await make_user()
    await app_client.patch(
        f"/api/v2/memory/categories/{memory.CATEGORY_REJECTION}",
        json={"category": memory.CATEGORY_REJECTION, "enabled": False},
        headers=auth(token),
    )
    body = (await remember(
        app_client, token, subject_type="colour", signal="rejected", subject_label="mustard",
    )).json()
    assert body["learned"] is None
    assert "switched off" in body["message"]

    listing = (await app_client.get("/api/v2/memory", headers=auth(token))).json()
    assert listing["facts"] == []


async def test_a_mismatched_category_in_the_address_is_refused(app_client, make_user):
    _, token = await make_user()
    response = await app_client.patch(
        f"/api/v2/memory/categories/{memory.CATEGORY_REJECTION}",
        json={"category": memory.CATEGORY_FAVOURITE, "enabled": False},
        headers=auth(token),
    )
    assert response.status_code == 422


# --- Export ---------------------------------------------------------------------------------------------


async def test_the_export_includes_what_was_deleted_and_why(app_client, make_user):
    """An export that quietly omits deletions is not an honest export."""
    _, token = await make_user()
    await remember(app_client, token, subject_type="colour", signal="rejected", subject_label="mustard")
    listing = (await app_client.get("/api/v2/memory", headers=auth(token))).json()
    await app_client.delete(f"/api/v2/memory/{listing['facts'][0]['id']}", headers=auth(token))

    body = (await app_client.get("/api/v2/memory/export", headers=auth(token))).json()
    assert body["deleted_count"] == 1
    deleted = [row for row in body["facts"] if row["deletion_state"] == "deleted"]
    assert deleted
    assert deleted[0]["fact"] == "", "deleted text must actually be gone"
    assert deleted[0]["revisions"], "but the record of what happened stays"
    assert "mustard" in deleted[0]["revisions"][0]["previous_fact"]


async def test_the_export_is_scoped_to_the_caller(app_client, make_user):
    _, owner = await make_user()
    _, stranger = await make_user()
    await remember(app_client, owner, subject_type="colour", signal="rejected", subject_label="mustard")

    body = (await app_client.get("/api/v2/memory/export", headers=auth(stranger))).json()
    assert body["facts"] == []
    assert body["total"] == 0


async def test_the_export_records_which_categories_are_switched_off(app_client, make_user):
    _, token = await make_user()
    await app_client.patch(
        f"/api/v2/memory/categories/{memory.CATEGORY_PURCHASE}",
        json={"category": memory.CATEGORY_PURCHASE, "enabled": False},
        headers=auth(token),
    )
    body = (await app_client.get("/api/v2/memory/export", headers=auth(token))).json()
    assert memory.CATEGORY_PURCHASE in body["disabled_categories"]


# --- Goals ------------------------------------------------------------------------------------------------


async def test_a_goal_records_where_you_started(app_client, make_user):
    _, token = await make_user()
    await add_item(app_client, token, "wardrobe", "Navy shirt", {"colour": "navy"})
    body = (await app_client.post("/api/v2/goals", json={
        "kind": "wardrobe", "title": "Use more of what I own",
        "metric_key": "inventory_balance", "target_value": 4.0,
    }, headers=auth(token))).json()
    assert body["starting_value"] is not None
    assert body["updates"][0]["source"] == "created"


async def test_goal_progress_is_measured_from_the_starting_point(app_client, make_user):
    _, token = await make_user()
    await add_item(app_client, token, "wardrobe", "Navy shirt", {"colour": "navy"})
    goal = (await app_client.post("/api/v2/goals", json={
        "kind": "wardrobe", "title": "Two categories in use",
        "metric_key": "inventory_balance", "target_value": 2.0,
    }, headers=auth(token))).json()
    assert goal["starting_value"] == 1.0
    assert goal["progress"] == 0.0

    await add_item(app_client, token, "shoes", "Derby shoes", {"shoe_type": "derby"})
    after = (await app_client.get("/api/v2/goals", headers=auth(token))).json()["goals"][0]
    assert after["current_value"] == 2.0
    assert after["progress"] == 1.0


async def test_a_goal_on_an_unavailable_metric_says_so_rather_than_showing_zero(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.post("/api/v2/goals", json={
        "kind": "routine", "title": "Keep to my routine",
        "metric_key": "routine_consistency", "target_value": 0.8,
    }, headers=auth(token))).json()
    assert body["metric_status"] == "unavailable"
    assert body["progress"] is None
    assert "does not have enough data" in body["progress_note"]


async def test_a_goal_naming_an_unknown_metric_is_refused(app_client, make_user):
    _, token = await make_user()
    response = await app_client.post("/api/v2/goals", json={
        "kind": "custom", "title": "Be more attractive", "metric_key": "attractiveness",
    }, headers=auth(token))
    assert response.status_code == 422


async def test_a_target_date_before_the_start_is_refused(app_client, make_user):
    _, token = await make_user()
    response = await app_client.post("/api/v2/goals", json={
        "kind": "no_buy", "title": "No buying",
        "starts_on": TODAY.isoformat(),
        "target_date": (TODAY - timedelta(days=5)).isoformat(),
    }, headers=auth(token))
    assert response.status_code == 422


async def test_a_goal_with_no_metric_is_tracked_by_your_own_updates(app_client, make_user):
    _, token = await make_user()
    goal = (await app_client.post("/api/v2/goals", json={
        "kind": "custom", "title": "Wear the green kurta more",
    }, headers=auth(token))).json()

    body = (await app_client.patch(
        f"/api/v2/goals/{goal['id']}",
        json={"progress_note": "Wore it to the office on Tuesday."},
        headers=auth(token),
    )).json()
    assert any(row["note"] == "Wore it to the office on Tuesday." for row in body["updates"])


async def test_reaching_a_goal_earns_a_milestone(app_client, make_user):
    _, token = await make_user()
    goal = (await app_client.post("/api/v2/goals", json={
        "kind": "custom", "title": "Wear the green kurta more",
    }, headers=auth(token))).json()
    await app_client.patch(
        f"/api/v2/goals/{goal['id']}", json={"status": "achieved"}, headers=auth(token),
    )
    body = (await app_client.get("/api/v2/milestones", headers=auth(token))).json()
    assert any(row["rule_id"] == "milestone.reached_a_goal" for row in body["earned"])


# --- Gamification ---------------------------------------------------------------------------------------------


def test_no_milestone_rewards_opening_the_app():
    """The brief's blunt instruction, tested rather than trusted."""
    for rule in milestones.RULES:
        assert rule.behaviour in milestones.REWARDABLE_BEHAVIOURS
        assert rule.behaviour not in milestones.NEVER_REWARDABLE


def test_the_rewardable_list_contains_no_engagement_metric():
    for behaviour in milestones.REWARDABLE_BEHAVIOURS:
        for banned in ("open", "login", "session", "visit", "notification", "invite"):
            assert banned not in behaviour, f"{behaviour} looks like engagement"


def test_a_milestone_rewarding_engagement_is_rejected():
    original = milestones.RULES
    bad = milestones.MilestoneRule(
        "milestone.opened_app", "Opened the app ten times",
        "You came back ten days running.", "app_opened", 10,
    )
    milestones.RULES = original + (bad,)
    try:
        with pytest.raises(AssertionError, match="engagement"):
            milestones.validate_rules()
    finally:
        milestones.RULES = original


def test_milestone_wording_is_not_childish_and_does_not_shame():
    milestones.validate_rules()
    for rule in milestones.RULES:
        text = f"{rule.label} {rule.description}".lower()
        for word in milestones.FORBIDDEN_WORDS:
            assert word not in text, f"{rule.rule_id} says {word!r}"


def test_a_milestone_with_childish_wording_is_rejected():
    original = milestones.RULES
    bad = milestones.MilestoneRule(
        "milestone.badge", "Congratulations, a badge!",
        "You unlocked a trophy.", milestones.BEHAVIOUR_COMPLETED_ROUTINE, 1,
    )
    milestones.RULES = original + (bad,)
    try:
        with pytest.raises(AssertionError, match="wording"):
            milestones.validate_rules()
    finally:
        milestones.RULES = original


async def test_the_same_action_cannot_be_counted_twice(app_client, make_user):
    """Anti-abuse, enforced by a unique constraint rather than a check."""
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    from app.domains.progress import service

    factory = sql.get_sessionmaker()
    async with factory() as session:
        subject = uuid.uuid4()
        for _ in range(5):
            await service.record_behaviour(
                session, account_id, milestones.BEHAVIOUR_USED_LOW_USE_PRODUCT,
                occurred_on=TODAY, subject_id=subject,
            )
        await session.commit()

        rows = (await session.execute(
            select(GamificationEvent).where(GamificationEvent.account_id == account_id)
        )).scalars().all()
        assert len(rows) == 1


async def test_different_subjects_on_the_same_day_count_separately(app_client, make_user):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    from app.domains.progress import service

    factory = sql.get_sessionmaker()
    async with factory() as session:
        for _ in range(3):
            await service.record_behaviour(
                session, account_id, milestones.BEHAVIOUR_USED_LOW_USE_PRODUCT,
                occurred_on=TODAY, subject_id=uuid.uuid4(),
            )
        await session.commit()
        rows = (await session.execute(
            select(GamificationEvent).where(GamificationEvent.account_id == account_id)
        )).scalars().all()
        assert len(rows) == 3


async def test_recording_an_engagement_behaviour_is_refused(app_client, make_user):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    from app.domains.progress import service
    from app.shared.errors.exceptions import ValidationFailedError

    factory = sql.get_sessionmaker()
    async with factory() as session:
        with pytest.raises(ValidationFailedError):
            await service.record_behaviour(
                session, account_id, "app_opened", occurred_on=TODAY,
            )


def test_a_repeatable_milestone_fires_on_multiples_not_on_every_event():
    rules = milestones.earned(milestones.BEHAVIOUR_USED_LOW_USE_PRODUCT, 5)
    assert [row.rule_id for row in rules] == ["milestone.used_five_low_use"]
    assert milestones.earned(milestones.BEHAVIOUR_USED_LOW_USE_PRODUCT, 6) == []
    assert milestones.earned(milestones.BEHAVIOUR_USED_LOW_USE_PRODUCT, 10)


def test_a_one_off_milestone_is_not_earned_twice():
    already = ["milestone.no_buy_thirty"]
    assert milestones.earned(milestones.BEHAVIOUR_NO_BUY_MAINTAINED, 30, already) == []


async def test_the_milestones_route_names_what_it_will_never_reward(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.get("/api/v2/milestones", headers=auth(token))).json()
    assert "app_opened" in body["never_rewarded"]
    assert "Opening the app is not one of them" in body["note"]


async def test_acknowledging_a_milestone_that_is_not_yours_is_refused(app_client, make_user):
    _, owner = await make_user()
    _, stranger = await make_user()
    goal = (await app_client.post("/api/v2/goals", json={
        "kind": "custom", "title": "A goal",
    }, headers=auth(owner))).json()
    await app_client.patch(
        f"/api/v2/goals/{goal['id']}", json={"status": "achieved"}, headers=auth(owner),
    )
    earned = (await app_client.get("/api/v2/milestones", headers=auth(owner))).json()["earned"]
    response = await app_client.post(
        f"/api/v2/milestones/{earned[0]['id']}/acknowledge", headers=auth(stranger),
    )
    assert response.status_code == 404


# --- Streaks ---------------------------------------------------------------------------------------------------


async def test_a_streak_counts_consecutive_days_and_a_gap_restarts_it(app_client, make_user):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    from app.domains.progress import service

    factory = sql.get_sessionmaker()
    async with factory() as session:
        for offset in (4, 3, 2):
            await service.update_streak(
                session, account_id, "no_buy", on=TODAY - timedelta(days=offset),
            )
        row = await service.update_streak(session, account_id, "no_buy", on=TODAY)
        # A day was skipped, so the run restarts but the longest is remembered.
        assert row.current_length == 1
        assert row.longest_length == 3
        await session.commit()


async def test_counting_the_same_day_twice_does_not_advance_a_streak(app_client, make_user):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    from app.domains.progress import service

    factory = sql.get_sessionmaker()
    async with factory() as session:
        await service.update_streak(session, account_id, "no_buy", on=TODAY)
        row = await service.update_streak(session, account_id, "no_buy", on=TODAY)
        assert row.current_length == 1


async def test_a_broken_streak_is_recorded_without_blame(app_client, make_user):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    from app.domains.progress import service

    factory = sql.get_sessionmaker()
    async with factory() as session:
        await service.update_streak(session, account_id, "no_buy", on=TODAY - timedelta(days=1))
        row = await service.update_streak(session, account_id, "no_buy", on=TODAY, broke=True)
        assert row.current_length == 0
        assert row.reset_count == 1
        await session.commit()

        rows = await service.streaks_for(session, account_id)
        assert "Nothing here is lost by starting again" in rows[0]["note"]


# --- Privacy and language ----------------------------------------------------------------------------------------


async def test_nothing_a_route_returns_ever_scores_a_person(app_client, make_user):
    _, token = await make_user()
    await add_item(app_client, token, "wardrobe", "Navy shirt", {"colour": "navy"})
    await remember(app_client, token, subject_type="colour", signal="rejected", subject_label="mustard")
    await app_client.post("/api/v2/goals", json={"kind": "no_buy", "title": "No buying"}, headers=auth(token))

    for path in (
        "/api/v2/progress", "/api/v2/progress/metrics", "/api/v2/goals",
        "/api/v2/memory", "/api/v2/memory/export", "/api/v2/milestones",
    ):
        body = (await app_client.get(path, headers=auth(token))).json()
        text = all_text(body).lower()
        for banned in (
            "attractiveness", "beauty score", "overall score", "how good you look",
            "better looking", "you look worse", "money wasted",
        ):
            assert banned not in text, f"{path} contains {banned!r}"


async def test_value_to_recover_is_framed_constructively(app_client, make_user):
    _, token = await make_user()
    await add_item(app_client, token, "beauty", "Serum", {"product_type": "serum"},
                   purchase_price="2400.00")
    body = (await app_client.get(
        "/api/v2/progress/metrics/value_to_recover", headers=auth(token),
    )).json()
    assert "not money you have lost" in body["current"]["note"]
    assert "Money you have wasted" in body["definition"]["not_a_measure_of"]
    text = all_text(body).lower()
    assert "wasted money" not in text
