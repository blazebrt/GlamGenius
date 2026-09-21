"""What the customer-facing Product Result may say, and what it may not.

Three questions meet here, and they are one question wearing three hats:

* an observation nobody can *read* must not reach a consumer that assumes it
  can (``.get()`` on a JSONB array is a 500 on a public route);
* an observation that was not read must not be described with the confidence
  of one that was ("Checked by us against the pack" over catalogue facts);
* a comparison nobody can *source* must not be published at all, however
  correct it is internally.

All three are the Constitution's evidence rule applied to stored data:
*no source, no claim*, and missing data is stated rather than filled.
"""
from __future__ import annotations

import uuid

import pytest
from app.domains.product import label_evidence
from app.domains.product.change_projection import (
    UNAVAILABLE_PROJECTION,
    FormulaChangeStatus,
    LabelChangeStatus,
    project_label_change,
)
from app.domains.product.confidence import ProductConfidence
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import text

# The alternative engine's own fixtures: a catalogue row that can be graded on
# its own is what makes the malformed-snapshot path reachable at all.
# ``off_clean`` comes from conftest. The other two are re-registered under
# their public names by assignment rather than imported directly, so a test
# parameter of the same name is not read as shadowing an import.
from tests.test_step6a_comparable_alternative import (
    CURRENT,
    CURRENT_LABEL,
    INGREDIENTS_C,
    OFF_NUTRIMENTS_A,
    seed_label,
    seed_off,
)
from tests.test_step6a_comparable_alternative import (
    no_off_network as _no_off_network,
)
from tests.test_step6a_comparable_alternative import (
    published_rules as _published_rules,
)
from tests.test_step12a_label_change_authority import NUTRITION, _confirm, _versions

no_off_network = _no_off_network
published_rules = _published_rules

#: The two shapes JSONB can hold that are not fact objects and that a caller
#: will happily call ``.get()`` on. Parametrised everywhere rather than picked,
#: because the array and the string fail in different frames.
CORRUPTIONS = {
    "json-array": "'[\"not\",\"an\",\"object\"]'",
    "json-string": '\'"broken"\'',
}


@pytest.fixture
async def device(app_client):
    response = await app_client.post(
        "/api/v2/scan/device",
        json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert response.status_code == 201, response.text
    return {"X-Device-Token": response.json()["token"]}


async def _corrupt_facts(barcode: str, kind: str) -> None:
    """Make the stored facts unreadable in the database, not in memory.

    An in-memory object cannot prove the route survives the trip, and this is
    the only place such a row can actually come from.
    """
    async with get_sessionmaker()() as session:
        await session.execute(
            text(  # noqa: S608 - fixed literals, not interpolated input
                f"UPDATE product_label_snapshots SET facts = CAST({CORRUPTIONS[kind]} AS jsonb) "
                "WHERE barcode = :barcode"
            ),
            {"barcode": barcode},
        )
        await session.commit()


async def _gradeable_current(app_client, *, record_confidence: str | None = None) -> None:
    """A confirmed pack plus a catalogue row that can be graded without it."""
    await seed_label(CURRENT, CURRENT_LABEL)
    await seed_off(
        CURRENT, name="Northstar Corn Flakes", brands="Northstar",
        nutriments=OFF_NUTRIMENTS_A, ingredients_text=INGREDIENTS_C,
    )
    if record_confidence is not None:
        async with get_sessionmaker()() as session:
            await session.execute(
                text(
                    "INSERT INTO product_records (id, barcode, origin, confidence) "
                    "VALUES (:id, :barcode, 'label_capture', :confidence) "
                    "ON CONFLICT (barcode) DO UPDATE SET confidence = EXCLUDED.confidence"
                ),
                {"id": uuid.uuid4(), "barcode": CURRENT, "confidence": record_confidence},
            )
            await session.commit()


# ---------------------------------------------------------------------------
# An unreadable observation must not reach a consumer that assumes it is one
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind", list(CORRUPTIONS))
async def test_a_malformed_snapshot_does_not_take_down_a_gradeable_page(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, kind,
):
    """The whole page, with the alternative engine actually running.

    The alternative engine reads ``current_snapshot.facts`` to check the panel
    basis. Handed the raw row, a JSONB array reaches ``.get()`` and a
    recoverable Product Result becomes a 500 — but only once the fallback is
    gradeable, which is why an ungradeable fixture hid this.
    """
    await _gradeable_current(app_client)
    await _corrupt_facts(CURRENT, kind)

    response = await app_client.get(f"/api/v2/scan/verdict/{CURRENT}", headers=device)

    assert response.status_code == 200, response.text
    payload = response.json()
    # Graded from what could still be read, and it says so.
    assert payload["facts_provenance"] == "open_food_facts"
    assert payload["grade"] is not None
    assert payload["nutrition"]["total_sugar_g"] == 1
    # The alternative completed, and said what it could establish.
    assert payload["alternative"]["status"] == "not_enough_information"
    # The value envelope consumes the decided alternative and must not go back
    # to the snapshot for a second opinion. It completed, and it says exactly
    # why it has nothing to compare: the alternative above found no candidate.
    # Any other reason would mean it had gone looking on its own.
    assert payload["value"]["status"] == "not_enough_information"
    assert payload["value"]["reason_key"] == "no_comparable_alternative"
    # History exists and is withheld — never `null`, which means never observed.
    assert payload["label_change"] == UNAVAILABLE_PROJECTION.as_payload()
    assert payload["label_change"] is not None


@pytest.mark.parametrize("kind", list(CORRUPTIONS))
async def test_the_alternative_engine_is_given_the_same_authority_as_the_verdict(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, kind,
):
    """Structural: the engine receives no confirmed snapshot at all.

    Not a weaker malformed-facts rule of its own — none. The verdict decided
    the snapshot was unreadable, and that decision travels.
    """
    from app.api.v2 import product as product_api

    await _gradeable_current(app_client)
    await _corrupt_facts(CURRENT, kind)

    received: list[object] = []
    original = product_api.alternatives_service.comparable_alternative_envelope

    async def spy(session, **kwargs):
        received.append(kwargs["current_snapshot"])
        return await original(session, **kwargs)

    product_api.alternatives_service.comparable_alternative_envelope = spy
    try:
        response = await app_client.get(f"/api/v2/scan/verdict/{CURRENT}", headers=device)
    finally:
        product_api.alternatives_service.comparable_alternative_envelope = original

    assert response.status_code == 200, response.text
    assert received == [None]


# ---------------------------------------------------------------------------
# Confidence describes the facts this response was actually built from
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind", list(CORRUPTIONS))
async def test_catalogue_facts_never_inherit_pack_verification(
    db_clean, off_clean, app_client, device, published_rules, no_off_network, kind,
):
    """A verified ProductRecord does not make catalogue facts pack-checked.

    ``verified`` means a reviewer opened the pack and confirmed a record. After
    the confirmed observation is rejected as unreadable, the facts on screen
    came from Open Food Facts — and "Checked by us against the pack" printed
    over them is simply false, whatever the record says.
    """
    await _gradeable_current(app_client, record_confidence=ProductConfidence.VERIFIED.value)
    await _corrupt_facts(CURRENT, kind)

    payload = (await app_client.get(
        f"/api/v2/scan/verdict/{CURRENT}", headers=device,
    )).json()

    assert payload["facts_provenance"] == "open_food_facts"
    assert payload["confidence"]["level"] != ProductConfidence.VERIFIED.value
    assert payload["confidence"]["level"] == ProductConfidence.UNVERIFIED.value
    assert "against the pack" not in payload["confidence"]["text"]
    assert "Checked by us" not in payload["confidence"]["text"]


async def test_a_readable_observation_still_carries_its_own_confidence(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """The ordinary path is untouched: a readable pack speaks for itself."""
    await _gradeable_current(app_client, record_confidence=ProductConfidence.VERIFIED.value)

    payload = (await app_client.get(
        f"/api/v2/scan/verdict/{CURRENT}", headers=device,
    )).json()

    assert payload["facts_provenance"] == "confirmed_label_snapshot"
    assert payload["confidence"]["level"] in tuple(c.value for c in ProductConfidence)


async def test_no_factual_source_at_all_is_stated_as_not_enough_information(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """Nothing readable and nothing to fall back to is an answer, not a blank."""
    await seed_label(CURRENT, CURRENT_LABEL)
    await _corrupt_facts(CURRENT, "json-array")

    payload = (await app_client.get(
        f"/api/v2/scan/verdict/{CURRENT}", headers=device,
    )).json()

    assert payload["facts_provenance"] == "open_food_facts"
    assert payload["confidence"]["level"] == ProductConfidence.NOT_ENOUGH_INFORMATION.value


# ---------------------------------------------------------------------------
# A comparison nobody can source is not published, however correct it is
# ---------------------------------------------------------------------------
async def test_the_engine_still_compares_two_valid_observations_correctly(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """Withholding is a publication decision, not a lobotomy."""
    barcode = "8900000000401"
    token, account_id = await registered_supabase_user()
    base = {"product_name": "Observed", "ingredients_text": "Water,Glycerin", **NUTRITION}
    await _confirm(app_client, device, token, account_id, barcode, base)
    await _confirm(app_client, device, token, account_id, barcode,
                   {**base, "ingredients_text": "Water,Niacinamide"})

    versions = await _versions(barcode)
    internal = project_label_change(current=versions[1], previous=versions[0])

    assert internal.status is LabelChangeStatus.CHANGED
    assert internal.changed_fields == ("ingredients",)
    assert internal.formula.status is FormulaChangeStatus.INGREDIENT_SET_CHANGED
    assert [row.name for row in internal.formula.only_on_current_label] == ["Niacinamide"]
    assert [row.name for row in internal.formula.only_on_previous_label] == ["Glycerin"]


async def test_the_anonymous_product_result_publishes_none_of_it(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """Every field the claim would travel in, checked one by one.

    The comparison above is correct. Neither observation has a source this
    caller could open — nothing on the chain stores a locator for the
    photograph, and the photograph is a private MediaAsset — so the sentence
    is not published in any of its forms.
    """
    barcode = "8900000000418"
    token, account_id = await registered_supabase_user()
    base = {"product_name": "Observed", "ingredients_text": "Water,Glycerin", **NUTRITION}
    await _confirm(app_client, device, token, account_id, barcode, base)
    await _confirm(app_client, device, token, account_id, barcode,
                   {**base, "ingredients_text": "Water,Niacinamide"})

    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)
    assert response.status_code == 200, response.text
    payload = response.json()
    change = payload["label_change"]

    assert change == UNAVAILABLE_PROJECTION.as_payload()
    assert change["status"] not in ("changed", "unchanged", "first_observed_version")
    assert change["changed_fields"] == []
    assert change["current_version"] is None and change["previous_version"] is None
    assert change["formula"]["status"] == "not_applicable"
    assert change["formula"]["only_on_current_label"] == []
    assert change["formula"]["only_on_previous_label"] == []
    assert "Niacinamide" not in repr(change) and "Glycerin" not in repr(change)


async def test_changed_fields_does_not_leak_through_the_version_block(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """The same assertion, published beside it under a quieter name.

    ``None`` and not ``[]``: an empty list positively asserts that no field
    changed, which is a claim of exactly the kind being withheld. The stored
    row keeps its real value — this is publication, not deletion.
    """
    barcode = "8900000000425"
    token, account_id = await registered_supabase_user()
    base = {"product_name": "Observed", "ingredients_text": "Water,Glycerin", **NUTRITION}
    await _confirm(app_client, device, token, account_id, barcode, base)
    await _confirm(app_client, device, token, account_id, barcode,
                   {**base, "ingredients_text": "Water,Niacinamide"})

    version_block = (await app_client.get(
        f"/api/v2/scan/verdict/{barcode}", headers=device,
    )).json()["label_version"]

    assert version_block["changed_fields"] is None
    assert version_block["changed_fields"] != []
    # Identity the shelf and decision memory are pinned to survives untouched.
    assert version_block["version_number"] == 2
    assert version_block["content_fingerprint"]
    assert version_block["completeness"]
    # And the stored history is intact underneath.
    assert (await _versions(barcode))[1].changed_fields == ["ingredients"]


async def test_our_own_metadata_is_not_a_source(db_clean):
    """Version numbers, fingerprints and timestamps are ours, not evidence.

    Each is identity or integrity metadata. Offering one as a source would be
    the app speaking in its own voice while pointing at itself.
    """
    from tests.test_step12a_label_change_authority import _snapshot

    snapshot = _snapshot({"product_name": "Observed", "ingredients_text": "Water"})
    assert label_evidence.observation_source(snapshot) is None
    assert not label_evidence.comparison_is_publishable(current=snapshot, previous=None)

    for ours in (
        str(snapshot.version_number),
        snapshot.content_fingerprint,
        str(snapshot.id),
        str(snapshot.scan_event_id),
    ):
        assert not label_evidence.is_openable_customer_source(ours)


@pytest.mark.parametrize(
    "candidate",
    [
        pytest.param("/api/v2/media/0f2c1b4e", id="our-own-media-path"),
        pytest.param("https://glamgenius.example/api/v2/media/0f2c1b4e", id="our-own-api-url"),
        pytest.param("media/0f2c1b4e.jpg", id="storage-key"),
        pytest.param("0f2c1b4e-0000-4000-8000-000000000000", id="bare-uuid"),
        pytest.param("file:///var/media/photo.jpg", id="local-file"),
        pytest.param("not a url at all", id="prose"),
        pytest.param("", id="empty"),
        pytest.param(None, id="absent"),
        pytest.param(12345, id="number"),
    ],
)
def test_the_publication_gate_is_not_satisfied_by_a_manufactured_locator(candidate):
    """A future contributor cannot open the gate with a string they assembled.

    The private media route is the tempting one: it exists, it resolves, and it
    is exactly what a customer may not open — it belongs to one account.
    """
    assert not label_evidence.is_openable_customer_source(candidate)


async def test_a_product_never_observed_still_reports_none(
    db_clean, off_clean, app_client, device, published_rules, no_off_network,
):
    """``None`` keeps its own meaning: no confirmed observation exists.

    Collapsing "never seen" into "seen but unpublishable" would erase the
    difference between a product nobody has photographed and one whose history
    we are declining to characterise.
    """
    await seed_off(CURRENT, name="Catalogue only", nutriments=OFF_NUTRIMENTS_A)

    payload = (await app_client.get(
        f"/api/v2/scan/verdict/{CURRENT}", headers=device,
    )).json()

    assert payload["label_version"] is None
    assert payload["label_change"] is None


async def test_the_withheld_envelope_carries_nothing_private(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """No account, device, snapshot, media or storage identifier, and no reason."""
    from tests.test_step12a_label_change_authority import _uuids_in

    barcode = "8900000000432"
    token, account_id = await registered_supabase_user()
    await _confirm(app_client, device, token, account_id, barcode,
                   {"product_name": "Observed", "ingredients_text": "Water", **NUTRITION})

    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)
    change = response.json()["label_change"]

    assert not _uuids_in(change)
    assert str(account_id) not in response.text
    for leaked in ("media", "storage_key", "device_id", "account_id",
                   "label_facts_invalid", "invariant", "previous_snapshot_id"):
        assert leaked not in repr(change)


# ---------------------------------------------------------------------------
# Ordering: integrity first, publication second
#
# The two authorities answer different questions and must not be wired in
# series. When publication was decided first and the projection ran only inside
# its ``True`` branch, the integrity authority silently left the Product Result
# — ``comparison_is_publishable()`` is False for every pair today, so
# ``project_label_change()`` never ran on this route at all. A corrupt chain
# was then never examined, and the day a locator field is persisted it would be
# examined for the first time with real callers behind it.
#
# Every test below fails on a route that asks the evidence gate first.
# ---------------------------------------------------------------------------
def _publication(monkeypatch, allowed: bool) -> None:
    """Force the evidence gate's answer without touching the real schema.

    No fake locator is persisted and ``is_openable_customer_source`` is left
    exactly as it is. This only simulates the day a lawful source exists, so
    the branch behind that day can be tested before it arrives.
    """
    from app.api.v2 import product as product_api

    monkeypatch.setattr(
        product_api.label_evidence,
        "comparison_is_publishable",
        lambda **_kwargs: allowed,
    )


async def _corrupt_fingerprint(barcode: str) -> None:
    """Break a Step 12A integrity invariant, leaving the facts readable.

    ``readable_label_snapshot`` still accepts this row — its ``facts`` are a
    perfectly good object — so nothing upstream filters it out. Only
    ``project_label_change`` can refuse it, which is the point.
    """
    async with get_sessionmaker()() as session:
        await session.execute(
            text("UPDATE product_label_snapshots SET content_fingerprint = :v "
                 "WHERE barcode = :b"),
            {"v": "d" * 64, "b": barcode},
        )
        await session.commit()


async def test_the_internal_authority_runs_even_when_publication_is_withheld(
    db_clean, off_clean, app_client, device, registered_supabase_user,
):
    """The projection is called on the real route, with evidence absent.

    Kills "skip the projection whenever the evidence gate returns false".
    A withheld answer is still an answer that had to be computed and checked.
    """
    from app.api.v2 import product as product_api

    barcode = "8900000000449"
    token, account_id = await registered_supabase_user()
    base = {"product_name": "Observed", "ingredients_text": "Water,Glycerin", **NUTRITION}
    await _confirm(app_client, device, token, account_id, barcode, base)
    await _confirm(app_client, device, token, account_id, barcode,
                   {**base, "ingredients_text": "Water,Niacinamide"})

    calls: list[tuple[object, object]] = []
    original = product_api.change_projection.project_label_change

    def spy(*, current, previous):
        calls.append((current, previous))
        return original(current=current, previous=previous)

    product_api.change_projection.project_label_change = spy
    try:
        response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)
    finally:
        product_api.change_projection.project_label_change = original

    assert response.status_code == 200, response.text
    # It ran, on exactly the two rows the stored link names.
    assert len(calls) == 1
    current, previous = calls[0]
    assert current.version_number == 2
    assert previous is not None and previous.id == current.previous_snapshot_id
    # And the customer is still told nothing, because nothing sources it.
    payload = response.json()
    assert payload["label_change"] == UNAVAILABLE_PROJECTION.as_payload()
    assert payload["label_version"]["changed_fields"] is None


async def test_a_corrupt_history_is_still_detected_when_evidence_is_absent(
    db_clean, off_clean, app_client, device, registered_supabase_user,
    published_rules, no_off_network, caplog,
):
    """Corruption is found and logged even though nobody was going to be told.

    On a route that asks the evidence gate first this row is never read by the
    integrity authority at all: the page looks identical, and the warning that
    is the only trace of the corruption is never emitted.
    """
    import logging

    barcode = "8900000000456"
    token, account_id = await registered_supabase_user()
    base = {"product_name": "Observed", "ingredients_text": "Water,Glycerin", **NUTRITION}
    await _confirm(app_client, device, token, account_id, barcode, base)
    await _confirm(app_client, device, token, account_id, barcode,
                   {**base, "ingredients_text": "Water,Niacinamide"})
    await _corrupt_fingerprint(barcode)

    with caplog.at_level(logging.WARNING, logger="app.api.v2.product"):
        response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)

    # The page survives: everything around this envelope was established
    # without it and is still true.
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["grade"] is not None
    assert payload["label_change"] == UNAVAILABLE_PROJECTION.as_payload()
    assert payload["label_version"]["changed_fields"] is None

    # The corruption was seen, named to the log, and named to nobody else.
    warnings = [r for r in caplog.records if "label_history_invariant_failed" in r.getMessage()]
    assert warnings, "the integrity authority never ran"
    logged = warnings[0].getMessage()
    assert "fingerprint_mismatch" in logged
    assert barcode in logged
    # The reason is for the server log. It is not in the response, anywhere.
    assert "fingerprint_mismatch" not in response.text
    assert "invariant" not in response.text


async def test_evidence_can_never_override_integrity(
    db_clean, off_clean, app_client, device, registered_supabase_user, monkeypatch,
):
    """With publication allowed and the history corrupt, nothing is published.

    This is the milestone-after-next test. When a lawful locator is finally
    persisted, the evidence gate starts returning True — and that must not turn
    a chain the integrity authority rejects into a customer-facing claim.
    """
    barcode = "8900000000463"
    token, account_id = await registered_supabase_user()
    base = {"product_name": "Observed", "ingredients_text": "Water,Glycerin", **NUTRITION}
    await _confirm(app_client, device, token, account_id, barcode, base)
    await _confirm(app_client, device, token, account_id, barcode,
                   {**base, "ingredients_text": "Water,Niacinamide"})
    await _corrupt_fingerprint(barcode)
    _publication(monkeypatch, True)

    response = await app_client.get(f"/api/v2/scan/verdict/{barcode}", headers=device)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["label_change"] == UNAVAILABLE_PROJECTION.as_payload()
    assert payload["label_version"]["changed_fields"] is None
    # Not one word of the comparison the corrupt rows would have implied.
    assert "Niacinamide" not in repr(payload["label_change"])
    assert "Glycerin" not in repr(payload["label_change"])


async def test_valid_history_with_publication_allowed_is_published(
    db_clean, off_clean, app_client, device, registered_supabase_user, monkeypatch,
):
    """Structural proof of the far side: both authorities satisfied.

    Nothing here weakens the real evidence gate — it is stubbed for this one
    request and no locator is written. It exists so the publishing branch is
    exercised at all, and so the two published values are proven to come from
    the validated projection rather than straight from the stored row.
    """
    barcode = "8900000000470"
    token, account_id = await registered_supabase_user()
    base = {"product_name": "Observed", "ingredients_text": "Water,Glycerin", **NUTRITION}
    await _confirm(app_client, device, token, account_id, barcode, base)
    await _confirm(app_client, device, token, account_id, barcode,
                   {**base, "ingredients_text": "Water,Niacinamide"})
    _publication(monkeypatch, True)

    payload = (await app_client.get(
        f"/api/v2/scan/verdict/{barcode}", headers=device,
    )).json()

    versions = await _versions(barcode)
    expected = project_label_change(current=versions[1], previous=versions[0])
    assert payload["label_change"] == expected.as_payload()
    assert payload["label_change"]["status"] == LabelChangeStatus.CHANGED.value
    assert payload["label_change"]["formula"]["status"] == (
        FormulaChangeStatus.INGREDIENT_SET_CHANGED.value
    )
    # The version block agrees with the projection that was validated, not with
    # whatever the row happens to store.
    assert payload["label_version"]["changed_fields"] == list(expected.changed_fields)
