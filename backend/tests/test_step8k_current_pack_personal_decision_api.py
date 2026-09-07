"""Step 8K — the FOR YOU answer for the pack in somebody's hand.

The first customer surface over the governed Step 8A–8H chain. Almost every
test here is about something the endpoint must *refuse* to do: answer from a
pack nobody confirmed, answer under a category nobody recorded, answer from a
snapshot that did not exist yet, answer from a release nobody activated, or
answer with wording nobody reviewed.

The decisive test runs the whole chain for real — capture through reviewed
copy — with only the model boundary mocked.
"""

from __future__ import annotations

import ast
import json
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from app.api.v2 import skin_care_personal_decision as api
from app.bootstrap import run as run_reference_seed
from app.content import for_you_copy
from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun, AIRunOutput
from app.domains.evidence import authoring as evidence_authoring
from app.domains.evidence.enums import EvidenceStrength, SourceStatus, SourceType
from app.domains.evidence.models import EvidenceClaim, EvidenceSource
from app.domains.personal_applicability import authoring as applicability_authoring
from app.domains.personal_applicability.enums import PersonalApplicabilityCategory
from app.domains.personal_decision_explanation import PERSONAL_DECISION_EXPLANATION_RULES
from app.domains.personal_decision_policy import PERSONAL_DECISION_POLICY_RULES
from app.domains.personal_decision_release import authoring as release_authoring
from app.domains.personal_decision_release.models import PersonalDecisionRelease
from app.domains.personal_decision_release.runtime import (
    load_active_personal_decision_release,
)
from app.domains.personal_decision_release.validation import ReleaseVerification
from app.domains.personal_decision_semantics import PERSONAL_DECISION_SEMANTIC_RULES
from app.domains.product import care_extraction, service
from app.domains.product.models import LabelSnapshot, ScanEvent
from app.domains.product.personal_decision import (
    CurrentPackSnapshotUnresolved,
    resolve_current_pack_label_snapshot,
)
from app.domains.profile.models import AppearanceProfile, ProfileAttribute
from app.domains.substances import authoring as substance_authoring
from app.domains.substances.enums import EntityKind, NameNamespace
from app.knowledge_packs import petrolatum_dry_skin_v1 as pack
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select, update

from tests.conftest import auth, png_bytes

BACKEND_ROOT = Path(__file__).resolve().parents[1]
API_PATH = BACKEND_ROOT / "app" / "api" / "v2" / "skin_care_personal_decision.py"
RESOLVER_PATH = BACKEND_ROOT / "app" / "domains" / "product" / "personal_decision.py"
COPY_PATH = BACKEND_ROOT / "app" / "content" / "for_you_copy.py"
STEP8K_MODULES = (API_PATH, RESOLVER_PATH, COPY_PATH)

FOR_YOU_URL = "/api/v2/scan/skin-care/for-you"
CONFIRM_URL = "/api/v2/scan/skin-care/label/confirm"
TRANSCRIBE_URL = "/api/v2/scan/skin-care/label/transcribe"

BARCODE = "8901030000011"
OTHER_BARCODE = "8901030000028"
PETROLATUM = "Petrolatum"

EVIDENCE_VERIFICATION = evidence_authoring.VerificationInput(
    source_opened=True,
    founder_verified_fact=True,
    claude_review_completed=True,
    codex_review_completed=True,
    independent_reviews_agree=True,
    adversarial_review_passed=True,
    unresolved_doubt=False,
)
RELEASE_VERIFICATION = ReleaseVerification(
    founder_review_completed=True,
    claude_review_completed=True,
    codex_review_completed=True,
    independent_reviews_agree=True,
    adversarial_review_passed=True,
    unresolved_doubt=False,
)


# ---------------------------------------------------------------------------
# Capture helpers — the real Step 8J path
# ---------------------------------------------------------------------------
async def _register_device(app_client) -> tuple[dict[str, str], uuid.UUID]:
    response = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert response.status_code == 201, response.text
    return {"X-Device-Token": response.json()["token"]}, uuid.UUID(response.json()["device_id"])


async def _claim(app_client, headers, token) -> None:
    claimed = await app_client.post(
        "/api/v2/scan/device/claim", headers={**headers, **auth(token)},
    )
    assert claimed.status_code == 200, claimed.text


async def _seed_run(account_id: uuid.UUID, ingredients: str = PETROLATUM) -> uuid.UUID:
    factory = get_sessionmaker()
    async with factory() as session:
        run = AIRun(
            account_id=account_id, feature=care_extraction.FEATURE, provider="test",
            model="test-model", prompt_version=care_extraction.PROMPT_VERSION,
            schema_version=care_extraction.SCHEMA_VERSION,
            status=AI_STATUS_SUCCEEDED, validation_passed=True,
        )
        session.add(run)
        await session.flush()
        session.add(AIRunOutput(
            ai_run_id=run.id, schema_version=care_extraction.SCHEMA_VERSION,
            payload={
                "product_name": "Synthetic Petrolatum Ointment",
                "brand": "Synthetic Brand",
                "ingredients_text": ingredients,
            },
        ))
        await session.commit()
        return run.id


async def _confirm(app_client, headers, token, *, barcode=BARCODE, ingredients=PETROLATUM,
                   account_id: uuid.UUID):
    run_id = await _seed_run(account_id, ingredients)
    response = await app_client.post(
        CONFIRM_URL, headers={**headers, **auth(token)},
        json={
            "barcode": barcode, "ai_run_id": str(run_id),
            "client_scan_id": uuid.uuid4().hex,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _confirmed_device(app_client, registered_supabase_user, *, barcode=BARCODE,
                            ingredients=PETROLATUM):
    """An account with a claimed device holding one confirmed skin-care pack."""
    token, account_id = await registered_supabase_user()
    headers, device_id = await _register_device(app_client)
    await _claim(app_client, headers, token)
    body = await _confirm(
        app_client, headers, token, barcode=barcode,
        ingredients=ingredients, account_id=account_id,
    )
    return {
        "token": token, "account_id": account_id, "headers": headers,
        "device_id": device_id, "capture": body,
    }


async def _for_you(app_client, headers, token, *, barcode=BARCODE, safety=None):
    payload: dict[str, Any] = {"barcode": barcode}
    if safety is not None:
        payload["safety"] = safety
    return await app_client.post(
        FOR_YOU_URL, headers={**headers, **auth(token)}, json=payload,
    )


# ---------------------------------------------------------------------------
# Evidence and release helpers — the real Step 8G/8H authorities
# ---------------------------------------------------------------------------
async def _publish_identity(session) -> None:
    result = await substance_authoring.create_identity_draft(
        session,
        substance_key=pack.SUBSTANCE_KEY,
        entity_kind=EntityKind.MIXTURE.value,
        names=[{
            "name": pack.IDENTITY_NAME,
            "namespace": NameNamespace.OFFICIAL_REFERENCE.value,
            "language_tag": "und",
            "is_preferred": True,
        }],
        summary=f"The reviewed reference records the exact identity name {pack.IDENTITY_NAME}.",
        scope="Identity and nomenclature only.",
        evidence_strength=EvidenceStrength.STRONG.value,
        strength_rationale="The named governmental reference records this identity directly.",
        source_title=pack.IDENTITY_SOURCE_TITLE,
        source_publisher=pack.IDENTITY_SOURCE_PUBLISHER,
        source_type=SourceType.GOVERNMENT_REFERENCE.value,
        source_url=pack.IDENTITY_SOURCE_URL,
        license_or_use_note=pack.IDENTITY_SOURCE_USE_NOTE,
        author="step8k.test",
    )
    claim_id = uuid.UUID(result["claim_id"])
    await evidence_authoring.approve(session, claim_id, reviewer="step8k.reviewer")
    await evidence_authoring.record_publication_verification(
        session, claim_id, verification=EVIDENCE_VERIFICATION, actor="step8k.founder",
    )
    await evidence_authoring.publish(session, claim_id, publisher="step8k.publisher")


def _applicability_input(*, existing_sources: list[dict] | None = None):
    if existing_sources is not None:
        sources: tuple[Any, ...] = tuple(
            applicability_authoring.ExistingSourceInput(
                source_key=str(source["source_key"]), locator=str(source["locator"]),
            )
            for source in existing_sources
        )
    else:
        sources = _NEW_SOURCES()
    return applicability_authoring.PersonalApplicabilityDraftInput(
        category=PersonalApplicabilityCategory.SKIN_CARE,
        substance_key=pack.SUBSTANCE_KEY,
        summary=pack.EVIDENCE_SUMMARY,
        scope=pack.EVIDENCE_SCOPE,
        evidence_strength=pack.EVIDENCE_STRENGTH,
        strength_rationale=pack.EVIDENCE_STRENGTH_RATIONALE,
        conditions=(
            applicability_authoring.AuthoringConditionInput(
                fact_key=pack.FACT_KEY, values=pack.FACT_VALUES,
            ),
        ),
        sources=sources,
    )


def _NEW_SOURCES():
    return (
            applicability_authoring.NewSourceInput(
                source_type=SourceType.PROFESSIONAL_CONSENSUS.value,
                title=pack.AAD_SOURCE_TITLE,
                publisher=pack.AAD_SOURCE_PUBLISHER,
                canonical_url=pack.AAD_SOURCE_URL,
                license_or_use_note=pack.AAD_SOURCE_USE_NOTE,
                locator=pack.AAD_SOURCE_LOCATOR,
                publication_date=None,
                version_or_revision=pack.AAD_SOURCE_VERSION,
                jurisdiction=pack.AAD_SOURCE_JURISDICTION,
            ),
            applicability_authoring.NewSourceInput(
                source_type=SourceType.PEER_REVIEWED_RESEARCH.value,
                title=pack.PUBMED_SOURCE_TITLE,
                publisher=pack.PUBMED_SOURCE_PUBLISHER,
                canonical_url=pack.PUBMED_SOURCE_URL,
                license_or_use_note=pack.PUBMED_SOURCE_USE_NOTE,
                locator=pack.PUBMED_SOURCE_LOCATOR,
                publication_date=date.fromisoformat(pack.PUBMED_SOURCE_PUBLICATION_DATE),
                version_or_revision=pack.PUBMED_SOURCE_VERSION,
                jurisdiction=pack.PUBMED_SOURCE_JURISDICTION,
            ),
    )


async def _publish_applicability(
    session, *, entry_id: uuid.UUID | None = None, existing_sources: list[dict] | None = None,
) -> dict:
    entry = _applicability_input(existing_sources=existing_sources)
    if entry_id is None:
        view = await applicability_authoring.create_personal_applicability_draft(
            session, entry, author="step8k.author",
        )
    else:
        view = await applicability_authoring.edit_personal_applicability_entry(
            session, entry_id, entry, author="step8k.author",
        )
    identifier = uuid.UUID(view["id"])
    await applicability_authoring.approve_personal_applicability_entry(
        session, identifier, reviewer="step8k.reviewer",
    )
    await applicability_authoring.record_personal_applicability_publication_verification(
        session, identifier, verification=EVIDENCE_VERIFICATION, actor="step8k.founder",
    )
    return await applicability_authoring.publish_personal_applicability_entry(
        session, identifier, publisher="step8k.publisher",
    )


async def _activate(session, published: dict, *, manifest: dict | None = None) -> dict:
    manifest = manifest or pack.build_release_manifest_from_published_entry(published)
    draft = await release_authoring.create_personal_decision_release_draft(
        session, manifest, actor="step8k.author",
    )
    release_id = uuid.UUID(draft["id"])
    await release_authoring.record_personal_decision_release_verification(
        session, release_id, verification=RELEASE_VERIFICATION, actor="step8k.reviewer",
    )
    validation = await release_authoring.validate_personal_decision_release(session, release_id)
    assert validation["ready"] is True, validation
    await release_authoring.approve_personal_decision_release(
        session, release_id, actor="step8k.approver",
    )
    return await release_authoring.activate_personal_decision_release(
        session, release_id, actor="step8k.activator",
    )


async def _profile(
    session, account_id: uuid.UUID, *,
    usual_feel: str = "often_dry_or_tight",
    sensitivity: str | None = "rarely_reactive",
) -> None:
    profile = AppearanceProfile(account_id=account_id)
    session.add(profile)
    await session.flush()
    values = [(pack.FACT_KEY, usual_feel)]
    if sensitivity is not None:
        values.append(("care_skin_sensitivity", sensitivity))
    for key, value in values:
        session.add(ProfileAttribute(
            profile_id=profile.id, key=key, value=value, source="user_declared",
            confidence=1.0, verification_state="confirmed",
        ))
    await session.flush()


async def _qualified_release(session) -> dict:
    await _publish_identity(session)
    published = await _publish_applicability(session)
    activated = await _activate(session, published)
    await session.commit()
    return {"published": published, "activated": activated}


# ---------------------------------------------------------------------------
# Response inspection
# ---------------------------------------------------------------------------
_DECISION_TOKENS = frozenset({"buy", "wait", "skip", "BUY", "WAIT", "SKIP"})


def _values(node: Any):
    """Every scalar value in a serialized response, with its key path."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from ((f"{key}.{path}" if path else key, item) for path, item in _values(value))
    elif isinstance(node, list):
        for entry in node:
            yield from _values(entry)
    else:
        yield "", node


def _assert_no_decision(body: dict) -> None:
    """Structural, not substring: no field anywhere carries an action value.

    A prose sentence may legitimately contain "waiting"; a *value* equal to
    "wait" is a leaked verdict. Comparing whole values keeps the two apart.
    """
    result = body["result"]
    assert result["action"] is None, result
    assert result["verdict_key"] is None, result
    assert result["verdict_text"] is None, result
    assert result["citation"] is None, result
    for path, value in _values(body):
        assert value not in _DECISION_TOKENS, (path, value)
    assert result["reason_text"].strip()


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_module_tree(path)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _declared_fields(path: Path) -> dict[str, set[str]]:
    fields: dict[str, set[str]] = {}
    for node in ast.walk(_module_tree(path)):
        if isinstance(node, ast.ClassDef):
            fields[node.name] = {
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
            }
    return fields


# ---------------------------------------------------------------------------
# 1. The current pack is the only physical authority
# ---------------------------------------------------------------------------
class TestCurrentPackPrecondition:
    async def test_a_device_that_never_scanned_gets_no_decision(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        response = await _for_you(app_client, headers, token)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["result"]["status"] == api.STATUS_PACK_NOT_CONFIRMED
        assert body["result"]["reason_key"] == for_you_copy.REASON_KEY_CONFIRMED_PACK
        assert body["product_category"] is None
        assert body["pack"]["is_proven"] is False
        assert body["release"] is None
        _assert_no_decision(body)

    async def test_a_plain_scan_is_not_a_confirmed_pack(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)
        scanned = await app_client.post(
            "/api/v2/scan/events", headers=headers,
            json={"barcode": BARCODE, "client_scan_id": uuid.uuid4().hex},
        )
        assert scanned.status_code == 201, scanned.text
        body = (await _for_you(app_client, headers, token)).json()
        assert body["result"]["status"] == api.STATUS_PACK_NOT_CONFIRMED
        _assert_no_decision(body)

    async def test_a_later_plain_scan_withdraws_the_answer(
        self, db_clean, off_clean, app_client, registered_supabase_user,
    ):
        """Never reach backwards. A plain scan means a different packet."""
        owner = await _confirmed_device(app_client, registered_supabase_user)
        scanned = await app_client.post(
            "/api/v2/scan/events", headers=owner["headers"],
            json={"barcode": BARCODE, "client_scan_id": uuid.uuid4().hex},
        )
        assert scanned.status_code == 201, scanned.text
        body = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        assert body["result"]["status"] == api.STATUS_PACK_NOT_CONFIRMED
        _assert_no_decision(body)

    async def test_a_stranger_global_snapshot_answers_nobody_else(
        self, db_clean, app_client, registered_supabase_user,
    ):
        await _confirmed_device(app_client, registered_supabase_user)
        token, _ = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        await _claim(app_client, headers, token)

        factory = get_sessionmaker()
        async with factory() as session:
            assert await service.latest_label_snapshot(session, BARCODE) is not None

        body = (await _for_you(app_client, headers, token)).json()
        assert body["result"]["status"] == api.STATUS_PACK_NOT_CONFIRMED
        assert body["pack"]["label_snapshot_id"] is None
        _assert_no_decision(body)

    async def test_a_forged_capture_without_a_run_answers_nothing(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, account_id = await registered_supabase_user()
        headers, device_id = await _register_device(app_client)
        await _claim(app_client, headers, token)
        factory = get_sessionmaker()
        async with factory() as session:
            session.add(ScanEvent(
                device_id=device_id, account_id=account_id, barcode=BARCODE,
                outcome=service.OUTCOME_LABEL, client_scan_id=uuid.uuid4().hex,
                label_facts={"ingredients_text": PETROLATUM, "product_category": "skin_care"},
                ai_run_id=None,
            ))
            await session.commit()
        body = (await _for_you(app_client, headers, token)).json()
        assert body["result"]["status"] == api.STATUS_PACK_NOT_CONFIRMED
        _assert_no_decision(body)

    async def test_an_unclaimed_or_foreign_device_gets_no_personal_decision(
        self, db_clean, app_client, registered_supabase_user,
    ):
        token, _ = await registered_supabase_user()
        headers, _ = await _register_device(app_client)
        unclaimed = await _for_you(app_client, headers, token)
        assert unclaimed.status_code == 403, unclaimed.text
        assert unclaimed.json()["detail"]["reason"] == "device_unclaimed"

        owner = await _confirmed_device(app_client, registered_supabase_user)
        stranger_token, _ = await registered_supabase_user()
        foreign = await _for_you(app_client, owner["headers"], stranger_token)
        assert foreign.status_code == 403, foreign.text
        assert foreign.json()["detail"]["reason"] == "device_claimed_by_another_account"

    async def test_a_capture_attributed_to_another_account_is_refused(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Owning the device is not owning the capture on it."""
        owner = await _confirmed_device(app_client, registered_supabase_user)
        _, stranger_id = await registered_supabase_user()
        factory = get_sessionmaker()
        async with factory() as session:
            await session.execute(
                update(ScanEvent)
                .where(ScanEvent.barcode == BARCODE)
                .values(account_id=stranger_id)
            )
            await session.commit()
        response = await _for_you(app_client, owner["headers"], owner["token"])
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["reason"] == "current_pack_not_owned"


# ---------------------------------------------------------------------------
# 2. The label version behind the pack
# ---------------------------------------------------------------------------
class TestSnapshotResolution:
    async def test_a_capture_that_owns_its_snapshot_resolves_to_exactly_that_row(
        self, db_clean, app_client, registered_supabase_user,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            from app.domains.product import pack_context

            resolved_pack = await pack_context.current_pack(
                session, barcode=BARCODE, device_id=owner["device_id"],
            )
            snapshot = await resolve_current_pack_label_snapshot(session, pack=resolved_pack)
        assert str(snapshot.id) == owner["capture"]["label_snapshot"]["id"]
        assert snapshot.scan_event_id == resolved_pack.scan_event.id

    async def test_a_deduplicated_capture_resolves_to_the_version_that_holds_it(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The second confirmation writes no snapshot, and still has one."""
        owner = await _confirmed_device(app_client, registered_supabase_user)
        first_snapshot_id = owner["capture"]["label_snapshot"]["id"]
        again = await _confirm(
            app_client, owner["headers"], owner["token"], account_id=owner["account_id"],
        )
        assert again["created"] is True
        assert again["label_snapshot"]["id"] == first_snapshot_id

        body = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        assert body["pack"]["is_proven"] is True
        assert body["pack"]["label_snapshot_id"] == first_snapshot_id
        # The two identities are different facts and stay different in the JSON.
        assert body["pack"]["current_pack_scan_id"] == again["scan_id"]
        assert body["pack"]["label_snapshot_source_scan_id"] != again["scan_id"]
        assert body["pack"]["label_snapshot_source_scan_id"] == owner["capture"]["scan_id"]

    async def test_a_future_capture_cannot_rewrite_an_earlier_packs_provenance(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """The mandatory adversarial case.

        Device A confirms formula A twice — the second deduplicates. Device B
        then publishes formula B and formula A again, so a *newer* snapshot
        with formula A's exact fingerprint now exists. Device A's pack was
        confirmed before that row existed, and must not be attributed to it.
        """
        owner = await _confirmed_device(app_client, registered_supabase_user)
        v1_id = owner["capture"]["label_snapshot"]["id"]
        a2 = await _confirm(
            app_client, owner["headers"], owner["token"], account_id=owner["account_id"],
        )

        other_token, other_account = await registered_supabase_user()
        other_headers, _ = await _register_device(app_client)
        await _claim(app_client, other_headers, other_token)
        v2 = await _confirm(
            app_client, other_headers, other_token,
            ingredients="Glycerin", account_id=other_account,
        )
        v3 = await _confirm(
            app_client, other_headers, other_token,
            ingredients=PETROLATUM, account_id=other_account,
        )
        assert v2["label_snapshot"]["version_number"] == 2
        assert v3["label_snapshot"]["version_number"] == 3
        assert v3["label_snapshot"]["id"] != v1_id

        body = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        assert body["pack"]["current_pack_scan_id"] == a2["scan_id"]
        assert body["pack"]["label_snapshot_id"] == v1_id
        assert body["pack"]["label_snapshot_version"] == 1
        assert body["pack"]["label_snapshot_id"] != v3["label_snapshot"]["id"]

    async def test_a_proven_pack_with_no_resolvable_version_fails_closed(
        self, db_clean, app_client, registered_supabase_user,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            snapshot = (await session.execute(select(LabelSnapshot))).scalar_one()
            snapshot.content_fingerprint = "0" * 64
            await session.commit()
        response = await _for_you(app_client, owner["headers"], owner["token"])
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "FEATURE_UNAVAILABLE"
        assert "action" not in response.text

    async def test_the_resolver_refuses_a_snapshot_that_no_longer_matches(
        self, db_clean, app_client, registered_supabase_user,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            from app.domains.product import pack_context

            resolved_pack = await pack_context.current_pack(
                session, barcode=BARCODE, device_id=owner["device_id"],
            )
            snapshot = (await session.execute(select(LabelSnapshot))).scalar_one()
            snapshot.facts = {**snapshot.facts, "ingredients_text": "Glycerin"}
            await session.flush()
            with pytest.raises(CurrentPackSnapshotUnresolved):
                await resolve_current_pack_label_snapshot(session, pack=resolved_pack)
            await session.rollback()


# ---------------------------------------------------------------------------
# 3. Category comes from the snapshot and nowhere else
# ---------------------------------------------------------------------------
class TestCategoryAuthority:
    async def test_a_confirmed_skin_care_pack_is_answered_under_skin_care(
        self, db_clean, app_client, registered_supabase_user,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        body = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        assert body["product_category"] == "skin_care"

    @pytest.mark.parametrize(
        "category",
        ["Skin Care", "skin-care", " skin_care ", "hair_care", "packaged_food", None],
    )
    async def test_no_other_category_becomes_skin_care(
        self, db_clean, app_client, registered_supabase_user, category,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            snapshot = (await session.execute(select(LabelSnapshot))).scalar_one()
            facts = dict(snapshot.facts)
            if category is None:
                facts.pop("product_category", None)
            else:
                facts["product_category"] = category
            snapshot.facts = facts
            snapshot.content_fingerprint = service.label_content_fingerprint(facts)
            event = (await session.execute(
                select(ScanEvent).where(ScanEvent.outcome == service.OUTCOME_LABEL)
            )).scalar_one()
            event.label_facts = facts
            await session.commit()

        body = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        assert body["result"]["status"] == api.STATUS_PACK_CATEGORY_NOT_SUPPORTED
        assert body["result"]["reason_key"] == for_you_copy.REASON_KEY_PACK_CATEGORY
        assert body["product_category"] is None
        assert body["pack"]["is_proven"] is True
        _assert_no_decision(body)


# ---------------------------------------------------------------------------
# 4. Safety: structured in, canonical out, nothing kept
# ---------------------------------------------------------------------------
class TestStructuredSafety:
    @pytest.mark.parametrize(
        ("safety", "expected_reason"),
        [
            ({"pregnancy": True}, "pregnancy"),
            ({"breastfeeding": True}, "breastfeeding"),
            ({"medication_involved": True}, "medication"),
            ({"diagnosed_condition_involved": True}, "clinical_condition"),
            ({"subject_is_child": True}, "child_subject"),
            ({"stated_age": 11}, "age_under_minimum"),
        ],
    )
    async def test_each_structured_flag_hands_over_to_a_clinician(
        self, db_clean, app_client, registered_supabase_user, safety, expected_reason,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        response = await _for_you(
            app_client, owner["headers"], owner["token"], safety=safety,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["result"]["status"] == "handoff_required"
        assert body["result"]["handoff"]["reason"] == expected_reason
        assert body["result"]["handoff"]["message"].strip()
        assert body["result"]["reason_text"] == body["result"]["handoff"]["message"]
        assert body["release"] is None
        _assert_no_decision(body)

    async def test_the_handoff_message_is_the_authoritys_own_words(
        self, db_clean, app_client, registered_supabase_user,
    ):
        from app.domains.routines.hard_handoff import HANDOFF_MESSAGES, HandoffReason

        owner = await _confirmed_device(app_client, registered_supabase_user)
        body = (await _for_you(
            app_client, owner["headers"], owner["token"], safety={"pregnancy": True},
        )).json()
        assert body["result"]["handoff"]["message"] == HANDOFF_MESSAGES[HandoffReason.PREGNANCY]

    async def test_age_twelve_alone_does_not_hand_over(
        self, db_clean, app_client, registered_supabase_user,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        body = (await _for_you(
            app_client, owner["headers"], owner["token"], safety={"stated_age": 12},
        )).json()
        assert body["result"]["status"] != "handoff_required"

    async def test_the_response_never_echoes_the_safety_state(
        self, db_clean, app_client, registered_supabase_user,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        body = (await _for_you(
            app_client, owner["headers"], owner["token"],
            safety={"pregnancy": True, "stated_age": 34},
        )).json()
        # Structural: no key names the safety state and no value carries it.
        for path, value in _values(body):
            assert "safety" not in path
            assert "pregnan" not in path
            assert "stated_age" not in path
            assert "medication" not in path
            assert value != 34
        assert "safety" not in json.dumps(body)

    async def test_safety_state_is_never_written_down(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """No table, no analytics row, no audit payload holds these flags."""
        owner = await _confirmed_device(app_client, registered_supabase_user)
        from app.shared.database.registry import Base

        factory = get_sessionmaker()
        async with factory() as session:
            before = {
                table.name: (await session.execute(
                    select(func.count()).select_from(table)
                )).scalar_one()
                for table in Base.metadata.sorted_tables
            }
        await _for_you(
            app_client, owner["headers"], owner["token"],
            safety={"pregnancy": True, "medication_involved": True, "stated_age": 30},
        )
        async with factory() as session:
            after = {
                table.name: (await session.execute(
                    select(func.count()).select_from(table)
                )).scalar_one()
                for table in Base.metadata.sorted_tables
            }
        assert before == after

    @pytest.mark.parametrize(
        "safety",
        [
            {"pregnancy": "true"}, {"pregnancy": 1}, {"pregnancy": "yes"},
            {"breastfeeding": 0}, {"medication_involved": "false"},
            {"subject_is_child": "1"},
            {"stated_age": "11"}, {"stated_age": 11.0}, {"stated_age": -1},
            {"stated_age": 121},
        ],
    )
    async def test_safety_state_is_never_coerced(
        self, db_clean, app_client, registered_supabase_user, safety,
    ):
        """Whether somebody is pregnant is not a field to be lenient about."""
        owner = await _confirmed_device(app_client, registered_supabase_user)
        response = await _for_you(
            app_client, owner["headers"], owner["token"], safety=safety,
        )
        assert response.status_code == 422, (safety, response.text)

    @pytest.mark.parametrize(
        "safety",
        [
            {"text": "I take metformin"},
            {"notes": "eczema"},
            {"message": "hello"},
            {"condition": "pcos"},
            {"condition_name": "pcos"},
            {"diagnosis": "pcos"},
            {"medication_name": "metformin"},
            {"medicine": "metformin"},
            {"drug_name": "metformin"},
        ],
    )
    async def test_no_named_medicine_or_diagnosis_can_be_sent(
        self, db_clean, app_client, registered_supabase_user, safety,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        response = await _for_you(
            app_client, owner["headers"], owner["token"], safety=safety,
        )
        assert response.status_code == 422, (safety, response.text)

    def test_the_adapter_only_speaks_for_flags_that_are_true(self):
        adapt = api.personal_lens_safety_input
        assert adapt(None) is None
        none_stated = adapt(api.StructuredSafetyContext())
        assert none_stated.text is None
        assert none_stated.stated_age is None
        assert none_stated.subject_is_child is False
        # False is "not stated", never an assertion to the contrary.
        explicit_false = adapt(api.StructuredSafetyContext(pregnancy=False))
        assert explicit_false.text is None
        both = adapt(api.StructuredSafetyContext(
            pregnancy=True, medication_involved=True, stated_age=30,
        ))
        assert "pregnancy" in both.text
        assert "medication" in both.text
        assert both.stated_age == 30


# ---------------------------------------------------------------------------
# 5. Release loading, and the states around it
# ---------------------------------------------------------------------------
class TestReleaseAuthority:
    async def test_no_active_release_means_no_verdict_and_no_fallback(
        self, db_clean, app_client, registered_supabase_user,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_identity(session)
            await _publish_applicability(session)
            await _profile(session, owner["account_id"])
            await session.commit()
            assert await load_active_personal_decision_release(session) is None

        body = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        assert body["release"] == {"id": None, "version": None, "content_hash": None} or (
            body["release"] is None
        )
        _assert_no_decision(body)

    async def test_a_corrupt_active_release_is_not_the_same_as_none(
        self, db_clean, app_client, registered_supabase_user,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            await _qualified_release(session)
            await _profile(session, owner["account_id"])
            await session.commit()
            await session.execute(
                update(PersonalDecisionRelease).values(content_hash="f" * 64)
            )
            await session.commit()

        response = await _for_you(app_client, owner["headers"], owner["token"])
        assert response.status_code == 503, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "FEATURE_UNAVAILABLE"
        # None of the manifest, the hash or the rule identities reaches anybody.
        for leak in ("manifest", "content_hash", "rule_id", "semantic", "policy"):
            assert leak not in response.text.lower()

    async def test_a_hard_handoff_never_waits_on_a_healthy_release(
        self, db_clean, app_client, registered_supabase_user, monkeypatch,
    ):
        """The one answer this product owes unconditionally.

        The release loader is replaced with one that raises if it is called at
        all, so the test proves the handoff path skips it entirely rather than
        merely surviving it.
        """
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            await _qualified_release(session)
            await _profile(session, owner["account_id"])
            await session.commit()
            await session.execute(
                update(PersonalDecisionRelease).values(content_hash="f" * 64)
            )
            await session.commit()

        called: list[int] = []

        async def forbidden(*args, **kwargs):
            called.append(1)
            raise AssertionError("the release loader ran before the safety handoff")

        monkeypatch.setattr(api, "load_active_personal_decision_release", forbidden)
        response = await _for_you(
            app_client, owner["headers"], owner["token"], safety={"pregnancy": True},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["result"]["status"] == "handoff_required"
        assert body["result"]["handoff"]["reason"] == "pregnancy"
        assert body["release"] is None
        assert called == []
        _assert_no_decision(body)

    async def test_deactivating_the_release_withdraws_the_verdict(
        self, db_clean, app_client, registered_supabase_user,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            qualified = await _qualified_release(session)
            await _profile(session, owner["account_id"])
            await session.commit()

        first = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        assert first["result"]["action"] == "buy"

        async with factory() as session:
            await release_authoring.deactivate_personal_decision_release(
                session, uuid.UUID(qualified["activated"]["id"]), actor="step8k.operator",
            )
            await session.commit()

        second = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        _assert_no_decision(second)
        assert second["release"] in (None, {"id": None, "version": None, "content_hash": None})


# ---------------------------------------------------------------------------
# 6. The decisive end-to-end case
# ---------------------------------------------------------------------------
class TestPositiveDecision:
    async def test_the_whole_chain_answers_with_a_reviewed_sourced_verdict(
        self, db_clean, app_client, registered_supabase_user, fake_provider, media_root,
    ):
        """Camera to customer sentence, with only the model boundary mocked.

        Everything after the gateway result is the real authority: the real
        transcription and confirmation routes, the real pack context, the real
        snapshot resolver, the real Step 8A/8B, a real Step 8G publication, a
        real Step 8H release compiled from the Step 8I pack and activated
        through its own governance, the real Steps 8C–8F, and the reviewed copy
        catalogue.
        """
        fake_provider.text = json.dumps({
            "product_name": "Synthetic Petrolatum Ointment",
            "brand": "Synthetic Brand",
            "ingredients_text": PETROLATUM,
            "confidence": 0.93,
        })
        token, account_id = await registered_supabase_user()
        headers, device_id = await _register_device(app_client)
        await _claim(app_client, headers, token)

        asset = await app_client.post(
            "/api/v2/media/upload", headers=auth(token),
            files={"file": ("label.png", png_bytes(), "image/png")},
        )
        assert asset.status_code in (200, 201), asset.text
        draft = await app_client.post(
            TRANSCRIBE_URL, headers=auth(token),
            json={"barcode": BARCODE, "media_asset_id": asset.json()["id"]},
        )
        assert draft.status_code == 200, draft.text
        confirmed = await app_client.post(
            CONFIRM_URL, headers={**headers, **auth(token)},
            json={
                "barcode": BARCODE,
                "ai_run_id": draft.json()["provenance"]["ai_run_id"],
                "client_scan_id": uuid.uuid4().hex,
            },
        )
        assert confirmed.status_code == 201, confirmed.text
        capture = confirmed.json()

        factory = get_sessionmaker()
        async with factory() as session:
            qualified = await _qualified_release(session)
            await _profile(session, account_id)
            await session.commit()
        activated = qualified["activated"]

        response = await _for_you(app_client, headers, token)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["barcode"] == BARCODE
        assert body["product_category"] == "skin_care"
        assert body["copy_version"] == "for-you-copy.v1"

        assert body["pack"]["is_proven"] is True
        assert body["pack"]["current_pack_scan_id"] == capture["scan_id"]
        assert body["pack"]["label_snapshot_id"] == capture["label_snapshot"]["id"]
        assert body["pack"]["label_snapshot_source_scan_id"] == capture["scan_id"]
        assert body["pack"]["label_snapshot_version"] == 1
        assert body["pack"]["content_fingerprint"] == capture["label_snapshot"]["content_fingerprint"]

        result = body["result"]
        assert result["status"] == "decision_presentable"
        assert result["reason"] == "reviewed_explanation_available"
        assert result["action"] == "buy"
        assert result["verdict_key"] == "for_you.verdict.buy"
        assert result["verdict_text"] == "BUY"
        assert result["reason_key"] == pack.REASON_KEY
        assert result["reason_text"] == (
            "For dry skin, dermatologist guidance includes petrolatum among ingredients to "
            "look for in a cream or ointment."
        )
        assert result["handoff"] is None

        citation = result["citation"]
        assert citation["title"] == pack.AAD_SOURCE_TITLE
        assert citation["publisher"] == pack.AAD_SOURCE_PUBLISHER
        assert citation["canonical_url"] == pack.AAD_SOURCE_URL
        assert citation["locator"] == pack.AAD_SOURCE_LOCATOR
        assert citation["publication_date"] is None
        assert citation["version_or_revision"] == pack.AAD_SOURCE_VERSION
        assert citation["jurisdiction"] is None

        assert body["release"]["id"] == activated["id"]
        assert body["release"]["version"] == activated["release_version"]
        assert body["release"]["content_hash"] == activated["content_hash"]

        # Implementation topology stays inside. The customer gets a decision,
        # a reason and a source.
        rendered = json.dumps(body)
        for leak in (
            "semantic_rule_id", "policy_id", "claim_key", "claim_version",
            "evidence_strength", "signal_set", "manifest", "profile_id",
            "profile_attribute_id", "care_skin_usual_feel", "care_skin_sensitivity",
            pack.EVIDENCE_SUMMARY, pack.EVIDENCE_SCOPE,
        ):
            assert leak not in rendered, leak

    async def test_changing_the_persons_skin_changes_the_answer_without_rescanning(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Product evidence is snapshot-bound. Personal context is live."""
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            await _qualified_release(session)
            await _profile(session, owner["account_id"])
            await session.commit()

        first = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        assert first["result"]["action"] == "buy"

        async with factory() as session:
            await session.execute(
                update(ProfileAttribute)
                .where(ProfileAttribute.key == pack.FACT_KEY)
                .values(value="comfortable")
            )
            await session.commit()

        second = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        _assert_no_decision(second)
        # Same pack, same snapshot: only the person changed.
        assert second["pack"]["label_snapshot_id"] == first["pack"]["label_snapshot_id"]

    async def test_partial_personal_context_withholds_the_decision(
        self, db_clean, app_client, registered_supabase_user,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            await _qualified_release(session)
            await _profile(session, owner["account_id"], usual_feel="comfortable")
            await session.commit()
        body = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        _assert_no_decision(body)
        assert body["result"]["reason_text"] == for_you_copy.FOR_YOU_REASON_COPY[
            body["result"]["reason_key"]
        ]

    async def test_an_unknown_second_ingredient_withholds_the_decision(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """Step 8C–8E gap rules stay the authority; petrolatum is not special-cased."""
        owner = await _confirmed_device(
            app_client, registered_supabase_user,
            ingredients="Petrolatum, Completely Unknown Ingredient",
        )
        factory = get_sessionmaker()
        async with factory() as session:
            await _qualified_release(session)
            await _profile(session, owner["account_id"])
            await session.commit()
        body = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        _assert_no_decision(body)

    async def test_superseded_evidence_withdraws_the_verdict(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """A release names an exact claim version. A newer one is not a substitute."""
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_identity(session)
            published = await _publish_applicability(session)
            await _activate(session, published)
            await _profile(session, owner["account_id"])
            await session.commit()

        first = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        assert first["result"]["action"] == "buy"

        async with factory() as session:
            await _publish_applicability(
                session, entry_id=uuid.UUID(published["id"]),
                existing_sources=list(published["sources"]),
            )
            await session.commit()

        second = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        _assert_no_decision(second)

    async def test_a_retired_source_withdraws_the_citation_and_the_verdict(
        self, db_clean, app_client, registered_supabase_user,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            await _qualified_release(session)
            await _profile(session, owner["account_id"])
            await session.commit()

        first = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        assert first["result"]["action"] == "buy"

        async with factory() as session:
            await session.execute(
                update(EvidenceSource)
                .where(EvidenceSource.canonical_url == pack.AAD_SOURCE_URL)
                .values(status=SourceStatus.RETIRED.value)
            )
            await session.commit()

        second = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        _assert_no_decision(second)


# ---------------------------------------------------------------------------
# 7. Copy is the second gate
# ---------------------------------------------------------------------------
class TestCustomerCopyGate:
    def test_the_reviewed_reason_matches_the_step8i_review_exactly(self):
        """Pinned against the pack, without the runtime ever importing it."""
        assert for_you_copy.FOR_YOU_REASON_COPY[pack.REASON_KEY] == pack.FUTURE_REASON_INTENT

    def test_the_customer_reason_makes_none_of_the_out_of_scope_claims(self):
        sentence = for_you_copy.FOR_YOU_REASON_COPY[pack.REASON_KEY].lower()
        for claim in pack.REASON_CLAIMS_OUT_OF_SCOPE:
            assert claim.lower() not in sentence, claim

    def test_the_catalogue_carries_every_structural_key_step8f_can_emit(self):
        from app.domains.personal_decision_explanation import service as explanation

        for key in (
            explanation.REASON_KEY_PERSONAL_CONTEXT,
            explanation.REASON_KEY_FORMULA,
            explanation.REASON_KEY_SEMANTIC_MAPPING,
            explanation.REASON_KEY_DECISION_POLICY,
            explanation.REASON_KEY_EXPLANATION,
        ):
            assert for_you_copy.reason_text(key), key

    def test_no_sentence_characterises_a_product_or_a_body(self):
        banned = (
            "bad product", "unsafe", "dangerous", "toxic", "healthy",
            "unhealthy", "good for you", "harmful", "cure", "treat",
        )
        for key, sentence in for_you_copy.FOR_YOU_REASON_COPY.items():
            lowered = sentence.lower()
            for word in banned:
                assert word not in lowered, (key, word)

    def test_key_lookups_are_exact_and_never_fall_back(self):
        assert for_you_copy.reason_text("for_you.not_enough") is None
        assert for_you_copy.reason_text("For_You.Not_Enough.Copy") is None
        assert for_you_copy.reason_text(" for_you.not_enough.copy ") is None
        assert for_you_copy.verdict_text("for_you.verdict.BUY") is None
        assert for_you_copy.verdict_text(None) is None

    async def test_an_unreviewed_reason_key_hides_the_whole_decision(
        self, db_clean, app_client, registered_supabase_user,
    ):
        """A release can be activated before anybody writes the customer wording.

        Release governance owns the rules and the sources; it does not own the
        sentence. When the two disagree the customer sees nothing — not the
        action, not the verdict, and not the citation, because a source beside
        a withheld verdict still reveals what we were about to say.
        """
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_identity(session)
            published = await _publish_applicability(session)
            manifest = pack.build_release_manifest_from_published_entry(published)
            manifest["explanation_rules"][0]["reason_key"] = (
                "for_you.synthetic.not_reviewed_for_customer_copy"
            )
            await _activate(session, published, manifest=manifest)
            await _profile(session, owner["account_id"])
            await session.commit()

        body = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        assert body["result"]["status"] == api.STATUS_NOT_ENOUGH_COPY
        assert body["result"]["reason_key"] == for_you_copy.REASON_KEY_NO_COPY
        assert body["result"]["reason_text"] == (
            "We haven't finished reviewing the wording for this result yet."
        )
        _assert_no_decision(body)
        # The release that was consulted is still named, so the gap is traceable.
        assert body["release"]["id"] is not None


# ---------------------------------------------------------------------------
# 8. The endpoint reads, and reads the same way twice
# ---------------------------------------------------------------------------
class TestReadOnlyAndDeterministic:
    async def test_calling_the_endpoint_writes_nothing_at_all(
        self, db_clean, app_client, registered_supabase_user,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            await _qualified_release(session)
            await _profile(session, owner["account_id"])
            await session.commit()
        from app.shared.database.registry import Base

        async def counts() -> dict[str, int]:
            async with factory() as session:
                return {
                    table.name: (await session.execute(
                        select(func.count()).select_from(table)
                    )).scalar_one()
                    for table in Base.metadata.sorted_tables
                }

        before = await counts()
        await _for_you(app_client, owner["headers"], owner["token"])
        await _for_you(app_client, owner["headers"], owner["token"])
        assert await counts() == before

    async def test_two_identical_calls_return_the_same_answer(
        self, db_clean, app_client, registered_supabase_user,
    ):
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            await _qualified_release(session)
            await _profile(session, owner["account_id"])
            await session.commit()
        first = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        second = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        assert first == second
        assert first["result"]["action"] == "buy"

    async def test_the_decision_needs_no_model_and_no_network(
        self, db_clean, app_client, registered_supabase_user, monkeypatch,
    ):
        """Capture already happened. Deciding is arithmetic over reviewed rows."""
        owner = await _confirmed_device(app_client, registered_supabase_user)
        factory = get_sessionmaker()
        async with factory() as session:
            await _qualified_release(session)
            await _profile(session, owner["account_id"])
            await session.commit()

        async def forbidden(*args, **kwargs):
            raise AssertionError("the FOR YOU endpoint reached an external service")

        monkeypatch.setattr("app.domains.ai_gateway.gateway.run_structured", forbidden)
        monkeypatch.setattr("app.domains.ai_gateway.providers.gemini.generate", forbidden)
        monkeypatch.setattr("app.domains.off.client.fetch_product", forbidden)

        body = (await _for_you(app_client, owner["headers"], owner["token"])).json()
        assert body["result"]["action"] == "buy"


# ---------------------------------------------------------------------------
# 9. Static boundaries
# ---------------------------------------------------------------------------
class TestStaticBoundaries:
    def test_the_request_schema_lets_the_client_choose_nothing(self):
        banned = {
            "category", "product_category", "decision_category", "personal_category",
            "interpretation_category", "label_snapshot_id", "scan_event_id",
            "version_number", "content_fingerprint", "release_id", "release_version",
            "release_hash", "manifest", "action", "verdict", "verdict_key",
            "reason_key", "source_key", "ingredients_text",
        }
        for name in ("SkinCareForYouBody", "StructuredSafetyContext"):
            declared = set(getattr(api, name).model_fields)
            assert not (declared & banned), (name, declared & banned)
        assert set(api.SkinCareForYouBody.model_fields) == {"barcode", "safety"}

    def test_the_safety_schema_collects_no_free_text(self):
        banned = {
            "text", "notes", "message", "condition", "condition_name", "diagnosis",
            "medication_name", "medicine", "drug_name", "medical_notes", "free_text",
        }
        declared = set(api.StructuredSafetyContext.model_fields)
        assert not (declared & banned), declared & banned
        assert declared == {
            "pregnancy", "breastfeeding", "medication_involved",
            "diagnosed_condition_involved", "subject_is_child", "stated_age",
        }

    def test_no_route_signature_takes_a_category_snapshot_or_release(self):
        """Only the HTTP surface is checked. The internal builder is handed a
        category *derived* from the snapshot, which is the whole point of it."""
        banned = {
            "category", "product_category", "decision_category", "label_snapshot_id",
            "scan_event_id", "release_id", "release_version", "content_fingerprint",
        }
        routes = [
            node for node in ast.walk(_module_tree(API_PATH))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "router"
                for decorator in node.decorator_list
            )
        ]
        assert routes, "no route handler found to check"
        for node in routes:
            args = node.args
            names = {arg.arg for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
            assert not (names & banned), (node.name, names & banned)
            assert names == {"body", "device", "current", "session"}, node.name

    def test_step8k_never_imports_the_knowledge_pack(self):
        for path in STEP8K_MODULES:
            for module in _imported_modules(path):
                assert not module.startswith("app.knowledge_packs"), (path.name, module)

    def test_step8k_never_imports_a_model_or_a_network_client(self):
        for path in STEP8K_MODULES:
            for module in _imported_modules(path):
                for banned in ("ai_gateway", "gemini", "domains.off", "httpx", "requests"):
                    assert banned not in module, (path.name, module)

    def test_step8k_never_touches_the_legacy_care_verdict_systems(self):
        for path in STEP8K_MODULES:
            for module in _imported_modules(path):
                for banned in ("purchase", "recommendation", "alternatives", "value"):
                    assert not module.startswith(f"app.domains.{banned}"), (path.name, module)

    def test_the_orchestration_never_reaches_for_the_global_latest_snapshot(self):
        """``latest_label_snapshot`` answers a question about the product, not the hand."""
        for path in (API_PATH, RESOLVER_PATH):
            names = {
                node.attr for node in ast.walk(_module_tree(path))
                if isinstance(node, ast.Attribute)
            } | {
                node.id for node in ast.walk(_module_tree(path)) if isinstance(node, ast.Name)
            }
            assert "latest_label_snapshot" not in names, path.name
            assert "latest_label_snapshots" not in names, path.name

    def test_no_step8k_module_maps_a_signal_to_an_action(self):
        for path in STEP8K_MODULES:
            for node in ast.walk(_module_tree(path)):
                if not isinstance(node, ast.Dict):
                    continue
                pairs = {
                    key.value: value.value
                    for key, value in zip(node.keys, node.values, strict=True)
                    if isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                }
                for signal, action in (
                    ("supporting", "buy"), ("cautionary", "skip"), ("mixed", "wait"),
                ):
                    assert pairs.get(signal) != action, (path.name, signal, action)

    def test_the_copy_module_cannot_see_anything_it_could_decide_from(self):
        """It receives keys. It resolves keys. It is given nothing else."""
        tree = _module_tree(COPY_PATH)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                names = {
                    arg.arg for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]
                }
                assert names <= {"verdict_key", "reason_key"}, (node.name, names)
        assert _imported_modules(COPY_PATH) == {"__future__", "__future__.annotations"}

    def test_the_copy_version_is_pinned(self):
        assert for_you_copy.FOR_YOU_COPY_VERSION == "for-you-copy.v1"
        assert {
            "for_you.verdict.buy": "BUY",
            "for_you.verdict.wait": "WAIT",
            "for_you.verdict.skip": "SKIP",
        } == for_you_copy.FOR_YOU_VERDICT_COPY

    def test_the_api_states_are_only_the_three_this_layer_owns(self):
        assert api.STATUS_PACK_NOT_CONFIRMED == "pack_not_confirmed"
        assert api.STATUS_PACK_CATEGORY_NOT_SUPPORTED == "pack_category_not_supported"
        assert api.STATUS_NOT_ENOUGH_COPY == "not_enough_copy"


# ---------------------------------------------------------------------------
# 10. Production holds
# ---------------------------------------------------------------------------
class TestProductionHold:
    def test_the_three_registries_are_still_empty(self):
        assert PERSONAL_DECISION_SEMANTIC_RULES == ()
        assert PERSONAL_DECISION_POLICY_RULES == ()
        assert PERSONAL_DECISION_EXPLANATION_RULES == ()

    async def test_the_ordinary_seed_activates_nothing(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await run_reference_seed(session)
            claims = (await session.execute(
                select(func.count()).select_from(EvidenceClaim)
                .where(EvidenceClaim.claim_key.like("personal-applicability:%"))
            )).scalar_one()
            releases = (await session.execute(
                select(func.count()).select_from(PersonalDecisionRelease)
            )).scalar_one()
            active = (await session.execute(
                select(func.count()).select_from(PersonalDecisionRelease)
                .where(PersonalDecisionRelease.status == "active")
            )).scalar_one()
        assert (claims, releases, active) == (0, 0, 0)
