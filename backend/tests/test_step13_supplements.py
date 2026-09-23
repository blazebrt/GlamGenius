"""Step 13 — governed supplement label and ownership intelligence.

The matrix A–X from the Step 13 brief, against a real PostgreSQL database and
the real routes. Where a test needs a supplement knowledge entry to be
*published*, it walks the real authoring workflow (approve, record the
verification attestations, publish) and performs, as explicit test fixtures,
the two operator acts the generic authoring tool has no step for: grading the
claim and classifying its source. Those fixtures stand in for people. Nothing
in this file claims that any real entry was reviewed; the real knowledge base
stays unreviewed and is proven dormant in section H.
"""
from __future__ import annotations

import ast
import json
import logging
import re
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from app.domains.evidence import authoring as evidence_authoring
from app.domains.evidence.enums import EvidenceStrength, ReviewStatus, SourceType
from app.domains.evidence.grading import Verification
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource
from app.domains.inventory.models import InventoryItem
from app.domains.off.models import OffProduct
from app.domains.off.store import get_off_sessionmaker
from app.domains.privacy import deletion_service
from app.domains.routines.hard_handoff import requires_handoff
from app.domains.routines.safety import needs_professional
from app.domains.supplements import boundary as supplement_boundary
from app.domains.supplements import forms
from app.domains.supplements import strings as supplement_copy
from app.domains.supplements.chemistry import elemental_percent
from app.domains.supplements.detail import build_detail
from app.domains.supplements.engine import build_utility
from app.domains.supplements.knowledge import COMPOUNDS
from app.domains.supplements.knowledge_loader import load
from app.domains.supplements.knowledge_reader import (
    BINDING_KEY,
    KnowledgeStatus,
    WithheldBecause,
    read_form_knowledge,
)
from app.domains.supplements.models import SupplementComponentKnowledge, SupplementLabelComponent
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import auth
from tests.journey import ok

VERIFIED = evidence_authoring.VerificationInput(
    source_opened=True,
    founder_verified_fact=True,
    claude_review_completed=True,
    codex_review_completed=True,
    independent_reviews_agree=True,
    adversarial_review_passed=True,
    unresolved_doubt=False,
)

#: Wording this surface may never produce in its own voice.
ADVICE_PHRASES = (
    "you should take", "take 2", "take two", "take one", "your dose", "your dosage", "recommended dose",
    "recommended dosage", "daily intake", "per day you", "you are taking", "too much", "exceed",
    "safe limit", "upper limit", "you need", "deficient", "unsafe", "toxic", "spoiled", "ineffective",
    "stop taking", "start taking", "absorbed amount", "you absorb", "you get",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _supplement(client, headers, name: str, *, expiry: str | None = None, purpose: str | None = None) -> str:
    details: dict[str, str] = {"supplement_name": name}
    if expiry:
        details["expiry_date"] = expiry
    if purpose:
        details["user_entered_purpose"] = purpose
    item = ok(await client.post(
        "/api/v2/inventory/items", headers=headers,
        json={"category": "supplements", "display_name": name, "details": details},
    ))
    return item["id"]


async def _fact(client, headers, item_id: str, raw_name: str, amount: str | None = None,
                unit: str | None = None, serving_text: str | None = None) -> dict:
    body = {"raw_name": raw_name, "amount": amount, "unit": unit, "serving_text": serving_text}
    return ok(await client.post(
        f"/api/v2/supplements/items/{item_id}/label-facts", headers=headers,
        json={key: value for key, value in body.items() if value is not None},
    ))


async def _detail(client, headers, item_id: str) -> dict:
    return ok(await client.get(f"/api/v2/supplements/items/{item_id}", headers=headers))


def _component(detail: dict, printed_name: str) -> dict:
    return next(row for row in detail["components"] if row["printed"]["name"] == printed_name)


async def _photo_draft(account_id, item_id: str, raw_name: str, **values) -> uuid.UUID:
    """A draft row shaped as the photo bridge writes it, inserted directly (the route has its own tests)."""
    from app.domains.supplements.engine import component_identity

    key, _display = component_identity(raw_name)
    async with get_sessionmaker()() as session:
        row = SupplementLabelComponent(
            account_id=account_id, item_id=uuid.UUID(item_id), raw_name=raw_name,
            normalized_name=key, canonical_component_key=key,
            source="photo_extracted", verification_state="draft", confidence=0.61,
            **values,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _load_knowledge() -> None:
    async with get_sessionmaker()() as session:
        await load(session)
        await session.commit()


async def _publish_form(
    form: str,
    *,
    confirm_row: bool = True,
    publish: bool = True,
    approve: bool = True,
    source_type: str = SourceType.PEER_REVIEWED_RESEARCH.value,
) -> uuid.UUID:
    """Walk one loaded entry to publication. Test fixture: stands in for people.

    Grading the claim and classifying its source are operator acts the generic
    authoring tool has no step for; they are set directly here and nowhere else.
    """
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(SupplementComponentKnowledge).where(
            SupplementComponentKnowledge.compound_form == form,
        ))).scalar_one()
        claim = await session.get(EvidenceClaim, row.evidence_claim_id)
        claim.evidence_strength = EvidenceStrength.MODERATE.value
        claim.strength_rationale = "Test fixture: graded by a person in the real workflow."
        for link, source in (await session.execute(
            select(EvidenceClaimSource, EvidenceSource)
            .join(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id)
            .where(EvidenceClaimSource.claim_id == claim.id)
        )).all():
            source.source_type = source_type
            source.license_or_use_note = "Test fixture: cited under the publisher's terms."
        await session.flush()
        if approve:
            await evidence_authoring.approve(session, claim.id, reviewer="reviewer")
            await evidence_authoring.record_publication_verification(
                session, claim.id, verification=VERIFIED, actor="founder",
            )
        if approve and publish:
            await evidence_authoring.publish(session, claim.id, publisher="founder")
        if confirm_row:
            row.verification = Verification.CONFIRMED.value
        await session.commit()
        return claim.id


async def _knowledge(key: str, form: str):
    async with get_sessionmaker()() as session:
        return (await read_form_knowledge(session, [(key, form)]))[(key, form)]


#: Keys whose values are identifiers or hashes. Random hex can contain any
#: digit run, so number checks must never look inside them.
_IDENTIFIER_KEYS = frozenset({"id", "inventory_item_id", "item_id", "fingerprint"})


def _without_identifiers(value):
    """The payload with every identifier and hash value removed."""
    if isinstance(value, dict):
        return {key: _without_identifiers(item) for key, item in value.items() if key not in _IDENTIFIER_KEYS}
    if isinstance(value, list):
        return [_without_identifiers(item) for item in value]
    return value


def _mentions_number(payload, number: str) -> bool:
    """Does ``number`` appear as a whole number anywhere outside identifiers?"""
    rendered = json.dumps(_without_identifiers(payload))
    return re.search(rf"(?<![\d.]){re.escape(number)}(?![\d])", rendered) is not None


def test_number_checks_ignore_identifiers_and_respect_digit_boundaries():
    payload = {"id": "a3013017-0000-4000-8000-000000000750", "fingerprint": "301f750", "x": {"amount": "1301"}}
    assert not _mentions_number(payload, "301") and not _mentions_number(payload, "750")
    assert _mentions_number({"total": "750"}, "750") and _mentions_number({"v": "301.5 mg"}, "301.5")


def _walk_keys(value, found: set[str]) -> set[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(str(key))
            _walk_keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _walk_keys(item, found)
    return found


# ---------------------------------------------------------------------------
# A. Manual fact provenance
# ---------------------------------------------------------------------------
async def test_a_manual_entry_stays_user_declared_and_cannot_become_scan_confirmed(
    app_client, db_clean, registered_supabase_user,
):
    token, _account = await registered_supabase_user()
    headers = auth(token)
    item_id = await _supplement(app_client, headers, "Daily C")
    fact = await _fact(app_client, headers, item_id, "Vitamin C", "500", "mg")
    assert fact["source"] == "user_declared" and fact["verification_state"] == "confirmed"

    # Confirming a manual fact changes nothing about where it came from.
    confirmed = ok(await app_client.post(
        f"/api/v2/supplements/items/{item_id}/label-facts/{fact['id']}/confirm", headers=headers,
    ))
    assert confirmed["source"] == "user_declared"
    for field, value in {"source": "photo_extracted", "verification_state": "draft"}.items():
        patched = await app_client.patch(
            f"/api/v2/supplements/items/{item_id}/label-facts/{fact['id']}", headers=headers, json={field: value},
        )
        assert patched.status_code == 422, field

    detail = await _detail(app_client, headers, item_id)
    row = _component(detail, "Vitamin C")
    assert row["provenance"] == "you_entered"
    rendered = json.dumps(detail).lower()
    for masquerade in ("scan_confirmed", "scanned", "verified", "manufacturer", "regulator", "official", "ai_verified"):
        assert masquerade not in rendered, masquerade

    # The table itself refuses any other provenance, including a scan-confirmed one.
    async with get_sessionmaker()() as session:
        for forged in ("scan_confirmed", "manufacturer", "open_food_facts", "ai_verified"):
            await session.execute(text("SAVEPOINT forged"))
            with pytest.raises(IntegrityError):
                await session.execute(
                    text("UPDATE supplement_label_components SET source = :source WHERE id = :id"),
                    {"source": forged, "id": uuid.UUID(fact["id"])},
                )
            await session.execute(text("ROLLBACK TO SAVEPOINT forged"))


# ---------------------------------------------------------------------------
# B. Cross-account isolation
# ---------------------------------------------------------------------------
async def test_b_another_account_cannot_read_change_or_delete_supplement_facts(
    app_client, db_clean, registered_supabase_user,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    a, b = auth(token_a), auth(token_b)
    item_a = await _supplement(app_client, a, "A's Magnesium")
    fact_a = await _fact(app_client, a, item_a, "Magnesium oxide", "250", "mg")
    item_b = await _supplement(app_client, b, "B's Magnesium")
    await _fact(app_client, b, item_b, "Magnesium oxide", "400", "mg")

    base = f"/api/v2/supplements/items/{item_a}"
    assert (await app_client.get(base, headers=b)).status_code == 404
    assert (await app_client.get(f"{base}/label-facts", headers=b)).status_code == 404
    assert (await app_client.post(f"{base}/label-facts", headers=b, json={"raw_name": "Zinc"})).status_code == 404
    assert (await app_client.patch(f"{base}/label-facts/{fact_a['id']}", headers=b, json={"amount": "1"})).status_code == 404
    assert (await app_client.post(f"{base}/label-facts/{fact_a['id']}/confirm", headers=b)).status_code == 404
    assert (await app_client.delete(f"{base}/label-facts/{fact_a['id']}", headers=b)).status_code == 404
    # B cannot reach A's fact through B's own item either.
    assert (await app_client.patch(
        f"/api/v2/supplements/items/{item_b}/label-facts/{fact_a['id']}", headers=b, json={"amount": "1"},
    )).status_code == 404

    unchanged = ok(await app_client.get(f"{base}/label-facts", headers=a))["label_facts"]
    assert [row["amount"] for row in unchanged] == ["250"]

    # Same component in both accounts: never an overlap across accounts.
    detail_a = await _detail(app_client, a, item_a)
    assert detail_a["overlaps"] == []
    summary_a = ok(await app_client.get("/api/v2/supplements/summary", headers=a))
    assert summary_a["overlaps"] == []
    for payload in (detail_a, summary_a):
        rendered = json.dumps(payload)
        assert item_b not in rendered and "B's Magnesium" not in rendered
        assert str(account_a) not in rendered and str(account_b) not in rendered


# ---------------------------------------------------------------------------
# C. Confirmed versus draft
# ---------------------------------------------------------------------------
async def test_c_photo_drafts_drive_nothing_until_the_customer_confirms_them(
    app_client, db_clean, registered_supabase_user,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    first = await _supplement(app_client, headers, "Bottle one")
    second = await _supplement(app_client, headers, "Bottle two")
    draft_id = await _photo_draft(account, first, "Magnesium oxide", amount=Decimal("250"), unit="mg")
    await _fact(app_client, headers, second, "Magnesium oxide", "400", "mg")

    detail = await _detail(app_client, headers, first)
    row = _component(detail, "Magnesium oxide")
    assert row["provenance"] == "read_from_photo_not_confirmed"
    assert row["confirmed"] is False and row["counts_for_overlap"] is False
    for block in ("nutrient", "form", "package_chemistry", "published_knowledge"):
        assert row[block] == {"status": "awaiting_confirmation"}, block
    assert detail["overlaps"] == [] and "confirmation" in detail["missing_information"]
    assert ok(await app_client.get("/api/v2/supplements/summary", headers=headers))["overlaps"] == []

    confirmed = ok(await app_client.post(
        f"/api/v2/supplements/items/{first}/label-facts/{draft_id}/confirm", headers=headers,
    ))
    # Confirmation by the customer is recorded as that, and nothing grander.
    assert confirmed["source"] == "photo_extracted" and confirmed["verification_state"] == "confirmed"
    detail = await _detail(app_client, headers, first)
    row = _component(detail, "Magnesium oxide")
    assert row["provenance"] == "read_from_photo_confirmed_by_you"
    assert row["form"] == {"status": "exact", "name": "magnesium oxide"}
    assert [group["component_key"] for group in detail["overlaps"]] == ["magnesium"]


async def test_c_an_unconfirmed_inventory_item_drives_nothing_either():
    """An AI-drafted item the customer never confirmed: its facts do not count."""

    class Fact:
        def __init__(self, raw_name):
            self.id = uuid.uuid4()
            self.raw_name = raw_name
            self.normalized_name = "magnesium"
            self.canonical_component_key = "magnesium"
            self.amount = Decimal("250")
            self.unit = "mg"
            self.serving_text = None
            self.source = "user_declared"
            self.verification_state = "confirmed"
            self.confidence = 1.0

    drafted = {"id": "a", "display_name": "A", "verification_state": "draft", "facts": [Fact("Magnesium oxide")]}
    other = {"id": "b", "display_name": "B", "verification_state": "confirmed", "facts": [Fact("Magnesium oxide")]}
    detail = build_detail(drafted, others=[drafted, other], knowledge={})
    assert detail["overlaps"] == []
    assert detail["components"][0]["form"] == {"status": "awaiting_confirmation"}
    assert build_utility([drafted, other])["overlaps"] == []


# ---------------------------------------------------------------------------
# D. Same nutrient, different form
# ---------------------------------------------------------------------------
async def test_d_form_knowledge_is_never_transferred_through_a_shared_nutrient_key(
    app_client, db_clean, registered_supabase_user,
):
    await _load_knowledge()
    await _publish_form("magnesium oxide")
    token, _account = await registered_supabase_user()
    headers = auth(token)
    oxide = await _supplement(app_client, headers, "Oxide")
    citrate = await _supplement(app_client, headers, "Citrate")
    await _fact(app_client, headers, oxide, "Magnesium oxide", "250", "mg")
    await _fact(app_client, headers, citrate, "Magnesium citrate", "250", "mg")

    oxide_row = _component(await _detail(app_client, headers, oxide), "Magnesium oxide")
    citrate_row = _component(await _detail(app_client, headers, citrate), "Magnesium citrate")
    assert oxide_row["published_knowledge"]["status"] == "published"
    assert citrate_row["published_knowledge"] == {"status": "not_enough_information"}
    assert citrate_row["form"] == {"status": "exact", "name": "magnesium citrate"}
    # Both share the nutrient key, which is exactly what must not carry knowledge.
    assert oxide_row["nutrient"]["key"] == citrate_row["nutrient"]["key"] == "magnesium"
    oxide_summary = oxide_row["published_knowledge"]["summary"]
    assert oxide_summary not in json.dumps(citrate_row)


def test_d_every_form_resolves_only_to_itself():
    for spelling, form in forms.EXACT_FORMS.items():
        resolution = forms.resolve_form(spelling, canonical_component_key=form.canonical_component_key)
        assert resolution.status is forms.FormStatus.EXACT, spelling
        expected = (form.canonical_component_key, form.compound_form) if form.knowledge_eligible else None
        assert resolution.knowledge_key == expected, spelling
        # The same printed name under a different nutrient key is refused, not re-keyed.
        other_key = "iron" if form.canonical_component_key != "iron" else "zinc"
        assert forms.resolve_form(spelling, canonical_component_key=other_key).status is forms.FormStatus.NOT_ENOUGH_INFORMATION


# ---------------------------------------------------------------------------
# E. Unknown form
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("printed", "key"), [
    ("Magnesium", "magnesium"), ("Elemental Iron", "iron"), ("Zinc", "zinc"), ("Calcium", "calcium"),
    ("Vitamin C", "vitamin c"), ("Vitamin B12", "vitamin b12"), ("Curcumin", "curcumin"), ("CoQ10", "coenzyme q10"),
])
def test_e_a_bare_nutrient_name_states_no_form_and_inherits_none(printed, key):
    resolution = forms.resolve_form(printed, canonical_component_key=key)
    assert resolution.status is forms.FormStatus.NOT_STATED
    assert resolution.form is None and resolution.knowledge_key is None
    chemistry = forms.package_chemistry(resolution)
    assert chemistry.percent_by_weight is None and chemistry.formula is None


@pytest.mark.parametrize(("printed", "key"), [
    ("Magnesium glycinate", "magnesium"),      # a form is printed, but not one we can pin exactly
    ("Epsom salt", "magnesium"),               # trivial name; not read as a hydrate
    ("Calcium citrate malate", "calcium"),     # a different compound from calcium citrate
    ("5 MTHF", "folate"),                      # racemic or L- is not stated
    ("Haldi extract", "curcumin"),
    ("Chelated magnesium", "chelated magnesium"),
])
def test_e_an_unrecognised_form_is_not_enough_information(printed, key):
    resolution = forms.resolve_form(printed, canonical_component_key=key)
    assert resolution.status is forms.FormStatus.NOT_ENOUGH_INFORMATION
    assert resolution.knowledge_key is None
    assert forms.package_chemistry(resolution).status is forms.ChemistryStatus.NOT_ENOUGH_INFORMATION


async def test_e_bare_magnesium_gets_nothing_even_when_every_magnesium_form_is_published(
    app_client, db_clean, registered_supabase_user,
):
    await _load_knowledge()
    for compound in COMPOUNDS:
        if compound.key == "magnesium" and compound.absorption is not None:
            await _publish_form(compound.form)
    token, _account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers, "Plain magnesium")
    await _fact(app_client, headers, item, "Magnesium", "300", "mg")
    row = _component(await _detail(app_client, headers, item), "Magnesium")
    assert row["form"] == {"status": "not_stated", "name": None}
    assert row["package_chemistry"]["status"] == "withheld"
    assert row["package_chemistry"]["withheld_reason"] == "form_not_stated"
    assert row["published_knowledge"] == {"status": "not_enough_information"}
    for compound in COMPOUNDS:
        if compound.key == "magnesium" and compound.absorption is not None:
            assert compound.absorption.summary not in json.dumps(row)


# ---------------------------------------------------------------------------
# F. Hydration ambiguity
# ---------------------------------------------------------------------------
WITHHELD_MINERALS = sorted(
    (spelling, form) for spelling, form in forms.EXACT_FORMS.items()
    if form.canonical_component_key in forms.MINERAL_KEYS and form.formula is None
)


@pytest.mark.parametrize(("spelling", "form"), WITHHELD_MINERALS, ids=[s for s, _ in WITHHELD_MINERALS])
def test_f_an_ambiguous_mineral_name_never_gets_a_silent_formula(spelling, form):
    chemistry = forms.package_chemistry(forms.resolve_form(spelling, canonical_component_key=form.canonical_component_key))
    assert chemistry.status is forms.ChemistryStatus.WITHHELD
    assert chemistry.withheld_reason is not None
    assert chemistry.percent_by_weight is None and chemistry.formula is None and chemistry.hydration is None


def test_f_the_names_indian_labels_usually_print_are_withheld_for_hydration():
    for spelling in ("ferrous sulphate", "ferrous sulfate", "zinc sulphate", "calcium citrate",
                     "calcium lactate", "magnesium chloride", "magnesium sulphate", "ferrous gluconate", "zinc gluconate"):
        form = forms.EXACT_FORMS[spelling]
        assert form.withheld is forms.WithheldReason.HYDRATION_NOT_STATED, spelling
    assert forms.EXACT_FORMS["magnesium citrate"].withheld is forms.WithheldReason.SALT_FORM_NOT_STATED
    assert forms.EXACT_FORMS["dried ferrous sulphate"].withheld is forms.WithheldReason.COMPOSITION_VARIES


async def test_f_no_hydration_figure_reaches_the_customer_for_an_ambiguous_label(
    app_client, db_clean, registered_supabase_user,
):
    token, _account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers, "Iron")
    await _fact(app_client, headers, item, "Ferrous Sulphate", "200", "mg")
    detail = await _detail(app_client, headers, item)
    row = _component(detail, "Ferrous Sulphate")
    assert row["form"] == {"status": "exact", "name": "ferrous sulfate"}
    assert row["package_chemistry"]["status"] == "withheld"
    assert row["package_chemistry"]["withheld_reason"] == "hydration_not_stated"
    rendered = json.dumps(detail)
    for figure in ("36.8", "20.1", "30.0", "32.9", "FeSO4"):
        assert figure not in rendered, figure


# ---------------------------------------------------------------------------
# G. Arithmetic
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("printed", "key", "expected", "formula"), [
    ("Magnesium Oxide", "magnesium", "60.3", "MgO"),
    ("Zinc oxide", "zinc", "80.3", "ZnO"),
    ("Calcium carbonate", "calcium", "40.0", "CaCO3"),
    ("Ferrous fumarate", "iron", "32.9", "FeC4H2O4"),
    ("Ferrous sulphate heptahydrate", "iron", "20.1", "FeSO4H14O7"),
    ("Ferrous sulfate anhydrous", "iron", "36.8", "FeSO4"),
    ("Magnesium chloride hexahydrate", "magnesium", "12.0", "MgCl2H12O6"),
    ("Magnesium sulphate heptahydrate", "magnesium", "9.9", "MgSO4H14O7"),
    ("Zinc sulphate monohydrate", "zinc", "36.4", "ZnSO4H2O"),
    ("Zinc sulfate heptahydrate", "zinc", "22.7", "ZnSO4H14O7"),
    ("Calcium citrate tetrahydrate", "calcium", "21.1", "Ca3C12H18O18"),
    ("Calcium citrate anhydrous", "calcium", "24.1", "Ca3C12H10O14"),
    ("Calcium lactate pentahydrate", "calcium", "13.0", "CaC6H20O11"),
    ("Ferrous gluconate dihydrate", "iron", "11.6", "FeC12H26O16"),
    ("Trimagnesium dicitrate anhydrous", "magnesium", "16.2", "Mg3C12H10O14"),
])
def test_g_an_exact_form_gives_deterministic_package_chemistry(printed, key, expected, formula):
    chemistry = forms.package_chemistry(forms.resolve_form(printed, canonical_component_key=key))
    assert chemistry.status is forms.ChemistryStatus.CALCULATED
    assert chemistry.formula == formula
    assert chemistry.percent_by_weight == Decimal(expected)
    form = forms.EXACT_FORMS[forms.normalize_component(printed)]
    assert chemistry.percent_by_weight == elemental_percent(form.formula, form.element, form.element_atoms)


def test_g_hydrate_figures_agree_with_the_knowledge_base_notes():
    """Two independent statements of the same arithmetic must agree."""
    notes = {compound.form: compound.hydration or "" for compound in COMPOUNDS}
    pairs = {
        "ferrous sulphate heptahydrate": ("ferrous sulfate", "20.1"),
        "magnesium chloride hexahydrate": ("magnesium chloride", "12.0"),
        "magnesium sulphate heptahydrate": ("magnesium sulfate", "9.9"),
        "zinc sulphate monohydrate": ("zinc sulfate", "36.4"),
        "zinc sulphate heptahydrate": ("zinc sulfate", "22.7"),
        "calcium lactate pentahydrate": ("calcium lactate", "13.0"),
        "ferrous gluconate dihydrate": ("ferrous gluconate", "11.6"),
        "calcium citrate anhydrous": ("calcium citrate", "24.1"),
    }
    for spelling, (compound_form, figure) in pairs.items():
        form = forms.EXACT_FORMS[spelling]
        assert str(elemental_percent(form.formula, form.element, form.element_atoms)) == figure
        assert figure in notes[compound_form], (spelling, notes[compound_form])


async def test_g_chemistry_is_never_combined_with_the_printed_amount(
    app_client, db_clean, registered_supabase_user,
):
    token, _account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers, "Mag")
    await _fact(app_client, headers, item, "Magnesium oxide", "500", "mg", "2 capsules daily")
    detail = await _detail(app_client, headers, item)
    row = _component(detail, "Magnesium oxide")
    assert row["package_chemistry"]["percent_by_weight"] == "60.3"
    assert row["printed"] == {"name": "Magnesium oxide", "amount": "500", "unit": "mg", "serving_text": "2 capsules daily"}
    rendered = json.dumps(detail).lower()
    # 500 mg x 60.3% = 301.5 mg; no product of the two appears anywhere.
    for derived in ("301.5", "301", "302", "603"):
        assert not _mentions_number(detail, derived), derived
    keys = _walk_keys(detail, set())
    for forbidden in ("absorbed", "absorption_amount", "intake", "daily", "dose", "dosage", "total", "sum", "elemental_amount"):
        assert not any(forbidden in key for key in keys), forbidden
    for phrase in ADVICE_PHRASES:
        assert phrase not in rendered, phrase


# ---------------------------------------------------------------------------
# H. Unverified knowledge
# ---------------------------------------------------------------------------
async def test_h_the_loaded_knowledge_base_is_dormant_for_every_form(db_clean):
    await _load_knowledge()
    pairs = [(compound.key, compound.form) for compound in COMPOUNDS]
    async with get_sessionmaker()() as session:
        decided = await read_form_knowledge(session, pairs)
        rows = (await session.execute(select(SupplementComponentKnowledge))).scalars().all()
        claims = (await session.execute(select(EvidenceClaim).where(
            EvidenceClaim.subject_type == "supplement_component",
        ))).scalars().all()
    assert len(decided) == len(COMPOUNDS)
    assert {row.status for row in decided.values()} == {KnowledgeStatus.NOT_ENOUGH_INFORMATION}
    assert {row.verification for row in rows} == {Verification.UNVERIFIED.value}
    assert {claim.review_status for claim in claims} == {ReviewStatus.DRAFT.value}
    # Every loaded draft carries its binding, so review has something exact to approve.
    assert all(BINDING_KEY in (claim.structured_value or {}) for claim in claims)


async def test_h_a_high_confidence_rating_is_not_verification(db_clean):
    await _load_knowledge()
    d3 = next(c for c in COMPOUNDS if c.form.startswith("vitamin D3"))
    assert str(d3.absorption.confidence) == "high"
    decided = await _knowledge("vitamin d", d3.form)
    assert decided.status is KnowledgeStatus.NOT_ENOUGH_INFORMATION
    assert decided.withheld_because is WithheldBecause.ROW_UNVERIFIED


async def test_h_a_published_claim_over_an_unverified_row_is_withheld(db_clean):
    await _load_knowledge()
    await _publish_form("magnesium oxide", confirm_row=False)
    decided = await _knowledge("magnesium", "magnesium oxide")
    assert decided.status is KnowledgeStatus.NOT_ENOUGH_INFORMATION
    assert decided.withheld_because is WithheldBecause.ROW_UNVERIFIED


# ---------------------------------------------------------------------------
# I. Draft evidence
# ---------------------------------------------------------------------------
async def test_i_a_confirmed_row_over_a_draft_claim_is_withheld(db_clean, caplog):
    await _load_knowledge()
    await _publish_form("magnesium oxide", approve=False, publish=False)
    with caplog.at_level(logging.WARNING, logger="app.domains.supplements.knowledge_reader"):
        decided = await _knowledge("magnesium", "magnesium oxide")
    assert decided.status is KnowledgeStatus.NOT_ENOUGH_INFORMATION
    assert decided.withheld_because is WithheldBecause.CLAIM_NOT_PUBLISHED
    assert any(getattr(record, "reason", None) == "claim_not_published" for record in caplog.records)


async def test_i_an_approved_but_unpublished_claim_is_withheld(db_clean):
    await _load_knowledge()
    await _publish_form("magnesium oxide", publish=False)
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(SupplementComponentKnowledge).where(
            SupplementComponentKnowledge.compound_form == "magnesium oxide",
        ))).scalar_one()
        assert (await session.get(EvidenceClaim, row.evidence_claim_id)).review_status == ReviewStatus.APPROVED.value
    decided = await _knowledge("magnesium", "magnesium oxide")
    assert decided.withheld_because is WithheldBecause.CLAIM_NOT_PUBLISHED


# ---------------------------------------------------------------------------
# J. Published reviewed knowledge
# ---------------------------------------------------------------------------
async def test_j_only_fully_published_knowledge_reaches_the_customer_with_its_source(
    app_client, db_clean, registered_supabase_user,
):
    await _load_knowledge()
    claim_id = await _publish_form("magnesium oxide")
    token, _account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers, "Oxide")
    await _fact(app_client, headers, item, "Magnesium oxide", "250", "mg")
    detail = await _detail(app_client, headers, item)
    knowledge = _component(detail, "Magnesium oxide")["published_knowledge"]
    oxide = next(c for c in COMPOUNDS if c.form == "magnesium oxide")
    assert knowledge["status"] == "published"
    assert knowledge["summary"] == oxide.absorption.summary
    assert knowledge["value"] == oxide.absorption.value_text
    assert knowledge["unit"] == oxide.absorption.unit
    assert knowledge["disagreement"] == oxide.absorption.disagreement
    assert knowledge["source"]["url"] == oxide.absorption.source_url
    assert knowledge["source"]["name"] and knowledge["source"]["publisher"]
    rendered = json.dumps(detail)
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(SupplementComponentKnowledge).where(
            SupplementComponentKnowledge.compound_form == "magnesium oxide",
        ))).scalar_one()
    for internal in (str(claim_id), str(row.id), "reviewer", "founder", "publication_verification", BINDING_KEY):
        assert internal not in rendered, internal


# ---------------------------------------------------------------------------
# K. Missing source
# ---------------------------------------------------------------------------
async def test_k_a_claim_with_no_source_link_is_withheld(db_clean):
    await _load_knowledge()
    claim_id = await _publish_form("magnesium oxide")
    async with get_sessionmaker()() as session:
        await session.execute(delete(EvidenceClaimSource).where(EvidenceClaimSource.claim_id == claim_id))
        await session.commit()
    decided = await _knowledge("magnesium", "magnesium oxide")
    assert decided.withheld_because is WithheldBecause.NO_PUBLIC_SOURCE


async def test_k_an_unclassified_source_is_withheld(db_clean):
    """The authoring tool files new sources as ``other``; that cannot carry a public claim."""
    await _load_knowledge()
    await _publish_form("magnesium oxide", source_type=SourceType.OTHER.value)
    decided = await _knowledge("magnesium", "magnesium oxide")
    assert decided.withheld_because is WithheldBecause.NO_PUBLIC_SOURCE


async def test_k_a_not_enough_information_entry_can_never_carry_a_figure(db_clean):
    await _load_knowledge()
    decided = await _knowledge("magnesium", "magnesium bisglycinate")
    assert decided.status is KnowledgeStatus.NOT_ENOUGH_INFORMATION
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(SupplementComponentKnowledge).where(
            SupplementComponentKnowledge.compound_form == "magnesium bisglycinate",
        ))).scalar_one()
        row.verification = Verification.CONFIRMED.value
        await session.commit()
    decided = await _knowledge("magnesium", "magnesium bisglycinate")
    assert decided.withheld_because is WithheldBecause.ROW_HAS_NO_FIGURE


# ---------------------------------------------------------------------------
# L. Bad or unopenable source
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_url", ["https://", "https://?x=1", "ftp://example.org/paper", "javascript:alert(1)", "https://exa mple.org/x"])
async def test_l_an_unopenable_source_url_is_withheld(db_clean, bad_url):
    await _load_knowledge()
    claim_id = await _publish_form("magnesium oxide")
    async with get_sessionmaker()() as session:
        for _link, source in (await session.execute(
            select(EvidenceClaimSource, EvidenceSource)
            .join(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id)
            .where(EvidenceClaimSource.claim_id == claim_id)
        )).all():
            source.canonical_url = bad_url
        await session.commit()
    assert (await _knowledge("magnesium", "magnesium oxide")).status is KnowledgeStatus.NOT_ENOUGH_INFORMATION


async def test_l_a_row_url_that_is_not_the_reviewed_source_is_withheld(db_clean):
    await _load_knowledge()
    await _publish_form("magnesium oxide")
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(SupplementComponentKnowledge).where(
            SupplementComponentKnowledge.compound_form == "magnesium oxide",
        ))).scalar_one()
        row.source_url = "https://"
        await session.commit()
    decided = await _knowledge("magnesium", "magnesium oxide")
    assert decided.withheld_because is WithheldBecause.ROW_SOURCE_NOT_OPENABLE


# ---------------------------------------------------------------------------
# M. Knowledge row / evidence mismatch
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("field", "value"), [
    ("absorption_value", "about 40"),
    ("absorption_summary", "Absorbed as well as any other salt."),
    ("absorption_unit", "% of something else"),
    ("disagreement", None),
    ("confidence", "high"),
])
async def test_m_a_row_edited_after_review_is_withheld(db_clean, caplog, field, value):
    await _load_knowledge()
    await _publish_form("magnesium oxide")
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(SupplementComponentKnowledge).where(
            SupplementComponentKnowledge.compound_form == "magnesium oxide",
        ))).scalar_one()
        setattr(row, field, value)
        await session.commit()
    with caplog.at_level(logging.WARNING, logger="app.domains.supplements.knowledge_reader"):
        decided = await _knowledge("magnesium", "magnesium oxide")
    assert decided.status is KnowledgeStatus.NOT_ENOUGH_INFORMATION
    assert decided.withheld_because is WithheldBecause.BINDING_MISMATCH
    assert any(getattr(record, "reason", None) == "binding_mismatch" for record in caplog.records)


async def test_m_a_published_claim_for_another_form_is_never_accepted(db_clean):
    """Point citrate's row at oxide's fully published claim: refused, not re-labelled."""
    await _load_knowledge()
    oxide_claim = await _publish_form("magnesium oxide")
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(SupplementComponentKnowledge).where(
            SupplementComponentKnowledge.compound_form == "magnesium citrate",
        ))).scalar_one()
        row.evidence_claim_id = oxide_claim
        row.verification = Verification.CONFIRMED.value
        await session.commit()
    decided = await _knowledge("magnesium", "magnesium citrate")
    assert decided.withheld_because is WithheldBecause.CLAIM_SUBJECT_MISMATCH


async def test_m_a_published_claim_without_its_binding_is_withheld(db_clean):
    await _load_knowledge()
    claim_id = await _publish_form("magnesium oxide")
    async with get_sessionmaker()() as session:
        claim = await session.get(EvidenceClaim, claim_id)
        value = dict(claim.structured_value)
        value.pop(BINDING_KEY)
        claim.structured_value = value
        await session.commit()
    assert (await _knowledge("magnesium", "magnesium oxide")).withheld_because is WithheldBecause.BINDING_MISSING


async def test_m_reviewed_claim_text_must_contain_the_sentence_shown(db_clean):
    await _load_knowledge()
    claim_id = await _publish_form("magnesium oxide")
    async with get_sessionmaker()() as session:
        claim = await session.get(EvidenceClaim, claim_id)
        claim.summary = "magnesium oxide is 60.3% magnesium by weight (elemental)."
        await session.commit()
    assert (await _knowledge("magnesium", "magnesium oxide")).withheld_because is WithheldBecause.CLAIM_TEXT_MISMATCH


async def test_m_a_disputed_row_is_withheld_even_over_a_published_claim(db_clean):
    await _load_knowledge()
    await _publish_form("magnesium oxide")
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(SupplementComponentKnowledge).where(
            SupplementComponentKnowledge.compound_form == "magnesium oxide",
        ))).scalar_one()
        row.verification = Verification.DISPUTED.value
        await session.commit()
    assert (await _knowledge("magnesium", "magnesium oxide")).withheld_because is WithheldBecause.ROW_DISPUTED


async def test_m_editing_a_published_claim_withdraws_it_until_the_new_version_is_published(db_clean):
    await _load_knowledge()
    claim_id = await _publish_form("magnesium oxide")
    async with get_sessionmaker()() as session:
        claim = await session.get(EvidenceClaim, claim_id)
        await evidence_authoring.edit(session, claim_id, evidence_authoring.EntryInput(
            subject_type=claim.subject_type, subject_key=claim.subject_key, claim=claim.summary,
            source_name="Edited source", source_url="https://example.org/edited", evidence_tier=claim.evidence_tier,
            domain=claim.domain,
        ), author="editor")
        await session.commit()
    assert (await _knowledge("magnesium", "magnesium oxide")).status is KnowledgeStatus.NOT_ENOUGH_INFORMATION


async def test_m_the_loader_never_rebinds_a_draft_under_recorded_verification(db_clean):
    await _load_knowledge()
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(SupplementComponentKnowledge).where(
            SupplementComponentKnowledge.compound_form == "magnesium oxide",
        ))).scalar_one()
        claim = await session.get(EvidenceClaim, row.evidence_claim_id)
        await evidence_authoring.record_publication_verification(
            session, claim.id, verification=VERIFIED, actor="founder",
        )
        value = dict(claim.structured_value)
        binding = dict(value[BINDING_KEY])
        binding["absorption_value"] = "a figure somebody verified"
        value[BINDING_KEY] = binding
        claim.structured_value = value
        await session.commit()
    async with get_sessionmaker()() as session:
        summary = await load(session)
        await session.commit()
    assert summary["drafts_bound"] == 0
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(SupplementComponentKnowledge).where(
            SupplementComponentKnowledge.compound_form == "magnesium oxide",
        ))).scalar_one()
        claim = await session.get(EvidenceClaim, row.evidence_claim_id)
        assert claim.structured_value[BINDING_KEY]["absorption_value"] == "a figure somebody verified"


# ---------------------------------------------------------------------------
# N, O, P. Overlap, no totals, same-item duplicates
# ---------------------------------------------------------------------------
async def test_n_o_p_overlap_is_factual_per_product_and_never_a_total(
    app_client, db_clean, registered_supabase_user,
):
    token, _account = await registered_supabase_user()
    headers = auth(token)
    one = await _supplement(app_client, headers, "One bottle")
    two = await _supplement(app_client, headers, "Two bottle")
    await _fact(app_client, headers, one, "Vitamin C", "500", "mg", "Per tablet")
    await _fact(app_client, headers, one, "Ascorbic acid", "250", "mg", "Per tablet")   # same item, alias
    await _fact(app_client, headers, two, "Vitamin C", "250", "mg", "Per tablet")

    detail = await _detail(app_client, headers, one)
    assert len(detail["overlaps"]) == 1
    group = detail["overlaps"][0]
    assert group["component_key"] == "vitamin c"
    assert group["product_count"] == 2, "two rows on one bottle are still one product"
    assert group["printed_names_here"] == ["Ascorbic acid", "Vitamin C"]
    assert group["other_products"] == [{"inventory_item_id": two, "product_name": "Two bottle", "printed_names": ["Vitamin C"]}]
    summary = ok(await app_client.get("/api/v2/supplements/summary", headers=headers))
    assert [row["product_count"] for row in summary["overlaps"]] == [2]

    for payload in (detail, summary):
        rendered = json.dumps(payload).lower()
        assert not _mentions_number(payload, "750") and not _mentions_number(payload, "1000"), "amounts are never added"
        for phrase in ADVICE_PHRASES:
            assert phrase not in rendered, phrase
    keys = _walk_keys(detail, set())
    assert not any(word in key for key in keys for word in ("total", "sum", "daily", "intake", "combined"))
    # Overlap carries names, never amounts.
    assert "amount" not in json.dumps(detail["overlaps"])


# ---------------------------------------------------------------------------
# Q. Printed serving text
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("serving", ["2 capsules daily", "Take 1 tablet after food", "1 scoop (5 g)", "As directed by physician"])
async def test_q_serving_text_is_returned_exactly_as_printed(app_client, db_clean, registered_supabase_user, serving):
    token, _account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers, "Bottle")
    await _fact(app_client, headers, item, "Zinc", "10", "mg", serving)
    detail = await _detail(app_client, headers, item)
    row = _component(detail, "Zinc")
    assert row["printed"]["serving_text"] == serving
    rendered = json.dumps(detail)
    assert rendered.count(serving) == 1, "the printed text appears once, as printed, and is not restated"
    lowered = rendered.lower()
    for rewrite in ("you should take", "your dose", "your dosage", "take 2 capsules", "recommended"):
        assert rewrite not in lowered.replace(serving.lower(), ""), rewrite


# ---------------------------------------------------------------------------
# R. Professional boundary
# ---------------------------------------------------------------------------
BOUNDARY_QUESTIONS = {
    "medicine interaction": ["Can I take this with my blood pressure tablets?", "Does this interact with metformin?"],
    "pregnancy": ["Is this ok while pregnant?", "Can I use this in my first trimester?"],
    "breastfeeding": ["Is this fine while breastfeeding?"],
    "child use": ["Is this ok for kids?", "Can children take this?", "Can my son have this?"],
    "disease or condition": ["Does this help with a disease?", "Is this ok with my kidney condition?", "Will this help my diabetes?"],
    "deficiency": ["Am I deficient in iron?", "Do I have low vitamin D?", "Is my B12 low?"],
    "side effects": ["What are the side effects?"],
    "adverse reaction": ["I felt sick after this", "I had a reaction to this", "I got a rash after taking this"],
    "dose": ["How much should I take?", "Is this dose too high?", "How many capsules a day?"],
    "overdose": ["What if I overdose?", "Can I take too much?"],
    "lab result": ["My lab report shows low ferritin", "My test results came back low"],
    "whether to start": ["Should I start this?", "Do I need this?", "Is it worth taking?"],
    "whether to stop": ["Should I stop this?", "Can I quit taking it?"],
    "recommendation": ["Which supplement should I buy?", "What is the best magnesium?"],
}
ORDINARY_QUESTIONS = ["Where should I store this bottle?", "When does this expire?", "What does the label say?"]


@pytest.mark.parametrize(
    ("category", "question"),
    [(category, question) for category, items in BOUNDARY_QUESTIONS.items() for question in items],
)
async def test_r_health_like_questions_route_out_and_are_never_answered(
    app_client, db_clean, registered_supabase_user, category, question,
):
    token, _account = await registered_supabase_user()
    response = ok(await app_client.post(
        "/api/v2/supplements/professional-boundary", headers=auth(token), json={"question": question},
    ))
    assert response["boundary"] is True, (category, question)
    rendered = json.dumps(response).lower()
    for phrase in ADVICE_PHRASES + ("mg", "mcg", "iu"):
        assert f" {phrase} " not in f" {rendered} ", phrase
    assert question.lower() not in rendered, "the person's words are never echoed back"


@pytest.mark.parametrize("question", ORDINARY_QUESTIONS)
async def test_r_ordinary_questions_get_the_tracking_statement_not_an_answer(
    app_client, db_clean, registered_supabase_user, question,
):
    token, _account = await registered_supabase_user()
    response = ok(await app_client.post(
        "/api/v2/supplements/professional-boundary", headers=auth(token), json={"question": question},
    ))
    assert response == {"boundary": False, "message": supplement_boundary.NO_BOUNDARY_MESSAGE}


def test_r_the_hard_handoff_gate_is_called_first_and_never_overridden():
    decided = supplement_boundary.evaluate("Is this ok while pregnant?")
    assert decided.rule == "hard_handoff:pregnancy"
    # The gate's fail-closed reading of a bare taking-frame stands in supplement context too.
    assert requires_handoff("I take this after breakfast.") is True
    assert supplement_boundary.evaluate("I take this after breakfast.").boundary is True


def test_r_the_supplement_patterns_close_gaps_the_existing_authorities_demonstrably_leave():
    """Why a supplement layer exists at all: each of these slips past both older checks."""
    gaps = [
        "Is this ok for kids?", "Does this help with a disease?", "Do I have low vitamin D?",
        "I felt sick after this", "How many capsules a day?", "What if I overdose?",
        "My lab report shows low ferritin", "Should I start this?", "Should I stop this?",
        "Do I need this?", "Which supplement should I buy?",
    ]
    for question in gaps:
        assert requires_handoff(question) is False, question
        assert needs_professional(question) is False, question
        assert supplement_boundary.requires_boundary(question) is True, question


async def test_r_a_health_like_note_on_the_item_shows_the_boundary_on_the_detail(
    app_client, db_clean, registered_supabase_user,
):
    token, _account = await registered_supabase_user()
    headers = auth(token)
    flagged = await _supplement(app_client, headers, "Flagged", purpose="For my thyroid")
    plain = await _supplement(app_client, headers, "Plain", purpose="Better sleep")
    boundary = (await _detail(app_client, headers, flagged))["professional_boundary"]
    assert boundary["boundary"] is True and boundary["message"]
    assert "thyroid" not in json.dumps(boundary).lower()
    assert (await _detail(app_client, headers, plain))["professional_boundary"] == {
        "boundary": False, "reason": None, "message": None,
    }


# ---------------------------------------------------------------------------
# S. Expiry
# ---------------------------------------------------------------------------
async def test_s_expiry_states_are_factual(app_client, db_clean, registered_supabase_user):
    token, _account = await registered_supabase_user()
    headers = auth(token)
    today = date.today()
    cases = {
        "past": (today - timedelta(days=1)).isoformat(),
        "coming_up": (today + timedelta(days=30)).isoformat(),
        "current": (today + timedelta(days=400)).isoformat(),
        "unknown": None,
    }
    for state, expiry in cases.items():
        item = await _supplement(app_client, headers, f"Expiry {state}", expiry=expiry)
        detail = await _detail(app_client, headers, item)
        assert detail["expiry"]["state"] == state
        assert detail["expiry"]["date"] == expiry
        assert ("expiry_date" in detail["missing_information"]) is (expiry is None)
        rendered = json.dumps(detail).lower()
        for word in ("toxic", "unsafe", "ineffective", "spoiled", "dispose", "throw away", "replace it"):
            assert word not in rendered, word
    summary = ok(await app_client.get("/api/v2/supplements/summary", headers=headers))
    messages = {flag["message"] for row in summary["supplements"] for flag in row["flags"]}
    assert messages == {"Past the date you recorded.", "Date coming up.", "Expiry date not added."}


# ---------------------------------------------------------------------------
# T. Privacy export
# ---------------------------------------------------------------------------
async def test_t_export_carries_label_facts_and_provenance_without_internal_ids(
    app_client, db_clean, registered_supabase_user,
):
    await _load_knowledge()
    claim_id = await _publish_form("magnesium oxide")
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    a, b = auth(token_a), auth(token_b)
    item_a = await _supplement(app_client, a, "A oxide")
    fact_a = await _fact(app_client, a, item_a, "Magnesium oxide", "250", "mg", "1 tablet")
    draft_a = await _photo_draft(account_a, item_a, "Zinc oxide")
    item_b = await _supplement(app_client, b, "B oxide")
    fact_b = await _fact(app_client, b, item_b, "Magnesium oxide", "400", "mg")

    exported = ok(await app_client.get("/api/v2/privacy/export", headers=a))
    rows = exported["domains"]["routines"]["supplement_label_components"]
    by_id = {row["id"]: row for row in rows}
    assert set(by_id) == {fact_a["id"], str(draft_a)}
    manual = by_id[fact_a["id"]]
    assert manual["raw_name"] == "Magnesium oxide" and manual["unit"] == "mg" and manual["serving_text"] == "1 tablet"
    assert manual["source"] == "user_declared" and manual["verification_state"] == "confirmed"
    assert by_id[str(draft_a)]["source"] == "photo_extracted" and by_id[str(draft_a)]["verification_state"] == "draft"
    for row in rows:
        for internal in ("account_id", "source_ai_run_id", "model_version", "prompt_version", "client_mutation_id"):
            assert internal not in row, internal
    rendered = json.dumps(exported["domains"]["routines"]["supplement_label_components"])
    assert fact_b["id"] not in rendered and str(account_b) not in rendered
    # Global knowledge and its review metadata are not the person's data.
    assert str(claim_id) not in json.dumps(exported)
    assert "supplement_component_knowledge" not in json.dumps(list(exported["domains"]))


# ---------------------------------------------------------------------------
# U. Inventory item deletion
# ---------------------------------------------------------------------------
async def test_u_removing_an_item_takes_its_label_facts_out_of_every_surface(
    app_client, db_clean, registered_supabase_user,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    kept = await _supplement(app_client, headers, "Kept")
    removed = await _supplement(app_client, headers, "Removed")
    await _fact(app_client, headers, kept, "Zinc oxide", "10", "mg")
    removed_fact = await _fact(app_client, headers, removed, "Zinc oxide", "15", "mg")
    assert len((await _detail(app_client, headers, kept))["overlaps"]) == 1

    assert (await app_client.delete(f"/api/v2/inventory/items/{removed}", headers=headers)).status_code == 200
    assert (await app_client.get(f"/api/v2/supplements/items/{removed}", headers=headers)).status_code == 404
    assert (await app_client.get(f"/api/v2/supplements/items/{removed}/label-facts", headers=headers)).status_code == 404
    assert (await app_client.patch(
        f"/api/v2/supplements/items/{removed}/label-facts/{removed_fact['id']}", headers=headers, json={"amount": "1"},
    )).status_code == 404
    assert (await _detail(app_client, headers, kept))["overlaps"] == []
    summary = ok(await app_client.get("/api/v2/supplements/summary", headers=headers))
    assert [row["display_name"] for row in summary["supplements"]] == ["Kept"] and summary["overlaps"] == []

    # Removing the item row itself removes its facts with it: nothing is orphaned.
    async with get_sessionmaker()() as session:
        await session.execute(delete(InventoryItem).where(InventoryItem.id == uuid.UUID(removed)))
        await session.commit()
        left = (await session.execute(select(SupplementLabelComponent).where(
            SupplementLabelComponent.account_id == account,
        ))).scalars().all()
    assert [row.item_id for row in left] == [uuid.UUID(kept)]


async def test_u_label_facts_cascade_from_both_their_item_and_their_account(db_clean):
    async with get_sessionmaker()() as session:
        rules = dict((await session.execute(text(
            """
            SELECT kcu.column_name, rc.delete_rule
            FROM information_schema.referential_constraints rc
            JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = rc.constraint_name
            WHERE kcu.table_name = 'supplement_label_components'
              AND kcu.column_name IN ('item_id', 'account_id')
            """
        ))).all())
    assert rules == {"item_id": "CASCADE", "account_id": "CASCADE"}


# ---------------------------------------------------------------------------
# V. Account deletion
# ---------------------------------------------------------------------------
async def test_v_deleting_the_account_removes_its_supplement_facts_and_nobody_elses(
    app_client, db_clean, registered_supabase_user, monkeypatch,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    item_a = await _supplement(app_client, auth(token_a), "A")
    await _fact(app_client, auth(token_a), item_a, "Vitamin C", "500", "mg")
    await _photo_draft(account_a, item_a, "Zinc oxide")
    item_b = await _supplement(app_client, auth(token_b), "B")
    fact_b = await _fact(app_client, auth(token_b), item_b, "Vitamin C", "250", "mg")

    class _Admin:
        class auth:
            class admin:
                @staticmethod
                def delete_user(_uid):
                    return None

    monkeypatch.setattr(deletion_service, "get_supabase_admin", lambda: _Admin())
    assert (await app_client.delete("/api/v2/privacy/account", headers=auth(token_a))).status_code == 202
    async with get_sessionmaker()() as session:
        await deletion_service.drain_all(session)
        await session.commit()
    async with get_sessionmaker()() as session:
        remaining = (await session.execute(select(SupplementLabelComponent))).scalars().all()
    assert [str(row.id) for row in remaining] == [fact_b["id"]]
    assert all(row.account_id == account_b for row in remaining)


# ---------------------------------------------------------------------------
# W. Open Food Facts separation
# ---------------------------------------------------------------------------
async def test_w_open_food_facts_can_never_become_a_confirmed_supplement_label_fact(
    app_client, db_clean, off_clean, registered_supabase_user,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    barcode = "8901234567899"
    async with get_off_sessionmaker()() as off_session:
        off_session.add(OffProduct(
            barcode=barcode, product_name="OFF Magnesium Tablets", brands="OFF Brand",
            ingredients_text="Magnesium oxide 500 mg, Zinc oxide 10 mg", fetched_at=datetime.now(UTC),
        ))
        await off_session.commit()
    device = await app_client.post("/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"})
    assert device.status_code == 201
    # A product lookup reads Store A; it must write nothing into supplement tables.
    looked_up = await app_client.get(
        f"/api/v2/scan/lookup/{barcode}", headers={**headers, "X-Device-Token": device.json()["token"]},
    )
    assert looked_up.status_code == 200 and "OFF Magnesium Tablets" in looked_up.text
    item = await _supplement(app_client, headers, "OFF Magnesium Tablets")
    detail = await _detail(app_client, headers, item)
    assert detail["components"] == [] and "label_components" in detail["missing_information"]
    async with get_sessionmaker()() as session:
        assert (await session.execute(select(SupplementLabelComponent).where(
            SupplementLabelComponent.account_id == account,
        ))).scalars().all() == []
    rendered = json.dumps(detail)
    assert "Magnesium oxide 500 mg" not in rendered and "OFF Brand" not in rendered


def test_w_the_supplement_domain_never_imports_store_a():
    root = Path(__file__).resolve().parents[1] / "app" / "domains" / "supplements"
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("app.domains.off"), f"{path.name} imports {node.module}"
            if isinstance(node, ast.Import):
                assert not any(alias.name.startswith("app.domains.off") for alias in node.names), path.name
    from app.domains.off.models import OffBase
    assert SupplementLabelComponent.__table__.metadata is not OffBase.metadata
    assert SupplementComponentKnowledge.__table__.metadata is not OffBase.metadata


# ---------------------------------------------------------------------------
# X. Unknown knowledge
# ---------------------------------------------------------------------------
async def test_x_an_exact_form_with_no_knowledge_row_is_not_enough_information(
    app_client, db_clean, registered_supabase_user,
):
    token, _account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers, "Unloaded")
    await _fact(app_client, headers, item, "Ferrous fumarate", "100", "mg")
    row = _component(await _detail(app_client, headers, item), "Ferrous fumarate")
    assert row["form"] == {"status": "exact", "name": "ferrous fumarate"}
    assert row["published_knowledge"] == {"status": "not_enough_information"}
    assert (await _knowledge("iron", "ferrous fumarate")).withheld_because is WithheldBecause.NO_ROW


async def test_x_a_component_nobody_has_described_is_not_enough_information(
    app_client, db_clean, registered_supabase_user,
):
    token, _account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers, "Selenium")
    await _fact(app_client, headers, item, "Sodium selenite", "55", "mcg")
    row = _component(await _detail(app_client, headers, item), "Sodium selenite")
    assert row["nutrient"]["status"] == "not_identified"
    assert row["form"] == {"status": "not_enough_information", "name": None}
    assert row["package_chemistry"]["status"] == "not_enough_information"
    assert row["published_knowledge"] == {"status": "not_enough_information"}
    # Printed unit kept exactly as entered; never normalised or converted.
    assert row["printed"]["unit"] == "mcg"


@pytest.mark.parametrize("unit", ["µg", "mcg", "IU", "mg", "% RDA", "mg/tab"])
async def test_x_printed_units_are_preserved_and_never_converted(app_client, db_clean, registered_supabase_user, unit):
    token, _account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers, "Units")
    await _fact(app_client, headers, item, "Vitamin D3", "1000", unit)
    row = _component(await _detail(app_client, headers, item), "Vitamin D3")
    assert row["printed"]["amount"] == "1000" and row["printed"]["unit"] == unit
    assert row["package_chemistry"]["status"] == "not_applicable"


# ---------------------------------------------------------------------------
# Contract hygiene
# ---------------------------------------------------------------------------
async def test_the_detail_route_requires_a_registered_account(app_client, db_clean, fake_supabase_user):
    item = uuid.uuid4()
    assert (await app_client.get(f"/api/v2/supplements/items/{item}")).status_code == 401
    token, _uid = fake_supabase_user()
    response = await app_client.get(f"/api/v2/supplements/items/{item}", headers=auth(token))
    assert response.status_code == 403


async def test_the_detail_carries_no_internal_identifiers(app_client, db_clean, registered_supabase_user):
    token, account = await registered_supabase_user()
    headers = auth(token)
    item = await _supplement(app_client, headers, "Hygiene")
    await _photo_draft(account, item, "Zinc oxide")
    detail = await _detail(app_client, headers, item)
    keys = _walk_keys(detail, set())
    for internal in ("account_id", "source_ai_run_id", "ai_run_id", "model_version", "prompt_version",
                     "evidence_claim_id", "claim_id", "knowledge_row_id", "storage_key", "withheld_because",
                     "reviewed_by", "published_by", "client_mutation_id", "confidence"):
        assert internal not in keys, internal
    assert str(account) not in json.dumps(detail)


# ===========================================================================
# Correction round (independent review of PR #194)
# ===========================================================================
# ---------------------------------------------------------------------------
# D1. Only reviewed identity executes; stored keys are revalidated
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("label", "old_key"), [
    ("Triglyceride", "omega 3"),
    ("Turmeric extract", "curcumin"),
    ("Fish oil concentrate", "omega 3"),
    ("Haldi extract", "curcumin"),
    ("Turmeric with black pepper", "curcumin"),
    ("Ethyl ester", "omega 3"),
    ("rTG", "omega 3"),
    ("Epsom salt", "magnesium"),
    ("Oyster shell calcium", "calcium"),
    ("Magtein", "magnesium"),
])
def test_d1_unreviewed_knowledge_aliases_never_decide_identity(label, old_key):
    from app.domains.supplements.identity import component_identity
    from app.domains.supplements.names import normalize_component

    key, display = component_identity(label)
    assert key == normalize_component(label) and key != old_key
    assert display == label


def test_d1_the_executable_identity_map_is_exactly_the_reviewed_sources():
    from app.domains.supplements.identity import REVIEWED_IDENTITIES
    from app.domains.supplements.names import NUTRIENT_SPELLINGS, REVIEWED_EQUIVALENTS

    expected = {**NUTRIENT_SPELLINGS, **REVIEWED_EQUIVALENTS,
                **{spelling: form.canonical_component_key for spelling, form in forms.EXACT_FORMS.items()}}
    assert expected == REVIEWED_IDENTITIES


def test_d1_no_production_module_reads_the_knowledge_aliases():
    root = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "knowledge.py" and path.parent.name == "supplements":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "raw_aliases":
                offenders.append(str(path))
            if isinstance(node, ast.Attribute) and node.attr == "raw_aliases":
                offenders.append(str(path))
            if isinstance(node, ast.ImportFrom) and any(alias.name == "raw_aliases" for alias in node.names):
                offenders.append(str(path))
    assert offenders == []


async def test_d1_an_unreviewed_alias_never_overlaps_a_reviewed_identity(
    app_client, db_clean, registered_supabase_user,
):
    token, _account = await registered_supabase_user()
    headers = auth(token)
    pairs = [("Fish oil concentrate", "Omega 3"), ("Turmeric extract", "Curcumin"), ("Triglyceride", "Omega 3 fatty acids")]
    items = []
    for loose, reviewed in pairs:
        first = await _supplement(app_client, headers, f"Loose {loose}")
        second = await _supplement(app_client, headers, f"Reviewed {reviewed}")
        await _fact(app_client, headers, first, loose)
        await _fact(app_client, headers, second, reviewed)
        items.append(first)
    summary = ok(await app_client.get("/api/v2/supplements/summary", headers=headers))
    # "Omega 3" and "Omega 3 fatty acids" are both reviewed omega-3 spellings; nothing else groups.
    assert [group["component_key"] for group in summary["overlaps"]] == ["omega 3"]
    assert {row["product_name"] for row in summary["overlaps"][0]["items"]} == {
        "Reviewed Omega 3", "Reviewed Omega 3 fatty acids",
    }
    for item in items:
        assert (await _detail(app_client, headers, item))["overlaps"] == []

    # The deliberate VC-07 identity still holds.
    c1 = await _supplement(app_client, headers, "C one")
    c2 = await _supplement(app_client, headers, "C two")
    await _fact(app_client, headers, c1, "Vitamin C")
    await _fact(app_client, headers, c2, "Ascorbic acid")
    assert [group["component_key"] for group in (await _detail(app_client, headers, c1))["overlaps"]] == ["vitamin c"]


async def test_d1_legacy_rows_keyed_by_old_aliases_are_revalidated_not_trusted_and_not_repaired(
    app_client, db_clean, registered_supabase_user, caplog,
):
    token, account = await registered_supabase_user()
    headers = auth(token)
    legacy_item = await _supplement(app_client, headers, "Legacy")
    reviewed_item = await _supplement(app_client, headers, "Reviewed")
    await _fact(app_client, headers, reviewed_item, "Omega 3")
    await _fact(app_client, headers, reviewed_item, "Curcumin")
    # Rows exactly as the pre-correction code wrote them, through the old broad alias set.
    async with get_sessionmaker()() as session:
        legacy = [
            SupplementLabelComponent(
                account_id=account, item_id=uuid.UUID(legacy_item), raw_name=raw, normalized_name=key,
                canonical_component_key=key, source="user_declared", verification_state="confirmed", confidence=1.0,
            )
            for raw, key in (("Triglyceride", "omega 3"), ("Haldi extract", "curcumin"))
        ]
        session.add_all(legacy)
        await session.commit()
        legacy_ids = [row.id for row in legacy]

    with caplog.at_level(logging.WARNING, logger="app.domains.supplements.identity"):
        detail = await _detail(app_client, headers, legacy_item)
        summary = ok(await app_client.get("/api/v2/supplements/summary", headers=headers))
    assert detail["overlaps"] == [] and summary["overlaps"] == []
    for name in ("Triglyceride", "Haldi extract"):
        row = _component(detail, name)
        assert row["nutrient"] == {"status": "not_enough_information", "key": None, "display_name": None}
        assert row["form"]["status"] == "not_enough_information"
        assert row["published_knowledge"] == {"status": "not_enough_information"}
    assert any(getattr(record, "reason", None) == "stored_identity_disagrees_with_reviewed_authority"
               for record in caplog.records)
    # Nothing was rewritten.
    async with get_sessionmaker()() as session:
        stored = {row.raw_name: row.canonical_component_key for row in (await session.execute(
            select(SupplementLabelComponent).where(SupplementLabelComponent.id.in_(legacy_ids))
        )).scalars().all()}
    assert stored == {"Triglyceride": "omega 3", "Haldi extract": "curcumin"}


# ---------------------------------------------------------------------------
# D2. Exact forms are explicit; recognition is not knowledge eligibility
# ---------------------------------------------------------------------------
def test_d2_adding_a_compound_to_the_knowledge_file_adds_no_form():
    from app.domains.supplements.knowledge import Compound

    invented = Compound(
        key="magnesium", nutrient="Magnesium", form="magnesium unobtainate",
        aliases=("mg unobtainate",), formula="MgO", element="Mg",
    )
    table = forms.build_exact_forms((*COMPOUNDS, invented))
    assert set(table) == set(forms.EXACT_FORMS)
    assert "magnesium unobtainate" not in table and "mg unobtainate" not in table


@pytest.mark.parametrize("printed", [
    "liposomal vitamin C", "curcumin (plain extract)", "curcumin with piperine",
    "curcumin phospholipid complex", "ethyl ester (EE)", "triglyceride (rTG or natural TG)",
])
def test_d2_unreviewed_knowledge_form_names_are_not_enough_information(printed):
    from app.domains.supplements.identity import component_identity

    key, _display = component_identity(printed)
    resolution = forms.resolve_form(printed, canonical_component_key=key)
    assert resolution.status is forms.FormStatus.NOT_ENOUGH_INFORMATION
    assert resolution.knowledge_key is None


def test_d2_every_exact_form_spelling_is_written_out_in_the_module():
    source = ast.parse((Path(forms.__file__)).read_text())
    literal_keys: set[str] = set()
    for node in ast.walk(source):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id in {"_MINERAL_FORMS", "_OTHER_FORMS"} and isinstance(node.value, ast.Dict):
            literal_keys |= {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
    assert literal_keys and set(forms.EXACT_FORMS) == literal_keys


def test_d2_recognised_is_not_knowledge_eligible_where_the_name_does_not_fix_the_compound():
    for spelling in ("magnesium citrate", "magnesium bisglycinate", "ferrous bisglycinate",
                     "zinc bisglycinate", "magnesium malate", "dried ferrous sulphate", "carbonyl iron"):
        form = forms.EXACT_FORMS[spelling]
        resolution = forms.resolve_form(spelling, canonical_component_key=form.canonical_component_key)
        assert resolution.status is forms.FormStatus.EXACT and resolution.knowledge_key is None, spelling


async def test_d2_published_knowledge_needs_an_eligible_exact_form_not_just_the_same_name(
    app_client, db_clean, registered_supabase_user,
):
    """A published "magnesium citrate" entry reaches no label at all.

    "Magnesium citrate" does not fix the salt; "Trimagnesium dicitrate
    anhydrous" fixes it, but is narrower than the generic subject the evidence
    was reviewed for. Both are recognised; neither inherits the research.
    """
    await _load_knowledge()
    await _publish_form("magnesium citrate")
    token, _account = await registered_supabase_user()
    headers = auth(token)
    ambiguous = await _supplement(app_client, headers, "Ambiguous salt")
    exact = await _supplement(app_client, headers, "Exact salt")
    await _fact(app_client, headers, ambiguous, "Magnesium citrate")
    await _fact(app_client, headers, exact, "Trimagnesium dicitrate anhydrous")
    ambiguous_row = _component(await _detail(app_client, headers, ambiguous), "Magnesium citrate")
    exact_row = _component(await _detail(app_client, headers, exact), "Trimagnesium dicitrate anhydrous")
    assert ambiguous_row["form"] == {"status": "exact", "name": "magnesium citrate"}
    assert ambiguous_row["published_knowledge"] == {"status": "not_enough_information"}
    assert exact_row["form"] == {"status": "exact", "name": "magnesium citrate"}
    assert exact_row["package_chemistry"]["status"] == "calculated"
    assert exact_row["package_chemistry"]["formula"] == "Mg3C12H10O14"
    assert exact_row["published_knowledge"] == {"status": "not_enough_information"}


# ---------------------------------------------------------------------------
# P1. Generic evidence never crosses into a more specific printed form
# ---------------------------------------------------------------------------
HYDRATE_SPELLINGS = sorted(spelling for spelling, form in forms.EXACT_FORMS.items() if form.hydration)


def test_p1_every_hydrate_spelling_is_recognised_calculated_and_never_a_knowledge_key():
    assert len(HYDRATE_SPELLINGS) == 24
    for spelling in HYDRATE_SPELLINGS:
        form = forms.EXACT_FORMS[spelling]
        resolution = forms.resolve_form(spelling, canonical_component_key=form.canonical_component_key)
        assert resolution.status is forms.FormStatus.EXACT, spelling
        assert forms.package_chemistry(resolution).status is forms.ChemistryStatus.CALCULATED, spelling
        assert resolution.knowledge_key is None, spelling
    for spelling in ("trimagnesium dicitrate", "trimagnesium dicitrate anhydrous"):
        assert forms.resolve_form(spelling, canonical_component_key="magnesium").knowledge_key is None, spelling


def test_p1_an_eligible_mineral_spelling_is_its_subjects_own_name():
    for spelling, form in forms.EXACT_FORMS.items():
        if form.knowledge_eligible and form.canonical_component_key in forms.MINERAL_KEYS:
            subject = forms.normalize_component(form.compound_form)
            assert spelling in {subject, subject.replace("sulfate", "sulphate")}, spelling
            assert form.hydration is None, spelling


@pytest.mark.parametrize("spelling", [
    "ferrous sulfate heptahydrate", "ferrous sulphate monohydrate", "magnesium chloride hexahydrate",
    "calcium citrate tetrahydrate", "ferrous gluconate dihydrate", "zinc sulfate anhydrous",
    "trimagnesium dicitrate", "trimagnesium dicitrate anhydrous",
])
def test_p1_the_table_refuses_a_narrower_form_made_eligible_for_a_generic_subject(spelling):
    table = {**forms.EXACT_FORMS, spelling: replace(forms.EXACT_FORMS[spelling], knowledge_eligible=True)}
    with pytest.raises(ValueError, match="more specific than the knowledge subject"):
        forms.build_exact_forms(COMPOUNDS, table=table)


@pytest.mark.parametrize(("first", "second"), [
    ("vitamin d3", "cholecalciferol"), ("mecobalamin", "methylcobalamin"), ("cobamamide", "adenosylcobalamin"),
    ("folic acid", "pteroylglutamic acid"), ("ferrous sulphate", "ferrous sulfate"),
    ("magnesium sulphate", "magnesium sulfate"), ("zinc sulphate", "zinc sulfate"),
])
def test_p1_nomenclature_synonyms_of_one_subject_stay_eligible(first, second):
    one, other = forms.EXACT_FORMS[first], forms.EXACT_FORMS[second]
    keys = {
        forms.resolve_form(spelling, canonical_component_key=form.canonical_component_key).knowledge_key
        for spelling, form in ((first, one), (second, other))
    }
    assert len(keys) == 1 and None not in keys


async def _published_generic_and_hydrate(app_client, registered_supabase_user, subject, generic, hydrate):
    await _load_knowledge()
    await _publish_form(subject)
    token, _account = await registered_supabase_user()
    headers = auth(token)
    generic_item = await _supplement(app_client, headers, "Generic label")
    hydrate_item = await _supplement(app_client, headers, "Hydrate label")
    await _fact(app_client, headers, generic_item, generic, "100", "mg")
    await _fact(app_client, headers, hydrate_item, hydrate, "100", "mg")
    return (
        _component(await _detail(app_client, headers, generic_item), generic),
        _component(await _detail(app_client, headers, hydrate_item), hydrate),
    )


async def test_p1_generic_ferrous_sulfate_research_does_not_reach_the_heptahydrate(
    app_client, db_clean, registered_supabase_user,
):
    generic, hydrate = await _published_generic_and_hydrate(
        app_client, registered_supabase_user, "ferrous sulfate", "Ferrous sulfate", "Ferrous sulfate heptahydrate",
    )
    assert generic["published_knowledge"]["status"] == "published"
    assert hydrate["form"] == {"status": "exact", "name": "ferrous sulfate"}
    assert hydrate["package_chemistry"]["status"] == "calculated"
    assert hydrate["package_chemistry"]["formula"] == "FeSO4H14O7"
    assert hydrate["package_chemistry"]["hydration"] == "heptahydrate"
    assert hydrate["package_chemistry"]["percent_by_weight"] == "20.1"
    assert hydrate["published_knowledge"] == {"status": "not_enough_information"}
    assert generic["published_knowledge"]["summary"] not in json.dumps(hydrate)


async def test_p1_generic_magnesium_chloride_research_does_not_reach_the_hexahydrate(
    app_client, db_clean, registered_supabase_user,
):
    generic, hydrate = await _published_generic_and_hydrate(
        app_client, registered_supabase_user, "magnesium chloride", "Magnesium chloride",
        "Magnesium chloride hexahydrate",
    )
    assert generic["published_knowledge"]["status"] == "published"
    assert hydrate["form"] == {"status": "exact", "name": "magnesium chloride"}
    assert hydrate["package_chemistry"]["status"] == "calculated"
    assert hydrate["package_chemistry"]["formula"] == "MgCl2H12O6"
    assert hydrate["package_chemistry"]["percent_by_weight"] == "12.0"
    assert hydrate["published_knowledge"] == {"status": "not_enough_information"}
    assert generic["published_knowledge"]["summary"] not in json.dumps(hydrate)


# ---------------------------------------------------------------------------
# D3. The loader never rewrites reviewed authority
# ---------------------------------------------------------------------------
SNAPSHOT_FIELDS = (
    "nutrient", "elemental_percent", "percent_kind", "hydration_note", "absorption_summary",
    "absorption_value", "absorption_unit", "disagreement", "source_name", "source_url",
    "source_identifier", "confidence", "evidence_tier", "notes", "verification", "evidence_claim_id",
)


async def _row_and_claim(form: str) -> tuple[dict, dict]:
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(SupplementComponentKnowledge).where(
            SupplementComponentKnowledge.compound_form == form,
        ))).scalar_one()
        claim = await session.get(EvidenceClaim, row.evidence_claim_id)
        row_values = {field: getattr(row, field) for field in SNAPSHOT_FIELDS}
        claim_values = {
            "id": claim.id, "review_status": claim.review_status, "summary": claim.summary,
            "notes": claim.notes, "structured_value": json.dumps(claim.structured_value, sort_keys=True),
            "published_at": claim.published_at, "reviewed_by": claim.reviewed_by,
        }
        return row_values, claim_values


def _drifted_compounds(form: str, **absorption_changes) -> tuple:
    import dataclasses

    changed = []
    for compound in COMPOUNDS:
        if compound.form == form:
            compound = dataclasses.replace(
                compound, absorption=dataclasses.replace(compound.absorption, **absorption_changes),
            )
        changed.append(compound)
    return tuple(changed)


async def _reload(monkeypatch=None, compounds=None) -> dict:
    from app.domains.supplements import knowledge_loader

    if monkeypatch is not None and compounds is not None:
        monkeypatch.setattr(knowledge_loader, "COMPOUNDS", compounds)
    async with get_sessionmaker()() as session:
        summary = await knowledge_loader.load(session)
        await session.commit()
    return summary


async def test_d3_a_confirmed_published_row_survives_a_loader_rerun_byte_for_byte(db_clean):
    await _load_knowledge()
    await _publish_form("magnesium oxide")
    before = await _row_and_claim("magnesium oxide")
    summary = await _reload()
    after = await _row_and_claim("magnesium oxide")
    assert after == before
    assert after[0]["verification"] == Verification.CONFIRMED.value
    assert summary["reviewed_rows_preserved"] == 1 and summary["reviewed_row_drift"] == 0
    assert (await _knowledge("magnesium", "magnesium oxide")).status is KnowledgeStatus.PUBLISHED


async def test_d3_reviewed_drift_is_reported_and_never_applied(db_clean, monkeypatch, caplog):
    await _load_knowledge()
    await _publish_form("magnesium oxide")
    before = await _row_and_claim("magnesium oxide")
    with caplog.at_level(logging.WARNING, logger="app.domains.supplements.knowledge_loader"):
        summary = await _reload(monkeypatch, _drifted_compounds("magnesium oxide", value_text="about 40"))
    after = await _row_and_claim("magnesium oxide")
    assert after == before
    assert summary["reviewed_row_drift"] == 1 and summary["reviewed_rows_preserved"] == 1
    assert any(getattr(record, "reason", None) == "reviewed_row_drift" for record in caplog.records)
    assert (await _knowledge("magnesium", "magnesium oxide")).status is KnowledgeStatus.PUBLISHED


async def test_d3_a_disputed_row_survives_a_loader_rerun(db_clean, monkeypatch):
    await _load_knowledge()
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(SupplementComponentKnowledge).where(
            SupplementComponentKnowledge.compound_form == "magnesium oxide",
        ))).scalar_one()
        row.verification = Verification.DISPUTED.value
        await session.commit()
    before = await _row_and_claim("magnesium oxide")
    summary = await _reload(monkeypatch, _drifted_compounds("magnesium oxide", value_text="about 40", summary="Rewritten."))
    after = await _row_and_claim("magnesium oxide")
    assert after == before and after[0]["verification"] == Verification.DISPUTED.value
    assert summary["reviewed_row_drift"] == 1


async def test_d3_an_approved_claim_freezes_an_unconfirmed_row(db_clean, monkeypatch):
    await _load_knowledge()
    await _publish_form("magnesium oxide", publish=False, confirm_row=False)
    before = await _row_and_claim("magnesium oxide")
    assert before[0]["verification"] == Verification.UNVERIFIED.value
    await _reload(monkeypatch, _drifted_compounds("magnesium oxide", value_text="about 40"))
    assert await _row_and_claim("magnesium oxide") == before


async def test_d3_unreviewed_draft_rows_still_follow_the_file_idempotently(db_clean, monkeypatch):
    await _load_knowledge()
    drifted = _drifted_compounds("magnesium oxide", value_text="about 40")
    first = await _reload(monkeypatch, drifted)
    row, claim = await _row_and_claim("magnesium oxide")
    assert row["absorption_value"] == "about 40" and row["verification"] == Verification.UNVERIFIED.value
    assert json.loads(claim["structured_value"])[BINDING_KEY]["absorption_value"] == "about 40"
    assert first["drafts_bound"] == 1 and first["reviewed_rows_preserved"] == 0
    second = await _reload(monkeypatch, drifted)
    assert second["drafts_bound"] == 0 and await _row_and_claim("magnesium oxide") == (row, claim)


# ---------------------------------------------------------------------------
# D4. The boundary's alternatives are about records, never use
# ---------------------------------------------------------------------------
USE_GUIDANCE = (
    "order to use", "when to take", "take first", "take together", "with food", "before food",
    "after food", "best time", "food ideas", "how often you take", "order you use",
)


async def test_d4_no_boundary_payload_offers_use_order_timing_or_food_pairing(
    app_client, db_clean, registered_supabase_user,
):
    token, _account = await registered_supabase_user()
    questions = [q for items in BOUNDARY_QUESTIONS.values() for q in items] + ORDINARY_QUESTIONS
    for question in questions:
        response = ok(await app_client.post(
            "/api/v2/supplements/professional-boundary", headers=auth(token), json={"question": question},
        ))
        rendered = json.dumps(response).lower()
        for phrase in USE_GUIDANCE:
            assert phrase not in rendered, (question, phrase)


def test_d4_the_supplement_alternatives_are_records_and_labels_only():
    from app.domains.routines.safety import narrative_is_safe

    assert supplement_boundary.CAN_HELP_WITH == (
        "What supplements you recorded",
        "What the package label says, as you recorded it",
        "Which of your products list the same component",
        "Expiry dates you recorded",
        "Which label details still need your confirmation",
    )
    for text_value in (*supplement_boundary.CAN_HELP_WITH, supplement_boundary.NO_BOUNDARY_MESSAGE,
                       supplement_boundary.SUPPLEMENT_PROFESSIONAL_BOUNDARY):
        assert narrative_is_safe(text_value), text_value
        assert not any(phrase in text_value.lower() for phrase in USE_GUIDANCE), text_value


# ---------------------------------------------------------------------------
# P1 governance. Step 13 customer copy lives in the keyed string authority
# ---------------------------------------------------------------------------
SUPPLEMENTS_DIR = Path(supplement_boundary.__file__).parent
BACKEND_DIR = SUPPLEMENTS_DIR.parents[2]

#: Every supplement module is scanned unless it is listed here, with its reason.
COPY_SCAN_EXCLUDED = {
    "__init__.py": "no code",
    "strings.py": "the string authority itself",
    "knowledge.py": "unverified knowledge data; reaches a customer only as a reviewed, published claim",
    "knowledge_loader.py": "writes that data into draft evidence claims for human review",
}

#: VC-07 customer copy that predates Step 13, frozen by exact value. Not Step 13
#: copy, left in place so this PR does not rewrite historical modules; nothing
#: may be added to it.
PRE_STEP13_VC07_COPY = {
    "engine.py": frozenset({
        "Expiry date not added.",
        "Past the date you recorded.",
        "Date coming up.",
        "This question is best discussed with a qualified professional.",
        "Tell you how much to take",
        "Tell you to start or stop anything",
        "Say what a supplement will do for you",
        "Advise on interactions with medicines",
        "Label tracking only. GlamGenius does not provide supplement dosage or medical advice.",
        "No supplements recorded. Add one and we will keep the label facts you enter.",
        "Package label facts only; no instructions about how much to take, treatment, medical assessment, "
        "or interaction conclusions.",
        "Questions that need health guidance belong with a qualified professional.",
        "Amounts are shown per product and are never added into intake totals.",
        "Upper-limit, RDA, EAR, and deficiency comparisons are not active in this utility.",
    }),
    "service.py": frozenset({
        "We could not find that supplement.",
        "This label-fact submission key is already used for different data.",
        "We could not find that label fact.",
    }),
}

#: Developer-facing exceptions: raised on a programming or table error, never
#: rendered to a customer.
_DEVELOPER_EXCEPTIONS = frozenset({"ValueError", "TypeError", "KeyError", "LookupError", "RuntimeError"})


def _reads_as_customer_prose(value: str) -> bool:
    stripped = value.strip()
    if stripped.isupper():  # SQL keywords such as "SET NULL"
        return False
    return len(stripped.split()) >= 2 and (stripped[:1].isupper() or stripped.endswith((".", "!", "?")))


class _CopyScanner(ast.NodeVisitor):
    """String literals that read as customer prose, outside exempt contexts.

    Exempt: docstrings; operator logs; developer exceptions; SQL passed to
    ``text()``; the model-facing prompt (``SYSTEM`` and ``prompt()``); and the
    nutrient display names, which are vocabulary rather than sentences.
    """

    def __init__(self) -> None:
        self.found: list[tuple[int, str]] = []
        self.keys: set[str] = set()
        self._exempt = 0

    def _skip(self, node: ast.AST) -> None:
        self._exempt += 1
        self.generic_visit(node)
        self._exempt -= 1

    def _mark_docstring(self, node) -> None:
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            first.value.docstring = True

    def visit_Module(self, node):
        self._mark_docstring(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self._mark_docstring(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        if node.name == "prompt":
            return self._skip(node)
        self._mark_docstring(node)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node):
        if any(getattr(target, "id", "") == "SYSTEM" for target in node.targets):
            return self._skip(node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if getattr(node.target, "id", "") == "NUTRIENT_DISPLAY_NAMES":
            return self._skip(node)
        self.generic_visit(node)

    def visit_Raise(self, node):
        if getattr(getattr(node.exc, "func", None), "id", "") in _DEVELOPER_EXCEPTIONS:
            return self._skip(node)
        self.generic_visit(node)

    def visit_Call(self, node):
        func = node.func
        owner = getattr(getattr(func, "value", None), "id", "")
        if isinstance(func, ast.Attribute) and owner == "copy" and func.attr == "text" and node.args \
                and isinstance(node.args[0], ast.Constant):
            self.keys.add(node.args[0].value)
            return None
        if owner in {"logger", "logging"} or getattr(func, "id", "") == "text":
            return self._skip(node)
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, str) and not self._exempt and not getattr(node, "docstring", False) \
                and _reads_as_customer_prose(node.value):
            self.found.append((node.lineno, node.value))


def _scan(path: Path) -> _CopyScanner:
    scanner = _CopyScanner()
    scanner.visit(ast.parse(path.read_text()))
    return scanner


def _scanned_modules() -> list[Path]:
    modules = [path for path in sorted(SUPPLEMENTS_DIR.glob("*.py")) if path.name not in COPY_SCAN_EXCLUDED]
    return [*modules, BACKEND_DIR / "app" / "api" / "v2" / "supplements.py"]


def test_p1_step13_modules_carry_no_customer_prose_outside_the_string_authority():
    assert set(COPY_SCAN_EXCLUDED) <= {path.name for path in SUPPLEMENTS_DIR.glob("*.py")} | {"__init__.py"}
    for path in _scanned_modules():
        allowed = PRE_STEP13_VC07_COPY.get(path.name, frozenset()) if path.parent == SUPPLEMENTS_DIR else frozenset()
        stray = [(line, value) for line, value in _scan(path).found if value not in allowed]
        assert stray == [], f"{path.name}: customer copy belongs in strings.py: {stray}"


def test_p1_every_copy_key_used_exists_and_every_key_is_used():
    used: set[str] = set(supplement_copy.CAN_HELP_WITH_KEYS)
    for path in _scanned_modules():
        used |= _scan(path).keys
    assert used <= set(supplement_copy.SUPPLEMENT_COPY), used - set(supplement_copy.SUPPLEMENT_COPY)
    assert set(supplement_copy.SUPPLEMENT_COPY) <= used, set(supplement_copy.SUPPLEMENT_COPY) - used


def test_p1_the_scanner_catches_a_hard_coded_customer_sentence(tmp_path):
    module = tmp_path / "hard_coded.py"
    module.write_text(
        '"""Docstring prose is fine."""\n'
        'import logging\nlogger = logging.getLogger(__name__)\n'
        'OK_KEY = "supplement.photo.not_an_image"\n'
        'def f():\n'
        '    logger.warning("Operator text is fine here.")\n'
        '    raise ValueError("Developer text is fine here.")\n'
        'def g():\n'
        '    raise NotFoundError("We could not find that photo.")\n',
    )
    assert [value for _line, value in _scan(module).found] == ["We could not find that photo."]


def test_p1_every_supplement_sentence_is_free_of_use_guidance_and_advice():
    from app.domains.routines.safety import narrative_is_safe

    for key, value in supplement_copy.SUPPLEMENT_COPY.items():
        assert narrative_is_safe(value), key
        assert not any(phrase in value.lower() for phrase in USE_GUIDANCE), key
