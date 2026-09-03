"""Step 6A — one comparable alternative, and everything it must leave alone.

The feature makes a single public claim: *the source lists another product in
the same category, and under the same rules it grades higher.* Almost every
test here defends the boundary around that sentence rather than the sentence
itself — because the ways this goes wrong are all ways of quietly claiming
more: a market search we did not perform, a category we inferred, a panel basis
we guessed, an availability we assumed, a person we profiled, or a score we
invented.

Two stores answer two different questions and neither may answer the other's.
Open Food Facts says which products *might* be comparable — the category, the
countries, and how recently we copied the row. A confirmed label snapshot says
what a candidate actually *is*, including the one thing the catalogue cannot
tell us: whether its panel was printed per 100 g or per 100 ml.

The layers below are load-bearing and must not move. The grade, the decision,
the official record and the shopper observations are computed by other domains
and are byte-identical whether an alternative is found or not.
"""
from __future__ import annotations

import ast
import inspect
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun, AIRunOutput
from app.domains.alternatives import category as category_module
from app.domains.alternatives import policy as policy_module
from app.domains.alternatives import service as alternatives_service
from app.domains.nutrition.grading.production_rules import (
    STATUS_PUBLISHED,
    ProductionRuleset,
    candidate_ruleset,
)
from app.domains.nutrition.grading.rules import Grade
from app.domains.off import client as off_client
from app.domains.off import freshness as off_freshness
from app.domains.off import taxonomy as off_taxonomy
from app.domains.off.attribution import ATTRIBUTION_TEXT
from app.domains.off.models import OffBase, OffProduct
from app.domains.off.store import create_off_schema, get_off_engine, get_off_sessionmaker
from app.domains.official_records import service as official_records
from app.domains.product import service as product_service
from app.domains.product.models import LabelSnapshot, ProductRecord, ScanEvent
from app.shared.database.sql import get_engine, get_sessionmaker
from sqlalchemy import event, func, select

from tests.conftest import auth
from tests.test_official_records import LICENCE, data_row, make_export

# ---------------------------------------------------------------------------
# The cast. One current product and a shelf of candidates around it.
# ---------------------------------------------------------------------------
CURRENT = "8901000000001"          # graded C
CANDIDATE_B = "8901000000002"      # graded B, India
CANDIDATE_B_LATER = "8901000000003"  # graded B, India, higher barcode
CANDIDATE_A = "8901000000004"      # graded A, India — wins on grade
CANDIDATE_C = "8901000000005"      # graded C — same grade, never offered
CANDIDATE_D = "8901000000006"      # graded D — worse, never offered
OTHER_CATEGORY = "8901000000007"   # graded A, but a different source leaf
UK_ONLY = "8901000000008"          # graded B, not listed for India
NO_COUNTRY = "8901000000009"       # graded B, no country list at all
DRINK = "8901000000010"            # graded B, but measured per 100 ml
STALE = "8901000000011"            # graded A, but our source copy has expired

#: The raw ``categories`` text, which is what a contributor typed in whatever
#: language they were editing in. Two spellings of the same product kind are
#: kept here on purpose: the source publishes text like this and it is *not*
#: what decides a comparison. See ``app/domains/off/taxonomy.py``.
CEREAL_CATEGORY = "Foods, Breakfasts, Breakfast cereals"
CEREAL_CATEGORY_OTHER_PATH = "Plant foods, Breakfast cereals"
BAR_CATEGORY = "Foods, Cereal bars"

#: The non-lossy ``categories_hierarchy`` arrays, which are what decides
#: comparability. Both cereal spellings above carry the same classification,
#: because they describe the same kind of product — which is the entire reason
#: the raw text cannot be the authority. (Named ``*_TAGS`` for brevity; they are
#: hierarchy arrays.)
CEREAL_TAGS = ["en:plant-based-foods-and-beverages", "en:cereals-and-potatoes",
               "en:breakfast-cereals"]
BAR_TAGS = ["en:snacks", "en:sweet-snacks", "en:cereal-bars"]
GHEE_TAGS = ["en:fats", "en:clarified-butters"]
INDIA_TAGS = ["en:india"]
UK_TAGS = ["en:united-kingdom"]

#: Which hierarchy array goes with which raw text, so a test that varies the raw
#: category still says something meaningful about the canonical one. A test that
#: needs them to disagree passes ``categories_hierarchy`` explicitly.
CATEGORY_TAGS: dict[str, list[str]] = {
    CEREAL_CATEGORY: CEREAL_TAGS,
    CEREAL_CATEGORY_OTHER_PATH: CEREAL_TAGS,
    BAR_CATEGORY: BAR_TAGS,
}
COUNTRY_TAGS: dict[str, list[str]] = {
    "India": INDIA_TAGS,
    "United Kingdom": UK_TAGS,
}

#: Distinguishes "the caller did not say" from "the caller said there is none".
_UNSET = object()


def tags_for_category(categories: str | None) -> list[str] | None:
    """The ``categories_hierarchy`` array a raw category string stands in for."""
    if categories is None:
        return None
    return CATEGORY_TAGS.get(categories, ["en:" + categories.split(",")[-1].strip().casefold().replace(" ", "-")])


def tags_for_country(countries: str | None) -> list[str] | None:
    """The country taxonomy array a raw country string stands in for."""
    if countries is None:
        return None
    parts = [part.strip() for part in countries.split(",") if part.strip()]
    if not parts:
        return None
    tags: list[str] = []
    for part in parts:
        tags.extend(COUNTRY_TAGS.get(part, ["en:" + part.casefold().replace(" ", "-")]))
    return tags

#: Panels, per 100 g unless a fixture says otherwise. Calibrated against the
#: real grader: these produce C, B, A and D through the confirmed-label path.
PANEL_C = {"energy_kcal": "380", "sugars_g": "8", "saturated_fat_g": "1",
           "salt_g": "0.5", "protein_g": "7", "fibre_g": "3"}
PANEL_B = {"energy_kcal": "370", "sugars_g": "2", "saturated_fat_g": "1.2",
           "salt_g": "0.3", "protein_g": "12", "fibre_g": "9"}
PANEL_A = {"energy_kcal": "380", "sugars_g": "1", "saturated_fat_g": "1.2",
           "salt_g": "0.02", "protein_g": "13", "fibre_g": "10"}
PANEL_D = {"energy_kcal": "420", "sugars_g": "30", "saturated_fat_g": "2",
           "salt_g": "0.9", "protein_g": "6", "fibre_g": "2"}
PANEL_B_DRINK = {"energy_kcal": "70", "sugars_g": "2", "saturated_fat_g": "0.3",
                 "salt_g": "0.1", "protein_g": "3", "fibre_g": "1.5"}

INGREDIENTS_C = "maize, sugar, salt, flavouring, emulsifier (ins 322)"
INGREDIENTS_B = "whole grain oats, salt"
INGREDIENTS_A = "whole grain oats"
INGREDIENTS_D = "wheat flour, sugar, invert sugar syrup, salt, flavouring, emulsifier (ins 322)"

#: Catalogue nutriments, only ever used to prove they do NOT decide a grade.
OFF_NUTRIMENTS_A = {"energy-kcal_100g": 380, "sugars_100g": 1, "saturated-fat_100g": 1.2,
                    "salt_100g": 0.02, "proteins_100g": 13, "fiber_100g": 10}


def label_facts(
    *,
    product_name: str,
    ingredients: str,
    panel: dict,
    basis: str | None = "per_100g",
    brand: str | None = "Sunfield",
    **extra,
) -> dict:
    """One confirmed pack, in the shape Step 3 writes it."""
    facts = {
        "product_name": product_name, "brand": brand, "ingredients_text": ingredients,
        "nutrition_per_100g": panel, "net_quantity": "200 g", **extra,
    }
    if basis is not None:
        facts["nutrition_basis"] = basis
    return facts


CURRENT_LABEL = label_facts(
    product_name="Northstar Corn Flakes", brand="Northstar",
    ingredients=INGREDIENTS_C, panel=PANEL_C,
)


@pytest_asyncio.fixture
async def off_clean():
    """Store A has its own cleanup, because it is a separate store."""
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
    """An anonymous phone. No account, no subscription, no entitlement."""
    return await register_device(app_client)


async def register_device(app_client) -> dict[str, str]:
    response = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert response.status_code == 201, response.text
    return {"X-Device-Token": response.json()["token"]}


def _published() -> ProductionRuleset:
    candidate = candidate_ruleset()
    return ProductionRuleset(provenance={
        rule_id: replace(row, status=STATUS_PUBLISHED)
        for rule_id, row in candidate.provenance.items()
    })


@pytest.fixture
def published_rules(monkeypatch):
    """Every grading rule through its evidence lifecycle, as production has it."""
    from app.api.v2 import product as product_api

    async def resolve(_session):
        return _published()

    monkeypatch.setattr(product_api, "resolve_production_ruleset", resolve)
    return resolve


@pytest.fixture
def no_off_network(monkeypatch):
    """Every live Open Food Facts lookup this test made, in order.

    Discovery must read the cache and nothing else: no search endpoint, no
    crawl, and above all no request per candidate. The current product's own
    lookup is a different thing and is allowed — it is the path that existed
    before this milestone — so the stub answers "not found" and records the
    barcode instead of failing outright. Tests then assert the exact list, which
    is what distinguishes one permitted lookup from a fan-out.
    """
    calls: list[str] = []

    async def record(barcode: str):
        calls.append(barcode)
        return None

    monkeypatch.setattr(off_client, "fetch_product", record)
    return calls


def fresh_at() -> datetime:
    """A cached copy taken a moment ago. Server time, never a client's."""
    return datetime.now(UTC)


def expired_at() -> datetime:
    """A cached copy one day past the shared freshness window."""
    return datetime.now(UTC) - off_freshness.OFF_CACHE_TTL - timedelta(days=1)


async def seed_off(
    barcode: str,
    *,
    name: str | None = "Catalogue Name",
    brands: str | None = "Catalogue Brand",
    categories: str | None = CEREAL_CATEGORY,
    countries: str | None = "India",
    categories_hierarchy: Any = _UNSET,
    countries_tags: Any = _UNSET,
    off_category_key: Any = _UNSET,
    nutriments: dict | None = None,
    ingredients_text: str | None = INGREDIENTS_B,
    fetched_at: datetime | None = None,
) -> None:
    """Put one Open Food Facts row in Store A, and nothing of ours anywhere.

    ``fetched_at`` defaults to now: a realistic cached copy has an age, and it
    is the age that decides whether the row may support a comparative claim.

    The taxonomy arrays default to whichever ones the raw strings stand in for,
    so a test that only cares about "same kind" or "sold here" says so once. The
    canonical columns are then computed by the *same* function the live cache
    write path uses — a test that hand-wrote them would be checking its own
    arithmetic rather than the encoder that runs in production.
    """
    hierarchy = tags_for_category(categories) if categories_hierarchy is _UNSET else categories_hierarchy
    countries_array = tags_for_country(countries) if countries_tags is _UNSET else countries_tags
    factory = get_off_sessionmaker()
    async with factory() as session:
        session.add(OffProduct(
            barcode=barcode, product_name=name, brands=brands,
            ingredients_text=ingredients_text, nutriments=nutriments,
            categories=categories, countries=countries, quantity="200 g",
            categories_hierarchy=hierarchy, countries_tags=countries_array,
            off_category_key=(
                off_taxonomy.category_fingerprint(hierarchy)
                if off_category_key is _UNSET else off_category_key
            ),
            off_listed_for_india=off_taxonomy.listed_for_india(countries_array),
            fetched_at=fresh_at() if fetched_at is None else fetched_at,
        ))
        await session.commit()


async def seed_label(barcode: str, facts: dict, *, device_id: uuid.UUID | None = None) -> LabelSnapshot:
    """A confirmed pack in Store B, written through the real versioning path.

    ``device_id`` of ``None`` is a capture nobody on this test's devices owns —
    which is exactly the situation the reference-view rules exist for.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        held = ScanEvent(
            device_id=device_id, barcode=barcode, outcome="label_captured",
            client_scan_id=uuid.uuid4().hex, label_facts=facts,
        )
        session.add(held)
        await session.flush()
        row = await product_service.store_label_snapshot(
            session, barcode=barcode, facts=facts, device_id=device_id, scan_event_id=held.id,
        )
        await session.commit()
        return row


async def seed_current(app_client=None, *, facts: dict | None = None, **off) -> None:
    """The pack in the shopper's hand: a confirmed label plus a catalogue row.

    Both halves are needed. The label carries the science and the explicit panel
    basis; the catalogue row carries the category and the India listing, and its
    age decides whether a comparative claim may rest on it.
    """
    await seed_label(CURRENT, facts if facts is not None else CURRENT_LABEL)
    await seed_off(CURRENT, name="Northstar Corn Flakes", brands="Northstar", **off)


async def seed_candidate(
    barcode: str,
    *,
    product_name: str,
    ingredients: str,
    panel: dict,
    basis: str | None = "per_100g",
    brand: str | None = "Sunfield",
    categories: str | None = CEREAL_CATEGORY_OTHER_PATH,
    countries: str | None = "India",
    categories_hierarchy: Any = _UNSET,
    countries_tags: Any = _UNSET,
    off_category_key: Any = _UNSET,
    fetched_at: datetime | None = None,
    off_nutriments: dict | None = None,
    with_label: bool = True,
) -> None:
    """A comparable product: discoverable in Store A, gradeable from Store B."""
    await seed_off(
        barcode, name="Catalogue Name For " + barcode, categories=categories,
        countries=countries, categories_hierarchy=categories_hierarchy,
        countries_tags=countries_tags, off_category_key=off_category_key,
        fetched_at=fetched_at, nutriments=off_nutriments,
    )
    if with_label:
        await seed_label(barcode, label_facts(
            product_name=product_name, ingredients=ingredients, panel=panel,
            basis=basis, brand=brand,
        ))


async def verdict(app_client, headers, barcode: str = CURRENT, *, physical_pack: bool | None = None) -> dict:
    url = f"/api/v2/scan/verdict/{barcode}"
    if physical_pack is not None:
        url += f"?physical_pack_context={'true' if physical_pack else 'false'}"
    response = await app_client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def confirm_label_through_api(app_client, device, token, account_id, barcode, facts) -> None:
    """A pack this device photographed and a person confirmed, via the real route."""
    factory = get_sessionmaker()
    async with factory() as session:
        run = AIRun(
            account_id=account_id, feature="product_label_transcribe", provider="test",
            model="test-model", prompt_version="scan-label.v1", schema_version="scan-label.v1",
            status=AI_STATUS_SUCCEEDED, validation_passed=True,
        )
        session.add(run)
        await session.flush()
        session.add(AIRunOutput(ai_run_id=run.id, schema_version="scan-label.v1", payload=facts))
        await session.commit()
        run_id = run.id
    response = await app_client.post(
        "/api/v2/scan/label/confirm", headers={**device, **auth(token)},
        json={"barcode": barcode, "ai_run_id": str(run_id), "client_scan_id": uuid.uuid4().hex},
    )
    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# The category authority: the non-lossy hierarchy, in full, and nothing looser
# ---------------------------------------------------------------------------
# The deep source-semantics coverage lives in
# tests/test_step6a1_discovery_provenance.py. These few pin the domain-level
# re-exports the rest of this module relies on.
def test_a_missing_or_malformed_hierarchy_has_no_fingerprint():
    """Fail closed: no usable ``categories_hierarchy``, no comparison key."""
    for value in (None, [], "en:breakfast-cereals", 12345, {"en": "x"},
                  ["en:ok", "nocolon"], ["en:ok", 5], ["   "]):
        assert category_module.comparable_category_fingerprint(value) is None


def test_two_paths_to_the_same_classification_are_comparable_and_a_sibling_is_not():
    """Exact whole-set semantics. No parent/child equivalence, no fuzzy distance."""
    assert category_module.same_comparable_category(CEREAL_TAGS, list(reversed(CEREAL_TAGS)))
    assert not category_module.same_comparable_category(CEREAL_TAGS, BAR_TAGS)
    # A broad classification and a specific one are not comparable, in either
    # direction — no element is privileged as "the leaf".
    broad = ["en:cereals"]
    specific = ["en:cereals", "en:breakfast-cereals"]
    assert not category_module.same_comparable_category(broad, specific)
    assert not category_module.same_comparable_category(specific, broad)
    # Near-misses stay misses. Nothing here measures edit distance.
    for near in (["en:breakfast-cereal"], ["en:breakfast_cereals"], ["en:cereals"]):
        assert not category_module.same_comparable_category(CEREAL_TAGS, near), near
    # A missing classification never matches another missing one.
    assert not category_module.same_comparable_category(None, None)
    assert not category_module.same_comparable_category([], [])


def test_the_raw_category_text_and_the_lossy_key_are_not_the_authority():
    """The two removed authorities, pinned so neither can return.

    The raw ``categories`` text is untaxonomised editor's prose; the old
    joined-key helper read the lossy ``categories_tags``. Neither function
    should exist any more, and a comma-separated string is not a hierarchy.
    """
    for removed in ("category_leaf", "coarse_category_filter", "same_source_category",
                    "country_tokens", "INDIA_COUNTRY_TOKENS", "comparable_category_key",
                    "canonical_tags", "category_key"):
        assert not hasattr(category_module, removed), removed
    assert category_module.comparable_category_fingerprint(CEREAL_CATEGORY) is None


# ---------------------------------------------------------------------------
# India availability: what the source's taxonomy says, never what we infer
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("countries_tags", "eligible"),
    [
        (["en:india"], True),
        (["en:india", "en:united-kingdom"], True),
        (["en:united-kingdom", "en:india"], True),
        (["en:united-kingdom"], False),
        (None, False),
        ([], False),
        # Exact id only. Country tags are canonical, so a non-canonical spelling
        # is not accepted — we do not re-normalise and we keep no translation map.
        (["  EN:India  "], False),
        (["EN:INDIA"], False),
        # Never inferred from a look-alike. Their taxonomy gives these their own
        # ids, and only the exact India id counts.
        (["en:british-indian-ocean-territory"], False),
        (["en:indiana"], False),
        # The raw text field is not a country list here either, however it reads.
        ("India", False),
        (["India"], False),
    ],
)
def test_india_availability_is_the_exact_source_taxonomy_tag(countries_tags, eligible):
    assert category_module.listed_for_india(countries_tags) is eligible


def test_india_is_one_canonical_tag_rather_than_a_list_of_spellings():
    """Their taxonomy already resolves the spellings; we read its answer.

    ``taxonomies/countries.txt`` gives the India entry as
    ``en: India, Bharat, Hindustan, IN, IND`` with ``country_code_2:en: IN``,
    so every one of those spellings and every translation already arrives as
    the single id below. Re-deriving that mapping here would be rebuilding
    something they publish, badly.
    """
    assert category_module.INDIA_COUNTRY_TAG == "en:india"
    for spelling in ("en:bharat", "en:hindustan", "en:in", "en:ind"):
        assert not category_module.listed_for_india([spelling]), spelling


# ---------------------------------------------------------------------------
# Source freshness: one policy, shared with the product lookup
# ---------------------------------------------------------------------------
def test_one_freshness_window_serves_both_readers():
    """Two constants would drift. The lookup re-exports the shared one."""
    assert product_service.OFF_CACHE_TTL is off_freshness.OFF_CACHE_TTL
    assert timedelta(days=30) == off_freshness.OFF_CACHE_TTL
    # And the alternative domain reads the helper rather than a number.
    source = inspect.getsource(alternatives_service)
    assert "off_freshness.is_fresh" in source
    assert "timedelta(days=30)" not in source
    assert "OFF_CACHE_TTL =" not in source


def test_an_undated_copy_is_never_treated_as_recent():
    now = datetime(2026, 9, 1, tzinfo=UTC)
    assert off_freshness.is_fresh(now - timedelta(days=1), now=now)
    assert off_freshness.is_fresh(now - timedelta(days=29), now=now)
    assert not off_freshness.is_fresh(now - timedelta(days=31), now=now)
    # A record we cannot date is a record we cannot vouch for.
    assert not off_freshness.is_fresh(None, now=now)
    assert off_freshness.is_stale(None, now=now)
    # A naive stamp is read as UTC rather than rejected or trusted blindly.
    assert off_freshness.is_fresh(datetime(2026, 8, 31), now=now)


# ---------------------------------------------------------------------------
# The policy: strictly higher, no worse a decision, a basis somebody printed
# ---------------------------------------------------------------------------
def test_only_a_strictly_higher_grade_qualifies():
    assert policy_module.strictly_better_grade(Grade.B, Grade.C)
    assert policy_module.strictly_better_grade(Grade.A, Grade.E)
    # Equal is not better. There is no same-grade optimisation in V1.
    assert not policy_module.strictly_better_grade(Grade.C, Grade.C)
    assert not policy_module.strictly_better_grade(Grade.D, Grade.C)
    # An unknown letter on either side fails closed rather than being ranked.
    assert not policy_module.strictly_better_grade(None, Grade.C)
    assert not policy_module.strictly_better_grade(Grade.B, None)


def test_a_state_is_never_ranked_as_though_it_were_a_poor_letter():
    """NOT_GRADED and NOT_ENOUGH_INFORMATION are states, not bad scores."""
    from app.domains.nutrition.grading.engine import GradeResult
    from app.domains.nutrition.grading.rules import GradeOutcome

    for outcome in (GradeOutcome.NOT_GRADED, GradeOutcome.NOT_ENOUGH_INFORMATION):
        result = GradeResult(
            engine_version="food-grade-v1", outcome=outcome, grade=None, headline="",
            detail="", nova_group=None, ceiling=None, trace=(),
        )
        assert policy_module.published_grade(result) is None
        assert not policy_module.strictly_better_grade(
            policy_module.published_grade(result), Grade.E,
        )


def test_the_candidate_decision_may_not_contradict_the_card():
    assert policy_module.action_is_no_worse("buy", "wait")
    assert policy_module.action_is_no_worse("wait", "wait")
    assert not policy_module.action_is_no_worse("skip", "buy")
    assert not policy_module.action_is_no_worse("wait", "buy")
    # An action we cannot establish makes the candidate ineligible; it is never
    # assumed to be equivalent.
    assert not policy_module.action_is_no_worse(None, "buy")
    assert not policy_module.action_is_no_worse("buy", None)
    assert not policy_module.action_is_no_worse("recommended", "buy")
    # No new action vocabulary was invented for this milestone.
    assert policy_module.ACTION_ORDER == ("buy", "wait", "skip")


def test_a_basis_is_source_known_only_when_a_pack_stated_it():
    """The correction at the heart of this milestone.

    ``basis_for()`` decides "drink" because a name or a category contains the
    word *milk*. That guess may pick a threshold table; it may never be
    published as a statement about how two products were compared.
    """
    assert policy_module.source_known_basis("per_100g")
    assert policy_module.source_known_basis("per_100ml")
    for guess in (None, "", "  ", "solid", "drink", "per_serving", "per_100_g", 100, True, {}):
        assert not policy_module.source_known_basis(guess), guess


def test_only_matching_per_hundred_bases_are_compared():
    assert policy_module.comparable_basis("solid", "solid")
    assert policy_module.comparable_basis("drink", "drink")
    # No millilitre-to-gram conversion exists, because it needs a density.
    assert not policy_module.comparable_basis("drink", "solid")
    assert not policy_module.comparable_basis("solid", "drink")
    assert not policy_module.comparable_basis("unknown", "solid")
    assert not policy_module.comparable_basis(None, "solid")
    assert policy_module.basis_key("solid") == "per_100g"
    assert policy_module.basis_key("drink") == "per_100ml"
    assert policy_module.basis_key("unknown") is None


def test_selection_is_lexicographic_and_never_a_composite_number():
    """Grade, then action, then barcode. Three comparisons, no arithmetic."""
    better = policy_module.Candidate("9", "Better", "B", Grade.A, "buy", "solid")
    same_grade_worse_action = policy_module.Candidate("1", "Wait", "B", Grade.A, "wait", "solid")
    lower = policy_module.Candidate("0", "Lower", "B", Grade.B, "buy", "solid")

    assert policy_module.select([lower, same_grade_worse_action, better]) is better
    assert policy_module.select([lower, same_grade_worse_action]) is same_grade_worse_action
    assert policy_module.select([]) is None

    first = policy_module.Candidate("111", "A", None, Grade.A, "buy", "solid")
    second = policy_module.Candidate("222", "B", None, Grade.A, "buy", "solid")
    assert policy_module.select([second, first]) is first
    assert policy_module.select([first, second]) is first


# ---------------------------------------------------------------------------
# Structural guards
# ---------------------------------------------------------------------------
#: The three modules the whole feature is made of. The structural tests below
#: read their syntax tree rather than their text, so a rule can be *described*
#: in a docstring — "there is no alternative_score" — without the description
#: tripping the check that enforces it.
ALTERNATIVE_MODULES = (category_module, policy_module, alternatives_service)


def _code_identifiers(module) -> set[str]:
    """Every name and literal the module's code actually uses, docstrings aside."""
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


def _imported_modules(module) -> list[str]:
    tree = ast.parse(inspect.getsource(module))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return modules


FORBIDDEN_SCORE_NAMES = (
    "alternative_score", "best_choice_score", "health_value_score", "quality_score",
    "health_score", "value_score", "weighted_score", "composite_score", "weighting",
    "overall_score", "rank_score", "fit_score",
)

FORBIDDEN_PRICE_NAMES = (
    "price", "price_paise", "mrp", "retail_price", "value_for_money", "discount",
    "affiliate", "retailer", "cheapest", "price_per_100g", "price_per_unit",
)


def test_the_alternative_domain_contains_no_composite_score():
    """A structural regression: the number must not exist to be hidden."""
    for module in ALTERNATIVE_MODULES:
        identifiers = _code_identifiers(module)
        for name in FORBIDDEN_SCORE_NAMES:
            offenders = [value for value in identifiers if name in value]
            assert not offenders, f"{module.__name__} defines {offenders}"


def test_the_alternative_domain_knows_nothing_about_money():
    """Selection in this milestone is independent of what anything costs."""
    for module in ALTERNATIVE_MODULES:
        identifiers = _code_identifiers(module)
        for name in FORBIDDEN_PRICE_NAMES:
            offenders = [value for value in identifiers if name in value]
            assert not offenders, f"{module.__name__} references {offenders}"


def test_the_alternative_domain_imports_no_layer_that_would_bias_selection():
    """Selection may not read a layer that would make it non-deterministic.

    The candidate's own confirmed label is now read from the product domain, and
    that narrow read is allowed — it is the same canonical truth the candidate's
    Product Result uses, which is the point. Everything that could bias a
    ranking stays banned: AI, shopper observations, official records, money and
    the person.
    """
    forbidden_imports = (
        "ai_gateway", "gemini", "community", "official_records",
        "price", "mrp", "retailer", "affiliate", "razorpay",
        "identity", "profile", "purchase", "beta_access", "consent", "family",
        "progress", "planning", "inventory", "recommendation",
    )
    for module in ALTERNATIVE_MODULES:
        for imported in _imported_modules(module):
            for banned in forbidden_imports:
                assert banned not in imported.lower(), f"{module.__name__} imports {imported}"


def test_the_product_domain_read_is_narrow_and_deliberate():
    """Only the canonical label path, and nothing else from that domain."""
    product_imports = [
        name for name in _imported_modules(alternatives_service)
        if name.startswith("app.domains.product")
    ]
    assert sorted(product_imports) == [
        "app.domains.product", "app.domains.product.models",
    ], product_imports
    used = _code_identifiers(alternatives_service)
    # The two things it may reach for, and no scan, device, complaint or report.
    assert "latest_label_snapshots" in used
    assert "result_identity" in used
    for reachable in ("scanevent", "scandevice", "labelerrorreport", "productrecord",
                      "fssaicomplainthandoff", "record_scan", "devices"):
        assert reachable not in used, reachable


def test_discovery_is_bounded_by_named_constants():
    """Bounded in both dimensions, and the total is derived rather than typed.

    A page size without a page limit is not a bound, and two numbers that have
    to be multiplied by hand eventually disagree with each other.
    """
    for name in ("DISCOVERY_PAGE_SIZE", "MAX_DISCOVERY_PAGES", "MAX_DISCOVERY_ROWS"):
        value = getattr(policy_module, name)
        assert isinstance(value, int) and value > 0, name
    assert policy_module.MAX_DISCOVERY_ROWS == (
        policy_module.DISCOVERY_PAGE_SIZE * policy_module.MAX_DISCOVERY_PAGES
    )
    assert policy_module.MAX_DISCOVERY_ROWS <= 1000


# ---------------------------------------------------------------------------
# Canonical candidate truth
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_higher_graded_same_category_indian_product_is_offered(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The one case that produces a candidate, and everything it carries."""
    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )

    body = await verdict(app_client, device)
    envelope = body["alternative"]

    assert envelope["policy_version"] == "comparable-food-alternative-v1"
    assert envelope["status"] == "available"
    assert envelope["reason_key"] == "comparable_option_found"

    candidate = envelope["candidate"]
    assert candidate["barcode"] == CANDIDATE_B
    # The name and brand come from the confirmed pack, never from the catalogue.
    assert candidate["product_name"] == "Sunfield Oat Porridge"
    assert candidate["brand"] == "Sunfield"
    assert candidate["grade"] == "B"
    assert candidate["band"] == "green"
    assert candidate["decision"] == "buy"
    assert candidate["comparison"] == {
        "category_match": "exact_source_taxonomy",
        "category_source": "open_food_facts",
        "current_grade": "C",
        "candidate_grade": "B",
        "basis": "per_100g",
    }
    assert candidate["attribution"]["text"] == ATTRIBUTION_TEXT
    assert body["grade"] == "C"
    assert body["result_contract_version"] == "v1"


@pytest.mark.asyncio
async def test_a_catalogue_only_candidate_is_never_offered(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """No confirmed label, no comparison — however good the catalogue looks.

    Open Food Facts does not say whether a panel was printed per 100 g or per
    100 ml, and a card that reports a basis has to know one.
    """
    await seed_current()
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
        off_nutriments=OFF_NUTRIMENTS_A, with_label=False,
    )

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["status"] == "not_enough_information"
    assert envelope["reason_key"] == "no_comparable_candidate_in_cached_data"
    assert envelope["candidate"] is None

    # The same product, once a pack has been confirmed, does qualify.
    await seed_label(CANDIDATE_A, label_facts(
        product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
    ))
    after = (await verdict(app_client, device))["alternative"]
    assert after["candidate"]["barcode"] == CANDIDATE_A


@pytest.mark.asyncio
async def test_a_candidate_whose_pack_never_stated_its_basis_is_ineligible(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Explicit per_100g / per_100ml, or nothing. Never inferred."""
    await seed_current()
    for barcode, basis in ((CANDIDATE_A, None), (CANDIDATE_B, "per_serving")):
        await seed_candidate(
            barcode, product_name=f"Oats {barcode}", ingredients=INGREDIENTS_A,
            panel=PANEL_A, basis=basis,
        )

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["candidate"] is None


@pytest.mark.asyncio
async def test_a_drink_is_never_offered_against_a_solid(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Same source category, better letter, incomparable panel.

    Proven both ways: the same product read per 100 g does qualify, so it is the
    basis and nothing else that rejected the drink.
    """
    await seed_current()
    await seed_candidate(
        DRINK, product_name="Oat Porridge Drink", ingredients=INGREDIENTS_B,
        panel=PANEL_B_DRINK, basis="per_100ml",
    )

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["candidate"] is None

    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )
    after = (await verdict(app_client, device))["alternative"]
    assert after["candidate"]["barcode"] == CANDIDATE_B
    assert after["candidate"]["comparison"]["basis"] == "per_100g"


@pytest.mark.asyncio
async def test_the_current_product_also_needs_a_basis_somebody_printed(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Both halves of a stated comparison must rest on a printed basis.

    A catalogue-only current product still gets its ordinary verdict — the
    grading adapter picks a threshold table and that is fine. What it does not
    get is a published comparison whose ``basis`` field would be describing a
    guess on one side.
    """
    await seed_off(
        CURRENT, name="Northstar Corn Flakes", ingredients_text=INGREDIENTS_C,
        nutriments={"energy-kcal_100g": 380, "sugars_100g": 8, "saturated-fat_100g": 1,
                    "salt_100g": 0.5, "proteins_100g": 7, "fiber_100g": 3},
    )
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )

    body = await verdict(app_client, device)
    assert body["outcome"] == "graded"
    assert body["facts_provenance"] == "open_food_facts"
    assert body["alternative"]["reason_key"] == "current_product_basis_not_source_known"
    assert body["alternative"]["candidate"] is None


@pytest.mark.asyncio
async def test_a_stale_confirmed_capture_does_not_reach_past_itself(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The latest snapshot is the answer, complete or not.

    A newer capture that no longer carries a panel means we do not currently
    know this product well enough — reaching back to an older complete version
    would answer with facts the pack may no longer have.
    """
    await seed_current()
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
    )
    assert (await verdict(app_client, device))["alternative"]["candidate"]["barcode"] == CANDIDATE_A

    # A later capture of the same pack that could not read the panel.
    await seed_label(CANDIDATE_A, label_facts(
        product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel={}, basis=None,
    ))
    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(LabelSnapshot).where(LabelSnapshot.barcode == CANDIDATE_A)
            .order_by(LabelSnapshot.version_number)
        )).scalars().all()
    assert [row.version_number for row in rows] == [1, 2]
    assert rows[1].completeness == "incomplete_for_grading"

    after = (await verdict(app_client, device))["alternative"]
    assert after["candidate"] is None


@pytest.mark.asyncio
async def test_the_catalogue_cannot_overrule_the_confirmed_pack(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The contradiction case, in both directions.

    A catalogue row whose numbers would grade A does not make a product an A.
    The card must never offer a "Grade A better option" for a pack whose own
    Product Result says Grade D and SKIP.
    """
    await seed_current()
    # Catalogue says A. The pack, confirmed by a person, is a D.
    await seed_candidate(
        CANDIDATE_D, product_name="Sweet Flakes", ingredients=INGREDIENTS_D, panel=PANEL_D,
        off_nutriments=OFF_NUTRIMENTS_A,
    )

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["candidate"] is None

    # And its own Product Result agrees it is a D.
    detail = await verdict(app_client, device, barcode=CANDIDATE_D)
    assert detail["grade"] == "D"
    assert detail["decision"]["action"] == "skip"

    # Now the positive half: catalogue numbers differ, the confirmed pack is B,
    # and the card publishes B — the pack's letter, not the catalogue's.
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B, off_nutriments=OFF_NUTRIMENTS_A,
    )
    offered = (await verdict(app_client, device))["alternative"]["candidate"]
    assert offered["barcode"] == CANDIDATE_B
    assert offered["grade"] == "B"


@pytest.mark.asyncio
async def test_the_card_and_the_product_it_opens_state_the_same_thing(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The card is not an alternate truth surface.

    Grade, decision and name are compared, not the grade alone: a card that
    agrees on the letter and disagrees on the action or the name is still a card
    the shopper cannot trust.
    """
    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B, off_nutriments=OFF_NUTRIMENTS_A,
    )

    card = (await verdict(app_client, device))["alternative"]["candidate"]
    detail = await verdict(app_client, device, barcode=CANDIDATE_B, physical_pack=False)

    assert card["grade"] == detail["grade"]
    assert card["decision"] == detail["decision"]["action"]
    assert card["product_name"] == detail["product_name"]
    assert card["brand"] == detail["brand"]
    # And the same holds through the ordinary, non-reference read.
    ordinary = await verdict(app_client, device, barcode=CANDIDATE_B)
    assert (card["grade"], card["decision"], card["product_name"]) == (
        ordinary["grade"], ordinary["decision"]["action"], ordinary["product_name"],
    )


@pytest.mark.asyncio
async def test_a_candidate_with_no_name_on_its_pack_is_not_recommended(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """A barcode is an identifier, not a name.

    "Better option: 8901000000004" is not a suggestion anybody can act on, so a
    candidate whose canonical facts carry no name is not offered at all.
    """
    await seed_current()
    # Discoverable in Store A, gradeable in Store B, and nameless on the pack.
    await seed_off(CANDIDATE_A, categories=CEREAL_CATEGORY_OTHER_PATH)
    factory = get_sessionmaker()
    for blank in ("", "   "):
        async with factory() as session:
            await session.execute(
                LabelSnapshot.__table__.delete().where(LabelSnapshot.barcode == CANDIDATE_A)
            )
            await session.commit()
        await seed_label(CANDIDATE_A, label_facts(
            product_name=blank, ingredients=INGREDIENTS_A, panel=PANEL_A,
        ))
        envelope = (await verdict(app_client, device))["alternative"]
        assert envelope["candidate"] is None, repr(blank)

    # The same pack, once its name is read, is offered.
    async with factory() as session:
        await session.execute(
            LabelSnapshot.__table__.delete().where(LabelSnapshot.barcode == CANDIDATE_A)
        )
        await session.commit()
    await seed_label(CANDIDATE_A, label_facts(
        product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
    ))
    offered = (await verdict(app_client, device))["alternative"]["candidate"]
    assert offered["product_name"] == "Rolled Oats"
    # And the barcode is never smuggled in as the name.
    assert offered["product_name"] != offered["barcode"]


# ---------------------------------------------------------------------------
# Freshness gates the comparative claim
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_expired_candidate_copy_cannot_support_a_comparison(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Expired and undated both fail closed, and neither is refreshed."""
    await seed_current()
    await seed_candidate(
        STALE, product_name="Stale Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
        fetched_at=expired_at(),
    )
    assert (await verdict(app_client, device))["alternative"]["candidate"] is None

    # An undated copy is not evidence of anything either.
    factory = get_off_sessionmaker()
    async with factory() as session:
        row = await session.get(OffProduct, STALE)
        row.fetched_at = None
        await session.commit()
    assert (await verdict(app_client, device))["alternative"]["candidate"] is None

    # No refresh was attempted for the candidate. Fan-out stays absolute.
    assert STALE not in no_off_network


@pytest.mark.asyncio
async def test_a_fresh_candidate_wins_over_a_stale_better_one(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The stale row is ignored, not preferred and not merely down-ranked."""
    await seed_current()
    # The stale one would otherwise win outright: an A against a B.
    await seed_candidate(
        STALE, product_name="Stale Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
        fetched_at=expired_at(),
    )
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )

    candidate = (await verdict(app_client, device))["alternative"]["candidate"]
    assert candidate["barcode"] == CANDIDATE_B
    assert candidate["grade"] == "B"


@pytest.mark.asyncio
async def test_an_expired_current_category_makes_no_comparative_claim(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Showing a product and comparing two products are different acts.

    The ordinary Product Result goes on working from the expired copy, exactly
    as it did before this milestone. The comparison does not.
    """
    await seed_label(CURRENT, CURRENT_LABEL)
    await seed_off(CURRENT, name="Northstar Corn Flakes", fetched_at=expired_at())
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
    )

    body = await verdict(app_client, device)
    # Unchanged lookup behaviour: the product still answers.
    assert body["outcome"] == "graded"
    assert body["grade"] == "C"
    assert body["facts_provenance"] == "confirmed_label_snapshot"
    # But no comparative claim rests on an out-of-date copy of their category.
    assert body["alternative"]["reason_key"] == "source_category_copy_is_out_of_date"
    assert body["alternative"]["candidate"] is None

    # Refreshing the copy — without touching anything else — restores it.
    factory = get_off_sessionmaker()
    async with factory() as session:
        row = await session.get(OffProduct, CURRENT)
        row.fetched_at = fresh_at()
        await session.commit()
    assert (await verdict(app_client, device))["alternative"]["candidate"]["barcode"] == CANDIDATE_A


# ---------------------------------------------------------------------------
# The rest of the eligibility matrix
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_same_or_lower_graded_candidate_is_never_offered(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Equal is not better, however clean the other label reads."""
    await seed_current()
    await seed_candidate(
        CANDIDATE_C, product_name="Rival Corn Flakes", ingredients=INGREDIENTS_C, panel=PANEL_C,
    )
    await seed_candidate(
        CANDIDATE_D, product_name="Sweet Flakes", ingredients=INGREDIENTS_D, panel=PANEL_D,
    )

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["status"] == "not_enough_information"
    assert envelope["reason_key"] == "no_comparable_candidate_in_cached_data"
    assert envelope["candidate"] is None


@pytest.mark.asyncio
async def test_a_not_graded_current_product_gets_no_alternative(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """A cooking ingredient has no letter, so there is nothing to improve on.

    This is the rule that stops the system inventing a "better ghee".
    """
    await seed_label(CURRENT, label_facts(
        product_name="Ghee", brand="Northstar", ingredients="ghee",
        panel={"energy_kcal": "900", "saturated_fat_g": "60", "sugars_g": "0", "salt_g": "0"},
    ))
    await seed_off(CURRENT, name="Ghee", categories="Foods, Ghee")
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
        categories="Plant foods, Ghee",
    )

    body = await verdict(app_client, device)
    assert body["outcome"] == "not_graded"
    assert body["alternative"]["reason_key"] == "current_product_has_no_published_grade"
    assert body["alternative"]["candidate"] is None


@pytest.mark.asyncio
async def test_a_current_product_we_cannot_grade_gets_no_alternative(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Not enough information about this pack is not a licence to compare it."""
    await seed_label(CURRENT, label_facts(
        product_name="Mystery Flakes", ingredients="", panel=PANEL_C,
    ))
    await seed_off(CURRENT, name="Mystery Flakes")
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
    )

    body = await verdict(app_client, device)
    assert body["outcome"] == "not_enough_information"
    assert body["alternative"]["reason_key"] == "current_product_has_no_published_grade"
    assert body["alternative"]["candidate"] is None


@pytest.mark.asyncio
async def test_a_candidate_missing_ingredients_or_nutrition_is_ineligible(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """A candidate is held to exactly the bar the current product met."""
    await seed_current()
    await seed_candidate(
        CANDIDATE_A, product_name="No Ingredient List Oats", ingredients="", panel=PANEL_A,
    )
    await seed_candidate(
        CANDIDATE_B, product_name="No Panel Oats", ingredients=INGREDIENTS_A, panel={},
    )

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["candidate"] is None


@pytest.mark.asyncio
async def test_an_unpublished_required_rule_surfaces_no_comparison_at_all(
    db_clean, off_clean, app_client, device, no_off_network,
):
    """Candidate constants do not become a customer comparison.

    ``published_rules`` is deliberately absent here, so the real resolver runs
    against an empty evidence domain. Both products are then ungradeable — the
    same ruleset governs both — and the honest answer is that we do not know.
    """
    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )

    body = await verdict(app_client, device)
    assert body["outcome"] == "not_enough_information"
    assert body["alternative"]["reason_key"] == "current_product_has_no_published_grade"


@pytest.mark.asyncio
async def test_both_products_are_judged_by_the_same_resolved_ruleset(
    db_clean, off_clean, app_client, device, monkeypatch, no_off_network,
):
    """The ruleset object handed to the alternative service is the same one.

    Not an equal copy — the identical object. A second resolution could differ
    from the first, and a letter from one ruleset compared against a letter from
    another is not a comparison at all.
    """
    from app.api.v2 import product as product_api

    resolved: list[ProductionRuleset] = []

    async def resolve(_session):
        ruleset = _published()
        resolved.append(ruleset)
        return ruleset

    seen: list[ProductionRuleset] = []
    real_envelope = alternatives_service.comparable_alternative_envelope

    async def capture(session, **kwargs):
        seen.append(kwargs["ruleset"])
        return await real_envelope(session, **kwargs)

    monkeypatch.setattr(product_api, "resolve_production_ruleset", resolve)
    monkeypatch.setattr(product_api.alternatives_service, "comparable_alternative_envelope", capture)

    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )
    body = await verdict(app_client, device)

    assert len(resolved) == 1, "the request resolved the production ruleset more than once"
    assert len(seen) == 1
    assert seen[0] is resolved[0]
    assert body["alternative"]["candidate"]["barcode"] == CANDIDATE_B


# ---------------------------------------------------------------------------
# Discovery: bounded, deterministic, and only ever one candidate
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_product_is_never_its_own_alternative(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Obvious, and therefore worth a test rather than an assumption."""
    await seed_label(CURRENT, label_facts(
        product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B, panel=PANEL_B,
    ))
    await seed_off(CURRENT)

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["candidate"] is None
    assert envelope["reason_key"] == "no_comparable_candidate_in_cached_data"


@pytest.mark.asyncio
async def test_a_different_source_leaf_is_not_a_comparable_category(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Cereal bars are not breakfast cereals, whatever they have in common."""
    await seed_current()
    await seed_candidate(
        OTHER_CATEGORY, product_name="Oat Bar", ingredients=INGREDIENTS_A, panel=PANEL_A,
        categories=BAR_CATEGORY,
    )
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
        categories="Foods, Breakfasts",
    )

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["candidate"] is None


@pytest.mark.asyncio
async def test_a_product_the_source_does_not_list_for_india_is_ineligible(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The India gate, end to end. A missing country list is not availability."""
    await seed_current()
    await seed_candidate(
        UK_ONLY, product_name="British Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
        countries="United Kingdom",
    )
    await seed_candidate(
        NO_COUNTRY, product_name="Unlisted Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
        countries=None,
    )

    assert (await verdict(app_client, device))["alternative"]["candidate"] is None

    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
        countries="United Kingdom, India",
    )
    after = (await verdict(app_client, device))["alternative"]
    assert after["candidate"]["barcode"] == CANDIDATE_A


@pytest.mark.asyncio
async def test_the_public_contract_carries_exactly_one_candidate(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Several may be evaluated. One is published — never a ranked list."""
    await seed_current()
    for barcode in (CANDIDATE_B, CANDIDATE_B_LATER):
        await seed_candidate(
            barcode, product_name=f"Oats {barcode}", ingredients=INGREDIENTS_B, panel=PANEL_B,
        )
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
    )

    envelope = (await verdict(app_client, device))["alternative"]
    assert isinstance(envelope["candidate"], dict)
    assert envelope["candidate"]["barcode"] == CANDIDATE_A
    assert envelope["candidate"]["grade"] == "A"
    assert "candidates" not in envelope
    assert "alternatives" not in envelope


@pytest.mark.asyncio
async def test_the_public_candidate_exposes_no_internals(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """One product's public facts. Not the pool, the query or a ranking."""
    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )

    body = await verdict(app_client, device)
    assert set(body["alternative"]) == {"policy_version", "status", "reason_key", "candidate"}
    assert set(body["alternative"]["candidate"]) == {
        "barcode", "product_name", "brand", "grade", "band", "decision",
        "comparison", "attribution",
    }
    serialised = str(body["alternative"])
    for leaked in ("ilike", "%breakfast%", "candidate_pool", "categories", CEREAL_CATEGORY,
                   "fetched_at", "snapshot", "version_number", "completeness"):
        assert leaked not in serialised, leaked

    # And no Store B identifier travels with it.
    factory = get_sessionmaker()
    async with factory() as session:
        snapshot = (await session.execute(
            select(LabelSnapshot).where(LabelSnapshot.barcode == CANDIDATE_B)
        )).scalar_one()
    assert str(snapshot.id) not in serialised


@pytest.mark.asyncio
async def test_selection_is_stable_whatever_order_the_rows_arrive_in(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Same inputs, same barcode, every time — and never a random tie-break."""
    tied = [CANDIDATE_B, CANDIDATE_B_LATER, "8901000000099"]
    await seed_current()
    for barcode in tied:
        await seed_candidate(
            barcode, product_name=f"Oats {barcode}", ingredients=INGREDIENTS_B, panel=PANEL_B,
        )

    first = (await verdict(app_client, device))["alternative"]["candidate"]["barcode"]
    assert first == min(tied), "the tie-break is not the lowest barcode"

    factory = get_off_sessionmaker()
    async with factory() as session:
        for barcode in tied:
            await session.delete(await session.get(OffProduct, barcode))
        await session.commit()
    for barcode in reversed(tied):
        await seed_off(barcode, categories=CEREAL_CATEGORY_OTHER_PATH)

    again = (await verdict(app_client, device))["alternative"]["candidate"]["barcode"]
    assert again == first

    for _ in range(3):
        assert (await verdict(app_client, device))["alternative"]["candidate"]["barcode"] == first


@pytest.mark.asyncio
async def test_a_wildcard_in_a_category_cannot_widen_the_search(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """A source category is data, not a pattern."""
    await seed_current(categories="Foods, 100%pure")
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
        categories="Foods, 100 grain pure",
    )
    assert (await verdict(app_client, device))["alternative"]["candidate"] is None

    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B, categories="Plant foods, 100%pure",
    )
    after = (await verdict(app_client, device))["alternative"]
    assert after["candidate"]["barcode"] == CANDIDATE_B


@pytest.mark.asyncio
async def test_one_page_of_discovery_is_capped(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, monkeypatch,
):
    """A Product Result never turns into an unbounded scan of Store A."""
    monkeypatch.setattr(alternatives_service, "DISCOVERY_PAGE_SIZE", 3)
    await seed_current()
    for index in range(10):
        await seed_off(f"890100002{index:04d}", categories=CEREAL_CATEGORY_OTHER_PATH)

    factory = get_off_sessionmaker()
    async with factory() as session:
        rows = await alternatives_service._discover_page(
            session, fingerprint=off_taxonomy.category_fingerprint(CEREAL_TAGS),
            exclude_barcode=CURRENT, cutoff=fresh_at() - off_freshness.OFF_CACHE_TTL,
            after=None,
        )
    assert len(rows) == 3
    assert [row.barcode for row in rows] == sorted(row.barcode for row in rows)


@pytest.mark.asyncio
async def test_every_source_gate_runs_before_the_row_limit(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, monkeypatch,
):
    """A row that cannot qualify must not consume a place in the window.

    This is the second half of the starvation, and the cheaper half to get
    wrong. When the category, country and freshness gates run in Python *after*
    a ``LIMIT``, a shelf of stale or foreign rows fills the window and is then
    thrown away, so a window named fifty can yield nothing at all while
    qualifying rows sit just past it. Every gate is therefore SQL, and this
    proves it: a page of one, taken over a table whose first rows are all
    disqualified, still returns the qualifying row.
    """
    monkeypatch.setattr(alternatives_service, "DISCOVERY_PAGE_SIZE", 1)
    await seed_current()
    # Three rows that sort first and each fail a different gate.
    await seed_off("8901000010001", categories=BAR_CATEGORY)                 # other category
    await seed_off("8901000010002", countries="United Kingdom")              # not India
    await seed_off("8901000010003", fetched_at=expired_at())                 # expired copy
    await seed_off("8901000010004")                                          # the only qualifier

    factory = get_off_sessionmaker()
    async with factory() as session:
        rows = await alternatives_service._discover_page(
            session, fingerprint=off_taxonomy.category_fingerprint(CEREAL_TAGS),
            exclude_barcode=CURRENT, cutoff=fresh_at() - off_freshness.OFF_CACHE_TTL,
            after=None,
        )
    assert [row.barcode for row in rows] == ["8901000010004"]


@pytest.mark.asyncio
async def test_a_valid_candidate_behind_a_full_page_of_unusable_ones_is_still_found(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, monkeypatch,
):
    """The starvation this milestone exists to remove, end to end.

    Sixty products qualify on everything Store A can see — same classification,
    listed for India, freshly copied — and not one of them has ever had its
    label photographed, so none can be graded. The sixty-first is a real,
    offerable alternative.

    Under a single window of fifty, that candidate was unreachable for ever: the
    window always started at the same place, always filled with the same sixty
    unusable rows, and always returned nothing. No amount of re-scanning, waiting
    or refreshing would have changed it, because nothing about it was random.
    Paging walks past them.
    """
    monkeypatch.setattr(alternatives_service, "DISCOVERY_PAGE_SIZE", 10)
    await seed_current()
    # Sixty source-qualified rows with no confirmed label anywhere in Store B.
    for index in range(60):
        await seed_off(f"890100003{index:04d}", categories=CEREAL_CATEGORY_OTHER_PATH)
    # And, sorting after all of them, one that can actually be offered.
    await seed_candidate(
        "8901000390000", product_name="Rolled Oats", ingredients=INGREDIENTS_A,
        panel=PANEL_A,
    )

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["status"] == "available"
    assert envelope["candidate"]["barcode"] == "8901000390000"


@pytest.mark.asyncio
async def test_running_out_of_budget_is_not_reported_as_running_out_of_products(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, monkeypatch,
):
    """Two different sentences, because they are two different facts.

    "We looked at everything we hold and found nothing comparable" is a
    statement about the cached data. "We stopped looking" is a statement about
    our own work limit. Collapsing them would let a capacity ceiling be read as
    a fact about the market, and would hide the one signal that says the ceiling
    needs raising.
    """
    monkeypatch.setattr(alternatives_service, "DISCOVERY_PAGE_SIZE", 2)
    monkeypatch.setattr(alternatives_service, "MAX_DISCOVERY_PAGES", 2)
    await seed_current()
    for index in range(10):
        await seed_off(f"890100004{index:04d}", categories=CEREAL_CATEGORY_OTHER_PATH)

    exhausted = (await verdict(app_client, device))["alternative"]
    assert exhausted["status"] == "not_enough_information"
    assert exhausted["reason_key"] == policy_module.REASON_SEARCH_BUDGET_EXHAUSTED

    # The same shelf, with a budget that reaches the end of it, says the other
    # thing — and the customer-facing status is identical either way.
    monkeypatch.setattr(alternatives_service, "MAX_DISCOVERY_PAGES", 10)
    finished = (await verdict(app_client, device))["alternative"]
    assert finished["status"] == "not_enough_information"
    assert finished["reason_key"] == policy_module.REASON_NO_COMPARABLE_CANDIDATE


@pytest.mark.asyncio
async def test_paging_stops_as_soon_as_nothing_left_could_win(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, monkeypatch,
):
    """The budget is a ceiling, not a quota to spend.

    Discovery walks Store A in ascending barcode order and barcode is the final
    tie-break, so once the top of both ladders is held, no row further down can
    displace it. Reading on would change the bill and not the answer.
    """
    monkeypatch.setattr(alternatives_service, "DISCOVERY_PAGE_SIZE", 2)
    pages: list[int] = []
    real_page = alternatives_service._discover_page

    async def counting(*args, **kwargs):
        pages.append(1)
        return await real_page(*args, **kwargs)

    monkeypatch.setattr(alternatives_service, "_discover_page", counting)

    await seed_current()
    # A grade-A, buy candidate first, then plenty more that cannot beat it.
    await seed_candidate(
        "8901000500001", product_name="Rolled Oats", ingredients=INGREDIENTS_A,
        panel=PANEL_A,
    )
    for index in range(20):
        await seed_off(f"890100051{index:04d}", categories=CEREAL_CATEGORY_OTHER_PATH)

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["candidate"]["barcode"] == "8901000500001"
    assert envelope["candidate"]["grade"] == "A"
    # One page was enough. Without the early stop this would be eleven.
    assert len(pages) == 1, pages


@pytest.mark.asyncio
async def test_a_provisional_winner_is_withheld_until_its_rank_is_global(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, monkeypatch,
):
    """A Grade B in hand does not rule out an unseen Grade A.

    Within a tiny budget a valid Grade-B/BUY candidate is found; a valid
    Grade-A/BUY candidate sits immediately past the budget. Because the B is not
    provably global — better rows remain unread — it must be **withheld**, and
    the honest answer is that the budget ran out, not that nothing exists.
    Raising the budget to reach the rest then yields the A.
    """
    monkeypatch.setattr(alternatives_service, "DISCOVERY_PAGE_SIZE", 1)
    monkeypatch.setattr(alternatives_service, "MAX_DISCOVERY_PAGES", 1)
    await seed_current()
    # Barcode order puts the B first (inside the 1-row budget) and the A next.
    await seed_candidate(
        "8901000600001", product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )
    await seed_candidate(
        "8901000600002", product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
    )

    withheld = (await verdict(app_client, device))["alternative"]
    assert withheld["status"] == "not_enough_information"
    assert withheld["reason_key"] == policy_module.REASON_SEARCH_BUDGET_EXHAUSTED
    assert withheld["candidate"] is None, "a provisional B must not be published"

    # Enough budget to reach the A: the global winner, not the provisional one.
    monkeypatch.setattr(alternatives_service, "MAX_DISCOVERY_PAGES", 10)
    resolved = (await verdict(app_client, device))["alternative"]
    assert resolved["status"] == "available"
    assert resolved["candidate"]["barcode"] == "8901000600002"
    assert resolved["candidate"]["grade"] == "A"


@pytest.mark.asyncio
async def test_a_hash_collision_cannot_manufacture_a_match(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The fingerprint is only a discovery key; the hierarchy is the authority.

    Two candidate rows are given the *same* ``off_category_key`` as the current
    product by hand — one with the current product's real hierarchy (the
    control), one with a different hierarchy (the forced collision). Both would
    grade A. Discovery finds both by the shared key, but the exact-hierarchy
    revalidation admits only the control, so a digest collision cannot
    manufacture a comparison.
    """
    current_hierarchy = CEREAL_TAGS
    forced_key = off_taxonomy.category_fingerprint(current_hierarchy)
    # The collider sorts FIRST, so if revalidation were skipped it would win the
    # barcode tie-break and be offered — which is exactly what must not happen.
    collider = "8901000700001"
    control = "8901000700002"

    await seed_current()  # current row carries CEREAL_TAGS -> forced_key
    # Control: matching hierarchy, same key.
    await seed_candidate(
        control, product_name="Rolled Oats Control", ingredients=INGREDIENTS_A, panel=PANEL_A,
        categories_hierarchy=list(current_hierarchy),
    )
    # Collider: a different hierarchy, but forced onto the same discovery key.
    await seed_candidate(
        collider, product_name="Rolled Oats Collider", ingredients=INGREDIENTS_A, panel=PANEL_A,
        categories_hierarchy=list(BAR_TAGS), off_category_key=forced_key,
    )

    # Sanity: the two candidates really do share the discovery key.
    factory = get_off_sessionmaker()
    async with factory() as session:
        keys = {
            bc: (await session.get(OffProduct, bc)).off_category_key
            for bc in (CURRENT, control, collider)
        }
    assert keys[control] == keys[collider] == keys[CURRENT] == forced_key

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["status"] == "available"
    assert envelope["candidate"]["barcode"] == control, "the collider must be rejected"


# ---------------------------------------------------------------------------
# One query for the whole window, not one per candidate
# ---------------------------------------------------------------------------
def _count_snapshot_queries():
    """Count every statement that touches the label-snapshot table."""
    counted: list[str] = []
    engine = get_engine().sync_engine

    def listener(_conn, _cursor, statement, _params, _context, _many):  # noqa: ANN001
        if "product_label_snapshots" in statement:
            counted.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    return counted, lambda: event.remove(engine, "before_cursor_execute", listener)


@pytest.mark.asyncio
async def test_candidate_snapshots_load_in_one_query_however_many_there_are(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """No N+1. The proof is a query count that does not move with N.

    Three candidates and twelve candidates cost the same number of reads of the
    snapshot table: one for the product in hand, and one for the whole window.
    """
    await seed_current()

    async def snapshot_queries_for(count: int) -> int:
        factory = get_off_sessionmaker()
        async with factory() as session:
            for row in (await session.execute(select(OffProduct))).scalars().all():
                if row.barcode != CURRENT:
                    await session.delete(row)
            await session.commit()
        factory = get_sessionmaker()
        async with factory() as session:
            await session.execute(
                LabelSnapshot.__table__.delete().where(LabelSnapshot.barcode != CURRENT)
            )
            await session.commit()
        for index in range(count):
            await seed_candidate(
                f"890100003{index:04d}", product_name=f"Oats {index}",
                ingredients=INGREDIENTS_B, panel=PANEL_B,
            )
        counted, stop = _count_snapshot_queries()
        try:
            body = await verdict(app_client, device)
        finally:
            stop()
        assert body["alternative"]["candidate"] is not None
        return len(counted)

    for_three = await snapshot_queries_for(3)
    for_twelve = await snapshot_queries_for(12)

    assert for_three == for_twelve, (
        f"snapshot reads scale with the candidate count: {for_three} then {for_twelve}"
    )
    # Two: the product in hand, and the whole candidate window.
    assert for_three == 2, for_three


@pytest.mark.asyncio
async def test_the_batch_reader_returns_the_same_latest_as_the_single_reader(
    db_clean, off_clean, app_client, published_rules,
):
    """One definition of "latest", used by both callers, so they cannot drift."""
    barcodes = [CANDIDATE_A, CANDIDATE_B, CANDIDATE_C]
    for barcode in barcodes:
        await seed_label(barcode, label_facts(
            product_name=f"First {barcode}", ingredients=INGREDIENTS_A, panel=PANEL_A,
        ))
        await seed_label(barcode, label_facts(
            product_name=f"Second {barcode}", ingredients=INGREDIENTS_B, panel=PANEL_B,
        ))

    factory = get_sessionmaker()
    async with factory() as session:
        batch = await product_service.latest_label_snapshots(session, barcodes)
        for barcode in barcodes:
            one = await product_service.latest_label_snapshot(session, barcode)
            assert batch[barcode].id == one.id
            assert batch[barcode].version_number == 2
        # An empty ask costs nothing and returns nothing.
        assert await product_service.latest_label_snapshots(session, []) == {}
        # A barcode with no snapshot is simply absent, never a None value.
        assert "8909999999999" not in await product_service.latest_label_snapshots(
            session, [*barcodes, "8909999999999"],
        )


# ---------------------------------------------------------------------------
# Reference mode: opening a product you are not holding
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_reference_view_shows_no_other_shoppers_recall(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, tmp_path,
):
    """Somebody else's packet is not this caller's packet.

    The newest label snapshot for a barcode may be a stranger's photograph of a
    stranger's pack. Reading it as the caller's own would attach that stranger's
    lot — and the recall matched to it — to a pack this device has never seen.
    """
    batch = "B-6A-REF"
    candidate_facts = label_facts(
        product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B, panel=PANEL_B,
        fssai_licence=LICENCE, batch_number=batch,
    )
    await seed_current()
    await seed_off(CANDIDATE_B, categories=CEREAL_CATEGORY_OTHER_PATH)
    # A capture owned by nobody on this test's devices.
    await seed_label(CANDIDATE_B, candidate_facts)

    path = make_export(
        tmp_path / f"foscos-{uuid.uuid4().hex}.xlsx",
        rows=[data_row(recall_id=90101, batch=batch, brand="Sunfield",
                       product="Sunfield Oat Porridge", status="Initiated", termination="NA")],
    )
    factory = get_sessionmaker()
    async with factory() as session:
        await official_records.ingest_recall_xlsx(
            session, path, source_checked_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        )
        await session.commit()

    # The card offers it, on product science alone.
    assert (await verdict(app_client, device))["alternative"]["candidate"]["barcode"] == CANDIDATE_B

    reference = await verdict(app_client, device, barcode=CANDIDATE_B, physical_pack=False)
    assert reference["physical_pack_context"] is False
    # The product science is unchanged — that is a fact about the product.
    assert reference["grade"] == "B"
    assert reference["facts_provenance"] == "confirmed_label_snapshot"
    # The physical-pack layer is silent.
    assert reference["official_records"]["records"] == []
    # The envelope itself is still present and honest about what it is.
    assert reference["official_records"]["authority"] == "FSSAI / FoSCoS"


@pytest.mark.asyncio
async def test_a_real_capture_restores_the_pack_layer(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
    registered_supabase_user, tmp_path,
):
    """Reference mode only ever removes authority; it never adds any.

    The same device, having actually photographed the pack, gets the ordinary
    Step 4 behaviour back — and still sees nothing extra in a reference view.
    """
    batch = "B-6A-OWN"
    facts = label_facts(
        product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B, panel=PANEL_B,
        fssai_licence=LICENCE, batch_number=batch,
    )
    path = make_export(
        tmp_path / f"foscos-{uuid.uuid4().hex}.xlsx",
        rows=[data_row(recall_id=90102, batch=batch, brand="Sunfield",
                       product="Sunfield Oat Porridge", status="Initiated", termination="NA")],
    )
    factory = get_sessionmaker()
    async with factory() as session:
        await official_records.ingest_recall_xlsx(
            session, path, source_checked_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        )
        await session.commit()

    await seed_off(CANDIDATE_B, categories=CEREAL_CATEGORY_OTHER_PATH)
    token, account_id = await registered_supabase_user()
    await confirm_label_through_api(app_client, device, token, account_id, CANDIDATE_B, facts)

    ordinary = await verdict(app_client, device, barcode=CANDIDATE_B)
    assert ordinary["physical_pack_context"] is True
    assert [row["recall_id"] for row in ordinary["official_records"]["records"]] == ["90102"]

    reference = await verdict(app_client, device, barcode=CANDIDATE_B, physical_pack=False)
    assert reference["official_records"]["records"] == []
    # Same science either way. Only the pack-specific layer moved.
    assert reference["grade"] == ordinary["grade"]
    assert reference["decision"] == ordinary["decision"]


@pytest.mark.asyncio
async def test_a_forged_non_label_event_authorises_no_pack_layer(
    db_clean, off_clean, app_client, published_rules, no_off_network,
    registered_supabase_user, tmp_path,
):
    """Facts that resemble a capture are not a capture.

    A device's newest event is a plain ``found_local`` scan whose ``label_facts``
    have been stuffed with an FSSAI licence and a batch number that exactly match
    a real recall — but it never went through the confirmation route, so it has no
    ``ai_run_id``. It must authorise nothing: not on the verdict route, and not on
    the lookup route, which share the one pack-context rule. A genuine confirmed
    capture afterwards restores authority.
    """
    barcode = "8901000088801"
    forged_batch = "B-FORGED"
    # Register a device and keep its id so we can plant the forged event.
    reg = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert reg.status_code == 201, reg.text
    device_id = uuid.UUID(reg.json()["device_id"])
    headers = {"X-Device-Token": reg.json()["token"]}

    await seed_off(barcode, categories=CEREAL_CATEGORY)
    path = make_export(
        tmp_path / f"foscos-{uuid.uuid4().hex}.xlsx",
        rows=[data_row(recall_id=90501, batch=forged_batch, brand="Sunfield",
                       product="Sunfield Oat Porridge", status="Initiated", termination="NA")],
    )
    factory = get_sessionmaker()
    async with factory() as session:
        await official_records.ingest_recall_xlsx(
            session, path, source_checked_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        )
        # The forged event: a plain scan wearing a capture's clothes.
        session.add(ScanEvent(
            device_id=device_id, barcode=barcode, outcome="found_local",
            client_scan_id=uuid.uuid4().hex,
            label_facts={"fssai_licence": LICENCE, "batch_number": forged_batch},
            ai_run_id=None,
        ))
        await session.commit()

    # Verdict: no pack authority, so no records and no batch community context.
    forged = await verdict(app_client, headers, barcode=barcode)
    assert forged["physical_pack_context"] is False
    assert forged["official_records"]["records"] == []
    assert [s for s in forged["community_observations"]["signals"] if s["scope"] == "batch"] == []

    # Lookup route shares the rule: the forged event authorises no record there.
    lookup = await app_client.get(f"/api/v2/scan/lookup/{barcode}", headers=headers)
    assert lookup.status_code == 200, lookup.text
    assert lookup.json()["official_records"]["records"] == []

    # A genuine confirmed capture of the same lot restores authority.
    token, account_id = await registered_supabase_user()
    facts = label_facts(
        product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B, panel=PANEL_B,
        fssai_licence=LICENCE, batch_number=forged_batch,
    )
    await confirm_label_through_api(app_client, headers, token, account_id, barcode, facts)
    restored = await verdict(app_client, headers, barcode=barcode)
    assert restored["physical_pack_context"] is True
    assert [row["recall_id"] for row in restored["official_records"]["records"]] == ["90501"]


@pytest.mark.asyncio
async def test_a_reference_view_carries_no_batch_signal(
    db_clean, off_clean, app_client, published_rules, no_off_network,
    registered_supabase_user, public_display,
):
    """A batch signal is about one lot, and a reference viewer holds none.

    Product-scoped observations are facts about the product and stay visible in
    reference mode. Batch-scoped ones do not, and the proof is a device that
    *does* have the lot: it sees the signal on its ordinary read and not on its
    reference read.
    """
    from tests.test_community_reporting import BARCODE as COMMUNITY_BARCODE
    from tests.test_community_reporting import label_facts as community_label_facts
    from tests.test_community_reporting import three_reporters

    await seed_off(COMMUNITY_BARCODE, categories=CEREAL_CATEGORY)
    shoppers = await three_reporters(
        app_client, registered_supabase_user, facts=community_label_facts(),
    )
    headers = shoppers[0][0].headers()

    ordinary = await verdict(app_client, headers, barcode=COMMUNITY_BARCODE)
    signals = ordinary["community_observations"]["signals"]
    assert signals and signals[0]["scope"] == "batch"
    assert signals[0]["batch_number"]

    reference = await verdict(
        app_client, headers, barcode=COMMUNITY_BARCODE, physical_pack=False,
    )
    assert reference["physical_pack_context"] is False
    assert [s for s in reference["community_observations"]["signals"] if s["scope"] == "batch"] == []
    # The envelope is still rendered; it simply has no lot to speak about.
    assert reference["community_observations"]["public_enabled"] is True


@pytest.mark.asyncio
async def test_reference_mode_defaults_to_the_ordinary_physical_read(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
    registered_supabase_user,
):
    """Nothing changes for a caller that does not ask for it.

    The default and the explicit ``true`` are the same read, which is the part
    that must not move. What the flag then *reports* is the authority actually
    in force: this device has never photographed this pack, so asking to be
    treated as holding it does not make it so, and the answer says so rather
    than echoing the request back. Capture the pack and the same request is
    honoured.
    """
    await seed_current()
    default = await verdict(app_client, device)
    explicit = await verdict(app_client, device, physical_pack=True)
    assert default == explicit
    # Asked for, not proven: a seeded snapshot nobody on this device captured.
    assert default["physical_pack_context"] is False

    token, account_id = await registered_supabase_user()
    await confirm_label_through_api(app_client, device, token, account_id, CURRENT, CURRENT_LABEL)
    assert (await verdict(app_client, device))["physical_pack_context"] is True


@pytest.mark.asyncio
async def test_opening_a_candidate_is_not_a_scan_of_it(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Reading about an alternative is not holding one."""
    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )

    factory = get_sessionmaker()
    async with factory() as session:
        before = int(await session.scalar(select(func.count(ScanEvent.id))) or 0)

    assert (await verdict(app_client, device))["alternative"]["candidate"]["barcode"] == CANDIDATE_B
    await verdict(app_client, device, barcode=CANDIDATE_B, physical_pack=False)

    async with factory() as session:
        after = int(await session.scalar(select(func.count(ScanEvent.id))) or 0)
        owned = (await session.execute(
            select(ScanEvent).where(ScanEvent.barcode == CANDIDATE_B)
        )).scalars().all()
    assert after == before, "viewing an alternative recorded a scan"
    # The only event for that barcode is the seeded capture, owned by nobody.
    assert all(row.device_id is None for row in owned)


@pytest.mark.asyncio
async def test_a_candidate_result_computes_its_own_alternative_and_stops(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """No chain. One request evaluates candidates for one requested barcode."""
    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
    )

    first = await verdict(app_client, device)
    assert first["alternative"]["candidate"]["barcode"] == CANDIDATE_A

    best = await verdict(app_client, device, barcode=CANDIDATE_A, physical_pack=False)
    assert best["grade"] == "A"
    assert best["alternative"]["candidate"] is None

    middle = await verdict(app_client, device, barcode=CANDIDATE_B, physical_pack=False)
    assert middle["alternative"]["candidate"]["barcode"] == CANDIDATE_A


# ---------------------------------------------------------------------------
# The layers below must not move
# ---------------------------------------------------------------------------
PROTECTED_KEYS = (
    "result_contract_version", "grade", "band", "outcome", "decision", "negatives",
    "positives", "lowers", "helps", "components", "evidence", "trace", "confidence",
    "facts_provenance", "label_version", "official_records", "community_observations",
    "nutrition", "taxonomy", "ingredients", "quantity_guidance", "purity_note", "missing",
    "product_name", "brand", "barcode", "pack_size_g", "basis", "attribution",
    "engine_version", "better_next_action", "physical_pack_context",
)


@pytest.mark.asyncio
async def test_finding_an_alternative_changes_no_part_of_the_verdict(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The scientific verdict is computed without reference to any alternative."""
    await seed_current()
    before = await verdict(app_client, device)
    assert before["alternative"]["status"] == "not_enough_information"

    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )
    after = await verdict(app_client, device)
    assert after["alternative"]["status"] == "available"

    for key in PROTECTED_KEYS:
        assert after.get(key) == before.get(key), key
    # The MRP envelope moves with the candidate, because it describes the very
    # comparison that just became possible. It is downstream of the choice and
    # cannot reach back into it — Step 6B's own tests hold that line.
    assert {key for key in after if after[key] != before.get(key)} == {"alternative", "value"}


@pytest.mark.asyncio
async def test_shopper_observations_move_no_part_of_the_selection(
    db_clean, off_clean, app_client, published_rules, no_off_network,
    registered_supabase_user, public_display,
):
    """Community is an observation layer. It does not grade or rank anything."""
    from tests.test_community_reporting import BARCODE as COMMUNITY_BARCODE
    from tests.test_community_reporting import label_facts as community_label_facts
    from tests.test_community_reporting import three_reporters

    await seed_off(COMMUNITY_BARCODE, categories=CEREAL_CATEGORY)
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
    )
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )

    shoppers = await three_reporters(
        app_client, registered_supabase_user, facts=community_label_facts(),
    )
    headers = shoppers[0][0].headers()
    before = await verdict(app_client, headers, barcode=COMMUNITY_BARCODE)
    assert before["alternative"]["candidate"]["barcode"] == CANDIDATE_A
    reporters_before = before["community_observations"]["signals"][0]["independent_reporters"]

    await three_reporters(app_client, registered_supabase_user, facts=community_label_facts())
    await three_reporters(app_client, registered_supabase_user, facts=community_label_facts())

    after = await verdict(app_client, headers, barcode=COMMUNITY_BARCODE)
    assert after["community_observations"]["signals"][0]["independent_reporters"] > reporters_before
    assert after["alternative"] == before["alternative"]
    assert after["alternative"]["candidate"]["barcode"] == CANDIDATE_A


@pytest.mark.asyncio
async def test_an_official_record_moves_no_part_of_the_selection(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
    registered_supabase_user, tmp_path,
):
    """A recall is about one physical pack, and we do not know the candidate's."""
    batch = "B-6A-1"
    facts = label_facts(
        product_name="Northstar Corn Flakes", brand="Northstar", ingredients=INGREDIENTS_C,
        panel=PANEL_C, fssai_licence=LICENCE, batch_number=batch,
    )
    token, account_id = await registered_supabase_user()
    await confirm_label_through_api(app_client, device, token, account_id, CURRENT, facts)
    await seed_off(CURRENT, name="A Different Catalogue Name")
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
    )

    before = await verdict(app_client, device)
    assert before["facts_provenance"] == "confirmed_label_snapshot"
    # The grade rests on the photographed pack, not the catalogue row.
    assert before["product_name"] == "Northstar Corn Flakes"
    assert before["alternative"]["candidate"]["barcode"] == CANDIDATE_A
    assert before["official_records"]["records"] == []

    path = make_export(
        tmp_path / f"foscos-{uuid.uuid4().hex}.xlsx",
        rows=[data_row(recall_id=90001, batch=batch, brand="Northstar",
                       product="Northstar Corn Flakes", status="Initiated", termination="NA")],
    )
    factory = get_sessionmaker()
    async with factory() as session:
        await official_records.ingest_recall_xlsx(
            session, path, source_checked_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        )
        await session.commit()

    after = await verdict(app_client, device)
    assert [row["recall_id"] for row in after["official_records"]["records"]] == ["90001"]
    assert after["alternative"] == before["alternative"]


@pytest.mark.asyncio
async def test_a_confirmed_label_with_no_catalogue_row_fails_closed(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """A pack we only know from a photograph has no source category."""
    await seed_label(CURRENT, CURRENT_LABEL)
    await seed_candidate(
        CANDIDATE_A, product_name="Rolled Oats", ingredients=INGREDIENTS_A, panel=PANEL_A,
    )

    body = await verdict(app_client, device)
    assert body["outcome"] == "graded"
    assert body["facts_provenance"] == "confirmed_label_snapshot"
    assert body["alternative"]["reason_key"] == "no_source_category_for_this_product"
    assert body["alternative"]["candidate"] is None
    # Exactly one live lookup, for the pack in hand. Discovery fetched nothing.
    assert no_off_network == [CURRENT]


# ---------------------------------------------------------------------------
# Free, anonymous, and writing nothing anywhere
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_anonymous_device_receives_the_same_alternative_as_an_account(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
    registered_supabase_user,
):
    """Product truth is free. No account, entitlement or profile is consulted."""
    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )

    anonymous = (await verdict(app_client, device))["alternative"]

    token, _account_id = await registered_supabase_user()
    claimed = await app_client.post("/api/v2/scan/device/claim", headers={**device, **auth(token)})
    assert claimed.status_code == 200, claimed.text
    signed_in = (await verdict(app_client, {**device, **auth(token)}))["alternative"]

    assert anonymous["status"] == "available"
    assert anonymous == signed_in


@pytest.mark.asyncio
async def test_computing_an_alternative_writes_nothing_to_either_store(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Runtime computation is allowed. Persistence is not."""
    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )

    async def snapshot_store_a():
        factory = get_off_sessionmaker()
        async with factory() as session:
            rows = (await session.execute(
                select(OffProduct).order_by(OffProduct.barcode)
            )).scalars().all()
            return [
                (row.barcode, row.product_name, row.brands, row.ingredients_text,
                 row.nutriments, row.categories, row.countries, row.quantity,
                 row.off_last_modified_t, row.fetched_at)
                for row in rows
            ]

    async def snapshot_store_b():
        from app.shared.database.registry import Base

        factory = get_sessionmaker()
        async with factory() as session:
            return {
                name: int(await session.scalar(select(func.count()).select_from(table)) or 0)
                for name, table in sorted(Base.metadata.tables.items())
            }

    before_a, before_b = await snapshot_store_a(), await snapshot_store_b()
    assert len(before_a) == 2
    assert any(before_b.values()), "Store B is empty, so this proves nothing"

    body = await verdict(app_client, device)
    assert body["alternative"]["status"] == "available"
    assert await snapshot_store_a() == before_a
    assert await snapshot_store_b() == before_b


@pytest.mark.asyncio
async def test_no_open_food_facts_field_reaches_store_b(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The candidate's category and country stay in Store A.

    Everything the shopper sees about the alternative is assembled in memory for
    this one response. A copy of any of it in a Store B row would be a derived
    database, and ODbL's share-alike clause would then oblige us to publish the
    whole knowledge base.
    """
    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )
    body = await verdict(app_client, device)
    assert body["alternative"]["candidate"]["product_name"] == "Sunfield Oat Porridge"

    factory = get_sessionmaker()
    async with factory() as session:
        records = (await session.execute(select(ProductRecord))).scalars().all()
        snapshots = (await session.execute(select(LabelSnapshot))).scalars().all()
    assert [row for row in records if row.barcode == CANDIDATE_B] == []
    # The candidate's snapshot is the one a person captured, and it carries no
    # catalogue field: the Store A row's name and brand are different strings.
    stored = next(row for row in snapshots if row.barcode == CANDIDATE_B)
    assert stored.facts["product_name"] == "Sunfield Oat Porridge"
    assert "Catalogue Name" not in str(stored.facts)
    assert "categories" not in stored.facts
    assert "countries" not in stored.facts

    for column in ProductRecord.__table__.columns:
        assert column.name not in {
            "categories", "category", "canonical_category", "category_key",
            "countries", "availability", "product_name", "brands",
            "ingredients_text", "nutriments", "alternative_barcode",
        }, column.name


@pytest.mark.asyncio
async def test_selecting_an_alternative_asks_no_ai_anything(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, monkeypatch,
):
    """Deterministic all the way down. Nothing here reaches the gateway."""
    from app.domains.ai_gateway import gateway

    async def forbidden(*args, **kwargs):
        raise AssertionError("Step 6A reached the AI gateway")

    monkeypatch.setattr(gateway, "run_structured", forbidden)

    await seed_current()
    await seed_candidate(
        CANDIDATE_B, product_name="Sunfield Oat Porridge", ingredients=INGREDIENTS_B,
        panel=PANEL_B,
    )

    body = await verdict(app_client, device)
    assert body["alternative"]["candidate"]["barcode"] == CANDIDATE_B

    factory = get_sessionmaker()
    async with factory() as session:
        assert (await session.execute(select(AIRun))).scalars().all() == []
