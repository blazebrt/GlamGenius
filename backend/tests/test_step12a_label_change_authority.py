"""Step 12A — the confirmed-label change authority.

One question, answered from two explicitly named observations of the same
product: *what did these two packs say differently?* The answer is a fact. It
is never a recommendation, a safety opinion, a regulatory reading or a reason
to interrupt anybody — those are later steps, and a test here that started
asserting one of them would mean this layer had grown a second job.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun, AIRunOutput
from app.domains.evidence import authoring as evidence_authoring
from app.domains.evidence.enums import EvidenceStrength, SourceType
from app.domains.product import change_projection
from app.domains.product.change_projection import (
    UNAVAILABLE_PROJECTION,
    FormulaChangeStatus,
    LabelChangeStatus,
    LabelHistoryInvariantError,
    project_label_change,
)
from app.domains.product.confidence import ProductConfidence
from app.domains.product.formula_projection import (
    boundary_significance,
    formula_entries_from_label_snapshot,
)
from app.domains.product.models import LabelSnapshot
from app.domains.product.service import (
    canonical_label_facts,
    label_changed_fields,
    label_content_fingerprint,
)
from app.domains.substances import authoring as substance_authoring
from app.domains.substances.enums import EntityKind, NameNamespace
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth

BACKEND_ROOT = Path(__file__).resolve().parents[1]

VERIFIED = evidence_authoring.VerificationInput(
    source_opened=True,
    founder_verified_fact=True,
    claude_review_completed=True,
    codex_review_completed=True,
    independent_reviews_agree=True,
    adversarial_review_passed=True,
    unresolved_doubt=False,
)

#: Nutrition values so the grading engine has something to say, which is the
#: point: every additive-envelope test needs a verdict that could visibly move.
NUTRITION = {
    "nutrition_per_100g": {"energy_kcal": "100", "sugars_g": "2", "salt_g": "0.1"},
    "nutrition_basis": "per_100g",
}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def device(app_client):
    """A phone that has just been launched for the first time."""
    response = await app_client.post(
        "/api/v2/scan/device",
        json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert response.status_code == 201, response.text
    return {"X-Device-Token": response.json()["token"]}


async def _seed_label_run(facts: dict, account_id: uuid.UUID) -> uuid.UUID:
    async with get_sessionmaker()() as session:
        run = AIRun(
            account_id=account_id, feature="product_label_transcribe", provider="test",
            model="test-model", prompt_version="scan-label.v1",
            schema_version="scan-label.v1", status=AI_STATUS_SUCCEEDED,
            validation_passed=True,
        )
        session.add(run)
        await session.flush()
        session.add(
            AIRunOutput(ai_run_id=run.id, schema_version="scan-label.v1", payload=facts)
        )
        await session.commit()
        return run.id


async def _confirm(client, device_headers, token, account_id, barcode, facts):
    """Store one confirmed physical-pack observation through the real route."""
    run_id = await _seed_label_run(facts, account_id)
    response = await client.post(
        "/api/v2/scan/label/confirm",
        headers={**device_headers, **auth(token)},
        json={
            "barcode": barcode,
            "ai_run_id": str(run_id),
            "client_scan_id": uuid.uuid4().hex,
        },
    )
    assert response.status_code == 201, response.text
    return response


def _snapshot(
    facts: dict,
    *,
    barcode: str = "8900000000012",
    version: int = 1,
    previous: LabelSnapshot | None = None,
) -> LabelSnapshot:
    """An in-memory snapshot in exactly the shape the write path produces.

    Fingerprint and ``changed_fields`` come from the real authority rather than
    from a literal, so a test cannot accidentally assert against a history the
    application would never have written.
    """
    return LabelSnapshot(
        id=uuid.uuid4(),
        barcode=barcode,
        scan_event_id=uuid.uuid4(),
        facts=facts,
        confidence=ProductConfidence.UNVERIFIED.value,
        content_fingerprint=label_content_fingerprint(facts),
        version_number=version,
        previous_snapshot_id=previous.id if previous else None,
        changed_fields=label_changed_fields(previous.facts, facts) if previous else [],
        completeness="complete_for_grading",
    )


def _pair(previous_text: object, current_text: object, **extra):
    """Two adjacent versions differing in the supplied ingredient text."""
    old = _snapshot({"product_name": "Observed", "ingredients_text": previous_text})
    new = _snapshot(
        {"product_name": "Observed", "ingredients_text": current_text, **extra},
        version=2,
        previous=old,
    )
    return old, new


def _uuids_in(value: object) -> list[str]:
    """Every UUID-shaped string anywhere inside a response fragment."""
    import re

    pattern = re.compile(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )
    return pattern.findall(repr(value))


async def _versions(barcode: str) -> list[LabelSnapshot]:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(LabelSnapshot)
            .where(LabelSnapshot.barcode == barcode)
            .order_by(LabelSnapshot.version_number)
        )).scalars().all()
        return list(rows)


# ---------------------------------------------------------------------------
# 1–4. Version identity: what counts as a different label
# ---------------------------------------------------------------------------
async def test_1_a_first_observation_is_not_a_change():
    """Never seen before is not reformulated, added to, or anything else."""
    current = _snapshot({"ingredients_text": "Water,Glycerin"})
    result = project_label_change(current=current, previous=None)

    assert result.status is LabelChangeStatus.FIRST_OBSERVED_VERSION
    assert result.previous_version is None
    assert result.current_version == 1
    assert result.changed_fields == ()
    assert result.formula.status is FormulaChangeStatus.NOT_APPLICABLE
    assert result.formula.only_on_current_label == () and result.formula.only_on_previous_label == ()


async def test_2_case_only_difference_is_a_label_change_but_not_a_formula_change():
    old, new = _pair("Water,Glycerin", "water,glycerin")
    result = project_label_change(current=new, previous=old)

    assert result.changed_fields == ("ingredients",)
    assert result.formula.status is FormulaChangeStatus.UNCHANGED
    assert result.formula.only_on_current_label == () and result.formula.only_on_previous_label == ()


def test_3_harmless_spacing_is_not_a_different_label():
    """Two photographs of one pack must not become two versions of it.

    Spacing is how a pack was printed and how a camera read it. Treating a
    doubled space as a new version would fill a product's history with changes
    nobody made.
    """
    spaced = {"product_name": "Observed", "ingredients_text": "Water,  Glycerin "}
    plain = {"product_name": "Observed", "ingredients_text": " Water, Glycerin"}

    assert label_content_fingerprint(spaced) == label_content_fingerprint(plain)
    assert label_changed_fields(spaced, plain) == []


def test_4_a_line_break_the_parser_cannot_place_is_a_different_label():
    """The defect this step exists to close.

    Step 7B refuses to guess where an entry ends when a top-level line break
    appears, and reports nothing rather than a list it invented. So
    ``"Water\\nGlycerin"`` and ``"Water Glycerin"`` are two labels the product
    reads completely differently — one yields no ingredients at all, the other
    yields one. A version authority that folded them together would store the
    second pack as "no change" and leave the first reading attached to it.
    """
    broken = {"product_name": "Observed", "ingredients_text": "Water\nGlycerin"}
    spaced = {"product_name": "Observed", "ingredients_text": "Water Glycerin"}

    assert label_content_fingerprint(broken) != label_content_fingerprint(spaced)
    assert label_changed_fields(broken, spaced) == ["ingredients"]

    broken_entries = formula_entries_from_label_snapshot(_snapshot(broken))
    spaced_entries = formula_entries_from_label_snapshot(_snapshot(spaced))
    assert broken_entries.status.value == "ambiguous_boundary"
    assert broken_entries.entries == ()
    assert spaced_entries.status.value == "parsed"
    assert [row.raw_name for row in spaced_entries.entries] == ["Water Glycerin"]


@pytest.mark.parametrize(
    ("left", "right", "same"),
    [
        # Which boundary character was printed is presentation; that one was
        # printed is not.
        ("Water\r\nGlycerin", "Water\nGlycerin", True),
        ("Water Glycerin", "Water\nGlycerin", True),
        ("Water \n Glycerin", "Water\nGlycerin", True),
        # A break at either end is exactly as unplaceable as one in the middle.
        ("\nWater,Glycerin", "Water,Glycerin", False),
        ("Water,Glycerin\n", "Water,Glycerin", False),
        # Plain spaces at either end are not.
        ("  Water,Glycerin  ", "Water,Glycerin", True),
    ],
)
def test_4b_boundary_presentation_folds_but_boundary_presence_does_not(left, right, same):
    left_facts = {"ingredients_text": left}
    right_facts = {"ingredients_text": right}
    equal = label_content_fingerprint(left_facts) == label_content_fingerprint(right_facts)
    assert equal is same
    # Whatever the version authority decides, the parser must agree: two labels
    # it reads differently are never one version.
    parsed_alike = (
        formula_entries_from_label_snapshot(_snapshot(left_facts)).status
        is formula_entries_from_label_snapshot(_snapshot(right_facts)).status
    )
    if equal:
        assert parsed_alike


def _assert_same_version(left: str, right: str) -> None:
    assert label_content_fingerprint({"ingredients_text": left}) == (
        label_content_fingerprint({"ingredients_text": right})
    )
    assert label_changed_fields({"ingredients_text": left}, {"ingredients_text": right}) == []


def _assert_different_versions(left: str, right: str) -> None:
    assert label_content_fingerprint({"ingredients_text": left}) != (
        label_content_fingerprint({"ingredients_text": right})
    )
    assert label_changed_fields(
        {"ingredients_text": left}, {"ingredients_text": right}
    ) == ["ingredients"]


@pytest.mark.parametrize(
    ("wrapped", "spaced"),
    [
        # Grouping wins. Step 7B keeps a break inside balanced grouping within
        # one entry, and Step 7A collapses it when producing that entry's
        # canonical key, so both packs printed the same formula.
        ("Parfum (A\nB), Water", "Parfum (A B), Water"),
        # Nested grouping is still grouping.
        (
            "Aqua, Parfum (Linalool (A\nB), Citral), Water",
            "Aqua, Parfum (Linalool (A B), Citral), Water",
        ),
        # Square and curly brackets are grouping pairs too.
        ("Parfum [A\nB], Water", "Parfum [A B], Water"),
        ("Parfum {A\nB}, Water", "Parfum {A B}, Water"),
        # Compatibility forms. Step 7B reads structure through an NFKC view, so
        # fullwidth brackets group exactly as ASCII ones do — the case a
        # grouping rule written locally in the product domain would miss.
        ("Parfum（A\nB）, Water", "Parfum（A B）, Water"),
        ("Parfum［A\nB］, Water", "Parfum［A B］, Water"),
        # A break at either end of a protected region, not only in the middle.
        ("Parfum (\nA B), Water", "Parfum ( A B), Water"),
        ("Parfum (A B\n), Water", "Parfum (A B ), Water"),
    ],
)
def test_4c_a_break_protected_by_grouping_is_printing_not_a_new_version(wrapped, spaced):
    """A line wrap inside a bracket must not manufacture a label version.

    The version identity is what decision memory and shelf links are pinned
    to. Splitting it on a difference the formula engine cannot see would
    detach a remembered decision from the pack it was made about, over a wrap
    the printer chose.
    """
    _assert_same_version(wrapped, spaced)

    # Same reading, same entries, same canonical identities — which is why
    # they are one version and not two.
    outcomes = {
        tuple(
            (row.position, row.normalized_name)
            for row in formula_entries_from_label_snapshot(
                _snapshot({"ingredients_text": text})
            ).entries
        )
        for text in (wrapped, spaced)
    }
    assert len(outcomes) == 1


def test_4d_the_distinction_is_exactly_as_wide_as_the_grammar():
    """One rule, both directions, in one place.

    A top-level break changes what the parser concludes and is therefore a
    different label. The same characters inside balanced grouping do not, and
    are therefore the same label. Anything wider manufactures versions;
    anything narrower loses the reading attached to a pack.
    """
    _assert_different_versions("Water\nGlycerin", "Water Glycerin")
    _assert_same_version("Parfum (A\nB), Water", "Parfum (A B), Water")

    # And the parser agrees, which is the whole justification.
    assert formula_entries_from_label_snapshot(
        _snapshot({"ingredients_text": "Water\nGlycerin"})
    ).status.value == "ambiguous_boundary"
    assert formula_entries_from_label_snapshot(
        _snapshot({"ingredients_text": "Water Glycerin"})
    ).status.value == "parsed"
    for grouped in ("Parfum (A\nB), Water", "Parfum (A B), Water"):
        entries = formula_entries_from_label_snapshot(
            _snapshot({"ingredients_text": grouped})
        )
        assert entries.status.value == "parsed"
        assert [row.normalized_name for row in entries.entries] == ["parfum (a b)", "water"]


@pytest.mark.parametrize(
    ("wrapped", "spaced"),
    [
        # A closer with nothing open: refused as the walk reaches it.
        pytest.param("Water)G\nH", "Water)G H", id="stray-closer"),
        # An opener never closed: refused only once the whole text is read.
        pytest.param("Parfum (A\nB, Water", "Parfum (A B, Water", id="unclosed-opener"),
        pytest.param("Aqua, Parfum ([A\nB), Water", "Aqua, Parfum ([A B), Water", id="crossed-pairs"),
        # Compatibility forms whose normalisation would relocate structure.
        # Step 7B withholds the formula rather than guess at the offsets, so
        # it has judged no position and nothing here may be folded away.
        pytest.param("Water\u2474A\nB", "Water\u2474A B", id="circled-paren-one"),
        pytest.param("Water\u2033A\nB", "Water\u2033A B", id="double-prime"),
        pytest.param("Water\u2116A\nB", "Water\u2116A B", id="numero-sign"),
    ],
)
def test_4e_when_the_grammar_will_not_speak_every_boundary_is_kept(wrapped, spaced):
    """No answer is not the same as "no boundary here".

    Grouping that never balances, and a compatibility form whose offsets cannot
    be reconstructed, both leave the parser with no position it has judged.
    Folding the break away on a guess would be the one error that cannot be
    undone: a lost version, not a spare one.
    """
    assert boundary_significance(wrapped) is None
    assert boundary_significance(spaced) is None
    _assert_different_versions(wrapped, spaced)


def test_4f_canonicalising_never_changes_what_the_parser_concludes():
    """The canonical form is a faithful stand-in for the printed one.

    This is the property the whole rule rests on: folding presentation may not
    add, remove or re-read a single entry. Checked over the shapes that make
    boundaries interesting rather than over one example.
    """
    for text in (
        "Water,Glycerin", "  Water ,  Glycerin  ", "Water\nGlycerin",
        "\nWater,Glycerin", "Water,Glycerin\n", "Parfum (A\nB), Water",
        "Parfum（A\nB）, Water", "A(B(C\nD)E), F", "Water)G\nH",
        "N,N-Dimethylacetamide, Water", "CI 77491,CI 77492",
        "Aqua\t(Water\r\nDeionised) , Glycerin",
    ):
        canonical = canonical_label_facts({"ingredients_text": text})["ingredients_text"]
        before = formula_entries_from_label_snapshot(_snapshot({"ingredients_text": text}))
        after = formula_entries_from_label_snapshot(
            _snapshot({"ingredients_text": canonical})
        )
        assert before.status is after.status, text
        assert [row.normalized_name for row in before.entries] == [
            row.normalized_name for row in after.entries
        ], text


def test_4g_the_product_domain_holds_no_grammar_of_its_own():
    """Structure is asked for, never re-derived on this side of the door.

    The grouping and Unicode rules live in one place. A second copy here would
    answer the fullwidth-bracket case differently on the day somebody forgot
    it, and the label version identity would quietly move underneath every
    remembered decision.
    """
    source = (BACKEND_ROOT / "app" / "domains" / "product" / "service.py").read_text(
        encoding="utf-8"
    )
    for grammar in (
        "_GROUPING_PAIRS", "_CLOSERS", "structural_view", "unicodedata", "NFKC",
    ):
        assert grammar not in source, grammar
    assert "boundary_significance" in source  # it asks, through the one door

    door = (
        BACKEND_ROOT / "app" / "domains" / "product" / "formula_projection.py"
    ).read_text(encoding="utf-8")
    assert "boundary_significance" in door


# ---------------------------------------------------------------------------
# 5–13. The formula change classification
# ---------------------------------------------------------------------------
async def test_5_reordering_is_reported_as_reordering_and_nothing_more():
    """Printed order is printed order. It is not concentration.

    Reading a moved ingredient as "there is more of it now" would be an
    unsourced regulatory inference, so the status says only what happened: the
    same entries were printed in a different order.
    """
    old, new = _pair("Water,Glycerin,Niacinamide", "Niacinamide,Water,Glycerin")
    result = project_label_change(current=new, previous=old)

    assert result.formula.status is FormulaChangeStatus.REORDERED_ONLY
    assert result.formula.only_on_current_label == () and result.formula.only_on_previous_label == ()


async def test_6_a_single_addition_is_reported_once():
    old, new = _pair("Water,Glycerin", "Water,Glycerin,Niacinamide")
    result = project_label_change(current=new, previous=old)

    assert result.formula.status is FormulaChangeStatus.INGREDIENT_SET_CHANGED
    assert [row.as_payload() for row in result.formula.only_on_current_label] == [
        {"name": "Niacinamide", "occurrences": 1},
    ]
    assert result.formula.only_on_previous_label == ()


async def test_7_a_single_removal_is_reported_once():
    old, new = _pair("Water,Glycerin,Niacinamide", "Water,Glycerin")
    result = project_label_change(current=new, previous=old)

    assert result.formula.status is FormulaChangeStatus.INGREDIENT_SET_CHANGED
    assert result.formula.only_on_current_label == ()
    assert [row.as_payload() for row in result.formula.only_on_previous_label] == [
        {"name": "Niacinamide", "occurrences": 1},
    ]


async def test_8_duplicates_are_counted_not_flattened():
    """A pack that printed a name twice printed it twice.

    Comparing sets would report this pair as one addition and no removal, which
    is not what the two labels say.
    """
    old, new = _pair(
        "Water,Glycerin,Glycerin", "Water,Glycerin,Niacinamide,Niacinamide",
    )
    result = project_label_change(current=new, previous=old)

    assert result.formula.status is FormulaChangeStatus.INGREDIENT_SET_CHANGED
    assert [row.as_payload() for row in result.formula.only_on_current_label] == [
        {"name": "Niacinamide", "occurrences": 2},
    ]
    assert [row.as_payload() for row in result.formula.only_on_previous_label] == [
        {"name": "Glycerin", "occurrences": 1},
    ]


async def test_9_an_unreadable_list_is_never_partially_compared():
    """No half answer. A client can say the label changed and stop there."""
    old, new = _pair("Water,Glycerin", "Water,,Glycerin")
    result = project_label_change(current=new, previous=old)

    assert result.formula.status is FormulaChangeStatus.NOT_COMPARABLE
    assert result.formula.only_on_current_label == () and result.formula.only_on_previous_label == ()
    assert result.formula.previous_parse_status.value == "parsed"
    assert result.formula.current_parse_status.value == "malformed"


async def test_10_an_ambiguous_boundary_is_reported_as_itself_not_as_malformed():
    """Two different reasons a list could not be read, told apart.

    "We could not tell where one ingredient ended" is a different fact from
    "this is not a list", and a client that wants to explain the silence needs
    the real one.
    """
    old, new = _pair("Water,Glycerin", "Water\nGlycerin")
    result = project_label_change(current=new, previous=old)

    assert result.formula.status is FormulaChangeStatus.NOT_COMPARABLE
    assert result.formula.current_parse_status.value == "ambiguous_boundary"
    assert result.formula.previous_parse_status.value == "parsed"

    broken_old, broken_new = _pair("Water,Glycerin", "Water,,Glycerin")
    malformed = project_label_change(current=broken_new, previous=broken_old)
    assert malformed.formula.current_parse_status.value == "malformed"
    assert (
        malformed.formula.current_parse_status
        is not result.formula.current_parse_status
    )


async def test_11_an_absent_ingredient_observation_is_not_comparable():
    """A pack photographed without its ingredient list states nothing about it."""
    old = _snapshot({"product_name": "Observed", "ingredients_text": "Water,Glycerin"})
    new = _snapshot({"product_name": "Observed"}, version=2, previous=old)
    result = project_label_change(current=new, previous=old)

    assert result.changed_fields == ("ingredients",)
    assert result.formula.status is FormulaChangeStatus.NOT_COMPARABLE
    assert result.formula.current_parse_status.value == "empty"
    assert result.formula.only_on_current_label == () and result.formula.only_on_previous_label == ()


async def test_11b_an_entry_with_no_usable_key_makes_the_pair_not_comparable():
    """Both lists parsed, and still no difference can be stated.

    Step 7A refuses to key a name beyond its length ceiling, and an entry that
    cannot be keyed cannot be said to be present on one label and absent from
    the other. The readable entries around it are not reported as a difference
    either: half a comparison is a difference somebody did not observe.
    """
    from app.domains.substances.normalization import MAX_NAME_LENGTH

    unkeyable = "W" * (MAX_NAME_LENGTH + 5)
    old, new = _pair("Water,Glycerin", f"Water,{unkeyable}")

    entries = formula_entries_from_label_snapshot(new)
    assert entries.status.value == "parsed"  # the parser was perfectly happy
    assert [row.normalized_name for row in entries.entries] == ["water", None]

    result = project_label_change(current=new, previous=old)
    assert result.formula.status is FormulaChangeStatus.NOT_COMPARABLE
    assert result.formula.previous_parse_status.value == "parsed"
    assert result.formula.current_parse_status.value == "parsed"
    assert result.formula.only_on_current_label == ()
    assert result.formula.only_on_previous_label == ()


async def test_12_a_pack_change_that_left_the_ingredients_alone_is_not_a_formula_change():
    old = _snapshot({"ingredients_text": "Water", "net_quantity": "100 g"})
    new = _snapshot(
        {"ingredients_text": "Water", "net_quantity": "120 g"}, version=2, previous=old,
    )
    result = project_label_change(current=new, previous=old)

    assert result.changed_fields == ("net_quantity",)
    assert result.formula.status is FormulaChangeStatus.UNCHANGED
    assert result.formula.only_on_current_label == () and result.formula.only_on_previous_label == ()


async def test_13_an_unreadable_list_that_did_not_change_is_unchanged():
    """Both packs printed the same list we cannot read. That is not a change.

    Answering ``not_comparable`` here would let a client tell somebody the
    ingredients might have moved on a pack where they demonstrably did not.
    """
    old = _snapshot({"ingredients_text": "Water\nGlycerin", "net_quantity": "100 g"})
    new = _snapshot(
        {"ingredients_text": "Water\nGlycerin", "net_quantity": "120 g"},
        version=2,
        previous=old,
    )
    result = project_label_change(current=new, previous=old)

    assert result.changed_fields == ("net_quantity",)
    assert result.formula.status is FormulaChangeStatus.UNCHANGED
    assert result.formula.previous_parse_status.value == "ambiguous_boundary"
    assert result.formula.current_parse_status.value == "ambiguous_boundary"


# ---------------------------------------------------------------------------
# 14–17. Purity, determinism, and independence from the identity registry
# ---------------------------------------------------------------------------
def test_14_the_projection_takes_two_snapshots_and_no_way_to_reach_anything():
    """Structural: selection, I/O and the clock are all outside this function."""
    signature = inspect.signature(project_label_change)
    assert tuple(signature.parameters) == ("current", "previous")
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    assert not inspect.iscoroutinefunction(project_label_change)

    # Structural rather than by keyword: nothing in the module awaits, and it
    # imports nothing that could reach a database, a clock, a random source or
    # a model. A comparison that could do any of those would stop being
    # reproducible for the exact pair a caller named.
    import ast

    tree = ast.parse(
        (BACKEND_ROOT / "app" / "domains" / "product" / "change_projection.py")
        .read_text(encoding="utf-8")
    )
    for node in ast.walk(tree):
        assert not isinstance(
            node, ast.Await | ast.AsyncFunctionDef | ast.AsyncWith | ast.AsyncFor
        ), ast.dump(node)[:80]

    imported = {
        alias.name for node in ast.walk(tree)
        if isinstance(node, ast.Import) for alias in node.names
    } | {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported == {
        "__future__",
        "collections",
        "collections.abc",
        "dataclasses",
        "enum",
        "typing",
        "app.domains.product.formula_projection",
        "app.domains.product.models",
        "app.domains.product.service",
        "app.shared.errors.codes",
        "app.shared.errors.exceptions",
    }, imported


async def test_15_the_projection_never_selects_a_version_for_itself(monkeypatch):
    from app.domains.product import service as product_service

    async def _must_not_run(*args, **kwargs):
        pytest.fail("the projection selected a snapshot instead of being given one")

    monkeypatch.setattr(product_service, "latest_label_snapshot", _must_not_run)
    old, new = _pair("Water,Glycerin", "Water,Niacinamide")
    assert project_label_change(current=new, previous=old).current_version == 2


async def test_16_the_same_pair_always_produces_the_same_answer():
    old, new = _pair("Water,Glycerin,Glycerin", "Glycerin,Niacinamide,Water")
    answers = {
        repr(project_label_change(current=new, previous=old).as_payload())
        for _ in range(5)
    }
    assert len(answers) == 1


async def test_17_publishing_a_canonical_identity_later_does_not_move_the_delta(
    db_clean,
):
    """Two stored observations compare the same way before and after review.

    The identity registry is a table a reviewer keeps adding to. If the
    comparison consulted it, the day somebody published a synonym for an
    ingredient would look like the day a manufacturer changed the pack — a
    claim about a company that nobody made and no label supports.
    """
    old, new = _pair("Water,Glycerin", "Water,Niacinamide")
    before = project_label_change(current=new, previous=old).as_payload()

    async with get_sessionmaker()() as session:
        draft = await substance_authoring.create_identity_draft(
            session,
            substance_key="niacinamide",
            entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
            names=[{
                "name": "Niacinamide",
                "namespace": NameNamespace.INCI.value,
                "language_tag": "und",
                "is_preferred": True,
            }],
            summary="Names recorded for niacinamide.",
            scope="Nomenclature only.",
            evidence_strength=EvidenceStrength.STRONG.value,
            strength_rationale="A named reference work records this nomenclature directly.",
            source_title="Reference entry",
            source_publisher="Example Reference",
            source_type=SourceType.INGREDIENT_REFERENCE_DATABASE.value,
            source_url="https://example.org/reference/niacinamide",
            license_or_use_note="Reproduced under the publisher's stated terms of use.",
            author="tester",
        )
        claim = uuid.UUID(draft["claim_id"])
        await evidence_authoring.approve(session, claim, reviewer="reviewer")
        await evidence_authoring.record_publication_verification(
            session, claim, verification=VERIFIED, actor="founder",
        )
        await evidence_authoring.publish(session, claim, publisher="founder")
        await session.commit()

    after = project_label_change(current=new, previous=old).as_payload()
    assert after == before
    assert after["formula"]["only_on_current_label"] == [
        {"name": "Niacinamide", "occurrences": 1},
    ]


# ---------------------------------------------------------------------------
# 18–19. Corrupt history fails closed, and says nothing it should not
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("wrong_barcode", "label_predecessor_barcode_mismatch"),
        ("wrong_previous_id", "label_predecessor_identity_mismatch"),
        ("missing_previous", "label_predecessor_missing"),
        ("noncontiguous", "label_version_chain_non_contiguous"),
        ("same_content", "adjacent_label_versions_have_same_content"),
        ("changed_fields_drift", "label_changed_fields_mismatch"),
        ("facts_drift", "current_label_fingerprint_mismatch"),
        ("previous_facts_drift", "previous_label_fingerprint_mismatch"),
        ("zero_version", "current_label_version_invalid"),
        ("first_with_predecessor", "first_label_version_has_predecessor"),
        ("first_with_changed_fields", "first_label_version_has_changed_fields"),
    ],
)
async def test_18_corrupt_history_fails_closed(mutation, reason):
    """Not one of these is repaired into a plausible answer.

    Every entry is a way two stored rows could disagree about what they are. A
    projection that picked whichever reading still parsed would publish a
    change fact nobody observed and hide the corruption for good.
    """
    old, new = _pair("Water", "Water,Glycerin")
    previous: LabelSnapshot | None = old

    if mutation == "wrong_barcode":
        old.barcode = "8900000000099"
    elif mutation == "wrong_previous_id":
        new.previous_snapshot_id = uuid.uuid4()
    elif mutation == "missing_previous":
        previous = None
    elif mutation == "noncontiguous":
        old.version_number = 4
        new.version_number = 9
    elif mutation == "same_content":
        old.facts = dict(new.facts)
        old.content_fingerprint = new.content_fingerprint
        new.changed_fields = []
    elif mutation == "changed_fields_drift":
        new.changed_fields = ["net_quantity"]
    elif mutation == "facts_drift":
        new.facts = {**new.facts, "ingredients_text": "Something else entirely"}
    elif mutation == "previous_facts_drift":
        old.facts = {**old.facts, "ingredients_text": "Something else entirely"}
    elif mutation == "zero_version":
        new.version_number = 0
    elif mutation == "first_with_predecessor":
        new.version_number = 1
    elif mutation == "first_with_changed_fields":
        new = _snapshot({"ingredients_text": "Water"})
        new.changed_fields = ["ingredients"]
        previous = None

    with pytest.raises(LabelHistoryInvariantError) as raised:
        project_label_change(current=new, previous=previous)
    assert raised.value.reason == reason


async def test_19_a_governed_failure_says_nothing_internal():
    """The customer sentence is fixed; the diagnosis is for the log only."""
    error = LabelHistoryInvariantError("label_predecessor_identity_mismatch")
    detail = error.to_detail()

    assert error.status_code == 503
    assert detail == {
        "code": "FEATURE_UNAVAILABLE",
        "message": "This product history is not available right now.",
        "retryable": False,
    }
    assert "label_predecessor" not in repr(detail)
    assert error.reason == "label_predecessor_identity_mismatch"


# ---------------------------------------------------------------------------
# 20–24. Product Result: additive, product-scoped, and never invented from OFF
# ---------------------------------------------------------------------------
async def test_20_the_product_result_carries_the_immediate_version_change(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    barcode = "8900000000128"
    token, account_id = await registered_supabase_user()
    first = {"product_name": "Observed serum", "ingredients_text": "Water,Glycerin", **NUTRITION}
    await _confirm(app_client, device, token, account_id, barcode, first)

    opening = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)
    assert opening.status_code == 200, opening.text
    assert opening.json()["label_change"] == {
        "scope": "confirmed_label_history",
        "status": "first_observed_version",
        "current_version": 1,
        "previous_version": None,
        "changed_fields": [],
        "formula": {
            "status": "not_applicable",
            "previous_parse_status": None,
            "current_parse_status": None,
            "only_on_current_label": [],
            "only_on_previous_label": [],
        },
    }

    await _confirm(
        app_client, device, token, account_id, barcode,
        {**first, "ingredients_text": "Water,Niacinamide"},
    )
    second = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)
    assert second.status_code == 200, second.text
    change = second.json()["label_change"]
    assert change["status"] == "changed"
    assert (change["previous_version"], change["current_version"]) == (1, 2)
    assert change["changed_fields"] == ["ingredients"]
    assert change["formula"]["status"] == "ingredient_set_changed"
    assert change["formula"]["only_on_current_label"] == [{"name": "Niacinamide", "occurrences": 1}]
    assert change["formula"]["only_on_previous_label"] == [{"name": "Glycerin", "occurrences": 1}]
    # Nothing in the envelope identifies a row, a device or an account. The
    # label *version* block already carries the snapshot id under its own
    # contract; this envelope adds no second copy of it.
    assert not _uuids_in(change)


#: Every key ``GET /api/v2/scan/verdict/{barcode}`` returned before Step 12A,
#: plus the one this step adds. Written out rather than derived, because the
#: whole claim is that exactly one key appeared and the answer is that this is
#: the list somebody has to change on purpose.
VERDICT_KEYS = {
    "alternative", "attribution", "band", "barcode", "basis", "better_next_action",
    "brand", "community_observations", "components", "confidence", "decision",
    "engine_version", "evidence", "facts_provenance", "grade", "helps",
    "ingredients", "label_version", "lowers", "missing", "negatives",
    "nutrition", "official_records", "outcome", "pack_size_g",
    "physical_pack_context", "positives", "product_name", "purity_note",
    "quantity_guidance", "result_contract_version", "taxonomy", "trace", "value",
} | {"label_change"}


async def test_21_the_envelope_changes_nothing_else_on_the_page(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """Step 12A is additive or it is wrong.

    Not a list of fields spot-checked by hand: the whole response is compared,
    and the set of keys that moved is named. A future change that let the
    change envelope disturb the grade, the band, the evidence, an alternative
    or the value comparison fails here by appearing in that set, whether or not
    anybody remembered to add an assertion for it.
    """
    barcode = "8900000000159"
    token, account_id = await registered_supabase_user()
    base = {"product_name": "Observed food", "ingredients_text": "Water,Glycerin", **NUTRITION}
    await _confirm(app_client, device, token, account_id, barcode, base)
    before = (await app_client.get(
        f"/api/v2/scan/verdict/{barcode}", headers=device,
    )).json()

    await _confirm(
        app_client, device, token, account_id, barcode,
        {**base, "ingredients_text": "Water,Niacinamide"},
    )
    after = (await app_client.get(
        f"/api/v2/scan/verdict/{barcode}", headers=device,
    )).json()

    # Exactly one key is Step 12A's, and it did not displace anything.
    assert set(before) == set(after) == VERDICT_KEYS

    # The pack really did print a different ingredient list, so the ingredient
    # line and the version identity are expected to move. Nothing else may.
    assert {key for key in before if before[key] != after[key]} == {
        "ingredients", "label_version", "label_change",
    }

    assert before["label_change"]["status"] == "first_observed_version"
    assert after["label_change"]["status"] == "changed"


async def test_22_history_stays_product_scoped_in_reference_mode(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """A pack nobody proved is in this caller's hand still has a product history.

    The envelope says whose history it is — the product's confirmed labels —
    so no surface can render it as "your packet changed".
    """
    barcode = "8900000000166"
    token, account_id = await registered_supabase_user()
    first = {"product_name": "Observed product", "ingredients_text": "Water", **NUTRITION}
    await _confirm(app_client, device, token, account_id, barcode, first)
    await _confirm(
        app_client, device, token, account_id, barcode,
        {**first, "ingredients_text": "Water,Glycerin"},
    )

    response = await app_client.get(
        f"/api/v2/scan/verdict/{barcode}?physical_pack_context=false", headers=device,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["physical_pack_context"] is False
    assert payload["label_change"]["scope"] == "confirmed_label_history"
    assert payload["label_change"]["status"] == "changed"


async def test_23_an_open_food_facts_refresh_never_becomes_a_pack_change(
    db_clean, off_clean, app_client, device, monkeypatch,
):
    """Store A moving is not somebody photographing a pack.

    Open Food Facts editing its own copy of a product is a catalogue edit. It
    is not an observation of a physical pack, so it cannot produce a version
    here — and the two databases still never meet outside this request.
    """
    from datetime import UTC, datetime, timedelta

    from app.domains.off import client as off_client
    from app.domains.off.models import OffProduct
    from app.domains.off.store import get_off_sessionmaker
    from app.domains.product import service as product_service

    barcode = "8900000000142"
    async with get_off_sessionmaker()() as off_session:
        off_session.add(OffProduct(
            barcode=barcode,
            product_name="Catalogue version A",
            ingredients_text="water",
            fetched_at=datetime.now(UTC) - product_service.OFF_CACHE_TTL - timedelta(days=1),
        ))
        await off_session.commit()

    async def _refresh(_barcode: str):
        return {
            "product_name": "Catalogue version B",
            "brands": "Example",
            "ingredients_text": "water,glycerin,niacinamide",
        }

    monkeypatch.setattr(off_client, "fetch_product", _refresh)
    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["facts_provenance"] == "open_food_facts"
    assert payload["label_version"] is None
    assert payload["label_change"] is None
    assert await _versions(barcode) == []


async def test_24_a_to_b_to_a_compares_only_the_immediate_predecessor(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """Returning to an earlier formula is a change from the pack before it."""
    barcode = "8900000000135"
    token, account_id = await registered_supabase_user()
    base = {"product_name": "Observed cleanser", **NUTRITION}
    for ingredients in ("Water", "Water,Glycerin", "Water"):
        await _confirm(
            app_client, device, token, account_id, barcode,
            {**base, "ingredients_text": ingredients},
        )

    change = (await app_client.get(
        f"/api/v2/scan/verdict/{barcode}", headers=device,
    )).json()["label_change"]

    assert (change["previous_version"], change["current_version"]) == (2, 3)
    assert change["formula"]["only_on_previous_label"] == [{"name": "Glycerin", "occurrences": 1}]
    assert change["formula"]["only_on_current_label"] == []

    versions = await _versions(barcode)
    assert [row.version_number for row in versions] == [1, 2, 3]
    assert versions[2].previous_snapshot_id == versions[1].id
    # Version 3 repeats version 1's content. The history keeps both, because
    # the pack really was observed twice.
    assert versions[2].content_fingerprint == versions[0].content_fingerprint


async def test_25_corrupt_stored_history_silences_the_envelope_not_the_verdict(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """An addition may not take the page down with it.

    The grade, the band and every card were established without Step 12A. When
    the history underneath fails its own invariants, the honest answer is that
    the history is unavailable — not that the product is.
    """
    barcode = "8900000000173"
    token, account_id = await registered_supabase_user()
    base = {"product_name": "Observed product", "ingredients_text": "Water", **NUTRITION}
    await _confirm(app_client, device, token, account_id, barcode, base)
    healthy = (await app_client.get(
        f"/api/v2/scan/verdict/{barcode}", headers=device,
    )).json()

    await _confirm(
        app_client, device, token, account_id, barcode,
        {**base, "ingredients_text": "Water,Glycerin"},
    )

    async with get_sessionmaker()() as session:
        latest = (await session.execute(
            select(LabelSnapshot)
            .where(LabelSnapshot.barcode == barcode)
            .order_by(LabelSnapshot.version_number.desc())
            .limit(1)
        )).scalar_one()
        latest.changed_fields = ["net_quantity"]
        await session.commit()

    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["label_change"] == UNAVAILABLE_PROJECTION.as_payload()
    assert payload["label_change"]["status"] == "unavailable"
    # "Unavailable" is not "never seen", which is what ``null`` means.
    assert payload["label_change"] is not None
    assert payload["label_version"]["version_number"] == 2
    for key in ("grade", "band", "components", "evidence"):
        if key in healthy:
            assert payload[key] == healthy[key], key
    # Not one word of the diagnosis reached the customer.
    for reason in (
        "label_changed_fields_mismatch",
        "label_predecessor",
        "invariant",
        "mismatch",
    ):
        assert reason not in response.text


# ---------------------------------------------------------------------------
# 26–27. Two phones at once
# ---------------------------------------------------------------------------
async def test_26_two_identical_confirmations_at_once_make_one_version(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """The same pack photographed twice is one version of it, under any timing."""
    barcode = "8900000000180"
    token, account_id = await registered_supabase_user()
    facts = {"product_name": "Observed", "ingredients_text": "Water,Glycerin", **NUTRITION}
    await _confirm(app_client, device, token, account_id, barcode, facts)

    runs = [await _seed_label_run(facts, account_id) for _ in range(2)]
    responses = await asyncio.gather(*(
        app_client.post(
            "/api/v2/scan/label/confirm",
            headers={**device, **auth(token)},
            json={
                "barcode": barcode,
                "ai_run_id": str(run_id),
                "client_scan_id": uuid.uuid4().hex,
            },
        )
        for run_id in runs
    ))
    assert [row.status_code for row in responses] == [201, 201]

    versions = await _versions(barcode)
    assert [row.version_number for row in versions] == [1]

    change = (await app_client.get(
        f"/api/v2/scan/verdict/{barcode}", headers=device,
    )).json()["label_change"]
    assert change["status"] == "first_observed_version"


async def test_27_two_different_confirmations_at_once_leave_one_unbroken_chain(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """Whoever lands second is version 2, and points at version 1."""
    barcode = "8900000000197"
    token, account_id = await registered_supabase_user()
    base = {"product_name": "Observed", "ingredients_text": "Water", **NUTRITION}
    await _confirm(app_client, device, token, account_id, barcode, base)

    runs = [
        await _seed_label_run({**base, "ingredients_text": text}, account_id)
        for text in ("Water,Glycerin", "Water,Niacinamide")
    ]
    responses = await asyncio.gather(*(
        app_client.post(
            "/api/v2/scan/label/confirm",
            headers={**device, **auth(token)},
            json={
                "barcode": barcode,
                "ai_run_id": str(run_id),
                "client_scan_id": uuid.uuid4().hex,
            },
        )
        for run_id in runs
    ))
    assert [row.status_code for row in responses] == [201, 201]

    versions = await _versions(barcode)
    assert [row.version_number for row in versions] == [1, 2, 3]
    assert versions[1].previous_snapshot_id == versions[0].id
    assert versions[2].previous_snapshot_id == versions[1].id
    assert len({row.content_fingerprint for row in versions}) == 3

    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)
    change = response.json()["label_change"]
    assert response.status_code == 200, response.text
    assert change["status"] == "changed"
    assert (change["previous_version"], change["current_version"]) == (2, 3)


# ---------------------------------------------------------------------------
# 28–29. What this layer is not allowed to say, and what it must not restate
# ---------------------------------------------------------------------------
def test_28_the_layer_never_characterises_a_change():
    """Every word here would be a judgement about a manufacturer or a formula.

    Step 12A states what two labels said. Whether that is better, worse, safer
    or stronger is not a fact this step holds, and 12B and 12C are where the
    interpretation is supposed to arrive.
    """
    source = (
        BACKEND_ROOT / "app" / "domains" / "product" / "change_projection.py"
    ).read_text(encoding="utf-8")
    # The module's own opening paragraph lists what this layer does not decide,
    # which is a boundary being stated and not a claim being made. Everything
    # after it is the code, and none of this vocabulary belongs there.
    preamble, body = source.split('"""', 2)[1:]
    assert "safer" in preamble  # the boundary really is stated, once
    for word in (
        "reformulated", "reformulation", "manufacturer changed", "improved formula",
        "downgraded", "safer", "stronger", "weaker", "concentration increased",
        "recall", "unsafe", "harmful", "diagnos",
    ):
        assert word not in body.lower(), word

    payload_words = repr([
        status.value for status in change_projection.FormulaChangeStatus
    ] + [status.value for status in change_projection.LabelChangeStatus])
    for word in ("reformulated", "safer", "stronger", "weaker", "improved", "worse"):
        assert word not in payload_words


def test_29_the_boundary_rule_is_one_rule_in_three_places_that_agree():
    """The migration froze a copy of the canonicalisation. It must still match.

    A migration has to keep doing what it did on the day it ran, so it cannot
    import a rule that is free to change underneath it. The copy is therefore
    deliberate — and this is the test that notices when the two drift apart.
    """
    import importlib.util

    from app.domains.formulas.parser import LINE_BOUNDARIES
    from app.domains.product.formula_projection import (
        LINE_BOUNDARIES as DOOR_BOUNDARIES,
    )
    from app.domains.product.service import (
        CONTENT_FACT_FIELDS,
        FORMULA_SIGNIFICANT_FACT_FIELDS,
    )

    assert DOOR_BOUNDARIES is LINE_BOUNDARIES

    path = (
        BACKEND_ROOT / "migrations" / "versions"
        / "j8k9l0m1n2_step12a_boundary_aware_label_identity.py"
    )
    spec = importlib.util.spec_from_file_location("step12a_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert set(migration.LINE_BOUNDARIES) == set(LINE_BOUNDARIES)
    assert set(migration.CONTENT_FACT_FIELDS) == set(CONTENT_FACT_FIELDS)
    assert (
        set(migration.FORMULA_SIGNIFICANT_FACT_FIELDS)
        == set(FORMULA_SIGNIFICANT_FACT_FIELDS)
    )

    # And the frozen implementation still agrees, fingerprint for fingerprint,
    # across the shapes that make boundaries interesting — including the
    # grouping and compatibility forms the structural grammar has to get right.
    texts = (
        "Water,  Glycerin", "Water\nGlycerin", "Water\r\n Glycerin",
        "\nWater,Glycerin\n", "", "Parfum (A\nB), Water", "Parfum (A B), Water",
        "Parfum（A\nB）, Water", "A(B(C\nD)E), F", "Water)G\nH",
        "Aqua\t(Water\r\nDeionised) , Glycerin", "N,N-Dimethylacetamide\nWater",
    )
    for text in texts:
        facts = {"product_name": " Oats  ", "ingredients_text": text}
        assert migration._fingerprint(facts, boundary_aware=True) == (
            label_content_fingerprint(facts)
        ), text
        assert migration._normalise(
            text, preserve_boundaries=True,
        ) == canonical_label_facts(facts).get("ingredients_text"), text
        assert migration._boundary_significance(text) == boundary_significance(text), text

    for facts in (
        {"nutrition_per_100g": {"sugars_g": "1"}, "nutrition_basis": "per_100g"},
        {"ingredients_text": "", "product_name": "Only a name"},
    ):
        assert migration._fingerprint(facts, boundary_aware=True) == (
            label_content_fingerprint(facts)
        ), facts

    # The difference between two labels is derived the same way on both sides
    # too. This is the value the migration rewrites, so a drift here would put
    # every migrated row at odds with the projection that reads it back.
    for left in texts:
        for right in texts:
            previous = {"ingredients_text": left, "net_quantity": "100 g"}
            current = {"ingredients_text": right, "net_quantity": "120 g"}
            assert migration._changed_fields(
                previous, current, boundary_aware=True
            ) == label_changed_fields(previous, current), (left, right)


async def test_30_the_backfill_moves_every_stored_copy_of_a_stale_fingerprint(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """The migration body, run against real rows rather than only on release day.

    Three tables hold a snapshot's fingerprint, and Step 12A's integrity check
    reads one of them on every product request. A backfill that moved the
    snapshot and left the two copies behind would silence the history for every
    shelf item and every remembered decision on a line-broken label.

    The snapshot and the remembered decision are written through their real
    routes. The shelf link is constructed, because the shelf only accepts care
    categories and the food-label schema has no category field — and what is
    under test here is the backfill's SQL, not the shelf's eligibility rule.
    """

    from app.bootstrap import seed_inventory_categories
    from app.domains.inventory.models import InventoryItem, InventoryProductLink
    from app.domains.product.models import ProductRecord
    from sqlalchemy import text

    barcode = "8900000000203"
    token, account_id = await registered_supabase_user()
    await _confirm(
        app_client, device, token, account_id, barcode,
        {"product_name": "Observed", "ingredients_text": "Water\nGlycerin", **NUTRITION},
    )
    snapshot = (await _versions(barcode))[0]

    remembered = await app_client.post(
        f"/api/v2/scan/verdict/{barcode}/memory",
        headers={**device, **auth(token)},
        json={
            "decision": "BUY",
            "label_snapshot_id": str(snapshot.id),
            "label_version": snapshot.version_number,
            "content_fingerprint": snapshot.content_fingerprint,
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    assert remembered.status_code == 200, remembered.text

    async with get_sessionmaker()() as session:
        await seed_inventory_categories(session)
        product = (await session.execute(
            select(ProductRecord).where(ProductRecord.barcode == barcode)
        )).scalar_one()
        item = InventoryItem(
            account_id=account_id, category="beauty", display_name="Observed",
        )
        session.add(item)
        await session.flush()
        session.add(InventoryProductLink(
            account_id=account_id,
            inventory_item_id=item.id,
            product_record_id=product.id,
            barcode=barcode,
            label_snapshot_id=snapshot.id,
            label_version=snapshot.version_number,
            content_fingerprint=snapshot.content_fingerprint,
        ))
        await session.commit()

    migration = _load_migration()

    stale = migration._fingerprint(snapshot.facts, boundary_aware=False)
    assert stale != snapshot.content_fingerprint  # the row really is affected

    async def _fingerprints() -> set[str]:
        async with get_sessionmaker()() as session:
            rows = await session.execute(text(
                "SELECT content_fingerprint FROM product_label_snapshots "
                "UNION ALL SELECT content_fingerprint FROM scan_decision_events "
                "UNION ALL SELECT content_fingerprint FROM inventory_product_links"
            ))
            return set(rows.scalars().all())

    async def _run_backfill(*, forward: bool) -> None:
        await _apply_revision(migration, forward=forward)

    assert len(await _fingerprints()) == 1  # all three tables agree to begin with

    # Wind the three tables back to the identity the old rule produced, then
    # let the migration bring them forward again.
    await _run_backfill(forward=False)
    assert await _fingerprints() == {stale}

    await _run_backfill(forward=True)
    assert await _fingerprints() == {snapshot.content_fingerprint}

    # And the product page is readable again, which is the point of the whole
    # backfill: a stale copy is what the integrity check refuses.
    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)
    assert response.status_code == 200, response.text
    assert response.json()["label_change"]["status"] == "first_observed_version"


def _load_migration():
    """The Step 12A revision, loaded as a module so its own body can be run."""
    import importlib.util

    path = (
        BACKEND_ROOT / "migrations" / "versions"
        / "j8k9l0m1n2_step12a_boundary_aware_label_identity.py"
    )
    spec = importlib.util.spec_from_file_location("step12a_revision", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _run_revision(sync_connection, migration, *, forward: bool) -> None:
    """Run the revision's own ``upgrade`` / ``downgrade``, not its helper.

    Going through Alembic's operations context means the wiring is under test
    too: a revision whose ``upgrade`` quietly did nothing, or applied the rule
    it was meant to undo, fails here rather than on release day.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    context = MigrationContext.configure(sync_connection)
    with Operations.context(context):
        migration.upgrade() if forward else migration.downgrade()


async def _apply_revision(migration, *, forward: bool) -> None:
    async with get_sessionmaker()() as session:
        connection = await session.connection()
        await connection.run_sync(
            lambda sync: _run_revision(sync, migration, forward=forward)
        )
        await session.commit()


async def test_31_the_backfill_migrates_the_difference_between_two_labels(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """History written before Step 12A has to arrive as history it still owns.

    ``changed_fields`` is a derived reading of two labels, and Step 12A changed
    the canonicalisation it is derived from. A pack whose ingredient line wrapped
    differently was legitimately recorded as "only the quantity changed", because
    under the old rule the newline *was* a space. Leave that value behind and the
    projection recomputes a different one on the very next request and refuses the
    whole history as corrupt — for data nothing was ever wrong with.

    So the migration moves it, in both directions, using the predecessor each row
    actually names.
    """
    from sqlalchemy import text

    barcode = "8900000000210"
    token, account_id = await registered_supabase_user()

    # The exact pre-Step-12A history: a wrapped ingredient line that the old
    # rule could not tell apart, and a quantity that genuinely changed.
    first = {"product_name": "Observed", "ingredients_text": "Water\nGlycerin",
             "net_quantity": "100 g", **NUTRITION}
    second = {**first, "ingredients_text": "Water Glycerin", "net_quantity": "120 g"}
    await _confirm(app_client, device, token, account_id, barcode, first)
    await _confirm(app_client, device, token, account_id, barcode, second)

    versions = await _versions(barcode)
    assert [row.version_number for row in versions] == [1, 2]
    assert versions[1].previous_snapshot_id == versions[0].id

    migration = _load_migration()

    async def _second_version_changed_fields() -> list[str]:
        async with get_sessionmaker()() as session:
            return (await session.execute(
                text(
                    "SELECT changed_fields FROM product_label_snapshots "
                    "WHERE barcode = :barcode AND version_number = 2"
                ),
                {"barcode": barcode},
            )).scalar_one()

    # Today's write path already stores the new reading, so wind the pair back
    # to the state a pre-Step-12A deployment left behind.
    await _apply_revision(migration, forward=False)
    assert await _second_version_changed_fields() == ["net_quantity"]

    await _apply_revision(migration, forward=True)
    assert await _second_version_changed_fields() == ["ingredients", "net_quantity"]

    await _apply_revision(migration, forward=False)
    assert await _second_version_changed_fields() == ["net_quantity"]

    await _apply_revision(migration, forward=True)
    assert await _second_version_changed_fields() == ["ingredients", "net_quantity"]

    # And the point of all of it: the product page reads its own history back.
    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)
    assert response.status_code == 200, response.text
    change = response.json()["label_change"]
    assert change["status"] == "changed"
    assert change["changed_fields"] == ["ingredients", "net_quantity"]
    assert change["formula"]["status"] == "not_comparable"
    assert change["formula"]["previous_parse_status"] == "ambiguous_boundary"
    assert change["formula"]["current_parse_status"] == "parsed"


async def test_32_the_backfill_never_invents_a_predecessor(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """Structurally broken history is left broken, not tidied into a story.

    Two rows that no longer name each other cannot have their difference
    recomputed from anything. Pairing them by barcode or by version number
    would produce a clean-looking history that no observation supports, and the
    corruption would never be seen again.
    """
    from sqlalchemy import text

    barcode = "8900000000227"
    token, account_id = await registered_supabase_user()
    first = {"product_name": "Observed", "ingredients_text": "Water\nGlycerin",
             "net_quantity": "100 g", **NUTRITION}
    await _confirm(app_client, device, token, account_id, barcode, first)
    await _confirm(
        app_client, device, token, account_id, barcode,
        {**first, "ingredients_text": "Water Glycerin", "net_quantity": "120 g"},
    )

    async with get_sessionmaker()() as session:
        await session.execute(
            text(
                "UPDATE product_label_snapshots SET previous_snapshot_id = NULL, "
                "changed_fields = CAST('[\"net_quantity\"]' AS jsonb) "
                "WHERE barcode = :barcode AND version_number = 2"
            ),
            {"barcode": barcode},
        )
        await session.commit()

    await _apply_revision(_load_migration(), forward=True)

    async with get_sessionmaker()() as session:
        stored = (await session.execute(
            text(
                "SELECT changed_fields FROM product_label_snapshots "
                "WHERE barcode = :barcode AND version_number = 2"
            ),
            {"barcode": barcode},
        )).scalar_one()
    assert stored == ["net_quantity"]  # untouched, not rewritten

    # The projection refuses it in the open rather than the migration hiding it.
    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)
    assert response.status_code == 200, response.text
    assert response.json()["label_change"]["status"] == "unavailable"


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param(["not", "an", "object"], id="json-array"),
        pytest.param("broken", id="json-string"),
        pytest.param(42, id="json-number"),
        pytest.param(None, id="json-null"),
        pytest.param({"nutrition_per_100g": {1: "a", "b": "c"}}, id="unsortable-member"),
    ],
)
def test_33_facts_that_are_not_facts_are_refused_in_the_governed_way(broken):
    """A corrupt JSONB row must not arrive as an ``AttributeError``.

    The route can only fail soft over a failure it recognises. An ordinary
    Python exception three frames down is not one, so a single malformed row
    would take the whole product page to a 500 — the exact opposite of the
    contract this layer was given.
    """
    current = _snapshot({"ingredients_text": "Water"})
    current.facts = broken

    with pytest.raises(LabelHistoryInvariantError) as raised:
        project_label_change(current=current, previous=None)
    assert raised.value.reason == "current_label_facts_invalid"


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param(["not", "an", "object"], id="json-array"),
        pytest.param("broken", id="json-string"),
    ],
)
def test_34_a_predecessor_that_is_not_facts_is_refused_the_same_way(broken):
    """The predecessor is read too, and is corrupt on the same terms."""
    old, new = _pair("Water", "Water,Glycerin")
    old.facts = broken

    with pytest.raises(LabelHistoryInvariantError) as raised:
        project_label_change(current=new, previous=old)
    assert raised.value.reason == "previous_label_facts_invalid"


@pytest.mark.parametrize(
    ("broken", "sql"),
    [
        pytest.param("array", "CAST('[\"not\",\"an\",\"object\"]' AS jsonb)", id="json-array"),
        pytest.param("string", "CAST('\"broken\"' AS jsonb)", id="json-string"),
    ],
)
async def test_35_a_corrupt_row_silences_the_history_not_the_product(
    db_clean, off_clean, app_client, device, registered_supabase_user, broken, sql,
):
    """The whole point of the fail-soft envelope, against a really corrupt row.

    Corrupted in the database rather than in memory, because that is where this
    can actually happen and because an in-memory object cannot prove the route
    survives the trip.
    """
    from datetime import UTC, datetime

    from app.domains.off.models import OffProduct
    from app.domains.off.store import get_off_sessionmaker
    from sqlalchemy import text

    barcode = f"890000000023{'4' if broken == 'array' else '5'}"
    control = f"890000000024{'4' if broken == 'array' else '5'}"
    token, account_id = await registered_supabase_user()

    # A catalogue record for the corrupted barcode, so that once the confirmed
    # facts become unreadable there is still something the page can honestly
    # grade — and an identical one for a barcode nobody has photographed, which
    # is the control the corrupted page has to match. Store A is read at query
    # time and never written into Store B.
    catalogue = {
        "product_name": "Catalogue biscuit",
        "brands": "Example",
        "ingredients_text": "wheat flour, sugar",
        "nutriments": {"energy-kcal_100g": 480.0, "sugars_100g": 22.5, "salt_100g": 0.7},
    }
    async with get_off_sessionmaker()() as off_session:
        for code in (barcode, control):
            off_session.add(
                OffProduct(barcode=code, fetched_at=datetime.now(UTC), **catalogue)
            )
        await off_session.commit()

    await _confirm(
        app_client, device, token, account_id, barcode,
        {"product_name": "Observed food", "ingredients_text": "Water,Glycerin", **NUTRITION},
    )
    healthy = (await app_client.get(
        f"/api/v2/scan/verdict/{barcode}", headers=device,
    )).json()
    assert healthy["label_change"]["status"] == "first_observed_version"
    assert healthy["facts_provenance"] == "confirmed_label_snapshot"

    async with get_sessionmaker()() as session:
        await session.execute(
            text(
                f"UPDATE product_label_snapshots SET facts = {sql} "  # noqa: S608 - fixed literals
                "WHERE barcode = :barcode"
            ),
            {"barcode": barcode},
        )
        await session.commit()

    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == VERDICT_KEYS

    # The precise claim, rather than a hand-picked field or two: the page is
    # exactly the page this product gets when it has no readable confirmed
    # observation at all. Step 12A silenced its own envelope and nothing else.
    reference = (await app_client.get(
        f"/api/v2/scan/verdict/{control}", headers=device,
    )).json()
    assert reference["label_version"] is None
    assert reference["label_change"] is None
    # ``physical_pack_context`` is about this device's own scan history, not
    # about the snapshot's contents: this device really did photograph this
    # pack and never photographed the control's. That the corrupted row does
    # not cost the caller that authority is itself worth stating.
    assert payload["physical_pack_context"] is True
    assert reference["physical_pack_context"] is False

    moved = {
        key for key in VERDICT_KEYS
        if key not in ("barcode", "physical_pack_context", "label_version", "label_change")
        and payload[key] != reference[key]
    }
    assert moved == set(), moved

    # And that page really does carry a verdict, graded from what could still
    # be read, rather than claiming a confirmed pack it cannot open.
    assert payload["band"] is not None
    assert payload["nutrition"]["total_sugar_g"] == 22.5
    assert payload["components"]
    assert payload["facts_provenance"] == "open_food_facts"
    # The version still exists and is still what decision memory and shelf
    # links are pinned to, so it is still reported.
    assert payload["label_version"] is not None
    assert payload["label_version"]["version_number"] == 1
    assert payload["label_version"]["id"] == healthy["label_version"]["id"]
    # And the history says it cannot speak, rather than claiming there is none.
    assert payload["label_change"] == UNAVAILABLE_PROJECTION.as_payload()
    assert payload["label_change"]["status"] == "unavailable"
    assert payload["label_change"] is not None
    # Nothing the log was told reaches the customer.
    for reason in ("facts_invalid", "label_facts", "invariant", "mismatch"):
        assert reason not in response.text
    assert not _uuids_in(payload["label_change"])


async def test_36_the_backfill_pairs_rows_only_by_the_link_they_carry(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """Two products, interleaved versions, and no room to guess.

    Version numbers are per barcode, so "the row one version lower" is not a
    predecessor — it is a different product's pack. Pairing on anything other
    than ``previous_snapshot_id`` computes a difference between two labels that
    were never observed as a sequence, and writes it into history as though
    somebody had seen it.
    """
    from sqlalchemy import text

    token, account_id = await registered_supabase_user()
    first_barcode, second_barcode = "8900000000241", "8900000000258"

    # Deliberately different products, so a mis-pairing produces a visibly
    # different answer rather than accidentally the right one.
    left = {"product_name": "Left", "ingredients_text": "Water\nGlycerin",
            "net_quantity": "100 g", **NUTRITION}
    right = {"product_name": "Right", "ingredients_text": "Cocamidopropyl Betaine",
             "net_quantity": "500 ml", **NUTRITION}

    await _confirm(app_client, device, token, account_id, first_barcode, left)
    await _confirm(app_client, device, token, account_id, second_barcode, right)
    await _confirm(
        app_client, device, token, account_id, first_barcode,
        {**left, "ingredients_text": "Water Glycerin", "net_quantity": "120 g"},
    )
    await _confirm(
        app_client, device, token, account_id, second_barcode,
        {**right, "ingredients_text": "Cocamidopropyl Betaine, Water", "net_quantity": "750 ml"},
    )

    migration = _load_migration()

    async def _stored(barcode: str) -> list[str]:
        async with get_sessionmaker()() as session:
            return (await session.execute(
                text(
                    "SELECT changed_fields FROM product_label_snapshots "
                    "WHERE barcode = :barcode AND version_number = 2"
                ),
                {"barcode": barcode},
            )).scalar_one()

    await _apply_revision(migration, forward=False)
    assert await _stored(first_barcode) == ["net_quantity"]
    assert await _stored(second_barcode) == ["ingredients", "net_quantity"]

    await _apply_revision(migration, forward=True)
    # The wrapped line is now visible on the left product, and the right
    # product is untouched — which it would not be if the walk had reached
    # across the two chains looking for "the previous version number".
    assert await _stored(first_barcode) == ["ingredients", "net_quantity"]
    assert await _stored(second_barcode) == ["ingredients", "net_quantity"]

    for barcode in (first_barcode, second_barcode):
        response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)
        assert response.status_code == 200, response.text
        assert response.json()["label_change"]["status"] == "changed"
