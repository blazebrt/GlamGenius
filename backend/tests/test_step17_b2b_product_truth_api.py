"""Step 17 — the Verified Product Truth API (B2B V1).

Against PostgreSQL 16 and the real routes. Product Truth is built by the shared
authority the consumer Product Result uses; credentials by the real issuance
route; quotas by the real SQL. The suite proves an organisation can *request*
a GlamGenius decision and can never *influence* one.

* A — the frozen ``b2b-product-truth-v1`` contract
* B — eligibility: Store B confirmed labels only, every honest refusal
* C — the ODbL wall and every collaborator B2B must never touch
* D — independence: client, quota, key, usage, Commerce cannot move the answer
* E — one Product Truth authority, shared with the consumer
* F — evidence: published only, no source no claim
* G — authentication, adversarially
* H — the credential at rest, in responses, in logs and in Sentry
* I — burst limit, daily allowance and the concurrent final unit
* J — administration and audit
* K — privacy and deletion
* L — schema and migration
* M — OpenAPI
* N — read-only, single-barcode, no AI, no personal parameters
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import copy
import hashlib
import json
import logging
import re
import socket
import types
import urllib.request
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app import config
from app.api.b2b import schemas as b2b_schemas
from app.api.v2 import product as product_api
from app.domains.ai_gateway import gateway as ai_gateway
from app.domains.alternatives import service as alternatives_service
from app.domains.audit.models import AuditEvent
from app.domains.b2b import access, credentials, quota
from app.domains.b2b import truth as b2b_truth
from app.domains.b2b.models import B2BApiClient, B2BApiKey, B2BApiUsageDaily
from app.domains.commerce import partners as commerce_partners
from app.domains.community import service as community_service
from app.domains.nutrition.grading import ProductInput
from app.domains.nutrition.grading.production_rules import (
    RULES_BY_ID,
    STATUS_PUBLISHED,
    ProductionRuleset,
    candidate_ruleset,
)
from app.domains.off import client as off_client
from app.domains.off import join as off_join
from app.domains.off import store as off_store
from app.domains.official_records import service as official_records_service
from app.domains.privacy import EXPORT_SCHEMA_VERSION, REGISTRY, Classification, deletion_service, included_tables
from app.domains.privacy.coverage import EXPORT_COVERAGE
from app.domains.product import devices, pack_context
from app.domains.product import service as product_service
from app.domains.product import truth as shared_truth
from app.domains.product.models import LabelSnapshot, ScanEvent
from app.domains.value import service as value_service
from app.shared.database.base import Base
from app.shared.database.sql import get_engine, get_sessionmaker
from app.shared.observability.logging import OAuthRedactionFilter
from app.shared.observability.sentry_privacy import scrub_event
from app.shared.security import network
from app.shared.security.rate_limit import FixedWindowLimiter
from fastapi.exceptions import ResponseValidationError
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.conftest import alembic_head_revision, alembic_revision_chain, auth
from tests.test_step6a_comparable_alternative import (
    CURRENT_LABEL,
    INGREDIENTS_D,
    PANEL_A,
    PANEL_D,
    label_facts,
    register_device,
    seed_label,
    seed_off,
)
from tests.test_step17_consumer_product_result_golden import normalise

pytestmark = pytest.mark.asyncio

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
REPO = BACKEND.parent
B2B_DOMAIN = APP / "domains" / "b2b"
B2B_API = APP / "api" / "b2b"
B2B_ADMIN = APP / "api" / "v2" / "b2b_admin.py"
MIGRATION = BACKEND / "migrations" / "versions" / "m1n2o3p4q5_step17_b2b_product_truth_api.py"
TRUTH_PATH = "/api/b2b/v1/products/{barcode}/truth"


def gtin(body: str) -> str:
    """``body`` with its GS1 check digit appended."""
    digits = [int(character) for character in body]
    total = sum(digit * (3 if position % 2 == 0 else 1) for position, digit in enumerate(reversed(digits)))
    return body + str((10 - total % 10) % 10)


GRADED = gtin("890177000001")       # CURRENT_LABEL: grade C, WAIT
BUY = gtin("890177000002")          # PANEL_A, whole grain oats: grade A, BUY
SKIP = gtin("890177000003")         # PANEL_D: SKIP
OFF_RICH = gtin("890177000004")     # Open Food Facts knows it richly; we never confirmed it
IDENTITY_ONLY = gtin("890177000005")
GHEE = gtin("890177000006")         # a culinary ingredient: NOT_GRADED
UNREADABLE = gtin("890177000007")
UNKNOWN = gtin("890177000008")
INGREDIENTS_ONLY = gtin("890177000009")

BUY_LABEL = label_facts(product_name="Sunfield Oats", brand="Sunfield", ingredients="whole grain oats", panel=PANEL_A)
SKIP_LABEL = label_facts(product_name="Choco Crunch", brand="Crunchco", ingredients=INGREDIENTS_D, panel=PANEL_D)
GHEE_LABEL = label_facts(
    product_name="Ghee", brand="Dairy Co", ingredients="ghee",
    panel={"energy_kcal": "900", "saturated_fat_g": "60", "protein_g": "0"},
)


# ---------------------------------------------------------------------------
# Rules: a published ruleset that cites claims, shared by both readers
# ---------------------------------------------------------------------------
def published_ruleset(*, unpublished: tuple[str, ...] = (), without_claims: tuple[str, ...] = ()) -> ProductionRuleset:
    """Every grading rule through its lifecycle, each citing one published claim."""
    provenance = {}
    for index, (rule_id, row) in enumerate(sorted(candidate_ruleset().provenance.items())):
        if rule_id in unpublished:
            provenance[rule_id] = row
            continue
        claims = () if rule_id in without_claims else (uuid.UUID(int=index + 1),)
        provenance[rule_id] = replace(
            row, status=STATUS_PUBLISHED, claim_ids=claims, claim_version=1 if claims else None,
        )
    return ProductionRuleset(provenance=provenance)


@dataclass
class Rules:
    current: ProductionRuleset


@pytest.fixture
def rules(monkeypatch) -> Rules:
    """One ruleset for the consumer route and the B2B API alike, swappable mid-test."""
    holder = Rules(current=published_ruleset())

    async def resolve(_session):
        return holder.current

    monkeypatch.setattr(product_api, "resolve_production_ruleset", resolve)
    monkeypatch.setattr(b2b_truth, "resolve_production_ruleset", resolve)
    return holder


@pytest.fixture(autouse=True)
def _fresh_burst_state():
    quota.reset_burst_state()
    quota.reset_auth_network_state()
    yield
    quota.reset_burst_state()
    quota.reset_auth_network_state()


@pytest.fixture
def off_network(monkeypatch) -> list[str]:
    """The consumer's own permitted OFF lookups. B2B must never add one."""
    calls: list[str] = []

    async def record(barcode: str):
        calls.append(barcode)
        return None

    monkeypatch.setattr(off_client, "fetch_product", record)
    return calls


# ---------------------------------------------------------------------------
# Helpers — every client and key goes through the real admin routes
# ---------------------------------------------------------------------------
@pytest.fixture
def admin(fake_supabase_user) -> str:
    token, _ = fake_supabase_user(admin=True)
    return token


async def new_client(
    app_client, admin_token: str, *, client_key: str = "acme-retail", rpm: int = 60, rpd: int = 1000,
    display_name: str = "Acme Retail",
) -> dict:
    response = await app_client.post(
        "/api/v2/admin/b2b/clients", headers=auth(admin_token),
        json={"client_key": client_key, "display_name": display_name,
              "requests_per_minute": rpm, "requests_per_day": rpd},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def new_key(app_client, admin_token: str, client_id: str, **body: Any) -> tuple[dict, str]:
    response = await app_client.post(
        f"/api/v2/admin/b2b/clients/{client_id}/keys", headers=auth(admin_token), json=body,
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return payload["key"], payload["api_key"]


async def client_with_key(app_client, admin_token: str, **kwargs: Any) -> tuple[dict, str]:
    client = await new_client(app_client, admin_token, **kwargs)
    _, raw = await new_key(app_client, admin_token, client["id"])
    return client, raw


async def ask(app_client, raw: str | None, barcode: str, *, params: dict | None = None, headers: dict | None = None):
    sent = {**({} if raw is None else auth(raw)), **(headers or {})}
    return await app_client.get(TRUTH_PATH.format(barcode=barcode), headers=sent, params=params or {})


async def ok(app_client, raw: str, barcode: str) -> dict:
    response = await ask(app_client, raw, barcode)
    assert response.status_code == 200, response.text
    return response.json()


def without_meta(body: dict) -> dict:
    return {key: value for key, value in body.items() if key != "meta"}


def canonical(body: dict) -> str:
    return json.dumps(without_meta(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


async def usage_row(client_id: str) -> B2BApiUsageDaily | None:
    async with get_sessionmaker()() as session:
        return (await session.execute(
            select(B2BApiUsageDaily).where(B2BApiUsageDaily.client_id == uuid.UUID(client_id))
            .order_by(B2BApiUsageDaily.usage_date.desc())
        )).scalars().first()


async def sql(statement: str, **params: Any) -> None:
    async with get_sessionmaker()() as session:
        await session.execute(text(statement), params)
        await session.commit()


async def seed_unreadable(barcode: str) -> None:
    """A snapshot whose stored facts are not an object. No write path makes one."""
    async with get_sessionmaker()() as session:
        held = ScanEvent(device_id=None, barcode=barcode, outcome="label_captured",
                         client_scan_id=uuid.uuid4().hex, label_facts={})
        session.add(held)
        await session.flush()
        session.add(LabelSnapshot(
            barcode=barcode, device_id=None, scan_event_id=held.id, facts=["not", "an", "object"],
            confidence="unverified", content_fingerprint="0" * 64, version_number=1,
            completeness="complete_for_grading",
        ))
        await session.commit()


async def seed_world() -> None:
    """Confirmed labels of every kind, plus a product only Open Food Facts knows."""
    await seed_label(GRADED, CURRENT_LABEL)
    await seed_label(BUY, BUY_LABEL)
    await seed_label(SKIP, SKIP_LABEL)
    await seed_label(GHEE, GHEE_LABEL)
    await seed_label(IDENTITY_ONLY, {"product_name": "Name Only", "brand": "Label Brand"})
    await seed_label(INGREDIENTS_ONLY, {"product_name": "No Panel", "ingredients_text": "rice, salt"})
    await seed_unreadable(UNREADABLE)
    await seed_off(OFF_RICH, name="Catalogue Rich", brands="Catalogue Brand", nutriments={
        "energy-kcal_100g": 380, "sugars_100g": 1, "saturated-fat_100g": 1.2, "salt_100g": 0.02,
        "proteins_100g": 13, "fiber_100g": 10,
    })


def _keys(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from _keys(value)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _identifiers(path: Path) -> set[str]:
    """Every name, attribute, argument and import in a module. Not its prose."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.alias):
            found.add(node.asname or node.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _b2b_sources() -> list[Path]:
    return [*sorted(B2B_DOMAIN.glob("*.py")), *sorted(B2B_API.glob("*.py"))]


# ===========================================================================
# A — the frozen b2b-product-truth-v1 contract
# ===========================================================================
TOP_LEVEL = {"contract_version", "barcode", "state", "reason", "truth", "truth_fingerprint", "meta"}
TRUTH_KEYS = {"product", "facts_provenance", "label", "confidence", "grade", "decision", "factors", "ruleset", "scope"}
NESTED_KEYS = {
    "product": {"name", "brand"},
    "label": {"version_number", "content_fingerprint", "completeness"},
    "confidence": {"level"},
    "grade": {"outcome", "grade", "band", "engine_version"},
    "decision": {"state", "verdict", "reason_key"},
    "factors": {"negatives", "positives"},
    "ruleset": {"rule_version", "fingerprint"},
    "scope": {"physical_pack_context", "official_records"},
}
FACTOR_KEYS = {"key", "label", "status", "band", "explanation", "quantity", "rule", "evidence", "sources"}
EVIDENCE_KEYS = {"status", "rule_version", "evidence_claim_ids", "evidence_claim_version"}
SOURCE_KEYS = {"name", "url", "publisher", "identifier"}
QUANTITY_KEYS = {"value", "unit", "basis"}
META_KEYS = {"request_id", "generated_at"}

#: Words no part of a B2B answer may be keyed by. Semantic, recursive, and
#: deliberately broad: a new field that names any of these is a contract change
#: that has to be argued for.
FORBIDDEN_KEY_FRAGMENTS = (
    "account", "user", "subject", "device", "commerce", "affiliate", "referral", "price", "commission",
    "prompt", "raw_ai", "ai_", "internal", "scan", "lot", "batch", "mrp", "alternative", "memory",
    "shelf", "inventory", "lens", "health", "pregnan", "breastfeed", "medic", "condition", "household",
    "for_you", "client", "quota", "api_key", "secret", "token", "hash", "trace", "finding", "detail",
    "partner", "offer", "rank", "sponsor", "override", "watch", "community", "snapshot_id", "order",
)


async def test_a_the_available_answer_is_exactly_the_frozen_contract(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    body = await ok(app_client, raw, GRADED)
    assert set(body) == TOP_LEVEL
    assert body["contract_version"] == "b2b-product-truth-v1"
    assert (body["barcode"], body["state"], body["reason"]) == (GRADED, "available", None)
    assert set(body["truth"]) == TRUTH_KEYS
    for name, expected in NESTED_KEYS.items():
        assert set(body["truth"][name]) == expected, name
    assert set(body["meta"]) == META_KEYS
    for side in ("negatives", "positives"):
        for row in body["truth"]["factors"][side]:
            assert set(row) == FACTOR_KEYS
            assert row["quantity"] is None or set(row["quantity"]) == QUANTITY_KEYS
            assert row["evidence"] is None or set(row["evidence"]) == EVIDENCE_KEYS
            assert all(set(source) == SOURCE_KEYS for source in row["sources"])
    assert body["truth"]["factors"]["negatives"], "the WAIT must be explained"


async def test_a_no_forbidden_concept_appears_anywhere_in_an_answer(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    for barcode in (GRADED, BUY, SKIP, GHEE, OFF_RICH, UNKNOWN, IDENTITY_ONLY, UNREADABLE):
        body = await ok(app_client, raw, barcode)
        for key in _keys(body):
            assert not any(fragment in key.lower() for fragment in FORBIDDEN_KEY_FRAGMENTS), (barcode, key)
        text_body = json.dumps(body)
        assert raw not in text_body and "ggb_" not in text_body
        assert "Catalogue" not in text_body, "an Open Food Facts value reached a B2B answer"


async def test_a_the_not_enough_information_answer_has_no_truth(app_client, db_clean, off_clean, admin, rules):
    _, raw = await client_with_key(app_client, admin)
    body = await ok(app_client, raw, UNKNOWN)
    assert set(body) == TOP_LEVEL
    assert (body["state"], body["reason"], body["truth"]) == ("not_enough_information", "no_confirmed_label", None)


async def test_a_the_fingerprint_covers_exactly_the_canonical_answer(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    for barcode in (GRADED, UNKNOWN):
        body = await ok(app_client, raw, barcode)
        material = {key: body[key] for key in ("contract_version", "barcode", "state", "reason", "truth")}
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert body["truth_fingerprint"] == hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        # A field hidden from the visible answer would change the recomputation.
        assert body["truth_fingerprint"] == b2b_truth.fingerprint(material)


def test_a_every_contract_model_forbids_extra_fields():
    for name in dir(b2b_schemas):
        model = getattr(b2b_schemas, name)
        if isinstance(model, type) and issubclass(model, b2b_schemas._Contract) and model is not b2b_schemas._Contract:
            assert model.model_config.get("extra") == "forbid", name
    fields = set(b2b_schemas.ProductTruthResponse.model_fields)
    assert fields == TOP_LEVEL
    assert set(b2b_schemas.Truth.model_fields) == TRUTH_KEYS
    assert set(b2b_schemas.Factor.model_fields) == FACTOR_KEYS


async def test_a_a_field_outside_the_contract_cannot_leave_the_server(app_client, db_clean, off_clean, admin, rules,
                                                                      monkeypatch):
    """Fail closed: the response model refuses an extra field rather than passing it on."""
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    real = b2b_truth.product_truth

    async def leaky(session, barcode):
        body = await real(session, barcode)
        body["truth"]["affiliate_url"] = "https://example.invalid/?tag=x"
        return body

    monkeypatch.setattr(b2b_truth, "product_truth", leaky)
    with pytest.raises(ResponseValidationError):
        await ask(app_client, raw, GRADED)


def test_a_answer_refuses_inconsistent_shapes():
    for args in (
        ("x", "available", "no_confirmed_label", None),
        ("x", "available", None, None),
        ("x", "not_enough_information", None, None),
        ("x", "not_enough_information", "no_confirmed_label", {"truth": 1}),
        ("x", "not_enough_information", "made_up_reason", None),
        ("x", "maybe", None, None),
    ):
        with pytest.raises(ValueError):
            b2b_truth.answer(*args)


# ===========================================================================
# B — eligibility: Store B confirmed labels only, every honest refusal
# ===========================================================================
@pytest.mark.parametrize(("barcode", "reason"), [
    (UNKNOWN, "no_confirmed_label"),
    (OFF_RICH, "no_confirmed_label"),
    (UNREADABLE, "label_unreadable"),
    (IDENTITY_ONLY, "label_incomplete"),
    (INGREDIENTS_ONLY, "label_incomplete"),
    (GHEE, "not_graded"),
])
async def test_b_every_unmet_requirement_is_an_honest_200(app_client, db_clean, off_clean, admin, rules, barcode, reason):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    response = await ask(app_client, raw, barcode)
    assert response.status_code == 200, "not enough information is a scientific answer, not a failure"
    body = response.json()
    assert (body["state"], body["reason"], body["truth"]) == ("not_enough_information", reason, None)


@pytest.mark.parametrize(("barcode", "grade", "verdict"), [(GRADED, "C", "wait"), (BUY, "A", "buy"), (SKIP, "D", "skip")])
async def test_b_a_complete_confirmed_label_under_published_rules_is_available(
    app_client, db_clean, off_clean, admin, rules, barcode, grade, verdict,
):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    truth = (await ok(app_client, raw, barcode))["truth"]
    assert truth["grade"]["grade"] == grade
    assert truth["decision"] == {"state": "decided", "verdict": verdict, "reason_key": truth["decision"]["reason_key"]}
    assert truth["facts_provenance"] == "confirmed_label_snapshot"
    assert truth["label"]["completeness"] == "complete_for_grading"


async def test_b_product_identity_is_the_confirmed_label_never_the_catalogue(app_client, db_clean, off_clean, admin, rules):
    await seed_label(GRADED, CURRENT_LABEL)
    await seed_off(GRADED, name="Catalogue Name", brands="Catalogue Brand")
    _, raw = await client_with_key(app_client, admin)
    truth = (await ok(app_client, raw, GRADED))["truth"]
    assert truth["product"] == {"name": "Northstar Corn Flakes", "brand": "Northstar"}


async def test_b_an_absent_name_stays_absent(app_client, db_clean, off_clean, admin, rules):
    facts = {key: value for key, value in BUY_LABEL.items() if key not in {"product_name", "brand"}}
    await seed_label(BUY, facts)
    _, raw = await client_with_key(app_client, admin)
    body = await ok(app_client, raw, BUY)
    assert body["state"] == "available"
    assert body["truth"]["product"] == {"name": None, "brand": None}, "never the barcode, never invented"


async def test_b_the_newest_label_version_is_the_one_answered(app_client, db_clean, off_clean, admin, rules):
    await seed_label(GRADED, CURRENT_LABEL)
    _, raw = await client_with_key(app_client, admin)
    first = await ok(app_client, raw, GRADED)
    await seed_label(GRADED, BUY_LABEL)
    second = await ok(app_client, raw, GRADED)
    assert first["truth"]["label"]["version_number"] == 1
    assert second["truth"]["label"]["version_number"] == 2
    assert second["truth"]["grade"]["grade"] == "A"
    assert first["truth_fingerprint"] != second["truth_fingerprint"]


async def test_b_a_newer_incomplete_capture_is_not_skipped_for_an_older_complete_one(
    app_client, db_clean, off_clean, admin, rules,
):
    await seed_label(GRADED, CURRENT_LABEL)
    await seed_label(GRADED, {"product_name": "Northstar Corn Flakes", "brand": "Northstar"})
    _, raw = await client_with_key(app_client, admin)
    body = await ok(app_client, raw, GRADED)
    assert (body["state"], body["reason"]) == ("not_enough_information", "label_incomplete")


def test_b_label_facts_insufficient_when_the_engine_cannot_grade_a_complete_snapshot():
    """The engine is the final authority; a stored completeness flag cannot override it."""
    product = ProductInput(name="Bare", ingredients=())
    graded = shared_truth.grade(product, published_ruleset())
    snapshot = types.SimpleNamespace(
        facts={"product_name": "Bare"}, version_number=1, content_fingerprint="a" * 64,
        completeness="complete_for_grading", confidence="unverified",
    )
    body = b2b_truth.project(GRADED, snapshot, graded)
    assert (body["state"], body["reason"]) == ("not_enough_information", "label_facts_insufficient")


# ===========================================================================
# C — the ODbL wall and every collaborator B2B must never touch
# ===========================================================================
class Forbidden(AssertionError):
    pass


@contextlib.contextmanager
def forbidden_collaborators(monkeypatch) -> Iterator[list[str]]:
    """Every Store A path, every pack/person layer, Commerce and the AI gateway: each one explodes."""
    calls: list[str] = []

    def sync(name):
        def explode(*_args, **_kwargs):
            calls.append(name)
            raise Forbidden(name)
        return explode

    def asynchronous(name):
        async def explode(*_args, **_kwargs):
            calls.append(name)
            raise Forbidden(name)
        return explode

    with monkeypatch.context() as patch:
        patch.setattr(off_client, "fetch_product", asynchronous("off_client.fetch_product"))
        patch.setattr(off_store, "get_off_sessionmaker", sync("off_store.get_off_sessionmaker"))
        patch.setattr(product_service, "get_off_sessionmaker", sync("product_service.get_off_sessionmaker"))
        patch.setattr(product_service, "lookup", asynchronous("product_service.lookup"))
        patch.setattr(off_join, "read_off_product", asynchronous("off_join.read_off_product"))
        patch.setattr(off_join, "read_off_product_with_age", asynchronous("off_join.read_off_product_with_age"))
        patch.setattr(alternatives_service, "comparable_alternative_envelope", asynchronous("alternative"))
        patch.setattr(value_service, "pack_mrp_value_envelope", asynchronous("value"))
        patch.setattr(official_records_service, "official_records_envelope", asynchronous("official_records"))
        patch.setattr(community_service, "community_observations_envelope", asynchronous("community"))
        patch.setattr(pack_context, "current_pack", asynchronous("pack_context"))
        patch.setattr(devices, "resolve", asynchronous("devices.resolve"))
        patch.setattr(ai_gateway, "run_structured", asynchronous("ai_gateway"))
        patch.setattr(commerce_partners, "active_partner", sync("commerce.active_partner"))
        yield calls


@contextlib.contextmanager
def statements() -> Iterator[list[str]]:
    seen: list[str] = []

    def listen(_conn, _cursor, statement, *_args):
        seen.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", listen)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", listen)


async def test_c_rich_open_food_facts_data_without_our_label_is_not_enough_information(
    app_client, db_clean, off_clean, admin, rules, monkeypatch,
):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    with forbidden_collaborators(monkeypatch) as calls, statements() as seen:
        response = await ask(app_client, raw, OFF_RICH)
    assert response.status_code == 200
    assert (response.json()["state"], response.json()["reason"]) == ("not_enough_information", "no_confirmed_label")
    assert calls == []
    assert not any("off_products" in statement for statement in seen)

    # Our own confirmed label arrives: available, from that label alone.
    await seed_label(OFF_RICH, BUY_LABEL)
    with forbidden_collaborators(monkeypatch) as calls, statements() as seen:
        response = await ask(app_client, raw, OFF_RICH)
    body = response.json()
    assert response.status_code == 200 and body["state"] == "available"
    assert body["truth"]["product"] == {"name": "Sunfield Oats", "brand": "Sunfield"}
    assert calls == []
    assert not any("off_products" in statement for statement in seen)


async def test_c_an_available_answer_reads_only_store_b_tables(app_client, db_clean, off_clean, admin, rules, monkeypatch):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    with forbidden_collaborators(monkeypatch) as calls, statements() as seen:
        assert (await ok(app_client, raw, GRADED))["state"] == "available"
    assert calls == []
    tables = {match for statement in seen for match in re.findall(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(?!SET\b)\"?(\w+)", statement)}
    assert tables <= {"b2b_api_keys", "b2b_api_clients", "b2b_api_usage_daily", "product_label_snapshots"}, tables


def test_c_no_b2b_module_reaches_store_a_or_its_derivatives():
    forbidden = (
        "app.domains.off", "app.domains.alternatives", "app.domains.value", "app.domains.official_records",
        "app.domains.community",
    )
    for path in [*_b2b_sources(), B2B_ADMIN]:
        for module in _imports(path):
            assert not module.startswith(forbidden), (path.name, module)
        source = path.read_text(encoding="utf-8")
        assert "lookup(" not in source and "open_food_facts\"" not in source, path.name


def test_c_no_b2b_table_could_hold_a_joined_or_cached_answer():
    for table in Base.metadata.sorted_tables:
        if not table.name.startswith("b2b_"):
            continue
        for column in table.columns:
            assert column.type.__class__.__name__ not in {"JSONB", "JSON", "ARRAY"}, (table.name, column.name)
            assert not re.search(r"barcode|truth|payload|response|grade|verdict|nutri|ingredient|product",
                                 column.name), (table.name, column.name)
    assert not any("cache" in table.name and "b2b" in table.name for table in Base.metadata.sorted_tables)


async def test_c_a_request_writes_nothing_but_its_usage_count(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    client, raw = await client_with_key(app_client, admin)

    async def counts() -> dict[str, int]:
        async with get_sessionmaker()() as session:
            return {
                table.name: (await session.execute(text(f'SELECT count(*) FROM "{table.name}"'))).scalar_one()
                for table in Base.metadata.sorted_tables
            }

    before = await counts()
    for barcode in (GRADED, BUY, UNKNOWN, OFF_RICH):
        await ok(app_client, raw, barcode)
    after = await counts()
    changed = {name for name in before if before[name] != after[name]}
    assert changed == {"b2b_api_usage_daily"}, changed


# ===========================================================================
# D — independence: client, quota, key, usage, Commerce cannot move the answer
# ===========================================================================
async def test_d_a_ten_a_day_pilot_and_a_hundred_thousand_a_day_enterprise_get_identical_truth(
    app_client, db_clean, off_clean, admin, rules,
):
    await seed_world()
    small, small_raw = await client_with_key(
        app_client, admin, client_key="one-rupee-pilot", rpm=10, rpd=10, display_name="One Rupee Pilot",
    )
    large, large_raw = await client_with_key(
        app_client, admin, client_key="hundred-crore-enterprise", rpm=600, rpd=100_000,
        display_name="Hundred Crore Enterprise",
    )
    for barcode in (GRADED, BUY, SKIP, GHEE, UNKNOWN, OFF_RICH):
        a = await ok(app_client, small_raw, barcode)
        b = await ok(app_client, large_raw, barcode)
        assert canonical(a) == canonical(b), barcode
        assert a["truth_fingerprint"] == b["truth_fingerprint"]
        assert a["meta"]["request_id"] != b["meta"]["request_id"]


async def test_d_nothing_operational_can_move_the_answer(app_client, db_clean, off_clean, admin, rules, monkeypatch):
    """Quota, display name, usage count, key rotation, suspension history and Commerce: all inert."""
    await seed_world()
    client, raw = await client_with_key(app_client, admin, rpm=600, rpd=50)
    reference = canonical(await ok(app_client, raw, GRADED))

    await sql("UPDATE b2b_api_clients SET requests_per_day = 49, display_name = 'Renamed' WHERE id = :id",
              id=uuid.UUID(client["id"]))
    assert canonical(await ok(app_client, raw, GRADED)) == reference
    await sql("UPDATE b2b_api_usage_daily SET request_count = 40 WHERE client_id = :id", id=uuid.UUID(client["id"]))
    assert canonical(await ok(app_client, raw, GRADED)) == reference
    _, rotated = await new_key(app_client, admin, client["id"])
    assert canonical(await ok(app_client, rotated, GRADED)) == reference
    await app_client.post(f"/api/v2/admin/b2b/clients/{client['id']}/suspend", headers=auth(admin))
    await app_client.post(f"/api/v2/admin/b2b/clients/{client['id']}/activate", headers=auth(admin))
    assert canonical(await ok(app_client, rotated, GRADED)) == reference
    monkeypatch.setattr(config, "COMMERCE_PARTNER", "amazon_in")
    monkeypatch.setattr(config, "COMMERCE_AFFILIATE_TAG", "glamgenius-21")
    assert commerce_partners.active_partner() is not None
    assert canonical(await ok(app_client, rotated, GRADED)) == reference


async def test_d_revoking_a_key_changes_access_and_never_truth(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    a, a_raw = await client_with_key(app_client, admin, client_key="client-a")
    _, b_raw = await client_with_key(app_client, admin, client_key="client-b")
    reference = canonical(await ok(app_client, b_raw, GRADED))
    a_key = (await app_client.get("/api/v2/admin/b2b/clients", headers=auth(admin))).json()["clients"][0]["keys"][0]
    revoked = await app_client.post(f"/api/v2/admin/b2b/keys/{a_key['id']}/revoke", headers=auth(admin))
    assert revoked.status_code == 200
    assert (await ask(app_client, a_raw, GRADED)).status_code == 401
    assert canonical(await ok(app_client, b_raw, GRADED)) == reference


def test_d_the_projection_cannot_see_who_is_asking():
    import inspect

    assert list(inspect.signature(b2b_truth.product_truth).parameters) == ["session", "barcode"]
    assert list(inspect.signature(b2b_truth.project).parameters) == ["barcode", "snapshot", "graded"]
    assert list(inspect.signature(shared_truth.grade).parameters) == ["product", "ruleset"]
    for identifier in _identifiers(B2B_DOMAIN / "truth.py"):
        for word in ("caller", "client", "quota", "requests_per", "key_prefix", "usage", "access", "credential"):
            assert word not in identifier.lower(), identifier
    assert not {module for module in _imports(B2B_DOMAIN / "truth.py") if module.startswith("app.domains.b2b")}


def test_d_b2b_imports_no_commerce_growth_or_personalisation():
    forbidden = (
        "app.domains.commerce", "app.domains.growth", "app.domains.personal", "app.domains.family",
        "app.domains.inventory", "app.domains.purchase", "app.domains.recommendation", "app.domains.profile",
        "app.domains.identity", "app.domains.consent", "app.domains.analytics", "app.domains.routines",
        "app.domains.care", "app.domains.ai_gateway", "app.domains.scan", "app.api.v2",
        "app.shared.security.supabase_auth", "app.shared.security.deps",
    )
    for path in _b2b_sources():
        for module in _imports(path):
            assert not module.startswith(forbidden), (path.name, module)
    # The admin surface uses the consumer admin authority and nothing commercial.
    for module in _imports(B2B_ADMIN):
        assert not module.startswith(("app.domains.commerce", "app.domains.growth", "app.domains.b2b.truth")), module


def test_d_nothing_but_the_b2b_surface_imports_b2b():
    allowed = {
        *(path.resolve() for path in _b2b_sources()),
        B2B_ADMIN.resolve(),
        (APP / "shared" / "database" / "registry.py").resolve(),
    }
    for path in APP.rglob("*.py"):
        if path.resolve() in allowed:
            continue
        for module in _imports(path):
            assert not module.startswith(("app.domains.b2b", "app.api.b2b")), (str(path), module)


def test_d_product_truth_and_its_consumers_never_mention_b2b():
    roots = [
        APP / "domains" / "nutrition", APP / "domains" / "product", APP / "domains" / "evidence",
        APP / "domains" / "purchase", APP / "domains" / "commerce", APP / "domains" / "alternatives",
        APP / "domains" / "value", APP / "domains" / "official_records",
    ]
    paths = [path for root in roots for path in root.rglob("*.py")]
    paths += [APP / "api" / "v2" / "product.py", APP / "api" / "v2" / "commerce.py"]
    for path in paths:
        for identifier in _identifiers(path):
            assert "b2b" not in identifier.lower(), (str(path), identifier)


async def test_d_b2b_activity_changes_no_consumer_answer_or_fingerprint(
    app_client, db_clean, off_clean, admin, rules, off_network,
):
    await seed_label(GRADED, CURRENT_LABEL)
    device = await register_device(app_client)

    async def consumer() -> dict:
        verdict = await app_client.get(f"/api/v2/scan/verdict/{GRADED}", headers=device)
        check = await app_client.get(f"/api/v2/scan/verdict/{GRADED}/purchase-check", headers=device)
        assert verdict.status_code == check.status_code == 200
        return normalise({"product_result": verdict.json(), "purchase_check": check.json()})

    before = await consumer()
    _, raw = await client_with_key(app_client, admin)
    for _ in range(3):
        await ok(app_client, raw, GRADED)
    after = await consumer()
    assert after == before
    assert after["purchase_check"]["decision"]["decision_fingerprint"] == before["purchase_check"]["decision"]["decision_fingerprint"]
    assert "b2b" not in json.dumps(after).lower()


async def test_d_a_consumer_scan_never_needs_or_accepts_a_b2b_key(app_client, db_clean, off_clean, admin, rules,
                                                                  off_network):
    await seed_label(GRADED, CURRENT_LABEL)
    device = await register_device(app_client)
    assert (await app_client.get(f"/api/v2/scan/verdict/{GRADED}", headers=device)).status_code == 200
    _, raw = await client_with_key(app_client, admin)
    # A B2B key is not a device and not an account.
    response = await app_client.get(f"/api/v2/scan/verdict/{GRADED}", headers=auth(raw))
    assert response.status_code == 401 and response.json()["detail"]["code"] == "DEVICE_UNKNOWN"
    response = await app_client.get(f"/api/v2/scan/verdict/{GRADED}", headers={"X-Device-Token": raw})
    assert response.status_code == 401


# ===========================================================================
# E — one Product Truth authority, shared with the consumer
# ===========================================================================
async def test_e_b2b_and_the_consumer_reference_view_agree_on_every_shared_fact(
    app_client, db_clean, off_clean, admin, rules, off_network,
):
    await seed_world()
    device = await register_device(app_client)
    _, raw = await client_with_key(app_client, admin)
    for barcode in (GRADED, BUY, SKIP):
        consumer = (await app_client.get(
            f"/api/v2/scan/verdict/{barcode}", headers=device, params={"physical_pack_context": "false"},
        )).json()
        check = (await app_client.get(
            f"/api/v2/scan/verdict/{barcode}/purchase-check", headers=device, params={"physical_pack_context": "false"},
        )).json()
        truth = (await ok(app_client, raw, barcode))["truth"]
        assert truth["grade"] == {
            "outcome": consumer["outcome"], "grade": consumer["grade"], "band": consumer["band"],
            "engine_version": consumer["engine_version"],
        }
        assert truth["decision"]["verdict"] == consumer["decision"]["action"] == check["decision"]["verdict"]
        assert truth["decision"]["reason_key"] == consumer["decision"]["reason_key"]
        assert truth["label"]["version_number"] == consumer["label_version"]["version_number"]
        assert truth["label"]["content_fingerprint"] == consumer["label_version"]["content_fingerprint"]
        assert truth["confidence"]["level"] == consumer["confidence"]["level"]
        assert [(row["key"], row["rule"], row["evidence"]) for row in truth["factors"]["negatives"]] == [
            (row["key"], row["rule"], {name: row["evidence"][name] for name in EVIDENCE_KEYS})
            for row in consumer["negatives"]
        ]


async def test_e_the_same_facts_under_the_same_rules_are_the_same_answer_every_time(
    app_client, db_clean, off_clean, admin, rules,
):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    answers = {canonical(await ok(app_client, raw, GRADED)) for _ in range(4)}
    assert len(answers) == 1


async def test_e_changing_the_published_rules_changes_the_answer_and_its_ruleset(app_client, db_clean, off_clean,
                                                                                 admin, rules):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    first = await ok(app_client, raw, BUY)
    rules.current = replace_claims(rules.current)
    second = await ok(app_client, raw, BUY)
    assert first["truth"]["grade"] == second["truth"]["grade"]
    assert first["truth"]["ruleset"]["fingerprint"] != second["truth"]["ruleset"]["fingerprint"]
    assert first["truth_fingerprint"] != second["truth_fingerprint"]


def replace_claims(ruleset: ProductionRuleset) -> ProductionRuleset:
    return ProductionRuleset(provenance={
        rule_id: replace(row, claim_ids=(uuid.uuid4(),)) for rule_id, row in ruleset.provenance.items()
    })


def test_e_both_readers_grade_only_through_the_shared_authority():
    consumer = (APP / "api" / "v2" / "product.py").read_text(encoding="utf-8")
    b2b = (B2B_DOMAIN / "truth.py").read_text(encoding="utf-8")
    assert "product_truth.grade(product, ruleset)" in consumer
    assert "shared_truth.grade(product, ruleset)" in b2b
    for source in (consumer, b2b):
        assert "grade_product(" not in source
        assert "enforce_published_required_rules(" not in source
        assert "presentation.present(" not in source
    assert "from_scan.build(" not in b2b, "B2B must never build a product from a catalogue half"
    assert "from_scan.build_confirmed_label(" in b2b


# ===========================================================================
# F — evidence: published only, no source no claim
# ===========================================================================
async def test_f_every_distributed_factor_rests_on_a_published_cited_rule(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    for barcode in (GRADED, SKIP):
        truth = (await ok(app_client, raw, barcode))["truth"]
        assert truth["factors"]["negatives"]
        for row in truth["factors"]["negatives"]:
            assert row["rule"] and row["evidence"]["status"] == "published"
            assert row["evidence"]["evidence_claim_ids"], row["key"]
            assert row["sources"] and all(source["url"].startswith("https://") for source in row["sources"])
        for row in truth["factors"]["positives"]:
            assert row["explanation"] == "declared_on_label"
            assert (row["rule"], row["evidence"], row["sources"]) == (None, None, [])


async def test_f_an_unpublished_required_rule_withholds_the_whole_answer(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    rules.current = published_ruleset(unpublished=("grade.step1.nova",))
    _, raw = await client_with_key(app_client, admin)
    body = await ok(app_client, raw, BUY)
    assert (body["state"], body["reason"], body["truth"]) == ("not_enough_information", "evidence_unpublished", None)


async def test_f_an_unpublished_optional_rule_that_fired_withholds_the_answer(app_client, db_clean, off_clean, admin,
                                                                             rules, off_network):
    """The consumer shows such a row labelled candidate; B2B distributes no candidate."""
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    device = await register_device(app_client)
    consumer = (await app_client.get(f"/api/v2/scan/verdict/{SKIP}", headers=device)).json()
    optional = next(
        row["rule"] for row in consumer["negatives"]
        if row["rule"] and not rules.current.for_rule(row["rule"]).required
    )
    spec = rules.current.for_rule(optional).rule_id
    assert not RULES_BY_ID[spec].required
    rules.current = published_ruleset(unpublished=(spec,))
    still_graded = (await app_client.get(f"/api/v2/scan/verdict/{SKIP}", headers=device)).json()
    assert still_graded["grade"] is not None, "an optional candidate does not stop the consumer grade"
    body = await ok(app_client, raw, SKIP)
    assert (body["state"], body["reason"]) == ("not_enough_information", "evidence_unpublished")


async def test_f_a_published_rule_citing_no_claim_is_not_distributed(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    fired = (await ok(app_client, raw, GRADED))["truth"]["factors"]["negatives"][0]["rule"]
    rules.current = published_ruleset(without_claims=(rules.current.for_rule(fired).rule_id,))
    body = await ok(app_client, raw, GRADED)
    assert (body["state"], body["reason"]) == ("not_enough_information", "evidence_unpublished")


@pytest.mark.parametrize("sources", [
    [],
    [{"url": ""}],
    [{"url": "   "}],
    [{"url": "/relative/source"}],
    [{"url": "javascript:alert(1)"}],
    [{"url": "https:///missing-authority"}],
    [{"url": "https://example.org/bad\npath"}],
    [{"url": "https://example.org/white space"}],
    [{"url": "https://example.org/good"}, {"url": "javascript:alert(1)"}],
])
async def test_f_a_negative_without_only_openable_sources_withholds_the_whole_answer(
    app_client, db_clean, off_clean, admin, rules, monkeypatch, sources,
):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    real_grade = shared_truth.grade

    def grade_with_bad_source(product, ruleset):
        graded = real_grade(product, ruleset)
        payload = copy.deepcopy(graded.payload)
        assert payload["negatives"] and payload["negatives"][0]["sources"]
        template = payload["negatives"][0]["sources"][0]
        payload["negatives"][0]["sources"] = [{**template, **source} for source in sources]
        return replace(graded, payload=payload)

    monkeypatch.setattr(shared_truth, "grade", grade_with_bad_source)
    body = await ok(app_client, raw, GRADED)
    assert (body["state"], body["reason"], body["truth"]) == (
        "not_enough_information", "evidence_unpublished", None,
    )


@pytest.mark.parametrize("url", ["https://example.org/source", "http://example.org/source"])
async def test_f_an_openable_source_remains_distributable_without_network(
    app_client, db_clean, off_clean, admin, rules, monkeypatch, url,
):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    real_grade = shared_truth.grade

    def grade_with_openable_source(product, ruleset):
        graded = real_grade(product, ruleset)
        payload = copy.deepcopy(graded.payload)
        assert payload["negatives"] and payload["negatives"][0]["sources"]
        payload["negatives"][0]["sources"][0]["url"] = url
        return replace(graded, payload=payload)

    monkeypatch.setattr(shared_truth, "grade", grade_with_openable_source)
    body = await ok(app_client, raw, GRADED)
    assert body["state"] == "available"
    assert body["truth"]["factors"]["negatives"][0]["sources"][0]["url"] == url


async def test_f_openable_source_check_makes_no_outbound_request(monkeypatch):
    def outbound_forbidden(*_args, **_kwargs):
        raise AssertionError("source-shape validation must not perform a network request")

    monkeypatch.setattr(socket, "getaddrinfo", outbound_forbidden)
    monkeypatch.setattr(socket, "create_connection", outbound_forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", outbound_forbidden)
    row = {
        "rule": "grade.step1.nova",
        "evidence": {"status": STATUS_PUBLISHED, "evidence_claim_ids": ["claim-id"]},
        "sources": [{"url": "https://example.org/published-evidence"}],
    }
    assert b2b_truth._rests_on_published_rule(row)


async def test_f_no_internal_reasoning_or_workflow_is_distributed(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    body = await ok(app_client, raw, SKIP)
    keys = set(_keys(body))
    assert not keys & {"trace", "detail", "finding", "order", "ingredients", "components", "nutrition",
                       "missing", "notes", "review_note", "reviewed_by", "published_by", "rejection_reason"}


# ===========================================================================
# G — authentication, adversarially
# ===========================================================================
async def _expect_generic_401(response) -> dict:
    assert response.status_code == 401, response.text
    assert response.headers.get("www-authenticate") == "Bearer"
    detail = response.json()["detail"]
    assert set(detail) == {"code", "message", "request_id"}
    assert (detail["code"], detail["message"]) == (
        "B2B_UNAUTHENTICATED", "A valid GlamGenius B2B API key is required.",
    )
    return detail


async def test_g_every_invalid_credential_gets_the_same_401(app_client, db_clean, off_clean, admin, rules,
                                                            fake_supabase_user):
    await seed_world()
    client, raw = await client_with_key(app_client, admin)
    _, prefix, secret = raw.split("_")
    consumer_token, _ = fake_supabase_user()
    expired_client, expired_raw = await client_with_key(app_client, admin, client_key="expired-co")
    await sql(
        "UPDATE b2b_api_keys SET created_at = now() - interval '2 days', expires_at = now() - interval '1 day' "
        "WHERE client_id = :id", id=uuid.UUID(expired_client["id"]),
    )
    revoked_client, revoked_raw = await client_with_key(app_client, admin, client_key="revoked-co")
    revoked_key = (await app_client.get("/api/v2/admin/b2b/clients", headers=auth(admin))).json()["clients"]
    revoked_key_id = next(c["keys"][0]["id"] for c in revoked_key if c["id"] == revoked_client["id"])
    await app_client.post(f"/api/v2/admin/b2b/keys/{revoked_key_id}/revoke", headers=auth(admin))
    suspended_client, suspended_raw = await client_with_key(app_client, admin, client_key="suspended-co")
    await app_client.post(f"/api/v2/admin/b2b/clients/{suspended_client['id']}/suspend", headers=auth(admin))

    attempts = {
        "absent": {},
        "basic scheme": {"Authorization": f"Basic {raw}"},
        "empty bearer": {"Authorization": "Bearer "},
        "random": {"Authorization": "Bearer " + uuid.uuid4().hex},
        "malformed prefix": {"Authorization": f"Bearer ggb_{prefix[:-1]}_{secret}"},
        "uppercase": {"Authorization": f"Bearer {raw.upper()}"},
        "wrong scheme word": {"Authorization": f"Bearer gga_{prefix}_{secret}"},
        "unknown prefix": {"Authorization": f"Bearer ggb_{'0' * 12}_{secret}"},
        "valid prefix wrong secret": {"Authorization": f"Bearer ggb_{prefix}_{'f' * 64}"},
        "truncated secret": {"Authorization": f"Bearer ggb_{prefix}_{secret[:-1]}"},
        "trailing junk": {"Authorization": f"Bearer {raw}x"},
        "embedded space": {"Authorization": f"Bearer {raw} extra"},
        "expired": auth(expired_raw),
        "revoked": auth(revoked_raw),
        "suspended": auth(suspended_raw),
        "consumer supabase token": auth(consumer_token),
        "consumer jwt shape": {"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"},
        "device token header only": {"X-Device-Token": raw},
    }
    for name, headers in attempts.items():
        response = await app_client.get(TRUTH_PATH.format(barcode=GRADED), headers=headers)
        await _expect_generic_401(response)
        assert name  # every case reaches the same body
    # And the genuine key still works.
    assert (await ok(app_client, raw, GRADED))["state"] == "available"


async def test_g_a_prefix_cannot_be_borrowed_from_another_client(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    _, a_raw = await client_with_key(app_client, admin, client_key="client-a")
    _, b_raw = await client_with_key(app_client, admin, client_key="client-b")
    _, a_prefix, a_secret = a_raw.split("_")
    _, b_prefix, b_secret = b_raw.split("_")
    for forged in (f"ggb_{b_prefix}_{a_secret}", f"ggb_{a_prefix}_{b_secret}"):
        await _expect_generic_401(await ask(app_client, forged, GRADED))


async def test_g_a_consumer_token_is_refused_even_for_a_registered_admin(app_client, db_clean, off_clean, admin, rules,
                                                                         registered_supabase_user):
    await seed_world()
    token, _ = await registered_supabase_user(admin=True)
    await _expect_generic_401(await ask(app_client, token, GRADED))
    await _expect_generic_401(await ask(app_client, admin, GRADED))


async def test_g_a_b2b_key_is_refused_by_every_consumer_authentication(app_client, db_clean, off_clean, admin, rules):
    _, raw = await client_with_key(app_client, admin)
    for method, path in (
        ("GET", "/api/v2/me"), ("GET", "/api/v2/admin/b2b/clients"), ("GET", "/api/v2/consent"),
        ("POST", "/api/v2/commerce/events"), ("GET", "/api/v2/privacy/export"),
    ):
        response = await app_client.request(method, path, headers=auth(raw), json={})
        assert response.status_code == 401, (path, response.status_code)


async def test_g_rotation_old_revoked_fails_new_works(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    client, old = await client_with_key(app_client, admin)
    new_view, new = await new_key(app_client, admin, client["id"])
    assert (await ok(app_client, old, GRADED))["state"] == (await ok(app_client, new, GRADED))["state"]
    listing = (await app_client.get("/api/v2/admin/b2b/clients", headers=auth(admin))).json()["clients"][0]["keys"]
    old_id = next(key["id"] for key in listing if key["id"] != new_view["id"])
    await app_client.post(f"/api/v2/admin/b2b/keys/{old_id}/revoke", headers=auth(admin))
    await _expect_generic_401(await ask(app_client, old, GRADED))
    assert (await ok(app_client, new, GRADED))["state"] == "available"


async def test_g_reactivating_a_client_restores_access(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    client, raw = await client_with_key(app_client, admin)
    await app_client.post(f"/api/v2/admin/b2b/clients/{client['id']}/suspend", headers=auth(admin))
    await _expect_generic_401(await ask(app_client, raw, GRADED))
    await app_client.post(f"/api/v2/admin/b2b/clients/{client['id']}/activate", headers=auth(admin))
    assert (await ok(app_client, raw, GRADED))["state"] == "available"


async def test_g_the_comparison_is_constant_time_whatever_was_found(app_client, db_clean, off_clean, admin, rules,
                                                                    monkeypatch):
    _, raw = await client_with_key(app_client, admin)
    _, prefix, _secret = raw.split("_")
    compared: list[tuple[str, str]] = []
    real = credentials.hmac.compare_digest

    def spy(a, b):
        compared.append((a, b))
        return real(a, b)

    monkeypatch.setattr(credentials.hmac, "compare_digest", spy)
    await ask(app_client, f"ggb_{'0' * 12}_{'a' * 64}", GRADED)     # unknown prefix
    await ask(app_client, f"ggb_{prefix}_{'a' * 64}", GRADED)        # wrong secret
    await ask(app_client, raw, GRADED)                               # right
    assert len(compared) == 3
    tree = ast.parse((B2B_DOMAIN / "credentials.py").read_text(encoding="utf-8"))
    matches = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "matches")
    assert any(isinstance(node, ast.Attribute) and node.attr == "compare_digest" for node in ast.walk(matches))
    assert not any(isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.Eq) for node in ast.walk(matches))


async def test_g_a_malformed_credential_never_reaches_the_database(app_client, db_clean, admin):
    with statements() as seen:
        await _expect_generic_401(await ask(app_client, "not-a-key", GRADED))
        await _expect_generic_401(await ask(app_client, None, GRADED))
    assert seen == []


async def test_g_valid_shaped_unknown_key_has_one_indexed_lookup_and_generic_401(app_client, db_clean):
    unknown = f"ggb_{'0' * 12}_{'a' * 64}"
    with statements() as seen:
        response = await ask(app_client, unknown, GRADED)
    await _expect_generic_401(response)
    lookups = [statement for statement in seen if "b2b_api_keys" in statement]
    assert len(lookups) == 1
    assert "key_prefix" in lookups[0]


async def test_g_anonymous_network_limit_blocks_lookup_without_a_prefix_or_status_oracle(
    app_client, db_clean, admin, monkeypatch,
):
    _, known = await client_with_key(app_client, admin)
    unknown = f"ggb_{'0' * 12}_{'a' * 64}"
    monkeypatch.setattr(quota, "AUTH_NETWORK_PER_IP_PER_MINUTE", 2)
    monkeypatch.setattr(quota, "AUTH_NETWORK_GLOBAL_PER_MINUTE", 10)
    await _expect_generic_401(await ask(app_client, unknown, GRADED))
    await _expect_generic_401(await ask(app_client, unknown, GRADED))

    async def authentication_must_not_run(*_args, **_kwargs):
        raise AssertionError("network-limited requests must not reach database authentication")

    monkeypatch.setattr(access, "authenticate", authentication_must_not_run)
    with statements() as seen:
        blocked_unknown = await ask(app_client, unknown, GRADED)
        blocked_known = await ask(app_client, known, GRADED)
        malformed = await ask(app_client, "not-a-key", GRADED)
    assert not any("b2b_api_keys" in statement for statement in seen)
    await _expect_generic_401(malformed)
    for response in (blocked_unknown, blocked_known):
        assert response.status_code == 429
        assert response.json()["detail"]["code"] == "B2B_RATE_LIMITED"
        assert 1 <= int(response.headers["Retry-After"]) <= 60
    assert blocked_unknown.json()["detail"]["message"] == blocked_known.json()["detail"]["message"]


async def test_g_global_network_limit_bounds_distinct_addresses_and_spoofed_leftmost_forwarding(
    app_client, db_clean, monkeypatch,
):
    monkeypatch.setattr(network, "TRUSTED_PROXY_HOPS", 1)
    monkeypatch.setattr(quota, "AUTH_NETWORK_PER_IP_PER_MINUTE", 2)
    monkeypatch.setattr(quota, "AUTH_NETWORK_GLOBAL_PER_MINUTE", 3)
    unknown = f"ggb_{'0' * 12}_{'a' * 64}"
    for leftmost in ("198.51.100.1", "198.51.100.2"):
        await _expect_generic_401(await ask(
            app_client, unknown, GRADED,
            headers={"X-Forwarded-For": f"{leftmost}, 203.0.113.9"},
        ))
    with statements() as seen:
        spoofed = await ask(app_client, unknown, GRADED,
                            headers={"X-Forwarded-For": "198.51.100.3, 203.0.113.9"})
    assert spoofed.status_code == 429
    assert not any("b2b_api_keys" in statement for statement in seen)
    assert set(quota._auth_network_limiter.state) == {"global", "ip:203.0.113.9"}
    # Reset only the admission buckets to isolate the second bound: distinct
    # trusted addresses cannot collectively exceed the global lookup budget.
    quota.reset_auth_network_state()
    for address in ("203.0.113.10", "203.0.113.11", "203.0.113.12"):
        await _expect_generic_401(await ask(
            app_client, unknown, GRADED,
            headers={"X-Forwarded-For": f"198.51.100.4, {address}"},
        ))
    with statements() as seen:
        globally_blocked = await ask(
            app_client, unknown, GRADED,
            headers={"X-Forwarded-For": "198.51.100.5, 203.0.113.13"},
        )
    assert globally_blocked.status_code == 429
    assert not any("b2b_api_keys" in statement for statement in seen)


async def test_g_anonymous_network_limiter_has_a_finite_table(monkeypatch):
    monkeypatch.setattr(quota, "_auth_network_limiter", FixedWindowLimiter(
        window_seconds=60, max_per_window=100, max_keys=2,
    ))
    assert quota.auth_network_retry_after("203.0.113.1") is None
    assert quota.auth_network_retry_after("203.0.113.2") is not None
    assert len(quota._auth_network_limiter.state) == 2


# ===========================================================================
# H — the credential at rest, in responses, in logs and in Sentry
# ===========================================================================
def test_h_generation_is_256_bit_random_and_well_formed():
    issued = [credentials.generate() for _ in range(50)]
    assert len({item.raw for item in issued}) == 50 and len({item.prefix for item in issued}) == 50
    for item in issued:
        scheme, prefix, secret = item.raw.split("_")
        assert scheme == "ggb" and re.fullmatch(r"[0-9a-f]{12}", prefix) and re.fullmatch(r"[0-9a-f]{64}", secret)
        assert credentials.SECRET_BYTES * 8 >= 256
        assert item.key_hash == hashlib.sha256(item.raw.encode()).hexdigest() != item.raw
        assert credentials.prefix_of(item.raw) == item.prefix
        assert item.raw not in repr(item)
    source = (B2B_DOMAIN / "credentials.py").read_text(encoding="utf-8")
    assert "secrets.token_hex" in source and "random." not in source


async def test_h_the_raw_key_is_never_stored_anywhere(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    client, raw = await client_with_key(app_client, admin)
    await ok(app_client, raw, GRADED)
    secret = raw.split("_")[2]
    async with get_sessionmaker()() as session:
        key = (await session.execute(select(B2BApiKey))).scalar_one()
        assert key.key_hash == hashlib.sha256(raw.encode()).hexdigest()
        for table in Base.metadata.sorted_tables:
            columns = [column.name for column in table.columns]
            rows = (await session.execute(text(f'SELECT * FROM "{table.name}"'))).all()
            for row in rows:
                dumped = json.dumps([str(value) for value in row])
                assert raw not in dumped and secret not in dumped, (table.name, columns)


async def test_h_the_database_refuses_a_raw_key_in_the_hash_column(app_client, db_clean, admin):
    """Only 64 lowercase hex characters fit: a whole key, or any 64 characters of one, are refused."""
    client = await new_client(app_client, admin)
    raw = credentials.generate().raw
    for value, error in ((raw, DBAPIError), (raw[:64], IntegrityError), (raw[-64:].upper(), IntegrityError)):
        async with get_sessionmaker()() as session:
            session.add(B2BApiKey(client_id=uuid.UUID(client["id"]), key_prefix="a" * 12, key_hash=value))
            with pytest.raises(error):
                await session.flush()


async def test_h_two_keys_can_never_share_a_prefix(app_client, db_clean, admin):
    client = await new_client(app_client, admin)
    async with get_sessionmaker()() as session:
        for index in range(2):
            session.add(B2BApiKey(client_id=uuid.UUID(client["id"]), key_prefix="b" * 12,
                                  key_hash=hashlib.sha256(str(index).encode()).hexdigest()))
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_h_issuance_redraws_a_colliding_prefix(app_client, db_clean, admin, monkeypatch):
    client = await new_client(app_client, admin)
    first_view, first = await new_key(app_client, admin, client["id"])
    real = credentials.generate
    draws = iter([
        credentials.IssuedCredential(prefix=first_view["key_prefix"], key_hash="c" * 64,
                                     raw=f"ggb_{first_view['key_prefix']}_{'c' * 64}"),
    ])

    def colliding():
        return next(draws, None) or real()

    monkeypatch.setattr(credentials, "generate", colliding)
    second_view, second = await new_key(app_client, admin, client["id"])
    assert second_view["key_prefix"] != first_view["key_prefix"] and second != first


async def test_h_issuance_always_returns_a_new_secret_and_only_once(app_client, db_clean, admin):
    client = await new_client(app_client, admin)
    issued = [await new_key(app_client, admin, client["id"]) for _ in range(3)]
    raws = [raw for _, raw in issued]
    assert len(set(raws)) == 3
    async with get_sessionmaker()() as session:
        hashes = {row.key_prefix: row.key_hash for row in (await session.execute(select(B2BApiKey))).scalars()}
    for view, raw in issued:
        assert hashes[view["key_prefix"]] == hashlib.sha256(raw.encode()).hexdigest()
    # Every later read names keys by id and prefix only.
    reads = [
        await app_client.get("/api/v2/admin/b2b/clients", headers=auth(admin)),
        await app_client.post(f"/api/v2/admin/b2b/keys/{issued[0][0]['id']}/revoke", headers=auth(admin)),
        await app_client.post(f"/api/v2/admin/b2b/clients/{client['id']}/suspend", headers=auth(admin)),
        await app_client.post(f"/api/v2/admin/b2b/clients/{client['id']}/activate", headers=auth(admin)),
        await app_client.get(f"/api/v2/admin/b2b/clients/{client['id']}/usage", headers=auth(admin)),
    ]
    for response in reads:
        assert response.status_code == 200, response.text
        dumped = response.text
        assert "ggb_" not in dumped and "api_key" not in dumped and "key_hash" not in dumped
        for raw in raws:
            assert raw not in dumped and raw.split("_")[2] not in dumped
        for key_hash in hashes.values():
            assert key_hash not in dumped
    for row in reads[0].json()["clients"][0]["keys"]:
        assert set(row) == {"id", "client_id", "key_prefix", "created_at", "expires_at", "revoked_at", "state"}


async def test_h_no_log_line_carries_the_key_or_the_authorization_header(app_client, db_clean, off_clean, admin, rules,
                                                                         caplog, monkeypatch):
    """What application code hands to a logger, read before any handler filter runs.

    The log filter would redact a key on the way out; this proves no key is
    handed to a logger in the first place, so the filter is a second line, not
    the only one.
    """
    await seed_world()
    caplog.set_level(logging.DEBUG)
    handed: list[str] = []
    real_handle = logging.Logger.handle

    def capture(self, record):
        try:
            handed.append(f"{record.name} {record.getMessage()}")
        except Exception:  # noqa: BLE001 - a malformed record is still evidence
            handed.append(f"{record.name} {record.msg} {record.args!r}")
        return real_handle(self, record)

    monkeypatch.setattr(logging.Logger, "handle", capture)
    client, raw = await client_with_key(app_client, admin)
    await ok(app_client, raw, GRADED)
    await ask(app_client, raw, "1234")
    wrong = raw[:-1] + ("0" if raw[-1] != "0" else "1")
    await ask(app_client, wrong, GRADED)
    listing = (await app_client.get("/api/v2/admin/b2b/clients", headers=auth(admin))).json()
    await app_client.post(f"/api/v2/admin/b2b/keys/{listing['clients'][0]['keys'][0]['id']}/revoke", headers=auth(admin))
    await ask(app_client, raw, GRADED)
    secret = raw.split("_")[2]
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    logged = "\n".join(handed)
    assert raw.split("_")[1] in logged, "the public prefix is the traceable identifier"
    for leaked in (raw, secret, key_hash, f"Bearer {raw}", wrong, wrong.split("_")[2], "ggb_"):
        assert leaked not in logged
    assert GRADED not in "\n".join(line for line in handed if line.startswith("app.")), "no barcode in B2B logs"


def test_h_the_log_filter_and_sentry_redact_any_b2b_key():
    raw = credentials.generate().raw
    record = logging.LogRecord("app", logging.INFO, __file__, 1, "presented %s and %s", (raw, "ggb_short123"), None)
    OAuthRedactionFilter().filter(record)
    assert raw not in record.getMessage() and "ggb_short123" not in record.getMessage()
    event = scrub_event({
        "request": {"headers": {"Authorization": f"Bearer {raw}"}, "data": f"oops {raw} then ggb_abc123_def"},
        "extra": {"key_hash": hashlib.sha256(raw.encode()).hexdigest(), "api_key": raw,
                  "note": f"retry with {raw[:30]}"},
        "exception": {"values": [{"value": f"bad credential {raw}"}]},
    })
    dumped = json.dumps(event)
    assert raw not in dumped and raw[:30] not in dumped and "ggb_abc123_def" not in dumped
    assert hashlib.sha256(raw.encode()).hexdigest() not in dumped


# ===========================================================================
# I — burst limit, daily allowance and the concurrent final unit
# ===========================================================================
async def test_i_first_final_and_exhausted_requests(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    client, raw = await client_with_key(app_client, admin, rpm=600, rpd=3)
    for _ in range(3):
        assert (await ask(app_client, raw, GRADED)).status_code == 200
    refused = await ask(app_client, raw, GRADED)
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "B2B_DAILY_QUOTA_EXHAUSTED"
    async with get_sessionmaker()() as session:
        seconds = (await session.execute(text(
            "SELECT extract(epoch FROM (date_trunc('day', timezone('UTC', now())) + interval '1 day') "
            "- timezone('UTC', now()))"
        ))).scalar_one()
    assert 1 <= int(refused.headers["Retry-After"]) <= int(seconds) + 2
    row = await usage_row(client["id"])
    assert (row.request_count, row.successful_count, row.not_enough_information_count, row.rate_limited_count) == (
        3, 3, 0, 1,
    )


async def test_i_the_usage_day_is_the_database_utc_day(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    client, raw = await client_with_key(app_client, admin)
    await ok(app_client, raw, UNKNOWN)
    async with get_sessionmaker()() as session:
        today = (await session.execute(text("SELECT timezone('UTC', now())::date"))).scalar_one()
    row = await usage_row(client["id"])
    assert row.usage_date == today
    assert (row.request_count, row.successful_count, row.not_enough_information_count) == (1, 0, 1)


async def test_i_yesterdays_spent_allowance_does_not_carry_over(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    client, raw = await client_with_key(app_client, admin, rpd=2)
    await sql(
        "INSERT INTO b2b_api_usage_daily (client_id, usage_date, request_count, successful_count) "
        "VALUES (:id, timezone('UTC', now())::date - 1, 2, 2)", id=uuid.UUID(client["id"]),
    )
    assert (await ask(app_client, raw, GRADED)).status_code == 200
    assert (await ask(app_client, raw, GRADED)).status_code == 200
    assert (await ask(app_client, raw, GRADED)).status_code == 429


async def _client_row(app_client, admin, rpd: int) -> uuid.UUID:
    client = await new_client(app_client, admin, rpd=rpd, rpm=600)
    return uuid.UUID(client["id"])


async def test_i_two_requests_cannot_both_take_the_last_unit(app_client, db_clean, admin):
    client_id = await _client_row(app_client, admin, rpd=5)
    await sql(
        "INSERT INTO b2b_api_usage_daily (client_id, usage_date, request_count) "
        "VALUES (:id, timezone('UTC', now())::date, 4)", id=client_id,
    )
    factory = get_sessionmaker()
    async with factory() as first, factory() as second:
        taken = await quota.acquire_daily(first, client_id)
        assert taken is not None
        contender = asyncio.create_task(quota.acquire_daily(second, client_id))
        await asyncio.sleep(0.4)
        assert not contender.done(), "the second request must wait on the row, not read a stale count"
        await first.commit()
        assert await contender is None
        await second.commit()
    assert (await usage_row(str(client_id))).request_count == 5


async def test_i_two_first_requests_of_the_day_cannot_both_take_a_one_unit_allowance(app_client, db_clean, admin):
    client_id = await _client_row(app_client, admin, rpd=1)
    factory = get_sessionmaker()
    async with factory() as first, factory() as second:
        taken = await quota.acquire_daily(first, client_id)
        contender = asyncio.create_task(quota.acquire_daily(second, client_id))
        await asyncio.sleep(0.4)
        assert not contender.done()
        await first.commit()
        assert taken is not None and await contender is None
        await second.commit()
    assert (await usage_row(str(client_id))).request_count == 1


async def test_i_a_burst_of_concurrent_requests_never_overspends(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    client, raw = await client_with_key(app_client, admin, rpm=600, rpd=4)
    responses = await asyncio.gather(*(ask(app_client, raw, GRADED) for _ in range(12)))
    codes = sorted(response.status_code for response in responses)
    assert codes.count(200) == 4 and codes.count(429) == 8, codes
    row = await usage_row(client["id"])
    assert (row.request_count, row.successful_count, row.rate_limited_count) == (4, 4, 8)


async def test_i_the_burst_limit_refuses_before_any_truth_or_allowance(app_client, db_clean, off_clean, admin, rules,
                                                                       monkeypatch):
    await seed_world()
    client, raw = await client_with_key(app_client, admin, rpm=2, rpd=100)
    computed: list[str] = []
    real = b2b_truth.product_truth

    async def counting(session, barcode):
        computed.append(barcode)
        return await real(session, barcode)

    monkeypatch.setattr(b2b_truth, "product_truth", counting)
    assert (await ask(app_client, raw, GRADED)).status_code == 200
    assert (await ask(app_client, raw, GRADED)).status_code == 200
    refused = await ask(app_client, raw, GRADED)
    assert refused.status_code == 429 and refused.json()["detail"]["code"] == "B2B_RATE_LIMITED"
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    assert computed == [GRADED, GRADED]
    row = await usage_row(client["id"])
    assert (row.request_count, row.rate_limited_count) == (2, 1)
    # Keyed by client: a second key of the same client shares the bucket.
    _, second = await new_key(app_client, admin, client["id"])
    assert (await ask(app_client, second, GRADED)).status_code == 429
    assert all("ggb_" not in key for key in quota._burst_limiter.state)
    assert set(quota._burst_limiter.state) == {client["id"]}


async def test_i_the_daily_allowance_is_taken_before_any_truth(app_client, db_clean, off_clean, admin, rules,
                                                               monkeypatch):
    await seed_world()
    _, raw = await client_with_key(app_client, admin, rpd=1)
    computed: list[str] = []
    real = b2b_truth.product_truth

    async def counting(session, barcode):
        computed.append(barcode)
        return await real(session, barcode)

    monkeypatch.setattr(b2b_truth, "product_truth", counting)
    assert (await ask(app_client, raw, GRADED)).status_code == 200
    assert (await ask(app_client, raw, GRADED)).status_code == 429
    assert computed == [GRADED]


async def test_i_a_suspended_client_spends_nothing(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    client, raw = await client_with_key(app_client, admin, rpd=5)
    await app_client.post(f"/api/v2/admin/b2b/clients/{client['id']}/suspend", headers=auth(admin))
    for _ in range(3):
        await _expect_generic_401(await ask(app_client, raw, GRADED))
    assert await usage_row(client["id"]) is None


async def test_i_a_refused_request_shape_spends_nothing(app_client, db_clean, off_clean, admin, rules, monkeypatch):
    await seed_world()
    client, raw = await client_with_key(app_client, admin, rpd=5)
    computed: list[str] = []

    async def never(session, barcode):
        computed.append(barcode)
        raise Forbidden("product truth for a refused request")

    monkeypatch.setattr(b2b_truth, "product_truth", never)
    wrong_check = GRADED[:-1] + str((int(GRADED[-1]) + 1) % 10)
    arabic_indic = GRADED.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
    for barcode in (wrong_check, "abc", "12345", "0" * 13, arabic_indic, GRADED + "0" * 2, "%20" + GRADED):
        response = await ask(app_client, raw, barcode)
        assert response.status_code == 422 and response.json()["detail"]["code"] == "B2B_BARCODE_INVALID", barcode
    response = await ask(app_client, raw, GRADED, params={"verdict": "buy"})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "B2B_QUERY_NOT_ACCEPTED"
    assert computed == []
    assert await usage_row(client["id"]) is None


async def test_i_quota_never_changes_content(app_client, db_clean, off_clean, admin, rules):
    """The answer just before exhaustion is the answer a fresh, unlimited client gets."""
    await seed_world()
    _, tight = await client_with_key(app_client, admin, client_key="tight", rpd=2)
    _, loose = await client_with_key(app_client, admin, client_key="loose", rpd=1_000_000)
    await ok(app_client, tight, BUY)
    last = await ok(app_client, tight, GRADED)
    assert (await ask(app_client, tight, GRADED)).status_code == 429
    assert canonical(last) == canonical(await ok(app_client, loose, GRADED))


# ===========================================================================
# J — administration and audit
# ===========================================================================
async def test_j_admin_surface_is_hidden_from_everyone_else(app_client, db_clean, admin, fake_supabase_user,
                                                            registered_supabase_user):
    client = await new_client(app_client, admin)
    stranger, _ = await registered_supabase_user()
    plain, _ = fake_supabase_user()
    routes = [
        ("POST", "/api/v2/admin/b2b/clients", {"client_key": "x-co", "display_name": "X",
                                               "requests_per_minute": 1, "requests_per_day": 1}),
        ("GET", "/api/v2/admin/b2b/clients", None),
        ("POST", f"/api/v2/admin/b2b/clients/{client['id']}/keys", {}),
        ("POST", f"/api/v2/admin/b2b/keys/{uuid.uuid4()}/revoke", None),
        ("POST", f"/api/v2/admin/b2b/clients/{client['id']}/suspend", None),
        ("POST", f"/api/v2/admin/b2b/clients/{client['id']}/activate", None),
        ("GET", f"/api/v2/admin/b2b/clients/{client['id']}/usage", None),
    ]
    for method, path, body in routes:
        assert (await app_client.request(method, path, json=body)).status_code == 401, path
        for token in (stranger, plain):
            response = await app_client.request(method, path, headers=auth(token), json=body)
            assert response.status_code == 404, (path, response.status_code)
    async with get_sessionmaker()() as session:
        assert (await session.execute(select(func.count()).select_from(B2BApiKey))).scalar_one() == 0
        assert (await session.execute(select(func.count()).select_from(B2BApiClient))).scalar_one() == 1


async def test_j_client_creation_is_validated(app_client, db_clean, admin):
    good = {"client_key": "acme-retail", "display_name": "Acme", "requests_per_minute": 10, "requests_per_day": 10}
    for change in (
        {"client_key": "Acme"}, {"client_key": "a"}, {"client_key": "-acme"}, {"client_key": "acme_retail"},
        {"display_name": "   "}, {"display_name": "x" * 121}, {"requests_per_minute": 0},
        {"requests_per_minute": 601}, {"requests_per_day": 0}, {"requests_per_day": 1_000_001},
        {"price_per_call": 1}, {"commission": 5}, {"contract_value": 10},
    ):
        response = await app_client.post("/api/v2/admin/b2b/clients", headers=auth(admin), json={**good, **change})
        assert response.status_code == 422, change
    assert (await app_client.post("/api/v2/admin/b2b/clients", headers=auth(admin), json=good)).status_code == 201
    duplicate = await app_client.post("/api/v2/admin/b2b/clients", headers=auth(admin), json=good)
    assert duplicate.status_code == 409 and duplicate.json()["detail"]["code"] == "client_key_taken"


async def test_j_key_expiry_must_be_in_the_future_and_aware(app_client, db_clean, admin):
    client = await new_client(app_client, admin)
    path = f"/api/v2/admin/b2b/clients/{client['id']}/keys"
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    assert (await app_client.post(path, headers=auth(admin), json={"expires_at": past})).status_code == 422
    naive = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None).isoformat()
    assert (await app_client.post(path, headers=auth(admin), json={"expires_at": naive})).status_code == 422
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    view, _ = await new_key(app_client, admin, client["id"], expires_at=future)
    assert view["state"] == "active" and view["expires_at"]
    assert (await app_client.post(f"/api/v2/admin/b2b/clients/{uuid.uuid4()}/keys", headers=auth(admin),
                                  json={})).status_code == 404


async def test_j_revocation_is_idempotent_and_keeps_history(app_client, db_clean, admin):
    client = await new_client(app_client, admin)
    view, _ = await new_key(app_client, admin, client["id"])
    first = (await app_client.post(f"/api/v2/admin/b2b/keys/{view['id']}/revoke", headers=auth(admin))).json()
    second = (await app_client.post(f"/api/v2/admin/b2b/keys/{view['id']}/revoke", headers=auth(admin))).json()
    assert first["state"] == second["state"] == "revoked" and first["revoked_at"] == second["revoked_at"]
    async with get_sessionmaker()() as session:
        assert (await session.execute(select(func.count()).select_from(B2BApiKey))).scalar_one() == 1


async def test_j_every_change_is_audited_without_the_secret(app_client, db_clean, registered_supabase_user):
    token, admin_id = await registered_supabase_user(admin=True)
    client = await new_client(app_client, token)
    view, raw = await new_key(app_client, token, client["id"])
    await app_client.post(f"/api/v2/admin/b2b/keys/{view['id']}/revoke", headers=auth(token))
    await app_client.post(f"/api/v2/admin/b2b/clients/{client['id']}/suspend", headers=auth(token))
    await app_client.post(f"/api/v2/admin/b2b/clients/{client['id']}/activate", headers=auth(token))
    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(AuditEvent).where(AuditEvent.action.like("b2b.%")).order_by(AuditEvent.created_at)
        )).scalars().all()
    assert [row.action for row in rows] == [
        "b2b.client.created", "b2b.key.issued", "b2b.key.revoked", "b2b.client.suspended", "b2b.client.activated",
    ]
    for row in rows:
        assert row.actor_type == "admin" and row.account_id == admin_id
        dumped = json.dumps(row.context)
        assert raw not in dumped and raw.split("_")[2] not in dumped
        assert hashlib.sha256(raw.encode()).hexdigest() not in dumped
        assert row.context.get("client_key") == "acme-retail"
    assert rows[1].context["key_prefix"] == view["key_prefix"]


async def test_j_an_admin_without_an_account_row_is_audited_anonymously(app_client, db_clean, admin):
    await new_client(app_client, admin)
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(AuditEvent).where(AuditEvent.action == "b2b.client.created"))).scalar_one()
    assert row.account_id is None and row.actor_type == "admin"


async def test_j_usage_reports_operations_never_money(app_client, db_clean, off_clean, admin, rules):
    await seed_world()
    client, raw = await client_with_key(app_client, admin, rpd=3)
    await ok(app_client, raw, GRADED)
    await ok(app_client, raw, UNKNOWN)
    await ok(app_client, raw, BUY)
    await ask(app_client, raw, GRADED)
    usage = (await app_client.get(f"/api/v2/admin/b2b/clients/{client['id']}/usage?days=7", headers=auth(admin))).json()
    assert usage["today"] == {
        "usage_date": usage["window"]["to"], "requests": 3, "successful": 2, "not_enough_information": 1,
        "rate_limited": 1, "remaining": 0,
    }
    assert usage["quota"] == {"requests_per_minute": 60, "requests_per_day": 3}
    assert usage["status"] == "active" and usage["window"]["days"] == 7 and len(usage["daily"]) == 7
    keys = " ".join(_keys(usage)).lower()
    for word in ("revenue", "purchase", "conversion", "arr", "sale", "price", "amount", "charge", "barcode", "product"):
        assert not re.search(rf"\b{word}", keys), word
    for days in (0, 91):
        response = await app_client.get(f"/api/v2/admin/b2b/clients/{client['id']}/usage?days={days}",
                                        headers=auth(admin))
        assert response.status_code == 422


# ===========================================================================
# K — privacy and deletion
# ===========================================================================
def test_k_the_new_tables_are_classified_and_the_consumer_export_is_unchanged():
    assert REGISTRY["b2b_api_clients"] == Classification.NOT_USER_OWNED
    assert REGISTRY["b2b_api_keys"] == Classification.SECRET_EXCLUDED
    assert REGISTRY["b2b_api_usage_daily"] == Classification.OPERATIONAL
    assert EXPORT_SCHEMA_VERSION == "1.6"
    assert len(EXPORT_COVERAGE) == 112
    assert not {name for name in included_tables() if name.startswith("b2b_")}
    assert not {name for name in EXPORT_COVERAGE if name.startswith("b2b_")}


def test_k_no_b2b_table_belongs_to_a_consumer_account():
    for table in Base.metadata.sorted_tables:
        if not table.name.startswith("b2b_"):
            continue
        assert "account_id" not in table.columns and "device_id" not in table.columns
        for key in table.foreign_keys:
            assert key.column.table.name.startswith("b2b_"), (table.name, key.target_fullname)
            assert key.ondelete == "RESTRICT", (table.name, key.target_fullname)


async def test_k_deleting_the_admins_account_leaves_b2b_access_intact(app_client, db_clean, off_clean, rules,
                                                                      registered_supabase_user, media_root):
    await seed_world()
    token, admin_id = await registered_supabase_user(admin=True)
    client, raw = await client_with_key(app_client, token)
    await ok(app_client, raw, GRADED)
    async with get_sessionmaker()() as session:
        await deletion_service.request_deletion(session, admin_id)
        await session.commit()
    async with get_sessionmaker()() as session:
        assert await deletion_service.drain_all(session) >= 1
        await session.commit()
    async with get_sessionmaker()() as session:
        assert (await session.execute(select(func.count()).select_from(B2BApiClient))).scalar_one() == 1
        assert (await session.execute(select(func.count()).select_from(B2BApiKey))).scalar_one() == 1
        assert (await session.execute(select(func.count()).select_from(B2BApiUsageDaily))).scalar_one() == 1
    assert (await ok(app_client, raw, GRADED))["state"] == "available"


# ===========================================================================
# L — schema and migration
# ===========================================================================
def test_l_exactly_one_additive_migration_on_the_previous_head():
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    assigned = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    assert (assigned["revision"], assigned["down_revision"]) == ("m1n2o3p4q5", "l0m1n2o3p4")
    # One linear chain, and Step 17 sits directly on Step 15's revision.
    chain = alembic_revision_chain()
    assert chain.index("m1n2o3p4q5") + 1 == chain.index("l0m1n2o3p4")
    assert alembic_head_revision() == chain[0]
    assert len(chain) == len(set(chain))
    downgrade = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "downgrade")
    dropped = {
        call.args[0].value for call in ast.walk(downgrade)
        if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "drop_table"
    }
    assert dropped == {"b2b_api_clients", "b2b_api_keys", "b2b_api_usage_daily"}
    calls = {getattr(call.func, "attr", "") for call in ast.walk(downgrade) if isinstance(call, ast.Call)}
    assert calls <= {"drop_table", "drop_index"}, calls
    upgrade = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "upgrade")
    upgrade_calls = {getattr(call.func, "attr", "") for call in ast.walk(upgrade) if isinstance(call, ast.Call)}
    assert not upgrade_calls & {"add_column", "alter_column", "drop_column", "drop_table", "execute"}


def test_l_the_tables_carry_their_constraints_and_no_commercial_field():
    tables = {table.name: table for table in Base.metadata.sorted_tables if table.name.startswith("b2b_")}
    assert set(tables) == {"b2b_api_clients", "b2b_api_keys", "b2b_api_usage_daily"}
    names = {
        name: {constraint.name for constraint in table.constraints if constraint.name}
        for name, table in tables.items()
    }
    assert {"uq_b2b_api_clients_client_key", "ck_b2b_api_clients_status",
            "ck_b2b_api_clients_requests_per_day"} <= names["b2b_api_clients"]
    assert {"uq_b2b_api_keys_key_prefix", "uq_b2b_api_keys_key_hash", "ck_b2b_api_keys_key_hash"} <= names["b2b_api_keys"]
    assert {"ck_b2b_api_usage_daily_outcomes_within_requests"} <= names["b2b_api_usage_daily"]
    assert [column.name for column in tables["b2b_api_usage_daily"].primary_key.columns] == ["client_id", "usage_date"]
    for table in tables.values():
        for column in table.columns:
            assert not re.search(r"price|amount|commission|contract|invoice|plan|charge|revenue|card|currency|config",
                                 column.name), (table.name, column.name)


async def test_l_the_database_enforces_the_usage_invariant(app_client, db_clean, admin):
    client_id = await _client_row(app_client, admin, rpd=5)
    with pytest.raises(IntegrityError):
        await sql(
            "INSERT INTO b2b_api_usage_daily (client_id, usage_date, request_count, successful_count) "
            "VALUES (:id, current_date, 1, 2)", id=client_id,
        )
    with pytest.raises(IntegrityError):
        await sql("UPDATE b2b_api_clients SET status = 'paid' WHERE id = :id", id=client_id)
    with pytest.raises(IntegrityError):
        await _restrict_delete(client_id)


async def _restrict_delete(client_id: uuid.UUID) -> None:
    await sql(
        "INSERT INTO b2b_api_keys (id, client_id, key_prefix, key_hash) VALUES (:kid, :id, :p, :h)",
        kid=uuid.uuid4(), id=client_id, p="d" * 12, h="e" * 64,
    )
    await sql("DELETE FROM b2b_api_clients WHERE id = :id", id=client_id)


# ===========================================================================
# M — OpenAPI
# ===========================================================================
def test_m_the_b2b_route_is_a_clear_separate_contract_in_openapi():
    from server import app

    schema = app.openapi()
    b2b_paths = {path: ops for path, ops in schema["paths"].items() if path.startswith("/api/b2b")}
    assert set(b2b_paths) == {TRUTH_PATH}
    operations = b2b_paths[TRUTH_PATH]
    assert set(operations) == {"get"}
    operation = operations["get"]
    assert operation["tags"] == ["b2b-product-truth-v1"]
    assert operation["summary"] == "Verified Product Truth for one exact barcode"
    assert operation["operationId"] == "readB2BProductTruth"
    assert operation["security"] == [{"GlamGeniusB2BApiKey": []}]
    assert set(operation["responses"]) == {"200", "401", "422", "429"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ProductTruthResponse",
    )
    scheme = schema["components"]["securitySchemes"]["GlamGeniusB2BApiKey"]
    assert (scheme["type"], scheme["scheme"], scheme["bearerFormat"]) == ("http", "bearer", "ggb_<prefix>_<secret>")
    for path, ops in schema["paths"].items():
        if path.startswith("/api/b2b"):
            continue
        for op in ops.values():
            assert {"GlamGeniusB2BApiKey": []} not in (op.get("security") or []), path
            assert "b2b-product-truth-v1" not in (op.get("tags") or []), path
    for name in schema["components"]["schemas"]:
        assert not name.startswith(("B2BApi", "B2BCaller")), name


# ===========================================================================
# N — read-only, single-barcode, no AI, no personal parameters
# ===========================================================================
async def test_n_the_truth_route_is_read_only(app_client, db_clean, off_clean, admin, rules):
    _, raw = await client_with_key(app_client, admin)
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = await app_client.request(method, TRUTH_PATH.format(barcode=GRADED), headers=auth(raw), json={})
        assert response.status_code == 405, method
    for path in ("/api/b2b/v1/products", "/api/b2b/v1/products/search", "/api/b2b/v1/export",
                 "/api/b2b/v1/products/batch", "/api/b2b/v1/webhooks"):
        assert (await app_client.request("GET", path, headers=auth(raw))).status_code in {404, 405}, path
        assert (await app_client.request("POST", path, headers=auth(raw), json={})).status_code in {404, 405}, path


@pytest.mark.parametrize("params", [
    {"pregnancy": "true"}, {"breastfeeding": "true"}, {"medication": "metformin"}, {"condition": "diabetes"},
    {"child_age": "4"}, {"health_profile": "x"}, {"desired_verdict": "buy"}, {"grade": "A"}, {"tone": "kind"},
    {"brand_priority": "1"}, {"customer_policy": "lenient"}, {"physical_pack_context": "true"},
    {"partner": "amazon_in"}, {"subject_id": str(uuid.UUID(int=1))}, {"account_id": str(uuid.UUID(int=1))},
])
async def test_n_no_parameter_can_steer_the_answer(app_client, db_clean, off_clean, admin, rules, params):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    response = await ask(app_client, raw, GRADED, params=params)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "B2B_QUERY_NOT_ACCEPTED"


async def test_n_no_ai_call_is_made_for_any_answer(app_client, db_clean, off_clean, admin, rules, monkeypatch):
    await seed_world()
    _, raw = await client_with_key(app_client, admin)
    with forbidden_collaborators(monkeypatch) as calls:
        for barcode in (GRADED, BUY, SKIP, GHEE, UNKNOWN, OFF_RICH, IDENTITY_ONLY):
            await ok(app_client, raw, barcode)
    assert calls == []


def test_n_no_b2b_route_writes_product_data():
    """The only Step 17 writes are the admin lifecycle routes, and none names a product table."""
    for path in _b2b_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"add", "delete", "merge"}:
                owner = node.value
                assert not (isinstance(owner, ast.Name) and owner.id == "session") or path.name == "access.py", (
                    path.name, node.attr,
                )
        source = path.read_text(encoding="utf-8")
        for table in ("product_label_snapshots", "product_records", "evidence_", "scan_events", "rule_evidence"):
            assert f"INSERT INTO {table}" not in source and f"UPDATE {table}" not in source, (path.name, table)
