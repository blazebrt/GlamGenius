"""Phase B — controlled production knowledge activation.

The operator path for the exact reviewed Step 8I pack. Almost everything below
is about something the tool must *refuse*: to publish its own drafts, to approve
its own release, to guess which of two plausible authorities is the real one, to
activate a release whose content the operator could not name, or to replace
knowledge that is already answering customers.

The governance layer is never mocked. Every review, approval and publication in
these tests goes through the real Step 7A / 8G / 8H functions, because a test
that stubs the review gates cannot say anything about a milestone whose entire
purpose is that the review gates hold.
"""

from __future__ import annotations

import argparse
import ast
import copy
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from app.bootstrap import run as run_reference_seed
from app.content import for_you_copy
from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun, AIRunOutput
from app.domains.evidence import authoring as evidence_authoring
from app.domains.evidence.enums import ReviewStatus, SourceStatus, SourceType
from app.domains.evidence.models import (
    EvidenceClaim,
    EvidenceClaimSource,
    EvidenceSource,
)
from app.domains.personal_applicability import authoring as applicability_authoring
from app.domains.personal_applicability.enums import PersonalApplicabilityCategory
from app.domains.personal_decision_release import authoring as release_authoring
from app.domains.personal_decision_release.manifest import (
    manifest_content_hash,
    parse_release_manifest,
)
from app.domains.personal_decision_release.models import PersonalDecisionRelease
from app.domains.personal_decision_release.runtime import (
    load_active_personal_decision_release,
)
from app.domains.personal_decision_release.validation import ReleaseVerification
from app.domains.product import care_extraction
from app.domains.profile.models import AppearanceProfile, ProfileAttribute
from app.domains.substances.models import Substance, SubstanceName
from app.knowledge_packs import petrolatum_dry_skin_v1 as pack
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import event, func, select, text, update
from sqlalchemy.orm import Session as SyncSession

from tests.conftest import auth

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
OPERATOR_PATH = REPOSITORY_ROOT / "scripts" / "operate_step8i_petrolatum_release.py"
STEP8I_COMPILER_PATH = REPOSITORY_ROOT / "scripts" / "build_step8i_petrolatum_release.py"

FOR_YOU_URL = "/api/v2/scan/skin-care/for-you"
CONFIRM_URL = "/api/v2/scan/skin-care/label/confirm"
BARCODE = "8901030000059"
PETROLATUM = "Petrolatum"

ACTOR = "phase-b.operator"

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

#: Tables the operator could conceivably write. Fingerprinted whole, so an
#: update is caught as well as an insert.
_WATCHED_TABLES = (
    "substances",
    "substance_names",
    "evidence_claims",
    "evidence_sources",
    "evidence_claim_sources",
    "personal_decision_releases",
)


def _load_operator() -> Any:
    spec = importlib.util.spec_from_file_location("step8i_operator", OPERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


operator = _load_operator()


# ---------------------------------------------------------------------------
# Proving that nothing was written
# ---------------------------------------------------------------------------
async def _fingerprint(session) -> dict[str, str]:
    """A content hash of every table the operator could touch.

    Row counts alone would miss an in-place update, which is exactly the kind of
    silent overwrite this milestone exists to prevent.
    """
    marks: dict[str, str] = {}
    for table in _WATCHED_TABLES:
        marks[table] = (
            await session.execute(
                text(
                    f"select coalesce(md5(string_agg(t::text, '|' order by t::text)), 'empty') "
                    f"from {table} t"
                )
            )
        ).scalar_one()
    return marks


# ---------------------------------------------------------------------------
# The real governed review lifecycle, performed by "people"
# ---------------------------------------------------------------------------
async def _identity_claim(session) -> EvidenceClaim:
    return (
        await session.execute(
            select(EvidenceClaim).where(
                EvidenceClaim.claim_type == "substance_identity",
                EvidenceClaim.subject_key == pack.SUBSTANCE_KEY,
            )
        )
    ).scalars().one()


async def _human_publishes_identity(session) -> None:
    claim = await _identity_claim(session)
    await evidence_authoring.approve(session, claim.id, reviewer="human.reviewer")
    await evidence_authoring.record_publication_verification(
        session, claim.id, verification=EVIDENCE_VERIFICATION, actor="human.founder",
    )
    await evidence_authoring.publish(session, claim.id, publisher="human.publisher")


async def _human_publishes_applicability(session, entry_id: uuid.UUID) -> dict[str, Any]:
    await applicability_authoring.approve_personal_applicability_entry(
        session, entry_id, reviewer="human.reviewer",
    )
    await applicability_authoring.record_personal_applicability_publication_verification(
        session, entry_id, verification=EVIDENCE_VERIFICATION, actor="human.founder",
    )
    return await applicability_authoring.publish_personal_applicability_entry(
        session, entry_id, publisher="human.publisher",
    )


async def _human_approves_release(session, release_id: uuid.UUID) -> dict[str, Any]:
    await release_authoring.record_personal_decision_release_verification(
        session, release_id, verification=RELEASE_VERIFICATION, actor="human.reviewer",
    )
    validation = await release_authoring.validate_personal_decision_release(
        session, release_id,
    )
    assert validation["ready"] is True
    return await release_authoring.approve_personal_decision_release(
        session, release_id, actor="human.approver",
    )


# ---------------------------------------------------------------------------
# Stages, each built from the one before it
# ---------------------------------------------------------------------------
async def _prepared(session) -> dict[str, Any]:
    return await operator.run_prepare(session, actor=ACTOR)


async def _published(session) -> dict[str, Any]:
    prepared = await _prepared(session)
    await _human_publishes_identity(session)
    await _human_publishes_applicability(
        session, uuid.UUID(prepared["applicability"]["entry_id"])
    )
    return prepared


async def _compiled(session) -> dict[str, Any]:
    await _published(session)
    return await operator.run_compile(session, actor=ACTOR)


async def _approved(session) -> dict[str, Any]:
    compiled = await _compiled(session)
    await _human_approves_release(session, uuid.UUID(compiled["release"]["id"]))
    return compiled["release"]


async def _activated(session) -> dict[str, Any]:
    release = await _approved(session)
    await operator.run_activate(
        session,
        release_id=uuid.UUID(release["id"]),
        expected_content_hash=release["content_hash"],
        actor=ACTOR,
    )
    return release


def _other_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """A different, still-valid release: same evidence, different rule identities.

    Used only to put *somebody else's* release in the active slot, so the
    operator's refusal to replace it can be observed.
    """
    other = copy.deepcopy(manifest)
    for rule in other["semantic_rules"]:
        rule["rule_id"] = f"{rule['rule_id']}.other"
    for policy in other["policy_rules"]:
        policy["policy_id"] = f"{policy['policy_id']}.other"
        for identity in policy["semantic_rule_identities"]:
            identity["rule_id"] = f"{identity['rule_id']}.other"
    for explanation in other["explanation_rules"]:
        explanation["explanation_id"] = f"{explanation['explanation_id']}.other"
        explanation["policy_id"] = f"{explanation['policy_id']}.other"
        explanation["semantic_rule_id"] = f"{explanation['semantic_rule_id']}.other"
    return other


async def _release_row(session, release_id: str) -> PersonalDecisionRelease:
    return await session.get(PersonalDecisionRelease, uuid.UUID(release_id))


def _refusal(exc_info) -> Any:
    return exc_info.value


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
class TestStatus:
    async def test_status_writes_nothing_even_with_the_pack_fully_installed(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _activated(session)
            await session.commit()
            before = await _fingerprint(session)
            result = await operator.run_status(session)
            await session.rollback()
        async with factory() as session:
            after = await _fingerprint(session)
        assert result["ok"] is True
        assert after == before

    async def test_an_empty_database_reports_the_pack_uninstalled(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            result = await operator.run_status(session)
        assert result["substance"]["present"] is False
        assert result["identity"]["state"] == "absent"
        assert result["applicability"]["state"] == "absent"
        assert result["sources"]["aad"]["present"] is False
        assert result["sources"]["pubmed"]["present"] is False
        assert result["compiled"] == {
            "available": False,
            "content_hash": None,
            "reason": "APPLICABILITY_NOT_PUBLISHED",
        }
        assert result["releases"]["total"] == 0
        assert result["releases"]["step8i_release_active"] is False
        assert result["ambiguities"] == []

    async def test_exact_installed_records_are_identified(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _activated(session)
            await session.commit()
            result = await operator.run_status(session)
        assert result["substance"] == {
            "present": True,
            "substance_key": pack.SUBSTANCE_KEY,
            "entity_kind": pack.IDENTITY_ENTITY_KIND,
            "matches_pack": True,
        }
        assert result["identity"]["state"] == ReviewStatus.PUBLISHED.value
        assert result["identity"]["matches_pack"] is True
        assert result["applicability"]["state"] == ReviewStatus.PUBLISHED.value
        assert result["applicability"]["published_count"] == 1
        assert result["sources"]["aad"]["matches_pack"] is True
        assert result["sources"]["pubmed"]["matches_pack"] is True
        assert result["compiled"]["available"] is True
        assert result["compiled"]["content_hash"] == release["content_hash"]
        assert [r["id"] for r in result["releases"]["active"]] == [release["id"]]
        assert result["releases"]["step8i_release_active"] is True
        assert result["copy"]["reason_copy_matches_pack_intent"] is True
        assert result["copy"]["verdict_text"] == "BUY"
        assert result["ambiguities"] == []

    async def test_ambiguous_applicability_is_reported_never_resolved_by_recency(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            prepared = await _prepared(session)
            first = uuid.UUID(prepared["applicability"]["entry_id"])
            entry = await applicability_authoring.get_personal_applicability_entry(
                session, first,
            )
            # A second, later authority for exactly the same knowledge.
            second = await applicability_authoring.create_personal_applicability_draft(
                session,
                applicability_authoring.PersonalApplicabilityDraftInput(
                    category=PersonalApplicabilityCategory(pack.CATEGORY),
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
                    sources=tuple(
                        applicability_authoring.ExistingSourceInput(
                            source_key=source["source_key"], locator=source["locator"],
                        )
                        for source in entry["sources"]
                    ),
                ),
                author="someone.else",
            )
            await session.commit()
            result = await operator.run_status(session)

        assert result["applicability"]["state"] == "ambiguous"
        assert result["applicability"]["matches_pack"] is False
        assert result["applicability"]["entry_id"] is None
        assert sorted(result["applicability"]["matching_candidates"]) == sorted(
            [str(first), second["id"]]
        )
        assert "APPLICABILITY_AMBIGUOUS" in result["ambiguities"]

    async def test_ambiguous_identity_is_reported_never_resolved_by_recency(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _prepared(session)
            # A second identity claim carrying exactly the reviewed metadata.
            await operator.substance_authoring.create_identity_draft(
                session,
                substance_key=pack.SUBSTANCE_KEY,
                entity_kind=pack.IDENTITY_ENTITY_KIND,
                names=[
                    {
                        "name": pack.IDENTITY_NAME,
                        "namespace": pack.IDENTITY_NAME_NAMESPACE,
                        "language_tag": "und",
                        "is_preferred": pack.IDENTITY_NAME_PREFERRED,
                    }
                ],
                summary="A competing identity authority.",
                scope="Identity and nomenclature only.",
                evidence_strength="strong",
                strength_rationale="Recorded directly.",
                source_title=pack.IDENTITY_SOURCE_TITLE,
                source_publisher=pack.IDENTITY_SOURCE_PUBLISHER,
                source_type=pack.IDENTITY_SOURCE_TYPE,
                source_url=pack.IDENTITY_SOURCE_URL,
                license_or_use_note=pack.IDENTITY_SOURCE_USE_NOTE,
                author="someone.else",
            )
            await session.commit()
            result = await operator.run_status(session)
        assert result["identity"]["state"] == "ambiguous"
        assert result["identity"]["claim_id"] is None
        assert len(result["identity"]["matching_candidates"]) == 2
        assert "IDENTITY_AMBIGUOUS" in result["ambiguities"]

    async def test_the_cli_entry_point_runs_and_emits_json(self, db_clean) -> None:
        """The script an operator actually invokes, run as a process."""
        completed = subprocess.run(
            [sys.executable, str(OPERATOR_PATH), "status"],
            capture_output=True,
            text=True,
            cwd=str(REPOSITORY_ROOT),
            env={**os.environ},
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        body = json.loads(completed.stdout)
        assert body["ok"] is True
        assert body["operation"] == "status"
        assert body["pack_id"] == pack.PACK_ID


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------
class TestPrepare:
    async def test_prepare_creates_only_draft_authority(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            result = await _prepared(session)
            await session.commit()
            statuses = set(
                (
                    await session.execute(select(EvidenceClaim.review_status))
                ).scalars().all()
            )
        assert result["identity"]["review_status"] == ReviewStatus.DRAFT.value
        assert result["applicability"]["review_status"] == ReviewStatus.DRAFT.value
        assert statuses == {ReviewStatus.DRAFT.value}

    async def test_prepare_never_verifies_approves_publishes_or_activates(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _prepared(session)
            await session.commit()
            claims = (await session.execute(select(EvidenceClaim))).scalars().all()
            releases = (
                await session.execute(select(func.count()).select_from(PersonalDecisionRelease))
            ).scalar_one()
        for claim in claims:
            assert claim.review_status == ReviewStatus.DRAFT.value
            assert claim.reviewed_by is None and claim.reviewed_at is None
            assert claim.published_by is None and claim.published_at is None
            assert "publication_verification" not in (claim.structured_value or {})
        assert releases == 0

    async def test_a_second_identical_prepare_is_idempotent(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            first = await _prepared(session)
            await session.commit()
            before = await _fingerprint(session)
            second = await _prepared(session)
            await session.commit()
            after = await _fingerprint(session)
        assert first["identity"]["created"] is True
        assert first["applicability"]["created"] is True
        assert second["identity"] == {**first["identity"], "created": False}
        assert second["applicability"] == {**first["applicability"], "created": False}
        assert after == before

    async def test_repeated_prepare_duplicates_no_authority(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _prepared(session)
            await _prepared(session)
            await _prepared(session)
            await session.commit()

            async def _count(model, *where) -> int:
                query = select(func.count()).select_from(model)
                for clause in where:
                    query = query.where(clause)
                return (await session.execute(query)).scalar_one()

            assert await _count(Substance) == 1
            assert await _count(SubstanceName) == 1
            names = (await session.execute(select(SubstanceName.name))).scalars().all()
            assert names == [pack.IDENTITY_NAME]
            assert await _count(
                EvidenceClaim, EvidenceClaim.claim_type == "substance_identity"
            ) == 1
            assert await _count(
                EvidenceClaim,
                EvidenceClaim.claim_type == "substance_personal_applicability",
            ) == 1
            # One identity source plus the two reviewed applicability sources.
            assert await _count(EvidenceSource) == 3
            for url in (pack.AAD_SOURCE_URL, pack.PUBMED_SOURCE_URL):
                assert await _count(
                    EvidenceSource, EvidenceSource.canonical_url == url
                ) == 1
            assert await _count(EvidenceClaimSource) == 3

    async def test_an_exact_existing_reviewed_source_is_reused(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            first = await _prepared(session)
            entry = await applicability_authoring.get_personal_applicability_entry(
                session, uuid.UUID(first["applicability"]["entry_id"]),
            )
            original_keys = sorted(source["source_key"] for source in entry["sources"])
            # Remove only the claim; the two reviewed sources survive.
            claim_id = uuid.UUID(first["applicability"]["entry_id"])
            await session.execute(
                EvidenceClaimSource.__table__.delete().where(
                    EvidenceClaimSource.claim_id == claim_id
                )
            )
            await session.execute(
                EvidenceClaim.__table__.delete().where(EvidenceClaim.id == claim_id)
            )
            await session.commit()

            second = await _prepared(session)
            await session.commit()
            rebuilt = await applicability_authoring.get_personal_applicability_entry(
                session, uuid.UUID(second["applicability"]["entry_id"]),
            )
            sources = (
                await session.execute(select(func.count()).select_from(EvidenceSource))
            ).scalar_one()
        assert second["applicability"]["created"] is True
        assert sorted(s["source_key"] for s in rebuilt["sources"]) == original_keys
        assert sources == 3

    async def test_conflicting_source_metadata_blocks(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _prepared(session)
            await session.execute(
                update(EvidenceSource)
                .where(EvidenceSource.canonical_url == pack.AAD_SOURCE_URL)
                .values(publisher="Someone Else Entirely")
            )
            await session.commit()
            # The now-conflicting source belongs to a claim that no longer
            # matches the pack, so the applicability conflict is what fires
            # first; the operator is told which record disagrees either way.
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_prepare(session, actor=ACTOR)
            await session.rollback()
        assert _refusal(exc_info).code in {"APPLICABILITY_CONFLICT", "SOURCE_CONFLICT"}

    async def test_a_conflicting_orphan_source_blocks_before_authoring(
        self, db_clean
    ) -> None:
        """A reviewed URL already present with the wrong provenance."""
        factory = get_sessionmaker()
        async with factory() as session:
            session.add(
                EvidenceSource(
                    source_key="pre-existing.aad",
                    source_series_key="pre-existing.aad",
                    source_type=SourceType.PROFESSIONAL_CONSENSUS.value,
                    title=pack.AAD_SOURCE_TITLE,
                    publisher="A Different Publisher",
                    canonical_url=pack.AAD_SOURCE_URL,
                    version_or_revision=pack.AAD_SOURCE_VERSION,
                    accessed_at=evidence_authoring.utcnow(),
                    status=SourceStatus.ACTIVE.value,
                    license_or_use_note=pack.AAD_SOURCE_USE_NOTE,
                )
            )
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_prepare(session, actor=ACTOR)
            await session.rollback()
            claims = (
                await session.execute(
                    select(func.count()).select_from(EvidenceClaim).where(
                        EvidenceClaim.claim_type == "substance_personal_applicability"
                    )
                )
            ).scalar_one()
        refusal = _refusal(exc_info)
        assert refusal.code == "SOURCE_CONFLICT"
        assert refusal.details["conflicts"] == ["publisher"]
        assert claims == 0

    async def test_conflicting_identity_blocks(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await operator.substance_authoring.create_identity_draft(
                session,
                substance_key=pack.SUBSTANCE_KEY,
                entity_kind=pack.IDENTITY_ENTITY_KIND,
                names=[
                    {
                        "name": pack.IDENTITY_NAME,
                        "namespace": pack.IDENTITY_NAME_NAMESPACE,
                        "language_tag": "und",
                        "is_preferred": True,
                    }
                ],
                summary="An identity from somewhere else.",
                scope="Identity only.",
                evidence_strength="strong",
                strength_rationale="Recorded directly.",
                source_title="Some other reference",
                source_publisher="Some Other Publisher",
                source_type=SourceType.GOVERNMENT_REFERENCE.value,
                source_url="https://example.org/other-petrolatum",
                license_or_use_note="Other note.",
                author="someone.else",
            )
            await session.commit()
            before = await _fingerprint(session)
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_prepare(session, actor=ACTOR)
            await session.rollback()
            after = await _fingerprint(session)
        assert _refusal(exc_info).code == "IDENTITY_CONFLICT"
        assert after == before

    async def test_a_substance_of_the_wrong_kind_blocks(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            session.add(
                Substance(
                    substance_key=pack.SUBSTANCE_KEY,
                    entity_kind="defined_substance",
                    status="active",
                )
            )
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_prepare(session, actor=ACTOR)
            await session.rollback()
        assert _refusal(exc_info).code == "SUBSTANCE_CONFLICT"

    async def test_ambiguous_candidates_block_prepare(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            prepared = await _prepared(session)
            entry = await applicability_authoring.get_personal_applicability_entry(
                session, uuid.UUID(prepared["applicability"]["entry_id"]),
            )
            await applicability_authoring.create_personal_applicability_draft(
                session,
                applicability_authoring.PersonalApplicabilityDraftInput(
                    category=PersonalApplicabilityCategory(pack.CATEGORY),
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
                    sources=tuple(
                        applicability_authoring.ExistingSourceInput(
                            source_key=source["source_key"], locator=source["locator"],
                        )
                        for source in entry["sources"]
                    ),
                ),
                author="someone.else",
            )
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_prepare(session, actor=ACTOR)
            await session.rollback()
        refusal = _refusal(exc_info)
        assert refusal.code == "APPLICABILITY_AMBIGUOUS"
        assert len(refusal.details["candidates"]) == 2

    async def test_ordinary_bootstrap_seeding_still_creates_no_pack_authority(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await run_reference_seed(session)
            await session.commit()
            result = await operator.run_status(session)
            substances = (
                await session.execute(
                    select(func.count()).select_from(Substance).where(
                        Substance.substance_key == pack.SUBSTANCE_KEY
                    )
                )
            ).scalar_one()
        assert substances == 0
        assert result["identity"]["state"] == "absent"
        assert result["applicability"]["state"] == "absent"
        assert result["releases"]["total"] == 0

    async def test_prepare_requires_an_explicit_actor(self) -> None:
        parser = operator.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["prepare"])
        with pytest.raises(SystemExit):
            parser.parse_args(["prepare", "--actor", "   "])
        assert parser.parse_args(["prepare", "--actor", ACTOR]).actor == ACTOR


# ---------------------------------------------------------------------------
# compile
# ---------------------------------------------------------------------------
class TestCompile:
    async def test_compile_fails_before_the_evidence_is_published(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            prepared = await _prepared(session)
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as draft_refusal:
                await operator.run_compile(session, actor=ACTOR)
            await session.rollback()

            # Approved but not published is still not published.
            await applicability_authoring.approve_personal_applicability_entry(
                session,
                uuid.UUID(prepared["applicability"]["entry_id"]),
                reviewer="human.reviewer",
            )
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as approved_refusal:
                await operator.run_compile(session, actor=ACTOR)
            await session.rollback()
            releases = (
                await session.execute(select(func.count()).select_from(PersonalDecisionRelease))
            ).scalar_one()
        assert _refusal(draft_refusal).code == "APPLICABILITY_NOT_PUBLISHED"
        assert _refusal(approved_refusal).code == "APPLICABILITY_NOT_PUBLISHED"
        assert releases == 0

    async def test_compile_succeeds_after_the_real_publication_lifecycle(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            result = await _compiled(session)
            await session.commit()
        assert result["ok"] is True
        assert result["created"] is True
        assert result["release"]["status"] == "draft"

    async def test_the_manifest_comes_from_the_step8i_compiler(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            compiled = await _compiled(session)
            await session.commit()
            entry = await applicability_authoring.get_personal_applicability_entry(
                session, uuid.UUID(compiled["published_entry_id"]),
            )
            row = await _release_row(session, compiled["release"]["id"])
            expected = pack.build_release_manifest_from_published_entry(entry)
        assert row.manifest == expected
        assert row.content_hash == manifest_content_hash(parse_release_manifest(expected))
        # Nothing here was chosen by the operator: every decisive field is the
        # pack's own.
        assert row.manifest["policy_rules"][0]["action"] == pack.POLICY_ACTION
        assert row.manifest["semantic_rules"][0]["signal"] == pack.SEMANTIC_SIGNAL
        assert row.manifest["explanation_rules"][0]["reason_key"] == pack.REASON_KEY
        assert (
            row.manifest["explanation_rules"][0]["source_locator"]
            == pack.AAD_SOURCE_LOCATOR
        )

    async def test_the_compiled_release_begins_draft_and_unreviewed(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            compiled = await _compiled(session)
            await session.commit()
            row = await _release_row(session, compiled["release"]["id"])
        assert row.status == "draft"
        assert row.review_verification is None
        assert row.approved_by is None and row.approved_at is None
        assert row.activated_by is None and row.activated_at is None
        assert row.retired_by is None and row.retired_at is None

    async def test_repeated_compile_reuses_rather_than_spamming_versions(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            first = await _compiled(session)
            await session.commit()
            second = await operator.run_compile(session, actor=ACTOR)
            third = await operator.run_compile(session, actor=ACTOR)
            await session.commit()
            total = (
                await session.execute(select(func.count()).select_from(PersonalDecisionRelease))
            ).scalar_one()
        assert second["created"] is False and second["reused"] is True
        assert third["created"] is False and third["reused"] is True
        assert second["release"] == first["release"]
        assert third["release"] == first["release"]
        assert total == 1

    async def test_compile_reports_an_approved_match_without_creating_another(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            await session.commit()
            again = await operator.run_compile(session, actor=ACTOR)
            await session.commit()
            total = (
                await session.execute(select(func.count()).select_from(PersonalDecisionRelease))
            ).scalar_one()
        assert again["created"] is False
        assert again["release"]["status"] == "approved"
        assert again["release"]["id"] == release["id"]
        assert total == 1

    async def test_compile_reports_an_active_match_as_already_active(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _activated(session)
            await session.commit()
            again = await operator.run_compile(session, actor=ACTOR)
            await session.commit()
        assert again["already_active"] is True
        assert again["release"]["id"] == release["id"]
        assert again["release"]["status"] == "active"

    async def test_a_retired_release_is_never_automatically_resurrected(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _activated(session)
            await operator.run_deactivate(
                session,
                release_id=uuid.UUID(release["id"]),
                expected_content_hash=release["content_hash"],
                actor=ACTOR,
            )
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_compile(session, actor=ACTOR)
            await session.rollback()
            rows = (await session.execute(select(PersonalDecisionRelease))).scalars().all()
        refusal = _refusal(exc_info)
        assert refusal.code == "RELEASE_RETIRED"
        assert [row.status for row in rows] == ["retired"]

    async def test_compile_never_verifies_approves_or_activates(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            compiled = await _compiled(session)
            await session.commit()
            row = await _release_row(session, compiled["release"]["id"])
            claims = (await session.execute(select(EvidenceClaim))).scalars().all()
            active = await load_active_personal_decision_release(session)
        assert row.review_verification is None
        assert row.status == "draft"
        assert active is None
        # Publication states were set by the human lifecycle, never by compile.
        assert {claim.published_by for claim in claims} == {"human.publisher"}

    async def test_ambiguous_published_evidence_blocks_compile(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            prepared = await _published(session)
            entry = await applicability_authoring.get_personal_applicability_entry(
                session, uuid.UUID(prepared["applicability"]["entry_id"]),
            )
            second = await applicability_authoring.create_personal_applicability_draft(
                session,
                applicability_authoring.PersonalApplicabilityDraftInput(
                    category=PersonalApplicabilityCategory(pack.CATEGORY),
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
                    sources=tuple(
                        applicability_authoring.ExistingSourceInput(
                            source_key=source["source_key"], locator=source["locator"],
                        )
                        for source in entry["sources"]
                    ),
                ),
                author="someone.else",
            )
            await _human_publishes_applicability(session, uuid.UUID(second["id"]))
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_compile(session, actor=ACTOR)
            await session.rollback()
        refusal = _refusal(exc_info)
        assert refusal.code == "APPLICABILITY_AMBIGUOUS"
        assert len(refusal.details["candidates"]) == 2


# ---------------------------------------------------------------------------
# activate
# ---------------------------------------------------------------------------
class TestActivateRefuses:
    async def test_an_unknown_release_id(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_activate(
                    session,
                    release_id=uuid.uuid4(),
                    expected_content_hash="a" * 64,
                    actor=ACTOR,
                )
        assert _refusal(exc_info).code == "RELEASE_NOT_FOUND"

    def test_a_missing_expected_hash_at_the_cli_boundary(self) -> None:
        parser = operator.build_parser()
        release_id = str(uuid.uuid4())
        with pytest.raises(SystemExit):
            parser.parse_args(["activate", "--release-id", release_id, "--actor", ACTOR])
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "activate",
                    "--release-id", release_id,
                    "--expected-content-hash", "not-a-hash",
                    "--actor", ACTOR,
                ]
            )
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "activate",
                    "--expected-content-hash", "a" * 64,
                    "--actor", ACTOR,
                ]
            )
        parsed = parser.parse_args(
            [
                "activate",
                "--release-id", release_id,
                "--expected-content-hash", "a" * 64,
                "--actor", ACTOR,
            ]
        )
        assert str(parsed.release_id) == release_id

    async def test_a_wrong_expected_hash(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_activate(
                    session,
                    release_id=uuid.UUID(release["id"]),
                    expected_content_hash="b" * 64,
                    actor=ACTOR,
                )
            await session.rollback()
            row = await _release_row(session, release["id"])
        assert _refusal(exc_info).code == "CONTENT_HASH_MISMATCH"
        assert row.status == "approved"

    async def test_a_draft_release(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            compiled = await _compiled(session)
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_activate(
                    session,
                    release_id=uuid.UUID(compiled["release"]["id"]),
                    expected_content_hash=compiled["release"]["content_hash"],
                    actor=ACTOR,
                )
            await session.rollback()
            active = await load_active_personal_decision_release(session)
        assert _refusal(exc_info).code == "RELEASE_NOT_APPROVED"
        assert _refusal(exc_info).details["status"] == "draft"
        assert active is None

    async def test_a_retired_release(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _activated(session)
            await operator.run_deactivate(
                session,
                release_id=uuid.UUID(release["id"]),
                expected_content_hash=release["content_hash"],
                actor=ACTOR,
            )
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_activate(
                    session,
                    release_id=uuid.UUID(release["id"]),
                    expected_content_hash=release["content_hash"],
                    actor=ACTOR,
                )
            await session.rollback()
        assert _refusal(exc_info).code == "RELEASE_NOT_APPROVED"
        assert _refusal(exc_info).details["status"] == "retired"

    async def test_a_persisted_hash_inconsistent_with_its_manifest(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            row = await _release_row(session, release["id"])
            broken = copy.deepcopy(row.manifest)
            broken["semantic_rules"][0]["claim_version"] = 99
            await session.execute(
                update(PersonalDecisionRelease)
                .where(PersonalDecisionRelease.id == row.id)
                .values(manifest=broken)
            )
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_activate(
                    session,
                    release_id=uuid.UUID(release["id"]),
                    expected_content_hash=release["content_hash"],
                    actor=ACTOR,
                )
            await session.rollback()
        assert _refusal(exc_info).code == "MANIFEST_HASH_INCONSISTENT"

    async def test_a_manifest_modified_after_approval(self, db_clean) -> None:
        """The reason a customer would see, swapped in the database."""
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            row = await _release_row(session, release["id"])
            tampered = copy.deepcopy(row.manifest)
            tampered["explanation_rules"][0]["reason_key"] = "for_you.not_enough.copy"
            await session.execute(
                update(PersonalDecisionRelease)
                .where(PersonalDecisionRelease.id == row.id)
                .values(manifest=tampered)
            )
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_activate(
                    session,
                    release_id=uuid.UUID(release["id"]),
                    expected_content_hash=release["content_hash"],
                    actor=ACTOR,
                )
            await session.rollback()
            active = await load_active_personal_decision_release(session)
        assert _refusal(exc_info).code == "MANIFEST_HASH_INCONSISTENT"
        assert active is None

    async def test_a_manifest_that_no_longer_matches_the_compiler(
        self, db_clean
    ) -> None:
        """Self-consistent, still pack-shaped, but not what the compiler builds."""
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            row = await _release_row(session, release["id"])
            diverged = copy.deepcopy(row.manifest)
            for rule in diverged["semantic_rules"]:
                rule["claim_key"] = "personal-applicability:skin_care:somewhere-else"
            for explanation in diverged["explanation_rules"]:
                explanation["claim_key"] = "personal-applicability:skin_care:somewhere-else"
            new_hash = manifest_content_hash(parse_release_manifest(diverged))
            await session.execute(
                update(PersonalDecisionRelease)
                .where(PersonalDecisionRelease.id == row.id)
                .values(manifest=diverged, content_hash=new_hash)
            )
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_activate(
                    session,
                    release_id=uuid.UUID(release["id"]),
                    expected_content_hash=new_hash,
                    actor=ACTOR,
                )
            await session.rollback()
        assert _refusal(exc_info).code == "MANIFEST_DIVERGED_FROM_PACK"

    async def test_a_missing_published_applicability_entry(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            await session.execute(
                update(EvidenceClaim)
                .where(
                    EvidenceClaim.claim_type == "substance_personal_applicability"
                )
                .values(review_status=ReviewStatus.APPROVED.value)
            )
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_activate(
                    session,
                    release_id=uuid.UUID(release["id"]),
                    expected_content_hash=release["content_hash"],
                    actor=ACTOR,
                )
            await session.rollback()
        assert _refusal(exc_info).code == "APPLICABILITY_NOT_PUBLISHED"

    async def test_ambiguous_published_applicability(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            entry = await applicability_authoring.get_personal_applicability_entry(
                session,
                (
                    await session.execute(
                        select(EvidenceClaim.id).where(
                            EvidenceClaim.claim_type
                            == "substance_personal_applicability"
                        )
                    )
                ).scalars().first(),
            )
            second = await applicability_authoring.create_personal_applicability_draft(
                session,
                applicability_authoring.PersonalApplicabilityDraftInput(
                    category=PersonalApplicabilityCategory(pack.CATEGORY),
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
                    sources=tuple(
                        applicability_authoring.ExistingSourceInput(
                            source_key=source["source_key"], locator=source["locator"],
                        )
                        for source in entry["sources"]
                    ),
                ),
                author="someone.else",
            )
            await _human_publishes_applicability(session, uuid.UUID(second["id"]))
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_activate(
                    session,
                    release_id=uuid.UUID(release["id"]),
                    expected_content_hash=release["content_hash"],
                    actor=ACTOR,
                )
            await session.rollback()
            active = await load_active_personal_decision_release(session)
        assert _refusal(exc_info).code == "APPLICABILITY_AMBIGUOUS"
        assert active is None

    async def test_evidence_that_is_no_longer_eligible(self, db_clean) -> None:
        """The claim still matches the pack; its source path is no longer reviewed."""
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            await session.execute(
                update(EvidenceClaimSource).values(reviewed_by=None, reviewed_at=None)
            )
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_activate(
                    session,
                    release_id=uuid.UUID(release["id"]),
                    expected_content_hash=release["content_hash"],
                    actor=ACTOR,
                )
            await session.rollback()
            row = await _release_row(session, release["id"])
            active = await load_active_personal_decision_release(session)
        assert _refusal(exc_info).code == "RELEASE_VALIDATION_FAILED"
        assert row.status == "approved"
        assert active is None

    async def test_missing_reviewed_reason_copy(self, db_clean, monkeypatch) -> None:
        monkeypatch.delitem(for_you_copy.FOR_YOU_REASON_COPY, pack.REASON_KEY)
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_activate(
                    session,
                    release_id=uuid.UUID(release["id"]),
                    expected_content_hash=release["content_hash"],
                    actor=ACTOR,
                )
            await session.rollback()
            active = await load_active_personal_decision_release(session)
        assert _refusal(exc_info).code == "REASON_COPY_MISSING"
        assert active is None

    async def test_reason_copy_that_is_not_the_reviewed_intent(
        self, db_clean, monkeypatch
    ) -> None:
        monkeypatch.setitem(
            for_you_copy.FOR_YOU_REASON_COPY,
            pack.REASON_KEY,
            "Some other sentence nobody reviewed for this pack.",
        )
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_activate(
                    session,
                    release_id=uuid.UUID(release["id"]),
                    expected_content_hash=release["content_hash"],
                    actor=ACTOR,
                )
            await session.rollback()
        assert _refusal(exc_info).code == "REASON_COPY_MISMATCH"

    async def test_missing_reviewed_verdict_copy(self, db_clean, monkeypatch) -> None:
        monkeypatch.delitem(for_you_copy.FOR_YOU_VERDICT_COPY, "for_you.verdict.buy")
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_activate(
                    session,
                    release_id=uuid.UUID(release["id"]),
                    expected_content_hash=release["content_hash"],
                    actor=ACTOR,
                )
            await session.rollback()
        assert _refusal(exc_info).code == "VERDICT_COPY_MISSING"

    async def test_a_different_release_is_already_active(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            row = await _release_row(session, release["id"])
            other = await release_authoring.create_personal_decision_release_draft(
                session, _other_manifest(row.manifest), actor="someone.else",
            )
            other_id = uuid.UUID(other["id"])
            await _human_approves_release(session, other_id)
            await release_authoring.activate_personal_decision_release(
                session, other_id, actor="someone.else",
            )
            await session.commit()

            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_activate(
                    session,
                    release_id=uuid.UUID(release["id"]),
                    expected_content_hash=release["content_hash"],
                    actor=ACTOR,
                )
            await session.rollback()
            still = await load_active_personal_decision_release(session)
            ours = await _release_row(session, release["id"])
        refusal = _refusal(exc_info)
        assert refusal.code == "DIFFERENT_RELEASE_ACTIVE"
        assert refusal.details["active"][0]["id"] == other["id"]
        # The other release is untouched, and ours never became active.
        assert still is not None and str(still.release_id) == other["id"]
        assert ours.status == "approved"

    async def test_there_is_no_force_or_replace_switch(self) -> None:
        parser = operator.build_parser()
        for flag in ("--force", "--replace", "--latest", "--yes"):
            with pytest.raises(SystemExit):
                parser.parse_args(
                    [
                        "activate",
                        "--release-id", str(uuid.uuid4()),
                        "--expected-content-hash", "a" * 64,
                        "--actor", ACTOR,
                        flag,
                    ]
                )


class TestActivateGrantsNoGovernance:
    async def test_activate_never_verifies_approves_or_publishes_anything(
        self, db_clean
    ) -> None:
        """A refused activation leaves every governance field exactly as it was."""
        factory = get_sessionmaker()
        async with factory() as session:
            compiled = await _compiled(session)
            await session.commit()
            before = await _fingerprint(session)
            with pytest.raises(operator.OperatorRefusal):
                await operator.run_activate(
                    session,
                    release_id=uuid.UUID(compiled["release"]["id"]),
                    expected_content_hash=compiled["release"]["content_hash"],
                    actor=ACTOR,
                )
            await session.rollback()
            after = await _fingerprint(session)
            row = await _release_row(session, compiled["release"]["id"])
        assert after == before
        assert row.review_verification is None
        assert row.approved_by is None
        assert row.status == "draft"

    async def test_a_successful_activation_records_only_the_activation(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            await session.commit()
            evidence_before = await _fingerprint(session)
            await operator.run_activate(
                session,
                release_id=uuid.UUID(release["id"]),
                expected_content_hash=release["content_hash"],
                actor=ACTOR,
            )
            await session.commit()
            row = await _release_row(session, release["id"])
            evidence_after = await _fingerprint(session)
        # Evidence is untouched; only the release row moved.
        for table in ("evidence_claims", "evidence_sources", "evidence_claim_sources",
                      "substances", "substance_names"):
            assert evidence_after[table] == evidence_before[table]
        assert evidence_after["personal_decision_releases"] != (
            evidence_before["personal_decision_releases"]
        )
        assert row.status == "active"
        assert row.activated_by == ACTOR
        # The attestations and approval still name the humans who made them.
        assert row.approved_by == "human.approver"
        assert row.review_verification is not None

    async def test_activating_the_same_release_twice_is_idempotent(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _activated(session)
            await session.commit()
            before = await _fingerprint(session)
            again = await operator.run_activate(
                session,
                release_id=uuid.UUID(release["id"]),
                expected_content_hash=release["content_hash"],
                actor="a.different.operator",
            )
            await session.commit()
            after = await _fingerprint(session)
            row = await _release_row(session, release["id"])
        assert again["already_active"] is True
        assert again["changed"] is False
        assert after == before
        assert row.activated_by == ACTOR


# ---------------------------------------------------------------------------
# deactivate
# ---------------------------------------------------------------------------
class TestDeactivate:
    async def test_deactivating_the_exact_release_leaves_zero_active(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _activated(session)
            await session.commit()
            result = await operator.run_deactivate(
                session,
                release_id=uuid.UUID(release["id"]),
                expected_content_hash=release["content_hash"],
                actor=ACTOR,
            )
            await session.commit()
            active = await load_active_personal_decision_release(session)
        assert result["changed"] is True
        assert result["release"]["status"] == "retired"
        assert result["runtime"]["active"] is False
        assert active is None

    async def test_a_wrong_hash_refuses(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _activated(session)
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_deactivate(
                    session,
                    release_id=uuid.UUID(release["id"]),
                    expected_content_hash="c" * 64,
                    actor=ACTOR,
                )
            await session.rollback()
            active = await load_active_personal_decision_release(session)
        assert _refusal(exc_info).code == "CONTENT_HASH_MISMATCH"
        assert active is not None

    async def test_it_refuses_to_deactivate_somebody_elses_active_release(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            row = await _release_row(session, release["id"])
            other = await release_authoring.create_personal_decision_release_draft(
                session, _other_manifest(row.manifest), actor="someone.else",
            )
            other_id = uuid.UUID(other["id"])
            await _human_approves_release(session, other_id)
            await release_authoring.activate_personal_decision_release(
                session, other_id, actor="someone.else",
            )
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_deactivate(
                    session,
                    release_id=other_id,
                    expected_content_hash=other["content_hash"],
                    actor=ACTOR,
                )
            await session.rollback()
            active = await load_active_personal_decision_release(session)
        assert _refusal(exc_info).code == "NOT_THE_STEP8I_PACK"
        assert active is not None and str(active.release_id) == other["id"]

    async def test_an_approved_but_never_active_release_refuses(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _approved(session)
            await session.commit()
            with pytest.raises(operator.OperatorRefusal) as exc_info:
                await operator.run_deactivate(
                    session,
                    release_id=uuid.UUID(release["id"]),
                    expected_content_hash=release["content_hash"],
                    actor=ACTOR,
                )
            await session.rollback()
        assert _refusal(exc_info).code == "RELEASE_NOT_ACTIVE"

    async def test_deactivating_twice_is_idempotent_and_restores_nothing(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            release = await _activated(session)
            await operator.run_deactivate(
                session,
                release_id=uuid.UUID(release["id"]),
                expected_content_hash=release["content_hash"],
                actor=ACTOR,
            )
            await session.commit()
            before = await _fingerprint(session)
            again = await operator.run_deactivate(
                session,
                release_id=uuid.UUID(release["id"]),
                expected_content_hash=release["content_hash"],
                actor=ACTOR,
            )
            await session.commit()
            after = await _fingerprint(session)
            active = await load_active_personal_decision_release(session)
        assert again["changed"] is False
        assert again["already_inactive"] is True
        assert after == before
        assert active is None


# ---------------------------------------------------------------------------
# The whole path, end to end, through the real customer API
# ---------------------------------------------------------------------------
async def _register_device(app_client) -> dict[str, str]:
    response = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert response.status_code == 201, response.text
    return {"X-Device-Token": response.json()["token"]}


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


async def _confirmed_device(app_client, registered_supabase_user) -> dict[str, Any]:
    token, account_id = await registered_supabase_user()
    headers = await _register_device(app_client)
    claimed = await app_client.post(
        "/api/v2/scan/device/claim", headers={**headers, **auth(token)},
    )
    assert claimed.status_code == 200, claimed.text
    run_id = await _seed_run(account_id)
    confirmed = await app_client.post(
        CONFIRM_URL,
        headers={**headers, **auth(token)},
        json={
            "barcode": BARCODE,
            "ai_run_id": str(run_id),
            "client_scan_id": uuid.uuid4().hex,
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    return {"token": token, "account_id": account_id, "headers": headers}


async def _for_you(app_client, headers, token, *, safety=None):
    payload: dict[str, Any] = {"barcode": BARCODE}
    if safety is not None:
        payload["safety"] = safety
    return await app_client.post(
        FOR_YOU_URL, headers={**headers, **auth(token)}, json=payload,
    )


async def _record_dry_skin(account_id: uuid.UUID) -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        profile = AppearanceProfile(account_id=account_id)
        session.add(profile)
        await session.flush()
        for key, value in (
            (pack.FACT_KEY, "often_dry_or_tight"),
            ("care_skin_sensitivity", "rarely_reactive"),
        ):
            session.add(ProfileAttribute(
                profile_id=profile.id, key=key, value=value, source="user_declared",
                confidence=1.0, verification_state="confirmed",
            ))
        await session.commit()


def _assert_no_decision(body: dict[str, Any]) -> None:
    result = body["result"]
    assert result["status"] != "decision_presentable"
    assert result["action"] is None
    assert result["verdict_key"] is None
    assert result["verdict_text"] is None
    assert result["citation"] is None
    assert result["reason_key"].startswith("for_you.not_enough.")
    blob = json.dumps(body)
    for token in ("BUY", "WAIT", "SKIP", pack.REASON_KEY, pack.AAD_SOURCE_URL):
        assert token not in blob


class TestHappyPathIntegration:
    async def test_the_whole_operator_path_produces_one_reviewed_buy_and_stops(
        self, db_clean, app_client, registered_supabase_user,
    ) -> None:
        device = await _confirmed_device(app_client, registered_supabase_user)
        await _record_dry_skin(device["account_id"])
        headers, token = device["headers"], device["token"]
        factory = get_sessionmaker()

        # 1. prepare — drafts only.
        async with factory() as session:
            prepared = await operator.run_prepare(session, actor=ACTOR)
            await session.commit()
        entry_id = uuid.UUID(prepared["applicability"]["entry_id"])

        # 2. A draft alone gives the customer nothing.
        _assert_no_decision((await _for_you(app_client, headers, token)).json())

        # 3-4. The two human review lifecycles, through the real domain functions.
        async with factory() as session:
            await _human_publishes_identity(session)
            await _human_publishes_applicability(session, entry_id)
            await session.commit()

        # 5. compile.
        async with factory() as session:
            compiled = await operator.run_compile(session, actor=ACTOR)
            await session.commit()
        release_id = uuid.UUID(compiled["release"]["id"])
        content_hash = compiled["release"]["content_hash"]

        # 6. A compiled but inactive release still decides nothing.
        _assert_no_decision((await _for_you(app_client, headers, token)).json())

        # 7-9. Release verification, validation and approval — all human.
        async with factory() as session:
            approved = await _human_approves_release(session, release_id)
            await session.commit()
        assert approved["status"] == "approved"
        _assert_no_decision((await _for_you(app_client, headers, token)).json())

        # 10. The operator activates the exact release, named by id and hash.
        async with factory() as session:
            activated = await operator.run_activate(
                session,
                release_id=release_id,
                expected_content_hash=content_hash,
                actor=ACTOR,
            )
            await session.commit()
        assert activated["changed"] is True

        # 11-12. The runtime loader returns exactly that release.
        async with factory() as session:
            loaded = await load_active_personal_decision_release(session)
        assert loaded is not None
        assert str(loaded.release_id) == compiled["release"]["id"]
        assert loaded.content_hash == content_hash

        # 13-16. The real customer endpoint, over the real confirmed pack.
        response = await _for_you(app_client, headers, token)
        assert response.status_code == 200, response.text
        body = response.json()
        result = body["result"]
        assert result["status"] == "decision_presentable"
        assert result["action"] == pack.POLICY_ACTION
        assert result["verdict_key"] == "for_you.verdict.buy"
        assert result["verdict_text"] == for_you_copy.FOR_YOU_VERDICT_COPY[
            "for_you.verdict.buy"
        ]
        assert result["verdict_text"] == "BUY"
        assert result["reason_key"] == pack.REASON_KEY
        assert result["reason_text"] == pack.FUTURE_REASON_INTENT
        assert result["reason_text"] == for_you_copy.FOR_YOU_REASON_COPY[pack.REASON_KEY]
        citation = result["citation"]
        assert citation["title"] == pack.AAD_SOURCE_TITLE
        assert citation["publisher"] == pack.AAD_SOURCE_PUBLISHER
        assert citation["canonical_url"] == pack.AAD_SOURCE_URL
        assert citation["locator"] == pack.AAD_SOURCE_LOCATOR
        assert citation["publication_date"] == pack.AAD_SOURCE_PUBLICATION_DATE
        assert citation["version_or_revision"] == pack.AAD_SOURCE_VERSION
        assert citation["jurisdiction"] == pack.AAD_SOURCE_JURISDICTION
        # The PubMed source is reviewed evidence, but it is not this reason's
        # citation, and it is never shown beside a sentence it does not carry.
        assert pack.PUBMED_SOURCE_URL not in json.dumps(body)
        assert body["release"]["id"] == compiled["release"]["id"]
        assert body["release"]["content_hash"] == content_hash

        # 17. The client sent a barcode and nothing else: the request model
        # has no field that could carry an action, a reason, a release, a
        # citation or a category.
        from app.api.v2.skin_care_personal_decision import SkinCareForYouBody

        assert set(SkinCareForYouBody.model_fields) == {"barcode", "safety"}

        # 18-20. Emergency stop, and the decision is gone.
        async with factory() as session:
            stopped = await operator.run_deactivate(
                session,
                release_id=release_id,
                expected_content_hash=content_hash,
                actor=ACTOR,
            )
            await session.commit()
        assert stopped["changed"] is True
        after = (await _for_you(app_client, headers, token)).json()
        _assert_no_decision(after)

    async def test_a_client_supplied_decision_field_is_rejected_outright(
        self, db_clean, app_client, registered_supabase_user,
    ) -> None:
        device = await _confirmed_device(app_client, registered_supabase_user)
        await _record_dry_skin(device["account_id"])
        factory = get_sessionmaker()
        async with factory() as session:
            await _activated(session)
            await session.commit()
        for field, value in (
            ("action", "buy"),
            ("reason_key", pack.REASON_KEY),
            ("release_id", str(uuid.uuid4())),
            ("content_hash", "a" * 64),
            ("category", "skin_care"),
        ):
            response = await app_client.post(
                FOR_YOU_URL,
                headers={**device["headers"], **auth(device["token"])},
                json={"barcode": BARCODE, field: value},
            )
            assert response.status_code == 422, (field, response.text)

    async def test_the_hard_handoff_still_precedes_the_activated_release(
        self, db_clean, app_client, registered_supabase_user,
    ) -> None:
        """Activation must not weaken the medical boundary."""
        device = await _confirmed_device(app_client, registered_supabase_user)
        await _record_dry_skin(device["account_id"])
        factory = get_sessionmaker()
        async with factory() as session:
            await _activated(session)
            await session.commit()

        # Sanity: without the flags this exact setup is presentable.
        assert (
            (await _for_you(app_client, device["headers"], device["token"]))
            .json()["result"]["status"]
        ) == "decision_presentable"

        for flag in (
            "pregnancy",
            "breastfeeding",
            "medication_involved",
            "diagnosed_condition_involved",
            "subject_is_child",
        ):
            response = await _for_you(
                app_client, device["headers"], device["token"], safety={flag: True},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["result"]["status"] != "decision_presentable", flag
            assert body["result"]["action"] is None, flag
            assert body["result"]["citation"] is None, flag


# ---------------------------------------------------------------------------
# The unexpected-failure boundary
# ---------------------------------------------------------------------------
#: Unmistakable in any output. If this string ever reaches an operator's screen,
#: so could a real password.
SECRET_SENTINEL = "PHASE_B_SECRET_SENTINEL"
FAKE_DATABASE_URL = f"postgresql://operator:{SECRET_SENTINEL}@production.example/db"


class ExplodingDriverError(RuntimeError):
    """Stands in for whatever a driver, pool or config read might raise."""


#: Runs the real CLI boundary in a real process, with one operation replaced by
#: something that raises the way a driver would. A subprocess rather than an
#: in-process call on purpose: it is how an operator actually invokes the tool,
#: it captures stderr as an operator's terminal would, and `main` opens its own
#: event loop, which has no business being started inside the test session's.
_LEAK_PROBE = """
import importlib.util, sys

spec = importlib.util.spec_from_file_location("step8i_operator", {path!r})
op = importlib.util.module_from_spec(spec)
spec.loader.exec_module(op)


class ExplodingDriverError(RuntimeError):
    pass


async def _explode(session, **kwargs):
    raise ExplodingDriverError("could not connect to " + {url!r})


op.run_status = _explode
sys.exit(op.main(["status"]))
"""


class TestUnexpectedFailureBoundary:
    def test_an_unexpected_failure_never_leaks_what_it_said(self) -> None:
        """The one failure path the tool cannot reason about must say nothing.

        A driver, a connection attempt, a filesystem read or a future internal
        path can all raise with a connection string, a credential or an internal
        hostname in the message. The tool cannot inspect an unknown exception
        well enough to promise otherwise, so it prints a fixed structural result
        and the class name, and nothing else.
        """
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                _LEAK_PROBE.format(path=str(OPERATOR_PATH), url=FAKE_DATABASE_URL),
            ],
            capture_output=True,
            text=True,
            cwd=str(REPOSITORY_ROOT),
            env={**os.environ},
            timeout=180,
        )

        assert completed.returncode == 1, completed.stderr
        body = json.loads(completed.stdout)
        assert body["ok"] is False
        assert body["operation"] == "status"
        assert body["code"] == "UNEXPECTED_ERROR"
        assert body["message"] == operator.UNEXPECTED_ERROR_MESSAGE
        # Naming the class is allowed and is enough to route the incident.
        assert body["error_type"] == "ExplodingDriverError"
        assert set(body) == {"ok", "operation", "code", "message", "error_type"}

        everything = completed.stdout + completed.stderr
        assert SECRET_SENTINEL not in everything
        assert FAKE_DATABASE_URL not in everything
        assert "production.example" not in everything
        assert "could not connect" not in everything
        assert "Traceback" not in everything
        assert "operate_step8i_petrolatum_release.py" not in everything

    def test_the_generic_message_is_the_only_message(self) -> None:
        """No formatting of the exception survives anywhere in the boundary."""
        tokens = _executable_tokens(OPERATOR_PATH)
        for forbidden in ("{error}", "str(error)", "repr(error)", "format_exc", "{error!r}"):
            assert not any(forbidden in token for token in tokens), forbidden
        assert operator.UNEXPECTED_ERROR_MESSAGE == (
            "The operator command failed unexpectedly. No changes were committed."
        )

    async def test_an_unexpected_failure_rolls_the_transaction_back(
        self, db_clean, monkeypatch
    ) -> None:
        """Written, flushed, then blown up — and none of it survives.

        The rollback is asserted twice over, because either alone would be
        weaker than it looks. Closing the session would discard an uncommitted
        write anyway, so row-absence on its own cannot tell an explicit rollback
        from an accident of the context manager; and `after_soft_rollback` fires
        only for an explicit `rollback()` call, never on close, so it says the
        rollback really happened where the code claims it does.
        """
        marker = f"phase-b-rollback-{uuid.uuid4().hex[:8]}"
        rollbacks: list[str] = []

        def _record(session, previous_transaction) -> None:
            rollbacks.append("rollback")

        async def _write_then_explode(session, *, actor):
            session.add(
                Substance(
                    substance_key=marker,
                    entity_kind=pack.IDENTITY_ENTITY_KIND,
                    status="active",
                )
            )
            await session.flush()
            raise ExplodingDriverError(f"driver died mid-write: {FAKE_DATABASE_URL}")

        monkeypatch.setattr(operator, "run_prepare", _write_then_explode)
        args = operator.build_parser().parse_args(["prepare", "--actor", ACTOR])

        event.listen(SyncSession, "after_soft_rollback", _record)
        try:
            with pytest.raises(ExplodingDriverError):
                await operator._dispatch(args)
        finally:
            event.remove(SyncSession, "after_soft_rollback", _record)

        assert rollbacks, "the unexpected-failure path did not roll back explicitly"

        # A fresh session, so nothing is being read out of the failed one.
        factory = get_sessionmaker()
        async with factory() as session:
            survived = (
                await session.execute(
                    select(func.count())
                    .select_from(Substance)
                    .where(Substance.substance_key == marker)
                )
            ).scalar_one()
        assert survived == 0


# ---------------------------------------------------------------------------
# Static boundaries
# ---------------------------------------------------------------------------
def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_module_tree(path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _cli_options(parser: argparse.ArgumentParser) -> dict[str, set[str]]:
    """Every option each subcommand actually accepts, read off the parser."""
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return {
        name: {
            option
            for action in sub._actions
            for option in action.option_strings
        }
        for name, sub in subparsers.choices.items()
    }


def _executable_tokens(path: Path) -> set[str]:
    """Every identifier and non-docstring string literal in a module.

    Comments never reach the AST at all, and docstrings are dropped here, so a
    guard built on this asks what the code *does* rather than what its prose
    happens to mention.
    """
    tree = _module_tree(path)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    tokens: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.add(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.add(node.attr)
        elif isinstance(node, (ast.keyword, ast.arg)) and node.arg:
            tokens.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            tokens.add(node.name)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            tokens.add(node.value)
    return tokens


class TestStaticBoundaries:
    def test_normal_runtime_still_never_imports_the_knowledge_pack(self) -> None:
        allowed = {
            BACKEND_ROOT / "app" / "knowledge_packs" / "__init__.py",
            BACKEND_ROOT / "app" / "knowledge_packs" / "petrolatum_dry_skin_v1.py",
        }
        offenders = [
            path.relative_to(BACKEND_ROOT).as_posix()
            for path in (BACKEND_ROOT / "app").rglob("*.py")
            if path not in allowed
            and "app.knowledge_packs" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_the_operator_tool_lives_outside_the_application_package(self) -> None:
        assert OPERATOR_PATH.exists()
        assert OPERATOR_PATH.parent == REPOSITORY_ROOT / "scripts"
        offenders = [
            path.relative_to(BACKEND_ROOT).as_posix()
            for path in (BACKEND_ROOT / "app").rglob("*.py")
            if OPERATOR_PATH.stem in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_step8k_and_the_copy_catalogue_do_not_import_the_pack(self) -> None:
        for relative in (
            "app/api/v2/skin_care_personal_decision.py",
            "app/content/for_you_copy.py",
            "app/domains/product/personal_decision.py",
            "app/bootstrap/reference_data.py",
        ):
            path = BACKEND_ROOT / relative
            if not path.exists():
                continue
            assert not any(
                module.startswith("app.knowledge_packs")
                for module in _imported_modules(path)
            ), relative

    def test_the_operator_cli_exposes_only_operational_identity(self) -> None:
        """Asked of the parser itself, not of the file's prose.

        The whole surface an operator can steer is: who is acting, which exact
        release, and what that release must contain. Nothing here could select a
        different action, reason, citation, policy or signal, because no such
        option exists to pass.
        """
        options = _cli_options(operator.build_parser())
        assert set(options) == set(operator.OPERATIONS)
        assert options["status"] == {"-h", "--help"}
        assert options["prepare"] == {"-h", "--help", "--actor"}
        assert options["compile"] == {"-h", "--help", "--actor"}
        for name in ("activate", "deactivate"):
            assert options[name] == {
                "-h", "--help", "--actor", "--release-id", "--expected-content-hash",
            }, name

    def test_the_operator_never_selects_authority_by_recency(self) -> None:
        """No "latest", no "newest", no ordering by time — in code, not prose.

        Docstrings and comments are excluded on purpose: the module explains
        that it has no "latest" behaviour, and a raw text search would flag that
        sentence as the very thing it promises not to do.
        """
        tokens = _executable_tokens(OPERATOR_PATH)
        for forbidden in ("latest", "most_recent", "newest", "recent", "created_at"):
            offenders = sorted(t for t in tokens if forbidden in t.lower())
            assert offenders == [], (forbidden, offenders)

    def test_the_operator_defines_no_signal_action_or_reason_of_its_own(self) -> None:
        tree = _module_tree(OPERATOR_PATH)
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        # The verdict words and the reviewed reason sentence exist only in the
        # copy catalogue; the action, signal and reason key only in the pack.
        for forbidden in (
            "BUY",
            "WAIT",
            "SKIP",
            "supporting",
            "cautionary",
            pack.REASON_KEY,
            pack.SEMANTIC_RULE_ID,
            pack.POLICY_ID,
            pack.EXPLANATION_ID,
            pack.FUTURE_REASON_INTENT,
            pack.AAD_SOURCE_URL,
            pack.AAD_SOURCE_LOCATOR,
        ):
            assert forbidden not in literals, forbidden

    def test_the_existing_step8i_compiler_script_is_unchanged_in_contract(self) -> None:
        source = STEP8I_COMPILER_PATH.read_text(encoding="utf-8")
        assert 'parser.add_argument("input"' in source
        assert 'parser.add_argument("--output"' in source
        assert "build_release_manifest_from_published_entry" in source

    def test_production_activation_is_not_wired_into_anything_automatic(self) -> None:
        name = OPERATOR_PATH.name
        roots = [
            REPOSITORY_ROOT / ".github",
            BACKEND_ROOT / "migrations",
            BACKEND_ROOT / "app",
            REPOSITORY_ROOT / "docker-compose.yml",
        ]
        offenders: list[str] = []
        for root in roots:
            if not root.exists():
                continue
            paths = [root] if root.is_file() else list(root.rglob("*"))
            for path in paths:
                if not path.is_file() or path.suffix in {".pyc"}:
                    continue
                try:
                    body = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                if name in body:
                    offenders.append(path.relative_to(REPOSITORY_ROOT).as_posix())
        assert offenders == []

    def test_no_migration_or_forbidden_path_was_touched(self) -> None:
        changed = {
            line.strip()
            for line in subprocess.check_output(
                ["git", "diff", "--name-only", "HEAD"], cwd=REPOSITORY_ROOT, text=True
            ).splitlines()
            if line.strip()
        }
        for prefix in ("backend/migrations/", "frontend/", ".github/"):
            assert not any(path.startswith(prefix) for path in changed), prefix
        assert not any("requirements" in path for path in changed)
        assert not any(path.endswith("yarn.lock") for path in changed)
