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
from app.domains.product.formula_projection import formula_entries_from_label_snapshot
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
    assert result.formula.added == () and result.formula.removed == ()


async def test_2_case_only_difference_is_a_label_change_but_not_a_formula_change():
    old, new = _pair("Water,Glycerin", "water,glycerin")
    result = project_label_change(current=new, previous=old)

    assert result.changed_fields == ("ingredients",)
    assert result.formula.status is FormulaChangeStatus.UNCHANGED
    assert result.formula.added == () and result.formula.removed == ()


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
    assert result.formula.added == () and result.formula.removed == ()


async def test_6_a_single_addition_is_reported_once():
    old, new = _pair("Water,Glycerin", "Water,Glycerin,Niacinamide")
    result = project_label_change(current=new, previous=old)

    assert result.formula.status is FormulaChangeStatus.INGREDIENT_SET_CHANGED
    assert [row.as_payload() for row in result.formula.added] == [
        {"name": "Niacinamide", "occurrences": 1},
    ]
    assert result.formula.removed == ()


async def test_7_a_single_removal_is_reported_once():
    old, new = _pair("Water,Glycerin,Niacinamide", "Water,Glycerin")
    result = project_label_change(current=new, previous=old)

    assert result.formula.status is FormulaChangeStatus.INGREDIENT_SET_CHANGED
    assert result.formula.added == ()
    assert [row.as_payload() for row in result.formula.removed] == [
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
    assert [row.as_payload() for row in result.formula.added] == [
        {"name": "Niacinamide", "occurrences": 2},
    ]
    assert [row.as_payload() for row in result.formula.removed] == [
        {"name": "Glycerin", "occurrences": 1},
    ]


async def test_9_an_unreadable_list_is_never_partially_compared():
    """No half answer. A client can say the label changed and stop there."""
    old, new = _pair("Water,Glycerin", "Water,,Glycerin")
    result = project_label_change(current=new, previous=old)

    assert result.formula.status is FormulaChangeStatus.NOT_COMPARABLE
    assert result.formula.added == () and result.formula.removed == ()
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
    assert result.formula.added == () and result.formula.removed == ()


async def test_12_a_pack_change_that_left_the_ingredients_alone_is_not_a_formula_change():
    old = _snapshot({"ingredients_text": "Water", "net_quantity": "100 g"})
    new = _snapshot(
        {"ingredients_text": "Water", "net_quantity": "120 g"}, version=2, previous=old,
    )
    result = project_label_change(current=new, previous=old)

    assert result.changed_fields == ("net_quantity",)
    assert result.formula.status is FormulaChangeStatus.UNCHANGED
    assert result.formula.added == () and result.formula.removed == ()


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
    assert after["formula"]["added"] == [{"name": "Niacinamide", "occurrences": 1}]


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
            "added": [],
            "removed": [],
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
    assert change["formula"]["added"] == [{"name": "Niacinamide", "occurrences": 1}]
    assert change["formula"]["removed"] == [{"name": "Glycerin", "occurrences": 1}]
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
    assert change["formula"]["removed"] == [{"name": "Glycerin", "occurrences": 1}]
    assert change["formula"]["added"] == []

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

    # And the frozen implementation still agrees fingerprint for fingerprint.
    for facts in (
        {"product_name": " Oats  ", "ingredients_text": "Water,  Glycerin"},
        {"ingredients_text": "Water\nGlycerin"},
        {"ingredients_text": "Water\r\n Glycerin", "brand": "Acme\n Labs"},
        {"ingredients_text": "\nWater,Glycerin\n"},
        {"nutrition_per_100g": {"sugars_g": "1"}, "nutrition_basis": "per_100g"},
        {"ingredients_text": "", "product_name": "Only a name"},
    ):
        assert migration._fingerprint(facts, boundary_aware=True) == (
            label_content_fingerprint(facts)
        ), facts
        assert migration._normalise(
            facts.get("ingredients_text"), preserve_boundaries=True,
        ) == canonical_label_facts(facts).get("ingredients_text"), facts
