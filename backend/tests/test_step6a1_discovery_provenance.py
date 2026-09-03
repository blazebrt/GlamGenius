"""Step 6A.1 — the source-semantics, ranking and pack-proof corrections.

Step 6A.1 first shipped, then had to correct itself. This module pins the
corrected behaviour so it cannot regress. Four things were wrong:

1. **``categories_tags`` was treated as the comparison authority.** It is the
   *lossy, search-only* representation: an unmatched entry is deaccented and
   lowercased, so two distinct source categories can collapse onto one token and
   **manufacture** a false match. The authority is now the non-lossy
   ``categories_hierarchy``.
2. **The comparison key was an unbounded joined string** used directly as a
   B-tree index key. It is now a fixed 64-character SHA-256 fingerprint, and a
   candidate's actual hierarchy is re-compared exactly before it is graded, so a
   collision in the digest can never manufacture a match.
3. **A provisional winner could be published when the work budget ran out** with
   qualifying rows still unread. A candidate is now published only when its
   global rank is established.
4. **Pack authority accepted any newest event carrying dict ``label_facts``.** It
   now requires the canonical ``label_captured`` outcome *and* a confirmation
   ``ai_run_id`` — the provenance of the server-authorised path.

Every failure fails closed: false negatives are acceptable, manufactured
equality is not.
"""
from __future__ import annotations

import ast
import inspect
import json
import pathlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.domains.alternatives import category as category_module
from app.domains.alternatives import observability
from app.domains.alternatives import policy as policy_module
from app.domains.alternatives import service as alternatives_service
from app.domains.off import taxonomy as off_taxonomy
from app.domains.off.models import OFF_SCHEMA, OffBase, OffProduct
from app.domains.off.store import create_off_schema, get_off_engine, get_off_sessionmaker
from app.domains.off.wall import OFF_CANONICAL_FIELDS, OFF_FIELDS, OFF_PUBLISHED_FIELDS
from app.domains.product import pack_context
from app.domains.product import service as product_service
from app.domains.product.models import ScanDevice, ScanEvent
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import text

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "off_payloads"


def payload(name: str) -> dict:
    """One frozen Open Food Facts payload. Never a live call — see SOURCES.md."""
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


@pytest_asyncio.fixture
async def off_clean():
    await create_off_schema()
    async with get_off_engine().begin() as conn:
        names = ", ".join(
            f'"{t.schema}"."{t.name}"' for t in reversed(OffBase.metadata.sorted_tables)
        )
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield


# ---------------------------------------------------------------------------
# The frozen fixtures, and the failure modes of the field they replace
# ---------------------------------------------------------------------------
def test_every_documented_case_has_a_frozen_payload_and_no_live_call():
    """Every case is on disk, with its provenance written down beside it."""
    index = json.loads((FIXTURE_DIR / "case_index.json").read_text())
    assert len(index) == 12
    for name in index:
        assert (FIXTURE_DIR / f"{name}.json").is_file(), name
    sources = (FIXTURE_DIR / "SOURCES.md").read_text()
    assert "2026-09-03" in sources
    assert "raw.githubusercontent.com/openfoodfacts/openfoodfacts-server" in sources
    assert "not captures of live API responses" in sources


def test_the_authority_is_the_non_lossy_hierarchy_field():
    """The corrected field choice, pinned against both wrong ones.

    ``categories_hierarchy`` is compared; ``categories_tags`` is not read for
    comparison at all, and the raw ``categories`` text never was.
    """
    src = inspect.getsource(off_taxonomy)
    assert "categories_hierarchy" in src
    # The comparison functions take a hierarchy; there is no categories_tags key.
    assert not hasattr(off_taxonomy, "category_key")
    assert not hasattr(off_taxonomy, "canonical_tags")
    assert hasattr(off_taxonomy, "canonical_hierarchy")
    assert hasattr(off_taxonomy, "category_fingerprint")


def test_regression_a_the_same_product_kind_edited_in_another_language_still_matches():
    """Failure mode A: the raw text is the last editor's language.

    Two products Open Food Facts classifies identically — one last edited in
    English, one in French. The hierarchies match; the raw text does not.
    """
    english = payload("01_indian_breakfast_cereal")
    french = payload("02_same_kind_edited_in_french")
    assert off_taxonomy.same_category(
        english["categories_hierarchy"], french["categories_hierarchy"],
    )
    assert english["categories"] != french["categories"]


def test_regression_b_two_different_products_sharing_a_raw_leaf_do_not_match():
    """Failure mode B: the raw leaf collides, and a false comparison follows."""
    cereal = payload("01_indian_breakfast_cereal")
    bar = payload("03_raw_leaf_collision")

    def raw_leaf(row):
        return row["categories"].split(",")[-1].strip().casefold()

    assert raw_leaf(cereal) == raw_leaf(bar), "the fixture must actually collide on raw text"
    assert not off_taxonomy.same_category(
        cereal["categories_hierarchy"], bar["categories_hierarchy"],
    )


def test_regression_the_lossy_categories_tags_can_collide_but_the_hierarchy_does_not():
    """The proof that the field correction matters, not just the theory.

    Two genuinely different products (11 and 12) whose ``categories_tags`` are
    byte-identical because the lossy indexing deaccented "Müsli" and "Musli" to
    the same token — yet whose non-lossy ``categories_hierarchy`` differ. A
    comparison on ``categories_tags`` would call them the same; the comparison we
    actually run says they are not.
    """
    a = payload("11_lossy_collision_a")
    b = payload("12_lossy_collision_b")
    # The lossy tags really do collide.
    assert a["categories_tags"] == b["categories_tags"]
    # The non-lossy hierarchies really do differ.
    assert a["categories_hierarchy"] != b["categories_hierarchy"]
    # And the authority we use refuses the false match.
    assert not off_taxonomy.same_category(
        a["categories_hierarchy"], b["categories_hierarchy"],
    )
    assert off_taxonomy.category_fingerprint(a["categories_hierarchy"]) != (
        off_taxonomy.category_fingerprint(b["categories_hierarchy"])
    )


def test_an_unmatched_hierarchy_entry_is_kept_as_is():
    """The non-lossy contract: an unmatched entry keeps its exact source string.

    Re-normalising it here would rebuild the lossy form and could re-introduce
    the collision this milestone removes.
    """
    row = payload("08_unmatched_hierarchy_entry")
    canonical = off_taxonomy.canonical_hierarchy(row["categories_hierarchy"])
    assert "fr:Chikki Maison" in canonical  # mixed case and spacing preserved
    # The lossy tag form for the same entry is different, and is never consulted.
    assert "fr:chikki-maison" in row["categories_tags"]


def test_regression_c_india_is_found_through_the_taxonomy_not_a_list_of_spellings():
    """Failure mode C: a closed set of English spellings misses the taxonomy."""
    row = payload("04_india_only_in_the_taxonomy")
    assert row["countries"] == "Inde"
    assert off_taxonomy.listed_for_india(row["countries_tags"])
    assert not off_taxonomy.listed_for_india(row["countries"])


def test_regression_d_the_raw_country_text_cannot_overrule_the_taxonomy():
    """Failure mode D: raw text says India, the taxonomy does not."""
    row = payload("05_raw_says_india_taxonomy_does_not")
    assert row["countries"] == "India"
    assert not off_taxonomy.listed_for_india(row["countries_tags"])


@pytest.mark.parametrize(
    ("case", "has_key"),
    [
        ("01_indian_breakfast_cereal", True),
        ("06_no_category_hierarchy", False),
        ("07_empty_category_hierarchy", False),
        ("08_unmatched_hierarchy_entry", True),
        ("10_malformed_hierarchy", False),
    ],
)
def test_a_classification_is_present_or_it_is_absent(case, has_key):
    assert (off_taxonomy.category_fingerprint(payload(case)["categories_hierarchy"]) is not None) is has_key


def test_a_lookalike_country_is_a_different_country():
    assert not off_taxonomy.listed_for_india(payload("09_lookalike_country")["countries_tags"])


def test_compared_to_category_exists_and_is_deliberately_unused():
    """The research record, corrected. It EXISTS, and we do not use it.

    An earlier draft claimed ``compared_to_category`` appeared nowhere in the
    schema. It does — with a TODO for how it is chosen — so the documentation
    must acknowledge it, and the code must not read it.
    """
    assert "compared_to_category" in inspect.getsource(off_taxonomy)
    assert "compared_to_category" in (FIXTURE_DIR / "SOURCES.md").read_text()
    # It is documented, not consumed: no runtime module reads that field.
    for module in (off_taxonomy, alternatives_service, product_service):
        tree = ast.parse(inspect.getsource(module))
        literals = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        # It may appear inside a docstring (one big constant) but never as a
        # dict key / field access we read. Guard the access form specifically.
        assert 'payload.get("compared_to_category")' not in inspect.getsource(module)
        assert not any(lit == "compared_to_category" for lit in literals), module.__name__


# ---------------------------------------------------------------------------
# Store A schema evolution: additive, idempotent, and never a backfill
# ---------------------------------------------------------------------------
async def _columns() -> set[str]:
    async with get_off_engine().begin() as conn:
        rows = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = 'off_products'",
        ), {"schema": OFF_SCHEMA})
        return {row[0] for row in rows}


@pytest.mark.asyncio
async def test_the_final_store_a_model_carries_no_obsolete_column(db_clean, off_clean):
    """The lossy array is not stored — the model is limited to what is used."""
    columns = {c.name for c in OffProduct.__table__.columns}
    assert "categories_hierarchy" in columns
    assert "categories_tags" not in columns, "the lossy array must not be stored"
    assert "off_category_key" in columns and "off_listed_for_india" in columns


@pytest.mark.asyncio
async def test_the_store_a_schema_converges_from_any_starting_point(db_clean, off_clean):
    """Fresh, half-migrated and already-current all end in the same place."""
    expected = {column.name for column in OffProduct.__table__.columns}
    assert await _columns() == expected

    await create_off_schema()  # idempotent
    assert await _columns() == expected

    # A table created by an earlier release, missing the newer columns.
    async with get_off_engine().begin() as conn:
        for column in sorted(OFF_CANONICAL_FIELDS | {"categories_hierarchy", "countries_tags"}):
            await conn.execute(text(
                f'ALTER TABLE "{OFF_SCHEMA}".off_products DROP COLUMN IF EXISTS {column}',
            ))
        await conn.execute(text(f'DROP INDEX IF EXISTS "{OFF_SCHEMA}".ix_off_products_discovery'))
    assert await _columns() != expected

    await create_off_schema()
    assert await _columns() == expected


@pytest.mark.asyncio
async def test_an_old_row_is_left_alone_rather_than_backfilled(db_clean, off_clean):
    """A row copied before ``categories_hierarchy`` existed stays ineligible.

    The tempting, wrong repair is to derive the fingerprint from the raw
    ``categories`` text; that would reintroduce the very defect this corrects.
    """
    factory = get_off_sessionmaker()
    async with factory() as session:
        session.add(OffProduct(
            barcode="8901000099999",
            product_name="Legacy Row",
            categories="Foods, Breakfasts, Breakfast cereals",
            countries="India",
            fetched_at=datetime.now(UTC),
        ))
        await session.commit()

    await create_off_schema()

    async with factory() as session:
        row = await session.get(OffProduct, "8901000099999")
        assert row.categories == "Foods, Breakfasts, Breakfast cereals"  # untouched
        assert row.categories_hierarchy is None
        assert row.off_category_key is None
        assert not row.off_listed_for_india


@pytest.mark.asyncio
async def test_the_cache_write_path_computes_the_fingerprint_from_the_hierarchy(db_clean, off_clean):
    """The one place a derived column is written, from the hierarchy alone."""
    body = payload("01_indian_breakfast_cereal")
    await product_service._cache_off_product(body["code"], body)

    factory = get_off_sessionmaker()
    async with factory() as session:
        row = await session.get(OffProduct, body["code"])
    assert row.categories_hierarchy == body["categories_hierarchy"]
    assert row.countries_tags == body["countries_tags"]
    assert row.off_category_key == off_taxonomy.category_fingerprint(body["categories_hierarchy"])
    assert row.off_listed_for_india is True

    # A payload whose hierarchy is unusable gets no key rather than a guess.
    blank = payload("06_no_category_hierarchy")
    await product_service._cache_off_product(blank["code"], blank)
    async with factory() as session:
        row = await session.get(OffProduct, blank["code"])
    assert row.categories == blank["categories"]   # the text is still stored
    assert row.off_category_key is None            # and still decides nothing


# ---------------------------------------------------------------------------
# The discovery index: fixed-size key, and a plan that can use it
# ---------------------------------------------------------------------------
def test_the_discovery_index_is_on_the_fixed_size_key():
    """Structural, so it cannot rot silently when the query changes.

    The key column is a fixed-size digest, not an unbounded joined string, and
    the index is on it — not on the raw ``categories`` text and not on the lossy
    ``categories_tags`` (which is no longer even stored).
    """
    key_column = OffProduct.__table__.c.off_category_key
    assert key_column.type.length == 64, "the SQL key must be a fixed-size digest"

    index = next(i for i in OffProduct.__table__.indexes if i.name == "ix_off_products_discovery")
    assert [c.name for c in index.columns] == ["off_category_key", "barcode"]
    predicate = str(index.dialect_options["postgresql"]["where"])
    assert "off_category_key IS NOT NULL" in predicate
    assert "off_listed_for_india" in predicate
    assert index.dialect_options["postgresql"]["include"] == ["fetched_at"]

    # Nothing indexes the raw text, and the lossy array is not a column at all.
    assert not any(
        "categories" in [c.name for c in i.columns] for i in OffProduct.__table__.indexes
    )


@pytest.mark.asyncio
async def test_the_discovery_query_can_be_served_by_the_discovery_index(db_clean, off_clean):
    """An EXPLAIN that proves the index fits, without depending on row counts.

    Asserting the planner *chooses* the index would be brittle on a tiny table,
    so sequential scans are disabled and the plan is required to reach for this
    index by name. A predicate the index cannot answer shows up as a different
    plan. The pre-limit gates (India, freshness) appear in the plan too.
    """
    fingerprint = off_taxonomy.category_fingerprint(["en:breakfast-cereals"])
    factory = get_off_sessionmaker()
    async with factory() as session:
        for index in range(30):
            session.add(OffProduct(
                barcode=f"89010000{index:05d}",
                categories_hierarchy=["en:breakfast-cereals"],
                countries_tags=["en:india"],
                off_category_key=fingerprint,
                off_listed_for_india=True,
                fetched_at=datetime.now(UTC),
            ))
        await session.commit()

    async with get_off_engine().begin() as conn:
        await conn.execute(text(f'ANALYZE "{OFF_SCHEMA}".off_products'))
        await conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(row[0] for row in await conn.execute(text(
            f"EXPLAIN SELECT barcode FROM \"{OFF_SCHEMA}\".off_products "
            "WHERE barcode <> '8901000000001' AND off_category_key IS NOT NULL "
            f"AND off_category_key = '{fingerprint}' AND off_listed_for_india "
            "AND fetched_at IS NOT NULL AND fetched_at > now() - interval '30 days' "
            "ORDER BY barcode ASC LIMIT 50",
        )))
    assert "ix_off_products_discovery" in plan, plan
    assert "fetched_at" in plan  # the freshness gate is applied in the plan


# ---------------------------------------------------------------------------
# The fingerprint is only a key: a collision cannot manufacture a match
# ---------------------------------------------------------------------------
def test_the_fingerprint_is_a_fixed_size_hex_digest():
    fp = off_taxonomy.category_fingerprint(["en:a", "en:b"])
    assert isinstance(fp, str) and len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


# The integration proof that a shared fingerprint with a different hierarchy is
# rejected (the revalidation is load-bearing) lives in
# tests/test_step6a_comparable_alternative.py, where the verdict route and its
# seed helpers are — see test_a_hash_collision_cannot_manufacture_a_match.


# ---------------------------------------------------------------------------
# Coverage observability: aggregate gate frequency, no product, no person
# ---------------------------------------------------------------------------
def test_the_outcome_vocabulary_is_closed():
    assert isinstance(observability.DISCOVERY_OUTCOMES, frozenset)
    assert observability.DISCOVERY_OUTCOMES
    for outcome in observability.DISCOVERY_OUTCOMES:
        assert outcome.islower() and " " not in outcome


def test_an_unknown_outcome_is_refused_and_still_changes_nothing(caplog):
    with caplog.at_level("INFO", logger=observability.__name__):
        assert observability.record_discovery("something_new") is None
    assert observability.DISCOVERY_EVENT not in caplog.text


def test_a_recorded_outcome_carries_counts_and_no_identifier(caplog):
    with caplog.at_level("INFO", logger=observability.__name__):
        observability.record_discovery(
            "no_eligible_candidate", rows_scanned=7, pages_read=1,
            snapshots_read=3, candidates_evaluated=2,
        )
    line = caplog.text
    assert "outcome=no_eligible_candidate" in line
    assert "rows_scanned=7" in line and "candidates_evaluated=2" in line


FORBIDDEN_IN_OBSERVABILITY = (
    "barcode", "device", "account", "product_name", "brand", "batch",
    "fssai", "licence", "categories", "countries", "label_facts",
    "ingredients", "nutrition", "nutriments", "hierarchy", "fingerprint",
)


def test_observability_cannot_record_a_product_or_a_person():
    """Structural, because this is a privacy boundary rather than a preference."""
    tree = ast.parse(inspect.getsource(observability))
    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {
        arg.arg for node in ast.walk(tree) if isinstance(node, ast.arguments)
        for arg in [*node.args, *node.kwonlyargs, *node.posonlyargs]
    }
    for forbidden in FORBIDDEN_IN_OBSERVABILITY:
        assert not any(forbidden in name.lower() for name in names), forbidden


def test_observability_never_changes_the_answer(monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("the metrics pipeline is down")

    monkeypatch.setattr(observability.logger, "info", explode)
    assert observability.record_discovery("candidate_offered") is None


def test_every_service_outcome_is_a_declared_one():
    tree = ast.parse(inspect.getsource(alternatives_service))
    recorded = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record_discovery"
        and node.args and isinstance(node.args[0], ast.Constant)
    }
    assert recorded, "discovery is supposed to record its outcome"
    assert recorded <= observability.DISCOVERY_OUTCOMES, recorded - observability.DISCOVERY_OUTCOMES


# ---------------------------------------------------------------------------
# The ODbL wall, extended over the hierarchy and the fingerprint
# ---------------------------------------------------------------------------
def test_a_derived_column_is_still_open_food_facts_data():
    """The encoder reaches nothing proprietary, so nothing proprietary reaches a column."""
    assert OFF_FIELDS == OFF_PUBLISHED_FIELDS | OFF_CANONICAL_FIELDS
    assert {"off_category_key", "off_listed_for_india"} == OFF_CANONICAL_FIELDS
    assert not OFF_CANONICAL_FIELDS & OFF_PUBLISHED_FIELDS
    assert "categories_hierarchy" in OFF_PUBLISHED_FIELDS
    assert "categories_tags" not in OFF_FIELDS

    imported = {
        alias.name
        for node in ast.walk(ast.parse(inspect.getsource(off_taxonomy)))
        if isinstance(node, ast.Import) for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(ast.parse(inspect.getsource(off_taxonomy)))
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(name.startswith("app.") for name in imported), imported


UNMISTAKABLY_OFF_COLUMNS = frozenset({
    "categories_hierarchy", "countries_tags", "off_category_key",
    "off_listed_for_india", "off_last_modified_t", "nutriments",
})


def test_no_open_food_facts_column_appears_in_store_b():
    """A copy of one of their fields into our schema is a derived database.

    The prefix keeps this exact: Store B has an unrelated
    ``inventory_subtype_definitions.category_key`` that a bare-name assertion
    would trip on.
    """
    from app.shared.database.base import Base

    assert UNMISTAKABLY_OFF_COLUMNS <= OFF_FIELDS
    for table_name, table in Base.metadata.tables.items():
        for column in table.columns:
            assert column.name not in UNMISTAKABLY_OFF_COLUMNS, f"{table_name}.{column.name}"
            assert column.name != "categories_tags", f"{table_name}.{column.name}"


def test_the_canonical_columns_travel_in_the_odbl_export():
    from app.domains.off import export

    row = OffProduct(
        barcode="8901000000001", categories_hierarchy=["en:breakfast-cereals"],
        countries_tags=["en:india"],
        off_category_key=off_taxonomy.category_fingerprint(["en:breakfast-cereals"]),
        off_listed_for_india=True,
    )
    record = export._record(row)
    assert record["categories_hierarchy"] == ["en:breakfast-cereals"]
    assert record["off_category_key"] == off_taxonomy.category_fingerprint(["en:breakfast-cereals"])
    assert record["off_listed_for_india"] is True
    assert set(record) <= OFF_FIELDS


# ---------------------------------------------------------------------------
# The public contract did not move
# ---------------------------------------------------------------------------
def test_no_internal_reaches_the_public_envelope():
    from app.domains.alternatives.policy import comparison_block
    from app.domains.nutrition.grading.rules import Grade

    block = comparison_block(current_grade=Grade.C, candidate_grade=Grade.B, basis="solid")
    assert set(block) == {
        "category_match", "category_source", "current_grade", "candidate_grade", "basis",
    }
    assert block["category_match"] == policy_module.CATEGORY_MATCH_EXACT_SOURCE_TAXONOMY
    # The fingerprint is an opaque key and must never be shown.
    for value in block.values():
        assert len(str(value)) != 64 or not all(c in "0123456789abcdef" for c in str(value))


def test_both_budget_reasons_resolve_to_the_same_customer_status():
    reasons = {
        value for name, value in vars(policy_module).items()
        if name.startswith("REASON_") and isinstance(value, str)
    }
    assert policy_module.REASON_SEARCH_BUDGET_EXHAUSTED in reasons
    for reason in (policy_module.REASON_SEARCH_BUDGET_EXHAUSTED,
                   policy_module.REASON_NO_COMPARABLE_CANDIDATE):
        envelope = alternatives_service.not_enough_information(reason)
        assert envelope["status"] == policy_module.STATUS_NOT_ENOUGH_INFORMATION
        assert envelope["candidate"] is None
        assert set(envelope) == {"policy_version", "status", "reason_key", "candidate"}


# ---------------------------------------------------------------------------
# Pack authority: proven by the server, and now with the outcome + provenance
# ---------------------------------------------------------------------------
async def _make_device() -> uuid.UUID:
    device_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        session.add(ScanDevice(
            id=device_id, device_key=uuid.uuid4().hex, token_hash=uuid.uuid4().hex,
        ))
        await session.commit()
    return device_id


async def _make_ai_run() -> uuid.UUID:
    """A minimal successful AIRun, so a genuine capture can reference one."""
    from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun

    factory = get_sessionmaker()
    async with factory() as session:
        run = AIRun(
            account_id=None, feature="product_label_transcribe", provider="test",
            model="test-model", prompt_version="scan-label.v1", schema_version="scan-label.v1",
            status=AI_STATUS_SUCCEEDED, validation_passed=True,
        )
        session.add(run)
        await session.commit()
        return run.id


async def _add_event(device_id, barcode, *, outcome, label_facts, ai_run_id=None, created_at=None):
    factory = get_sessionmaker()
    async with factory() as session:
        session.add(ScanEvent(
            device_id=device_id, barcode=barcode, outcome=outcome,
            client_scan_id=uuid.uuid4().hex, label_facts=label_facts, ai_run_id=ai_run_id,
            created_at=created_at or datetime.now(UTC),
        ))
        await session.commit()


@pytest.mark.asyncio
async def test_a_non_label_event_with_forged_facts_proves_no_pack(db_clean):
    """The forged-row case, at the resolver. Facts alone are not a capture.

    A ``found_local`` event carrying an FSSAI licence and a batch number in its
    ``label_facts`` looks like a capture but did not come through the confirmation
    route. It must not authorise a pack.
    """
    device_id = await _make_device()
    barcode = "8901000066666"
    await _add_event(
        device_id, barcode, outcome="found_local",
        label_facts={"fssai_licence": "10012345678901", "batch_number": "B1"},
        ai_run_id=None,
    )
    factory = get_sessionmaker()
    async with factory() as session:
        pack = await pack_context.current_pack(session, barcode=barcode, device_id=device_id)
    assert pack.has_scan
    assert not pack.is_proven, "a non-label event must not prove a pack"
    assert pack.label_facts is None


@pytest.mark.asyncio
async def test_a_label_event_without_confirmation_provenance_proves_no_pack(db_clean):
    """``ai_run_id`` is the provenance of the server-authorised path.

    Every production ``label_captured`` is written by the confirmation route from
    a validated ``AIRun``, so a label event with no ``ai_run_id`` did not come
    through it and cannot prove a pack.
    """
    device_id = await _make_device()
    barcode = "8901000066667"
    await _add_event(
        device_id, barcode, outcome=product_service.OUTCOME_LABEL,
        label_facts={"product_name": "X", "batch_number": "B1"}, ai_run_id=None,
    )
    factory = get_sessionmaker()
    async with factory() as session:
        pack = await pack_context.current_pack(session, barcode=barcode, device_id=device_id)
    assert not pack.is_proven


@pytest.mark.asyncio
async def test_a_genuine_confirmed_capture_proves_the_pack_and_a_later_plain_scan_undoes_it(db_clean):
    """The positive case, and the "newest scan, never behind" rule together."""
    device_id = await _make_device()
    barcode = "8901000066668"
    base = datetime.now(UTC) - timedelta(hours=2)
    await _add_event(
        device_id, barcode, outcome=product_service.OUTCOME_LABEL,
        label_facts={"product_name": "X", "batch_number": "B1"},
        ai_run_id=await _make_ai_run(), created_at=base,
    )
    factory = get_sessionmaker()
    async with factory() as session:
        proven = await pack_context.current_pack(session, barcode=barcode, device_id=device_id)
    assert proven.is_proven and proven.label_facts["batch_number"] == "B1"

    # A newer plain scan: a different packet, lot unknown, never reaching behind.
    await _add_event(device_id, barcode, outcome="found_local", label_facts=None)
    async with factory() as session:
        now = await pack_context.current_pack(session, barcode=barcode, device_id=device_id)
    assert now.has_scan and not now.is_proven


@pytest.mark.asyncio
async def test_a_device_that_never_scanned_this_barcode_proves_nothing(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        empty = await pack_context.current_pack(
            session, barcode="8901000088888", device_id=uuid.uuid4(),
        )
        anonymous = await pack_context.current_pack(
            session, barcode="8901000088888", device_id=None,
        )
    for context in (empty, anonymous):
        assert not context.has_scan and not context.is_proven


def test_there_is_one_pack_resolver_and_community_uses_it():
    from app.domains.community import service as community_service

    source = inspect.getsource(community_service)
    assert "pack_context.current_pack" in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in {
            "current_pack_event", "current_pack_context",
        }:
            body = ast.dump(node)
            assert "ScanEvent" not in body or "pack_context" in body, node.name


def test_the_pack_resolver_requires_outcome_and_provenance_and_ignores_client_time():
    source = inspect.getsource(pack_context)
    assert "created_at.desc()" in source
    assert "OUTCOME_LABEL" in source
    assert "ai_run_id" in source
    # scanned_at is a client value and must decide nothing here.
    body = source.split('"""')[-1]
    assert "scanned_at" not in body


# ---------------------------------------------------------------------------
# Step 6B is not in this milestone
# ---------------------------------------------------------------------------
STEP_6B_TERMS = (
    "mrp", "price", "paise", "rupee", "unit_price", "value_for_money", "retailer",
    "affiliate", "commerce", "cart", "receipt", "discount", "offer_price",
)

STEP_6A1_MODULES = (
    alternatives_service, policy_module, observability, off_taxonomy, pack_context,
    category_module,
)


def test_no_step_6b_concept_appears_in_step_6a1_code():
    for module in STEP_6A1_MODULES:
        tree = ast.parse(inspect.getsource(module))
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        } | {
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        }
        for term in STEP_6B_TERMS:
            assert not any(term in name.lower() for name in names), f"{module.__name__}: {term}"
