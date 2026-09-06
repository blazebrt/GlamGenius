"""Step 8I — first governed production knowledge pack qualification."""

from __future__ import annotations

import ast
import copy
import json
import subprocess
import uuid
from datetime import date
from pathlib import Path

import pytest
from app.bootstrap import run as run_reference_seed
from app.domains.evidence import authoring as evidence_authoring
from app.domains.evidence.enums import EvidenceStrength, SourceStatus, SourceType
from app.domains.evidence.models import EvidenceClaim, EvidenceSource
from app.domains.identity import service as identity_service
from app.domains.personal_applicability import authoring as applicability_authoring
from app.domains.personal_applicability.enums import PersonalApplicabilityCategory
from app.domains.personal_applicability.service import interpret_label_snapshot_for_account
from app.domains.personal_decision_explanation import (
    PERSONAL_DECISION_EXPLANATION_RULES,
    PersonalDecisionPresentationStatus,
)
from app.domains.personal_decision_policy import (
    PERSONAL_DECISION_POLICY_RULES,
    PersonalDecisionAction,
)
from app.domains.personal_decision_release import authoring as release_authoring
from app.domains.personal_decision_release.manifest import (
    manifest_content_hash,
    parse_release_manifest,
)
from app.domains.personal_decision_release.models import PersonalDecisionRelease
from app.domains.personal_decision_release.runtime import (
    evaluate_personal_decision_with_release,
    load_active_personal_decision_release,
)
from app.domains.personal_decision_release.validation import ReleaseVerification
from app.domains.personal_decision_semantics import PERSONAL_DECISION_SEMANTIC_RULES
from app.domains.personal_lens.enums import PersonalLensStatus
from app.domains.personal_lens.service import PersonalLensSafetyInput
from app.domains.product.confidence import ProductConfidence
from app.domains.product.models import LabelSnapshot
from app.domains.profile.models import AppearanceProfile, ProfileAttribute
from app.domains.substances import authoring as substance_authoring
from app.domains.substances.enums import EntityKind, NameNamespace
from app.domains.substances.models import Substance, SubstanceName
from app.knowledge_packs import petrolatum_dry_skin_v1 as pack
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import delete, func, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent

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


def _source(
    *,
    aad: bool,
    source_key: str | None = None,
) -> dict[str, object]:
    if aad:
        return {
            "source_id": str(uuid.uuid4()),
            "source_key": source_key or "generated.aad.source",
            "source_type": pack.AAD_SOURCE_TYPE,
            "title": pack.AAD_SOURCE_TITLE,
            "publisher": pack.AAD_SOURCE_PUBLISHER,
            "canonical_url": pack.AAD_SOURCE_URL,
            "locator": pack.AAD_SOURCE_LOCATOR,
            "publication_date": pack.AAD_SOURCE_PUBLICATION_DATE,
            "version_or_revision": pack.AAD_SOURCE_VERSION,
            "jurisdiction": pack.AAD_SOURCE_JURISDICTION,
            "status": "active",
            "license_or_use_note": pack.AAD_SOURCE_USE_NOTE,
            "reviewed_by": "reviewer",
            "reviewed_at": "2026-09-07T00:00:00+00:00",
        }
    return {
        "source_id": str(uuid.uuid4()),
        "source_key": source_key or "generated.pubmed.source",
        "source_type": pack.PUBMED_SOURCE_TYPE,
        "title": pack.PUBMED_SOURCE_TITLE,
        "publisher": pack.PUBMED_SOURCE_PUBLISHER,
        "canonical_url": pack.PUBMED_SOURCE_URL,
        "locator": pack.PUBMED_SOURCE_LOCATOR,
        "publication_date": pack.PUBMED_SOURCE_PUBLICATION_DATE,
        "version_or_revision": pack.PUBMED_SOURCE_VERSION,
        "jurisdiction": pack.PUBMED_SOURCE_JURISDICTION,
        "status": "active",
        "license_or_use_note": pack.PUBMED_SOURCE_USE_NOTE,
        "reviewed_by": "reviewer",
        "reviewed_at": "2026-09-07T00:00:00+00:00",
    }


def _valid_entry(
    *,
    claim_key: str = "personal-applicability:skin_care:generated",
    aad_source_key: str = "generated.aad.source",
    pubmed_source_key: str = "generated.pubmed.source",
) -> dict[str, object]:
    return {
        "id": str(uuid.uuid4()),
        "claim_key": claim_key,
        "claim_version": 1,
        "category": pack.CATEGORY,
        "domain": pack.DOMAIN,
        "substance_key": pack.SUBSTANCE_KEY,
        "subject_type": "substance",
        "claim_type": "substance_personal_applicability",
        "summary": pack.EVIDENCE_SUMMARY,
        "scope": pack.EVIDENCE_SCOPE,
        "evidence_strength": pack.EVIDENCE_STRENGTH,
        "strength_rationale": pack.EVIDENCE_STRENGTH_RATIONALE,
        "evidence_tier": "clinically_studied",
        "review_status": "published",
        "claim_status": "supported",
        "ai_generated": False,
        "conditions": [
            {
                "fact_key": pack.FACT_KEY,
                "operator": pack.FACT_OPERATOR,
                "values": list(pack.FACT_VALUES),
            }
        ],
        "sources": [
            _source(aad=True, source_key=aad_source_key),
            _source(aad=False, source_key=pubmed_source_key),
        ],
        "verification": {},
        "reviewed_by": "reviewer",
        "reviewed_at": "2026-09-07T00:00:00+00:00",
        "published_by": "publisher",
        "published_at": "2026-09-07T00:00:00+00:00",
        "supersedes_claim_id": None,
        "rejection_reason": None,
    }


class TestCompiler:
    def test_valid_entry_builds_the_exact_reviewed_manifest(self) -> None:
        manifest = pack.build_release_manifest_from_published_entry(_valid_entry())
        assert len(manifest["semantic_rules"]) == 1
        assert len(manifest["policy_rules"]) == 1
        assert len(manifest["explanation_rules"]) == 1
        semantic = manifest["semantic_rules"][0]
        policy = manifest["policy_rules"][0]
        explanation = manifest["explanation_rules"][0]
        assert semantic == {
            "rule_id": pack.SEMANTIC_RULE_ID,
            "rule_version": "1",
            "category": "skin_care",
            "substance_key": "petrolatum",
            "claim_key": "personal-applicability:skin_care:generated",
            "claim_version": 1,
            "signal": "supporting",
        }
        assert policy["semantic_rule_identities"] == [
            {"rule_id": pack.SEMANTIC_RULE_ID, "rule_version": "1"}
        ]
        assert policy["signal_set"] == "supporting_only"
        assert policy["action"] == "buy"
        assert policy["has_identity_unresolved"] is False
        assert policy["has_identity_ambiguous"] is False
        assert policy["has_personal_evidence_gap"] is False
        assert explanation["action"] == "buy"
        assert explanation["source_key"] == "generated.aad.source"
        assert explanation["source_locator"] == pack.AAD_SOURCE_LOCATOR
        assert explanation["reason_key"] == pack.REASON_KEY

    def test_source_order_is_immaterial_to_manifest_and_hash(self) -> None:
        forward = _valid_entry()
        reverse = copy.deepcopy(forward)
        reverse["sources"].reverse()
        first = pack.build_release_manifest_from_published_entry(forward)
        second = pack.build_release_manifest_from_published_entry(reverse)
        assert first == second
        assert manifest_content_hash(parse_release_manifest(first)) == manifest_content_hash(
            parse_release_manifest(second)
        )

    def test_generated_claim_and_source_provenance_change_the_manifest_hash(self) -> None:
        first = pack.build_release_manifest_from_published_entry(_valid_entry())
        second = pack.build_release_manifest_from_published_entry(
            _valid_entry(claim_key="personal-applicability:skin_care:different")
        )
        third = pack.build_release_manifest_from_published_entry(
            _valid_entry(aad_source_key="generated.aad.different")
        )
        hashes = {
            manifest_content_hash(parse_release_manifest(value))
            for value in (first, second, third)
        }
        assert len(hashes) == 3

    @pytest.mark.parametrize(
        "mutation",
        [
            lambda e: e.update(review_status="draft"),
            lambda e: e.update(review_status="approved"),
            lambda e: e.update(review_status="superseded"),
            lambda e: e.update(claim_status="qualified"),
            lambda e: e.update(category="hair_care"),
            lambda e: e.update(domain="cosmetics"),
            lambda e: e.update(substance_key="glycerin"),
            lambda e: e.update(subject_type="formula"),
            lambda e: e.update(claim_type="substance_identity"),
            lambda e: e.update(evidence_tier="reference_data"),
            lambda e: e.update(ai_generated=True),
            lambda e: e.update(evidence_strength="strong"),
            lambda e: e.update(evidence_strength="limited"),
            lambda e: e.update(claim_version=2),
            lambda e: e.update(claim_key=" "),
            lambda e: e["conditions"][0].update(fact_key="care_skin_sensitivity"),
            lambda e: e["conditions"][0].update(operator="contains_any"),
            lambda e: e["conditions"][0].update(values=["comfortable"]),
            lambda e: e["conditions"][0].update(
                values=["often_dry_or_tight", "comfortable"]
            ),
            lambda e: e.update(conditions=[]),
            lambda e: e["conditions"].append(copy.deepcopy(e["conditions"][0])),
            lambda e: e.update(summary="Changed summary."),
            lambda e: e.update(scope="Changed scope."),
            lambda e: e.update(strength_rationale="Changed rationale."),
            lambda e: e["sources"].pop(0),
            lambda e: e["sources"].pop(1),
            lambda e: e["sources"].append(copy.deepcopy(e["sources"][0])),
            lambda e: e["sources"][0].update(source_type="official_guideline"),
            lambda e: e["sources"][1].update(source_type="systematic_review"),
            lambda e: e["sources"][0].update(title="Changed AAD title"),
            lambda e: e["sources"][1].update(publisher="Changed PubMed publisher"),
            lambda e: e["sources"][0].update(canonical_url="https://example.org/aad"),
            lambda e: e["sources"][1].update(canonical_url="https://example.org/pubmed"),
            lambda e: e["sources"][0].update(locator="Ointment or cream"),
            lambda e: e["sources"][1].update(locator="Abstract"),
            lambda e: e["sources"][0].update(status="unavailable"),
            lambda e: e["sources"][0].update(source_key=" "),
            lambda e: e.update(sources=[copy.deepcopy(e["sources"][0])] * 2),
            # Absent provenance must stay absent. Each of these is a plausible
            # inference someone could re-introduce, so each is rejected by name.
            lambda e: e["sources"][0].update(publication_date="2026-01-02"),
            lambda e: e["sources"][0].update(jurisdiction="global"),
            lambda e: e["sources"][1].update(jurisdiction="global"),
            lambda e: e["sources"][1].update(publisher="PubMed"),
            lambda e: e["sources"][0].update(version_or_revision="2026-01-02"),
            lambda e: e["sources"][0].update(jurisdiction="US"),
            lambda e: e["sources"][1].update(jurisdiction="international"),
        ],
    )
    def test_every_reviewed_boundary_fails_closed(self, mutation) -> None:
        entry = _valid_entry()
        mutation(entry)
        with pytest.raises(pack.FirstProductionKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)


def _generic_signal_action_mapping(source: str) -> None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
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
        if any(
            pairs.get(signal) == action
            for signal, action in (
                ("supporting", "buy"),
                ("cautionary", "skip"),
                ("mixed", "wait"),
            )
        ):
            raise AssertionError("generic signal-to-action mapping introduced")


class TestStaticBoundaries:
    def test_pack_pins_only_the_reviewed_identity_and_source_metadata(self) -> None:
        assert pack.PACK_ID == "for_you.skin_care.petrolatum_dry_skin.v1"
        assert (pack.SUBSTANCE_KEY, pack.IDENTITY_ENTITY_KIND) == ("petrolatum", "mixture")
        assert (pack.IDENTITY_NAME, pack.IDENTITY_NAME_NAMESPACE, pack.IDENTITY_NAME_PREFERRED) == (
            "Petrolatum",
            "official_reference",
            True,
        )
        assert pack.IDENTITY_SOURCE_TYPE == "government_reference"
        assert pack.IDENTITY_SOURCE_TITLE == "Petrolatum [USP]"
        assert pack.IDENTITY_SOURCE_EXTERNAL_ID == "0008009038"

    def test_no_generic_signal_to_action_algorithm_and_mutation_guard_is_live(self) -> None:
        path = BACKEND_ROOT / "app" / "knowledge_packs" / "petrolatum_dry_skin_v1.py"
        original = path.read_text(encoding="utf-8")
        _generic_signal_action_mapping(original)
        with pytest.raises(AssertionError, match="generic signal-to-action"):
            _generic_signal_action_mapping(
                original + '\nGENERIC_SIGNAL_ACTION = {"supporting": "buy"}\n'
            )
        assert path.read_text(encoding="utf-8") == original

    def test_normal_runtime_never_imports_the_knowledge_pack(self) -> None:
        allowed = {
            BACKEND_ROOT / "app" / "knowledge_packs" / "__init__.py",
            BACKEND_ROOT / "app" / "knowledge_packs" / "petrolatum_dry_skin_v1.py",
        }
        offenders = []
        for path in (BACKEND_ROOT / "app").rglob("*.py"):
            if path not in allowed and "app.knowledge_packs" in path.read_text(encoding="utf-8"):
                offenders.append(path.relative_to(BACKEND_ROOT).as_posix())
        assert offenders == []

    def test_static_decision_registries_remain_empty(self) -> None:
        assert PERSONAL_DECISION_SEMANTIC_RULES == ()
        assert PERSONAL_DECISION_POLICY_RULES == ()
        assert PERSONAL_DECISION_EXPLANATION_RULES == ()

    def test_operator_compiler_has_only_file_and_output_choices(self) -> None:
        script = (REPOSITORY_ROOT / "scripts" / "build_step8i_petrolatum_release.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "--action",
            "--signal",
            "--policy-id",
            "--reason-key",
            "--gap-flag",
            "--semantic-id",
            "--source-key",
        ):
            assert forbidden not in script
        assert 'parser.add_argument("input"' in script
        assert 'parser.add_argument("--output"' in script

    def test_forbidden_scope_is_untouched(self) -> None:
        changed = {
            line.strip()
            for line in subprocess.check_output(
                ["git", "diff", "--name-only", "HEAD"], cwd=REPOSITORY_ROOT, text=True
            ).splitlines()
        }
        assert not any(path.startswith("backend/migrations/") for path in changed)
        assert not any(path.startswith("frontend/") for path in changed)
        assert not any(path.startswith(".github/") for path in changed)
        assert not any("requirements" in path for path in changed)


async def _publish_identity(session, key: str = pack.SUBSTANCE_KEY, name: str = pack.IDENTITY_NAME) -> None:
    result = await substance_authoring.create_identity_draft(
        session,
        substance_key=key,
        entity_kind=(
            EntityKind.MIXTURE.value if key == pack.SUBSTANCE_KEY else EntityKind.DEFINED_SUBSTANCE.value
        ),
        names=[
            {
                "name": name,
                "namespace": NameNamespace.OFFICIAL_REFERENCE.value,
                "language_tag": "und",
                "is_preferred": True,
            }
        ],
        summary=f"The reviewed reference records the exact identity name {name}.",
        scope="Identity and nomenclature only.",
        evidence_strength=EvidenceStrength.STRONG.value,
        strength_rationale="The named governmental reference records this identity directly.",
        source_title=pack.IDENTITY_SOURCE_TITLE if key == pack.SUBSTANCE_KEY else "Test identity",
        source_publisher=(
            pack.IDENTITY_SOURCE_PUBLISHER if key == pack.SUBSTANCE_KEY else "Test Reference"
        ),
        source_type=SourceType.GOVERNMENT_REFERENCE.value,
        source_url=(pack.IDENTITY_SOURCE_URL if key == pack.SUBSTANCE_KEY else f"https://example.org/{key}"),
        license_or_use_note=pack.IDENTITY_SOURCE_USE_NOTE,
        author="step8i.test",
    )
    claim_id = uuid.UUID(result["claim_id"])
    await evidence_authoring.approve(session, claim_id, reviewer="step8i.reviewer")
    await evidence_authoring.record_publication_verification(
        session, claim_id, verification=EVIDENCE_VERIFICATION, actor="step8i.founder"
    )
    await evidence_authoring.publish(session, claim_id, publisher="step8i.publisher")


def _applicability_input(*, existing_sources: list[dict] | None = None, synthetic: bool = False):
    if existing_sources is not None:
        sources = tuple(
            applicability_authoring.ExistingSourceInput(
                source_key=str(source["source_key"]), locator=str(source["locator"])
            )
            for source in existing_sources
        )
    elif synthetic:
        sources = (
            applicability_authoring.NewSourceInput(
                source_type=SourceType.PEER_REVIEWED_RESEARCH.value,
                title="Synthetic test-only evidence",
                publisher="Test Publisher",
                canonical_url=f"https://example.org/test-only/{uuid.uuid4().hex}",
                license_or_use_note="Synthetic test metadata.",
                locator="Test section",
                publication_date=date(2026, 1, 1),
                version_or_revision="test-only",
                jurisdiction="global",
            ),
        )
    else:
        sources = (
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
    return applicability_authoring.PersonalApplicabilityDraftInput(
        category=PersonalApplicabilityCategory.SKIN_CARE,
        substance_key=pack.SUBSTANCE_KEY,
        summary=("Synthetic test-only applicability." if synthetic else pack.EVIDENCE_SUMMARY),
        scope=("Synthetic test scope." if synthetic else pack.EVIDENCE_SCOPE),
        evidence_strength=pack.EVIDENCE_STRENGTH,
        strength_rationale=(
            "Synthetic test rationale." if synthetic else pack.EVIDENCE_STRENGTH_RATIONALE
        ),
        conditions=(
            applicability_authoring.AuthoringConditionInput(
                fact_key=pack.FACT_KEY, values=pack.FACT_VALUES
            ),
        ),
        sources=sources,
    )


async def _publish_applicability(
    session,
    *,
    entry_id: uuid.UUID | None = None,
    existing_sources: list[dict] | None = None,
    synthetic: bool = False,
) -> dict:
    entry = _applicability_input(existing_sources=existing_sources, synthetic=synthetic)
    if entry_id is None:
        view = await applicability_authoring.create_personal_applicability_draft(
            session, entry, author="step8i.author"
        )
    else:
        view = await applicability_authoring.edit_personal_applicability_entry(
            session, entry_id, entry, author="step8i.author"
        )
    identifier = uuid.UUID(view["id"])
    await applicability_authoring.approve_personal_applicability_entry(
        session, identifier, reviewer="step8i.reviewer"
    )
    await applicability_authoring.record_personal_applicability_publication_verification(
        session,
        identifier,
        verification=EVIDENCE_VERIFICATION,
        actor="step8i.founder",
    )
    return await applicability_authoring.publish_personal_applicability_entry(
        session, identifier, publisher="step8i.publisher"
    )


async def _activate(session, published: dict) -> dict:
    manifest = pack.build_release_manifest_from_published_entry(published)
    draft = await release_authoring.create_personal_decision_release_draft(
        session, manifest, actor="step8i.author"
    )
    release_id = uuid.UUID(draft["id"])
    await release_authoring.record_personal_decision_release_verification(
        session,
        release_id,
        verification=RELEASE_VERIFICATION,
        actor="step8i.reviewer",
    )
    validation = await release_authoring.validate_personal_decision_release(session, release_id)
    assert validation["ready"] is True
    await release_authoring.approve_personal_decision_release(
        session, release_id, actor="step8i.approver"
    )
    return await release_authoring.activate_personal_decision_release(
        session, release_id, actor="step8i.activator"
    )


async def _account(
    session,
    *,
    usual_feel: str = "often_dry_or_tight",
    sensitivity: str | None = "rarely_reactive",
) -> uuid.UUID:
    account_id = uuid.uuid4()
    await identity_service.register_account(session, account_id)
    profile = AppearanceProfile(account_id=account_id)
    session.add(profile)
    await session.flush()
    values = [(pack.FACT_KEY, usual_feel)]
    if sensitivity is not None:
        values.append(("care_skin_sensitivity", sensitivity))
    for key, value in values:
        session.add(
            ProfileAttribute(
                profile_id=profile.id,
                key=key,
                value=value,
                source="user_declared",
                confidence=1.0,
                verification_state="confirmed",
            )
        )
    await session.flush()
    return account_id


def _snapshot(ingredients: str) -> LabelSnapshot:
    return LabelSnapshot(
        id=uuid.uuid4(),
        barcode="8900000000081",
        scan_event_id=uuid.uuid4(),
        facts={"ingredients_text": ingredients},
        content_fingerprint="8" * 64,
        version_number=1,
        previous_snapshot_id=None,
        changed_fields=[],
        completeness="complete_for_grading",
        confidence=ProductConfidence.VERIFIED.value,
    )


async def _evaluate(
    session,
    account_id: uuid.UUID,
    *,
    ingredients: str = "Petrolatum",
    category: PersonalApplicabilityCategory = PersonalApplicabilityCategory.SKIN_CARE,
    safety: PersonalLensSafetyInput | None = None,
):
    applicability = await interpret_label_snapshot_for_account(
        session,
        _snapshot(ingredients),
        account_id=account_id,
        category=category,
        safety=safety,
    )
    release = await load_active_personal_decision_release(session)
    return applicability, evaluate_personal_decision_with_release(applicability, release)


async def _qualify(session) -> tuple[dict, dict]:
    await _publish_identity(session)
    published = await _publish_applicability(session)
    activated = await _activate(session, published)
    await session.commit()
    return published, activated


def _no_action(result) -> None:
    assert result.presentation.action is None
    assert result.presentation.status is not PersonalDecisionPresentationStatus.DECISION_PRESENTABLE


class TestRealGovernedChain:
    async def test_exact_petrolatum_chain_is_presentable_with_exact_provenance(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            published, activated = await _qualify(session)
            account_id = await _account(session)
            await session.commit()
            applicability, result = await _evaluate(session, account_id)

        assert applicability.context_status is PersonalLensStatus.CONTEXT_AVAILABLE
        assert applicability.ingredients[0].substance_key == "petrolatum"
        factory = get_sessionmaker()
        async with factory() as session:
            substance = (
                await session.execute(
                    select(Substance).where(Substance.substance_key == pack.SUBSTANCE_KEY)
                )
            ).scalar_one()
            names = list(
                (
                    await session.execute(
                        select(SubstanceName).where(SubstanceName.substance_id == substance.id)
                    )
                )
                .scalars()
                .all()
            )
        assert substance.entity_kind == "mixture"
        assert [(name.name, name.namespace, name.is_preferred) for name in names] == [
            ("Petrolatum", "official_reference", True)
        ]
        presentation = result.presentation
        assert presentation.status is PersonalDecisionPresentationStatus.DECISION_PRESENTABLE
        assert presentation.action is PersonalDecisionAction.BUY
        assert presentation.explanation_id == pack.EXPLANATION_ID
        assert presentation.explanation_version == "1"
        assert presentation.reason_key == pack.REASON_KEY
        assert presentation.source_policy.policy_id == pack.POLICY_ID
        semantic = presentation.source_policy.source_aggregation.rules[0]
        assert (semantic.rule_id, semantic.rule_version) == (pack.SEMANTIC_RULE_ID, "1")
        assert (semantic.claim_key, semantic.claim_version) == (
            published["claim_key"],
            published["claim_version"],
        )
        aad = next(source for source in published["sources"] if source["canonical_url"] == pack.AAD_SOURCE_URL)
        assert presentation.citation.source_key == aad["source_key"]
        assert presentation.citation.canonical_url == pack.AAD_SOURCE_URL
        assert presentation.citation.locator == pack.AAD_SOURCE_LOCATOR
        assert str(result.release_id) == activated["id"]
        assert result.release_version == activated["release_version"]
        assert result.release_content_hash == activated["content_hash"]

    @pytest.mark.parametrize("usual_feel", ["comfortable", "often_oily"])
    async def test_other_skin_feel_never_inherits_the_action(self, db_clean, usual_feel) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _qualify(session)
            account_id = await _account(session, usual_feel=usual_feel)
            await session.commit()
            _, result = await _evaluate(session, account_id)
        _no_action(result)

    @pytest.mark.parametrize(
        ("sensitivity", "expected"),
        [(None, PersonalLensStatus.PARTIAL_CONTEXT), ("not_sure", PersonalLensStatus.PARTIAL_CONTEXT)],
    )
    async def test_incomplete_or_explicit_unknown_context_never_decides(
        self, db_clean, sensitivity, expected
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _qualify(session)
            account_id = await _account(session, sensitivity=sensitivity)
            await session.commit()
            applicability, result = await _evaluate(session, account_id)
        assert applicability.context_status is expected
        _no_action(result)

    async def test_hard_handoff_precedes_the_active_release(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _qualify(session)
            account_id = await _account(session)
            await session.commit()
            _, result = await _evaluate(
                session,
                account_id,
                safety=PersonalLensSafetyInput(text="I was diagnosed with eczema"),
            )
        assert result.presentation.status is PersonalDecisionPresentationStatus.HANDOFF_REQUIRED
        assert result.presentation.action is None

    async def test_wrong_category_unknown_and_known_second_ingredients_fail_closed(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _qualify(session)
            await _publish_identity(session, "glycerin", "Glycerin")
            account_id = await _account(session)
            await session.commit()
            _, wrong = await _evaluate(
                session, account_id, category=PersonalApplicabilityCategory.COSMETICS
            )
            _, unknown = await _evaluate(
                session, account_id, ingredients="Petrolatum,Completely Unknown Ingredient"
            )
            _, known = await _evaluate(session, account_id, ingredients="Petrolatum,Glycerin")
        _no_action(wrong)
        _no_action(unknown)
        _no_action(known)

    async def test_extra_unmapped_claim_and_v2_evidence_both_fail_closed(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            published, _ = await _qualify(session)
            account_id = await _account(session)
            await _publish_applicability(session, synthetic=True)
            await session.commit()
            _, unmapped = await _evaluate(session, account_id)
            _no_action(unmapped)

        async with factory() as session:
            revised = await _publish_applicability(
                session,
                entry_id=uuid.UUID(published["id"]),
                existing_sources=published["sources"],
            )
            assert revised["claim_version"] == 2
            await session.commit()
            _, stale = await _evaluate(session, account_id)
        _no_action(stale)

    async def test_source_ineligibility_deactivation_and_no_release_withhold(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            published, activated = await _qualify(session)
            account_id = await _account(session)
            aad = (
                await session.execute(
                    select(EvidenceSource).where(
                        EvidenceSource.source_key
                        == next(
                            source["source_key"]
                            for source in published["sources"]
                            if source["canonical_url"] == pack.AAD_SOURCE_URL
                        )
                    )
                )
            ).scalar_one()
            aad.status = SourceStatus.UNAVAILABLE.value
            await session.commit()
            _, ineligible = await _evaluate(session, account_id)
            _no_action(ineligible)

            await release_authoring.deactivate_personal_decision_release(
                session, uuid.UUID(activated["id"]), actor="step8i.operator"
            )
            await session.commit()
            _, deactivated = await _evaluate(session, account_id)
            _no_action(deactivated)

            await session.execute(delete(PersonalDecisionRelease))
            await session.commit()
            _, absent = await _evaluate(session, account_id)
        _no_action(absent)


class TestSeedBoundary:
    async def test_ordinary_reference_seed_creates_no_step8i_authority(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await run_reference_seed(session)
            claim_count = (
                await session.execute(
                    select(func.count())
                    .select_from(EvidenceClaim)
                    .where(
                        EvidenceClaim.subject_key == pack.SUBSTANCE_KEY,
                        EvidenceClaim.claim_key.like("personal-applicability:%"),
                    )
                )
            ).scalar_one()
            release_count = (
                await session.execute(select(func.count()).select_from(PersonalDecisionRelease))
            ).scalar_one()
        assert claim_count == 0
        assert release_count == 0
        assert PERSONAL_DECISION_SEMANTIC_RULES == ()
        assert PERSONAL_DECISION_POLICY_RULES == ()
        assert PERSONAL_DECISION_EXPLANATION_RULES == ()


# ---------------------------------------------------------------------------
# The customer reason must be supported by its own selected citation
# ---------------------------------------------------------------------------


class TestSourceToReasonCorrespondence:
    """Step 8F shows one reason beside one citation, so the two must agree.

    The Step 8G evidence body is reviewed from two sources: the AAD guidance page
    and the randomized PubMed study. Only the AAD page is the *selected* Step 8F
    citation. A customer reading the reason would take it as what that citation
    says, so the reason is scoped to what the AAD locator actually supports --
    that dermatologist guidance lists petrolatum among cream/ointment ingredients
    for dry skin.

    The moisture-loss / TEWL mechanism is real and reviewed, but it comes from the
    PubMed study. Attaching it to the AAD citation would cite a source for a claim
    it does not make at that locator. That is the boundary these tests hold.
    """

    def test_the_reason_key_is_scoped_to_dermatologist_guidance(self) -> None:
        assert pack.REASON_KEY == (
            "for_you.skin_care.petrolatum.dry_skin.dermatologist_guidance"
        )

    def test_the_retired_moisture_loss_reason_key_is_gone(self) -> None:
        """No alias: there is no released version, so nothing depends on it."""
        retired = "for_you.skin_care.petrolatum.dry_skin.moisture_loss"
        assert retired != pack.REASON_KEY
        source = (
            Path(pack.__file__).read_text(encoding="utf-8")
            if hasattr(pack, "__file__")
            else ""
        )
        assert retired not in source

    def test_the_retired_reason_key_is_absent_from_the_manifest(self) -> None:
        manifest = pack.build_release_manifest_from_published_entry(_valid_entry())
        rendered = json.dumps(manifest, sort_keys=True)
        assert "moisture_loss" not in rendered
        assert pack.REASON_KEY in rendered

    def test_the_future_reason_intent_stays_within_the_aad_scope(self) -> None:
        intent = pack.FUTURE_REASON_INTENT.lower()
        assert "dry skin" in intent
        assert "petrolatum" in intent
        assert "dermatologist" in intent
        # It must describe ingredient selection guidance, not a mechanism.
        assert "cream" in intent or "ointment" in intent

    @pytest.mark.parametrize("claim", pack.REASON_CLAIMS_OUT_OF_SCOPE)
    def test_the_future_reason_intent_makes_no_unsupported_claim(self, claim: str) -> None:
        """Each of these belongs to the other source, or to no source at all."""
        assert claim not in pack.FUTURE_REASON_INTENT.lower()

    def test_the_pubmed_mechanism_is_absent_from_the_customer_reason(self) -> None:
        for forbidden in ("tewl", "transepidermal", "moisture loss", "water loss"):
            assert forbidden not in pack.REASON_KEY.lower()
            assert forbidden not in pack.FUTURE_REASON_INTENT.lower()

    def test_the_intent_is_not_verbatim_source_wording(self) -> None:
        """An original paraphrase, not reproduced AAD text."""
        assert pack.FUTURE_REASON_INTENT not in pack.AAD_SOURCE_TITLE
        assert pack.FUTURE_REASON_INTENT != pack.AAD_SOURCE_LOCATOR

    def test_the_evidence_body_may_still_cite_both_reviewed_sources(self) -> None:
        """The Step 8G review is the two-source synthesis; only the reason narrows.

        This is the other half of the boundary. Narrowing the customer reason must
        not quietly narrow the evidence review that justified the strength.
        """
        combined = (pack.EVIDENCE_SUMMARY + pack.EVIDENCE_STRENGTH_RATIONALE).lower()
        assert "moisture loss" in combined or "water loss" in combined
        assert "dermatolog" in combined
        assert pack.EVIDENCE_STRENGTH == "moderate"

    def test_the_selected_citation_is_still_the_aad_page(self) -> None:
        manifest = pack.build_release_manifest_from_published_entry(_valid_entry())
        (explanation,) = manifest["explanation_rules"]
        entry = _valid_entry()
        aad = next(s for s in entry["sources"] if s["publisher"] == pack.AAD_SOURCE_PUBLISHER)
        assert explanation["source_key"] == aad["source_key"]
        assert explanation["source_locator"] == pack.AAD_SOURCE_LOCATOR


class TestProvenanceIsAbsentNotInferred:
    """Optional provenance stays null unless the source establishes it.

    A null here is a deliberate statement that the source does not say, not an
    accidental omission -- so each value is asserted by name.
    """

    def test_aad_reports_an_update_not_a_publication_date(self) -> None:
        assert pack.AAD_SOURCE_PUBLICATION_DATE is None
        assert pack.AAD_SOURCE_VERSION == "Last updated 2026-01-02"

    def test_neither_source_asserts_a_jurisdiction(self) -> None:
        assert pack.AAD_SOURCE_JURISDICTION is None
        assert pack.PUBMED_SOURCE_JURISDICTION is None

    def test_the_article_publisher_is_not_the_database(self) -> None:
        assert pack.PUBMED_SOURCE_PUBLISHER == "Wiley Periodicals, Inc."
        assert pack.PUBMED_SOURCE_URL == "https://pubmed.ncbi.nlm.nih.gov/31532576/"
        assert pack.PUBMED_SOURCE_VERSION == "PMID 31532576; DOI 10.1111/jocd.13163"

    def test_the_identity_publisher_is_the_depositor_not_the_host(self) -> None:
        assert pack.IDENTITY_SOURCE_PUBLISHER == "ChemIDplus"
        assert pack.IDENTITY_SOURCE_URL == (
            "https://pubchem.ncbi.nlm.nih.gov/substance/135345390"
        )

    def test_the_compiled_manifest_still_requires_exactly_two_sources(self) -> None:
        entry = _valid_entry()
        assert len(entry["sources"]) == 2
        assert pack.build_release_manifest_from_published_entry(entry)

    def test_source_order_does_not_change_the_compiled_manifest(self) -> None:
        forward = _valid_entry()
        reversed_entry = _valid_entry()
        reversed_entry["sources"].reverse()
        assert pack.build_release_manifest_from_published_entry(
            forward
        ) == pack.build_release_manifest_from_published_entry(reversed_entry)


class TestReasonChangeMovesTheHash:
    """A reviewed knowledge change must be visible in the release hash.

    The full manifest embeds generated claim and source keys, so its hash is
    environment-dependent and no fixed value can be asserted. Holding those
    constant and varying only the reason key isolates the thing under review.
    """

    def test_the_reason_key_participates_in_the_content_hash(self, monkeypatch) -> None:
        entry = _valid_entry()
        current = manifest_content_hash(
            parse_release_manifest(pack.build_release_manifest_from_published_entry(entry))
        )

        monkeypatch.setattr(
            pack, "REASON_KEY", "for_you.skin_care.petrolatum.dry_skin.moisture_loss"
        )
        retired = manifest_content_hash(
            parse_release_manifest(pack.build_release_manifest_from_published_entry(entry))
        )
        assert current != retired

    def test_the_hash_is_stable_for_one_reviewed_pack(self) -> None:
        """Same reviewed knowledge, same entry, same hash."""
        entry = _valid_entry()
        first = manifest_content_hash(
            parse_release_manifest(pack.build_release_manifest_from_published_entry(entry))
        )
        second = manifest_content_hash(
            parse_release_manifest(pack.build_release_manifest_from_published_entry(entry))
        )
        assert first == second
