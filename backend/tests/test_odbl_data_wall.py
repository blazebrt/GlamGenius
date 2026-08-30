"""The ODbL wall between Store A and Store B.

Open Food Facts is ODbL licensed with a share-alike clause: combining their
database with ours into one derived database would oblige us to publish the
combined thing openly — the absorption knowledge base, the thresholds, the
scores, all of it.

These tests are the enforcement. They are written to fail loudly and to explain
why, because whoever trips one will be in the middle of a change that looks
perfectly reasonable.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from app.domains.off import models as off_models
from app.domains.off.attribution import ATTRIBUTION_TEXT, attribution
from app.domains.off.client import request_headers, user_agent
from app.domains.off.export import DATA_FILE, LICENSE_FILE, MANIFEST_FILE, export
from app.domains.off.join import join_on_barcode
from app.domains.off.models import OffBase, OffProduct
from app.domains.off.store import create_off_schema, get_off_engine, get_off_sessionmaker
from app.domains.off.wall import (
    OFF_FIELDS,
    PROPRIETARY_MARKERS,
    ProprietaryFieldError,
    assert_no_cross_store_foreign_keys,
    assert_no_proprietary_fields,
    guard_off_session,
)
from app.shared.database.registry import Base
from sqlalchemy import Column, String, Table


@pytest_asyncio.fixture
async def off_clean():
    """Empty Store A before a test.

    Store A needs its own cleanup because the ordinary ``db_clean`` fixture
    truncates ``Base.metadata`` tables and Store A is deliberately not in that
    metadata. Needing a second fixture is not friction to be smoothed away — it
    is the separation showing up in the test suite, and if this ever becomes
    unnecessary the wall has been breached.
    """
    from sqlalchemy import text

    await create_off_schema()
    engine = get_off_engine()
    async with engine.begin() as conn:
        names = ", ".join(
            f'"{table.schema}"."{table.name}"' for table in reversed(OffBase.metadata.sorted_tables)
        )
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield


# ---------------------------------------------------------------------------
# No proprietary field can be written to Store A
# ---------------------------------------------------------------------------
def test_store_a_holds_only_open_food_facts_fields():
    """The acceptance criterion, checked against the live schema."""
    assert_no_proprietary_fields()


def test_a_proprietary_column_added_to_store_a_is_rejected():
    """Simulate the mistake: somebody adds a score column to an OFF table."""
    table = Table(
        "off_products_with_a_mistake", OffBase.metadata,
        Column("barcode", String(64), primary_key=True),
        Column("asli_score", String(4)),          # proprietary
    )
    try:
        with pytest.raises(ProprietaryFieldError) as caught:
            assert_no_proprietary_fields()
        message = str(caught.value)
        assert "asli_score" in message
        assert "Store B" in message, "the error does not say where the field belongs"
    finally:
        OffBase.metadata.remove(table)
    assert_no_proprietary_fields()


@pytest.mark.parametrize(
    "field",
    ["asli_score", "verdict", "absorption_percent", "evidence_claim_id", "account_id",
     "user_profile", "decision_memory", "confidence", "risk_tier", "elemental_percent"],
)
def test_every_kind_of_proprietary_field_is_recognised(field):
    """Each of these is a real column name from elsewhere in this codebase."""
    assert field not in OFF_FIELDS, f"{field} is on the Open Food Facts allowlist"
    assert any(marker in field.lower() for marker in PROPRIETARY_MARKERS), (
        f"{field} would not be recognised as proprietary if somebody added it"
    )


@pytest.mark.asyncio
async def test_the_write_guard_refuses_a_proprietary_value_at_flush(off_clean):
    """The last line of defence, on the real write path.

    A value set dynamically — a dict splatted from a proprietary record — is
    invisible to any static check. The session guard still catches it.
    """
    factory = get_off_sessionmaker()
    async with factory() as session:
        guard_off_session(session.sync_session)
        product = OffProduct(barcode="8901234567890", product_name="Test biscuit")
        # Exactly how it would happen: someone attaches our score to their record.
        product.asli_score = "D"
        session.add(product)
        with pytest.raises(ProprietaryFieldError) as caught:
            await session.flush()
    assert "asli_score" in str(caught.value)


@pytest.mark.asyncio
async def test_an_ordinary_open_food_facts_record_still_writes(off_clean):
    """The guard must not block the thing it exists to protect."""
    factory = get_off_sessionmaker()
    async with factory() as session:
        guard_off_session(session.sync_session)
        session.add(OffProduct(
            barcode="8901234567891", product_name="Test biscuit", brands="Test",
            ingredients_text="wheat flour, sugar, palm oil",
            nutriments={"sugars_100g": 22.5}, fetched_at=datetime.now(UTC),
        ))
        await session.flush()
        await session.commit()


# ---------------------------------------------------------------------------
# The two stores are structurally separate
# ---------------------------------------------------------------------------
def test_store_a_lives_in_its_own_schema():
    """Even sharing a server, the two stores are separate namespaces."""
    from app.domains.off.models import OFF_SCHEMA

    for table in OffBase.metadata.tables.values():
        assert table.schema == OFF_SCHEMA, f"{table.name} is not in the Open Food Facts schema"
    for table in Base.metadata.tables.values():
        assert table.schema != OFF_SCHEMA, f"{table.name} is in Store A's schema"


def test_the_application_migrations_ignore_store_a():
    """A migration written for the product must not be able to reach Store A."""
    from pathlib import Path

    from app.domains.off.models import OFF_SCHEMA

    env_path = Path(__file__).resolve().parents[1] / "migrations" / "env.py"
    source = env_path.read_text(encoding="utf-8")
    assert "include_object" in source, "the Alembic chain does not filter anything"
    assert "OFF_SCHEMA" in source, "the Alembic chain does not know about Store A's schema"
    assert OFF_SCHEMA == "off_data"


def test_store_a_tables_are_not_in_the_application_metadata():
    """If they shared metadata they would share migrations and foreign keys."""
    shared = set(OffBase.metadata.tables) & set(Base.metadata.tables)
    assert not shared, f"these tables are in both stores: {shared}"


def test_store_a_has_no_foreign_key_out_of_itself():
    assert_no_cross_store_foreign_keys()


def test_no_application_table_points_into_store_a():
    """A foreign key the other way ties the two together just as tightly."""
    off_tables = set(OffBase.metadata.tables)
    offenders = [
        f"{name} -> {fk.target_fullname}"
        for name, table in Base.metadata.tables.items()
        for fk in table.foreign_keys
        if fk.target_fullname.split(".")[0] in off_tables
    ]
    assert not offenders, f"Store B references Store A: {offenders}"


def test_the_stores_are_joined_only_on_barcode():
    """The join holds the two halves apart rather than merging them."""
    joined = join_on_barcode(
        "8901234567890",
        {"product_name": "Test biscuit", "nutriments": {"sugars_100g": 22.5}},
        {"asli_score": "D", "verdict": "wait"},
    )
    body = joined.as_dict()
    assert body["open_food_facts"]["product_name"] == "Test biscuit"
    assert body["glamgenius"]["asli_score"] == "D"
    # The halves must stay in their own keys, never flattened into one object.
    assert "asli_score" not in body["open_food_facts"]
    assert "product_name" not in body["glamgenius"]
    assert body["attribution"]["text"] == ATTRIBUTION_TEXT


# ---------------------------------------------------------------------------
# The export job
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_export_produces_a_valid_downloadable_dataset(off_clean, tmp_path):
    factory = get_off_sessionmaker()
    async with factory() as session:
        session.add_all([
            OffProduct(barcode="890000000001", product_name="Biscuit", brands="A",
                       nutriments={"sugars_100g": 22.5}),
            OffProduct(barcode="890000000002", product_name="Namkeen", brands="B",
                       nutriments={"salt_100g": 2.1}),
        ])
        await session.commit()

    manifest = await export(tmp_path)

    data = (tmp_path / DATA_FILE).read_text(encoding="utf-8").strip().splitlines()
    assert len(data) == manifest["record_count"] == 2
    for line in data:
        record = json.loads(line)          # valid JSON on every line
        assert set(record) <= OFF_FIELDS, f"the export leaked {set(record) - OFF_FIELDS}"

    licence = (tmp_path / LICENSE_FILE).read_text(encoding="utf-8")
    assert ATTRIBUTION_TEXT in licence
    assert "Open Database License" in licence

    written = json.loads((tmp_path / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert written["contains_proprietary_data"] is False
    assert written["sha256"] and written["license_url"]


@pytest.mark.asyncio
async def test_the_export_refuses_to_publish_a_proprietary_field(off_clean, tmp_path, monkeypatch):
    """If a field somehow reached Store A, the export must not redistribute it."""
    from app.domains.off import export as export_module

    factory = get_off_sessionmaker()
    async with factory() as session:
        session.add(OffProduct(barcode="890000000003", product_name="Biscuit"))
        await session.commit()

    def _leaky(product):
        return {"barcode": product.barcode, "asli_score": "D"}

    monkeypatch.setattr(export_module, "_record", _leaky)
    with pytest.raises(ProprietaryFieldError):
        await export_module.export(tmp_path)


# ---------------------------------------------------------------------------
# Attribution and the required User-Agent
# ---------------------------------------------------------------------------
def test_the_attribution_wording_is_exactly_what_odbl_requires():
    assert ATTRIBUTION_TEXT == (
        "Contains information from Open Food Facts, made available under the "
        "Open Database License (ODbL)"
    )
    block = attribution()
    assert block["license_url"].startswith("https://opendatacommons.org/")
    assert block["source_url"].startswith("https://world.openfoodfacts.org")


def test_every_api_call_identifies_itself():
    """Open Food Facts states this as a condition of using their API."""
    headers = request_headers()
    assert "User-Agent" in headers
    agent = headers["User-Agent"]
    assert agent.startswith("GlamGenius/"), agent
    assert "(" in agent and ")" in agent, "the User-Agent carries no contact detail"
    assert agent == user_agent()


def test_the_user_agent_is_not_left_as_a_default_library_string():
    assert "python-httpx" not in user_agent().lower()
    assert "unknown" not in user_agent().lower()


# ---------------------------------------------------------------------------
# The model surface itself
# ---------------------------------------------------------------------------
def test_the_off_product_table_matches_the_allowlist_exactly():
    columns = {c.name for c in off_models.OffProduct.__table__.columns}
    assert columns == OFF_FIELDS, (
        f"only on the table: {columns - OFF_FIELDS}; only on the allowlist: {OFF_FIELDS - columns}"
    )
