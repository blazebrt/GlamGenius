"""Step 6B — what confirmed pack labels said their MRP was, and nothing more.

The claim is small and the ways of overstating it are many, so most of what
follows defends the boundary rather than the arithmetic: this is a *maximum
retail price a pack declared on a date*, not a price, not a saving, not a
verdict on value, and never a reason to prefer one product over another.

The invariant that matters most is order. Step 6A picks the alternative on
scientific grounds and Step 6B reads that choice afterwards. Money has no path
back into selection, and several tests here try hard to find one.
"""
from __future__ import annotations

import ast
import inspect
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun, AIRunOutput
from app.domains.nutrition.grading.production_rules import (
    STATUS_PUBLISHED,
    ProductionRuleset,
    candidate_ruleset,
)
from app.domains.off import client as off_client
from app.domains.off.models import OffBase, OffProduct
from app.domains.off.store import create_off_schema, get_off_engine, get_off_sessionmaker
from app.domains.product import extraction
from app.domains.product import service as product_service
from app.domains.product.models import LabelSnapshot, ScanEvent
from app.domains.value import parsing as value_parsing
from app.domains.value import policy as value_policy
from app.domains.value import service as value_service
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select, update

from tests.conftest import auth

CURRENT = "8902000000001"
CANDIDATE_B = "8902000000002"
CANDIDATE_A = "8902000000003"

CEREAL_CATEGORY = "Foods, Breakfasts, Breakfast cereals"
CEREAL_CATEGORY_OTHER_PATH = "Plant foods, Breakfast cereals"

PANEL_C = {"energy_kcal": "380", "sugars_g": "8", "saturated_fat_g": "1",
           "salt_g": "0.5", "protein_g": "7", "fibre_g": "3"}
PANEL_B = {"energy_kcal": "370", "sugars_g": "2", "saturated_fat_g": "1.2",
           "salt_g": "0.3", "protein_g": "12", "fibre_g": "9"}
PANEL_A = {"energy_kcal": "380", "sugars_g": "1", "saturated_fat_g": "1.2",
           "salt_g": "0.02", "protein_g": "13", "fibre_g": "10"}
PANEL_B_DRINK = {"energy_kcal": "70", "sugars_g": "2", "saturated_fat_g": "0.3",
                 "salt_g": "0.1", "protein_g": "3", "fibre_g": "1.5"}

INGREDIENTS_C = "maize, sugar, salt, flavouring, emulsifier (ins 322)"
INGREDIENTS_B = "whole grain oats, salt"
INGREDIENTS_A = "whole grain oats"


def label_facts(
    *,
    product_name: str,
    ingredients: str,
    panel: dict,
    net_quantity: str = "500 g",
    basis: str = "per_100g",
    brand: str | None = "Sunfield",
    mrp_text: str | None = None,
    **extra,
) -> dict:
    facts = {
        "product_name": product_name, "brand": brand, "ingredients_text": ingredients,
        "nutrition_per_100g": panel, "nutrition_basis": basis,
        "net_quantity": net_quantity, **extra,
    }
    if mrp_text is not None:
        facts["mrp_text"] = mrp_text
    return facts


@pytest_asyncio.fixture
async def off_clean():
    from sqlalchemy import text

    await create_off_schema()
    async with get_off_engine().begin() as conn:
        names = ", ".join(
            f'"{t.schema}"."{t.name}"' for t in reversed(OffBase.metadata.sorted_tables)
        )
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield


@pytest_asyncio.fixture
async def device(app_client):
    response = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert response.status_code == 201, response.text
    return {"X-Device-Token": response.json()["token"]}


def _published() -> ProductionRuleset:
    return ProductionRuleset(provenance={
        rule_id: replace(row, status=STATUS_PUBLISHED)
        for rule_id, row in candidate_ruleset().provenance.items()
    })


@pytest.fixture
def published_rules(monkeypatch):
    from app.api.v2 import product as product_api

    async def resolve(_session):
        return _published()

    monkeypatch.setattr(product_api, "resolve_production_ruleset", resolve)
    return resolve


@pytest.fixture
def no_off_network(monkeypatch):
    calls: list[str] = []

    async def record(barcode: str):
        calls.append(barcode)
        return None

    monkeypatch.setattr(off_client, "fetch_product", record)
    return calls


async def seed_off(barcode: str, *, categories: str = CEREAL_CATEGORY, countries: str = "India") -> None:
    factory = get_off_sessionmaker()
    async with factory() as session:
        session.add(OffProduct(
            barcode=barcode, product_name="Catalogue Name", categories=categories,
            countries=countries, fetched_at=datetime.now(UTC),
        ))
        await session.commit()


async def seed_capture(
    barcode: str, facts: dict, *, device_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None, age: timedelta | None = None,
) -> ScanEvent:
    """One confirmed label capture, written through the real versioning path.

    ``age`` rewrites the server timestamp afterwards, which is the only way to
    test a freshness window without a client ever supplying a time.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        event = ScanEvent(
            device_id=device_id, account_id=account_id, barcode=barcode,
            outcome=product_service.OUTCOME_LABEL, client_scan_id=uuid.uuid4().hex,
            label_facts=facts,
        )
        session.add(event)
        await session.flush()
        await product_service.store_label_snapshot(
            session, barcode=barcode, facts=facts, device_id=device_id, scan_event_id=event.id,
        )
        await session.commit()
        event_id = event.id
    if age is not None:
        async with factory() as session:
            await session.execute(
                update(ScanEvent).where(ScanEvent.id == event_id)
                .values(created_at=datetime.now(UTC) - age)
            )
            await session.commit()
    factory = get_sessionmaker()
    async with factory() as session:
        return (await session.execute(
            select(ScanEvent).where(ScanEvent.id == event_id)
        )).scalar_one()


async def verdict(app_client, headers, barcode: str = CURRENT, *, physical_pack: bool | None = None) -> dict:
    url = f"/api/v2/scan/verdict/{barcode}"
    if physical_pack is not None:
        url += f"?physical_pack_context={'true' if physical_pack else 'false'}"
    response = await app_client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# The MRP parser: an explicit declaration, or nothing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("MRP ₹120", "120"),
        ("M.R.P. Rs. 120.00", "120.00"),
        ("Maximum Retail Price INR 75", "75"),
        ("MRP: ₹1,299/-", "1299"),
        ("Maximum Retail Price ₹125 incl. of all taxes", "125"),
        ("M.R.P Rs 99", "99"),
        ("mrp ₹49.50", "49.50"),
        # NFKC folds the full-width forms onto the same reading.
        ("ＭＲＰ ₹120", "120"),
    ],
)
def test_an_explicit_mrp_declaration_is_read_exactly(text, expected):
    assert value_parsing.parse_mrp_rupees(text) == Decimal(expected)


@pytest.mark.parametrize(
    "text",
    [
        # A rupee sign beside a number is not a declared maximum retail price.
        "₹120",
        "Offer ₹99",
        "Selling price ₹99",
        "Our price ₹99",
        "Approx ₹100",
        # Two amounts, and no way to know which one the pack means.
        "MRP ₹100 / ₹120",
        "MRP ₹100-120",
        "MRP ₹100 / 120",
        # Nothing that is money at all.
        "MRP FREE",
        "MRP 0",
        "MRP ₹0",
        "MRP -10",
        "MRP ₹-10",
        # Rupees have two decimal places; a third means we misread something.
        "MRP ₹99.999",
        "garbage",
        "",
        "   ",
    ],
)
def test_anything_doubtful_is_refused_rather_than_guessed(text):
    assert value_parsing.parse_mrp_rupees(text) is None


def test_a_non_string_or_oversized_clause_is_refused():
    for value in (None, 120, 12.5, ["MRP ₹120"], {"mrp": 120}, True):
        assert value_parsing.parse_mrp_rupees(value) is None
    assert value_parsing.parse_mrp_rupees("MRP ₹120" + " x" * 200) is None


def test_no_market_price_ceiling_rejects_a_legitimate_bulk_pack():
    """A 25 kg sack is not suspicious for costing what a 25 kg sack costs."""
    assert value_parsing.parse_mrp_rupees("MRP ₹4,250") == Decimal("4250")
    assert value_parsing.parse_mrp_rupees("MRP ₹18,999.00") == Decimal("18999.00")


# ---------------------------------------------------------------------------
# The quantity parser: the whole string, or nothing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "dimension", "amount", "unit"),
    [
        ("500 g", "mass", "500", "g"),
        ("500g", "mass", "500", "g"),
        ("1 kg", "mass", "1000", "g"),
        ("1.5 kg", "mass", "1500.0", "g"),
        ("250 ml", "volume", "250", "ml"),
        ("1 L", "volume", "1000", "ml"),
        ("330ml", "volume", "330", "ml"),
        ("4 x 25 g", "mass", "100", "g"),
        ("4 × 25 g", "mass", "100", "g"),
    ],
)
def test_a_stated_net_quantity_normalises_to_one_base_unit(text, dimension, amount, unit):
    quantity = value_parsing.parse_quantity(text)
    assert quantity is not None
    assert quantity.dimension == dimension
    assert quantity.base_amount == Decimal(amount)
    assert quantity.base_unit == unit


@pytest.mark.parametrize(
    "text",
    [
        "100 g + 20 g free",
        "approx 500 g",
        "10 pieces",
        "12 sachets",
        "1 bottle",
        "serves 4",
        "family pack",
        "500 g / 550 g",
        "500",
        "g",
        "0 g",
        "500 oz",
        "",
    ],
)
def test_an_ambiguous_pack_size_is_refused(text):
    assert value_parsing.parse_quantity(text) is None


def test_a_non_string_quantity_is_refused():
    for value in (None, 500, 1.5, ["500 g"], True):
        assert value_parsing.parse_quantity(value) is None


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("mrp", "quantity", "expected"),
    [
        ("100", "500 g", "20.00"),
        ("100", "1 kg", "10.00"),
        ("90", "250 ml", "36.00"),
        ("80", "1 L", "8.00"),
        ("100", "4 x 25 g", "100.00"),
        ("120", "500 g", "24.00"),
        ("100", "400 g", "25.00"),
    ],
)
def test_mrp_per_100_is_exact_decimal_arithmetic(mrp, quantity, expected):
    parsed = value_parsing.parse_quantity(quantity)
    per_100 = value_policy.mrp_per_100(Decimal(mrp), parsed.base_amount)
    assert value_policy.money_string(per_100) == expected


def test_money_never_passes_through_a_float():
    """A price that drifts by a paise per step is a price we cannot defend."""
    source = inspect.getsource(value_parsing) + inspect.getsource(value_policy)
    source += inspect.getsource(value_service)
    assert "float(" not in source
    # A third of a rupee rounds once, at the boundary, half up.
    assert value_policy.money_string(Decimal(100) * 100 / Decimal(3)) == "3333.33"
    assert value_policy.money_string(Decimal("0.005")) == "0.01"
    assert value_policy.money_string(Decimal("-4")) == "-4.00"


def test_the_difference_sign_is_defined_once_and_points_one_way():
    lower = value_policy.difference(Decimal("20"), Decimal("24"))
    higher = value_policy.difference(Decimal("25"), Decimal("24"))
    assert lower == Decimal("-4")
    assert higher == Decimal("1")
    assert value_policy.relationship(Decimal("20"), Decimal("24")) == "candidate_lower_mrp_per_100"
    assert value_policy.relationship(Decimal("25"), Decimal("24")) == "candidate_higher_mrp_per_100"
    assert value_policy.relationship(Decimal("24"), Decimal("24")) == "same_mrp_per_100"


def test_the_relationship_vocabulary_states_arithmetic_not_a_verdict():
    words = {
        value_policy.RELATIONSHIP_LOWER,
        value_policy.RELATIONSHIP_SAME,
        value_policy.RELATIONSHIP_HIGHER,
    }
    for banned in ("winner", "best", "worse", "cheap", "expensive", "value", "good", "bad"):
        assert not any(banned in word for word in words), banned


# ---------------------------------------------------------------------------
# Freshness: our comparison policy, on the server's clock
# ---------------------------------------------------------------------------
def test_the_observation_window_has_a_defined_boundary():
    now = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    assert value_policy.observation_is_fresh(now - timedelta(days=29, hours=23, minutes=59), now=now)
    # Exactly the window is already stale. Stated once, so it is not accidental.
    assert not value_policy.observation_is_fresh(now - timedelta(days=30), now=now)
    assert not value_policy.observation_is_fresh(now - timedelta(days=31), now=now)
    # An undated observation is not a recent one.
    assert not value_policy.observation_is_fresh(None, now=now)


def test_pack_freshness_is_its_own_policy_not_the_catalogue_cache():
    """Same number today, different question. They must be able to move apart."""
    from app.domains.off import freshness as off_freshness

    assert value_policy.MRP_OBSERVATION_MAX_AGE_DAYS == 30
    assert value_policy.MRP_OBSERVATION_MAX_AGE is not off_freshness.OFF_CACHE_TTL
    for module in (value_parsing, value_policy, value_service):
        for imported in _imported_modules(module):
            assert "off" not in imported.split("."), f"{module.__name__} imports {imported}"


# ---------------------------------------------------------------------------
# Structural guards
# ---------------------------------------------------------------------------
VALUE_MODULES = (value_parsing, value_policy, value_service)


def _imported_modules(module) -> list[str]:
    tree = ast.parse(inspect.getsource(module))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return modules


def _code_identifiers(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            first = body[0] if body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            found.add(node.name)
        elif isinstance(node, ast.alias):
            found.add(node.asname or node.name)
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            found.add(node.value)
    return {value.lower() for value in found}


#: Any number that would fold a grade and a price into one figure. The
#: Constitution rejects a composite score averaging incompatible things, and
#: quality-per-rupee is the purest example of one.
FORBIDDEN_VALUE_SCORES = (
    "value_score", "price_score", "bang_for_buck", "health_per_rupee",
    "grade_per_rupee", "quality_per_rupee", "weighted_value", "value_index",
    "best_value_score", "composite", "worth_it",
)


def test_no_number_folds_quality_and_money_together():
    for module in VALUE_MODULES:
        identifiers = _code_identifiers(module)
        for name in FORBIDDEN_VALUE_SCORES:
            offenders = [value for value in identifiers if name in value]
            assert not offenders, f"{module.__name__} defines {offenders}"


def test_the_value_domain_reaches_no_retailer_and_no_network():
    """Zero external price requests, enforced structurally rather than promised."""
    banned = (
        "httpx", "requests", "aiohttp", "urllib", "socket", "selenium",
        "amazon", "flipkart", "blinkit", "zepto", "instamart", "affiliate",
        "retailer", "scrape", "crawler", "commerce",
    )
    for module in VALUE_MODULES:
        for imported in _imported_modules(module):
            for name in banned:
                assert name not in imported.lower(), f"{module.__name__} imports {imported}"
        identifiers = _code_identifiers(module)
        for name in ("http://", "https://", "www."):
            assert not any(name in value for value in identifiers), f"{module.__name__}: {name}"


def test_the_value_domain_reads_no_ai_person_or_lower_epistemic_layer():
    banned = (
        "ai_gateway", "gemini", "community", "official_records", "grading",
        "identity", "profile", "family", "purchase", "beta_access", "consent",
        "progress", "planning", "inventory", "recommendation", "alternatives",
    )
    for module in VALUE_MODULES:
        for imported in _imported_modules(module):
            for name in banned:
                assert name not in imported.lower(), f"{module.__name__} imports {imported}"


def test_the_value_domain_writes_nothing():
    tree = ast.parse(inspect.getsource(value_service))
    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for writer in ("add", "add_all", "commit", "flush", "merge", "delete", "bulk_save_objects"):
        assert writer not in called, f"the value service calls session.{writer}()"


def test_no_price_table_was_introduced():
    """V1 carries no new persistence, so there is no migration and no new table."""
    from app.shared.database.registry import Base

    for name in Base.metadata.tables:
        lowered = name.lower()
        for banned in ("price", "mrp", "value_record", "retailer"):
            assert banned not in lowered, f"a price table appeared: {name}"


# ---------------------------------------------------------------------------
# Transcription: schema v2, and a v1 review that is still somebody's work
# ---------------------------------------------------------------------------
def test_the_extraction_schema_gained_an_optional_mrp_clause():
    assert extraction.SCHEMA_VERSION == "scan-label.v2"
    assert extraction.PROMPT_VERSION == "scan-label.v2"
    field = extraction.ExtractedLabel.model_fields["mrp_text"]
    assert field.default is None, "mrp_text must be optional so a v1 payload still validates"
    # A payload written before this field existed still validates.
    legacy = extraction.ExtractedLabel.model_validate({"product_name": "Old Flakes"})
    assert legacy.mrp_text is None
    assert "mrp_text" not in legacy.model_dump(exclude_none=True)


def test_the_prompt_permits_only_an_explicit_printed_declaration():
    """The model may copy a declaration. It may not decide one exists."""
    # Line wrapping is a detail of the source file, not of the instruction.
    instructions = " ".join(extraction.prompt().lower().split())
    system = " ".join(extraction.SYSTEM.lower().split())

    # What it may transcribe, and the condition attached to doing so.
    assert "maximum retail price" in instructions
    assert "exactly as printed" in instructions
    # A rupee sign beside a number is explicitly ruled out as sufficient.
    assert "not enough on its own" in instructions
    assert "offer price" in instructions and "selling price" in instructions
    # And an unreadable price is named as missing rather than guessed.
    assert "omit mrp_text" in instructions
    assert "uncertain_fields" in instructions

    # The system prompt forbids judging money rather than merely omitting to
    # mention it — the words appear here as prohibitions, which is the point.
    assert "never state or estimate a price" in system
    for judgement in ("cheap", "expensive", "affordable", "good value"):
        assert judgement in system, f"{judgement} is not explicitly forbidden"
    assert "do not decide whether anything is" in system


def test_both_schema_versions_remain_confirmable():
    assert {"scan-label.v1", "scan-label.v2"} == extraction.CONFIRMABLE_SCHEMA_VERSIONS


async def _seed_ai_run(account_id: uuid.UUID, payload: dict, *, schema_version: str) -> uuid.UUID:
    factory = get_sessionmaker()
    async with factory() as session:
        run = AIRun(
            account_id=account_id, feature=extraction.FEATURE, provider="test",
            model="test-model", prompt_version=schema_version, schema_version=schema_version,
            status=AI_STATUS_SUCCEEDED, validation_passed=True,
        )
        session.add(run)
        await session.flush()
        session.add(AIRunOutput(ai_run_id=run.id, schema_version=schema_version, payload=payload))
        await session.commit()
        return run.id


@pytest.mark.asyncio
async def test_a_review_started_before_the_deployment_still_confirms(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """A person photographed a label, walked to the till, and we deployed.

    Losing their capture because a schema version moved underneath them would
    throw away work they already did. ``mrp_text`` is optional precisely so the
    older payload is still valid.
    """
    token, account_id = await registered_supabase_user()
    legacy_payload = label_facts(
        product_name="Northstar Corn Flakes", ingredients=INGREDIENTS_C, panel=PANEL_C,
    )
    run_id = await _seed_ai_run(account_id, legacy_payload, schema_version="scan-label.v1")

    response = await app_client.post(
        "/api/v2/scan/label/confirm", headers={**device, **auth(token)},
        json={"barcode": CURRENT, "ai_run_id": str(run_id), "client_scan_id": uuid.uuid4().hex},
    )
    assert response.status_code == 201, response.text

    # And a new v2 transcription carrying an MRP confirms just as well.
    v2_payload = label_facts(
        product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B, panel=PANEL_B,
        mrp_text="MRP ₹100", net_quantity="400 g",
    )
    v2_run = await _seed_ai_run(account_id, v2_payload, schema_version="scan-label.v2")
    confirmed = await app_client.post(
        "/api/v2/scan/label/confirm", headers={**device, **auth(token)},
        json={"barcode": CANDIDATE_B, "ai_run_id": str(v2_run), "client_scan_id": uuid.uuid4().hex},
    )
    assert confirmed.status_code == 201, confirmed.text

    factory = get_sessionmaker()
    async with factory() as session:
        event = (await session.execute(
            select(ScanEvent).where(ScanEvent.barcode == CANDIDATE_B)
        )).scalar_one()
    assert event.label_facts["mrp_text"] == "MRP ₹100"


@pytest.mark.asyncio
async def test_the_transcription_a_person_reviews_shows_the_mrp_it_read(
    db_clean, off_clean, app_client, device, registered_supabase_user, monkeypatch,
):
    """Nothing is stored until a person has seen what we are about to store."""
    from app.domains.ai_gateway.gateway import AIResult
    from app.domains.media import service as media_service

    facts = extraction.ExtractedLabel(
        product_name="Northstar Corn Flakes", ingredients_text=INGREDIENTS_C,
        net_quantity="500 g", mrp_text="MRP ₹120",
    )

    async def fake_transcribe(_session, **_kwargs):
        return AIResult(
            data=facts, run_id=uuid.uuid4(), provider="test", model="test-model",
            prompt_version=extraction.PROMPT_VERSION, schema_version=extraction.SCHEMA_VERSION,
            confidence=None, latency_ms=5, estimated_cost_usd=None,
        )

    monkeypatch.setattr(extraction, "transcribe_label", fake_transcribe)
    monkeypatch.setattr(media_service, "get_owned_asset", fake_transcribe)
    token, _account_id = await registered_supabase_user()

    response = await app_client.post(
        "/api/v2/scan/label/transcribe", headers={**device, **auth(token)},
        json={"barcode": CURRENT, "media_asset_id": str(uuid.uuid4())},
    )
    assert response.status_code == 200, response.text
    # The clause travels to the review screen exactly as it was printed.
    assert response.json()["facts"]["mrp_text"] == "MRP ₹120"
    assert response.json()["stored"] is False


# ---------------------------------------------------------------------------
# A price change is not a reformulation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_price_change_creates_no_new_semantic_label_version(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The formula did not change because the sticker did.

    Semantic versioning tracks what is in the pack. Repricing the same recipe
    must not look like a reformulation, or every price rise would read as a
    changed product.
    """
    first = label_facts(
        product_name="Northstar Corn Flakes", ingredients=INGREDIENTS_C, panel=PANEL_C,
        mrp_text="MRP ₹100",
    )
    second = {**first, "mrp_text": "MRP ₹110"}
    await seed_capture(CURRENT, first)
    await seed_capture(CURRENT, second)

    factory = get_sessionmaker()
    async with factory() as session:
        snapshots = (await session.execute(
            select(LabelSnapshot).where(LabelSnapshot.barcode == CURRENT)
            .order_by(LabelSnapshot.version_number)
        )).scalars().all()
        events = (await session.execute(
            select(ScanEvent).where(ScanEvent.barcode == CURRENT)
            .order_by(ScanEvent.created_at, ScanEvent.id)
        )).scalars().all()

    # One semantic version, two captures, and each capture kept its own price.
    assert [row.version_number for row in snapshots] == [1]
    assert snapshots[0].changed_fields == []
    assert len(events) == 2
    assert [event.label_facts["mrp_text"] for event in events] == ["MRP ₹100", "MRP ₹110"]

    # The fingerprint is blind to price, and so is the changed-field list.
    assert product_service.label_content_fingerprint(first) == \
        product_service.label_content_fingerprint(second)
    assert product_service.label_changed_fields(first, second) == []
    assert "mrp_text" not in product_service.canonical_label_facts(second)
    assert "mrp_text" not in product_service.CONTENT_FACT_FIELDS

    # The newest capture is the commercial authority: ₹110, never ₹100.
    async with factory() as session:
        observation, _missing = await value_service.observe(session, CURRENT)
    assert observation.mrp_rupees == Decimal("110")

    # And a real reformulation still earns a new semantic version.
    reformulated = {**second, "ingredients_text": "maize, sugar, salt, flavouring"}
    await seed_capture(CURRENT, reformulated)
    async with factory() as session:
        versions = (await session.execute(
            select(LabelSnapshot.version_number).where(LabelSnapshot.barcode == CURRENT)
            .order_by(LabelSnapshot.version_number)
        )).scalars().all()
    assert versions == [1, 2]


@pytest.mark.asyncio
async def test_the_newest_capture_is_the_authority_even_when_it_says_less(
    db_clean, off_clean, app_client, device,
):
    """A newer photograph that could not read the price is the current truth.

    Reaching back to an older capture would publish a price we have reason to
    believe is no longer what the pack says.
    """
    factory = get_sessionmaker()
    priced = label_facts(
        product_name="Northstar Corn Flakes", ingredients=INGREDIENTS_C, panel=PANEL_C,
        mrp_text="MRP ₹100",
    )
    await seed_capture(CURRENT, priced)
    async with factory() as session:
        observation, _ = await value_service.observe(session, CURRENT)
    assert observation.mrp_rupees == Decimal("100")

    # A newer capture whose price could not be read.
    await seed_capture(CURRENT, {k: v for k, v in priced.items() if k != "mrp_text"})
    async with factory() as session:
        observation, missing = await value_service.observe(session, CURRENT)
    assert observation is None
    assert missing == "mrp"


@pytest.mark.asyncio
async def test_a_newer_unreadable_pack_size_does_not_borrow_an_older_one(
    db_clean, off_clean, app_client, device,
):
    """The denominator has to come from the same photograph as the price."""
    await seed_capture(CURRENT, label_facts(
        product_name="Northstar Corn Flakes", ingredients=INGREDIENTS_C, panel=PANEL_C,
        mrp_text="MRP ₹100", net_quantity="500 g",
    ))
    await seed_capture(CURRENT, label_facts(
        product_name="Northstar Corn Flakes", ingredients=INGREDIENTS_C, panel=PANEL_C,
        mrp_text="MRP ₹100", net_quantity="family pack",
    ))
    factory = get_sessionmaker()
    async with factory() as session:
        observation, missing = await value_service.observe(session, CURRENT)
    assert observation is None
    assert missing == "quantity"


@pytest.mark.asyncio
async def test_only_a_confirmed_label_capture_counts_as_an_observation(
    db_clean, off_clean, app_client, device,
):
    """A plain barcode scan saw no pack. It cannot state a price."""
    factory = get_sessionmaker()
    async with factory() as session:
        session.add(ScanEvent(
            barcode=CURRENT, outcome=product_service.OUTCOME_OFF,
            client_scan_id=uuid.uuid4().hex,
            label_facts={"mrp_text": "MRP ₹999", "net_quantity": "500 g"},
        ))
        await session.commit()
    async with factory() as session:
        assert await value_service.latest_confirmed_capture(session, CURRENT) is None


# ---------------------------------------------------------------------------
# End to end, through the route a phone actually calls
# ---------------------------------------------------------------------------
async def seed_current(*, mrp_text: str | None = "MRP ₹120", net_quantity: str = "500 g",
                       age: timedelta | None = None) -> None:
    await seed_capture(CURRENT, label_facts(
        product_name="Northstar Corn Flakes", brand="Northstar", ingredients=INGREDIENTS_C,
        panel=PANEL_C, net_quantity=net_quantity, mrp_text=mrp_text,
    ), age=age)
    await seed_off(CURRENT)


async def seed_candidate(
    barcode: str, *, product_name: str, ingredients: str, panel: dict,
    mrp_text: str | None = "MRP ₹100", net_quantity: str = "400 g",
    basis: str = "per_100g", age: timedelta | None = None,
) -> None:
    await seed_off(barcode, categories=CEREAL_CATEGORY_OTHER_PATH)
    await seed_capture(barcode, label_facts(
        product_name=product_name, ingredients=ingredients, panel=panel,
        net_quantity=net_quantity, basis=basis, mrp_text=mrp_text,
    ), age=age)


@pytest.mark.asyncio
async def test_the_full_pack_comparison_reads_as_arithmetic(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The case that proves a smaller number on a pack is not a lower MRP.

    ₹100 looks cheaper than ₹120 until the pack sizes are normalised, and then
    the candidate is the dearer of the two per 100 g. The card reports that
    rather than hiding it, which is the entire reason absolute pack facts are
    published beside the normalised ones.
    """
    await seed_current(mrp_text="MRP ₹120", net_quantity="500 g")
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B, mrp_text="MRP ₹100", net_quantity="400 g",
    )

    body = await verdict(app_client, device)
    assert body["alternative"]["candidate"]["barcode"] == CANDIDATE_B
    assert body["alternative"]["candidate"]["grade"] == "B"

    value = body["value"]
    assert value["policy_version"] == "pack-mrp-value-v1"
    assert value["status"] == "available"
    assert value["reason_key"] == "comparison_available"

    comparison = value["comparison"]
    assert comparison["basis"] == "per_100g"
    assert comparison["current"]["mrp_inr"] == "120.00"
    assert comparison["current"]["quantity"] == {"amount": "500", "unit": "g"}
    assert comparison["current"]["mrp_per_100_inr"] == "24.00"
    assert comparison["candidate"]["mrp_inr"] == "100.00"
    assert comparison["candidate"]["quantity"] == {"amount": "400", "unit": "g"}
    assert comparison["candidate"]["mrp_per_100_inr"] == "25.00"
    # Candidate minus current: the candidate's MRP per 100 g is the higher one.
    assert comparison["difference_inr_per_100"] == "1.00"
    assert comparison["relationship"] == "candidate_higher_mrp_per_100"
    assert comparison["current"]["source"] == "confirmed_pack_label"

    # Re-price the candidate. The science does not move; only the money does.
    await seed_capture(CANDIDATE_B, label_facts(
        product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B, panel=PANEL_B,
        net_quantity="400 g", mrp_text="MRP ₹80",
    ))
    after = await verdict(app_client, device)
    assert after["alternative"] == body["alternative"]
    assert after["value"]["comparison"]["candidate"]["mrp_per_100_inr"] == "20.00"
    assert after["value"]["comparison"]["difference_inr_per_100"] == "-4.00"
    assert after["value"]["comparison"]["relationship"] == "candidate_lower_mrp_per_100"


@pytest.mark.asyncio
async def test_the_public_envelope_exposes_no_internal_identifier(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """A price observation names a product and a date. Never a person."""
    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )
    body = await verdict(app_client, device)
    value = body["value"]

    assert set(value) == {"policy_version", "status", "reason_key", "comparison"}
    assert set(value["comparison"]) == {
        "basis", "current", "candidate", "relationship", "difference_inr_per_100",
    }
    assert set(value["comparison"]["current"]) == {
        "barcode", "mrp_inr", "quantity", "mrp_per_100_inr", "observed_at", "source",
    }

    factory = get_sessionmaker()
    async with factory() as session:
        events = (await session.execute(select(ScanEvent))).scalars().all()
    serialised = str(value)
    for event in events:
        assert str(event.id) not in serialised
        assert str(event.device_id) not in serialised or event.device_id is None
    for leaked in ("account_id", "device_id", "scan_event", "ai_run", "client_scan_id"):
        assert leaked not in serialised, leaked


@pytest.mark.asyncio
async def test_money_reaches_the_wire_as_decimal_strings(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Never a JSON float. Binary floating point cannot hold a rupee exactly."""
    await seed_current(mrp_text="MRP ₹99.99", net_quantity="333 g")
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B, mrp_text="MRP ₹49.50", net_quantity="150 g",
    )
    comparison = (await verdict(app_client, device))["value"]["comparison"]
    for side in ("current", "candidate"):
        assert isinstance(comparison[side]["mrp_inr"], str)
        assert isinstance(comparison[side]["mrp_per_100_inr"], str)
        assert isinstance(comparison[side]["quantity"]["amount"], str)
    assert isinstance(comparison["difference_inr_per_100"], str)
    # Exact arithmetic all the way to one rounding at the boundary.
    assert comparison["current"]["mrp_per_100_inr"] == "30.03"
    assert comparison["candidate"]["mrp_per_100_inr"] == "33.00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("age", "expected_reason"),
    [
        (timedelta(days=29, hours=23, minutes=59), "comparison_available"),
        (timedelta(days=30), "candidate_mrp_observation_stale"),
        (timedelta(days=31), "candidate_mrp_observation_stale"),
    ],
)
async def test_the_observation_window_holds_at_its_boundary(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
    age, expected_reason,
):
    """Server timestamps only. A phone's clock never decides what is recent."""
    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B, age=age,
    )
    body = await verdict(app_client, device)
    # The candidate is still chosen on science; only the money went quiet.
    assert body["alternative"]["candidate"]["barcode"] == CANDIDATE_B
    assert body["value"]["reason_key"] == expected_reason


@pytest.mark.asyncio
async def test_a_stale_current_observation_says_so_in_its_own_words(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    await seed_current(age=timedelta(days=45))
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )
    body = await verdict(app_client, device)
    assert body["value"]["reason_key"] == "current_mrp_observation_stale"
    assert body["value"]["comparison"] is None


@pytest.mark.asyncio
async def test_a_drink_and_a_solid_are_never_compared_by_price(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """A per-100-g comparison needs grams on both sides. No density is assumed."""
    await seed_current(net_quantity="500 g")
    # Same category, better grade, but its pack is stated in millilitres while
    # the scientific basis on both sides is per 100 g.
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B, net_quantity="400 ml",
    )
    body = await verdict(app_client, device)
    assert body["alternative"]["candidate"]["barcode"] == CANDIDATE_B
    assert body["value"]["reason_key"] == "quantity_basis_incompatible"


@pytest.mark.asyncio
async def test_no_alternative_means_no_comparison_to_make(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Step 6B never goes looking for a product Step 6A did not choose."""
    await seed_current()
    body = await verdict(app_client, device)
    assert body["alternative"]["candidate"] is None
    assert body["value"]["status"] == "not_enough_information"
    assert body["value"]["reason_key"] == "no_comparable_alternative"
    assert body["value"]["comparison"] is None


# ---------------------------------------------------------------------------
# Money may not touch the choice
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_cheaper_lesser_product_never_displaces_the_chosen_one(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The temptation this test exists for is real: B is far cheaper than A.

    A is the scientific winner with an eye-watering MRP; B grades lower and
    costs almost nothing. A developer optimising for a shopper's wallet would
    swap them. Selection must not notice money at all.
    """
    await seed_current()
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
        mrp_text="MRP ₹9,999", net_quantity="100 g",
    )
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B, mrp_text="MRP ₹5", net_quantity="500 g",
    )

    body = await verdict(app_client, device)
    # A wins on grade, and keeps winning despite costing a thousand times more.
    assert body["alternative"]["candidate"]["barcode"] == CANDIDATE_A
    assert body["alternative"]["candidate"]["grade"] == "A"
    assert body["value"]["comparison"]["candidate"]["mrp_per_100_inr"] == "9999.00"
    assert body["value"]["comparison"]["relationship"] == "candidate_higher_mrp_per_100"


@pytest.mark.asyncio
async def test_a_candidate_without_a_price_is_still_the_candidate(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """No price-aware fallback. A missing MRP silences money, not the science."""
    await seed_current()
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
        mrp_text=None,
    )
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B, mrp_text="MRP ₹40", net_quantity="500 g",
    )

    body = await verdict(app_client, device)
    assert body["alternative"]["candidate"]["barcode"] == CANDIDATE_A
    assert body["value"]["reason_key"] == "candidate_mrp_unavailable"
    assert body["value"]["comparison"] is None


@pytest.mark.asyncio
async def test_price_moves_nothing_in_the_scientific_verdict(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Every protected key is byte-identical before and after a price appears."""
    protected = (
        "result_contract_version", "grade", "band", "outcome", "decision", "negatives",
        "positives", "lowers", "helps", "components", "evidence", "trace", "confidence",
        "facts_provenance", "official_records", "community_observations", "nutrition",
        "taxonomy", "ingredients", "quantity_guidance", "purity_note", "missing",
        "product_name", "brand", "barcode", "pack_size_g", "basis", "attribution",
        "engine_version", "better_next_action", "physical_pack_context", "alternative",
    )
    await seed_current(mrp_text=None)
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B, mrp_text=None,
    )
    before = await verdict(app_client, device)
    assert before["value"]["status"] == "not_enough_information"

    # The same packs, now photographed with their prices legible.
    await seed_capture(CURRENT, label_facts(
        product_name="Northstar Corn Flakes", brand="Northstar", ingredients=INGREDIENTS_C,
        panel=PANEL_C, net_quantity="500 g", mrp_text="MRP ₹120",
    ))
    await seed_capture(CANDIDATE_B, label_facts(
        product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B, panel=PANEL_B,
        net_quantity="400 g", mrp_text="MRP ₹100",
    ))
    after = await verdict(app_client, device)
    assert after["value"]["status"] == "available"

    for key in protected:
        assert after.get(key) == before.get(key), key
    # A price is the only thing that moved anywhere in the payload.
    assert {key for key in after if after[key] != before.get(key)} == {"value"}


@pytest.mark.asyncio
async def test_a_price_cannot_turn_a_wait_into_a_buy(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Nothing becomes BUY for being inexpensive, or SKIP for being dear."""
    await seed_current(mrp_text="MRP ₹5", net_quantity="1 kg")
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B, mrp_text="MRP ₹9,999", net_quantity="100 g",
    )
    body = await verdict(app_client, device)
    # A near-free current product is still a C, and still WAIT.
    assert body["grade"] == "C"
    assert body["decision"]["action"] == "wait"
    # A ruinously expensive candidate is still a B, and still BUY.
    assert body["alternative"]["candidate"]["grade"] == "B"
    assert body["alternative"]["candidate"]["decision"] == "buy"


# ---------------------------------------------------------------------------
# The layers below, and the ones beside
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_shopper_observations_can_neither_supply_nor_move_a_price(
    db_clean, off_clean, app_client, published_rules, no_off_network,
    registered_supabase_user, public_display,
):
    from app.domains.community.observations import OBSERVATION_INGREDIENTS_DIFFER

    from tests.test_community_reporting import BARCODE as COMMUNITY_BARCODE
    from tests.test_community_reporting import label_facts as community_label_facts
    from tests.test_community_reporting import report, three_reporters

    # The shoppers' own confirmations are the newest captures of this pack, so
    # the price lives on them — which is exactly how a real observation arrives.
    priced = community_label_facts(mrp_text="MRP ₹120", net_quantity="500 g")
    await seed_off(COMMUNITY_BARCODE)
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
        mrp_text="MRP ₹100", net_quantity="400 g",
    )

    shoppers = await three_reporters(app_client, registered_supabase_user, facts=priced)
    headers = shoppers[0][0].headers()
    before = await verdict(app_client, headers, barcode=COMMUNITY_BARCODE)
    assert before["value"]["status"] == "available"
    assert before["value"]["comparison"]["current"]["mrp_inr"] == "120.00"
    signals_before = len(before["community_observations"]["signals"])

    # The same three shoppers, holding the same packs, report a second thing.
    # Community state moves on its own; no new capture, so no new observation.
    for shopper, *_rest in shoppers:
        response = await report(
            app_client, shopper, code=OBSERVATION_INGREDIENTS_DIFFER, barcode=COMMUNITY_BARCODE,
        )
        assert response.status_code in (200, 201), response.text

    after = await verdict(app_client, headers, barcode=COMMUNITY_BARCODE)
    assert len(after["community_observations"]["signals"]) > signals_before
    # A shopper saying something about a pack cannot state, move or silence a price.
    assert after["value"] == before["value"]
    assert after["alternative"] == before["alternative"]


@pytest.mark.asyncio
async def test_an_official_record_can_neither_supply_nor_move_a_price(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
    registered_supabase_user, tmp_path,
):
    from app.domains.official_records import service as official_records

    from tests.test_official_records import LICENCE, data_row, make_export

    batch = "B-6B-1"
    await seed_capture(CURRENT, label_facts(
        product_name="Northstar Corn Flakes", brand="Northstar", ingredients=INGREDIENTS_C,
        panel=PANEL_C, net_quantity="500 g", mrp_text="MRP ₹120",
        fssai_licence=LICENCE, batch_number=batch,
    ))
    await seed_off(CURRENT)
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B, mrp_text="MRP ₹100", net_quantity="400 g",
    )

    before = await verdict(app_client, device)
    assert before["value"]["status"] == "available"
    assert before["official_records"]["records"] == []

    path = make_export(
        tmp_path / f"foscos-{uuid.uuid4().hex}.xlsx",
        rows=[data_row(recall_id=96001, batch=batch, brand="Northstar",
                       product="Northstar Corn Flakes", status="Initiated", termination="NA")],
    )
    factory = get_sessionmaker()
    async with factory() as session:
        await official_records.ingest_recall_xlsx(
            session, path, source_checked_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        )
        await session.commit()

    after = await verdict(app_client, device)
    assert [row["recall_id"] for row in after["official_records"]["records"]] == ["96001"]
    assert after["value"] == before["value"]
    assert after["alternative"] == before["alternative"]


@pytest.mark.asyncio
async def test_a_reference_view_keeps_its_restrictions_and_may_still_state_a_dated_price(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, tmp_path,
):
    """MRP is explicitly a dated observation, so it survives reference mode.

    What must not survive is anything claiming this is the viewer's own packet.
    Step 6B may not loosen a single one of those restrictions.
    """
    from app.domains.official_records import service as official_records

    from tests.test_official_records import LICENCE, data_row, make_export

    batch = "B-6B-REF"
    await seed_current()
    await seed_off(CANDIDATE_B, categories=CEREAL_CATEGORY_OTHER_PATH)
    # A capture owned by nobody on this device, carrying a licence and a lot.
    await seed_capture(CANDIDATE_B, label_facts(
        product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B, panel=PANEL_B,
        net_quantity="400 g", mrp_text="MRP ₹100",
        fssai_licence=LICENCE, batch_number=batch,
    ))
    path = make_export(
        tmp_path / f"foscos-{uuid.uuid4().hex}.xlsx",
        rows=[data_row(recall_id=96002, batch=batch, brand="Sunfield",
                       product="Sunfield Oat Porridge", status="Initiated", termination="NA")],
    )
    factory = get_sessionmaker()
    async with factory() as session:
        await official_records.ingest_recall_xlsx(
            session, path, source_checked_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        )
        await session.commit()

    reference = await verdict(app_client, device, barcode=CANDIDATE_B, physical_pack=False)
    assert reference["physical_pack_context"] is False
    # Step 4 stays suppressed. Step 6B did not weaken it.
    assert reference["official_records"]["records"] == []
    assert [s for s in reference["community_observations"]["signals"]
            if s["scope"] == "batch"] == []
    # And the ordinary read of the same pack still shows the recall.
    ordinary = await verdict(app_client, device, barcode=CANDIDATE_B)
    assert [row["recall_id"] for row in ordinary["official_records"]["records"]] == ["96002"]


# ---------------------------------------------------------------------------
# Free, private, and writing nothing
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_anonymous_device_sees_the_same_comparison(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
    registered_supabase_user,
):
    """Reading an MRP comparison is free. No account, entitlement or profile."""
    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )
    anonymous = (await verdict(app_client, device))["value"]

    token, _account_id = await registered_supabase_user()
    claimed = await app_client.post("/api/v2/scan/device/claim", headers={**device, **auth(token)})
    assert claimed.status_code == 200, claimed.text
    signed_in = (await verdict(app_client, {**device, **auth(token)}))["value"]

    assert anonymous["status"] == "available"
    assert anonymous == signed_in


@pytest.mark.asyncio
async def test_reading_a_price_writes_nothing_anywhere(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Every request recomputes from live scan events. Nothing is cached."""
    from app.shared.database.registry import Base

    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )

    async def counts():
        factory = get_sessionmaker()
        async with factory() as session:
            return {
                name: int(await session.scalar(select(func.count()).select_from(table)) or 0)
                for name, table in sorted(Base.metadata.tables.items())
            }

    async def store_a_rows():
        factory = get_off_sessionmaker()
        async with factory() as session:
            rows = (await session.execute(
                select(OffProduct).order_by(OffProduct.barcode)
            )).scalars().all()
            return [(r.barcode, r.categories, r.countries, r.fetched_at) for r in rows]

    before_b, before_a = await counts(), await store_a_rows()
    assert any(before_b.values())

    body = await verdict(app_client, device)
    assert body["value"]["status"] == "available"
    assert await counts() == before_b
    assert await store_a_rows() == before_a


@pytest.mark.asyncio
async def test_no_price_field_reached_the_open_food_facts_store(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The ODbL wall does not move for money either."""
    from app.domains.off.wall import OFF_FIELDS

    for banned in ("price", "mrp", "retailer_price", "unit_price", "cost"):
        assert not any(banned in field for field in OFF_FIELDS), banned
    for column in OffProduct.__table__.columns:
        for banned in ("price", "mrp", "cost"):
            assert banned not in column.name, column.name

    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )
    body = await verdict(app_client, device)
    assert body["value"]["comparison"]["current"]["mrp_inr"] == "120.00"

    # The price lives in the confirmed capture, and nowhere near Store A.
    factory = get_off_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(select(OffProduct))).scalars().all()
    assert all("120" not in str(row.__dict__) for row in rows)


@pytest.mark.asyncio
async def test_computing_a_price_comparison_asks_no_ai_anything(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, monkeypatch,
):
    """The model transcribed a clause once. Comparing is deterministic code."""
    from app.domains.ai_gateway import gateway

    async def forbidden(*args, **kwargs):
        raise AssertionError("Step 6B reached the AI gateway")

    monkeypatch.setattr(gateway, "run_structured", forbidden)

    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )
    body = await verdict(app_client, device)
    assert body["value"]["comparison"]["candidate"]["mrp_per_100_inr"] == "25.00"

    factory = get_sessionmaker()
    async with factory() as session:
        assert (await session.execute(select(AIRun))).scalars().all() == []


@pytest.mark.asyncio
async def test_an_account_can_export_the_pack_prices_it_captured(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """A person's own price observations are their data, and travel with them."""
    from app.domains.privacy import export as privacy_export

    token, account_id = await registered_supabase_user()
    await seed_capture(
        CURRENT,
        label_facts(product_name="Northstar Corn Flakes", ingredients=INGREDIENTS_C,
                    panel=PANEL_C, net_quantity="500 g", mrp_text="MRP ₹120"),
        account_id=account_id,
    )
    # Somebody else's capture of another pack.
    other_token, other_account = await registered_supabase_user()
    del other_token
    await seed_capture(
        CANDIDATE_B,
        label_facts(product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
                    panel=PANEL_B, net_quantity="400 g", mrp_text="MRP ₹100"),
        account_id=other_account,
    )

    factory = get_sessionmaker()
    async with factory() as session:
        bundle = await privacy_export.build_export(session, account_id)
    serialised = str(bundle)
    assert "MRP ₹120" in serialised, "the account cannot export its own captured MRP"
    # And never another person's observation.
    assert "MRP ₹100" not in serialised


@pytest.mark.asyncio
async def test_deleting_an_account_removes_its_price_observation(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
    registered_supabase_user, monkeypatch,
):
    """Through the real deletion state machine, not a hand-written DELETE.

    The newest remaining confirmed capture becomes the authority afterwards —
    which is the ordinary rule, not a reach behind a deletion.
    """
    from app.domains.privacy import deletion_service

    async def no_supabase(_account_id):
        return None

    async def no_storage(*_args, **_kwargs):
        return None

    monkeypatch.setattr(deletion_service, "_delete_supabase_identity", no_supabase)
    monkeypatch.setattr(deletion_service, "_remove_external_integrations", no_storage)

    owner_token, owner_account = await registered_supabase_user()
    other_token, other_account = await registered_supabase_user()
    del owner_token, other_token

    await seed_off(CURRENT)
    # The older observation, owned by somebody who is staying.
    await seed_capture(
        CURRENT,
        label_facts(product_name="Northstar Corn Flakes", ingredients=INGREDIENTS_C,
                    panel=PANEL_C, net_quantity="500 g", mrp_text="MRP ₹110"),
        account_id=other_account, age=timedelta(days=2),
    )
    # The newest observation, owned by the account about to be deleted.
    await seed_capture(
        CURRENT,
        label_facts(product_name="Northstar Corn Flakes", ingredients=INGREDIENTS_C,
                    panel=PANEL_C, net_quantity="500 g", mrp_text="MRP ₹120"),
        account_id=owner_account,
    )
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )

    before = await verdict(app_client, device)
    assert before["value"]["comparison"]["current"]["mrp_inr"] == "120.00"

    factory = get_sessionmaker()
    async with factory() as session:
        await deletion_service.request_deletion(session, owner_account)
        await session.commit()
        await deletion_service.drain_all(session)
        await session.commit()

    async with factory() as session:
        remaining = (await session.execute(
            select(ScanEvent).where(ScanEvent.barcode == CURRENT)
        )).scalars().all()
    assert [event.account_id for event in remaining] == [other_account]

    after = await verdict(app_client, device)
    # The observation that is now newest — not a resurrection of the deleted one.
    assert after["value"]["comparison"]["current"]["mrp_inr"] == "110.00"
    assert "120" not in str(after["value"])
