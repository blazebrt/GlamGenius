"""Pure V3-05.7 Care Purchase read-model coverage."""
from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

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
    PURCHASE_INTELLIGENCE_FOUNDATION_VERSION,
    PURCHASE_STRATEGY_REGISTRY_VERSION,
    check_service,
)
from app.shared.errors.exceptions import ValidationFailedError


def test_v3_05_7_version_does_not_bump_existing_authorities():
    assert CARE_PURCHASE_CHECK_VERSION == "v3-05.7"
    assert PURCHASE_INTELLIGENCE_FOUNDATION_VERSION == "v3-05.0"
    assert PURCHASE_CANDIDATE_TRUTH_VERSION == "v3-05.1"
    assert CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION == "v3-05.1"
    assert CARE_PURCHASE_ASSESSMENT_VERSION == "v3-05.2"
    assert CARE_PURCHASE_EVIDENCE_VERSION == "v3-05.3"
    assert CARE_PURCHASE_VALUE_VERSION == "v3-05.4"
    assert CARE_PURCHASE_VERDICT_VERSION == "v3-05.5"
    assert PRODUCT_QUALITY_CONTRACT_VERSION == "v3-05.0"
    assert PURCHASE_STRATEGY_REGISTRY_VERSION == "v3-05.6"


def _authority(candidate_id: uuid.UUID, account_id: uuid.UUID, category: str, fingerprint: str):
    return {
        "account_id": str(account_id),
        "candidate_id": str(candidate_id),
        "category": category,
        "plan_date": "2026-08-20",
        "assessment_fingerprint": fingerprint,
    }


@pytest.mark.asyncio
async def test_composed_check_reuses_one_assessment_and_aligns_fingerprints(monkeypatch):
    account_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    candidate = SimpleNamespace(id=candidate_id, category="beauty")
    truth = SimpleNamespace(facts_trusted=True)
    assessment = {
        **_authority(candidate_id, account_id, "beauty", "assessment-a"),
        "dimensions": {"identity_confidence": {"missing_information": []}},
    }
    evidence = {**_authority(candidate_id, account_id, "beauty", "assessment-a"), "projection_fingerprint": "evidence-a"}
    value = {**_authority(candidate_id, account_id, "beauty", "assessment-a"), "value_fingerprint": "value-a"}
    verdict = {
        **_authority(candidate_id, account_id, "beauty", "assessment-a"),
        "evidence_projection_fingerprint": "evidence-a",
        "value_fingerprint": "value-a",
        "verdict": "wait",
    }
    monkeypatch.setattr(check_service.purchase_service, "owned_purchase_candidate", lambda *args, **kwargs: _async(candidate))
    monkeypatch.setattr(check_service.purchase_service, "_require_care", lambda category: None)
    monkeypatch.setattr(check_service, "build_care_candidate_truth", lambda row: truth)
    monkeypatch.setattr(check_service, "serialize_care_candidate_truth", lambda row: {"candidate": {"id": str(candidate_id), "category": "beauty"}})
    monkeypatch.setattr(check_service.purchase_service, "care_purchase_assessment", lambda *args, **kwargs: _async(assessment))
    monkeypatch.setattr(check_service, "resolve_care_purchase_evidence", lambda *args, **kwargs: _async(evidence))
    monkeypatch.setattr(check_service, "resolve_care_purchase_value", lambda *args, **kwargs: _async(value))
    monkeypatch.setattr(check_service, "resolve_care_purchase_verdict", lambda *args, **kwargs: _async(verdict))

    result = await check_service.resolve_care_purchase_check(
        object(), account_id=account_id, account_id_str=str(account_id), candidate_id=candidate_id, plan_date=date(2026, 8, 20)
    )
    assert result["care_purchase_check_version"] == "v3-05.7"
    assert result["strategy"] == "care_purchase"
    assert result["assessment"] is assessment
    assert result["evidence"] is evidence
    assert result["value"] is value
    assert result["verdict"] is verdict


@pytest.mark.asyncio
async def test_draft_candidate_is_rejected_before_read_model_assembly(monkeypatch):
    candidate = SimpleNamespace(id=uuid.uuid4(), category="hair")
    monkeypatch.setattr(check_service.purchase_service, "owned_purchase_candidate", lambda *args, **kwargs: _async(candidate))
    monkeypatch.setattr(check_service.purchase_service, "_require_care", lambda category: None)
    monkeypatch.setattr(check_service, "build_care_candidate_truth", lambda row: SimpleNamespace(facts_trusted=False))
    with pytest.raises(ValidationFailedError):
        await check_service.resolve_care_purchase_check(
            object(), account_id=uuid.uuid4(), account_id_str="account", candidate_id=candidate.id, plan_date=None
        )


async def _async(value):
    return value
