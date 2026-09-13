"""PostgreSQL race and guard-state proofs for Step 9A candidate snapshots."""
from __future__ import annotations

import asyncio

import pytest
from app.domains.purchase import check_service
from app.domains.purchase import service as purchase_service
from app.domains.recommendation.models import PurchaseDecision, ShoppingCandidate
from sqlalchemy import select

from tests.conftest import auth
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
