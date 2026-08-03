"""Progress metrics, goals and milestones (§1.12) regression coverage.

Assertions:

    * Every metric in ``registry.METRIC_KEYS`` has a computer registered in
      ``metrics.COMPUTERS`` (this is asserted at import time already; the
      test re-asserts as a smoke test).
    * Every metric definition carries the six required things: formula,
      formula_version, inputs, missing-data behaviour, explanation and
      update_frequency.
    * A metric with no data returns STATUS_UNAVAILABLE, value=None,
      populated missing_inputs — never a silent zero.
    * A metric with sufficient data returns STATUS_OK and a value.
    * No metric declares another metric as its input — makes an overall
      attractiveness score inexpressible by construction.
    * Goals: create returns a starting_value snapshot; status='achieved'
      stamps completed_at exactly once; cross-account patch raises
      NotFoundError.
    * Milestone rules are seeded, versioned, and duplicate identical
      events do not create two milestones for the same behaviour rule.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.bootstrap import main as run_seed
from app.domains.identity import service as identity
from app.domains.inventory import service as inv_service
from app.domains.inventory.schemas import ItemCreate
from app.domains.progress import metrics, milestones
from app.domains.progress import service as progress_service
from app.domains.progress.models import (
    Milestone,
    MilestoneRule,
    ProgressGoal,
)
from app.domains.progress.registry import METRIC_BY_KEY, METRIC_KEYS
from app.domains.progress.schemas import GoalCreate, GoalPatch
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import NotFoundError


pytestmark = pytest.mark.asyncio


async def _account() -> uuid.UUID:
    factory = get_sessionmaker()
    async with factory() as session:
        row = await identity.register_account(session, uuid.uuid4())
        await session.commit()
        return row.id


# ---------------------------------------------------------------------------
# Registry invariants
# ---------------------------------------------------------------------------

def test_every_metric_has_the_six_required_declarations():
    """§1.12 — every metric must declare formula, formula_version,
    inputs, missing-data behaviour, explanation, update_frequency."""
    for key in METRIC_KEYS:
        definition = METRIC_BY_KEY[key]
        assert definition.formula, key
        assert definition.formula_version, key
        assert definition.inputs, key
        assert definition.missing_data_behaviour, key
        assert definition.explanation, key
        assert definition.update_frequency, key


def test_no_metric_reads_another_metric_as_input():
    """The registry forbids composite metrics — makes an 'overall
    attractiveness score' inexpressible."""
    metric_keys = set(METRIC_KEYS)
    for key in METRIC_KEYS:
        definition = METRIC_BY_KEY[key]
        for input_name in definition.inputs:
            assert input_name not in metric_keys, (
                f"metric {key} reads another metric {input_name} — the registry "
                "would allow a composite attractiveness score."
            )


def test_metric_registry_and_computers_agree():
    assert set(metrics.COMPUTERS) == set(METRIC_KEYS)


# ---------------------------------------------------------------------------
# Missing-data behaviour
# ---------------------------------------------------------------------------

async def test_metric_with_no_data_returns_status_unavailable(db_clean):
    factory = get_sessionmaker()
    account = await _account()

    async with factory() as session:
        result = await metrics.compute(
            session, account, "wardrobe_readiness", today=date(2026, 2, 15)
        )
    # Never a silent zero — an empty account gets 'unavailable' with a
    # human-readable note explaining what is missing.
    assert result.status == metrics.STATUS_UNAVAILABLE
    assert result.value is None
    assert result.missing_inputs
    assert result.note


async def test_compute_all_reports_status_per_metric(db_clean):
    factory = get_sessionmaker()
    account = await _account()

    async with factory() as session:
        results = await metrics.compute_all(session, account, today=date(2026, 2, 15))

    # Every metric key surfaces — nothing skipped.
    assert {r.key for r in results} == set(METRIC_KEYS)
    for r in results:
        # A metric without data must NOT silently return 0.0 — the shape is
        # (None, unavailable, missing_inputs populated).
        if r.value is None:
            assert r.status == metrics.STATUS_UNAVAILABLE
            assert r.missing_inputs


# ---------------------------------------------------------------------------
# Metric calculation with real inputs
# ---------------------------------------------------------------------------

async def test_inventory_balance_becomes_ok_after_items(db_clean):
    """§1.12 — with items in the inventory, inventory_balance moves off
    'unavailable' and produces a value plus formula_version stamp."""
    factory = get_sessionmaker()
    await run_seed()
    account = await _account()

    async with factory() as session:
        for label in ("Charcoal Blazer", "Linen Trousers"):
            body = ItemCreate.model_validate({
                "category": "wardrobe",
                "display_name": label,
                "details": {"season": ["autumn"]},
            })
            await inv_service.create_item(session, account, body)
        await session.commit()

    async with factory() as session:
        result = await metrics.compute(
            session, account, "inventory_balance", today=date(2026, 2, 15)
        )
    assert result.status in {metrics.STATUS_OK, metrics.STATUS_PARTIAL}
    payload = result.to_payload()
    assert payload["formula_version"] == METRIC_BY_KEY["inventory_balance"].formula_version


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

async def test_goal_create_records_starting_value_and_update_row(db_clean):
    factory = get_sessionmaker()
    await run_seed()
    account = await _account()

    async with factory() as session:
        payload = await progress_service.create_goal(
            session,
            account,
            GoalCreate.model_validate({
                "kind": "wardrobe",
                "title": "Refresh smart-casual capsule",
                "metric_key": "purchase_efficiency",
                "target_value": 0.6,
            }),
        )
        await session.commit()

    assert payload["status"] == "active"
    assert payload["metric_key"] == "purchase_efficiency"
    # starting_value can be None when the metric is unavailable, but the
    # created-update row must always exist so audit is complete.
    assert any(u["source"] == "created" for u in payload["updates"])


async def test_goal_achieved_stamps_completed_at_only_once(db_clean):
    factory = get_sessionmaker()
    await run_seed()
    account = await _account()

    async with factory() as session:
        created = await progress_service.create_goal(
            session,
            account,
            GoalCreate.model_validate({
                "kind": "custom",
                "title": "Take one full-length photo weekly",
            }),
        )
        goal_id = uuid.UUID(created["id"])
        await session.commit()

    # First achieve.
    async with factory() as session:
        first = await progress_service.patch_goal(
            session,
            account,
            goal_id,
            GoalPatch.model_validate({"status": "achieved"}),
        )
        await session.commit()
    assert first["status"] == "achieved"

    async with factory() as session:
        row = await session.get(ProgressGoal, goal_id)
        first_ts = row.completed_at
    assert first_ts is not None

    # Second achieve — completed_at must not move.
    async with factory() as session:
        again = await progress_service.patch_goal(
            session,
            account,
            goal_id,
            GoalPatch.model_validate({"status": "achieved", "progress_note": "still done"}),
        )
        await session.commit()

    async with factory() as session:
        row = await session.get(ProgressGoal, goal_id)
    assert row.completed_at == first_ts


async def test_patch_goal_from_other_account_raises_not_found(db_clean):
    factory = get_sessionmaker()
    await run_seed()
    account_a = await _account()
    account_b = await _account()

    async with factory() as session:
        created = await progress_service.create_goal(
            session,
            account_a,
            GoalCreate.model_validate({"kind": "custom", "title": "A's goal"}),
        )
        goal_id = uuid.UUID(created["id"])
        await session.commit()

    async with factory() as session:
        with pytest.raises(NotFoundError):
            await progress_service.patch_goal(
                session, account_b, goal_id,
                GoalPatch.model_validate({"note": "hijack attempt"}),
            )


async def test_goal_rejects_unknown_metric_key():
    with pytest.raises(Exception):
        GoalCreate.model_validate({
            "kind": "custom",
            "title": "Whatever",
            "metric_key": "attractiveness_score",  # forbidden by construction
        })


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------

async def test_milestone_rules_are_seeded_and_versioned(db_clean):
    factory = get_sessionmaker()
    await run_seed()
    async with factory() as session:
        rules = (
            await session.execute(select(MilestoneRule))
        ).scalars().all()
    assert rules, "milestone rules must be seeded"
    for rule in rules:
        assert rule.rule_key
        assert rule.registry_version
        assert rule.headline


async def test_milestone_award_is_idempotent(db_clean):
    """Same behaviour recorded twice must produce at most one milestone
    row per rule (§3.9 — safe against duplicate event processing)."""
    factory = get_sessionmaker()
    await run_seed()
    account = await _account()

    async with factory() as session:
        # Behaviour that maps to a milestone: reached_own_goal is triggered
        # by achieving a goal. Directly call record_behaviour to keep the
        # test focused.
        await progress_service.record_behaviour(
            session,
            account,
            milestones.BEHAVIOUR_ORGANISED_INVENTORY,
            occurred_on=date(2026, 2, 15),
            subject_id=None,
            detail={"note": "first"},
        )
        await session.commit()

    async with factory() as session:
        await progress_service.record_behaviour(
            session,
            account,
            milestones.BEHAVIOUR_ORGANISED_INVENTORY,
            occurred_on=date(2026, 2, 15),
            subject_id=None,
            detail={"note": "duplicate"},
        )
        await session.commit()

    async with factory() as session:
        awarded = (
            await session.execute(
                select(func.count(Milestone.id)).where(
                    Milestone.account_id == account
                )
            )
        ).scalar_one()
    # Independent of whether a rule matched, awarding must never explode a
    # duplicate. The count is capped at the number of distinct milestone
    # rules that matched.
    async with factory() as session:
        distinct = (
            await session.execute(
                select(func.count(func.distinct(Milestone.rule_key))).where(
                    Milestone.account_id == account
                )
            )
        ).scalar_one()
    assert awarded == distinct
