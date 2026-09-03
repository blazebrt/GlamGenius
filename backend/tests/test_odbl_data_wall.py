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
from sqlalchemy import select as sa_select


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

    # The advertised digest has to be the digest of the file people download,
    # or nobody can verify what we published.
    import hashlib
    on_disk = hashlib.sha256((tmp_path / DATA_FILE).read_bytes()).hexdigest()
    assert written["sha256"] == on_disk


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


# ---------------------------------------------------------------------------
# The comparable alternative (Step 6A) reads both stores and combines neither
# ---------------------------------------------------------------------------
#: The Store B tables a barcode scan writes to. These are where a cached
#: Open Food Facts value would land if anybody ever decided the runtime join was
#: too slow, so they are the ones worth pinning.
#:
#: Provenance decides what is a breach, not the name of a column — a nutrition
#: panel somebody photographed is ours however much it resembles theirs, which
#: is why ``product_label_facts`` names its columns ``printed_*``. What follows
#: is therefore scoped to the scan tables and to the field names that could only
#: have come from the catalogue.
_SCAN_TABLES = ("product_records", "scan_events", "product_label_snapshots")

#: Names that could only be a copy of an Open Food Facts field, including the
#: two a category cache would introduce.
_OFF_DERIVED_COLUMN_NAMES = frozenset({
    "brands", "nutriments", "categories", "category_key", "canonical_category",
    "countries", "availability", "image_url", "off_last_modified_t",
    "off_product_name", "off_brand", "alternative_barcode",
})


def test_the_scan_tables_have_grown_no_open_food_facts_column():
    """The alternative reads their category and country. It stores neither.

    A ``canonical_category`` or an ``availability`` column on a scan table would
    be an Open Food Facts value written into one of ours — a derived database,
    and the share-alike obligation with it. That is the change that looks like a
    performance improvement and is a licence breach, so the comparison is
    recomputed per request and the column never has to exist.
    """
    present = [name for name in _SCAN_TABLES if name in Base.metadata.tables]
    assert present == list(_SCAN_TABLES), (
        f"a scan table was renamed or removed; update this test: {present}"
    )
    offenders = [
        f"{name}.{column.name}"
        for name in present
        for column in Base.metadata.tables[name].columns
        if column.name in _OFF_DERIVED_COLUMN_NAMES
    ]
    assert not offenders, (
        "these Store B columns hold Open Food Facts fields: " + ", ".join(offenders)
    )


def test_no_store_b_table_caches_an_alternative():
    """Runtime computation is allowed; a persisted alternative is not.

    A table of chosen alternatives is a join between the two stores written
    down. Durable decision memory belongs to its own milestone and to Store B
    data of our own, not to a cached pairing of their rows with our grades.
    """
    offenders = [
        name for name in Base.metadata.tables
        if "alternative" in name.lower() or "comparable" in name.lower()
    ]
    assert not offenders, f"the alternative was persisted into Store B: {offenders}"


def test_the_alternative_domain_reads_store_a_and_never_writes_it():
    """The one contact point is a read, and the code says so structurally."""
    import ast
    import inspect

    from app.domains.alternatives import service as alternatives_service

    source = inspect.getsource(alternatives_service)
    tree = ast.parse(source)
    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    # Nothing that could put a row into either store.
    for writer in ("add", "add_all", "commit", "flush", "merge", "delete", "bulk_save_objects"):
        assert writer not in called, f"the alternative service calls session.{writer}()"
    # And it reaches Store A through Store A's own guarded sessionmaker.
    assert "get_off_sessionmaker" in source


@pytest.mark.asyncio
async def test_the_alternative_pairing_exists_only_for_one_response(db_clean, off_clean):
    """Their half and ours are assembled together and thrown away together.

    The candidate a shopper sees is *discovered* through an Open Food Facts
    category and country listing, and *described* by a label somebody confirmed
    against the pack. That pairing is a response, not a record: it is built in
    memory, returned, and never written down. The proof is that Store A is
    byte-identical afterwards and neither store gained a field of the other's.
    """
    import uuid as uuid_module
    from dataclasses import replace as dataclass_replace

    from app.domains.alternatives.service import comparable_alternative_envelope
    from app.domains.nutrition.grading import from_scan, grade_product
    from app.domains.nutrition.grading.production_rules import (
        STATUS_PUBLISHED,
        ProductionRuleset,
        candidate_ruleset,
        enforce_published_required_rules,
    )
    from app.domains.product import service as product_service
    from app.domains.product.models import ScanEvent
    from app.shared.database.sql import get_sessionmaker

    current_facts = {
        "product_name": "Northstar Corn Flakes", "brand": "Northstar",
        "ingredients_text": "maize, sugar, salt, flavouring, emulsifier (ins 322)",
        "nutrition_per_100g": {
            "energy_kcal": "380", "sugars_g": "8", "saturated_fat_g": "1",
            "salt_g": "0.5", "protein_g": "7", "fibre_g": "3",
        },
        "nutrition_basis": "per_100g", "net_quantity": "200 g",
    }
    candidate_facts = {
        "product_name": "Sunfield Oat Porridge", "brand": "Sunfield",
        "ingredients_text": "whole grain oats, salt",
        "nutrition_per_100g": {
            "energy_kcal": "370", "sugars_g": "2", "saturated_fat_g": "1.2",
            "salt_g": "0.3", "protein_g": "12", "fibre_g": "9",
        },
        "nutrition_basis": "per_100g", "net_quantity": "200 g",
    }

    # Store A: the discovery universe. Category and country, nothing of ours.
    factory = get_off_sessionmaker()
    async with factory() as session:
        session.add(OffProduct(
            barcode="8901000000001", product_name="Catalogue Name",
            categories="Foods, Breakfasts, Breakfast cereals", countries="India",
            fetched_at=datetime.now(UTC),
        ))
        session.add(OffProduct(
            barcode="8901000000002", product_name="Another Catalogue Name",
            categories="Plant foods, Breakfast cereals", countries="India",
            fetched_at=datetime.now(UTC),
        ))
        await session.commit()

    # Store B: the confirmed packs. Independently sourced from photographs, and
    # named for what they are rather than borrowed from the catalogue.
    store_b = get_sessionmaker()
    snapshots = {}
    async with store_b() as session:
        for barcode, facts in (
            ("8901000000001", current_facts), ("8901000000002", candidate_facts),
        ):
            held = ScanEvent(
                barcode=barcode, outcome="label_captured",
                client_scan_id=uuid_module.uuid4().hex, label_facts=facts,
            )
            session.add(held)
            await session.flush()
            snapshots[barcode] = await product_service.store_label_snapshot(
                session, barcode=barcode, facts=facts, device_id=None, scan_event_id=held.id,
            )
        await session.commit()

    published = ProductionRuleset(provenance={
        rule_id: dataclass_replace(row, status=STATUS_PUBLISHED)
        for rule_id, row in candidate_ruleset().provenance.items()
    })
    product = from_scan.build_confirmed_label(barcode="8901000000001", facts=current_facts)
    result = enforce_published_required_rules(grade_product(product), published)

    async def store_a_rows():
        async with factory() as session:
            rows = (await session.execute(
                sa_select(OffProduct).order_by(OffProduct.barcode)
            )).scalars().all()
            return [
                (row.barcode, row.product_name, row.brands, row.ingredients_text,
                 row.nutriments, row.categories, row.countries, row.image_url,
                 row.quantity, row.off_last_modified_t, row.fetched_at)
                for row in rows
            ]

    before = await store_a_rows()
    async with store_b() as session:
        current_snapshot = await product_service.latest_label_snapshot(session, "8901000000001")
        envelope = await comparable_alternative_envelope(
            session,
            barcode="8901000000001", current_snapshot=current_snapshot,
            current_product=product, current_result=result, ruleset=published,
        )
    # The two halves did meet — that is the feature.
    assert envelope["candidate"]["product_name"] == "Sunfield Oat Porridge"
    assert envelope["candidate"]["grade"] == "B"
    assert envelope["candidate"]["attribution"]["text"] == ATTRIBUTION_TEXT
    # Store A is exactly as it was. No grade of ours was written beside their
    # row, and no row of theirs was copied anywhere.
    assert await store_a_rows() == before
    # And no Open Food Facts field leaked into the confirmed pack beside it.
    stored = snapshots["8901000000002"].facts
    assert "Another Catalogue Name" not in str(stored)
    assert "categories" not in stored
    assert "countries" not in stored
