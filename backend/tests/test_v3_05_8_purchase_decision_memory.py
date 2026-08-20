"""V3-05.8 shared purchase decision memory coverage."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from app.domains.purchase import (
    CARE_PURCHASE_ASSESSMENT_VERSION,
    CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION,
    CARE_PURCHASE_CHECK_VERSION,
    CARE_PURCHASE_EVIDENCE_VERSION,
    CARE_PURCHASE_VALUE_VERSION,
    CARE_PURCHASE_VERDICT_VERSION,
    PRODUCT_QUALITY_CONTRACT_VERSION,
    PURCHASE_CANDIDATE_TRUTH_VERSION,
    PURCHASE_DECISION_MEMORY_VERSION,
    PURCHASE_INTELLIGENCE_FOUNDATION_VERSION,
    PURCHASE_STRATEGY_REGISTRY_VERSION,
)
from app.domains.recommendation.models import PurchaseDecision, PurchaseEvaluation, RecommendationRun, ShoppingCandidate
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select, text

from tests.conftest import auth
from tests.test_v3_05_7_care_purchase_experience import (
    _care_read_only_counts,
    _seed_db_candidate,
)


def test_v3_05_8_adds_memory_without_bumping_prior_authorities():
    assert PURCHASE_DECISION_MEMORY_VERSION == "v3-05.8"
    assert PURCHASE_INTELLIGENCE_FOUNDATION_VERSION == "v3-05.0"
    assert PRODUCT_QUALITY_CONTRACT_VERSION == "v3-05.0"
    assert PURCHASE_CANDIDATE_TRUTH_VERSION == "v3-05.1"
    assert CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION == "v3-05.1"
    assert CARE_PURCHASE_ASSESSMENT_VERSION == "v3-05.2"
    assert CARE_PURCHASE_EVIDENCE_VERSION == "v3-05.3"
    assert CARE_PURCHASE_VALUE_VERSION == "v3-05.4"
    assert CARE_PURCHASE_VERDICT_VERSION == "v3-05.5"
    assert PURCHASE_STRATEGY_REGISTRY_VERSION == "v3-05.6"
    assert CARE_PURCHASE_CHECK_VERSION == "v3-05.7"


async def _check(client, token, candidate_id):
    response = await client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-check?on=2026-08-20",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _decision(client, token, candidate_id, decision, **extra):
    return await client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/decision?on=2026-08-20",
        headers=auth(token),
        json={"decision": decision, **extra},
    )


@pytest.mark.asyncio
async def test_care_decision_memory_is_candidate_backed_reversible_and_readable(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    beauty_id = await _seed_db_candidate(account_id, category="beauty")
    hair_id = await _seed_db_candidate(account_id, category="hair")
    before = await _care_read_only_counts(account_id)

    for candidate_id, choice in ((beauty_id, "waiting"), (hair_id, "skipped")):
        initial = await _check(app_client, token, candidate_id)
        saved = await _decision(app_client, token, candidate_id, choice, note="my considered choice")
        assert saved.status_code == 200, saved.text
        memory = saved.json()
        assert memory["purchase_decision_memory_version"] == "v3-05.8"
        assert memory["candidate_id"] == str(candidate_id)
        assert memory["strategy"] == "care_purchase"
        assert memory["evaluation_id"] is None
        assert memory["decision"] == choice
        assert memory["recommendation_at_decision"]["verdict"] == initial["verdict"]["verdict"]
        assert memory["recommendation_at_decision"]["version"] == "v3-05.5"
        expected_followed = {"buy": "bought", "wait": "waiting", "skip": "skipped"}[initial["verdict"]["verdict"]] == choice
        assert memory["followed_recommendation"] is expected_followed
        factory = get_sessionmaker()
        async with factory() as session:
            snapshot = await session.scalar(
                select(PurchaseDecision.recommendation_snapshot).where(
                    PurchaseDecision.id == uuid.UUID(memory["id"])
                )
            )
        assert snapshot["plan_date"] == "2026-08-20"

        read = await app_client.get(
            f"/api/v2/shopping/candidates/{candidate_id}/decision",
            headers=auth(token),
        )
        assert read.status_code == 200, read.text
        assert read.json()["decision"]["id"] == memory["id"]
        after_check = await _check(app_client, token, candidate_id)
        assert after_check["verdict"] == initial["verdict"]
        assert after_check["decision"]["id"] == memory["id"]

        repeated = await _decision(app_client, token, candidate_id, "bought")
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["id"] == memory["id"]

    after = await _care_read_only_counts(account_id)
    assert after["evaluations"] == before["evaluations"]
    assert after["factors"] == before["factors"]
    assert after["runs"] == before["runs"]
    assert after["inputs"] == before["inputs"]
    assert after["inventory"] == before["inventory"]
    assert after["value_events"] == before["value_events"]
    assert after["entitlement"] == before["entitlement"]
    assert after["decisions"] == before["decisions"] + 2


@pytest.mark.asyncio
async def test_care_decision_memory_rejects_client_policy_fields_and_cross_account_access(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    candidate_id = await _seed_db_candidate(account_id)
    rejected = await _decision(
        app_client,
        token,
        candidate_id,
        "waiting",
        strategy="style_purchase",
        verdict="buy",
        recommendation_fingerprint="forged",
    )
    assert rejected.status_code == 422
    empty = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/decision",
        headers=auth(token),
    )
    assert empty.status_code == 200
    assert empty.json()["decision"] is None

    other_token, _ = await registered_supabase_user()
    assert (await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/decision",
        headers=auth(other_token),
    )).status_code == 404
    assert (await _decision(
        app_client, other_token, candidate_id, "waiting"
    )).status_code == 404


@pytest.mark.asyncio
async def test_care_decision_save_is_serialized_and_keeps_one_row(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    candidate_id = await _seed_db_candidate(account_id)
    responses = await asyncio.gather(
        _decision(app_client, token, candidate_id, "bought"),
        _decision(app_client, token, candidate_id, "waiting"),
    )
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    assert {response.json()["decision"] for response in responses} <= {"bought", "waiting"}
    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(PurchaseDecision).where(
                PurchaseDecision.account_id == account_id,
                PurchaseDecision.candidate_id == candidate_id,
                PurchaseDecision.strategy_key == "care_purchase",
            )
        )).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_care_decision_write_requires_active_care_and_rejects_other_categories(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    beauty_id = await _seed_db_candidate(account_id)
    monkeypatch.setattr(
        "app.domains.purchase.decision_memory.is_active_care_category",
        lambda category: False,
    )
    assert (await _decision(app_client, token, beauty_id, "waiting")).status_code == 422

    monkeypatch.setattr(
        "app.domains.purchase.decision_memory.is_active_care_category",
        lambda category: category in {"beauty", "hair"},
    )
    fragrance_id = await _seed_db_candidate(account_id, category="perfumes")
    supplement_id = await _seed_db_candidate(account_id, category="supplements")
    assert (await _decision(app_client, token, fragrance_id, "waiting")).status_code == 422
    assert (await _decision(app_client, token, supplement_id, "waiting")).status_code == 422


def _run_alembic(revision: str) -> None:
    command = "upgrade" if revision in {"head", "e5f6a7b8c9d0"} else "downgrade"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", command, revision],
        cwd=Path(__file__).parents[1],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_v3_05_8_migration_backfills_existing_style_decision(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        run = RecommendationRun(account_id=account_id, kind="shopping", status="completed")
        candidate = ShoppingCandidate(
            account_id=account_id,
            source="manual",
            category="wardrobe",
            display_name="Historical style candidate",
            currency="INR",
        )
        session.add_all([run, candidate])
        await session.flush()
        evaluation = PurchaseEvaluation(
            account_id=account_id,
            candidate_id=candidate.id,
            run_id=run.id,
            verdict="skip",
            roi_score=0.23,
            roi_version="appearance-roi-v1",
            summary="Historical style recommendation",
        )
        session.add(evaluation)
        await session.commit()
        decision_id = uuid.uuid4()
        created_values = {
            "id": decision_id,
            "evaluation_id": evaluation.id,
            "account_id": account_id,
            "decision": "skipped",
            "note": "kept from the old schema",
            "followed_recommendation": True,
        }
        evaluation_values = {
            "id": evaluation.id,
            "candidate_id": candidate.id,
            "verdict": evaluation.verdict,
            "roi_version": evaluation.roi_version,
            "roi_score": evaluation.roi_score,
        }

    _run_alembic("d5e6f7a8b9c0")
    try:
        async with factory() as session:
            await session.execute(text("""
                INSERT INTO purchase_decisions
                    (id, evaluation_id, account_id, decision, note, followed_recommendation)
                VALUES (:id, :evaluation_id, :account_id, :decision, :note, :followed_recommendation)
            """), created_values)
            await session.commit()
        _run_alembic("e5f6a7b8c9d0")
        async with factory() as session:
            row = (await session.execute(text("""
                SELECT id, evaluation_id, account_id, decision, note,
                       followed_recommendation, candidate_id, strategy_key,
                       recommendation_verdict, recommendation_version,
                       recommendation_fingerprint, recommendation_snapshot
                FROM purchase_decisions WHERE id = :id
            """), {"id": decision_id})).mappings().one()
        assert row["id"] == decision_id
        assert row["evaluation_id"] == evaluation_values["id"]
        assert row["account_id"] == account_id
        assert row["decision"] == created_values["decision"]
        assert row["note"] == created_values["note"]
        assert row["followed_recommendation"] is True
        assert row["candidate_id"] == candidate.id
        assert row["strategy_key"] == "style_purchase"
        assert row["recommendation_verdict"] == evaluation_values["verdict"]
        assert row["recommendation_version"] == evaluation_values["roi_version"]
        assert row["recommendation_fingerprint"] is None
        assert row["recommendation_snapshot"] == {
            "strategy": "style_purchase",
            "evaluation_id": str(evaluation.id),
            "verdict": evaluation.verdict,
            "roi_version": evaluation.roi_version,
            "roi_score": evaluation.roi_score,
        }
        async with factory() as session:
            assert await session.scalar(text("SELECT count(*) FROM purchase_decisions")) == 1
    finally:
        _run_alembic("head")
