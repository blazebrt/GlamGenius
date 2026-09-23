"""Step 14 — the Purchase Operating System: one current answer, composed.

Against PostgreSQL 16 and the real routes. The Product Result is built by its
own route; Care and Fragrance by their canonical checks; official records by
the real importer and matcher. Nothing below re-grades or re-decides anything:
the suite proves the operating layer reads each authority as it is, applies its
one cross-authority rule (the exact official-record ``wait`` ceiling) and treats
everything else as context.

Letters follow the Step 14 brief's backend matrix, A to AE.
"""
from __future__ import annotations

import ast
import json
import re
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from app.api.v2 import product as product_api
from app.api.v2 import shopping as shopping_api
from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun
from app.domains.inventory.models import InventoryItem, InventoryProductLink
from app.domains.product import service as product_service
from app.domains.product.devices import _hash
from app.domains.product.models import LabelSnapshot, ScanDecisionEvent, ScanDevice
from app.domains.purchase import check_service
from app.domains.purchase import operating_system as purchase_os
from app.domains.recommendation.models import PurchaseDecision, ShoppingCandidate
from app.shared.database.base import Base
from app.shared.database.sql import get_engine, get_sessionmaker
from sqlalchemy import event, func, select, text

from tests import test_step6a_comparable_alternative as alternative_fixtures
from tests.conftest import auth
from tests.test_official_records_api import (
    BARCODE,
    BATCH,
    BRAND,
    PRODUCT,
    RECALL_ID,
    SOURCE_CHECKED_AT,
    confirm_label,
    label_facts,
    off_clean,  # noqa: F401 - re-exported fixture
)
from tests.test_step6a_comparable_alternative import (
    no_off_network,  # noqa: F401 - re-exported fixture
    published_rules,  # noqa: F401 - re-exported fixture
)
from tests.test_step11c_subject_scoped_decision_memory import _member
from tests.test_step12c_product_watch import (
    OTHER_BARCODE,
    _customer,
    _device,
    _force_label_publication,
    _force_regulatory_publication,
    _ingest,
    _ingest_rows,
    _row,
)
from tests.test_v3_05_7_care_purchase_experience import _seed_db_candidate
from tests.test_v3_05_9_fragrance_purchase import _fragrance_item

LATER = SOURCE_CHECKED_AT + timedelta(days=3)
OS_SOURCE = Path(purchase_os.__file__)

#: Label facts that the published ruleset grades to each action.
FACTS = {
    "buy": {"ingredients_text": "whole oats",
            "nutrition_per_100g": {"sugars_g": "1", "saturated_fat_g": "1", "salt_g": "0.01"}},
    "wait": {"ingredients_text": "maida, sugar, salt, emulsifier (322)",
             "nutrition_per_100g": {"sugars_g": "15", "saturated_fat_g": "4", "salt_g": "1"}},
    "skip": {"ingredients_text": "sugar, palm oil, maida, emulsifier (322), artificial flavour",
             "nutrition_per_100g": {"sugars_g": "45", "saturated_fat_g": "15", "salt_g": "2.5"}},
}


# ---------------------------------------------------------------------------
# Helpers — every fact is written through the real product paths
# ---------------------------------------------------------------------------
async def _pack(app_client, device, token, account_id, kind="buy", barcode=BARCODE, **changes):
    return await confirm_label(
        app_client, device, token, account_id, barcode, label_facts(**{**FACTS[kind], **changes}),
    )


async def _shelf_pack(device, account_id, barcode=BARCODE, category="beauty"):
    """A confirmed body-product pack, written through the confirm route's own service calls.

    The food label route's schema has no category and the skin-care route binds
    ``skin_care``, which the Step 10A shelf does not list; this is how Step 10A's
    own suite reaches a shelf-eligible pack, through ``record_scan``,
    ``apply_confirmed_label`` and ``store_label_snapshot`` in that order.
    """
    facts = {"product_category": category, "product_name": PRODUCT, "brand": BRAND,
             "ingredients_text": "aqua, glycerin"}
    async with get_sessionmaker()() as session:
        scan_device = (await session.execute(
            select(ScanDevice).where(ScanDevice.token_hash == _hash(device["X-Device-Token"]))
        )).scalar_one()
        run = AIRun(account_id=account_id, feature="product_label_transcribe", provider="test", model="test-model",
                    prompt_version="scan-label.v1", schema_version="scan-label.v1",
                    status=AI_STATUS_SUCCEEDED, validation_passed=True)
        session.add(run)
        await session.flush()
        await product_service.lock_label_version(session, barcode)
        event, _created = await product_service.record_scan(
            session, barcode=barcode, outcome=product_service.OUTCOME_LABEL, client_scan_id=uuid.uuid4().hex,
            device_id=scan_device.id, account_id=account_id, label_facts=facts, ai_run_id=run.id,
        )
        await product_service.apply_confirmed_label(session, barcode=barcode, facts=facts)
        await product_service.store_label_snapshot(
            session, barcode=barcode, facts=facts, device_id=scan_device.id, scan_event_id=event.id,
        )
        await session.commit()


async def _check(app_client, device, token=None, *, physical=True, subject_id=None, barcode=BARCODE):
    params = {} if physical else {"physical_pack_context": "false"}
    if subject_id:
        params["subject_id"] = subject_id
    return await app_client.get(
        f"/api/v2/scan/verdict/{barcode}/purchase-check",
        headers={**device, **(auth(token) if token else {})}, params=params,
    )


async def _ok_check(*args, **kwargs) -> dict:
    response = await _check(*args, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


async def _verdict(app_client, device, token=None, *, physical=True, barcode=BARCODE) -> dict:
    params = {} if physical else {"physical_pack_context": "false"}
    response = await app_client.get(
        f"/api/v2/scan/verdict/{barcode}", headers={**device, **(auth(token) if token else {})}, params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _authority(payload: dict, name: str) -> dict | None:
    return next((row for row in payload["authorities"] if row["authority"] == name), None)


async def _scan_decide(app_client, token, verdict: dict, decision: str, key: str, *, subject_id=None):
    version = verdict["label_version"]
    response = await app_client.post(
        f"/api/v2/scan/verdict/{verdict['barcode']}/memory",
        headers=auth(token), params={"subject_id": subject_id} if subject_id else {},
        json={
            "decision": decision, "label_snapshot_id": version["id"],
            "label_version": version["version_number"],
            "content_fingerprint": version["content_fingerprint"], "idempotency_key": key,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _candidate_check(app_client, token, candidate_id, *, subject_id=None) -> dict:
    params = {"on": "2026-08-20"}
    if subject_id:
        params["subject_id"] = subject_id
    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/purchase-check", headers=auth(token), params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _legacy_candidate(account_id, category: str) -> uuid.UUID:
    """A candidate row with a category no active strategy owns — legacy data exists."""
    candidate_id = uuid.uuid4()
    async with get_sessionmaker()() as session:
        session.add(ShoppingCandidate(
            id=candidate_id, account_id=account_id, source="manual", category=category,
            display_name="Legacy candidate", details={}, verification_state="confirmed",
            extraction_confidence=0.99, price=Decimal("500"), currency="INR",
        ))
        await session.commit()
    return candidate_id


async def _fragrance_candidate(app_client, token) -> str:
    response = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={"source": "manual", "item": _fragrance_item()},
    )
    assert response.status_code == 200, response.text
    return response.json()["candidate"]["id"]


def _forbid_candidate_checks(monkeypatch):
    async def refuse(*_args, **_kwargs):
        raise AssertionError("a canonical purchase check was called for a path that must not use one")
    monkeypatch.setattr(check_service, "resolve_care_purchase_check", refuse)
    monkeypatch.setattr(check_service, "resolve_fragrance_check", refuse)


async def _table_state() -> dict[str, str | None]:
    """A digest of every row of every table: an insert, update or delete anywhere changes it."""
    async with get_sessionmaker()() as session:
        return {
            table.name: await session.scalar(text(
                f'SELECT md5(string_agg(row_text, \'|\' ORDER BY row_text)) '
                f'FROM (SELECT t::text AS row_text FROM "{table.name}" t) rows'
            ))
            for table in Base.metadata.sorted_tables
        }


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys(item)}
    return set()


# ---------------------------------------------------------------------------
# A, B. Care and Fragrance: the canonical verdict is the decision
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("category", ["beauty", "hair"])
async def test_a_care_decision_is_the_canonical_care_verdict(app_client, db_clean, registered_supabase_user, category):
    token, account_id = await registered_supabase_user()
    candidate_id = await _seed_db_candidate(account_id, category=category)
    care = (await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-check?on=2026-08-20", headers=auth(token),
    )).json()["verdict"]
    payload = await _candidate_check(app_client, token, candidate_id)
    assert payload["contract_version"] == "step-14-v1"
    assert payload["context"] == {"kind": "candidate", "strategy": "care_purchase", "category": category}
    assert payload["decision"] == {
        "state": "decided", "verdict": care["verdict"], "primary_reason_code": care["primary_reason_code"],
        "primary_reason_authority": "care_purchase", "decision_fingerprint": care["decision_fingerprint"],
    }


async def test_b_fragrance_decision_is_the_canonical_fragrance_verdict(app_client, db_clean, registered_supabase_user):
    token, _account_id = await registered_supabase_user()
    candidate_id = await _fragrance_candidate(app_client, token)
    fragrance = (await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/fragrance-check", headers=auth(token),
    )).json()["verdict"]
    payload = await _candidate_check(app_client, token, candidate_id)
    assert payload["context"]["strategy"] == "fragrance_purchase"
    assert payload["decision"] == {
        "state": "decided", "verdict": fragrance["verdict"], "primary_reason_code": fragrance["primary_reason_code"],
        "primary_reason_authority": "fragrance_purchase", "decision_fingerprint": fragrance["decision_fingerprint"],
    }


async def test_a_b_unconfirmed_candidates_are_not_enough_information_not_a_verdict(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    draft = await _seed_db_candidate(account_id, verification_state="draft")
    _forbid_candidate_checks(monkeypatch)
    payload = await _candidate_check(app_client, token, draft)
    assert payload["decision"]["state"] == "not_enough_information"
    assert payload["decision"]["verdict"] is None
    assert payload["decision"]["primary_reason_code"] == "candidate_confirmation_required"


# ---------------------------------------------------------------------------
# C, D. Supplements are prohibited; unknown strategies fail closed
# ---------------------------------------------------------------------------
async def test_c_a_supplement_is_purchase_prohibited_with_no_fallback(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    candidate_id = await _legacy_candidate(account_id, "supplements")
    _forbid_candidate_checks(monkeypatch)
    payload = await _candidate_check(app_client, token, candidate_id)
    assert payload["decision"] == {
        "state": "prohibited", "verdict": None, "primary_reason_code": "supplement_purchase_prohibited",
        "primary_reason_authority": "supplement_boundary", "decision_fingerprint": None,
    }
    assert payload["boundary"] == {"code": "supplement_purchase_prohibited", "redirect": "supplement_label_utility"}
    assert payload["memory"] is None and payload["ownership"] is None and payload["alternative"] is None
    rendered = json.dumps(payload).lower()
    for phrase in ("buy this", "better supplement", "you need"):
        assert phrase not in rendered


@pytest.mark.parametrize("category", ["clothing", "shoes", "accessories", "cookware", "", "BEAUTY"])
async def test_d_an_unregistered_category_is_unsupported_never_defaulted(
    app_client, db_clean, registered_supabase_user, monkeypatch, category,
):
    token, account_id = await registered_supabase_user()
    candidate_id = await _legacy_candidate(account_id, category)
    _forbid_candidate_checks(monkeypatch)
    payload = await _candidate_check(app_client, token, candidate_id)
    assert payload["decision"]["state"] == "unsupported"
    assert payload["decision"]["verdict"] is None
    assert payload["decision"]["primary_reason_code"] == "unsupported_strategy"
    assert payload["context"]["strategy"] is None


def test_d_strategy_routing_is_an_explicit_literal_table():
    assert set(purchase_os.CANDIDATE_ADAPTERS) == {"care_purchase", "fragrance_purchase", "supplement_purchase"}
    tree = ast.parse(OS_SOURCE.read_text())
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not names & {"getattr", "globals", "locals", "eval", "exec", "__import__"}


async def test_d_another_accounts_candidate_is_the_same_not_found(app_client, db_clean, registered_supabase_user):
    token_a, account_a = await registered_supabase_user()
    token_b, _account_b = await registered_supabase_user()
    candidate_id = await _seed_db_candidate(account_a)
    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/purchase-check", headers=auth(token_b),
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# E, F. Scan: the Product Result action, and nothing manufactured
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["buy", "wait", "skip"])
async def test_e_an_ordinary_scan_decision_is_the_product_result_action(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, kind,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, kind)
    verdict = await _verdict(app_client, device, token)
    assert verdict["decision"]["action"] == kind
    payload = await _ok_check(app_client, device, token)
    assert payload["context"]["kind"] == "scan" and payload["context"]["strategy"] == "scan_product"
    assert payload["identity"]["state"] == "exact"
    assert payload["identity"]["label_version"] == verdict["label_version"]["version_number"]
    assert payload["decision"]["state"] == "decided"
    assert payload["decision"]["verdict"] == kind
    assert payload["decision"]["primary_reason_code"] == verdict["decision"]["reason_key"]
    assert payload["decision"]["primary_reason_authority"] == "product_result"
    assert _authority(payload, "official_records")["status"] == "no_governed_match"


async def test_f_an_ungraded_product_result_is_never_turned_into_a_verdict(
    app_client, db_clean, off_clean, registered_supabase_user, no_off_network,  # noqa: F811
):
    """Without the published ruleset the Product Result has no action."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    verdict = await _verdict(app_client, device, token)
    assert verdict["decision"]["action"] is None
    payload = await _ok_check(app_client, device, token)
    assert payload["decision"]["state"] == "not_enough_information"
    assert payload["decision"]["verdict"] is None
    assert payload["decision"]["primary_reason_code"] == verdict["decision"]["reason_key"]


async def test_f_no_exact_version_fails_closed(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    token, _account_id, device = await _customer(app_client, registered_supabase_user)
    payload = await _ok_check(app_client, device, token, barcode="8901058000999")
    assert payload["identity"]["state"] == "insufficient"
    assert payload["decision"] == {
        "state": "not_enough_information", "verdict": None, "primary_reason_code": "identity_insufficient",
        "primary_reason_authority": "purchase_os", "decision_fingerprint": payload["decision"]["decision_fingerprint"],
    }
    assert "exact_label_version" in payload["missing_information"]
    assert payload["memory"]["state"] == "identity_insufficient"
    assert payload["ownership"]["state"] == "identity_insufficient"


# ---------------------------------------------------------------------------
# G, H, I. The exact current official record places a WAIT ceiling
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("kind", "expected", "reason_is_official"), [
    ("buy", "wait", True), ("wait", "wait", False), ("skip", "skip", False),
])
async def test_ghi_an_exact_current_official_record_places_a_wait_ceiling(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
    kind, expected, reason_is_official,
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, kind)
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    verdict = await _verdict(app_client, device, token)
    assert verdict["decision"]["action"] == kind, "the Product Result itself is unchanged"
    assert verdict["official_records"]["records"], "the record matches this pack"
    payload = await _ok_check(app_client, device, token)
    assert payload["decision"]["verdict"] == expected
    official = _authority(payload, "official_records")
    assert official["source"] == {"name": "FSSAI / FoSCoS", "url": "https://foscos.fssai.gov.in/food-recall"}
    if reason_is_official:
        assert payload["decision"]["primary_reason_code"] == "official_record_matches_pack"
        assert payload["decision"]["primary_reason_authority"] == "official_records"
        assert official["status"] == "applied"
    else:
        assert payload["decision"]["primary_reason_code"] == verdict["decision"]["reason_key"]
        assert official["status"] == "consistent_with_decision"


async def test_g_an_unknown_base_under_a_governed_record_is_wait(
    app_client, db_clean, off_clean, registered_supabase_user, no_off_network, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    assert (await _verdict(app_client, device, token))["decision"]["action"] is None
    payload = await _ok_check(app_client, device, token)
    assert payload["decision"]["verdict"] == "wait"
    assert payload["decision"]["primary_reason_code"] == "official_record_matches_pack"


def test_ghi_the_ceiling_is_a_written_table_not_arithmetic():
    assert {base: purchase_os.official_ceiling(base) for base in ("buy", "wait", "skip", None)} == {
        "buy": "wait", "wait": "wait", "skip": "skip", None: "wait",
    }


# ---------------------------------------------------------------------------
# J, K, L. Integrity, ambiguity and Open Food Facts never create the guard
# ---------------------------------------------------------------------------
async def test_j_a_corrupt_official_ledger_gives_no_guard(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    async with get_sessionmaker()() as session:
        await session.execute(text("UPDATE official_records SET latest_revision = latest_revision + 1"))
        await session.commit()
    verdict = await _verdict(app_client, device, token)
    assert verdict["official_records"]["records"], "the screen still lists the record"
    payload = await _ok_check(app_client, device, token)
    assert payload["decision"]["verdict"] == "buy"
    assert payload["decision"]["primary_reason_code"] != "official_record_matches_pack"
    assert _authority(payload, "official_records")["status"] == "no_governed_match"


async def test_j_a_record_without_the_openable_source_gives_no_guard(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    async with get_sessionmaker()() as session:
        await session.execute(text("UPDATE official_records SET source_url = 'http://foscos.fssai.gov.in/food-recall'"))
        await session.commit()
    payload = await _ok_check(app_client, device, token)
    assert payload["decision"]["verdict"] == "buy"


async def test_k_an_ambiguous_official_match_gives_no_guard(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
):
    """Two rows under one licence and lot; one states no identity, so nothing resolves."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _ingest_rows(tmp_path, [
        _row(recall_id=RECALL_ID, brand=BRAND, product=PRODUCT),
        _row(recall_id="907", brand="", product=""),
    ])
    verdict = await _verdict(app_client, device, token)
    assert verdict["official_records"]["records"] == []
    payload = await _ok_check(app_client, device, token)
    assert payload["decision"]["verdict"] == "buy"
    assert _authority(payload, "official_records")["status"] == "no_governed_match"


async def test_l_open_food_facts_cannot_create_an_official_match(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
):
    from tests.test_step6a_comparable_alternative import seed_off

    token, _account_id, device = await _customer(app_client, registered_supabase_user)
    await seed_off(BARCODE, name=PRODUCT, brands=BRAND, ingredients_text=f"oats. FSSAI Lic {BATCH}")
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    payload = await _ok_check(app_client, device, token)
    assert payload["identity"]["state"] == "insufficient"
    assert payload["decision"]["primary_reason_code"] == "identity_insufficient"
    assert _authority(payload, "official_records")["status"] == "identity_insufficient"


# ---------------------------------------------------------------------------
# M. A reference view has no physical-pack authority
# ---------------------------------------------------------------------------
async def test_m_a_reference_view_never_uses_physical_pack_context(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    payload = await _ok_check(app_client, device, token, physical=False)
    assert payload["identity"]["physical_pack_context"] is False
    assert payload["identity"]["reference_view"] is True
    assert payload["decision"]["verdict"] == "buy"
    assert _authority(payload, "official_records")["status"] == "reference_view"
    assert payload["memory"]["state"] == "reference_view"
    assert payload["ownership"]["state"] == "reference_view"


async def test_m_another_device_without_the_capture_gets_no_pack_authority(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    stranger = await _device(app_client)
    payload = await _ok_check(app_client, stranger)
    assert payload["identity"]["physical_pack_context"] is False
    assert payload["decision"]["verdict"] == "buy"
    assert _authority(payload, "official_records")["status"] == "physical_pack_required"
    assert "physical_pack" in payload["missing_information"]


@pytest.mark.parametrize("requested_physical", [True, False])
async def test_m_the_operating_layer_itself_refuses_a_record_without_pack_authority(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
    requested_physical,
):
    """Defence in depth: even a governed record that reached the layer is not applied off-pack.

    The Product Result already withholds records without physical-pack
    authority. This proves the operating layer does not depend on that: given a
    real, governed, current record in a Product Result that did not grant pack
    authority (another phone, or a reference view), it still places no ceiling.
    """
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    held = await _verdict(app_client, device, token)
    assert held["official_records"]["records"], "the record must really be governed for this pack"
    leaked = {**held, "physical_pack_context": False}
    async with get_sessionmaker()() as session:
        payload = await purchase_os.scan_purchase_check(
            session, barcode=BARCODE, product_result=leaked, device=None,
            requested_physical_pack_context=requested_physical, principal_account_id=None, decision_subject=None,
        )
    assert payload["decision"]["verdict"] == "buy"
    assert payload["decision"]["primary_reason_code"] != "official_record_matches_pack"
    assert _authority(payload, "official_records")["status"] in {"physical_pack_required", "reference_view"}
    assert "source" not in _authority(payload, "official_records")


# ---------------------------------------------------------------------------
# N. Omission from a later export, a termination date or status text never clears
# ---------------------------------------------------------------------------
async def test_n_omission_from_a_later_export_does_not_release_the_guard(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    await _ingest_rows(tmp_path, [_row(recall_id="930", batch="Z-999")], checked_at=LATER)
    record = (await _verdict(app_client, device, token))["official_records"]["records"][0]
    assert record["seen_in_latest_successful_check"] is False
    payload = await _ok_check(app_client, device, token)
    assert payload["decision"]["verdict"] == "wait"
    assert payload["decision"]["primary_reason_code"] == "official_record_matches_pack"
    for word in ("cleared", "resolved", "safe", "no longer"):
        assert word not in json.dumps(payload).lower()


async def test_n_a_termination_date_or_status_text_does_not_release_the_guard(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT, status="Terminated", termination="15-08-2026")
    payload = await _ok_check(app_client, device, token)
    assert payload["decision"]["verdict"] == "wait"


# ---------------------------------------------------------------------------
# O, P, Q. Formula change, regulatory change and alternatives are context
# ---------------------------------------------------------------------------
async def test_o_a_formula_change_is_context_and_never_changes_the_decision(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, monkeypatch,  # noqa: F811
):
    _force_label_publication(monkeypatch)
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _pack(app_client, device, token, account_id, "buy", ingredients_text="rolled oats, wheat")
    verdict = await _verdict(app_client, device, token)
    assert verdict["label_change"]["status"] == "changed"
    payload = await _ok_check(app_client, device, token)
    assert payload["decision"]["verdict"] == verdict["decision"]["action"] == "buy"
    assert _authority(payload, "label_change") == {
        "authority": "label_change", "status": "changed", "effect": "context_only",
    }


@pytest.mark.parametrize(("kind", "expected"), [("buy", "wait"), ("skip", "skip")])
async def test_p_a_regulatory_change_is_context_and_never_changes_the_decision(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
    monkeypatch, kind, expected,
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, kind)
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    before = (await _ok_check(app_client, device, token))["decision"]
    _force_regulatory_publication(monkeypatch)
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT, status="Completed", checked_at=LATER)
    after = await _ok_check(app_client, device, token)
    assert _authority(after, "regulatory_change")["effect"] == "context_only"
    assert after["decision"]["verdict"] == before["verdict"] == expected
    assert after["decision"]["primary_reason_code"] == before["primary_reason_code"]


async def test_q_a_better_alternative_is_context_and_never_changes_the_decision(db_clean):
    product_result = {
        "decision": {"action": "buy", "reason_key": "label_facts"},
        "label_version": {"id": str(uuid.uuid4()), "version_number": 1, "content_fingerprint": "f" * 64},
        "physical_pack_context": False, "official_records": {"records": []},
        "label_change": {"status": "first_observed_version"},
        "alternative": {"status": "available", "reason_key": "available", "candidate": {
            "barcode": "8901058000214", "product_name": "Other", "brand": "B", "grade": "A",
            "decision": "buy", "attribution": {"source": "Open Food Facts"},
        }},
    }
    async with get_sessionmaker()() as session:
        payload = await purchase_os.scan_purchase_check(
            session, barcode=BARCODE, product_result=product_result, device=None,
            requested_physical_pack_context=True, principal_account_id=None, decision_subject=None,
        )
    assert payload["decision"]["verdict"] == "buy"
    assert payload["alternative"]["status"] == "available"
    assert isinstance(payload["alternative"]["candidate"], dict), "at most one candidate, never a list"
    assert payload["alternative"]["candidate"]["attribution"] == {"source": "Open Food Facts"}
    assert _authority(payload, "alternative") == {"authority": "alternative", "status": "available", "effect": "context_only"}


# ---------------------------------------------------------------------------
# R, S. Ownership only from the shelf link; a decision is not ownership
# ---------------------------------------------------------------------------
async def test_r_exact_ownership_is_shown_only_when_an_inventory_link_proves_it(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _shelf_pack(device, account_id)
    verdict = await _verdict(app_client, device, token)
    before = await _ok_check(app_client, device, token)
    assert before["ownership"]["state"] == "not_owned"
    version = verdict["label_version"]
    added = await app_client.post("/api/v2/inventory/from-scan", headers={**device, **auth(token)}, json={
        "barcode": BARCODE, "label_snapshot_id": version["id"], "label_version": version["version_number"],
        "content_fingerprint": version["content_fingerprint"], "client_mutation_id": "step14-own",
    })
    assert added.status_code == 200, added.text
    after = await _ok_check(app_client, device, token)
    assert after["ownership"] == {"authority": "inventory_product_link", "effect": "context_only", "state": "owned_exact_version"}
    assert after["decision"] == before["decision"], "owning the exact pack never changes the scan decision"


async def test_r_a_food_pack_is_never_claimed_as_owned(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    payload = await _ok_check(app_client, device, token)
    assert payload["ownership"]["state"] == "not_eligible_for_shelf"


async def test_s_a_bought_decision_is_memory_and_never_ownership(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _shelf_pack(device, account_id)
    verdict = await _verdict(app_client, device, token)
    await _scan_decide(app_client, token, verdict, "BUY", "step14-bought")
    payload = await _ok_check(app_client, device, token)
    assert payload["memory"]["current_decision"]["decision"] == "BUY"
    assert payload["ownership"]["state"] == "not_owned"
    async with get_sessionmaker()() as session:
        assert await session.scalar(select(func.count(InventoryItem.id)).where(InventoryItem.account_id == account_id)) == 0
        assert await session.scalar(select(func.count(InventoryProductLink.id))) == 0


# ---------------------------------------------------------------------------
# T, U. Candidate ownership and value come from their own strategies
# ---------------------------------------------------------------------------
async def test_t_care_owned_value_context_is_the_care_authoritys(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    candidate_id = await _seed_db_candidate(account_id)
    context = (await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-check?on=2026-08-20", headers=auth(token),
    )).json()["verdict"]["decision_context"]
    payload = await _candidate_check(app_client, token, candidate_id)
    assert payload["ownership"]["role_status"] == context["role_status"]
    assert payload["ownership"]["eligible_owned_same_slot_count"] == context["eligible_owned_same_slot_count"]
    assert payload["value"]["candidate_spend_status"] == context["candidate_spend_status"]
    assert payload["value"]["owned_value_recovery_status"] == context["owned_value_recovery_status"]
    assert payload["value"]["currency_context_status"] == context["currency_context_status"]


async def test_u_fragrance_ownership_is_the_fragrance_authoritys(app_client, db_clean, registered_supabase_user):
    token, _account_id = await registered_supabase_user()
    candidate_id = await _fragrance_candidate(app_client, token)
    collection = (await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/fragrance-check", headers=auth(token),
    )).json()["collection_context"]
    payload = await _candidate_check(app_client, token, candidate_id)
    assert payload["ownership"]["owned_perfume_count"] == collection["owned_perfume_count"]
    assert payload["ownership"]["exact_owned_count"] == len(collection["exact_owned"])
    assert payload["ownership"]["same_family_owned_count"] == len(collection["same_family_owned"])


def test_t_u_the_operating_layer_imports_no_strategy_internals():
    tree = ast.parse(OS_SOURCE.read_text())
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    for internal in ("care_assessment", "care_evidence", "care_value", "care_verdict", "verdict_service",
                     "value_service", "evidence_service", "fragrance_verdict", "nutrition.grading",
                     "alternatives", "official_records.matching"):
        assert not any(internal in module for module in imported), internal


# ---------------------------------------------------------------------------
# V, W, X, Y, Z. Memory: per person, never adopted, never reconstructed
# ---------------------------------------------------------------------------
async def test_v_two_household_members_get_independent_scan_memory(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    member_a = await _member(app_client, token)
    member_b = await _member(app_client, token)
    verdict = await _verdict(app_client, device, token)
    await _scan_decide(app_client, token, verdict, "BUY", "v-self")
    await _scan_decide(app_client, token, verdict, "SKIP", "v-member", subject_id=member_a)
    mine = await _ok_check(app_client, device, token)
    theirs = await _ok_check(app_client, device, token, subject_id=member_a)
    nobody = await _ok_check(app_client, device, token, subject_id=member_b)
    assert mine["memory"]["current_decision"]["decision"] == "BUY"
    assert theirs["memory"]["current_decision"]["decision"] == "SKIP"
    assert theirs["subject"]["household_subject_id"] == member_a
    assert nobody["memory"]["current_decision"] is None
    assert mine["decision"] == theirs["decision"] == nobody["decision"], "memory never changes the decision"


async def test_v_two_household_members_get_independent_candidate_memory(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    candidate_id = await _seed_db_candidate(account_id)
    member = await _member(app_client, token)
    for subject_id, decision in ((None, "bought"), (member, "skipped")):
        suffix = f"&subject_id={subject_id}" if subject_id else ""
        response = await app_client.post(
            f"/api/v2/shopping/candidates/{candidate_id}/decision?on=2026-08-20{suffix}",
            headers=auth(token), json={"decision": decision},
        )
        assert response.status_code == 200, response.text
    mine = await _candidate_check(app_client, token, candidate_id)
    theirs = await _candidate_check(app_client, token, candidate_id, subject_id=member)
    assert mine["memory"]["current_decision"]["decision"] == "bought"
    assert theirs["memory"]["current_decision"]["decision"] == "skipped"
    # The Decision Memory guard's own state travels verbatim, per subject.
    for subject_id, payload in ((None, mine), (member, theirs)):
        guard = (await app_client.get(
            f"/api/v2/shopping/candidates/{candidate_id}/purchase-guard",
            headers=auth(token), params={"subject_id": subject_id} if subject_id else {},
        )).json()
        assert payload["memory"]["guard_state"] == guard["guard_state"]


async def test_v_a_named_subject_without_an_account_is_refused(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    member = await _member(app_client, token)
    response = await _check(app_client, device, subject_id=member)
    assert response.status_code == 401


async def test_w_unattributed_legacy_scan_history_stays_incomplete_and_is_never_adopted(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _member(app_client, token)
    verdict = await _verdict(app_client, device, token)
    version = verdict["label_version"]
    async with get_sessionmaker()() as session:
        session.add(ScanDecisionEvent(
            account_id=account_id, household_subject_id=None, barcode=BARCODE,
            label_snapshot_id=uuid.UUID(version["id"]), label_version=version["version_number"],
            content_fingerprint=version["content_fingerprint"], decision="SKIP", idempotency_key="legacy-w",
        ))
        await session.commit()
    payload = await _ok_check(app_client, device, token)
    assert payload["memory"]["state"] == "history_incomplete"
    assert payload["memory"]["history_complete"] is False
    assert payload["memory"]["current_decision"] is None


async def test_x_scan_memory_states_only_the_stored_outcome(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    verdict = await _verdict(app_client, device, token)
    await _scan_decide(app_client, token, verdict, "WAIT", "x-wait")
    memory = (await _ok_check(app_client, device, token))["memory"]
    assert memory["fidelity"] == "user_outcome_only"
    assert set(memory["current_decision"]) == {"decision", "occurred_at"}
    assert not {"recommendation_at_decision", "recommended", "followed_recommendation"} & _keys(memory)


async def test_y_a_candidates_historical_recommendation_survives_a_policy_change(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    candidate_id = await _seed_db_candidate(account_id)
    first = await _candidate_check(app_client, token, candidate_id)
    recorded = first["decision"]["verdict"]
    response = await app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/decision?on=2026-08-20",
        headers=auth(token), json={"decision": "waiting"},
    )
    assert response.status_code == 200, response.text
    original = check_service.resolve_care_purchase_verdict

    async def new_policy(*args, **kwargs):
        verdict = await original(*args, **kwargs)
        flipped = "skip" if verdict["verdict"] != "skip" else "buy"
        return {**verdict, "verdict": flipped, "primary_reason_code": "policy_changed_in_test"}

    monkeypatch.setattr(check_service, "resolve_care_purchase_verdict", new_policy)
    later = await _candidate_check(app_client, token, candidate_id)
    assert later["decision"]["verdict"] != recorded
    assert later["memory"]["current_decision"]["recommendation_at_decision"] == recorded
    assert later["memory"]["fidelity"] == "recommendation_snapshot"


async def test_z_a_decision_on_an_older_version_is_never_presented_as_current(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    first = await _verdict(app_client, device, token)
    await _scan_decide(app_client, token, first, "BUY", "z-old")
    await _pack(app_client, device, token, account_id, "buy", ingredients_text="rolled oats, wheat")
    second = await _verdict(app_client, device, token)
    assert second["label_version"]["version_number"] != first["label_version"]["version_number"]
    memory = (await _ok_check(app_client, device, token))["memory"]
    assert memory["current_decision"] is None
    assert memory["state"] == "no_prior_exact_decision"
    assert memory["earlier_version_decision"] == {
        "decision": "BUY", "label_version": first["label_version"]["version_number"],
        "occurred_at": memory["earlier_version_decision"]["occurred_at"], "applies_to_current_version": False,
    }
    # A clean, attributed older decision leaves the barcode's history complete.
    assert memory["history_complete"] is True


# ---------------------------------------------------------------------------
# W/Z. Unattributed history on an *older* version still makes coverage incomplete
# ---------------------------------------------------------------------------
async def _ambiguous_decision_on(verdict: dict, account_id, key: str, decision: str = "SKIP") -> None:
    """A subject-less decision written after the household existed: it belongs to nobody."""
    version = verdict["label_version"]
    async with get_sessionmaker()() as session:
        session.add(ScanDecisionEvent(
            account_id=account_id, household_subject_id=None, barcode=verdict["barcode"],
            label_snapshot_id=uuid.UUID(version["id"]), label_version=version["version_number"],
            content_fingerprint=version["content_fingerprint"], decision=decision, idempotency_key=key,
        ))
        await session.commit()


async def _two_versions_with_ambiguous_older(app_client, registered_supabase_user, key: str):
    """Version 1, then a household, then an ambiguous v1 decision, then version 2 in hand."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    first = await _verdict(app_client, device, token)
    member = await _member(app_client, token)
    await _ambiguous_decision_on(first, account_id, key)
    await _pack(app_client, device, token, account_id, "buy", ingredients_text="rolled oats, wheat")
    second = await _verdict(app_client, device, token)
    assert second["label_version"]["version_number"] != first["label_version"]["version_number"]
    return token, account_id, device, member, second


async def test_w_ambiguous_history_on_an_older_version_makes_coverage_incomplete(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    token, _account_id, device, _member_id, _second = await _two_versions_with_ambiguous_older(
        app_client, registered_supabase_user, "w-older",
    )
    memory = (await _ok_check(app_client, device, token))["memory"]
    assert memory["current_decision"] is None
    assert memory["earlier_version_decision"] is None, "an ambiguous row is nobody's, never the holder's"
    assert memory["history_complete"] is False
    assert memory["state"] == "history_incomplete"


async def test_w_an_exact_current_decision_coexists_with_incomplete_older_history(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    token, _account_id, device, _member_id, second = await _two_versions_with_ambiguous_older(
        app_client, registered_supabase_user, "w-older-exact",
    )
    await _scan_decide(app_client, token, second, "WAIT", "w-current")
    memory = (await _ok_check(app_client, device, token))["memory"]
    assert memory["state"] == "prior_exact_decision"
    assert memory["current_decision"]["decision"] == "WAIT", "a known exact decision is never hidden"
    assert memory["history_complete"] is False, "and older history is still reported incomplete"
    assert memory["earlier_version_decision"] is None, "the ambiguous older row is still not adopted"


async def test_w_ambiguous_older_history_is_incomplete_for_a_named_member_too(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    token, _account_id, device, member, second = await _two_versions_with_ambiguous_older(
        app_client, registered_supabase_user, "w-older-member",
    )
    before = (await _ok_check(app_client, device, token, subject_id=member))["memory"]
    assert before["current_decision"] is None
    assert before["earlier_version_decision"] is None
    assert before["history_complete"] is False
    assert before["state"] == "history_incomplete"
    await _scan_decide(app_client, token, second, "BUY", "w-member-current", subject_id=member)
    after = (await _ok_check(app_client, device, token, subject_id=member))["memory"]
    assert after["current_decision"]["decision"] == "BUY"
    assert after["history_complete"] is False
    assert after["earlier_version_decision"] is None


async def test_w_a_pre_household_decision_on_an_older_version_is_the_holders_and_complete(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    """The other side of the boundary: before any household there was only one person."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    first = await _verdict(app_client, device, token)
    await _ambiguous_decision_on(first, account_id, "w-pre-household", decision="BUY")
    member = await _member(app_client, token)
    await _pack(app_client, device, token, account_id, "buy", ingredients_text="rolled oats, wheat")
    mine = (await _ok_check(app_client, device, token))["memory"]
    assert mine["earlier_version_decision"]["decision"] == "BUY"
    assert mine["history_complete"] is True
    theirs = (await _ok_check(app_client, device, token, subject_id=member))["memory"]
    assert theirs["earlier_version_decision"] is None, "the holder's legacy decision is not the member's"
    assert theirs["history_complete"] is True


async def test_w_barcode_coverage_is_scoped_to_the_barcode_and_the_account(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    """An ambiguous decision about another product says nothing about this one."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _pack(app_client, device, token, account_id, "buy", barcode=OTHER_BARCODE)
    await _member(app_client, token)
    other = await _verdict(app_client, device, token, barcode=OTHER_BARCODE)
    await _ambiguous_decision_on(other, account_id, "w-other-barcode")
    memory = (await _ok_check(app_client, device, token))["memory"]
    assert memory["history_complete"] is True
    assert memory["state"] == "no_prior_exact_decision"


# ---------------------------------------------------------------------------
# Override never changes engine truth; the read writes nothing
# ---------------------------------------------------------------------------
async def test_an_override_never_changes_the_scan_decision(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    before = (await _ok_check(app_client, device, token))["decision"]
    await _scan_decide(app_client, token, await _verdict(app_client, device, token), "SKIP", "override")
    after = await _ok_check(app_client, device, token)
    assert after["decision"] == before
    assert after["memory"]["current_decision"]["decision"] == "SKIP"


async def test_an_override_never_changes_the_candidate_decision(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    candidate_id = await _seed_db_candidate(account_id)
    before = (await _candidate_check(app_client, token, candidate_id))["decision"]
    opposite = "skipped" if before["verdict"] != "skip" else "bought"
    response = await app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/decision?on=2026-08-20",
        headers=auth(token), json={"decision": opposite},
    )
    assert response.status_code == 200, response.text
    assert (await _candidate_check(app_client, token, candidate_id))["decision"] == before


async def test_the_purchase_check_reads_write_nothing(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    care = await _seed_db_candidate(account_id)
    fragrance = await _fragrance_candidate(app_client, token)
    # A second product with a real comparable alternative, so the Open Food
    # Facts half of a response is exercised too: nothing of it may be stored.
    await alternative_fixtures.seed_current()
    await alternative_fixtures.seed_candidate(
        alternative_fixtures.CANDIDATE_B, product_name="Sunfield Oat Porridge",
        ingredients=alternative_fixtures.INGREDIENTS_B, panel=alternative_fixtures.PANEL_B,
    )
    await _verdict(app_client, device, token)
    before = await _table_state()
    await _ok_check(app_client, device, token)
    await _ok_check(app_client, device)
    await _ok_check(app_client, device, token, physical=False)
    with_alternative = await _ok_check(app_client, device, token, barcode=alternative_fixtures.CURRENT)
    assert with_alternative["alternative"]["status"] == "available"
    await _candidate_check(app_client, token, care)
    await _candidate_check(app_client, token, fragrance)
    assert await _table_state() == before


async def test_the_purchase_check_routes_take_no_row_or_advisory_lock(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
):
    """Every statement the two routes send is captured: none locks anything.

    A read that takes no lock cannot join any lock order, so it cannot invert
    one — against decision writes, subject removal, shelf links or watches.
    """
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    care = await _seed_db_candidate(account_id)
    fragrance = await _fragrance_candidate(app_client, token)
    member = await _member(app_client, token)
    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        await _ok_check(app_client, device, token)
        await _ok_check(app_client, device, token, subject_id=member)
        await _candidate_check(app_client, token, care)
        await _candidate_check(app_client, token, fragrance, subject_id=member)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements, "the capture must have seen the routes' queries"
    locking = [sql for sql in statements if re.search(r"FOR (UPDATE|NO KEY UPDATE|SHARE|KEY SHARE)|pg_advisory", sql, re.I)]
    assert locking == []


def test_the_operating_layer_takes_no_lock_and_calls_no_network():
    source = OS_SOURCE.read_text()
    for forbidden in ("with_for_update", "FOR UPDATE", "pg_advisory", "lock_account", "lock_label_version",
                      "httpx", "requests", "aiohttp", "urllib", "session.add", "session.commit",
                      "session.flush", "session.delete", ".execute("):
        assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# AA. No composite score
# ---------------------------------------------------------------------------
FORBIDDEN_SCORE_NAMES = ("score", "weight", "rank", "points", "rating")


async def test_aa_no_response_carries_a_score_or_weight(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    care = await _seed_db_candidate(account_id)
    payloads = [await _ok_check(app_client, device, token), await _candidate_check(app_client, token, care)]
    for payload in payloads:
        for key in _keys(payload):
            assert not any(word in key.lower() for word in FORBIDDEN_SCORE_NAMES), key


def test_aa_the_operating_layer_does_no_arithmetic():
    tree = ast.parse(OS_SOURCE.read_text())
    arithmetic = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.BinOp, ast.AugAssign))
        and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow))
    ]
    assert arithmetic == [], "decision precedence is written policy, never arithmetic"
    names = {node.id.lower() for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not any(word in name for name in names for word in FORBIDDEN_SCORE_NAMES)


# ---------------------------------------------------------------------------
# AB, AC. No commerce; no durable Store A / Store B join
# ---------------------------------------------------------------------------
COMMERCE_WORDS = ("cart", "checkout", "payment", "retailer", "affiliate", "coupon", "offer", "stock",
                  "seller", "order", "stripe", "razorpay", "marketplace", "buy_now", "deal")


def test_ab_no_commerce_import_or_route():
    tree = ast.parse(OS_SOURCE.read_text())
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    modules |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    for module in modules:
        assert not any(word in module.lower() for word in COMMERCE_WORDS), module
    from server import app

    for route in app.routes:
        path = getattr(route, "path", "")
        if "purchase-check" in path:
            assert not any(word in path.lower() for word in COMMERCE_WORDS), path


def test_ac_no_store_a_import_and_no_purchase_os_table():
    tree = ast.parse(OS_SOURCE.read_text())
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    # ``app.domains.official_records`` shares the prefix; Store A is the ``off`` package itself.
    assert not any(module.split(".")[:3] == ["app", "domains", "off"] for module in modules), modules
    tables = {table.name for table in Base.metadata.sorted_tables}
    for forbidden in ("purchase_operating", "purchase_score", "decision_snapshot", "purchase_context"):
        assert not any(forbidden in name for name in tables), forbidden


# ---------------------------------------------------------------------------
# AD. No internal identifier leaves the server
# ---------------------------------------------------------------------------
async def test_ad_no_internal_identifier_leaks(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _shelf_pack(device, account_id)
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    verdict = await _verdict(app_client, device, token)
    version = verdict["label_version"]
    await app_client.post("/api/v2/inventory/from-scan", headers={**device, **auth(token)}, json={
        "barcode": BARCODE, "label_snapshot_id": version["id"], "label_version": version["version_number"],
        "content_fingerprint": version["content_fingerprint"], "client_mutation_id": "ad-own",
    })
    await _scan_decide(app_client, token, verdict, "BUY", "ad-decide")
    care = await _seed_db_candidate(account_id)
    scan = json.dumps(await _ok_check(app_client, device, token))
    candidate = json.dumps(await _candidate_check(app_client, token, care))
    async with get_sessionmaker()() as session:
        internal = {str(account_id), version["id"]}
        internal |= {str(value) for value in (await session.execute(select(InventoryItem.id))).scalars()}
        internal |= {str(value) for value in (await session.execute(select(PurchaseDecision.id))).scalars()}
        internal |= {str(value) for value in (await session.execute(text("SELECT id FROM official_record_revisions"))).scalars()}
        internal |= {str(value) for value in (await session.execute(text("SELECT id FROM ai_runs"))).scalars()}
        internal |= {str(value) for value in (await session.execute(select(LabelSnapshot.id))).scalars()}
    for value in internal:
        assert value not in scan and value not in candidate, value
    for key in ("account_id", "candidate_id", "inventory_item_id", "label_snapshot_id", "ai_run_id",
                "storage_key", "media_asset_id", "revision_id", "selected_owned_item_id"):
        assert key not in _keys(json.loads(scan)) and key not in _keys(json.loads(candidate)), key


# ---------------------------------------------------------------------------
# AE. No customer prose outside the keyed string authority
# ---------------------------------------------------------------------------
def _prose(value: str) -> bool:
    stripped = value.strip()
    if stripped.isupper():
        return False
    return len(stripped.split()) >= 2 and (stripped[:1].isupper() or stripped.endswith((".", "!", "?")))


def _string_literals(node: ast.AST) -> list[str]:
    """String literals, minus docstrings and OpenAPI ``description=`` text (developer documentation)."""
    exempt = set()
    for inner in ast.walk(node):
        if isinstance(inner, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and inner.body:
            first = inner.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                exempt.add(id(first.value))
        if isinstance(inner, ast.keyword) and inner.arg == "description" and isinstance(inner.value, ast.Constant):
            exempt.add(id(inner.value))
    return [
        inner.value for inner in ast.walk(node)
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str) and id(inner) not in exempt
    ]


def test_ae_the_operating_layer_and_its_routes_carry_no_customer_prose():
    stray = [value for value in _string_literals(ast.parse(OS_SOURCE.read_text())) if _prose(value)]
    # The authority's proper name is a name, not a sentence.
    assert stray == ["FSSAI / FoSCoS"], stray
    for module, name in ((product_api, "read_scan_purchase_check"), (shopping_api, "get_candidate_purchase_check")):
        tree = ast.parse(Path(module.__file__).read_text())
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        )
        assert [value for value in _string_literals(function) if _prose(value)] == [], name
