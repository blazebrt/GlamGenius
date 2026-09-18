"""PostgreSQL race and guard-state proofs for Step 9A candidate snapshots."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from app.domains.purchase import check_service, decision_memory
from app.domains.purchase import service as purchase_service
from app.domains.purchase.identity import identity_for_candidate
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseDecisionEvent,
    ShoppingCandidate,
)
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_step9a_purchase_memory_guard import _make_candidate_exact


async def _self_subject(session, account_id):
    """The account holder, resolved the way every unscoped caller resolves it."""
    from app.domains.family.decision_subject import canonical_decision_subject
    from app.domains.family.subject import account_holder_subject

    return await canonical_decision_subject(
        session,
        principal_account_id=account_id,
        subject=account_holder_subject(account_id),
    )


@pytest.mark.asyncio
async def test_guard_snapshot_is_consistent_during_separate_session_event_commit(
    db_clean, registered_supabase_user,
):
    """A committed event is either wholly visible to the guard or absent.

    The writer holds an uncommitted matching event in one PostgreSQL session
    while a separate reader executes the real guard query.  Explicit events,
    rather than timing sleeps, coordinate the two transactions.
    """
    _token, account_id = await registered_supabase_user()
    candidate_id = await _make_candidate_exact(account_id)
    from app.shared.database.sql import get_sessionmaker
    factory = get_sessionmaker()
    writer_ready, release_writer = asyncio.Event(), asyncio.Event()

    async def writer():
        async with factory() as session:
            candidate = await session.get(ShoppingCandidate, candidate_id)
            identity = identity_for_candidate(candidate)
            session.add(PurchaseDecisionEvent(
                account_id=account_id, candidate_id=candidate_id, decision_id=None,
                category=candidate.category, strategy_key="care_purchase",
                candidate_display_name=candidate.display_name,
                identity_version=identity["version"], identity_state="exact",
                identity_fingerprint=identity["fingerprint"],
                recommendation_verdict="wait", recommendation_version="v3-05.5",
                recommendation_snapshot={}, decision="waiting", followed_recommendation=True,
            ))
            await session.flush()
            writer_ready.set()
            await release_writer.wait()
            await session.commit()

    write_task = asyncio.create_task(writer())
    await asyncio.wait_for(writer_ready.wait(), timeout=2)
    async with factory() as reader:
        candidate = await reader.get(ShoppingCandidate, candidate_id)
        before_commit = await decision_memory.purchase_guard(
            reader,
            principal_account_id=account_id,
            decision_subject=await _self_subject(reader, account_id),
            candidate=candidate,
        )
    release_writer.set()
    await write_task
    async with factory() as reader:
        candidate = await reader.get(ShoppingCandidate, candidate_id)
        after_commit = await decision_memory.purchase_guard(
            reader,
            principal_account_id=account_id,
            decision_subject=await _self_subject(reader, account_id),
            candidate=candidate,
        )
    for result in (before_commit, after_commit):
        if result["most_recent"] is not None:
            assert result["prior_consideration_count"] >= 1
        assert not (
            result["guard_state"].startswith("exact_prior_")
            and result["prior_consideration_count"] == 0
        )
    assert before_commit["prior_consideration_count"] == 0
    assert after_commit["prior_consideration_count"] == 1
    assert after_commit["most_recent"]["decision"] == "waiting"


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
        # Step 11C: whose decision this was. It is this account's own household
        # member id — the only id in the payload, and one the caller supplied or
        # can already read from their own circle. The forbidden list below still
        # keeps every internal and cross-account identifier out.
        "household_subject_id",
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
        # Through the account-owning authority rather than the raw helper: it
        # loads the canonical candidate under the principal itself, which is
        # what a caller outside this module is now expected to do.
        await decision_memory.record_decision_event_for_account(
            session, principal_account_id=account_id, decision_id=row.id,
        )
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
