"""V3-05.8 shared purchase decision memory coverage."""
from __future__ import annotations

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
