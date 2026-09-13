"""PostgreSQL race and guard-state proofs for Step 9A candidate snapshots."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from app.domains.purchase import check_service, decision_memory
from app.domains.purchase import service as purchase_service
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseDecisionEvent,
    ShoppingCandidate,
)
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_domain_shopping import _evaluate
from tests.test_step9a_purchase_memory_guard import _make_candidate_exact


@pytest.mark.asyncio
async def test_candidate_confirmation_waits_for_decision_snapshot_lock(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    """A decision must snapshot recommendation and identity from one candidate state."""
    token, account_id = await registered_supabase_user()
    candidate_id = await _make_candidate_exact(account_id)

    check_entered = asyncio.Event()
    release_check = asyncio.Event()
    confirmation_read_candidate = asyncio.Event()
    original_check = check_service.resolve_care_purchase_check
    original_apply = purchase_service._apply_candidate_corrections

    async def paused_check(*args, **kwargs):
        check_entered.set()
        await release_check.wait()
        return await original_check(*args, **kwargs)

    def marked_apply(row, body):
        confirmation_read_candidate.set()
        return original_apply(row, body)

    monkeypatch.setattr(check_service, "resolve_care_purchase_check", paused_check)
    monkeypatch.setattr(purchase_service, "_apply_candidate_corrections", marked_apply)

    decision_task = asyncio.create_task(app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/decision?on=2026-08-20",
        headers=auth(token), json={"decision": "waiting"},
    ))
    await asyncio.wait_for(check_entered.wait(), timeout=2)

    confirm_task = asyncio.create_task(app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/confirm",
        headers=auth(token), json={"brand": "Changed Labs"},
    ))
    await asyncio.wait_for(confirmation_read_candidate.wait(), timeout=2)
    await asyncio.sleep(0)
    assert not confirm_task.done(), "candidate confirmation bypassed the decision snapshot row lock"

    release_check.set()
    decision_response, confirm_response = await asyncio.gather(decision_task, confirm_task)
    assert decision_response.status_code == 200, decision_response.text
    assert confirm_response.status_code == 200, confirm_response.text

    guard = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/purchase-guard",
        headers=auth(token),
    )
    assert guard.status_code == 200, guard.text
    # Confirmation changed the trusted exact identity after the decision commit,
    # so the old event must not be reinterpreted as history for the new identity.
    assert guard.json()["guard_state"] == "no_step9a_prior_event"
    assert guard.json()["prior_consideration_count"] == 0


@pytest.mark.asyncio
async def test_identity_insufficient_precedes_legacy_coverage_state(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    candidate_id = await _make_candidate_exact(account_id)

    from app.shared.database.sql import get_sessionmaker
    async with get_sessionmaker()() as session:
        session.add(PurchaseDecision(
            account_id=account_id,
            candidate_id=candidate_id,
            evaluation_id=None,
            strategy_key="care_purchase",
            recommendation_verdict="wait",
            recommendation_version="legacy",
            recommendation_snapshot={},
            decision="waiting",
            followed_recommendation=True,
        ))
        candidate = await session.scalar(select(ShoppingCandidate).where(
            ShoppingCandidate.id == candidate_id,
            ShoppingCandidate.account_id == account_id,
        ))
        candidate.brand = None
        await session.commit()

    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/purchase-guard",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["identity"]["state"] == "insufficient"
    assert body["guard_state"] == "identity_insufficient"
    assert body["prior_consideration_count"] == 0


@pytest.mark.parametrize(
    ("decision", "guard_state"),
    [
        ("bought", "exact_prior_bought"),
        ("waiting", "exact_prior_waiting"),
        ("skipped", "exact_prior_skipped"),
    ],
)
@pytest.mark.asyncio
async def test_guard_distinguishes_exact_prior_decision_states(
    app_client, db_clean, registered_supabase_user, decision, guard_state,
):
    token, account_id = await registered_supabase_user()
    candidate_id = await _make_candidate_exact(account_id)
    response = await app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/decision?on=2026-08-20",
        headers=auth(token), json={"decision": decision},
    )
    assert response.status_code == 200, response.text

    guard = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/purchase-guard",
        headers=auth(token),
    )
    assert guard.status_code == 200, guard.text
    body = guard.json()
    assert body["guard_state"] == guard_state
    assert body["prior_consideration_count"] == 1
    assert body["most_recent"]["decision"] == decision
    assert body["owned_redundancy"] is None


@pytest.mark.asyncio
async def test_candidate_delete_cascades_event_and_invalidates_history_cursor(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    candidate_id = await _make_candidate_exact(account_id)
    response = await app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/decision?on=2026-08-20",
        headers=auth(token), json={"decision": "waiting"},
    )
    assert response.status_code == 200, response.text
    history = await app_client.get(
        "/api/v2/shopping/decision-history?limit=1", headers=auth(token),
    )
    assert history.status_code == 200, history.text
    event_id = uuid.UUID(history.json()["items"][0]["id"])

    from app.shared.database.sql import get_sessionmaker
    async with get_sessionmaker()() as session:
        candidate = await session.scalar(select(ShoppingCandidate).where(
            ShoppingCandidate.id == candidate_id,
            ShoppingCandidate.account_id == account_id,
        ))
        await session.delete(candidate)
        await session.commit()
    async with get_sessionmaker()() as session:
        remaining = await session.scalar(
            select(func.count()).select_from(PurchaseDecisionEvent).where(
                PurchaseDecisionEvent.id == event_id,
            )
        )
    assert remaining == 0
    dead_cursor = await app_client.get(
        f"/api/v2/shopping/decision-history?before={event_id}", headers=auth(token),
    )
    assert dead_cursor.status_code == 404


@pytest.mark.asyncio
async def test_public_history_payload_omits_internal_and_mutable_fields(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    candidate_id = await _make_candidate_exact(account_id)
    response = await app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/decision?on=2026-08-20",
        headers=auth(token), json={"decision": "waiting", "note": "private note must stay out of history"},
    )
    assert response.status_code == 200, response.text

    history = await app_client.get(
        "/api/v2/shopping/decision-history?limit=1", headers=auth(token),
    )
    assert history.status_code == 200, history.text
    item = history.json()["items"][0]
    assert set(item) == {
        "id",
        "candidate_id",
        "category",
        "strategy",
        "candidate_display_name",
        "identity",
        "recommendation_at_decision",
        "decision",
        "followed_recommendation",
        "occurred_at",
    }
    for forbidden in (
        "account_id",
        "decision_id",
        "note",
        "recommendation_snapshot",
        "product_url",
        "ai_run_id",
        "model_version",
        "prompt_version",
        "provider",
    ):
        assert forbidden not in item


@pytest.mark.asyncio
async def test_style_event_failure_rolls_back_current_decision(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    evaluation = (await _evaluate(app_client, token, price="2400.00")).json()
    evaluation_id = uuid.UUID(evaluation["id"])

    async def fail_event(*_args, **_kwargs):
        raise RuntimeError("forced Style Step 9A event failure")

    monkeypatch.setattr(decision_memory, "record_decision_event", fail_event)
    with pytest.raises(RuntimeError, match="forced Style Step 9A event failure"):
        await app_client.post(
            f"/api/v2/shopping/evaluations/{evaluation_id}/decision",
            headers=auth(token), json={"decision": "skipped", "note": None},
        )

    from app.shared.database.sql import get_sessionmaker
    async with get_sessionmaker()() as session:
        decisions = await session.scalar(
            select(func.count()).select_from(PurchaseDecision).where(
                PurchaseDecision.account_id == account_id,
                PurchaseDecision.evaluation_id == evaluation_id,
            )
        )
        events = await session.scalar(
            select(func.count()).select_from(PurchaseDecisionEvent).where(
                PurchaseDecisionEvent.account_id == account_id,
            )
        )
    assert decisions == events == 0


@pytest.mark.asyncio
async def test_recommendation_transition_appends_without_rewriting_first_event(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    candidate_id = await _make_candidate_exact(account_id)
    response = await app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/decision?on=2026-08-20",
        headers=auth(token), json={"decision": "waiting"},
    )
    assert response.status_code == 200, response.text

    from app.shared.database.sql import get_sessionmaker
    async with get_sessionmaker()() as session:
        row = await session.scalar(select(PurchaseDecision).where(
            PurchaseDecision.account_id == account_id,
            PurchaseDecision.candidate_id == candidate_id,
        ))
        candidate = await session.scalar(select(ShoppingCandidate).where(
            ShoppingCandidate.id == candidate_id,
            ShoppingCandidate.account_id == account_id,
        ))
        first = await session.scalar(select(PurchaseDecisionEvent).where(
            PurchaseDecisionEvent.decision_id == row.id,
        ))
        first_id = first.id
        old_verdict = first.recommendation_verdict
        new_verdict = "buy" if old_verdict != "buy" else "skip"
        row.recommendation_verdict = new_verdict
        row.recommendation_version = "step-9a-transition-test"
        row.recommendation_fingerprint = "transition-test-fingerprint"
        row.recommendation_snapshot = {"strategy": "care_purchase", "verdict": new_verdict}
        await session.flush()
        await decision_memory.record_decision_event(session, row=row, candidate=candidate)
        await session.commit()

    async with get_sessionmaker()() as session:
        first_after = await session.get(PurchaseDecisionEvent, first_id)
        rows = (await session.execute(select(PurchaseDecisionEvent).where(
            PurchaseDecisionEvent.account_id == account_id,
            PurchaseDecisionEvent.candidate_id == candidate_id,
        ))).scalars().all()
    assert len(rows) == 2
    assert first_after.recommendation_verdict == old_verdict
    assert {event.recommendation_verdict for event in rows} == {old_verdict, new_verdict}
