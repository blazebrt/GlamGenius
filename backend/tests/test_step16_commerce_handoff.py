"""Step 16 — Trust-Preserving Commerce V1: a disclosed outbound handoff.

Against PostgreSQL 16 and the real routes. The Product Result and the Purchase
OS are built by their own code; official records by the real importer and
matcher; the comparable alternative by the real Step 6A engine. The suite
proves Commerce reads a finished decision, never takes part in one, and fails
closed everywhere it cannot honestly link.

* A — the eligibility matrix, on the pure authority
* B — the route, end to end
* C — the official-record ceiling cannot be walked around
* D — what a person chose is not an input
* E — commercial independence (structural and byte-for-byte)
* F — the outbound address (security)
* G — partner configuration
* H — telemetry
* I — privacy
* J — ODbL
* K — admin metrics
* L — nothing stored, nothing added
"""
from __future__ import annotations

import ast
import copy
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app import config
from app.api.v2 import commerce as commerce_api
from app.domains.analytics.models import AppEvent
from app.domains.commerce import analytics, handoff, metrics, partners
from app.domains.privacy import REGISTRY, Classification, deletion_service
from app.domains.purchase import operating_system as purchase_os
from app.shared.database.base import Base
from app.shared.database.sql import get_engine, get_sessionmaker
from sqlalchemy import event, func, select

from tests.conftest import alembic_head_revision, auth
from tests.test_official_records_api import (
    BARCODE,
    BRAND,
    PRODUCT,
    off_clean,  # noqa: F401 - re-exported fixture
)
from tests.test_step6a_comparable_alternative import (
    CANDIDATE_B,
    CURRENT,
    CURRENT_LABEL,
    INGREDIENTS_B,
    PANEL_B,
    confirm_label_through_api,
    no_off_network,  # noqa: F401 - re-exported fixture
    published_rules,  # noqa: F401 - re-exported fixture
    seed_candidate,
    seed_off,
)
from tests.test_step12c_product_watch import OTHER_BARCODE, _customer, _device, _ingest
from tests.test_step14_purchase_operating_system import (
    _ok_check,
    _pack,
    _scan_decide,
    _table_state,
    _verdict,
)

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
REPO = BACKEND.parent
COMMERCE_DIR = APP / "domains" / "commerce"
COMMERCE_API = APP / "api" / "v2" / "commerce.py"
TAG = "glamgenius-21"
AMAZON = partners.PARTNERS["amazon_in"]


def _active(tag: str | None = TAG) -> partners.ActivePartner:
    """An enabled partner built by hand. ``tag=None`` is exactly what ``active_partner`` refuses."""
    return partners.ActivePartner(partner=AMAZON, affiliate_tag=tag)


def _url(barcode: str, tag: str | None = TAG) -> str:
    return f"https://www.amazon.in/s?k={barcode}" + (f"&tag={tag}" if tag else "")


@pytest.fixture
def partner_on(monkeypatch):
    monkeypatch.setattr(config, "COMMERCE_PARTNER", "amazon_in")
    monkeypatch.setattr(config, "COMMERCE_AFFILIATE_TAG", TAG)


@pytest.fixture
def partner_off(monkeypatch):
    monkeypatch.setattr(config, "COMMERCE_PARTNER", "")
    monkeypatch.setattr(config, "COMMERCE_AFFILIATE_TAG", "")


async def _handoff(app_client, device, *, physical=True, barcode=BARCODE) -> dict:
    params = {} if physical else {"physical_pack_context": "false"}
    response = await app_client.get(
        f"/api/v2/scan/verdict/{barcode}/commerce-handoff", headers=device, params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _check(
    *,
    verdict: str | None = "buy",
    state: str = "decided",
    reference_view: bool = False,
    physical: bool = True,
    identity_state: str = "exact",
    barcode: str = BARCODE,
    alternative: dict | None = None,
    kind: str = "scan",
    strategy: str = "scan_product",
    contract: str = purchase_os.PURCHASE_OS_CONTRACT_VERSION,
    boundary: dict | None = None,
    reason: str = "label_facts",
    authority: str = "product_result",
    memory: dict | None = None,
    ownership: dict | None = None,
) -> dict:
    """A finished Purchase OS answer, in exactly the Step 14 envelope shape."""
    return {
        "contract_version": contract,
        "context": {"kind": kind, "strategy": strategy, "category": "packaged_food"},
        "subject": None,
        "identity": {
            "state": identity_state, "barcode": barcode, "label_version": 1, "content_fingerprint": "f" * 64,
            "physical_pack_context": physical, "reference_view": reference_view,
        },
        "decision": {
            "state": state, "verdict": verdict if state == "decided" else None,
            "primary_reason_code": reason, "primary_reason_authority": authority,
            "decision_fingerprint": "d" * 64,
        },
        "authorities": [],
        "memory": memory,
        "ownership": ownership,
        "alternative": alternative if alternative is not None else {
            "status": "not_enough_information", "reason_key": "no_comparable_candidate_in_cached_data",
            "candidate": None,
        },
        "value": {"state": "missing", "missing": ["current_price"]},
        "boundary": boundary,
        "missing_information": [],
    }


def _alternative(barcode: str = OTHER_BARCODE, decision: str = "buy", status: str = "available") -> dict:
    return {"status": status, "reason_key": "comparable_option_found", "candidate": {
        "barcode": barcode, "product_name": "Other Oats", "brand": "Other", "grade": "A",
        "decision": decision, "attribution": {"source": "Open Food Facts"},
    }}


# ===========================================================================
# A — the eligibility matrix
# ===========================================================================
def test_a_buy_with_exact_physical_identity_targets_the_current_product():
    answer = handoff.build_handoff(_check(verdict="buy"), _active())
    assert answer == {
        "contract_version": "commerce-handoff-v1", "state": "available", "target": "current_product",
        "reason_code": "decided_buy", "decision": "buy",
        "identity": {"barcode": BARCODE, "label_version": 1, "content_fingerprint": "f" * 64},
        "target_barcode": BARCODE,
        "partner": {"key": "amazon_in", "display_name": "Amazon.in", "url": _url(BARCODE), "affiliate": True},
        "pack_notice": "rescan_received_pack",
    }


@pytest.mark.parametrize("verdict", ["wait", "skip"])
def test_a_wait_and_skip_never_link_the_current_product(verdict):
    answer = handoff.build_handoff(_check(verdict=verdict), _active())
    assert answer["state"] == "not_applicable"
    assert answer["reason_code"] == "no_eligible_alternative"
    assert answer["target"] is None and answer["partner"] is None and answer["target_barcode"] is None


@pytest.mark.parametrize("verdict", ["wait", "skip"])
def test_a_wait_and_skip_may_link_only_the_canonical_alternative(verdict):
    answer = handoff.build_handoff(_check(verdict=verdict, alternative=_alternative()), _active())
    assert answer["state"] == "available"
    assert answer["target"] == "alternative" and answer["reason_code"] == "canonical_alternative"
    assert answer["decision"] == verdict
    assert answer["target_barcode"] == OTHER_BARCODE
    assert answer["partner"]["url"] == _url(OTHER_BARCODE)
    assert BARCODE not in answer["partner"]["url"]


@pytest.mark.parametrize("own_decision", ["wait", "skip", None, "BUY", ""])
def test_a_an_alternative_is_linked_only_when_its_own_decision_is_buy(own_decision):
    answer = handoff.build_handoff(_check(verdict="skip", alternative=_alternative(decision=own_decision)), _active())
    assert (answer["state"], answer["reason_code"]) == ("not_applicable", "no_eligible_alternative")


@pytest.mark.parametrize("alternative", [
    _alternative(status="not_enough_information"),
    _alternative(barcode="8901000000004"),        # check digit wrong
    _alternative(barcode="89010580002"),          # 11 digits
    _alternative(barcode="8901058000214 "),
    _alternative(barcode="８９０１０５８０００２１４"),  # full-width digits
    _alternative(barcode=BARCODE),                # the scanned pack itself
    {"status": "available", "reason_key": "x", "candidate": None},
    {"status": "available", "reason_key": "x", "candidate": [_alternative()["candidate"]]},
    None,
])
def test_a_an_unusable_alternative_is_never_linked(alternative):
    check = _check(verdict="wait")
    check["alternative"] = alternative
    answer = handoff.build_handoff(check, _active())
    assert (answer["state"], answer["target"]) == ("not_applicable", None)


@pytest.mark.parametrize(("state", "reason"), [
    ("not_enough_information", "identity_insufficient"),
    ("not_enough_information", "not_enough_label_facts"),
    ("prohibited", "supplement_purchase_prohibited"),
    ("unsupported", "unsupported_strategy"),
])
def test_a_a_truth_state_never_gets_a_link(state, reason):
    answer = handoff.build_handoff(
        _check(state=state, verdict=None, reason=reason, alternative=_alternative()), _active(),
    )
    assert (answer["state"], answer["reason_code"], answer["target"]) == ("not_applicable", "decision_not_made", None)


@pytest.mark.parametrize(("changes", "reason"), [
    ({"reference_view": True}, "reference_view"),
    ({"physical": False}, "physical_pack_required"),
    ({"identity_state": "insufficient"}, "identity_insufficient"),
    ({"kind": "candidate", "strategy": "care_purchase"}, "unsupported_context"),
    ({"strategy": "supplement_purchase"}, "unsupported_context"),
    ({"contract": "step-14-v0"}, "unsupported_context"),
    ({"boundary": {"code": "supplement_purchase_prohibited"}}, "unsupported_context"),
    ({"barcode": "8901058000192"}, "barcode_unusable"),
    ({"barcode": "ABC1234567890"}, "barcode_unusable"),
])
def test_a_every_missing_condition_fails_closed(changes, reason):
    answer = handoff.build_handoff(_check(verdict="buy", **changes), _active())
    assert (answer["state"], answer["reason_code"]) == ("not_applicable", reason)
    assert answer["partner"] is None and answer["pack_notice"] is None


def test_a_no_partner_means_nothing_is_even_considered():
    for check in (_check(verdict="buy"), _check(verdict="wait", alternative=_alternative())):
        assert handoff.build_handoff(check, None) == {
            "contract_version": "commerce-handoff-v1", "state": "unavailable", "target": None,
            "reason_code": "partner_not_configured", "decision": None, "identity": None,
            "target_barcode": None, "partner": None, "pack_notice": None,
        }


def test_a_an_address_the_registry_would_not_build_is_never_returned(monkeypatch):
    def forged(active, barcode):
        raise partners.UnsafeDestination("host_not_registered")

    monkeypatch.setattr(partners, "search_url", forged)
    answer = handoff.build_handoff(_check(verdict="buy"), _active())
    assert (answer["state"], answer["reason_code"], answer["partner"]) == ("unavailable", "unsafe_destination", None)


def test_a_there_is_never_more_than_one_destination():
    answer = handoff.build_handoff(_check(verdict="buy", alternative=_alternative()), _active())
    assert isinstance(answer["partner"], dict) and isinstance(answer["partner"]["url"], str)
    assert answer["target"] == "current_product", "BUY links the product in hand, never an alternative too"


def test_a_the_decision_is_read_never_changed():
    check = _check(verdict="skip", alternative=_alternative())
    before = copy.deepcopy(check)
    handoff.build_handoff(check, _active())
    assert check == before


# ===========================================================================
# B — the route, end to end
# ===========================================================================
async def test_b_a_buy_pack_in_hand_gets_one_disclosed_search_link(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, partner_on,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    check = await _ok_check(app_client, device, token)
    answer = await _handoff(app_client, device)
    assert answer["state"] == "available" and answer["target"] == "current_product"
    assert answer["decision"] == check["decision"]["verdict"] == "buy"
    assert answer["identity"] == {
        "barcode": BARCODE, "label_version": check["identity"]["label_version"],
        "content_fingerprint": check["identity"]["content_fingerprint"],
    }
    assert answer["partner"] == {"key": "amazon_in", "display_name": "Amazon.in", "url": _url(BARCODE), "affiliate": True}
    assert answer["pack_notice"] == "rescan_received_pack"


@pytest.mark.parametrize("kind", ["wait", "skip"])
async def test_b_a_wait_or_skip_pack_without_an_alternative_gets_nothing(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, partner_on, kind,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, kind)
    answer = await _handoff(app_client, device)
    assert (answer["state"], answer["reason_code"], answer["decision"]) == ("not_applicable", "no_eligible_alternative", kind)
    assert answer["partner"] is None


async def test_b_a_wait_pack_links_the_one_canonical_alternative(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, partner_on,  # noqa: F811
):
    """The Step 6A engine chose the candidate; Commerce links exactly that one."""
    await seed_off(CURRENT, name="Northstar Corn Flakes", brands="Northstar")
    await seed_candidate(CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B, panel=PANEL_B)
    token, account_id = await registered_supabase_user()
    device = await _device(app_client)
    await confirm_label_through_api(app_client, device, token, account_id, CURRENT, CURRENT_LABEL)
    verdict = await _verdict(app_client, device, barcode=CURRENT)
    assert verdict["physical_pack_context"] is True
    assert verdict["decision"]["action"] in ("wait", "skip")
    assert verdict["alternative"]["candidate"]["barcode"] == CANDIDATE_B
    assert verdict["alternative"]["candidate"]["decision"] == "buy"
    answer = await _handoff(app_client, device, barcode=CURRENT)
    assert answer["state"] == "available" and answer["target"] == "alternative"
    assert answer["target_barcode"] == CANDIDATE_B
    assert answer["partner"]["url"] == _url(CANDIDATE_B)
    # Nothing about the alternative beyond its barcode, which the card already shows.
    assert "Sunfield" not in json.dumps(answer)


async def test_b_commerce_off_reads_nothing_at_all(app_client, db_clean, monkeypatch, partner_off):
    """Off means off: no device lookup, no Product Result, no Purchase OS, no SQL at all.

    Then, with a partner on, the same route goes back through the Product
    Result's own device authority, with its own ``401 DEVICE_UNKNOWN``.
    """
    from app.domains.product import devices

    def refuse(name):
        async def _refuse(*_args, **_kwargs):
            raise AssertionError(f"{name} must not run while Commerce is off")
        return _refuse

    monkeypatch.setattr(commerce_api, "current_device", refuse("current_device"))
    monkeypatch.setattr(devices, "resolve", refuse("devices.resolve"))
    monkeypatch.setattr(commerce_api, "read_product_verdict", refuse("read_product_verdict"))
    monkeypatch.setattr(purchase_os, "scan_purchase_check", refuse("scan_purchase_check"))
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_rest):
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        for headers in ({}, {"X-Device-Token": "not-a-real-device-token"}):
            response = await app_client.get(f"/api/v2/scan/verdict/{BARCODE}/commerce-handoff", headers=headers)
            assert response.status_code == 200, response.text
            assert response.json() == handoff.partner_not_configured()
            assert (response.json()["state"], response.json()["reason_code"]) == ("unavailable", "partner_not_configured")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert statements == [], "Commerce off read the database"

    # Partner on: the real authority, and the same refusal as the Product Result.
    monkeypatch.undo()
    monkeypatch.setattr(config, "COMMERCE_PARTNER", "amazon_in")
    monkeypatch.setattr(config, "COMMERCE_AFFILIATE_TAG", TAG)
    calls: list[str | None] = []
    real = commerce_api.current_device

    async def spy(*, x_device_token, session):
        calls.append(x_device_token)
        return await real(x_device_token=x_device_token, session=session)

    monkeypatch.setattr(commerce_api, "current_device", spy)
    for headers in ({}, {"X-Device-Token": "not-a-real-device-token"}):
        response = await app_client.get(f"/api/v2/scan/verdict/{BARCODE}/commerce-handoff", headers=headers)
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "DEVICE_UNKNOWN"
        product = await app_client.get(f"/api/v2/scan/verdict/{BARCODE}", headers=headers)
        assert (product.status_code, product.json()["detail"]) == (401, response.json()["detail"])
    assert calls == [None, "not-a-real-device-token"]


def test_b_the_route_resolves_no_device_before_it_knows_commerce_is_on():
    """``Depends(current_device)`` would run before the body; the route takes the raw header."""
    tree = ast.parse(COMMERCE_API.read_text())
    route = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
                 and node.name == "read_commerce_handoff")
    defaults = [ast.unparse(default) for default in route.args.defaults]
    assert not any("current_device" in default for default in defaults), defaults
    body = [ast.unparse(statement) for statement in route.body[1:]]  # after the docstring
    assert body[0] == "active = partners.active_partner()"
    assert body[1].startswith("if active is None:") and "partner_not_configured" in body[1]
    assert body[2] == "device = await current_device(x_device_token=x_device_token, session=session)"


async def test_b_the_same_device_authority_as_the_product_result(app_client, db_clean, partner_on):
    for headers in ({}, {"X-Device-Token": "not-a-real-device-token"}):
        response = await app_client.get(f"/api/v2/scan/verdict/{BARCODE}/commerce-handoff", headers=headers)
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "DEVICE_UNKNOWN"
    device = await _device(app_client)
    response = await app_client.get("/api/v2/scan/verdict/12345/commerce-handoff", headers=device)
    assert response.status_code == 422, "the barcode path keeps the Product Result's own bounds"


async def test_b_a_reference_view_never_links(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, partner_on,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    answer = await _handoff(app_client, device, physical=False)
    assert (answer["state"], answer["reason_code"]) == ("not_applicable", "reference_view")


async def test_b_another_device_without_the_capture_never_links(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, partner_on,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    stranger = await _device(app_client)
    answer = await _handoff(app_client, stranger)
    assert (answer["state"], answer["reason_code"]) == ("not_applicable", "physical_pack_required")


async def test_b_no_exact_version_never_links(
    app_client, db_clean, off_clean, published_rules, no_off_network, partner_on,  # noqa: F811
):
    device = await _device(app_client)
    answer = await _handoff(app_client, device, barcode="8901058000207")
    assert (answer["state"], answer["reason_code"]) == ("not_applicable", "identity_insufficient")


async def test_b_an_ungraded_product_never_links(
    app_client, db_clean, off_clean, registered_supabase_user, no_off_network, partner_on,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    assert (await _verdict(app_client, device, token))["decision"]["action"] is None
    answer = await _handoff(app_client, device)
    assert (answer["state"], answer["reason_code"]) == ("not_applicable", "decision_not_made")


async def test_b_the_read_writes_nothing(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, partner_on,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    before = await _table_state()
    assert (await _handoff(app_client, device))["state"] == "available"
    assert await _table_state() == before


async def test_b_the_address_is_never_logged(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, partner_on,  # noqa: F811
    caplog,
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    caplog.set_level("DEBUG")
    answer = await _handoff(app_client, device)
    assert answer["state"] == "available"
    for record in caplog.records:
        text = record.getMessage()
        assert "amazon.in" not in text and TAG not in text, text


def test_b_nothing_in_commerce_writes_a_log_line_with_an_address():
    for path in [*sorted(COMMERCE_DIR.glob("*.py")), COMMERCE_API]:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
                "debug", "info", "warning", "error", "exception", "critical", "log",
            ):
                rendered = ast.unparse(node)
                assert "url" not in rendered.lower() and "partner" not in rendered.lower(), (path.name, rendered)


# ===========================================================================
# C — the official-record ceiling cannot be walked around
# ===========================================================================
async def test_c_an_official_record_ceiling_removes_the_current_product_link(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, tmp_path, partner_on,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    assert (await _handoff(app_client, device))["target"] == "current_product"
    await _ingest(tmp_path, brand=BRAND, product=PRODUCT)
    verdict = await _verdict(app_client, device, token)
    assert verdict["decision"]["action"] == "buy", "the Product Result itself still says BUY"
    check = await _ok_check(app_client, device, token)
    assert check["decision"]["verdict"] == "wait"
    assert check["decision"]["primary_reason_code"] == "official_record_matches_pack"
    answer = await _handoff(app_client, device)
    assert answer["decision"] == "wait"
    assert answer["target"] is None and answer["partner"] is None
    assert answer["reason_code"] == "no_eligible_alternative"


def test_c_under_the_ceiling_only_an_eligible_alternative_may_be_linked():
    ceiling = _check(verdict="wait", reason="official_record_matches_pack", authority="official_records")
    assert handoff.build_handoff(ceiling, _active())["target"] is None
    ceiling["alternative"] = _alternative()
    answer = handoff.build_handoff(ceiling, _active())
    assert answer["target"] == "alternative" and answer["target_barcode"] == OTHER_BARCODE
    assert BARCODE not in answer["partner"]["url"]


# ===========================================================================
# D — what a person chose is not an input
# ===========================================================================
async def test_d_a_recorded_buy_after_skip_does_not_unlock_the_skipped_product(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, partner_on,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "skip")
    await _scan_decide(app_client, token, await _verdict(app_client, device, token), "BUY", "user-override")
    memory = await _ok_check(app_client, device, token)
    assert memory["memory"]["current_decision"]["decision"] == "BUY"
    assert memory["decision"]["verdict"] == "skip"
    answer = await _handoff(app_client, device)
    assert (answer["decision"], answer["target"], answer["partner"]) == ("skip", None, None)


async def test_d_a_historical_buy_does_not_unlock_a_later_skip_version(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, partner_on,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")
    await _scan_decide(app_client, token, await _verdict(app_client, device, token), "BUY", "earlier-buy")
    await _pack(app_client, device, token, account_id, "skip")
    answer = await _handoff(app_client, device)
    assert (answer["decision"], answer["target"]) == ("skip", None)


def test_d_memory_and_ownership_in_the_answer_change_nothing():
    unlocked = _check(
        verdict="skip",
        memory={"state": "prior_exact_decision", "current_decision": {"decision": "BUY"}},
        ownership={"state": "owned_exact_version"},
    )
    assert handoff.build_handoff(unlocked, _active())["target"] is None


def test_d_the_route_asks_for_the_canonical_answer_with_no_person_at_all():
    tree = ast.parse(COMMERCE_API.read_text())
    route = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
                 and node.name == "read_commerce_handoff")
    arguments = {arg.arg for arg in route.args.args}
    assert arguments == {"barcode", "physical_pack_context", "x_device_token", "session"}, "no account, no subject"
    call = next(node for node in ast.walk(route) if isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "scan_purchase_check")
    keywords = {kw.arg: ast.unparse(kw.value) for kw in call.keywords}
    assert keywords["principal_account_id"] == "None" and keywords["decision_subject"] == "None"
    source = COMMERCE_API.read_text() + "".join(p.read_text() for p in COMMERCE_DIR.glob("*.py"))
    for forbidden in ("scan_memory", "decision_memory", "scan_ownership", "InventoryItem", "ScanDecisionEvent"):
        assert forbidden not in source, forbidden


# ===========================================================================
# E — commercial independence
# ===========================================================================
def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            modules.add(base)
            modules |= {f"{base}.{alias.name}" for alias in node.names}
        elif isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
    return modules


def test_e_no_authority_imports_commerce():
    """Product Truth, evidence, official records, alternatives, Purchase OS, memory,
    shelf, Product Watch, Care, Fragrance, growth — nothing reaches Commerce.

    The only files that name it are its own package, its route module, the
    router that mounts it, and the start-up configuration check.
    """
    allowed = {
        *COMMERCE_DIR.glob("*.py"), COMMERCE_API,
        APP / "api" / "v2" / "__init__.py", APP / "config.py",
    }
    offenders = []
    for path in APP.rglob("*.py"):
        if path in allowed:
            continue
        for module in _imports(path):
            if "commerce" in module:
                offenders.append((str(path.relative_to(BACKEND)), module))
    assert offenders == []


@pytest.mark.parametrize("package", [
    "nutrition", "evidence", "official_records", "alternatives", "purchase", "product", "off", "value",
    "recommendation", "routines", "supplements", "care", "inventory", "growth", "planning", "family",
])
def test_e_each_authority_package_is_commerce_free(package):
    root = APP / "domains" / package
    assert root.is_dir(), package
    for path in root.rglob("*.py"):
        assert not any("commerce" in module for module in _imports(path)), path
        assert "COMMERCE_" not in path.read_text(), path


def test_e_the_config_names_commerce_only_to_refuse_a_bad_configuration():
    source = (APP / "config.py").read_text()
    lines = [line for line in source.splitlines() if "domains.commerce" in line]
    assert lines == [
        "    from app.domains.commerce.partners import configuration_errors as commerce_configuration_errors",
    ]


def test_e_commerce_receives_a_completed_decision_and_reads_no_decision_internals():
    handoff_imports = _imports(COMMERCE_DIR / "handoff.py")
    assert {m for m in handoff_imports if m.startswith("app.")} == {
        "app.domains.alternatives.policy", "app.domains.alternatives.policy.STATUS_AVAILABLE",
        "app.domains.commerce", "app.domains.commerce.partners",
        "app.domains.purchase.operating_system",
        "app.domains.purchase.operating_system.PURCHASE_OS_CONTRACT_VERSION",
        "app.domains.purchase.operating_system.STATE_DECIDED",
    }
    for path in COMMERCE_DIR.glob("*.py"):
        for module in _imports(path):
            for banned in ("grading", "evidence", "official_records", "alternatives.service", "scan_memory",
                           "decision_memory", "ai_gateway", "gemini", "off", "growth", "referral"):
                assert banned not in module.split("."), (path.name, module)
    tree = ast.parse((COMMERCE_DIR / "handoff.py").read_text())
    build = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "build_handoff")
    assert [arg.arg for arg in build.args.args] == ["purchase_check", "active"]


def test_e_growth_and_commerce_are_separate_contracts():
    growth_dir = APP / "domains" / "growth"
    for path in [*growth_dir.glob("*.py"), APP / "api" / "v2" / "growth.py"]:
        assert not any("commerce" in module for module in _imports(path)), path
    # The route reuses the shared app_events pruner and nothing else from growth.
    growth_imports = {m for m in _imports(COMMERCE_API) if "growth" in m}
    assert growth_imports == {"app.domains.growth.analytics", "app.domains.growth.analytics.prune_opportunistically"}
    for path in COMMERCE_DIR.glob("*.py"):
        assert not any("growth" in module for module in _imports(path)), path
    assert "commerce." not in json.dumps(__import__("app.domains.growth.analytics", fromlist=["x"]).EVENT_SCHEMAS)


def test_e_the_decision_fingerprint_holds_no_partner_or_money_field():
    tree = ast.parse(Path(purchase_os.__file__).read_text())
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_fingerprint":
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    keys |= {key.value for key in arg.keys if isinstance(key, ast.Constant)}
    assert keys, "the scan fingerprint material was found"
    for key in keys:
        assert not re.search(r"partner|affiliate|commission|payout|merchant|price|url|commerce", key), key
    assert not re.search(r"partner|affiliate|commission|payout|merchant", Path(purchase_os.__file__).read_text(), re.I)


async def test_e_changing_the_affiliate_configuration_changes_no_byte_of_product_truth(
    app_client, db_clean, off_clean, registered_supabase_user, published_rules, no_off_network, monkeypatch,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _pack(app_client, device, token, account_id, "buy")

    async def truth() -> tuple[str, str, str]:
        verdict = await app_client.get(f"/api/v2/scan/verdict/{BARCODE}", headers=device)
        check = await app_client.get(f"/api/v2/scan/verdict/{BARCODE}/purchase-check", headers=device)
        signed = await app_client.get(
            f"/api/v2/scan/verdict/{BARCODE}/purchase-check", headers={**device, **auth(token)},
        )
        assert verdict.status_code == check.status_code == signed.status_code == 200
        return verdict.text, check.text, signed.text

    observed = []
    for partner_key, tag, state in (
        ("", "", "unavailable"),
        ("amazon_in", "", "unavailable"),  # malformed: the tag is required
        ("amazon_in", "partner-a-21", "available"),
        ("amazon_in", "other-tag-21", "available"),
    ):
        monkeypatch.setattr(config, "COMMERCE_PARTNER", partner_key)
        monkeypatch.setattr(config, "COMMERCE_AFFILIATE_TAG", tag)
        observed.append(await truth())
        handoff_answer = await _handoff(app_client, device)
        assert handoff_answer["state"] == state
        if state == "available":
            assert handoff_answer["partner"]["url"].endswith(f"&tag={tag}")
    assert all(item == observed[0] for item in observed), "Product Truth must be byte-identical"


# ===========================================================================
# F — the outbound address
# ===========================================================================
@pytest.mark.parametrize("url", [
    "http://www.amazon.in/s?k=8901058000191&tag=glamgenius-21",
    "javascript:alert(1)",
    "HTTPS://www.amazon.in/s?k=8901058000191&tag=glamgenius-21",
    "https://WWW.AMAZON.IN/s?k=8901058000191&tag=glamgenius-21",
    "https://user:pass@www.amazon.in/s?k=8901058000191&tag=glamgenius-21",
    "https://www.amazon.in@evil.example/s?k=8901058000191&tag=glamgenius-21",
    "https://www.amazon.in.evil.example/s?k=8901058000191&tag=glamgenius-21",
    "https://evil.example/www.amazon.in/s?k=8901058000191&tag=glamgenius-21",
    "https://amazon.in/s?k=8901058000191&tag=glamgenius-21",
    "https://m.amazon.in/s?k=8901058000191&tag=glamgenius-21",
    "https://www.amazon.in:443/s?k=8901058000191&tag=glamgenius-21",
    "https://www.amazon.in:8443/s?k=8901058000191&tag=glamgenius-21",
    "https://www.amazon.in/s?k=8901058000191&tag=glamgenius-21#fragment",
    "https://www.amazon.in/s?k=8901058000191&tag=glamgenius-21&redirect=https://evil.example",
    "https://www.amazon.in/s?k=8901058000191&tag=glamgenius-21&ref=account-123",
    "https://www.amazon.in/s?tag=glamgenius-21&k=8901058000191",
    "https://www.amazon.in/s?k=8901058000191&k=8901058000214&tag=glamgenius-21",
    "https://www.amazon.in/s?k=%38901058000191&tag=glamgenius-21",
    "https://www.amazon.in/s?k=8901058000191%26x%3D1&tag=glamgenius-21",
    "https://www.amazon.in/s?k=8901058000191&tag=glamgenius-21%0d%0aSet-Cookie:x",
    "https://www.amazon.in/s?k=8901058000191&tag=glamgenius-21\r\nSet-Cookie:x",
    "https://www.amazon.in/s?k=8901058000191 &tag=glamgenius-21",
    "https://www.amazon.in\\@evil.example/s?k=8901058000191&tag=glamgenius-21",
    "https://www.amazon.in/s\\?k=8901058000191&tag=glamgenius-21",
    "https://www.amazon.in/gp/redirect?k=8901058000191&tag=glamgenius-21",
    "https://www.amazon.in/s/?k=8901058000191&tag=glamgenius-21",
    "https://www.amazon.in/s?k=8901058000191&tag=glamgenius-21‮",
    "https://www.аmazon.in/s?k=8901058000191&tag=glamgenius-21",  # Cyrillic a
    "https://www.amazon.in/s?k=8901058000191&tag=" + "a" * 300,
    "//www.amazon.in/s?k=8901058000191&tag=glamgenius-21",
    "",
    None,
    42,
])
def test_f_only_the_exact_registry_address_is_accepted(url):
    with pytest.raises(partners.UnsafeDestination):
        partners.verify_destination(url, _active(), "8901058000191")


def test_f_the_registry_address_is_accepted_and_deterministic():
    assert partners.search_url(_active(), "8901058000191") == _url("8901058000191")
    assert partners.search_url(_active(), "8901058000191") == partners.search_url(_active(), "8901058000191")
    # Amazon requires its tag: no untagged address is ever built or accepted.
    with pytest.raises(partners.UnsafeDestination) as refused:
        partners.search_url(_active(None), "8901058000191")
    assert refused.value.code == "affiliate_tag_missing"
    for untagged in (_active(None), _active()):
        with pytest.raises(partners.UnsafeDestination):
            partners.verify_destination(_url("8901058000191", None), untagged, "8901058000191")
    assert partners.verify_destination(_url("8901058000191"), _active(), "8901058000191") == _url("8901058000191")
    # The address for one barcode never verifies for another.
    with pytest.raises(partners.UnsafeDestination):
        partners.verify_destination(_url("8901058000191"), _active(), "8901058000214")


@pytest.mark.parametrize("barcode", [
    "8901058000192", "89010580001", "123456789012345", "1234567", "00000000", "0000000000000",
    "8901058000191\n", " 8901058000191", "8901058000191&tag=x", "8901058000191%00", "٨٩٠١٠٥٨٠٠٠١٩١",
    "８９０１０５８０００１９１", "890105800019A", "8" * 64, "8" * 10_000, "", None, 8901058000191,
    "../../8901058000191", "8901058000191#", "javascript:1",
])
def test_f_a_malicious_or_unusable_barcode_never_becomes_an_address(barcode):
    assert partners.is_exact_gtin(barcode) is False
    with pytest.raises(partners.UnsafeDestination):
        partners.search_url(_active(), barcode)


@pytest.mark.parametrize("barcode", ["96385074", "036000291452", "8901058000191", "4006381333931", "10012345678902"])
def test_f_each_gtin_length_with_a_valid_check_digit_is_usable(barcode):
    assert partners.is_exact_gtin(barcode) is True
    assert partners.search_url(_active(), barcode) == _url(barcode)


def test_f_the_server_never_fetches_the_partner_page():
    for path in [*COMMERCE_DIR.glob("*.py"), COMMERCE_API]:
        for module in _imports(path):
            for banned in ("httpx", "requests", "aiohttp", "urllib.request", "socket", "http.client"):
                assert not module.startswith(banned), (path.name, module)


def test_f_no_client_supplied_destination_exists_anywhere():
    tree = ast.parse(COMMERCE_API.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            names = {arg.arg for arg in node.args.args}
            assert not names & {"url", "destination", "redirect", "next", "host", "partner", "tag"}, node.name
    fields = set(commerce_api.CommerceEventBody.model_fields)
    assert fields == {"name", "client_event_id", "properties"}


# ===========================================================================
# G — partner configuration
# ===========================================================================
def test_g_the_registry_is_a_closed_literal_table():
    assert type(partners.PARTNERS).__name__ == "mappingproxy"
    assert partners.PARTNER_KEYS == ("amazon_in",)
    assert AMAZON.host == "www.amazon.in" and AMAZON.search_path == "/s"
    fields = set(partners.Partner.__dataclass_fields__)
    assert fields == {
        "key", "display_name", "host", "search_path", "query_parameter", "affiliate_parameter",
        "affiliate_tag_required", "affiliate_tag_shape",
    }
    # V1 has no non-affiliate partner: every row requires its tag.
    assert all(partner.affiliate_tag_required for partner in partners.PARTNERS.values())
    assert AMAZON.affiliate_tag_shape is partners.AMAZON_IN_TRACKING_ID
    for banned in ("commission", "payout", "rate", "rank", "score", "priority", "weight", "conversion", "price"):
        assert not any(banned in field for field in fields), banned


VALID_CONFIGURATIONS = [
    ("", ""),                       # Commerce off
    ("amazon_in", "glamgenius-21"),
    ("AMAZON_IN", "glamgenius-21"),
    ("amazon_in", "gg0mobile-21"),
    ("amazon_in", "glam-genius-app-21"),
]
INVALID_CONFIGURATIONS = [
    ("amazon_in", ""),              # Amazon without its tag
    ("amazon_in", "random"),
    ("amazon_in", "anything"),
    ("amazon_in", "not-india-20"),  # another marketplace's suffix
    ("amazon_in", "glamgenius-22"),
    ("amazon_in", "-21"),
    ("amazon_in", "glamgenius--21x"),
    ("amazon_in", "GlamGenius-21"),
    ("amazon_in", "bad tag"),
    ("amazon_in", "bad tag-21"),
    ("amazon_in", "a&b=c-21"),
    ("amazon_in", "glamgenius-21&ref=x"),
    ("amazon_in", "glam%2Dgenius-21"),
    ("amazon_in", "-lead-21"),
    ("amazon_in", "x" * 62 + "-21"),
    ("", "orphan-21"),              # a tag without a partner
    ("unknown", "anything-21"),
    ("flipkart", ""),
    ("https://evil.example", ""),
]


@pytest.mark.parametrize(("partner_key", "tag"), VALID_CONFIGURATIONS)
def test_g_a_valid_configuration(monkeypatch, partner_key, tag):
    monkeypatch.setattr(config, "COMMERCE_PARTNER", partner_key.strip().lower())
    monkeypatch.setattr(config, "COMMERCE_AFFILIATE_TAG", tag)
    assert partners.configuration_errors(partner_key.strip().lower(), tag) == []
    active = partners.active_partner()
    if not partner_key:
        assert active is None, "both empty means Commerce off"
    else:
        assert active.partner is AMAZON and active.affiliate_tag == tag
    for environment in ("development", "test", "staging", "production"):
        monkeypatch.setattr(config, "APP_ENV", environment)
        try:
            config.validate_production_configuration()
        except RuntimeError as exc:
            assert "COMMERCE_" not in str(exc), exc


@pytest.mark.parametrize(("partner_key", "tag"), INVALID_CONFIGURATIONS)
def test_g_a_malformed_configuration_enables_nothing(monkeypatch, partner_key, tag):
    monkeypatch.setattr(config, "COMMERCE_PARTNER", partner_key.strip().lower())
    monkeypatch.setattr(config, "COMMERCE_AFFILIATE_TAG", tag)
    assert partners.configuration_errors(partner_key.strip().lower(), tag)
    assert partners.active_partner() is None


@pytest.mark.parametrize("environment", ["development", "test", "staging", "production"])
@pytest.mark.parametrize(("partner_key", "tag"), INVALID_CONFIGURATIONS)
def test_g_startup_refuses_a_malformed_configuration_in_every_environment(monkeypatch, partner_key, tag, environment):
    monkeypatch.setattr(config, "COMMERCE_PARTNER", partner_key.strip().lower())
    monkeypatch.setattr(config, "COMMERCE_AFFILIATE_TAG", tag)
    monkeypatch.setattr(config, "APP_ENV", environment)
    with pytest.raises(RuntimeError, match="COMMERCE_"):
        config.validate_production_configuration()


def test_g_amazon_cannot_activate_without_its_tag(monkeypatch):
    monkeypatch.setattr(config, "COMMERCE_PARTNER", "amazon_in")
    monkeypatch.setattr(config, "COMMERCE_AFFILIATE_TAG", "")
    assert partners.configuration_errors("amazon_in", "") == [
        "COMMERCE_AFFILIATE_TAG is required for COMMERCE_PARTNER=amazon_in.",
    ]
    assert partners.active_partner() is None
    assert handoff.build_handoff(_check(verdict="buy"), partners.active_partner()) == handoff.partner_not_configured()
    # Even a partner assembled by hand, past the configuration check, builds no untagged link.
    answer = handoff.build_handoff(_check(verdict="buy"), _active(None))
    assert (answer["state"], answer["reason_code"], answer["partner"]) == ("unavailable", "unsafe_destination", None)


@pytest.mark.parametrize("check", [
    _check(verdict="buy"),
    _check(verdict="wait", alternative=_alternative()),
    _check(verdict="skip", alternative=_alternative()),
])
@pytest.mark.parametrize("tag", ["glamgenius-21", "gg0mobile-21"])
def test_g_every_available_amazon_handoff_is_a_disclosed_affiliate_link(check, tag):
    answer = handoff.build_handoff(check, partners.ActivePartner(partner=AMAZON, affiliate_tag=tag))
    assert answer["state"] == "available"
    assert answer["partner"]["key"] == "amazon_in"
    assert answer["partner"]["affiliate"] is True
    assert answer["partner"]["url"].endswith(f"&tag={tag}")


def test_g_commerce_is_off_by_default():
    assert "COMMERCE_PARTNER=\n" in (REPO / "env.example").read_text()
    assert "COMMERCE_AFFILIATE_TAG=\n" in (REPO / "env.example").read_text()
    assert "COMMERCE" not in (REPO / "render.yaml").read_text()


def test_g_the_amazon_activation_gate_is_written_down_and_claims_no_approval():
    """Seven owner conditions before ``amazon_in`` may be set, in the doc and beside the variable."""
    doc = (REPO / "docs" / "architecture" / "COMMERCE_HANDOFF.md").read_text()
    env = (REPO / "env.example").read_text()
    gate = doc[doc.index("**Before enabling — the Amazon mobile-app activation gate.**"):doc.index("## 8.")]
    flat = re.sub(r"\s+", " ", gate.replace("**", ""))
    for condition in (
        "Mobile Application Policy", "Approved Mobile Application", "Associates Central",
        "not merely a website tracking tag", "link mechanism", "Special Link", "Search-by-GTIN",
    ):
        assert condition in flat, condition
    assert [line[:2] for line in gate.splitlines() if re.match(r"^[1-7]\. ", line)] == [
        "1.", "2.", "3.", "4.", "5.", "6.", "7.",
    ]
    assert "If any one is unresolved, `COMMERCE_PARTNER` must remain empty." in flat
    assert "Not established." in flat
    for condition in ("Mobile Application Policy", "Approved Mobile Application", "Associates Central",
                      "not merely a website tracking tag", "Special Link", "Search-by-GTIN"):
        assert condition in " ".join(line.lstrip("# ").strip() for line in env.splitlines()), condition
    assert "amazon_in MUST STAY EMPTY until ALL of these are true" in env
    for claim in (r"Amazon (has )?approved", r"approved by Amazon", r"link (format )?is approved"):
        assert not re.search(claim, doc + env, re.I), claim


def test_g_the_affiliate_parameter_never_carries_a_person():
    url = partners.search_url(_active(), BARCODE)
    query = dict(pair.split("=") for pair in url.split("?", 1)[1].split("&"))
    assert query == {"k": BARCODE, "tag": TAG}


# ===========================================================================
# H — telemetry
# ===========================================================================
OPEN = {"surface": "product_result", "target": "current_product", "decision": "buy",
        "partner": "amazon_in", "affiliate": True}


async def _event(app_client, token, properties=OPEN, client_event_id=None, name="commerce.outbound_open"):
    return await app_client.post(
        "/api/v2/commerce/events", headers=auth(token),
        json={"name": name, "client_event_id": str(client_event_id or uuid.uuid4()), "properties": properties},
    )


async def _count(*where) -> int:
    async with get_sessionmaker()() as session:
        return int(await session.scalar(select(func.count()).select_from(AppEvent).where(*where)))


async def test_h_one_open_is_one_closed_row(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    client_event_id = uuid.uuid4()
    response = await _event(app_client, token, client_event_id=client_event_id)
    assert response.status_code == 202 and response.json() == {"recorded": True}
    retry = await _event(app_client, token, client_event_id=client_event_id)
    assert retry.json() == {"recorded": False}, "a retry is not counted twice"
    async with get_sessionmaker()() as session:
        [row] = (await session.execute(select(AppEvent))).scalars().all()
    assert row.name == "commerce.outbound_open" and row.account_id == account_id
    assert row.properties == OPEN and row.client_event_id == client_event_id


@pytest.mark.parametrize("properties", [
    {**OPEN, "barcode": BARCODE},
    {**OPEN, "product_name": "Whole Oats"},
    {**OPEN, "brand": BRAND},
    {**OPEN, "url": _url(BARCODE)},
    {**OPEN, "affiliate_url": _url(BARCODE)},
    {**OPEN, "search": BARCODE},
    {**OPEN, "account_id": str(uuid.uuid4())},
    {**OPEN, "order": "1", "amount": 199},
    {k: v for k, v in OPEN.items() if k != "affiliate"},
    {**OPEN, "surface": "Whole Oats from the shelf"},
    {**OPEN, "partner": "flipkart"},
    {**OPEN, "partner": "https://evil.example"},
    {**OPEN, "affiliate": 1},
    {**OPEN, "affiliate": "true"},
    {**OPEN, "affiliate": False},
    {**OPEN, "target": "alternative", "decision": "skip", "affiliate": False},
    {**OPEN, "target": "current_product", "decision": "skip"},
    {**OPEN, "target": "current_product", "decision": "wait"},
    {**OPEN, "target": "alternative", "decision": "buy"},
    {**OPEN, "decision": "BUY"},
    {**OPEN, "target": BARCODE},
    [],
    "commerce",
])
async def test_h_anything_outside_the_closed_schema_is_refused(app_client, db_clean, registered_supabase_user, properties):
    token, _ = await registered_supabase_user()
    response = await _event(app_client, token, properties=properties)
    assert response.status_code == 422, response.text
    assert BARCODE not in response.text and "Whole Oats" not in response.text
    assert await _count() == 0


@pytest.mark.parametrize("name", ["growth.scan_again", "growth.product_result_share", "commerce.purchase", "commerce.click"])
async def test_h_only_the_one_commerce_event_is_accepted(app_client, db_clean, registered_supabase_user, name):
    token, _ = await registered_supabase_user()
    response = await _event(app_client, token, name=name, properties={"surface": "product_result"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "event_not_allowed"


async def test_h_growth_telemetry_does_not_accept_commerce_events(app_client, db_clean, registered_supabase_user):
    token, _ = await registered_supabase_user()
    response = await app_client.post(
        "/api/v2/growth/events", headers=auth(token),
        json={"name": "commerce.outbound_open", "client_event_id": str(uuid.uuid4()), "properties": OPEN},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(("target", "decision"), [("alternative", "wait"), ("alternative", "skip")])
async def test_h_alternative_opens_are_recorded(app_client, db_clean, registered_supabase_user, target, decision):
    token, _ = await registered_supabase_user()
    response = await _event(app_client, token, properties={**OPEN, "target": target, "decision": decision})
    assert response.json() == {"recorded": True}


def test_h_an_amazon_open_is_always_an_affiliate_open():
    [schema] = analytics.EVENT_SCHEMAS.values()
    assert schema["affiliate"] == (True,)
    assert analytics.validate_event("commerce.outbound_open", OPEN) == OPEN
    for refused in (False, 0, None, "false"):
        with pytest.raises(analytics.InvalidCommerceEvent):
            analytics.validate_event("commerce.outbound_open", {**OPEN, "affiliate": refused})


async def test_h_anonymous_opens_are_not_recorded(app_client, db_clean):
    device = await _device(app_client)
    response = await app_client.post(
        "/api/v2/commerce/events", headers=device,
        json={"name": "commerce.outbound_open", "client_event_id": str(uuid.uuid4()), "properties": OPEN},
    )
    assert response.status_code == 401
    assert await _count() == 0


async def test_h_a_flood_is_rate_limited(app_client, db_clean, registered_supabase_user):
    token, _ = await registered_supabase_user()
    statuses = [(await _event(app_client, token)).status_code for _ in range(31)]
    assert statuses[:30] == [202] * 30 and statuses[30] == 429


async def test_h_a_storage_failure_is_soft(app_client, db_clean, registered_supabase_user, monkeypatch):
    token, _ = await registered_supabase_user()

    async def broken(*_args, **_kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr(analytics, "record_event", broken)
    response = await _event(app_client, token)
    assert response.status_code == 202 and response.json() == {"recorded": False}


def test_h_the_schema_has_no_open_string():
    [schema] = analytics.EVENT_SCHEMAS.values()
    assert set(schema) == {"surface", "target", "decision", "partner", "affiliate"}
    for values in schema.values():
        assert isinstance(values, tuple) and values, values
    assert schema["partner"] == partners.PARTNER_KEYS


# ===========================================================================
# I — privacy
# ===========================================================================
async def test_i_the_export_carries_the_open_under_the_existing_generic_contract(
    app_client, db_clean, registered_supabase_user,
):
    from app.domains.privacy import EXPORT_SCHEMA_VERSION
    from app.domains.privacy.coverage import EXPORT_COVERAGE, coverage_drift, export_locations

    token, account_id = await registered_supabase_user()
    client_event_id = uuid.uuid4()
    await _event(app_client, token, client_event_id=client_event_id)
    other, _ = await registered_supabase_user()
    await _event(app_client, other)
    response = await app_client.get("/api/v2/privacy/export", headers=auth(token))
    assert response.status_code == 200, response.text
    export = response.json()
    assert export["schema_version"] == EXPORT_SCHEMA_VERSION == "1.6"
    [event] = export["domains"]["ai_and_ops"]["app_events"]
    assert event["name"] == "commerce.outbound_open" and event["properties"] == OPEN
    assert event["account_id"] == str(account_id)
    assert "client_event_id" not in event
    assert "commerce" not in export["domains"], "no second Commerce exporter"
    serialised = json.dumps(export)
    for secret in (str(client_event_id), BARCODE, "amazon.in/", TAG):
        assert secret not in serialised
    assert export_locations()["app_events"] == ["ai_and_ops.app_events"]
    assert EXPORT_COVERAGE["app_events"].withheld == ("client_event_id",)
    assert REGISTRY["app_events"] == Classification.INCLUDED
    assert coverage_drift() == (set(), set())
    assert len(EXPORT_COVERAGE) == 112, "Step 16 adds no exported table"


async def test_i_erasure_removes_the_accounts_commerce_telemetry_and_nobody_elses(
    app_client, db_clean, registered_supabase_user, media_root,
):
    token, account_id = await registered_supabase_user()
    await _event(app_client, token)
    other, other_id = await registered_supabase_user()
    await _event(app_client, other)
    async with get_sessionmaker()() as session:
        await deletion_service.request_deletion(session, account_id)
        await session.commit()
    async with get_sessionmaker()() as session:
        assert await deletion_service.drain_all(session) >= 1
        await session.commit()
    assert await _count(AppEvent.account_id == account_id) == 0
    assert await _count(AppEvent.account_id == other_id) == 1


def test_i_no_user_owned_table_was_added():
    for table in Base.metadata.sorted_tables:
        assert not re.search(r"commerce|merchant|partner|offer|price|order|cart|click|affiliate", table.name), table.name
    assert "app_events" in REGISTRY


# ===========================================================================
# J — ODbL
# ===========================================================================
def test_j_commerce_never_touches_store_a():
    for path in [*COMMERCE_DIR.glob("*.py"), COMMERCE_API]:
        for module in _imports(path):
            assert module.split(".")[:3] != ["app", "domains", "off"], (path.name, module)
            assert "get_off_sessionmaker" not in module


def test_j_an_open_food_facts_alternative_lends_only_its_barcode_and_only_for_the_request():
    check = _check(verdict="wait", alternative=_alternative())
    answer = handoff.build_handoff(check, _active())
    text = json.dumps(answer)
    for off_field in ("Other Oats", "Other", "Open Food Facts", "attribution", "product_name", "brand", "grade"):
        assert off_field not in text, off_field
    assert answer["target_barcode"] == OTHER_BARCODE


async def test_j_an_alternative_open_writes_no_barcode_name_or_brand(app_client, db_clean, registered_supabase_user):
    token, _ = await registered_supabase_user()
    await _event(app_client, token, properties={**OPEN, "target": "alternative", "decision": "wait"})
    async with get_sessionmaker()() as session:
        [row] = (await session.execute(select(AppEvent))).scalars().all()
    stored = json.dumps(row.properties)
    assert not re.search(r"[0-9]{8,}", stored)
    assert set(row.properties) == {"surface", "target", "decision", "partner", "affiliate"}


# ===========================================================================
# K — admin metrics
# ===========================================================================
AS_OF = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


async def _seed_open(account_id, *, at, target="current_product", decision="buy"):
    async with get_sessionmaker()() as session:
        await analytics.record_event(
            session, account_id=account_id, name="commerce.outbound_open", client_event_id=uuid.uuid4(),
            properties={**OPEN, "target": target, "decision": decision}, now=at,
        )
        await session.commit()


async def test_k_metrics_are_aggregates_with_definitions_and_no_invented_money(db_clean, registered_supabase_user):
    _, first = await registered_supabase_user()
    _, second = await registered_supabase_user()
    await _seed_open(first, at=AS_OF - timedelta(days=1))
    await _seed_open(first, at=AS_OF - timedelta(days=2), target="alternative", decision="skip")
    await _seed_open(second, at=AS_OF - timedelta(days=3), target="alternative", decision="wait")
    await _seed_open(second, at=AS_OF - timedelta(days=40))  # outside the window
    async with get_sessionmaker()() as session:
        report = await metrics.commerce_metrics(session, now=AS_OF, window_days=30)
    assert report["metrics"] == {
        "outbound_opens": 3, "accounts_opening_commerce": 2, "current_product_opens": 1, "alternative_opens": 2,
        "opens_by_partner": {"amazon_in": 3}, "opens_by_decision": {"buy": 1, "wait": 1, "skip": 1},
    }
    assert set(report["definitions"]) == set(report["metrics"])
    for banned in ("purchases", "conversion_rate", "revenue", "gross_merchandise_value", "commission",
                   "average_order_value", "return_on_ad_spend", "order_completion"):
        assert banned in report["not_reported"]
        assert banned not in report["metrics"]
    text = json.dumps(report)
    assert str(first) not in text and str(second) not in text
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text)
    for word in ("sale", "sold", "purchase", "bought", "revenue", "conversion"):
        assert word not in json.dumps(report["metrics"]).lower()
        for definition in report["definitions"].values():
            assert word not in definition.lower()


async def test_k_metrics_are_for_admins_only(app_client, db_clean, registered_supabase_user):
    token, _ = await registered_supabase_user()
    # 404, not 403: an admin surface exists only for admins.
    assert (await app_client.get("/api/v2/admin/commerce/metrics", headers=auth(token))).status_code == 404
    assert (await app_client.get("/api/v2/admin/commerce/metrics")).status_code == 401
    admin, _ = await registered_supabase_user(admin=True)
    response = await app_client.get("/api/v2/admin/commerce/metrics", headers=auth(admin))
    assert response.status_code == 200 and response.json()["metrics_version"] == "commerce-metrics-v1"
    for window in (0, 91):
        bad = await app_client.get(f"/api/v2/admin/commerce/metrics?window_days={window}", headers=auth(admin))
        assert bad.status_code == 422


# ===========================================================================
# L — nothing stored, nothing added
# ===========================================================================
def test_l_no_migration_and_the_head_is_unchanged():
    assert alembic_head_revision() == "l0m1n2o3p4"
    versions = BACKEND / "migrations" / "versions"
    assert not [path for path in versions.glob("*.py") if re.search(r"commerce|step16|affiliate", path.name, re.I)]
    for path in COMMERCE_DIR.glob("*.py"):
        source = path.read_text()
        assert "mapped_column" not in source and "__tablename__" not in source, path.name


def test_l_the_commerce_code_names_no_forbidden_mechanism():
    banned = re.compile(
        r"\b(cart|basket|wallet|upi|card_number|order_id|coupon|cashback|in_stock|availability|cheapest|"
        r"best_price|best_deal|lowest_price|sponsor|promoted|bid|ranking|push_token|send_push|advertis)\w*",
        re.I,
    )
    for path in [*COMMERCE_DIR.glob("*.py"), COMMERCE_API]:
        tree = ast.parse(path.read_text())
        identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        identifiers |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        identifiers |= {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        offenders = sorted(name for name in identifiers if banned.search(name))
        assert offenders == [], (path.name, offenders)


def test_l_commerce_reaches_no_notification_or_product_watch_code():
    for path in [*COMMERCE_DIR.glob("*.py"), COMMERCE_API]:
        for module in _imports(path):
            for banned in ("notification", "planning", "workers", "watch", "push"):
                assert banned not in module.split("."), (path.name, module)
