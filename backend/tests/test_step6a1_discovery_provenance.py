"""Step 6A.1 — the four things Step 6A got wrong, and what stops them returning.

Step 6A shipped a comparable alternative that worked. These are the four ways it
was wrong underneath, each of which produced a correct-looking answer for the
wrong reason:

1. **Discovery could starve permanently.** Store A said which products were the
   same kind and sold here; only Store B knew which had a confirmed label. A
   single window of fifty source-qualified rows with no usable snapshot hid
   everything behind it, on every request, for ever.
2. **Rows that could never qualify consumed the window.** The category, country
   and freshness gates ran in Python *after* the ``LIMIT``, so a shelf of stale
   or foreign rows filled the window and was then thrown away.
3. **Raw ``categories`` text was treated as taxonomy.** Open Food Facts
   documents that field as untaxonomised, written in whichever language the last
   editor was using, and "mostly used for debugging and testing purposes".
4. **Pack authority was whatever the client declared.** ``physical_pack_context``
   arrived as ``true`` by default and was believed, while the facts behind it
   came from the newest snapshot for the barcode — possibly a stranger's
   photograph of a stranger's packet.

Everything here is a regression against one of those. Where a test could pass
for the wrong reason it is written so that it cannot: the starvation cases use
counts that a single window provably cannot reach, and the taxonomy cases are
driven from frozen payloads in which the raw text and the taxonomy deliberately
disagree.
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
from app.domains.alternatives import observability
from app.domains.alternatives import policy as policy_module
from app.domains.alternatives import service as alternatives_service
from app.domains.off import taxonomy as off_taxonomy
from app.domains.off.models import OFF_SCHEMA, OffBase, OffProduct
from app.domains.off.store import create_off_schema, get_off_engine, get_off_sessionmaker
from app.domains.off.wall import OFF_CANONICAL_FIELDS, OFF_FIELDS, OFF_PUBLISHED_FIELDS
from app.domains.product import pack_context
from app.domains.product import service as product_service
from app.domains.product.models import ScanEvent
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
    """Ten cases, on disk, with their provenance written down beside them."""
    index = json.loads((FIXTURE_DIR / "case_index.json").read_text())
    assert len(index) == 10
    for name in index:
        assert (FIXTURE_DIR / f"{name}.json").is_file(), name
    sources = (FIXTURE_DIR / "SOURCES.md").read_text()
    # The provenance file must say where they came from and when.
    assert "2026-09-03" in sources
    assert "raw.githubusercontent.com/openfoodfacts/openfoodfacts-server" in sources
    # And must not pretend they are live captures.
    assert "not captures of live API responses" in sources


def test_the_raw_text_and_the_taxonomy_disagree_exactly_where_they_should():
    """The fixtures are only useful if they carry the disagreement."""
    french = payload("02_same_kind_edited_in_french")
    english = payload("01_indian_breakfast_cereal")
    assert french["categories"] != english["categories"]
    assert french["categories_tags"] == english["categories_tags"]

    collision = payload("03_raw_leaf_collision")
    assert collision["categories"].split(",")[-1] == english["categories"].split(",")[-1]
    assert collision["categories_tags"] != english["categories_tags"]


def test_regression_a_the_same_product_kind_edited_in_another_language_still_matches():
    """Failure mode A: the raw text is the last editor's language.

    Two products Open Food Facts classifies identically, one last edited in
    English and one in French. Reading the raw ``categories`` text compares
    "breakfast cereals" with "céréales pour petit-déjeuner" and finds nothing,
    so a perfectly good alternative is silently never offered.
    """
    english = payload("01_indian_breakfast_cereal")
    french = payload("02_same_kind_edited_in_french")
    assert off_taxonomy.same_category(english["categories_tags"], french["categories_tags"])
    # The raw text alone would have made these two different products.
    assert english["categories"] != french["categories"]


def test_regression_b_two_different_products_sharing_a_raw_leaf_do_not_match():
    """Failure mode B: the raw leaf collides, and a false comparison follows.

    A cereal bar whose contributor happened to type "Breakfast cereals" as the
    final element. Reading the last comma-separated token calls it the same kind
    of product as a box of cereal and publishes a comparison between them.
    """
    cereal = payload("01_indian_breakfast_cereal")
    bar = payload("03_raw_leaf_collision")
    raw_leaf = lambda row: row["categories"].split(",")[-1].strip().casefold()  # noqa: E731
    assert raw_leaf(cereal) == raw_leaf(bar), "the fixture must actually collide"
    assert not off_taxonomy.same_category(cereal["categories_tags"], bar["categories_tags"])


def test_regression_c_india_is_found_through_the_taxonomy_not_a_list_of_spellings():
    """Failure mode C: a closed set of English spellings misses the taxonomy.

    The raw country text reads "Inde". A set of literal tokens — ``{"india",
    "en:india"}`` — does not contain it, so a product Open Food Facts itself
    lists for India is treated as unavailable here.
    """
    row = payload("04_india_only_in_the_taxonomy")
    assert row["countries"] == "Inde"
    assert off_taxonomy.listed_for_india(row["countries_tags"])
    # The raw text is not a country list, whatever it says.
    assert not off_taxonomy.listed_for_india(row["countries"])


def test_regression_d_the_raw_country_text_cannot_overrule_the_taxonomy():
    """Failure mode D: the raw text says India and the classification does not.

    ``countries`` is prose the last editor typed; ``countries_tags`` is what the
    source's own taxonomy resolved. When they disagree, believing the prose
    claims a product is sold here on the strength of a string nobody normalised.
    """
    row = payload("05_raw_says_india_taxonomy_does_not")
    assert row["countries"] == "India"
    assert not off_taxonomy.listed_for_india(row["countries_tags"])


@pytest.mark.parametrize(
    ("case", "has_key"),
    [
        ("01_indian_breakfast_cereal", True),
        ("06_no_category_tags", False),
        ("07_empty_category_tags", False),
        # A tag that matched no taxonomy entry is still an exact string to
        # compare. Its lossy normalisation can cost a match; because the whole
        # set must be identical, it can never manufacture one.
        ("08_non_taxonomy_category_entry", True),
        ("10_malformed_tags", False),
    ],
)
def test_a_classification_is_present_or_it_is_absent(case, has_key):
    assert (off_taxonomy.category_key(payload(case)["categories_tags"]) is not None) is has_key


def test_a_lookalike_country_is_a_different_country():
    row = payload("09_lookalike_country")
    assert not off_taxonomy.listed_for_india(row["countries_tags"])


# ---------------------------------------------------------------------------
# Store A schema evolution: additive, idempotent, and never a backfill
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_store_a_schema_converges_from_any_starting_point(db_clean, off_clean):
    """Fresh, half-migrated and already-current all end in the same place."""
    async def columns() -> set[str]:
        async with get_off_engine().begin() as conn:
            rows = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = 'off_products'",
            ), {"schema": OFF_SCHEMA})
            return {row[0] for row in rows}

    expected = {column.name for column in OffProduct.__table__.columns}
    assert await columns() == expected

    # Running it again changes nothing and raises nothing.
    await create_off_schema()
    assert await columns() == expected

    # Now the case that matters: a table created by an earlier release, which
    # ``create_all`` alone would leave untouched for ever.
    async with get_off_engine().begin() as conn:
        for column in sorted(OFF_CANONICAL_FIELDS | {"categories_tags", "countries_tags"}):
            await conn.execute(text(
                f'ALTER TABLE "{OFF_SCHEMA}".off_products DROP COLUMN IF EXISTS {column}',
            ))
        await conn.execute(text(
            f'DROP INDEX IF EXISTS "{OFF_SCHEMA}".ix_off_products_discovery',
        ))
    assert await columns() != expected

    await create_off_schema()
    assert await columns() == expected


@pytest.mark.asyncio
async def test_an_old_row_is_left_alone_rather_than_backfilled(db_clean, off_clean):
    """A row copied before the taxonomy arrays existed stays ineligible.

    This is the tempting mistake, and it is the one that would undo the whole
    milestone: deriving ``off_category_key`` from the raw ``categories`` text so the
    old rows "work again". They would work by reintroducing exactly the defect
    the canonical column exists to remove. They stay NULL, they stay out of
    discovery, and an ordinary refresh fixes them properly.
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

    await create_off_schema()  # the evolution runs again over the existing row

    async with factory() as session:
        row = await session.get(OffProduct, "8901000099999")
        assert row.categories == "Foods, Breakfasts, Breakfast cereals"  # untouched
        assert row.countries == "India"                                  # untouched
        assert row.off_category_key is None
        assert not row.off_listed_for_india


@pytest.mark.asyncio
async def test_the_cache_write_path_computes_the_canonical_columns(db_clean, off_clean):
    """The one place a canonical column is written, from the arrays alone."""
    body = payload("01_indian_breakfast_cereal")
    stored = await product_service._cache_off_product(body["code"], body)
    assert stored is not None

    factory = get_off_sessionmaker()
    async with factory() as session:
        row = await session.get(OffProduct, body["code"])
    assert row.categories_tags == body["categories_tags"]
    assert row.countries_tags == body["countries_tags"]
    assert row.off_category_key == off_taxonomy.category_key(body["categories_tags"])
    assert row.off_listed_for_india is True

    # And a payload whose taxonomy is unusable gets no key rather than a guess.
    blank = payload("06_no_category_tags")
    await product_service._cache_off_product(blank["code"], blank)
    async with factory() as session:
        row = await session.get(OffProduct, blank["code"])
    assert row.categories == blank["categories"]  # the text is still stored
    assert row.off_category_key is None               # and still decides nothing


# ---------------------------------------------------------------------------
# The discovery index: the query's shape, and a plan that can use it
# ---------------------------------------------------------------------------
def test_the_discovery_index_matches_the_discovery_query():
    """Structural, so it cannot rot silently when the query changes.

    The index is for the canonical query, not for the raw-category ``ILIKE``
    the previous design used — a trigram index on ``categories`` would now be
    an index on a column nothing filters by.
    """
    index = next(
        i for i in OffProduct.__table__.indexes if i.name == "ix_off_products_discovery"
    )
    assert [column.name for column in index.columns] == ["off_category_key", "barcode"]
    predicate = str(index.dialect_options["postgresql"]["where"])
    assert "off_category_key IS NOT NULL" in predicate
    assert "off_listed_for_india" in predicate
    assert index.dialect_options["postgresql"]["include"] == ["fetched_at"]

    # Nothing indexes the raw text for discovery, because nothing reads it.
    assert not any(
        "categories" in [c.name for c in i.columns] for i in OffProduct.__table__.indexes
    )


@pytest.mark.asyncio
async def test_the_discovery_query_can_be_served_by_the_discovery_index(db_clean, off_clean):
    """An EXPLAIN that proves the index fits, without depending on row counts.

    Asserting the planner *chooses* the index would be a brittle test: on a
    table of ten rows a sequential scan is genuinely cheaper and PostgreSQL is
    right to pick one. What must hold is that the index can serve this query
    shape at all — so sequential scans are disabled and the plan is required to
    reach for this index by name. A predicate the index cannot answer shows up
    immediately as a different plan.
    """
    factory = get_off_sessionmaker()
    async with factory() as session:
        for index in range(30):
            session.add(OffProduct(
                barcode=f"89010000{index:05d}",
                categories_tags=["en:breakfast-cereals"],
                countries_tags=["en:india"],
                off_category_key="en:breakfast-cereals",
                off_listed_for_india=True,
                fetched_at=datetime.now(UTC),
            ))
        await session.commit()

    async with get_off_engine().begin() as conn:
        await conn.execute(text(f'ANALYZE "{OFF_SCHEMA}".off_products'))
        await conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(row[0] for row in await conn.execute(text(
            f'EXPLAIN SELECT barcode FROM "{OFF_SCHEMA}".off_products '
            "WHERE barcode <> '8901000000001' AND off_category_key IS NOT NULL "
            "AND off_category_key = 'en:breakfast-cereals' AND off_listed_for_india "
            "AND fetched_at IS NOT NULL AND fetched_at > now() - interval '30 days' "
            "ORDER BY barcode ASC LIMIT 50",
        )))
    assert "ix_off_products_discovery" in plan, plan


# ---------------------------------------------------------------------------
# Coverage observability: aggregate gate frequency, and nothing about anybody
# ---------------------------------------------------------------------------
def test_the_outcome_vocabulary_is_closed():
    """An open reason field is how free text, and then identifiers, arrive."""
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
    "ingredients", "nutrition", "nutriments",
)


def test_observability_cannot_record_a_product_or_a_person():
    """Structural, because this is a privacy boundary rather than a preference.

    Coverage measurement answers "how often does the category gate close". It
    must not become a record of which products a person scanned — that is a
    different thing entirely, assembled out of a feature nobody was asked about.
    """
    source = inspect.getsource(observability)
    tree = ast.parse(source)
    # Every name this module can reach, ignoring prose in docstrings.
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

    # And the only values it can format are the ones it is handed.
    formatted = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"info", "warning", "error", "debug"}
    ]
    assert formatted, "the module is supposed to log something"


def test_observability_never_changes_the_answer(monkeypatch):
    """A counter that can break a Product Result is not a counter."""
    def explode(*_args, **_kwargs):
        raise RuntimeError("the metrics pipeline is down")

    monkeypatch.setattr(observability.logger, "info", explode)
    assert observability.record_discovery("candidate_offered") is None


def test_every_service_outcome_is_a_declared_one():
    """The service and the vocabulary cannot drift apart unnoticed."""
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
# The ODbL wall, extended over the canonical columns
# ---------------------------------------------------------------------------
def test_a_canonical_column_is_still_open_food_facts_data():
    """The argument for these columns, held up as a test.

    A canonical column is their data in another shape. What would make one a
    licence breach is a value that depends on something of *ours* — a threshold,
    a grade, a verdict, a customer fact — because Store A is published openly
    and that would publish the product with it.
    """
    assert OFF_FIELDS == OFF_PUBLISHED_FIELDS | OFF_CANONICAL_FIELDS
    assert {"off_category_key", "off_listed_for_india"} == OFF_CANONICAL_FIELDS
    assert not OFF_CANONICAL_FIELDS & OFF_PUBLISHED_FIELDS

    # The encoder reaches nothing proprietary, so nothing proprietary can reach
    # a canonical column. This is the property the argument above rests on.
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


#: Store A column names that could only ever mean Open Food Facts data. Not
#: every name in ``OFF_FIELDS`` qualifies: ``barcode``, ``quantity`` and
#: ``product_name`` are ordinary words that Store B uses for its own values, and
#: ``ingredients_text`` belongs to cosmetics as well as to food. These are the
#: ones whose appearance in Store B would mean a copy had been made.
UNMISTAKABLY_OFF_COLUMNS = frozenset({
    "categories_tags", "countries_tags", "off_category_key",
    "off_listed_for_india", "off_last_modified_t", "nutriments",
})


def test_no_open_food_facts_column_appears_in_store_b():
    """A copy of one of their fields into our schema is a derived database.

    The canonical columns carry the ``off_`` prefix precisely so this test can
    be exact. Store B already has an unrelated ``inventory_subtype_definitions.
    category_key`` — our own wardrobe taxonomy, nothing to do with food — and an
    assertion written against the bare name would either fail on it or have to
    be loosened until it proved nothing.
    """
    from app.shared.database.base import Base

    assert UNMISTAKABLY_OFF_COLUMNS <= OFF_FIELDS
    for table_name, table in Base.metadata.tables.items():
        for column in table.columns:
            assert column.name not in UNMISTAKABLY_OFF_COLUMNS, f"{table_name}.{column.name}"


def test_the_canonical_names_cannot_collide_with_an_unrelated_store_b_column():
    """The reason for the prefix, pinned so a later rename cannot drop it."""
    from app.shared.database.base import Base

    store_b_names = {
        column.name for table in Base.metadata.tables.values() for column in table.columns
    }
    # The collision that motivated the prefix is real and still there.
    assert "category_key" in store_b_names
    # And the prefixed names stay clear of it.
    for name in OFF_CANONICAL_FIELDS:
        assert name.startswith("off_"), name
        assert name not in store_b_names, name


def test_the_canonical_columns_travel_in_the_odbl_export():
    """Store A is published openly, and these are part of it."""
    from app.domains.off import export

    row = OffProduct(
        barcode="8901000000001", categories_tags=["en:breakfast-cereals"],
        countries_tags=["en:india"], off_category_key="en:breakfast-cereals",
        off_listed_for_india=True,
    )
    record = export._record(row)
    assert record["off_category_key"] == "en:breakfast-cereals"
    assert record["off_listed_for_india"] is True
    assert record["categories_tags"] == ["en:breakfast-cereals"]
    assert set(record) <= OFF_FIELDS


# ---------------------------------------------------------------------------
# The public contract did not move
# ---------------------------------------------------------------------------
INTERNALS = (
    "off_category_key", "categories_tags", "countries_tags", "categories", "countries",
    "off_listed_for_india", "rows_scanned", "pages_read", "page", "cursor", "after",
    "snapshot_id", "label_snapshot_id", "scan_event_id", "device_id", "account_id",
    "budget", "limit", "offset",
)


def test_no_internal_reaches_the_public_envelope():
    """None of these are facts about a product, so none of them are published."""
    from app.domains.alternatives.policy import comparison_block
    from app.domains.nutrition.grading.rules import Grade

    block = comparison_block(current_grade=Grade.C, candidate_grade=Grade.B, basis="solid")
    assert set(block) == {
        "category_match", "category_source", "current_grade", "candidate_grade", "basis",
    }
    assert block["category_match"] == policy_module.CATEGORY_MATCH_EXACT_SOURCE_TAXONOMY
    # The key itself is an opaque equality token and must never be shown.
    for value in block.values():
        assert off_taxonomy.KEY_SEPARATOR not in str(value)


def test_the_reason_vocabulary_stayed_closed_and_gained_exactly_one():
    """A new reason is an addition, not a change of shape."""
    reasons = {
        value for name, value in vars(policy_module).items()
        if name.startswith("REASON_") and isinstance(value, str)
    }
    assert policy_module.REASON_SEARCH_BUDGET_EXHAUSTED in reasons
    # Both exhaustion reasons resolve to the same customer-facing status, so the
    # distinction stays an engineering signal and never becomes a claim.
    for reason in (policy_module.REASON_SEARCH_BUDGET_EXHAUSTED,
                   policy_module.REASON_NO_COMPARABLE_CANDIDATE):
        envelope = alternatives_service.not_enough_information(reason)
        assert envelope["status"] == policy_module.STATUS_NOT_ENOUGH_INFORMATION
        assert envelope["candidate"] is None
        assert set(envelope) == {"policy_version", "status", "reason_key", "candidate"}


# ---------------------------------------------------------------------------
# Pack authority: proven by the server, only ever narrowed by the client
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_pack_resolver_reads_the_newest_scan_and_never_reaches_past_it(db_clean):
    """A plain scan means a new packet, and its lot is unknown until captured."""
    from app.shared.database.sql import get_sessionmaker

    device_id = uuid.uuid4()
    barcode = "8901000077777"
    factory = get_sessionmaker()
    async with factory() as session:
        from app.domains.product.models import ScanDevice

        session.add(ScanDevice(id=device_id, device_key=uuid.uuid4().hex, token_hash=uuid.uuid4().hex))
        await session.flush()
        base = datetime.now(UTC) - timedelta(hours=2)
        session.add(ScanEvent(
            device_id=device_id, barcode=barcode, outcome="label_captured",
            client_scan_id=uuid.uuid4().hex, label_facts={"batch_number": "OLD"},
            created_at=base,
        ))
        await session.commit()

    async with factory() as session:
        proven = await pack_context.current_pack(session, barcode=barcode, device_id=device_id)
    assert proven.is_proven
    assert proven.label_facts == {"batch_number": "OLD"}

    # A newer plain scan: a different physical packet, lot unknown.
    async with factory() as session:
        session.add(ScanEvent(
            device_id=device_id, barcode=barcode, outcome="found_local",
            client_scan_id=uuid.uuid4().hex, label_facts=None,
            created_at=datetime.now(UTC),
        ))
        await session.commit()

    async with factory() as session:
        now = await pack_context.current_pack(session, barcode=barcode, device_id=device_id)
    assert now.has_scan
    assert not now.is_proven, "the resolver reached backwards past a plain scan"
    assert now.label_facts is None


@pytest.mark.asyncio
async def test_a_device_that_never_scanned_this_barcode_proves_nothing(db_clean):
    from app.shared.database.sql import get_sessionmaker

    factory = get_sessionmaker()
    async with factory() as session:
        empty = await pack_context.current_pack(
            session, barcode="8901000088888", device_id=uuid.uuid4(),
        )
        anonymous = await pack_context.current_pack(
            session, barcode="8901000088888", device_id=None,
        )
    for context in (empty, anonymous):
        assert not context.has_scan
        assert not context.is_proven


def test_there_is_one_pack_resolver_and_community_uses_it():
    """Two copies of this rule would drift, and the drift shows up as a recall."""
    from app.domains.community import service as community_service

    source = inspect.getsource(community_service)
    assert "pack_context.current_pack" in source
    # The rule itself lives in one module: community must not re-issue the query.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in {
            "current_pack_event", "current_pack_context",
        }:
            body = ast.dump(node)
            assert "ScanEvent" not in body or "pack_context" in body, node.name


def test_the_pack_resolver_never_orders_by_a_client_timestamp():
    """``scanned_at`` is a value a client chooses. It decides nothing here."""
    source = inspect.getsource(pack_context)
    assert "created_at.desc()" in source
    assert "scanned_at" not in source.split('"""')[-1], "client time reached the query"


# ---------------------------------------------------------------------------
# Step 6B is not in this milestone
# ---------------------------------------------------------------------------
STEP_6B_TERMS = (
    "mrp", "price", "paise", "rupee", "unit_price", "value_for_money", "retailer",
    "affiliate", "commerce", "cart", "receipt", "discount", "offer_price",
)

STEP_6A1_MODULES = (
    alternatives_service, policy_module, observability, off_taxonomy, pack_context,
)


def test_no_step_6b_concept_appears_in_step_6a1_code():
    """Money is a later milestone with its own provenance. None of it is here."""
    for module in STEP_6A1_MODULES:
        source = inspect.getsource(module)
        tree = ast.parse(source)
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
