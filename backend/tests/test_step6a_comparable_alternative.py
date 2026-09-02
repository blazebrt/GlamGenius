"""Step 6A — one comparable alternative, and everything it must leave alone.

The feature makes a single public claim: *there is a product the source lists
in the same category, and under the same rules it grades higher.* Almost every
test here defends the boundary around that sentence rather than the sentence
itself — because the ways this goes wrong are all ways of quietly claiming
more: a market search we did not perform, a category we inferred, an
availability we assumed, a person we profiled, or a score we invented.

The layers below it are load-bearing and must not move. The grade, the
decision, the official record and the shopper observations are computed by
other domains and are byte-identical whether an alternative is found or not.
"""
from __future__ import annotations

import ast
import inspect
import uuid
from dataclasses import replace
from datetime import UTC, datetime

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
from app.domains.off.attribution import ATTRIBUTION_TEXT
from app.domains.off.models import OffBase, OffProduct
from app.domains.off.store import create_off_schema, get_off_engine, get_off_sessionmaker
from app.domains.official_records import service as official_records
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_official_records import LICENCE, data_row, make_export

# ---------------------------------------------------------------------------
# The cast. One current product and a shelf of candidates around it.
# ---------------------------------------------------------------------------
CURRENT = "8901000000001"          # graded C
CANDIDATE_B = "8901000000002"      # graded B, India — the one that should win
CANDIDATE_B_LATER = "8901000000003"  # graded B, India, higher barcode
CANDIDATE_A = "8901000000004"      # graded A, India
CANDIDATE_C = "8901000000005"      # graded C — same grade, never offered
CANDIDATE_D = "8901000000006"      # graded D — worse, never offered
OTHER_CATEGORY = "8901000000007"   # graded A, but a different source leaf
UK_ONLY = "8901000000008"          # graded B, not listed for India
NO_COUNTRY = "8901000000009"       # graded B, no country list at all
DRINK = "8901000000010"            # graded B, but measured per 100 ml

CEREAL_CATEGORY = "Foods, Breakfasts, Breakfast cereals"
CEREAL_CATEGORY_OTHER_PATH = "Plant foods, Breakfast cereals"
BAR_CATEGORY = "Foods, Cereal bars"

#: A pack of flakes that grades C: ultra-processed, but nothing high.
GRADE_C_FACTS = {
    "ingredients_text": "maize, sugar, salt, flavouring, emulsifier (ins 322)",
    "nutriments": {
        "energy-kcal_100g": 380, "sugars_100g": 8, "saturated-fat_100g": 1,
        "salt_100g": 0.5, "proteins_100g": 7, "fiber_100g": 3,
    },
}
#: Oats with salt: NOVA 3, nothing high — grade B.
GRADE_B_FACTS = {
    "ingredients_text": "whole grain oats, salt",
    "nutriments": {
        "energy-kcal_100g": 370, "sugars_100g": 2, "saturated-fat_100g": 1.2,
        "salt_100g": 0.3, "proteins_100g": 12, "fiber_100g": 9,
    },
}
#: Oats and nothing else — grade A.
GRADE_A_FACTS = {
    "ingredients_text": "whole grain oats",
    "nutriments": {
        "energy-kcal_100g": 380, "sugars_100g": 1, "saturated-fat_100g": 1.2,
        "salt_100g": 0.02, "proteins_100g": 13, "fiber_100g": 10,
    },
}
#: Ultra-processed and high in sugar — grade D.
GRADE_D_FACTS = {
    "ingredients_text": "wheat flour, sugar, invert sugar syrup, salt, flavouring, emulsifier (ins 322)",
    "nutriments": {
        "energy-kcal_100g": 420, "sugars_100g": 30, "saturated-fat_100g": 2,
        "salt_100g": 0.9, "proteins_100g": 6, "fiber_100g": 2,
    },
}
#: Would grade B, but its panel is per 100 ml. Never compared with a solid.
DRINK_B_FACTS = {
    "ingredients_text": "whole grain oats, salt",
    "nutriments": {
        "energy-kcal_100g": 70, "sugars_100g": 2, "saturated-fat_100g": 0.3,
        "salt_100g": 0.1, "proteins_100g": 3, "fiber_100g": 1.5,
    },
}


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


async def seed_off(
    barcode: str,
    *,
    facts: dict,
    name: str = "Northstar Flakes",
    brands: str | None = "Northstar",
    categories: str | None = CEREAL_CATEGORY,
    countries: str | None = "India",
    quantity: str | None = "200 g",
) -> None:
    """Put one Open Food Facts row in Store A, and nothing of ours anywhere."""
    factory = get_off_sessionmaker()
    async with factory() as session:
        session.add(OffProduct(
            barcode=barcode, product_name=name, brands=brands,
            ingredients_text=facts["ingredients_text"], nutriments=facts["nutriments"],
            categories=categories, countries=countries, quantity=quantity,
            # A freshly cached copy, so the current product's own lookup is
            # served from Store A. Nothing in this module needs a live call,
            # which is what lets ``no_off_network`` stay absolute.
            fetched_at=datetime.now(UTC),
        ))
        await session.commit()


async def verdict(app_client, headers, barcode: str = CURRENT) -> dict:
    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def confirm_label(app_client, device, token, account_id, barcode, facts) -> None:
    """A photographed, human-confirmed pack. Store B's own facts, not theirs."""
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
# The category parser: exact source leaf, and nothing more generous
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("categories", "expected"),
    [
        ("Foods, Breakfasts, Breakfast cereals", "breakfast cereals"),
        ("Plant foods, Breakfast cereals", "breakfast cereals"),
        ("Foods, Cereal bars", "cereal bars"),
        # Case, padding and repeated internal whitespace all normalise away.
        ("Foods,   BREAKFAST    Cereals  ", "breakfast cereals"),
        # NFKC folds the compatibility forms to the same tokens.
        ("Foods, ﬃBreakfast cereals", "ffibreakfast cereals"),
        ("Fｏods, Breakfast cereals", "breakfast cereals"),
        # Empty tokens are dropped, and the leaf is the last surviving one.
        ("Foods, Breakfast cereals, , ", "breakfast cereals"),
        # Nothing usable is nothing. It is never filled in from elsewhere.
        (None, None),
        ("", None),
        ("   ", None),
        (",,,", None),
    ],
)
def test_the_category_leaf_is_the_last_token_normalised_conservatively(categories, expected):
    assert category_module.category_leaf(categories) == expected


def test_a_non_string_category_is_absent_rather_than_coerced():
    """A malformed source row is missing data, not a category to guess at."""
    for value in (12345, ["Breakfast cereals"], {"en": "Breakfast cereals"}, True):
        assert category_module.category_leaf(value) is None


def test_two_paths_to_the_same_leaf_are_comparable_and_a_sibling_is_not():
    """Exact leaf semantics. No parent/child equivalence, no fuzzy distance."""
    assert category_module.same_source_category(CEREAL_CATEGORY, CEREAL_CATEGORY_OTHER_PATH)
    assert not category_module.same_source_category(CEREAL_CATEGORY, BAR_CATEGORY)
    # A parent is not the same use case as its child, in either direction.
    assert not category_module.same_source_category("Foods, Breakfasts", CEREAL_CATEGORY)
    assert not category_module.same_source_category(CEREAL_CATEGORY, "Foods, Breakfasts")
    # Near-misses stay misses. Nothing here measures edit distance.
    for near in ("Foods, Breakfast cereal", "Foods, Breakfast-cereals", "Foods, Cereals"):
        assert not category_module.same_source_category(CEREAL_CATEGORY, near), near
    # And a missing category never matches another missing one.
    assert not category_module.same_source_category(None, None)


def test_the_coarse_sql_filter_neutralises_wildcards_and_only_ever_prunes():
    """The pattern is a prune. It cannot admit a row the parser would reject."""
    assert category_module.coarse_category_filter("breakfast cereals") == "%breakfast%"
    # A leaf carrying LIKE metacharacters must not become a wildcard search.
    assert category_module.coarse_category_filter("100%_pure") == "%100\\%\\_pure%"
    assert category_module.coarse_category_filter("") is None


# ---------------------------------------------------------------------------
# India availability: what the source says, never what we could infer
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("countries", "eligible"),
    [
        ("India", True),
        ("India, United Kingdom", True),
        ("United Kingdom, India", True),
        ("  india  ", True),
        ("en:India", True),
        ("United Kingdom", False),
        (None, False),
        ("", False),
        # Never inferred from a look-alike. "British Indian Ocean Territory"
        # contains the letters and is a different country.
        ("British Indian Ocean Territory", False),
        ("Indiana", False),
    ],
)
def test_india_availability_is_an_exact_source_token(countries, eligible):
    assert category_module.listed_for_india(countries) is eligible


# ---------------------------------------------------------------------------
# The policy: strictly higher, no worse a decision, same basis, no score
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
        # And nothing lets them be compared against a real letter either way.
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

    # The tie-break is the barcode, and it is the only thing left to compare.
    first = policy_module.Candidate("111", "A", None, Grade.A, "buy", "solid")
    second = policy_module.Candidate("222", "B", None, Grade.A, "buy", "solid")
    assert policy_module.select([second, first]) is first
    assert policy_module.select([first, second]) is first


#: The three modules the whole feature is made of. The structural tests below
#: read their syntax tree rather than their text, so a rule can be *described*
#: in a docstring — "there is no alternative_score" — without the description
#: tripping the check that enforces it.
ALTERNATIVE_MODULES = (category_module, policy_module, alternatives_service)


def _code_identifiers(module) -> set[str]:
    """Every name and literal the module's code actually uses, docstrings aside.

    Comments and docstrings are prose about the code; they are removed so that
    explaining a prohibition cannot be mistaken for breaking it. Everything a
    composite score could hide in — a variable, an attribute, a parameter, a
    dictionary key, a wire field — is left in.
    """
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


#: Anything that would be a weighted or averaged ranking number. The
#: Constitution rejects a composite score averaging incompatible things, and
#: the point is that one is not created — not that one is hidden from the API.
FORBIDDEN_SCORE_NAMES = (
    "alternative_score", "best_choice_score", "health_value_score", "quality_score",
    "health_score", "value_score", "weighted_score", "composite_score", "weighting",
    "overall_score", "rank_score", "fit_score",
)

#: Money, in every form Step 6B will introduce and Step 6A must not.
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

    AI, shopper observations, official records, money and the person are each
    excluded for a different reason, and every exclusion is structural rather
    than a matter of remembering not to call them.
    """
    forbidden_imports = (
        "ai_gateway", "gemini", "community", "official_records",
        "price", "mrp", "retailer", "affiliate", "razorpay",
        "identity", "profile", "purchase", "beta_access", "consent", "family",
    )
    for module in ALTERNATIVE_MODULES:
        tree = ast.parse(inspect.getsource(module))
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
                modules.extend(f"{node.module or ''}.{alias.name}" for alias in node.names)
        for imported in modules:
            for banned in forbidden_imports:
                assert banned not in imported.lower(), f"{module.__name__} imports {imported}"


def test_discovery_is_bounded_by_a_named_constant():
    assert isinstance(policy_module.MAX_DISCOVERY_CANDIDATES, int)
    assert 0 < policy_module.MAX_DISCOVERY_CANDIDATES <= 200


# ---------------------------------------------------------------------------
# The scientific matrix, through the route a phone actually calls
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_higher_graded_same_category_indian_product_is_offered(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """A. The one case that produces a candidate, and everything it carries."""
    await seed_off(CURRENT, facts=GRADE_C_FACTS, name="Northstar Corn Flakes")
    await seed_off(
        CANDIDATE_B, facts=GRADE_B_FACTS, name="Sunfield Oat Porridge", brands="Sunfield",
        categories=CEREAL_CATEGORY_OTHER_PATH,
    )

    body = await verdict(app_client, device)
    envelope = body["alternative"]

    assert envelope["policy_version"] == "comparable-food-alternative-v1"
    assert envelope["status"] == "available"
    assert envelope["reason_key"] == "comparable_option_found"

    candidate = envelope["candidate"]
    assert candidate["barcode"] == CANDIDATE_B
    assert candidate["product_name"] == "Sunfield Oat Porridge"
    assert candidate["brand"] == "Sunfield"
    assert candidate["grade"] == "B"
    assert candidate["band"] == "green"
    assert candidate["decision"] == "buy"
    # The comparison states what was compared, and on what basis.
    assert candidate["comparison"] == {
        "category_match": "exact_source_leaf",
        "category_source": "open_food_facts",
        "current_grade": "C",
        "candidate_grade": "B",
        "basis": "per_100g",
    }
    # A licence condition travels with their data even inside our card.
    assert candidate["attribution"]["text"] == ATTRIBUTION_TEXT

    # The current product is unchanged by any of this.
    assert body["grade"] == "C"
    assert body["result_contract_version"] == "v1"


@pytest.mark.asyncio
async def test_the_public_candidate_exposes_no_internals(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """One product's public facts. Not the pool, the query or a ranking."""
    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    await seed_off(CANDIDATE_B, facts=GRADE_B_FACTS, name="Sunfield Oat Porridge")

    body = await verdict(app_client, device)
    assert set(body["alternative"]) == {"policy_version", "status", "reason_key", "candidate"}
    assert set(body["alternative"]["candidate"]) == {
        "barcode", "product_name", "brand", "grade", "band", "decision",
        "comparison", "attribution",
    }
    serialised = str(body["alternative"])
    # No pool size, no SQL, no raw taxonomy path, no ranking number.
    for leaked in ("ilike", "%breakfast%", "candidate_pool", "categories", CEREAL_CATEGORY):
        assert leaked not in serialised, leaked


@pytest.mark.asyncio
async def test_a_same_or_lower_graded_candidate_is_never_offered(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """B and C. Equal is not better, however clean the other label reads."""
    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    await seed_off(CANDIDATE_C, facts=GRADE_C_FACTS, name="Rival Corn Flakes")
    await seed_off(CANDIDATE_D, facts=GRADE_D_FACTS, name="Sweet Flakes")

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["status"] == "not_enough_information"
    assert envelope["reason_key"] == "no_comparable_candidate_in_cached_data"
    assert envelope["candidate"] is None


@pytest.mark.asyncio
async def test_a_not_graded_current_product_gets_no_alternative(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """D. A cooking ingredient has no letter, so there is nothing to improve on.

    This is the rule that stops the system inventing a "better ghee".
    """
    await seed_off(
        CURRENT, name="Ghee", brands="Northstar",
        facts={
            "ingredients_text": "ghee",
            "nutriments": {
                "energy-kcal_100g": 900, "saturated-fat_100g": 60,
                "sugars_100g": 0, "salt_100g": 0,
            },
        },
        categories="Foods, Ghee",
    )
    await seed_off(CANDIDATE_A, facts=GRADE_A_FACTS, categories="Foods, Ghee")

    body = await verdict(app_client, device)
    assert body["outcome"] == "not_graded"
    assert body["alternative"]["status"] == "not_enough_information"
    assert body["alternative"]["reason_key"] == "current_product_has_no_published_grade"
    assert body["alternative"]["candidate"] is None


@pytest.mark.asyncio
async def test_a_current_product_we_cannot_grade_gets_no_alternative(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """E. Not enough information about this pack is not a licence to compare it."""
    await seed_off(
        CURRENT, facts={"ingredients_text": "", "nutriments": {"energy-kcal_100g": 380}},
    )
    await seed_off(CANDIDATE_A, facts=GRADE_A_FACTS)

    body = await verdict(app_client, device)
    assert body["outcome"] == "not_enough_information"
    assert body["alternative"]["reason_key"] == "current_product_has_no_published_grade"
    assert body["alternative"]["candidate"] is None


@pytest.mark.asyncio
async def test_a_candidate_missing_ingredients_or_nutrition_is_ineligible(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """F and G. A candidate is held to exactly the bar the current product met."""
    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    await seed_off(CANDIDATE_A, facts={
        "ingredients_text": "", "nutriments": GRADE_A_FACTS["nutriments"],
    }, name="No Ingredient List Oats")
    await seed_off(CANDIDATE_B, facts={
        "ingredients_text": GRADE_A_FACTS["ingredients_text"], "nutriments": {},
    }, name="No Panel Oats")

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["status"] == "not_enough_information"
    assert envelope["candidate"] is None


@pytest.mark.asyncio
async def test_a_drink_is_never_offered_against_a_solid(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """H. Same source category, better letter, incomparable panel.

    Proven both ways: the identical nutrition read as a solid does qualify, so
    it is the basis and nothing else that rejected the drink.
    """
    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    await seed_off(DRINK, facts=DRINK_B_FACTS, name="Oat Milk Porridge Drink")

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["status"] == "not_enough_information"
    assert envelope["candidate"] is None

    await seed_off(CANDIDATE_B, facts=GRADE_B_FACTS, name="Sunfield Oat Porridge")
    after = (await verdict(app_client, device))["alternative"]
    assert after["status"] == "available"
    assert after["candidate"]["barcode"] == CANDIDATE_B
    assert after["candidate"]["comparison"]["basis"] == "per_100g"


@pytest.mark.asyncio
async def test_an_unpublished_required_rule_surfaces_no_comparison_at_all(
    db_clean, off_clean, app_client, device, no_off_network,
):
    """I. Candidate constants do not become a customer comparison.

    ``published_rules`` is deliberately absent here, so the real resolver runs
    against an empty evidence domain. Both products are then ungradeable — the
    same ruleset governs both — and the honest answer is that we do not know.
    """
    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    await seed_off(CANDIDATE_B, facts=GRADE_B_FACTS)

    body = await verdict(app_client, device)
    assert body["outcome"] == "not_enough_information"
    assert body["alternative"]["status"] == "not_enough_information"
    assert body["alternative"]["reason_key"] == "current_product_has_no_published_grade"


@pytest.mark.asyncio
async def test_both_products_are_judged_by_the_same_resolved_ruleset(
    db_clean, off_clean, app_client, device, monkeypatch, no_off_network,
):
    """The ruleset object handed to the alternative service is the same one.

    Not an equal copy — the identical object. A second resolution could differ
    from the first, and a letter from one ruleset compared against a letter
    from another is not a comparison at all.
    """
    from app.api.v2 import product as product_api

    resolved: list[ProductionRuleset] = []

    async def resolve(_session):
        ruleset = _published()
        resolved.append(ruleset)
        return ruleset

    seen: list[ProductionRuleset] = []
    real_envelope = alternatives_service.comparable_alternative_envelope

    async def capture(**kwargs):
        seen.append(kwargs["ruleset"])
        return await real_envelope(**kwargs)

    monkeypatch.setattr(product_api, "resolve_production_ruleset", resolve)
    monkeypatch.setattr(product_api.alternatives_service, "comparable_alternative_envelope", capture)

    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    await seed_off(CANDIDATE_B, facts=GRADE_B_FACTS)
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
    await seed_off(CURRENT, facts=GRADE_B_FACTS, name="Sunfield Oat Porridge")

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["candidate"] is None
    assert envelope["reason_key"] == "no_comparable_candidate_in_cached_data"


@pytest.mark.asyncio
async def test_a_different_source_leaf_is_not_a_comparable_category(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Cereal bars are not breakfast cereals, whatever they have in common."""
    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    await seed_off(OTHER_CATEGORY, facts=GRADE_A_FACTS, categories=BAR_CATEGORY)
    # A parent of the current leaf is not the same use case either.
    await seed_off(CANDIDATE_A, facts=GRADE_A_FACTS, categories="Foods, Breakfasts")

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["candidate"] is None


@pytest.mark.asyncio
async def test_a_product_the_source_does_not_list_for_india_is_ineligible(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The India gate, end to end. A missing country list is not availability."""
    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    await seed_off(UK_ONLY, facts=GRADE_A_FACTS, countries="United Kingdom")
    await seed_off(NO_COUNTRY, facts=GRADE_A_FACTS, countries=None)

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["candidate"] is None

    # The same product, listed for India as well, becomes eligible.
    await seed_off(CANDIDATE_A, facts=GRADE_A_FACTS, countries="United Kingdom, India")
    after = (await verdict(app_client, device))["alternative"]
    assert after["candidate"]["barcode"] == CANDIDATE_A


@pytest.mark.asyncio
async def test_the_public_contract_carries_exactly_one_candidate(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Several may be evaluated. One is published — never a ranked list."""
    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    for barcode in (CANDIDATE_B, CANDIDATE_B_LATER):
        await seed_off(barcode, facts=GRADE_B_FACTS, name=f"Oats {barcode}")
    await seed_off(CANDIDATE_A, facts=GRADE_A_FACTS, name="Rolled Oats")

    envelope = (await verdict(app_client, device))["alternative"]
    assert isinstance(envelope["candidate"], dict)
    # The highest valid grade wins the first lexicographic comparison.
    assert envelope["candidate"]["barcode"] == CANDIDATE_A
    assert envelope["candidate"]["grade"] == "A"
    # There is nowhere in the contract for a second one to appear.
    assert "candidates" not in envelope
    assert "alternatives" not in envelope


@pytest.mark.asyncio
async def test_selection_is_stable_whatever_order_the_rows_arrive_in(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Same inputs, same barcode, every time — and never a random tie-break.

    The rows are deleted and re-inserted in the opposite order, which changes
    the physical order a sequential scan would return them in.
    """
    tied = [CANDIDATE_B, CANDIDATE_B_LATER, "8901000000099"]
    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    for barcode in tied:
        await seed_off(barcode, facts=GRADE_B_FACTS, name=f"Oats {barcode}")

    first = (await verdict(app_client, device))["alternative"]["candidate"]["barcode"]
    assert first == min(tied), "the tie-break is not the lowest barcode"

    factory = get_off_sessionmaker()
    async with factory() as session:
        for barcode in tied:
            await session.delete(await session.get(OffProduct, barcode))
        await session.commit()
    for barcode in reversed(tied):
        await seed_off(barcode, facts=GRADE_B_FACTS, name=f"Oats {barcode}")

    again = (await verdict(app_client, device))["alternative"]["candidate"]["barcode"]
    assert again == first

    # And repeated calls in one state never drift.
    for _ in range(3):
        assert (await verdict(app_client, device))["alternative"]["candidate"]["barcode"] == first


@pytest.mark.asyncio
async def test_discovery_reads_the_cache_within_a_bounded_query(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, monkeypatch,
):
    """One bounded read of Store A, not one request or one query per candidate."""
    calls: list[object] = []
    real_discover = alternatives_service._discover

    async def counted(session, **kwargs):
        calls.append(kwargs)
        return await real_discover(session, **kwargs)

    monkeypatch.setattr(alternatives_service, "_discover", counted)

    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    for index in range(12):
        await seed_off(f"890100001{index:04d}", facts=GRADE_B_FACTS, name=f"Oats {index}")

    body = await verdict(app_client, device)
    assert body["alternative"]["status"] == "available"
    assert len(calls) == 1, "discovery ran more than once for a single Product Result"
    # And the fixture would already have failed on any live Open Food Facts call.
    assert no_off_network == []


@pytest.mark.asyncio
async def test_a_wildcard_in_a_category_cannot_widen_the_search(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """A source category is data, not a pattern.

    A leaf carrying a LIKE metacharacter must be matched literally. Unescaped,
    ``100%pure`` would become a wildcard and pull in the whole cache, and the
    exact parser would then be the only thing standing between a shopper and a
    comparison across unrelated categories.
    """
    await seed_off(CURRENT, facts=GRADE_C_FACTS, categories="Foods, 100%pure")
    # Same grade improvement, but a different category that a naive wildcard
    # search would have swept up.
    await seed_off(CANDIDATE_A, facts=GRADE_A_FACTS, categories="Foods, 100 grain pure")

    envelope = (await verdict(app_client, device))["alternative"]
    assert envelope["candidate"] is None

    # The literal category does match, so the escaping did not break the query.
    await seed_off(CANDIDATE_B, facts=GRADE_B_FACTS, categories="Plant foods, 100%pure")
    after = (await verdict(app_client, device))["alternative"]
    assert after["candidate"]["barcode"] == CANDIDATE_B


@pytest.mark.asyncio
async def test_the_candidate_window_is_capped(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, monkeypatch,
):
    """A Product Result never turns into an unbounded scan of Store A."""
    monkeypatch.setattr(alternatives_service, "MAX_DISCOVERY_CANDIDATES", 3)
    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    for index in range(10):
        await seed_off(f"890100002{index:04d}", facts=GRADE_B_FACTS, name=f"Oats {index}")

    factory = get_off_sessionmaker()
    async with factory() as session:
        rows = await alternatives_service._discover(
            session, leaf="breakfast cereals", exclude_barcode=CURRENT,
        )
    assert len(rows) == 3
    assert [row.barcode for row in rows] == sorted(row.barcode for row in rows)


# ---------------------------------------------------------------------------
# The layers below must not move
# ---------------------------------------------------------------------------
#: Every key the scientific, official and shopper layers own. The alternative
#: moves none of them.
PROTECTED_KEYS = (
    "result_contract_version", "grade", "band", "outcome", "decision", "negatives",
    "positives", "lowers", "helps", "components", "evidence", "trace", "confidence",
    "facts_provenance", "label_version", "official_records", "community_observations",
    "nutrition", "taxonomy", "ingredients", "quantity_guidance", "purity_note", "missing",
    "product_name", "brand", "barcode", "pack_size_g", "basis", "attribution",
    "engine_version", "better_next_action",
)


@pytest.mark.asyncio
async def test_finding_an_alternative_changes_no_part_of_the_verdict(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The scientific verdict is computed without reference to any alternative."""
    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    before = await verdict(app_client, device)
    assert before["alternative"]["status"] == "not_enough_information"

    await seed_off(CANDIDATE_B, facts=GRADE_B_FACTS, name="Sunfield Oat Porridge")
    after = await verdict(app_client, device)
    assert after["alternative"]["status"] == "available"

    for key in PROTECTED_KEYS:
        assert after.get(key) == before.get(key), key
    # The alternative is the only thing in the payload that moved.
    assert {key for key in after if after[key] != before.get(key)} == {"alternative"}


@pytest.mark.asyncio
async def test_shopper_observations_move_no_part_of_the_selection(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
    registered_supabase_user, public_display,
):
    """Community is an observation layer. It does not grade or rank anything.

    Reports are filed against both the current product and the chosen
    candidate, and the candidate is unchanged — it is neither promoted by
    approval nor banned by complaints.
    """
    from tests.test_community_reporting import BARCODE as COMMUNITY_BARCODE
    from tests.test_community_reporting import label_facts, three_reporters

    # The shopper's pack grades B from its confirmed label, so the A is the one
    # that can win and the B is a genuine same-grade near miss.
    await seed_off(COMMUNITY_BARCODE, facts=GRADE_C_FACTS, name="Northstar Corn Flakes")
    await seed_off(CANDIDATE_A, facts=GRADE_A_FACTS, name="Rolled Oats")
    await seed_off(CANDIDATE_B, facts=GRADE_B_FACTS, name="Sunfield Oat Porridge")

    shoppers = await three_reporters(
        app_client, registered_supabase_user, facts=label_facts(),
    )
    headers = shoppers[0][0].headers()
    before = await verdict(app_client, headers, barcode=COMMUNITY_BARCODE)
    assert before["alternative"]["candidate"]["barcode"] == CANDIDATE_A
    reporters_before = before["community_observations"]["signals"][0]["independent_reporters"]

    # Six more shoppers report the same thing about the pack in hand.
    await three_reporters(app_client, registered_supabase_user, facts=label_facts())
    await three_reporters(app_client, registered_supabase_user, facts=label_facts())

    after = await verdict(app_client, headers, barcode=COMMUNITY_BARCODE)
    # The community envelope moved, which is its own layer working as designed.
    assert after["community_observations"]["signals"][0]["independent_reporters"] > reporters_before
    # The alternative did not move at all.
    assert after["alternative"] == before["alternative"]

    # Nor does a candidate's own reputation reach the selection: the chosen
    # product is the same one, evaluated purely on the published grade.
    assert after["alternative"]["candidate"]["barcode"] == CANDIDATE_A


@pytest.mark.asyncio
async def test_an_official_record_moves_no_part_of_the_selection(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
    registered_supabase_user, tmp_path,
):
    """A recall is about one physical pack, and we do not know the candidate's.

    So an official record may not rank, reject or endorse a candidate. This also
    proves the confirmed-label path: the shopper's grade comes from the pack
    they photographed, while the category is read from Open Food Facts at
    runtime, on barcode, and is never written back.
    """
    batch = "B-6A-1"
    licence = LICENCE
    facts = {
        "product_name": "Northstar Corn Flakes", "brand": "Northstar",
        "ingredients_text": GRADE_C_FACTS["ingredients_text"],
        "nutrition_per_100g": {
            "sugars_g": "8", "saturated_fat_g": "1", "salt_g": "0.5",
            "protein_g": "7", "fibre_g": "3", "energy_kcal": "380",
        },
        "nutrition_basis": "per_100g", "net_quantity": "200 g",
        "fssai_licence": licence, "batch_number": batch,
    }
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, CURRENT, facts)
    # Store A carries the same barcode, and only ever supplies the category.
    await seed_off(CURRENT, facts=GRADE_C_FACTS, name="A Different Catalogue Name")
    await seed_off(CANDIDATE_A, facts=GRADE_A_FACTS, name="Rolled Oats")

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
    # The official envelope changed, exactly as Step 4 intends.
    assert [row["recall_id"] for row in after["official_records"]["records"]] == ["90001"]
    # The alternative did not.
    assert after["alternative"] == before["alternative"]


@pytest.mark.asyncio
async def test_a_confirmed_label_with_no_catalogue_row_fails_closed(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
    registered_supabase_user,
):
    """A pack we only know from a photograph has no source category.

    The Product Result still works exactly as it did. The category is not
    inferred from the product's name, and the alternative says so.
    """
    facts = {
        "product_name": "Regional Millet Flakes", "brand": "Local Foods",
        "ingredients_text": "millet flour, sugar, salt",
        "nutrition_per_100g": {
            "sugars_g": "8", "saturated_fat_g": "1", "salt_g": "0.5",
            "protein_g": "7", "fibre_g": "3", "energy_kcal": "380",
        },
        "nutrition_basis": "per_100g", "net_quantity": "200 g",
    }
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, CURRENT, facts)
    # A perfectly good candidate exists; there is simply no category to compare.
    await seed_off(CANDIDATE_A, facts=GRADE_A_FACTS, name="Rolled Oats")

    body = await verdict(app_client, device)
    assert body["outcome"] == "graded"
    assert body["facts_provenance"] == "confirmed_label_snapshot"
    assert body["alternative"]["status"] == "not_enough_information"
    assert body["alternative"]["reason_key"] == "no_source_category_for_this_product"
    assert body["alternative"]["candidate"] is None
    # Exactly one live lookup, for the pack in hand — the path that existed
    # before this milestone. Discovery fetched nothing.
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
    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    await seed_off(CANDIDATE_B, facts=GRADE_B_FACTS, name="Sunfield Oat Porridge")

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
    """Runtime computation is allowed. Persistence is not.

    Step 6A introduced no table, so there is nothing of its own to write; this
    proves it also leaves the two stores it reads exactly as it found them.
    """
    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    await seed_off(CANDIDATE_B, facts=GRADE_B_FACTS, name="Sunfield Oat Porridge")

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
        """A row count for every table Store B has. All of them, deliberately."""
        from app.shared.database.registry import Base

        factory = get_sessionmaker()
        async with factory() as session:
            return {
                name: int(await session.scalar(select(func.count()).select_from(table)) or 0)
                for name, table in sorted(Base.metadata.tables.items())
            }

    before_a, before_b = await snapshot_store_a(), await snapshot_store_b()
    # Guard against a vacuous comparison: both stores really do hold rows.
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
    """The candidate's name, brand, category and country stay in Store A.

    Everything the shopper sees about the alternative is assembled in memory for
    this one response. A copy of any of it in a Store B row would be a derived
    database, and ODbL's share-alike clause would then oblige us to publish the
    whole knowledge base.
    """
    from app.domains.product.models import LabelSnapshot, ProductRecord

    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    await seed_off(
        CANDIDATE_B, facts=GRADE_B_FACTS, name="Sunfield Oat Porridge", brands="Sunfield",
    )
    body = await verdict(app_client, device)
    assert body["alternative"]["candidate"]["product_name"] == "Sunfield Oat Porridge"

    factory = get_sessionmaker()
    async with factory() as session:
        records = (await session.execute(select(ProductRecord))).scalars().all()
        snapshots = (await session.execute(select(LabelSnapshot))).scalars().all()
    # No Store B row was created to hold any of it.
    assert [row for row in records if row.barcode == CANDIDATE_B] == []
    assert [row for row in snapshots if row.barcode == CANDIDATE_B] == []
    # And ProductRecord has grown no category, country or catalogue-name column.
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

    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    await seed_off(CANDIDATE_B, facts=GRADE_B_FACTS, name="Sunfield Oat Porridge")

    body = await verdict(app_client, device)
    assert body["alternative"]["candidate"]["barcode"] == CANDIDATE_B

    # And no AI run was recorded, which is the durable evidence either way.
    factory = get_sessionmaker()
    async with factory() as session:
        assert (await session.execute(select(AIRun))).scalars().all() == []


@pytest.mark.asyncio
async def test_opening_a_candidate_is_not_a_scan_of_it(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Reading about an alternative is not holding one.

    Its own Product Result works, and it records no scan event — so the
    physical-pack layers above (an exact official-record match, a batch-scoped
    shopper observation) keep failing closed on it, exactly as they should for a
    pack this phone has never seen.
    """
    from app.domains.product.models import ScanEvent

    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    await seed_off(CANDIDATE_B, facts=GRADE_B_FACTS, name="Sunfield Oat Porridge")

    assert (await verdict(app_client, device))["alternative"]["candidate"]["barcode"] == CANDIDATE_B
    candidate_result = await verdict(app_client, device, barcode=CANDIDATE_B)
    assert candidate_result["grade"] == "B"
    # No physical-pack context was manufactured for a pack nobody scanned.
    assert candidate_result["facts_provenance"] == "open_food_facts"
    assert candidate_result["label_version"] is None
    assert candidate_result["official_records"]["records"] == []

    factory = get_sessionmaker()
    async with factory() as session:
        events = (await session.execute(select(ScanEvent))).scalars().all()
    assert events == [], "viewing an alternative recorded a scan"


@pytest.mark.asyncio
async def test_a_candidate_result_computes_its_own_alternative_and_stops(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """No chain. One request evaluates candidates for one requested barcode."""
    await seed_off(CURRENT, facts=GRADE_C_FACTS)
    await seed_off(CANDIDATE_B, facts=GRADE_B_FACTS, name="Sunfield Oat Porridge")
    await seed_off(CANDIDATE_A, facts=GRADE_A_FACTS, name="Rolled Oats")

    first = await verdict(app_client, device)
    assert first["alternative"]["candidate"]["barcode"] == CANDIDATE_A

    # The A-graded product's own result has no better option, and asking for it
    # does not recurse: nothing outranks an A.
    best = await verdict(app_client, device, barcode=CANDIDATE_A)
    assert best["grade"] == "A"
    assert best["alternative"]["status"] == "not_enough_information"
    assert best["alternative"]["candidate"] is None

    # And the B-graded one points at the A, one level, no further.
    middle = await verdict(app_client, device, barcode=CANDIDATE_B)
    assert middle["alternative"]["candidate"]["barcode"] == CANDIDATE_A
