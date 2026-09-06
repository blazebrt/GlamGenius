"""Step 8H — governed personal decision knowledge release.

The decisive test in this file is ``TestEndToEnd``: it authors real evidence
through the real Step 8G lifecycle, builds a release around it, approves and
activates it through the real Step 8H services, runs the real Step 8B against
synthetic Step 7C/8A inputs, and then runs the real 8C -> 8D -> 8E -> 8F chain
against the loaded active release. Nothing in that path is hand-built; if any
link is wrong, it fails.

Everything else exists to prove the ways it must refuse.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import inspect
import re
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest
from app.domains.evidence import authoring as evidence_authoring
from app.domains.evidence.enums import (
    ClaimSourceRelationship,
    ClaimStatus,
    ClaimType,
    EvidenceDomain,
    EvidenceStrength,
    EvidenceTier,
    ReviewStatus,
    SourceStatus,
    SourceType,
)
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource
from app.domains.formulas.parser import ParseStatus
from app.domains.personal_applicability import authoring as evidence_8g
from app.domains.personal_applicability.enums import (
    PersonalApplicabilityCategory,
    PersonalApplicabilityStatus,
)
from app.domains.personal_applicability.schema import PERSONAL_APPLICABILITY_SCHEMA_VERSION
from app.domains.personal_applicability.service import apply_personal_evidence
from app.domains.personal_decision_aggregation import (
    PersonalSignalSet,
    aggregate_personal_decision_signals,
)
from app.domains.personal_decision_aggregation import service as aggregation_service
from app.domains.personal_decision_explanation import (
    PersonalDecisionPresentationStatus,
    present_personal_decision,
)
from app.domains.personal_decision_policy import (
    PersonalDecisionAction,
    evaluate_personal_decision_policy,
)
from app.domains.personal_decision_release import authoring as release_authoring
from app.domains.personal_decision_release.enums import (
    PERSONAL_DECISION_RELEASE_KEY,
    PersonalDecisionReleaseStatus,
    PersonalDecisionReleaseValidationCode,
)
from app.domains.personal_decision_release.manifest import (
    MAX_CANONICAL_MANIFEST_BYTES,
    MAX_EXPLANATION_RULES,
    MAX_POLICY_RULES,
    MAX_SEMANTIC_RULES,
    PERSONAL_DECISION_RELEASE_MANIFEST_SCHEMA_VERSION,
    PersonalDecisionReleaseManifestError,
    canonical_json,
    canonical_manifest,
    manifest_content_hash,
    parse_release_manifest,
)
from app.domains.personal_decision_release.models import PersonalDecisionRelease
from app.domains.personal_decision_release.runtime import (
    evaluate_personal_decision_with_release,
    load_active_personal_decision_release,
    materialise_active_release,
    select_active_release,
)
from app.domains.personal_decision_release.validation import _SIGNAL_SETS as RELEASE_SIGNAL_SETS
from app.domains.personal_decision_release.validation import (
    PersonalDecisionReleaseInvariantError,
    PersonalDecisionReleaseValidationError,
    ReleaseVerification,
    assert_manifest_carries_no_personal_data,
    parse_release_verification,
    validate_release_evidence,
    validate_release_manifest,
    validate_release_structure,
)
from app.domains.personal_decision_semantics import project_personal_decision_semantics
from app.domains.personal_lens.enums import PersonalLensCategory, PersonalLensStatus
from app.domains.personal_lens.service import (
    PersonalLensContext,
    PersonalLensFact,
    PersonalLensHandoff,
)
from app.domains.product.formula_projection import FormulaProjectionProvenance
from app.domains.substance_interpretation.enums import (
    InterpretationCategory,
    InterpretationStatus,
    ProjectedIdentityStatus,
)
from app.domains.substance_interpretation.service import (
    FormulaIngredientInterpretation,
    LabelSnapshotFormulaInterpretation,
)
from app.shared.database import sql
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import ConflictError, NotFoundError
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import auth

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIR = BACKEND_ROOT / "app" / "domains" / "personal_decision_release"
PURE_DOMAINS = (
    "personal_decision_semantics",
    "personal_decision_aggregation",
    "personal_decision_policy",
    "personal_decision_explanation",
)
BASE_REVISION = "b8c9d0e1f2"
ADMIN = "/api/v2/admin/personal-decision-releases"

SUBSTANCE = "glycerin"
CATEGORY = PersonalApplicabilityCategory.SKIN_CARE
FACT_KEY = "care_skin_sensitivity"
FACT_VALUE = "sometimes_reactive"
REASON_KEY = "for_you.reviewed.synthetic"


# ---------------------------------------------------------------------------
# Manifest fixtures
# ---------------------------------------------------------------------------


def _semantic(
    *,
    rule_id: str = "sem.a",
    rule_version: str = "1",
    category: str = CATEGORY.value,
    substance_key: str = SUBSTANCE,
    claim_key: str = "claim.a",
    claim_version: int = 1,
    signal: str = "supporting",
) -> dict:
    return {
        "rule_id": rule_id,
        "rule_version": rule_version,
        "category": category,
        "substance_key": substance_key,
        "claim_key": claim_key,
        "claim_version": claim_version,
        "signal": signal,
    }


def _policy(
    *,
    policy_id: str = "pol.a",
    policy_version: str = "1",
    category: str = CATEGORY.value,
    identities: list[tuple[str, str]] | None = None,
    signal_set: str = PersonalSignalSet.SUPPORTING_ONLY.value,
    unresolved: bool = False,
    ambiguous: bool = False,
    evidence_gap: bool = False,
    action: str = PersonalDecisionAction.BUY.value,
) -> dict:
    return {
        "policy_id": policy_id,
        "policy_version": policy_version,
        "category": category,
        "semantic_rule_identities": [
            {"rule_id": rule_id, "rule_version": rule_version}
            for rule_id, rule_version in (identities or [("sem.a", "1")])
        ],
        "signal_set": signal_set,
        "has_identity_unresolved": unresolved,
        "has_identity_ambiguous": ambiguous,
        "has_personal_evidence_gap": evidence_gap,
        "action": action,
    }


def _explanation(
    *,
    explanation_id: str = "exp.a",
    explanation_version: str = "1",
    policy_id: str = "pol.a",
    policy_version: str = "1",
    action: str = PersonalDecisionAction.BUY.value,
    semantic_rule_id: str = "sem.a",
    semantic_rule_version: str = "1",
    substance_key: str = SUBSTANCE,
    claim_key: str = "claim.a",
    claim_version: int = 1,
    source_key: str = "src.a",
    source_locator: str | None = "section 2",
    reason_key: str = REASON_KEY,
) -> dict:
    return {
        "explanation_id": explanation_id,
        "explanation_version": explanation_version,
        "policy_id": policy_id,
        "policy_version": policy_version,
        "action": action,
        "semantic_rule_id": semantic_rule_id,
        "semantic_rule_version": semantic_rule_version,
        "substance_key": substance_key,
        "claim_key": claim_key,
        "claim_version": claim_version,
        "source_key": source_key,
        "source_locator": source_locator,
        "reason_key": reason_key,
    }


def _manifest(
    semantic_rules: list[dict] | None = None,
    policy_rules: list[dict] | None = None,
    explanation_rules: list[dict] | None = None,
    *,
    schema_version: int = PERSONAL_DECISION_RELEASE_MANIFEST_SCHEMA_VERSION,
) -> dict:
    return {
        "schema_version": schema_version,
        "semantic_rules": [_semantic()] if semantic_rules is None else semantic_rules,
        "policy_rules": [_policy()] if policy_rules is None else policy_rules,
        "explanation_rules": [_explanation()] if explanation_rules is None else explanation_rules,
    }


def _release_verification(**overrides) -> ReleaseVerification:
    values = {
        "founder_review_completed": True,
        "claude_review_completed": True,
        "codex_review_completed": True,
        "independent_reviews_agree": True,
        "adversarial_review_passed": True,
        "unresolved_doubt": False,
    }
    values.update(overrides)
    return ReleaseVerification(**values)


# ---------------------------------------------------------------------------
# Evidence fixtures
# ---------------------------------------------------------------------------


def _publication_verification() -> dict[str, bool]:
    return {
        "source_opened": True,
        "founder_verified_fact": True,
        "claude_review_completed": True,
        "codex_review_completed": True,
        "independent_reviews_agree": True,
        "adversarial_review_passed": True,
        "unresolved_doubt": False,
    }


def _payload(category: PersonalApplicabilityCategory = CATEGORY) -> dict:
    return {
        "substance_personal_applicability": {
            "schema_version": PERSONAL_APPLICABILITY_SCHEMA_VERSION,
            "category": category.value,
            "all_of": [
                {
                    "fact_key": FACT_KEY,
                    "operator": "equals_any",
                    "values": [FACT_VALUE, "often_reactive"],
                }
            ],
        },
        "publication_verification": _publication_verification(),
    }


def _hair_payload() -> dict:
    """A payload that is valid for hair_care, used to test category disagreement."""
    return {
        "substance_personal_applicability": {
            "schema_version": PERSONAL_APPLICABILITY_SCHEMA_VERSION,
            "category": PersonalApplicabilityCategory.HAIR_CARE.value,
            "all_of": [
                {
                    "fact_key": "care_hair_processing",
                    "operator": "contains_any",
                    "values": ["coloured", "bleached"],
                }
            ],
        },
        "publication_verification": _publication_verification(),
    }


_DOMAIN_BY_CATEGORY = {
    PersonalApplicabilityCategory.PACKAGED_FOOD: EvidenceDomain.NUTRITION.value,
    PersonalApplicabilityCategory.SKIN_CARE: EvidenceDomain.SKIN_CARE.value,
    PersonalApplicabilityCategory.HAIR_CARE: EvidenceDomain.HAIR_CARE.value,
    PersonalApplicabilityCategory.COSMETICS: EvidenceDomain.COSMETICS.value,
}


async def _publish_claim(
    session,
    *,
    claim_key: str = "claim.a",
    claim_version: int = 1,
    source_key: str = "src.a",
    locator: str | None = "section 2",
    category: PersonalApplicabilityCategory = CATEGORY,
    subject_key: str = SUBSTANCE,
    subject_type: str = "substance",
    claim_type: str = ClaimType.SUBSTANCE_PERSONAL_APPLICABILITY.value,
    domain: str | None = None,
    review_status: str = ReviewStatus.PUBLISHED.value,
    evidence_tier: str = EvidenceTier.CLINICALLY_STUDIED.value,
    ai_generated: bool = False,
    structured_value: object | None = None,
    source_type: str = SourceType.PEER_REVIEWED_RESEARCH.value,
    source_status: str = SourceStatus.ACTIVE.value,
    relationship: str = ClaimSourceRelationship.SUPPORTS.value,
    reviewed_link: bool = True,
    source_url: str | None = "https://example.org/research/glycerin",
    license_note: str | None = "Use recorded for evidence review.",
    evidence_strength: str = EvidenceStrength.STRONG.value,
    extra_eligible_source: bool = False,
) -> EvidenceClaim:
    """A published, Step 8B-eligible claim written directly.

    Used where the test is about Step 8H's own refusals and the authoring
    lifecycle is not what is being exercised. The end-to-end test deliberately
    does not use this: it goes through the real Step 8G path instead.
    """
    now = utcnow()
    source = EvidenceSource(
        source_key=source_key,
        source_series_key=f"{source_key}.series",
        source_type=source_type,
        title="Reviewed research",
        publisher="Example Journal",
        publication_date=date(2025, 2, 3),
        version_or_revision="2025",
        jurisdiction="global",
        canonical_url=source_url,
        accessed_at=now,
        status=source_status,
        license_or_use_note=license_note,
    )
    claim = EvidenceClaim(
        claim_key=claim_key,
        claim_version=claim_version,
        domain=domain or _DOMAIN_BY_CATEGORY[category],
        subject_type=subject_type,
        subject_key=subject_key,
        claim_type=claim_type,
        summary="The reviewed source reports this scoped body-context relationship.",
        scope="Exact declared fact values and the named substance only.",
        evidence_strength=evidence_strength,
        strength_rationale="The named independent source directly supports the scoped claim.",
        claim_status=ClaimStatus.SUPPORTED.value,
        review_status=review_status,
        regulatory_context="unknown",
        structured_value=_payload(category) if structured_value is None else structured_value,
        ai_generated=ai_generated,
        evidence_tier=evidence_tier,
        reviewed_at=now if review_status != ReviewStatus.DRAFT.value else None,
        reviewed_by="reviewer" if review_status != ReviewStatus.DRAFT.value else None,
        published_at=now if review_status == ReviewStatus.PUBLISHED.value else None,
        published_by="publisher" if review_status == ReviewStatus.PUBLISHED.value else None,
    )
    session.add_all([source, claim])
    await session.flush()
    session.add(
        EvidenceClaimSource(
            claim_id=claim.id,
            source_id=source.id,
            relationship=relationship,
            locator=locator,
            reviewed_at=now if reviewed_link else None,
            reviewed_by="reviewer" if reviewed_link else None,
        )
    )
    if extra_eligible_source:
        # A second, unimpeachable path. With it the claim stays projectable by
        # Step 8B however the first source is corrupted, which is what lets a
        # test aim at the explanation gate rather than the projectability gate.
        spare = EvidenceSource(
            source_key=f"{source_key}.spare",
            source_series_key=f"{source_key}.spare.series",
            source_type=SourceType.PEER_REVIEWED_RESEARCH.value,
            title="A second reviewed source",
            publisher="Example Journal",
            publication_date=date(2025, 2, 3),
            version_or_revision="2025",
            jurisdiction="global",
            canonical_url="https://example.org/research/spare",
            accessed_at=now,
            status=SourceStatus.ACTIVE.value,
            license_or_use_note="Use recorded for evidence review.",
        )
        session.add(spare)
        await session.flush()
        session.add(
            EvidenceClaimSource(
                claim_id=claim.id,
                source_id=spare.id,
                relationship=ClaimSourceRelationship.SUPPORTS.value,
                locator="appendix",
                reviewed_at=now,
                reviewed_by="reviewer",
            )
        )
    await session.flush()
    return claim


async def _author_and_publish(
    session,
    *,
    substance_key: str = SUBSTANCE,
    locator: str = "Section 4.2",
    entry_id: uuid.UUID | None = None,
) -> dict:
    """Create or revise governed evidence through the real Step 8G lifecycle.

    Draft -> approve -> record publication verification -> publish, using the
    production authoring services rather than hand-written rows, so the
    end-to-end test is anchored to evidence that actually went through the
    reviewed path.
    """
    entry = evidence_8g.PersonalApplicabilityDraftInput(
        category=CATEGORY,
        substance_key=substance_key,
        summary="The reviewed source reports this scoped body-context relationship.",
        scope="Exact declared fact values and the named substance only.",
        evidence_strength=EvidenceStrength.STRONG.value,
        strength_rationale="The named independent source directly supports the scoped claim.",
        conditions=(
            evidence_8g.AuthoringConditionInput(
                fact_key=FACT_KEY,
                values=(FACT_VALUE, "often_reactive"),
            ),
        ),
        sources=(
            evidence_8g.NewSourceInput(
                source_type=SourceType.PEER_REVIEWED_RESEARCH.value,
                title="Reviewed research on the named substance",
                publisher="Example Journal",
                canonical_url=f"https://example.org/research/{uuid.uuid4().hex}",
                license_or_use_note="Use recorded for evidence review.",
                locator=locator,
                publication_date=date(2025, 2, 3),
                version_or_revision="2025",
                jurisdiction="global",
            ),
        ),
    )
    if entry_id is None:
        view = await evidence_8g.create_personal_applicability_draft(
            session, entry, author="admin.synthetic"
        )
    else:
        view = await evidence_8g.edit_personal_applicability_entry(
            session, entry_id, entry, author="admin.synthetic"
        )
    created_id = uuid.UUID(view["id"])
    await evidence_8g.approve_personal_applicability_entry(
        session, created_id, reviewer="admin.synthetic"
    )
    await evidence_8g.record_personal_applicability_publication_verification(
        session,
        created_id,
        verification=evidence_authoring.VerificationInput(
            source_opened=True,
            founder_verified_fact=True,
            claude_review_completed=True,
            codex_review_completed=True,
            independent_reviews_agree=True,
            adversarial_review_passed=True,
            unresolved_doubt=False,
        ),
        actor="admin.synthetic",
    )
    return await evidence_8g.publish_personal_applicability_entry(
        session, created_id, publisher="admin.synthetic"
    )


def _bundle_for(published: dict, *, action: str = PersonalDecisionAction.BUY.value) -> dict:
    """A complete, coherent manifest built around one published claim."""
    source = published["sources"][0]
    return _manifest(
        [
            _semantic(
                claim_key=published["claim_key"],
                claim_version=published["claim_version"],
                substance_key=published["substance_key"],
            )
        ],
        [_policy(action=action)],
        [
            _explanation(
                action=action,
                claim_key=published["claim_key"],
                claim_version=published["claim_version"],
                substance_key=published["substance_key"],
                source_key=source["source_key"],
                source_locator=source["locator"],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Step 8A / 7C input fixtures for the real Step 8B
# ---------------------------------------------------------------------------


def _context(
    *,
    status: PersonalLensStatus = PersonalLensStatus.CONTEXT_AVAILABLE,
) -> PersonalLensContext:
    return PersonalLensContext(
        category=PersonalLensCategory(CATEGORY.value),
        status=status,
        profile_id=uuid.uuid4(),
        profile_version=7,
        body_facts=(
            PersonalLensFact(
                key=FACT_KEY,
                value=FACT_VALUE,
                source="user_declared",
                verification_state="confirmed",
                profile_attribute_id=uuid.uuid4(),
                explicit_unknown=False,
                last_reviewed_at=None,
            ),
        ),
        preference_facts=(),
        missing_information=(),
        handoff=None,
    )


def _handoff() -> PersonalLensHandoff:
    """The exact hard-handoff shape Step 8A produces."""
    return PersonalLensHandoff(
        reason="professional_handoff_required",
        message="Please speak to a qualified professional about this.",
    )


def _interpretation(*substance_keys: str) -> LabelSnapshotFormulaInterpretation:
    keys = substance_keys or (SUBSTANCE,)
    return LabelSnapshotFormulaInterpretation(
        provenance=FormulaProjectionProvenance(
            label_snapshot_id=uuid.uuid4(),
            barcode="8901234567890",
            version_number=4,
            content_fingerprint="b" * 64,
            scan_event_id=uuid.uuid4(),
        ),
        category=InterpretationCategory(CATEGORY.value),
        formula_status=ParseStatus.PARSED.value,
        ingredients=tuple(
            FormulaIngredientInterpretation(
                position=position,
                raw_name=key.title(),
                normalized_name=key,
                identity_status=ProjectedIdentityStatus.RESOLVED,
                substance_key=key,
                entity_kind="defined_substance",
                candidate_substance_keys=(key,),
                interpretation_status=InterpretationStatus.NOT_ENOUGH_INFORMATION,
                claims=(),
            )
            for position, key in enumerate(keys, start=1)
        ),
    )


async def _real_step_8b(session, *, handoff: object | None = None, substances=(SUBSTANCE,)):
    """The real Step 8B over synthetic Step 7C and Step 8A inputs."""
    from app.domains.personal_applicability.service import LabelSnapshotPersonalApplicability

    interpretation = _interpretation(*substances)
    context = _context()
    ingredients = await apply_personal_evidence(
        session, interpretation, context, category=CATEGORY
    )
    return LabelSnapshotPersonalApplicability(
        provenance=interpretation.provenance,
        category=CATEGORY,
        formula_status=interpretation.formula_status,
        profile_id=context.profile_id,
        profile_version=context.profile_version,
        context_status=context.status,
        ingredients=ingredients,
        handoff=handoff,
    )


async def _draft(session, manifest: dict, *, actor: str = "admin.synthetic") -> dict:
    return await release_authoring.create_personal_decision_release_draft(
        session, manifest, actor=actor
    )


async def _verified_draft(session, manifest: dict, **overrides) -> dict:
    view = await _draft(session, manifest)
    return await release_authoring.record_personal_decision_release_verification(
        session,
        uuid.UUID(view["id"]),
        verification=_release_verification(**overrides),
        actor="admin.synthetic",
    )


async def _approved(session, manifest: dict) -> dict:
    view = await _verified_draft(session, manifest)
    return await release_authoring.approve_personal_decision_release(
        session, uuid.UUID(view["id"]), actor="admin.synthetic"
    )


async def _activated(session, manifest: dict) -> dict:
    view = await _approved(session, manifest)
    return await release_authoring.activate_personal_decision_release(
        session, uuid.UUID(view["id"]), actor="admin.synthetic"
    )


def _reason(error: PersonalDecisionReleaseValidationError) -> PersonalDecisionReleaseValidationCode:
    return error.reason


# ---------------------------------------------------------------------------
# Manifest schema
# ---------------------------------------------------------------------------


class TestManifestSchema:
    def test_a_complete_manifest_parses_into_the_real_rule_dataclasses(self) -> None:
        manifest = parse_release_manifest(_manifest())
        assert manifest.schema_version == PERSONAL_DECISION_RELEASE_MANIFEST_SCHEMA_VERSION
        assert manifest.semantic_rules[0].rule_id == "sem.a"
        assert manifest.policy_rules[0].action is PersonalDecisionAction.BUY
        assert manifest.explanation_rules[0].reason_key == REASON_KEY
        assert manifest.policy_rules[0].semantic_rule_identities == frozenset({("sem.a", "1")})

    def test_an_empty_manifest_parses_but_knows_it_is_empty(self) -> None:
        manifest = parse_release_manifest(_manifest([], [], []))
        assert manifest.is_empty

    def test_an_unsupported_schema_version_is_refused(self) -> None:
        for version in (0, 2, "1", None, True):
            with pytest.raises(PersonalDecisionReleaseManifestError):
                parse_release_manifest(_manifest(schema_version=version))

    def test_unknown_and_missing_keys_are_both_refused(self) -> None:
        extra = _manifest()
        extra["unexpected"] = True
        with pytest.raises(PersonalDecisionReleaseManifestError, match="unknown"):
            parse_release_manifest(extra)

        missing = _manifest()
        del missing["policy_rules"]
        with pytest.raises(PersonalDecisionReleaseManifestError, match="missing"):
            parse_release_manifest(missing)

    def test_unknown_keys_inside_each_rule_are_refused(self) -> None:
        for build, key in (
            (lambda rule: _manifest([rule], [], []), "semantic"),
            (lambda rule: _manifest([], [rule], []), "policy"),
            (lambda rule: _manifest([], [], [rule]), "explanation"),
        ):
            rule = {"semantic": _semantic, "policy": _policy, "explanation": _explanation}[key]()
            rule["smuggled"] = "value"
            with pytest.raises(PersonalDecisionReleaseManifestError, match="unknown"):
                parse_release_manifest(build(rule))

    def test_malformed_types_are_refused_rather_than_coerced(self) -> None:
        cases = [
            _manifest([_semantic(claim_version="1")], [], []),
            _manifest([_semantic(claim_version=0)], [], []),
            _manifest([_semantic(claim_version=True)], [], []),
            _manifest([_semantic(rule_id="   ")], [], []),
            _manifest([_semantic(signal="neutral")], [], []),
            _manifest([_semantic(category="supplements")], [], []),
            _manifest([], [_policy(signal_set="strong")], []),
            _manifest([], [_policy(action="avoid")], []),
            _manifest([], [_policy(unresolved="no")], []),
            _manifest([], [], [_explanation(action="hold")]),
            _manifest([], [], [_explanation(claim_version=-1)]),
        ]
        for document in cases:
            with pytest.raises(PersonalDecisionReleaseManifestError):
                parse_release_manifest(document)

    def test_rule_collections_must_be_lists_not_strings_or_objects(self) -> None:
        for value in ("sem.a", {"rule_id": "sem.a"}, 3, None):
            document = _manifest()
            document["semantic_rules"] = value
            with pytest.raises(PersonalDecisionReleaseManifestError, match="not a list"):
                parse_release_manifest(document)

    def test_a_policy_may_not_name_one_semantic_identity_twice(self) -> None:
        document = _manifest([], [_policy(identities=[("sem.a", "1"), ("sem.a", "1")])], [])
        with pytest.raises(PersonalDecisionReleaseManifestError, match="more than once"):
            parse_release_manifest(document)

    def test_bounds_refuse_a_release_nobody_reviewed_line_by_line(self) -> None:
        too_many_semantics = [
            _semantic(rule_id=f"sem.{index}") for index in range(MAX_SEMANTIC_RULES + 1)
        ]
        with pytest.raises(PersonalDecisionReleaseManifestError, match="at most"):
            parse_release_manifest(_manifest(too_many_semantics, [], []))

        too_many_policies = [
            _policy(policy_id=f"pol.{index}") for index in range(MAX_POLICY_RULES + 1)
        ]
        with pytest.raises(PersonalDecisionReleaseManifestError, match="at most"):
            parse_release_manifest(_manifest([], too_many_policies, []))

        too_many_explanations = [
            _explanation(explanation_id=f"exp.{index}")
            for index in range(MAX_EXPLANATION_RULES + 1)
        ]
        with pytest.raises(PersonalDecisionReleaseManifestError, match="at most"):
            parse_release_manifest(_manifest([], [], too_many_explanations))

    def test_a_manifest_larger_than_the_canonical_cap_is_refused(self) -> None:
        padding = "x" * 4000
        rules = [
            _semantic(rule_id=f"sem.{index}", claim_key=f"{padding}.{index}")
            for index in range(MAX_SEMANTIC_RULES)
        ]
        document = _manifest(rules, [], [])
        assert len(canonical_json(
            parse_release_manifest(_manifest([], [], []))
        ).encode("utf-8")) < MAX_CANONICAL_MANIFEST_BYTES
        with pytest.raises(PersonalDecisionReleaseManifestError, match="canonical manifest"):
            parse_release_manifest(document)

    def test_a_null_locator_is_kept_and_a_blank_one_is_refused(self) -> None:
        parsed = parse_release_manifest(_manifest([], [], [_explanation(source_locator=None)]))
        assert parsed.explanation_rules[0].source_locator is None

        for blank in ("", "   ", "\t"):
            with pytest.raises(PersonalDecisionReleaseManifestError, match="source_locator"):
                parse_release_manifest(_manifest([], [], [_explanation(source_locator=blank)]))

    def test_a_padded_locator_is_preserved_exactly(self) -> None:
        """A locator is how a reader finds the passage; padding may be part of it."""
        padded = "  Section 4.2  "
        parsed = parse_release_manifest(_manifest([], [], [_explanation(source_locator=padded)]))
        assert parsed.explanation_rules[0].source_locator == padded
        assert canonical_manifest(parsed)["explanation_rules"][0]["source_locator"] == padded


# ---------------------------------------------------------------------------
# Canonicalisation and the content hash
# ---------------------------------------------------------------------------


class TestCanonicalisationAndHash:
    def test_the_hash_is_lowercase_sha256_hex(self) -> None:
        digest = manifest_content_hash(parse_release_manifest(_manifest()))
        assert re.fullmatch(r"[0-9a-f]{64}", digest)

    def test_semantic_input_order_does_not_change_the_manifest_or_hash(self) -> None:
        rules = [
            _semantic(rule_id="sem.a", claim_key="claim.a"),
            _semantic(rule_id="sem.b", claim_key="claim.b"),
            _semantic(rule_id="sem.c", claim_key="claim.c"),
        ]
        forward = parse_release_manifest(_manifest(rules, [], []))
        backward = parse_release_manifest(_manifest(list(reversed(rules)), [], []))
        assert canonical_manifest(forward) == canonical_manifest(backward)
        assert manifest_content_hash(forward) == manifest_content_hash(backward)
        assert forward.semantic_rules == backward.semantic_rules

    def test_policy_input_order_does_not_change_the_manifest_or_hash(self) -> None:
        rules = [_policy(policy_id=f"pol.{letter}") for letter in ("a", "b", "c")]
        forward = parse_release_manifest(_manifest([], rules, []))
        backward = parse_release_manifest(_manifest([], list(reversed(rules)), []))
        assert canonical_manifest(forward) == canonical_manifest(backward)
        assert manifest_content_hash(forward) == manifest_content_hash(backward)

    def test_semantic_identity_order_inside_a_policy_does_not_change_the_hash(self) -> None:
        identities = [("sem.a", "1"), ("sem.b", "1"), ("sem.c", "2")]
        forward = parse_release_manifest(_manifest([], [_policy(identities=identities)], []))
        backward = parse_release_manifest(
            _manifest([], [_policy(identities=list(reversed(identities)))], [])
        )
        assert canonical_manifest(forward) == canonical_manifest(backward)
        assert manifest_content_hash(forward) == manifest_content_hash(backward)

    def test_explanation_input_order_does_not_change_the_manifest_or_hash(self) -> None:
        rules = [_explanation(explanation_id=f"exp.{letter}") for letter in ("a", "b", "c")]
        forward = parse_release_manifest(_manifest([], [], rules))
        backward = parse_release_manifest(_manifest([], [], list(reversed(rules))))
        assert canonical_manifest(forward) == canonical_manifest(backward)
        assert manifest_content_hash(forward) == manifest_content_hash(backward)

    def test_reordered_input_produces_identical_rule_behaviour(self) -> None:
        """Same contents, different order: the same rules reach Steps 8C to 8F."""
        rules = [
            _semantic(rule_id="sem.a", claim_key="claim.a"),
            _semantic(rule_id="sem.b", claim_key="claim.b"),
        ]
        forward = parse_release_manifest(_manifest(rules, [], []))
        backward = parse_release_manifest(_manifest(list(reversed(rules)), [], []))
        assert [rule.target for rule in forward.semantic_rules] == [
            rule.target for rule in backward.semantic_rules
        ]

    def test_enums_are_serialised_by_value_and_keys_are_sorted(self) -> None:
        encoded = canonical_json(parse_release_manifest(_manifest()))
        assert '"signal":"supporting"' in encoded
        assert '"action":"buy"' in encoded
        assert '"signal_set":"supporting_only"' in encoded
        # Sorted keys and stable separators, so two identical manifests are
        # byte-identical however they were built.
        assert ", " not in encoded
        first_rule = canonical_manifest(parse_release_manifest(_manifest()))["semantic_rules"][0]
        assert list(first_rule) != sorted(first_rule)  # dict order is not the guarantee
        assert encoded.index('"explanation_rules"') < encoded.index('"policy_rules"')

    def test_a_different_manifest_produces_a_different_hash(self) -> None:
        base = manifest_content_hash(parse_release_manifest(_manifest()))
        changed = manifest_content_hash(
            parse_release_manifest(_manifest([_semantic(signal="cautionary")], [], []))
        )
        assert base != changed

    def test_reparsing_the_canonical_form_is_stable(self) -> None:
        once = parse_release_manifest(_manifest())
        twice = parse_release_manifest(canonical_manifest(once))
        assert canonical_manifest(once) == canonical_manifest(twice)
        assert manifest_content_hash(once) == manifest_content_hash(twice)


# ---------------------------------------------------------------------------
# Structural cross-validation
# ---------------------------------------------------------------------------


def _structure(document: dict, *, require_complete: bool = True) -> None:
    validate_release_structure(parse_release_manifest(document), require_complete=require_complete)


class TestStructuralValidation:
    def test_a_coherent_bundle_passes(self) -> None:
        _structure(_manifest())

    def test_an_empty_release_is_refused_for_approval(self) -> None:
        for document in (
            _manifest([], [], []),
            _manifest([_semantic()], [], []),
            _manifest([_semantic()], [_policy()], []),
            _manifest([], [_policy()], [_explanation()]),
        ):
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                _structure(document)
            assert _reason(caught.value) in {
                PersonalDecisionReleaseValidationCode.RELEASE_EMPTY,
                PersonalDecisionReleaseValidationCode.POLICY_SEMANTIC_NOT_IN_RELEASE,
                PersonalDecisionReleaseValidationCode.POLICY_EXPLANATION_MISSING,
            }

    def test_a_draft_may_be_incomplete(self) -> None:
        """Half-written drafts are normal mid-review and must be saveable."""
        for document in (
            _manifest([], [], []),
            _manifest([_semantic()], [], []),
            _manifest([_semantic()], [_policy()], []),
        ):
            _structure(document, require_complete=False)

    def test_a_policy_naming_a_semantic_rule_outside_the_release_is_refused(self) -> None:
        document = _manifest(
            [_semantic(rule_id="sem.a")],
            [_policy(identities=[("sem.missing", "1")])],
            [_explanation(semantic_rule_id="sem.missing")],
        )
        with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
            _structure(document)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.POLICY_SEMANTIC_NOT_IN_RELEASE
        )

    def test_a_policy_naming_the_wrong_semantic_version_is_refused(self) -> None:
        document = _manifest(
            [_semantic(rule_version="1")],
            [_policy(identities=[("sem.a", "2")])],
            [_explanation(semantic_rule_version="2")],
        )
        with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
            _structure(document)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.POLICY_SEMANTIC_NOT_IN_RELEASE
        )

    def test_a_policy_in_a_different_category_from_its_semantics_is_refused(self) -> None:
        document = _manifest(
            [_semantic(category=PersonalApplicabilityCategory.SKIN_CARE.value)],
            [_policy(category=PersonalApplicabilityCategory.HAIR_CARE.value)],
            [_explanation()],
        )
        with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
            _structure(document)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.POLICY_CATEGORY_MISMATCH
        )

    @pytest.mark.parametrize(
        ("signals", "declared"),
        [
            (["supporting"], PersonalSignalSet.SUPPORTING_ONLY.value),
            (["cautionary"], PersonalSignalSet.CAUTIONARY_ONLY.value),
            (["supporting", "cautionary"], PersonalSignalSet.MIXED.value),
        ],
    )
    def test_coherent_signal_sets_are_accepted(self, signals, declared) -> None:
        semantics = [
            _semantic(rule_id=f"sem.{index}", claim_key=f"claim.{index}", signal=signal)
            for index, signal in enumerate(signals)
        ]
        identities = [(rule["rule_id"], rule["rule_version"]) for rule in semantics]
        document = _manifest(
            semantics,
            [_policy(identities=identities, signal_set=declared)],
            [
                _explanation(
                    semantic_rule_id=semantics[0]["rule_id"],
                    claim_key=semantics[0]["claim_key"],
                )
            ],
        )
        _structure(document)

    @pytest.mark.parametrize(
        ("signals", "declared"),
        [
            (["supporting"], PersonalSignalSet.MIXED.value),
            (["supporting"], PersonalSignalSet.CAUTIONARY_ONLY.value),
            (["cautionary"], PersonalSignalSet.SUPPORTING_ONLY.value),
            (["supporting", "cautionary"], PersonalSignalSet.SUPPORTING_ONLY.value),
            (["supporting", "cautionary"], PersonalSignalSet.CAUTIONARY_ONLY.value),
        ],
    )
    def test_an_incoherent_signal_set_blocks_approval_and_is_not_rewritten(
        self, signals, declared
    ) -> None:
        semantics = [
            _semantic(rule_id=f"sem.{index}", claim_key=f"claim.{index}", signal=signal)
            for index, signal in enumerate(signals)
        ]
        identities = [(rule["rule_id"], rule["rule_version"]) for rule in semantics]
        document = _manifest(
            semantics,
            [_policy(identities=identities, signal_set=declared)],
            [
                _explanation(
                    semantic_rule_id=semantics[0]["rule_id"],
                    claim_key=semantics[0]["claim_key"],
                )
            ],
        )
        with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
            _structure(document)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.POLICY_SIGNAL_SET_MISMATCH
        )
        # The manifest is not repaired to match reality, and reality is not
        # rewritten to match the manifest.
        assert document["policy_rules"][0]["signal_set"] == declared

    def test_the_direction_set_map_matches_step_8d_exactly(self) -> None:
        """The two maps derive the same thing from different inputs; pin them together."""
        assert RELEASE_SIGNAL_SETS == aggregation_service._SIGNAL_SETS

    def test_a_semantic_rule_no_policy_references_blocks_approval(self) -> None:
        document = _manifest(
            [_semantic(rule_id="sem.a"), _semantic(rule_id="sem.orphan", claim_key="claim.b")],
            [_policy(identities=[("sem.a", "1")])],
            [_explanation()],
        )
        with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
            _structure(document)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.UNREFERENCED_SEMANTIC_RULE
        )

    def test_one_semantic_rule_may_serve_several_distinct_policy_targets(self) -> None:
        document = _manifest(
            [_semantic()],
            [
                _policy(policy_id="pol.a"),
                _policy(policy_id="pol.b", unresolved=True, action="wait"),
            ],
            [
                _explanation(explanation_id="exp.a", policy_id="pol.a"),
                _explanation(explanation_id="exp.b", policy_id="pol.b", action="wait"),
            ],
        )
        _structure(document)

    def test_a_policy_with_no_explanation_blocks_approval(self) -> None:
        document = _manifest(
            [_semantic()],
            [_policy(policy_id="pol.a"), _policy(policy_id="pol.b", unresolved=True)],
            [_explanation(policy_id="pol.a")],
        )
        with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
            _structure(document)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.POLICY_EXPLANATION_MISSING
        )

    def test_an_explanation_for_a_policy_outside_the_release_is_refused(self) -> None:
        document = _manifest(
            [_semantic()], [_policy()], [_explanation(policy_id="pol.missing")]
        )
        with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
            _structure(document)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EXPLANATION_POLICY_NOT_IN_RELEASE
        )

    def test_an_explanation_naming_the_wrong_policy_version_is_refused(self) -> None:
        document = _manifest(
            [_semantic()], [_policy(policy_version="1")], [_explanation(policy_version="2")]
        )
        with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
            _structure(document)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EXPLANATION_POLICY_NOT_IN_RELEASE
        )

    def test_an_explanation_carrying_a_different_action_is_refused(self) -> None:
        document = _manifest(
            [_semantic()],
            [_policy(action="buy")],
            [_explanation(action="skip")],
        )
        with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
            _structure(document)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EXPLANATION_ACTION_MISMATCH
        )

    def test_an_explanation_anchored_outside_its_policys_semantics_is_refused(self) -> None:
        document = _manifest(
            [_semantic(rule_id="sem.a"), _semantic(rule_id="sem.b", claim_key="claim.b")],
            [
                _policy(policy_id="pol.a", identities=[("sem.a", "1")]),
                _policy(policy_id="pol.b", identities=[("sem.b", "1")], unresolved=True),
            ],
            [
                _explanation(explanation_id="exp.a", policy_id="pol.a", semantic_rule_id="sem.b"),
                _explanation(
                    explanation_id="exp.b",
                    policy_id="pol.b",
                    semantic_rule_id="sem.b",
                    claim_key="claim.b",
                ),
            ],
        )
        with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
            _structure(document)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EXPLANATION_SEMANTIC_NOT_IN_POLICY
        )

    def test_an_explanation_anchored_to_the_wrong_semantic_version_is_refused(self) -> None:
        document = _manifest(
            [_semantic(rule_version="1")],
            [_policy(identities=[("sem.a", "1")])],
            [_explanation(semantic_rule_version="9")],
        )
        with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
            _structure(document)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EXPLANATION_SEMANTIC_NOT_IN_POLICY
        )

    @pytest.mark.parametrize(
        "override",
        [
            {"substance_key": "other-substance"},
            {"claim_key": "claim.other"},
            {"claim_version": 2},
        ],
    )
    def test_an_explanation_citing_a_different_evidence_identity_is_refused(self, override) -> None:
        document = _manifest([_semantic()], [_policy()], [_explanation(**override)])
        with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
            _structure(document)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EXPLANATION_EVIDENCE_ANCHOR_MISMATCH
        )

    def test_two_explanations_for_one_reviewed_decision_are_refused_by_step_8f(self) -> None:
        document = _manifest(
            [_semantic()],
            [_policy()],
            [_explanation(explanation_id="exp.a"), _explanation(explanation_id="exp.b")],
        )
        with pytest.raises(ValueError, match="at most one reviewed reason"):
            _structure(document)

    def test_two_semantic_rules_for_one_evidence_identity_are_refused_by_step_8c(self) -> None:
        document = _manifest(
            [_semantic(rule_id="sem.a"), _semantic(rule_id="sem.b")],
            [],
            [],
        )
        with pytest.raises(ValueError, match="at most one reviewed mapping"):
            _structure(document, require_complete=False)

    def test_two_policies_for_one_governed_state_are_refused_by_step_8e(self) -> None:
        document = _manifest(
            [], [_policy(policy_id="pol.a"), _policy(policy_id="pol.b")], []
        )
        with pytest.raises(ValueError, match="same governed state"):
            _structure(document, require_complete=False)

    def test_a_manifest_naming_a_person_or_a_scan_is_refused(self) -> None:
        for key in ("account_id", "profile_id", "scan_id", "label_snapshot_id", "medications"):
            document = _manifest()
            document["semantic_rules"][0][key] = "leaked"
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                assert_manifest_carries_no_personal_data(document)
            assert _reason(caught.value) is (
                PersonalDecisionReleaseValidationCode.RELEASE_PERSONAL_DATA_PRESENT
            )

    def test_the_closed_schema_alone_already_rejects_personal_keys(self) -> None:
        document = _manifest()
        document["semantic_rules"][0]["account_id"] = "leaked"
        with pytest.raises(PersonalDecisionReleaseManifestError, match="unknown"):
            parse_release_manifest(document)


# ---------------------------------------------------------------------------
# Review verification
# ---------------------------------------------------------------------------


class TestReviewVerification:
    def test_a_complete_block_parses(self) -> None:
        parsed = parse_release_verification(_release_verification().as_dict())
        assert parsed is not None
        assert parsed.unresolved_doubt is False

    def test_a_partial_or_malformed_block_is_no_attestation_at_all(self) -> None:
        complete = _release_verification().as_dict()
        for mutate in (
            lambda block: block.pop("founder_review_completed"),
            lambda block: block.update({"founder_review_completed": "yes"}),
            lambda block: block.update({"extra": True}),
        ):
            block = dict(complete)
            mutate(block)
            assert parse_release_verification(block) is None
        assert parse_release_verification(None) is None
        assert parse_release_verification([]) is None


# ---------------------------------------------------------------------------
# Evidence cross-validation
# ---------------------------------------------------------------------------


async def _validate(session, document: dict, *, require_complete: bool = True):
    return await validate_release_manifest(
        session, parse_release_manifest(document), require_complete=require_complete
    )


class TestEvidenceValidation:
    async def test_a_bundle_over_published_eligible_evidence_passes(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            report = await _validate(session, _manifest())
        assert report.semantic_evidence_checked == 1
        assert report.policies_checked == 1
        assert report.explanations_checked == 1

    async def test_a_semantic_rule_naming_no_claim_at_all_is_refused(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await _validate(session, _manifest())
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_PUBLISHED
        )

    @pytest.mark.parametrize(
        "review_status",
        [
            ReviewStatus.DRAFT.value,
            ReviewStatus.APPROVED.value,
            ReviewStatus.REVIEWED.value,
            ReviewStatus.SUPERSEDED.value,
            ReviewStatus.RETIRED.value,
        ],
    )
    async def test_an_unpublished_claim_blocks_the_release(self, db_clean, review_status) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session, review_status=review_status)
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await _validate(session, _manifest())
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_PUBLISHED
        )

    @pytest.mark.parametrize(
        "override",
        [
            {"ai_generated": True},
            {"evidence_tier": EvidenceTier.TRADITIONAL_USE.value},
            {"structured_value": {"substance_personal_applicability": {"nonsense": True}}},
            {"structured_value": {"substance_personal_applicability": {
                "schema_version": PERSONAL_APPLICABILITY_SCHEMA_VERSION,
                "category": CATEGORY.value,
                "all_of": [{
                    "fact_key": FACT_KEY,
                    "operator": "equals_any",
                    "values": [FACT_VALUE],
                }],
            }}},
        ],
    )
    async def test_an_ineligible_claim_blocks_the_release(self, db_clean, override) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session, **override)
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await _validate(session, _manifest())
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_ELIGIBLE
        )

    async def test_a_claim_about_a_different_substance_is_refused(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session, subject_key="niacinamide")
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await _validate(session, _manifest())
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.SEMANTIC_EVIDENCE_MISMATCH
        )
        assert "niacinamide" in caught.value.message

    async def test_a_claim_in_a_different_category_is_refused(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(
                session,
                category=PersonalApplicabilityCategory.HAIR_CARE,
                structured_value=_payload(PersonalApplicabilityCategory.HAIR_CARE),
            )
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await _validate(session, _manifest())
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.SEMANTIC_EVIDENCE_MISMATCH
        )

    async def test_a_payload_category_disagreeing_with_the_rule_is_refused(self, db_clean) -> None:
        """A well-formed payload for the wrong category is still the wrong category.

        The claim sits in the skin_care domain the rule expects, so the domain
        check passes and it is the payload that gives it away.
        """
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session, structured_value=_hair_payload())
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await _validate(session, _manifest())
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.SEMANTIC_EVIDENCE_MISMATCH
        )
        assert "hair_care" in caught.value.message

    @pytest.mark.parametrize(
        "override",
        [
            {"claim_type": ClaimType.USAGE_CONTEXT.value},
            {"subject_type": "product"},
            {"domain": EvidenceDomain.NUTRITION.value},
        ],
    )
    async def test_a_claim_of_the_wrong_shape_is_refused(self, db_clean, override) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session, **override)
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await _validate(session, _manifest())
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.SEMANTIC_EVIDENCE_MISMATCH
        )

    async def test_an_explanation_citing_an_unknown_source_key_is_refused(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session, source_key="src.a")
            document = _manifest([], [], [_explanation(source_key="src.other")])
            document["semantic_rules"] = [_semantic()]
            document["policy_rules"] = [_policy()]
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await _validate(session, document)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EXPLANATION_SOURCE_PATH_NOT_ELIGIBLE
        )

    async def test_an_explanation_citing_the_wrong_locator_is_refused(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session, locator="section 2")
            document = _manifest(
                [_semantic()], [_policy()], [_explanation(source_locator="section 3")]
            )
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await _validate(session, document)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EXPLANATION_SOURCE_PATH_NOT_ELIGIBLE
        )

    async def test_a_locator_is_matched_exactly_and_never_normalised(self, db_clean) -> None:
        """``"section 2"`` and ``" section 2 "`` are different reviewed anchors."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session, locator=" section 2 ")
            exact = _manifest(
                [_semantic()], [_policy()], [_explanation(source_locator=" section 2 ")]
            )
            await _validate(session, exact)

            trimmed = _manifest(
                [_semantic()], [_policy()], [_explanation(source_locator="section 2")]
            )
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await _validate(session, trimmed)
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EXPLANATION_SOURCE_PATH_NOT_ELIGIBLE
        )

    async def test_a_null_locator_matches_only_a_null_locator(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session, locator=None)
            await _validate(
                session,
                _manifest([_semantic()], [_policy()], [_explanation(source_locator=None)]),
            )
            with pytest.raises(PersonalDecisionReleaseValidationError):
                await _validate(
                    session,
                    _manifest([_semantic()], [_policy()], [_explanation(source_locator="p1")]),
                )

    #: Every way a source path can stop being something Step 8B would accept.
    INELIGIBLE_SOURCE_PATHS = [
        {"source_status": SourceStatus.RETIRED.value},
        {"relationship": ClaimSourceRelationship.BACKGROUND.value},
        {"reviewed_link": False},
        {"license_note": None},
        {"source_url": "https://"},
        {"source_type": SourceType.MANUFACTURER_CLAIM.value},
    ]

    @pytest.mark.parametrize("override", INELIGIBLE_SOURCE_PATHS)
    async def test_a_claim_with_no_eligible_source_path_is_not_projectable(
        self, db_clean, override
    ) -> None:
        """No eligible path means Step 8B returns nothing, so the rule is dead.

        This is the projectability gate, not the citation gate: the claim
        itself can no longer reach a customer, whatever the explanation says.
        """
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session, **override)
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await _validate(session, _manifest())
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_ELIGIBLE
        )
        assert "Step 8B would accept" in caught.value.message

    @pytest.mark.parametrize("override", INELIGIBLE_SOURCE_PATHS)
    async def test_the_citation_gate_still_fires_on_a_projectable_claim(
        self, db_clean, override
    ) -> None:
        """The two source gates are separate and neither substitutes for the other.

        Here a second, valid path keeps the claim projectable, so Step 8B is
        satisfied -- and the reviewed citation the customer would actually see
        still points at a path that is not eligible. That must still fail, and
        with the citation code rather than the projectability one.
        """
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session, extra_eligible_source=True, **override)
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await _validate(session, _manifest())
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EXPLANATION_SOURCE_PATH_NOT_ELIGIBLE
        )

    async def test_evidence_validation_is_two_queries_however_many_rules(self, db_clean) -> None:
        """A per-rule query would make a large release unreviewable in practice."""
        factory = get_sessionmaker()
        rule_count = 12
        async with factory() as session:
            for index in range(rule_count):
                await _publish_claim(
                    session,
                    claim_key=f"claim.{index}",
                    source_key=f"src.{index}",
                )
            await session.commit()

        document = _manifest(
            [
                _semantic(rule_id=f"sem.{index}", claim_key=f"claim.{index}")
                for index in range(rule_count)
            ],
            [
                _policy(
                    policy_id=f"pol.{index}",
                    identities=[(f"sem.{index}", "1")],
                )
                for index in range(rule_count)
            ],
            [
                _explanation(
                    explanation_id=f"exp.{index}",
                    policy_id=f"pol.{index}",
                    semantic_rule_id=f"sem.{index}",
                    claim_key=f"claim.{index}",
                    source_key=f"src.{index}",
                )
                for index in range(rule_count)
            ],
        )

        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        engine = sql.get_engine().sync_engine
        async with factory() as session:
            event.listen(engine, "before_cursor_execute", record)
            try:
                report = await validate_release_evidence(session, parse_release_manifest(document))
            finally:
                event.remove(engine, "before_cursor_execute", record)

        assert report.semantic_evidence_checked == rule_count
        assert len(statements) == 2
        assert sum("evidence_claim_sources" in row for row in statements) == 1

    async def test_validation_never_writes_to_evidence(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            await session.commit()
        async with factory() as session:
            await _validate(session, _manifest())
            assert not session.new and not session.dirty and not session.deleted


# ---------------------------------------------------------------------------
# Release lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_a_draft_starts_at_version_one_with_no_verification(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            view = await _draft(session, _manifest())
            await session.commit()
        assert view["release_key"] == PERSONAL_DECISION_RELEASE_KEY
        assert view["release_version"] == 1
        assert view["status"] == PersonalDecisionReleaseStatus.DRAFT.value
        assert view["review_verification"] is None
        assert view["approved_at"] is None and view["activated_at"] is None
        assert view["counts"] == {
            "semantic_rules": 1,
            "policy_rules": 1,
            "explanation_rules": 1,
        }
        assert view["content_hash"] == manifest_content_hash(parse_release_manifest(_manifest()))

    async def test_versions_are_allocated_sequentially(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            versions = [
                (await _draft(session, _manifest()))["release_version"] for _ in range(3)
            ]
            await session.commit()
        assert versions == [1, 2, 3]

    async def test_the_release_key_is_a_constant_and_never_taken_from_input(self) -> None:
        signature = inspect.signature(
            release_authoring.create_personal_decision_release_draft
        )
        assert "release_key" not in signature.parameters

    async def test_editing_a_draft_replaces_the_manifest_and_clears_verification(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            view = await _verified_draft(session, _manifest())
            assert view["review_verification"] is not None
            edited = await release_authoring.edit_personal_decision_release_draft(
                session,
                uuid.UUID(view["id"]),
                _manifest([_semantic(signal="cautionary")], [], []),
                actor="admin.synthetic",
            )
            await session.commit()
        assert edited["review_verification"] is None
        assert edited["content_hash"] != view["content_hash"]
        assert edited["counts"]["policy_rules"] == 0

    async def test_only_a_draft_is_editable(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            approved = await _approved(session, _manifest())
            with pytest.raises(ConflictError, match="immutable"):
                await release_authoring.edit_personal_decision_release_draft(
                    session, uuid.UUID(approved["id"]), _manifest(), actor="admin.synthetic"
                )
            activated = await release_authoring.activate_personal_decision_release(
                session, uuid.UUID(approved["id"]), actor="admin.synthetic"
            )
            with pytest.raises(ConflictError, match="immutable"):
                await release_authoring.edit_personal_decision_release_draft(
                    session, uuid.UUID(activated["id"]), _manifest(), actor="admin.synthetic"
                )
            retired = await release_authoring.deactivate_personal_decision_release(
                session, uuid.UUID(activated["id"]), actor="admin.synthetic"
            )
            with pytest.raises(ConflictError, match="immutable"):
                await release_authoring.edit_personal_decision_release_draft(
                    session, uuid.UUID(retired["id"]), _manifest(), actor="admin.synthetic"
                )

    async def test_cloning_produces_an_independent_unverified_draft(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            source = await _activated(session, _manifest())
            clone = await release_authoring.clone_personal_decision_release(
                session, uuid.UUID(source["id"]), actor="admin.synthetic"
            )
            await session.commit()
        assert clone["id"] != source["id"]
        assert clone["release_version"] == source["release_version"] + 1
        assert clone["status"] == PersonalDecisionReleaseStatus.DRAFT.value
        assert clone["review_verification"] is None
        assert clone["approved_by"] is None and clone["approved_at"] is None
        assert clone["activated_by"] is None and clone["activated_at"] is None
        assert clone["retired_by"] is None and clone["retired_at"] is None
        assert clone["supersedes_release_id"] == source["id"]
        # Same rules, so the same hash. It still has to be reviewed again.
        assert clone["content_hash"] == source["content_hash"]
        assert clone["manifest"] == source["manifest"]

    async def test_recording_verification_does_not_approve(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            view = await _verified_draft(session, _manifest())
            await session.commit()
        assert view["status"] == PersonalDecisionReleaseStatus.DRAFT.value
        assert view["approved_at"] is None

    @pytest.mark.parametrize(
        "checkpoint",
        [
            "founder_review_completed",
            "claude_review_completed",
            "codex_review_completed",
            "independent_reviews_agree",
            "adversarial_review_passed",
        ],
    )
    async def test_incomplete_verification_blocks_approval(self, db_clean, checkpoint) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _verified_draft(session, _manifest(), **{checkpoint: False})
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.approve_personal_decision_release(
                    session, uuid.UUID(view["id"]), actor="admin.synthetic"
                )
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.RELEASE_VERIFICATION_INCOMPLETE
        )

    async def test_no_verification_at_all_blocks_approval(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _draft(session, _manifest())
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.approve_personal_decision_release(
                    session, uuid.UUID(view["id"]), actor="admin.synthetic"
                )
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.RELEASE_VERIFICATION_INCOMPLETE
        )

    async def test_unresolved_doubt_blocks_approval(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _verified_draft(session, _manifest(), unresolved_doubt=True)
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.approve_personal_decision_release(
                    session, uuid.UUID(view["id"]), actor="admin.synthetic"
                )
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.RELEASE_UNRESOLVED_DOUBT
        )

    async def test_approval_records_the_actor_and_freezes_the_bundle(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _approved(session, _manifest())
            await session.commit()
        assert view["status"] == PersonalDecisionReleaseStatus.APPROVED.value
        assert view["approved_by"] == "admin.synthetic"
        assert view["approved_at"] is not None
        assert view["activated_at"] is None

    async def test_approving_twice_is_refused(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _approved(session, _manifest())
            with pytest.raises(ConflictError, match="cannot become approved"):
                await release_authoring.approve_personal_decision_release(
                    session, uuid.UUID(view["id"]), actor="admin.synthetic"
                )

    async def test_an_empty_release_cannot_be_approved(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            view = await _verified_draft(session, _manifest([], [], []))
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.approve_personal_decision_release(
                    session, uuid.UUID(view["id"]), actor="admin.synthetic"
                )
        assert _reason(caught.value) is PersonalDecisionReleaseValidationCode.RELEASE_EMPTY

    async def test_approval_reads_the_persisted_row_not_the_request(self, db_clean) -> None:
        """Direct database corruption must be caught, not trusted."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _verified_draft(session, _manifest())
            await session.commit()
            release_id = uuid.UUID(view["id"])

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, release_id)
            corrupted = copy.deepcopy(row.manifest)
            corrupted["semantic_rules"][0]["signal"] = "cautionary"
            row.manifest = corrupted
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.approve_personal_decision_release(
                    session, release_id, actor="admin.synthetic"
                )
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.RELEASE_CONTENT_HASH_MISMATCH
        )

    async def test_the_validate_endpoint_reports_what_it_checked(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _verified_draft(session, _manifest())
            report = await release_authoring.validate_personal_decision_release(
                session, uuid.UUID(view["id"])
            )
        assert report["ready"] is True
        assert report["verification_recorded"] is True
        assert report["semantic_evidence_checked"] == 1
        assert report["policies_checked"] == 1
        assert report["explanations_checked"] == 1

    @pytest.mark.parametrize(
        "verification",
        [None, _release_verification(founder_review_completed=False).as_dict()],
        ids=["missing", "incomplete"],
    )
    async def test_validate_refuses_missing_or_incomplete_verification(
        self, db_clean, verification
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _draft(session, _manifest())
            row = await session.get(PersonalDecisionRelease, uuid.UUID(view["id"]))
            row.review_verification = verification
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.validate_personal_decision_release(
                    session, row.id
                )
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.RELEASE_VERIFICATION_INCOMPLETE
        )

    async def test_validate_refuses_unresolved_doubt(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _verified_draft(session, _manifest(), unresolved_doubt=True)
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.validate_personal_decision_release(
                    session, uuid.UUID(view["id"])
                )
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.RELEASE_UNRESOLVED_DOUBT
        )

    async def test_validate_refuses_corrupted_schema_column(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _verified_draft(session, _manifest())
            row = await session.get(PersonalDecisionRelease, uuid.UUID(view["id"]))
            row.manifest_schema_version = 999
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.validate_personal_decision_release(
                    session, row.id
                )
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.RELEASE_SCHEMA_VERSION_UNSUPPORTED
        )

    async def test_an_unknown_release_is_not_found(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            with pytest.raises(NotFoundError):
                await release_authoring.get_personal_decision_release(session, uuid.uuid4())


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------


class TestActivation:
    async def test_first_activation_makes_exactly_one_active_release(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _activated(session, _manifest())
            await session.commit()
        assert view["status"] == PersonalDecisionReleaseStatus.ACTIVE.value
        assert view["activated_by"] == "admin.synthetic"
        assert view["activated_at"] is not None

        async with factory() as session:
            active = (
                await session.execute(
                    select(PersonalDecisionRelease).where(
                        PersonalDecisionRelease.status
                        == PersonalDecisionReleaseStatus.ACTIVE.value
                    )
                )
            ).scalars().all()
        assert len(active) == 1

    async def test_only_an_approved_release_may_activate(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            draft = await _verified_draft(session, _manifest())
            with pytest.raises(ConflictError, match="cannot become active"):
                await release_authoring.activate_personal_decision_release(
                    session, uuid.UUID(draft["id"]), actor="admin.synthetic"
                )

    async def test_replacement_retires_the_old_release_atomically(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            first = await _activated(session, _manifest())
            second = await _approved(session, _manifest())
            await session.commit()
            second_id = uuid.UUID(second["id"])

        async with factory() as session:
            activated = await release_authoring.activate_personal_decision_release(
                session, second_id, actor="admin.synthetic"
            )
            await session.commit()

        assert activated["status"] == PersonalDecisionReleaseStatus.ACTIVE.value
        async with factory() as session:
            rows = {
                row.id: row
                for row in (
                    await session.execute(select(PersonalDecisionRelease))
                ).scalars().all()
            }
        assert rows[uuid.UUID(first["id"])].status == PersonalDecisionReleaseStatus.RETIRED.value
        assert rows[second_id].status == PersonalDecisionReleaseStatus.ACTIVE.value
        assert rows[uuid.UUID(first["id"])].retired_at is not None
        assert sum(
            row.status == PersonalDecisionReleaseStatus.ACTIVE.value for row in rows.values()
        ) == 1
        # Lineage is recorded, and the old manifest is untouched.
        assert rows[second_id].supersedes_release_id == uuid.UUID(first["id"])
        assert rows[uuid.UUID(first["id"])].manifest == first["manifest"]

    async def test_a_failed_replacement_leaves_the_old_release_active(self, db_clean) -> None:
        """No partial switch: production never ends up with neither release."""
        factory = get_sessionmaker()
        async with factory() as session:
            claim = await _publish_claim(session)
            first = await _activated(session, _manifest())
            second = await _approved(session, _manifest())
            await session.commit()
            claim_id, second_id = claim.id, uuid.UUID(second["id"])

        # Corrupt the evidence the second release depends on, after approval.
        async with factory() as session:
            row = await session.get(EvidenceClaim, claim_id)
            row.review_status = ReviewStatus.SUPERSEDED.value
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.activate_personal_decision_release(
                    session, second_id, actor="admin.synthetic"
                )
            await session.rollback()
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_PUBLISHED
        )

        async with factory() as session:
            rows = {
                row.id: row.status
                for row in (
                    await session.execute(select(PersonalDecisionRelease))
                ).scalars().all()
            }
        assert rows[uuid.UUID(first["id"])] == PersonalDecisionReleaseStatus.ACTIVE.value
        assert rows[second_id] == PersonalDecisionReleaseStatus.APPROVED.value

    async def test_the_database_refuses_a_second_active_row(self, db_clean) -> None:
        """The partial unique index, not the application, is the final backstop."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            await _activated(session, _manifest())
            second = await _approved(session, _manifest())
            await session.commit()
            second_id = uuid.UUID(second["id"])

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, second_id)
            row.status = PersonalDecisionReleaseStatus.ACTIVE.value
            with pytest.raises(IntegrityError):
                await session.commit()

    async def test_deactivation_leaves_zero_active_releases(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _activated(session, _manifest())
            retired = await release_authoring.deactivate_personal_decision_release(
                session, uuid.UUID(view["id"]), actor="admin.synthetic"
            )
            await session.commit()
        assert retired["status"] == PersonalDecisionReleaseStatus.RETIRED.value
        assert retired["retired_by"] == "admin.synthetic"

        async with factory() as session:
            assert await load_active_personal_decision_release(session) is None

    async def test_a_retired_release_is_never_reactivated(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _activated(session, _manifest())
            await release_authoring.deactivate_personal_decision_release(
                session, uuid.UUID(view["id"]), actor="admin.synthetic"
            )
            with pytest.raises(ConflictError, match="cannot become active"):
                await release_authoring.activate_personal_decision_release(
                    session, uuid.UUID(view["id"]), actor="admin.synthetic"
                )

    async def test_deactivating_a_draft_or_approved_release_is_refused(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            draft = await _draft(session, _manifest())
            with pytest.raises(ConflictError, match="cannot become retired"):
                await release_authoring.deactivate_personal_decision_release(
                    session, uuid.UUID(draft["id"]), actor="admin.synthetic"
                )
            approved = await _approved(session, _manifest())
            with pytest.raises(ConflictError, match="cannot become retired"):
                await release_authoring.deactivate_personal_decision_release(
                    session, uuid.UUID(approved["id"]), actor="admin.synthetic"
                )


# ---------------------------------------------------------------------------
# Runtime loading
# ---------------------------------------------------------------------------


class TestRuntimeLoader:
    async def test_no_release_loads_as_none(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            assert await load_active_personal_decision_release(session) is None

    async def test_a_draft_or_approved_release_is_not_active(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            await _approved(session, _manifest())
            await session.commit()
        async with factory() as session:
            assert await load_active_personal_decision_release(session) is None

    async def test_one_active_release_materialises_into_immutable_rules(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _activated(session, _manifest())
            await session.commit()

        async with factory() as session:
            release = await load_active_personal_decision_release(session)
        assert release is not None
        assert str(release.release_id) == view["id"]
        assert release.release_version == view["release_version"]
        assert release.content_hash == view["content_hash"]
        assert isinstance(release.semantic_rules, tuple)
        assert isinstance(release.policy_rules, tuple)
        assert isinstance(release.explanation_rules, tuple)
        assert release.semantic_rules[0].rule_id == "sem.a"
        assert release.policy_rules[0].action is PersonalDecisionAction.BUY

    async def test_loading_the_active_release_is_one_query(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            await _activated(session, _manifest())
            await session.commit()

        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        engine = sql.get_engine().sync_engine
        async with factory() as session:
            event.listen(engine, "before_cursor_execute", record)
            try:
                await load_active_personal_decision_release(session)
            finally:
                event.remove(engine, "before_cursor_execute", record)
        assert len(statements) == 1
        assert "personal_decision_releases" in statements[0]

    @pytest.mark.parametrize(
        "corrupt",
        [
            lambda block: {**block, "founder_review_completed": False},
            lambda block: {**block, "unresolved_doubt": True},
            lambda block: None,
            lambda block: {
                key: value
                for key, value in block.items()
                if key != "codex_review_completed"
            },
        ],
        ids=["false-checkpoint", "unresolved-doubt", "null", "missing-key"],
    )
    async def test_corrupted_active_review_verification_fails_closed(
        self, db_clean, corrupt
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _activated(session, _manifest())
            await session.commit()
            release_id = uuid.UUID(view["id"])

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, release_id)
            row.review_verification = corrupt(row.review_verification)
            await session.commit()

        async with factory() as session:
            with pytest.raises(
                PersonalDecisionReleaseInvariantError,
                match="review verification",
            ):
                await load_active_personal_decision_release(session)

    def test_more_than_one_active_release_is_never_resolved_by_a_tie_break(self) -> None:
        """The index should make this unconstructible; the rule is still tested."""
        rows = [
            PersonalDecisionRelease(release_version=1),
            PersonalDecisionRelease(release_version=2),
        ]
        with pytest.raises(PersonalDecisionReleaseInvariantError, match="refusing to choose"):
            select_active_release(rows)

    def test_zero_and_one_active_rows_resolve_normally(self) -> None:
        assert select_active_release([]) is None
        only = PersonalDecisionRelease(release_version=1)
        assert select_active_release([only]) is only

    async def test_a_manifest_edited_without_its_hash_fails_closed(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _activated(session, _manifest())
            await session.commit()
            release_id = uuid.UUID(view["id"])

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, release_id)
            corrupted = copy.deepcopy(row.manifest)
            corrupted["policy_rules"][0]["action"] = PersonalDecisionAction.SKIP.value
            corrupted["explanation_rules"][0]["action"] = PersonalDecisionAction.SKIP.value
            row.manifest = corrupted
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseInvariantError, match="content hash"):
                await load_active_personal_decision_release(session)

    async def test_a_hash_edited_without_its_manifest_fails_closed(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _activated(session, _manifest())
            await session.commit()
            release_id = uuid.UUID(view["id"])

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, release_id)
            row.content_hash = "0" * 64
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseInvariantError, match="content hash"):
                await load_active_personal_decision_release(session)

    async def test_an_unsupported_stored_schema_version_fails_closed(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _activated(session, _manifest())
            await session.commit()
            release_id = uuid.UUID(view["id"])

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, release_id)
            row.manifest_schema_version = 99
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseInvariantError, match="schema"):
                await load_active_personal_decision_release(session)

    async def test_a_structurally_broken_stored_manifest_fails_closed(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _activated(session, _manifest())
            await session.commit()
            release_id = uuid.UUID(view["id"])

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, release_id)
            row.manifest = {"schema_version": 1, "semantic_rules": "not a list"}
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseInvariantError, match="not usable"):
                await load_active_personal_decision_release(session)

    async def test_materialising_a_non_active_row_fails_closed(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _approved(session, _manifest())
            await session.commit()
            release_id = uuid.UUID(view["id"])
        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, release_id)
            with pytest.raises(PersonalDecisionReleaseInvariantError, match="not active"):
                materialise_active_release(row)


# ---------------------------------------------------------------------------
# Released orchestration
# ---------------------------------------------------------------------------


class TestReleasedOrchestration:
    async def test_no_release_presents_no_action(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            applicability = await _real_step_8b(session)
        result = evaluate_personal_decision_with_release(applicability, None)
        assert result.release_id is None
        assert result.release_version is None
        assert result.release_content_hash is None
        assert result.presentation.action is None
        assert result.presentation.status is not (
            PersonalDecisionPresentationStatus.DECISION_PRESENTABLE
        )

    async def test_no_release_still_preserves_a_hard_handoff(self, db_clean) -> None:
        """The absence of decision knowledge must never erase a safety handoff."""
        factory = get_sessionmaker()
        handoff = _handoff()
        async with factory() as session:
            applicability = await _real_step_8b(session, handoff=handoff)
        result = evaluate_personal_decision_with_release(applicability, None)
        assert result.presentation.status is (
            PersonalDecisionPresentationStatus.HANDOFF_REQUIRED
        )
        assert result.presentation.action is None

    async def test_an_active_release_still_preserves_a_hard_handoff(self, db_clean) -> None:
        factory = get_sessionmaker()
        handoff = _handoff()
        async with factory() as session:
            published = await _author_and_publish(session)
            await _activated(session, _bundle_for(published))
            await session.commit()
        async with factory() as session:
            release = await load_active_personal_decision_release(session)
            applicability = await _real_step_8b(session, handoff=handoff)
        result = evaluate_personal_decision_with_release(applicability, release)
        assert result.presentation.status is (
            PersonalDecisionPresentationStatus.HANDOFF_REQUIRED
        )
        assert result.presentation.action is None

    async def test_the_orchestrator_passes_empty_registries_explicitly(self) -> None:
        """No hidden global: the empty case is visible in the call, not the default."""
        source = (DOMAIN_DIR / "runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "evaluate_personal_decision_with_release"
        )
        rules_keywords = [
            keyword
            for call in ast.walk(function)
            if isinstance(call, ast.Call)
            for keyword in call.keywords
            if keyword.arg == "rules"
        ]
        assert len(rules_keywords) == 3
        assert all(isinstance(keyword.value, ast.Name) for keyword in rules_keywords)


# ---------------------------------------------------------------------------
# The decisive end-to-end path
# ---------------------------------------------------------------------------


class TestEndToEnd:
    async def test_real_8g_evidence_through_an_active_release_becomes_a_sourced_decision(
        self, db_clean
    ) -> None:
        """Step 8G -> Step 8B -> active Step 8H release -> Steps 8C/8D/8E/8F.

        Nothing intermediate is hand-built. The evidence goes through the real
        authoring lifecycle, the release through the real approval and
        activation services, the personal match through the real Step 8B, and
        the decision through the real governed chain.
        """
        factory = get_sessionmaker()
        async with factory() as session:
            published = await _author_and_publish(session, locator="Section 4.2")
            await session.commit()

        assert published["review_status"] == ReviewStatus.PUBLISHED.value
        source = published["sources"][0]
        bundle = _bundle_for(published)

        async with factory() as session:
            release_view = await _activated(session, bundle)
            await session.commit()

        async with factory() as session:
            release = await load_active_personal_decision_release(session)
            applicability = await _real_step_8b(session)

        # Step 8B really matched the authored evidence.
        ingredient = applicability.ingredients[0]
        assert ingredient.personal_applicability_status is (
            PersonalApplicabilityStatus.PERSONAL_EVIDENCE_AVAILABLE
        )
        assert ingredient.claims[0].claim_key == published["claim_key"]

        result = evaluate_personal_decision_with_release(applicability, release)
        presentation = result.presentation

        assert presentation.status is PersonalDecisionPresentationStatus.DECISION_PRESENTABLE
        assert presentation.action is PersonalDecisionAction(
            bundle["policy_rules"][0]["action"]
        )
        assert presentation.reason_key == REASON_KEY

        # Exact provenance, end to end.
        assert presentation.explanation_id == bundle["explanation_rules"][0]["explanation_id"]
        assert presentation.explanation_version == (
            bundle["explanation_rules"][0]["explanation_version"]
        )
        assert presentation.source_policy.policy_id == bundle["policy_rules"][0]["policy_id"]
        assert presentation.source_policy.policy_version == (
            bundle["policy_rules"][0]["policy_version"]
        )
        aggregated = presentation.source_policy.source_aggregation.rules
        assert [(rule.rule_id, rule.rule_version) for rule in aggregated] == [
            (
                bundle["semantic_rules"][0]["rule_id"],
                bundle["semantic_rules"][0]["rule_version"],
            )
        ]
        assert aggregated[0].claim_key == published["claim_key"]
        assert aggregated[0].claim_version == published["claim_version"]

        # The citation is the exact reviewed source path.
        citation = presentation.citation
        assert citation is not None
        assert citation.source_key == source["source_key"]
        assert citation.locator == "Section 4.2" == source["locator"]
        assert citation.canonical_url == source["canonical_url"]
        assert citation.title == source["title"]

        # The release that answered is named exactly.
        assert str(result.release_id) == release_view["id"]
        assert result.release_version == release_view["release_version"]
        assert result.release_content_hash == release_view["content_hash"]

    async def test_the_same_chain_carries_a_reviewed_skip_without_any_inference(
        self, db_clean
    ) -> None:
        """The action is whatever the manifest says, and nothing else decides it."""
        factory = get_sessionmaker()
        async with factory() as session:
            published = await _author_and_publish(session)
            await session.commit()

        bundle = _bundle_for(published, action=PersonalDecisionAction.SKIP.value)
        # Identical supporting evidence, opposite reviewed action.
        assert bundle["semantic_rules"][0]["signal"] == "supporting"

        async with factory() as session:
            await _activated(session, bundle)
            await session.commit()
        async with factory() as session:
            release = await load_active_personal_decision_release(session)
            applicability = await _real_step_8b(session)

        result = evaluate_personal_decision_with_release(applicability, release)
        assert result.presentation.status is (
            PersonalDecisionPresentationStatus.DECISION_PRESENTABLE
        )
        assert result.presentation.action is PersonalDecisionAction.SKIP

    async def test_a_release_whose_explanation_cites_no_real_source_shows_nothing(
        self, db_clean
    ) -> None:
        """Approval refuses it; if one somehow existed, Step 8F still withholds."""
        factory = get_sessionmaker()
        async with factory() as session:
            published = await _author_and_publish(session)
            await session.commit()

        bundle = _bundle_for(published)
        bundle["explanation_rules"][0]["source_key"] = "src.never-existed"

        async with factory() as session:
            view = await _verified_draft(session, bundle)
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.approve_personal_decision_release(
                    session, uuid.UUID(view["id"]), actor="admin.synthetic"
                )
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EXPLANATION_SOURCE_PATH_NOT_ELIGIBLE
        )


# ---------------------------------------------------------------------------
# Stale evidence
# ---------------------------------------------------------------------------


class TestStaleEvidence:
    async def test_an_active_release_does_not_silently_inherit_revised_evidence(
        self, db_clean
    ) -> None:
        """Exact claim-version matching makes staleness fail closed by itself.

        The release names claim version 1. Publishing version 2 supersedes it,
        so Step 8B now carries version 2, no semantic rule matches, and the
        chain stops before any action. Nothing about the release changed.
        """
        factory = get_sessionmaker()
        async with factory() as session:
            first = await _author_and_publish(session)
            await session.commit()
        assert first["claim_version"] == 1

        async with factory() as session:
            release_view = await _activated(session, _bundle_for(first))
            await session.commit()

        async with factory() as session:
            release = await load_active_personal_decision_release(session)
            applicability = await _real_step_8b(session)
        presentable = evaluate_personal_decision_with_release(applicability, release)
        assert presentable.presentation.status is (
            PersonalDecisionPresentationStatus.DECISION_PRESENTABLE
        )

        # Revise the evidence through the real Step 8G lifecycle.
        async with factory() as session:
            second = await _author_and_publish(session, entry_id=uuid.UUID(first["id"]))
            await session.commit()
        assert second["claim_key"] == first["claim_key"]
        assert second["claim_version"] == 2

        async with factory() as session:
            superseded = await session.get(EvidenceClaim, uuid.UUID(first["id"]))
            assert superseded.review_status == ReviewStatus.SUPERSEDED.value

        async with factory() as session:
            release = await load_active_personal_decision_release(session)
            applicability = await _real_step_8b(session)

        # The release is untouched, and still names version 1.
        assert release is not None
        assert str(release.release_id) == release_view["id"]
        assert release.semantic_rules[0].claim_version == 1
        # Step 8B now carries version 2.
        assert applicability.ingredients[0].claims[0].claim_version == 2

        result = evaluate_personal_decision_with_release(applicability, release)
        assert result.presentation.status is not (
            PersonalDecisionPresentationStatus.DECISION_PRESENTABLE
        )
        assert result.presentation.action is None

    async def test_activation_is_blocked_when_evidence_moved_after_approval(
        self, db_clean
    ) -> None:
        """Approved against version 1, activated after version 2 superseded it."""
        factory = get_sessionmaker()
        async with factory() as session:
            first = await _author_and_publish(session)
            await session.commit()

        async with factory() as session:
            approved = await _approved(session, _bundle_for(first))
            await session.commit()
            release_id = uuid.UUID(approved["id"])
        assert approved["status"] == PersonalDecisionReleaseStatus.APPROVED.value

        async with factory() as session:
            await _author_and_publish(session, entry_id=uuid.UUID(first["id"]))
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.activate_personal_decision_release(
                    session, release_id, actor="admin.synthetic"
                )
            await session.rollback()
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_PUBLISHED
        )

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, release_id)
            assert row.status == PersonalDecisionReleaseStatus.APPROVED.value
            assert await load_active_personal_decision_release(session) is None


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------


@pytest.fixture
async def admin_token(registered_supabase_user):
    token, _ = await registered_supabase_user(admin=True)
    return token


class TestAdminApi:
    async def test_every_route_requires_an_admin(
        self, db_clean, app_client, registered_supabase_user, admin_token
    ) -> None:
        non_admin_token, _ = await registered_supabase_user()
        release_id = uuid.uuid4()
        routes = [
            ("get", ADMIN, None),
            ("get", f"{ADMIN}/active", None),
            ("get", f"{ADMIN}/{release_id}", None),
            ("post", ADMIN, {"manifest": _manifest()}),
            ("put", f"{ADMIN}/{release_id}", {"manifest": _manifest()}),
            ("post", f"{ADMIN}/{release_id}/clone", None),
            (
                "post",
                f"{ADMIN}/{release_id}/review-verification",
                _release_verification().as_dict(),
            ),
            ("post", f"{ADMIN}/{release_id}/validate", None),
            ("post", f"{ADMIN}/{release_id}/approve", None),
            ("post", f"{ADMIN}/{release_id}/activate", None),
            ("post", f"{ADMIN}/{release_id}/deactivate", None),
        ]
        for method, path, body in routes:
            call = getattr(app_client, method)
            kwargs = {"json": body} if body is not None else {}
            unauthenticated = await call(path, **kwargs)
            assert unauthenticated.status_code in {401, 403}, path
            forbidden = await call(path, headers=auth(non_admin_token), **kwargs)
            assert forbidden.status_code == 403, path
            allowed = await call(path, headers=auth(admin_token), **kwargs)
            assert allowed.status_code not in {401, 403}, path

    async def test_the_full_lifecycle_over_http(self, db_clean, app_client, admin_token) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            published = await _author_and_publish(session)
            await session.commit()
        bundle = _bundle_for(published)

        created = await app_client.post(
            ADMIN, headers=auth(admin_token), json={"manifest": bundle}
        )
        assert created.status_code == 201, created.text
        release_id = created.json()["id"]
        assert created.json()["status"] == PersonalDecisionReleaseStatus.DRAFT.value

        recorded = await app_client.post(
            f"{ADMIN}/{release_id}/review-verification",
            headers=auth(admin_token),
            json=_release_verification().as_dict(),
        )
        assert recorded.status_code == 200, recorded.text

        validated = await app_client.post(
            f"{ADMIN}/{release_id}/validate", headers=auth(admin_token)
        )
        assert validated.status_code == 200
        assert validated.json()["ready"] is True

        approved = await app_client.post(
            f"{ADMIN}/{release_id}/approve", headers=auth(admin_token)
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == PersonalDecisionReleaseStatus.APPROVED.value

        activated = await app_client.post(
            f"{ADMIN}/{release_id}/activate", headers=auth(admin_token)
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["status"] == PersonalDecisionReleaseStatus.ACTIVE.value

        active = await app_client.get(f"{ADMIN}/active", headers=auth(admin_token))
        assert active.json()["release_id"] == release_id

        deactivated = await app_client.post(
            f"{ADMIN}/{release_id}/deactivate", headers=auth(admin_token)
        )
        assert deactivated.status_code == 200
        assert deactivated.json()["status"] == PersonalDecisionReleaseStatus.RETIRED.value

        empty = await app_client.get(f"{ADMIN}/active", headers=auth(admin_token))
        assert empty.json() is None

    async def test_validate_route_is_true_approval_readiness(
        self, db_clean, app_client, admin_token
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            await session.commit()

        created = await app_client.post(
            ADMIN, headers=auth(admin_token), json={"manifest": _manifest()}
        )
        release_id = created.json()["id"]

        missing = await app_client.post(
            f"{ADMIN}/{release_id}/validate", headers=auth(admin_token)
        )
        assert missing.status_code == 422
        assert missing.json()["detail"]["reason"] == (
            PersonalDecisionReleaseValidationCode.RELEASE_VERIFICATION_INCOMPLETE.value
        )

        recorded = await app_client.post(
            f"{ADMIN}/{release_id}/review-verification",
            headers=auth(admin_token),
            json=_release_verification(unresolved_doubt=True).as_dict(),
        )
        assert recorded.status_code == 200
        doubtful = await app_client.post(
            f"{ADMIN}/{release_id}/validate", headers=auth(admin_token)
        )
        assert doubtful.status_code == 422
        assert doubtful.json()["detail"]["reason"] == (
            PersonalDecisionReleaseValidationCode.RELEASE_UNRESOLVED_DOUBT.value
        )

        recorded = await app_client.post(
            f"{ADMIN}/{release_id}/review-verification",
            headers=auth(admin_token),
            json=_release_verification().as_dict(),
        )
        assert recorded.status_code == 200
        ready = await app_client.post(
            f"{ADMIN}/{release_id}/validate", headers=auth(admin_token)
        )
        assert ready.status_code == 200
        assert ready.json()["ready"] is True
        assert ready.json()["verification_recorded"] is True

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, uuid.UUID(release_id))
            row.manifest_schema_version = 999
            await session.commit()
        corrupted = await app_client.post(
            f"{ADMIN}/{release_id}/validate", headers=auth(admin_token)
        )
        assert corrupted.status_code == 422
        assert corrupted.json()["detail"]["reason"] == (
            PersonalDecisionReleaseValidationCode.RELEASE_SCHEMA_VERSION_UNSUPPORTED.value
        )

    async def test_an_approved_release_cannot_be_edited_over_http(
        self, db_clean, app_client, admin_token
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            approved = await _approved(session, _manifest())
            await session.commit()

        response = await app_client.put(
            f"{ADMIN}/{approved['id']}",
            headers=auth(admin_token),
            json={"manifest": _manifest()},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["reason"] == (
            PersonalDecisionReleaseValidationCode.RELEASE_NOT_EDITABLE.value
        )

    async def test_mutation_bodies_forbid_unknown_fields(
        self, db_clean, app_client, admin_token
    ) -> None:
        for path, body in (
            (ADMIN, {"manifest": _manifest(), "release_key": "other.series"}),
            (ADMIN, {"manifest": _manifest(), "status": "active"}),
            (
                f"{ADMIN}/{uuid.uuid4()}/review-verification",
                {**_release_verification().as_dict(), "approved": True},
            ),
        ):
            response = await app_client.post(path, headers=auth(admin_token), json=body)
            assert response.status_code == 422, response.text

    async def test_all_verification_checkpoints_are_required_and_write_nothing_when_omitted(
        self, db_clean, app_client, admin_token
    ) -> None:
        """An omitted attestation is not an attestation."""
        created = await app_client.post(
            ADMIN, headers=auth(admin_token), json={"manifest": _manifest()}
        )
        release_id = created.json()["id"]
        complete = _release_verification().as_dict()
        for omitted in complete:
            incomplete = {key: value for key, value in complete.items() if key != omitted}
            response = await app_client.post(
                f"{ADMIN}/{release_id}/review-verification",
                headers=auth(admin_token),
                json=incomplete,
            )
            assert response.status_code == 422, omitted

        factory = get_sessionmaker()
        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, uuid.UUID(release_id))
            assert row.review_verification is None

    async def test_verification_checkpoints_reject_boolean_coercion_and_write_nothing(
        self, db_clean, app_client, admin_token
    ) -> None:
        created = await app_client.post(
            ADMIN, headers=auth(admin_token), json={"manifest": _manifest()}
        )
        release_id = created.json()["id"]
        complete = _release_verification().as_dict()
        for field in complete:
            for coercible in ("true", "false", 1, 0, "yes", "no"):
                response = await app_client.post(
                    f"{ADMIN}/{release_id}/review-verification",
                    headers=auth(admin_token),
                    json={**complete, field: coercible},
                )
                assert response.status_code == 422, (field, coercible)

        factory = get_sessionmaker()
        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, uuid.UUID(release_id))
            assert row.review_verification is None

    async def test_six_explicit_boolean_checkpoints_are_recorded_exactly(
        self, db_clean, app_client, admin_token
    ) -> None:
        created = await app_client.post(
            ADMIN, headers=auth(admin_token), json={"manifest": _manifest()}
        )
        release_id = created.json()["id"]
        verification = _release_verification().as_dict()
        response = await app_client.post(
            f"{ADMIN}/{release_id}/review-verification",
            headers=auth(admin_token),
            json=verification,
        )
        assert response.status_code == 200, response.text

        factory = get_sessionmaker()
        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, uuid.UUID(release_id))
            assert row.review_verification == verification

    async def test_a_validation_failure_carries_its_deterministic_reason(
        self, db_clean, app_client, admin_token
    ) -> None:
        created = await app_client.post(
            ADMIN,
            headers=auth(admin_token),
            json={"manifest": _manifest([], [], [])},
        )
        release_id = created.json()["id"]
        await app_client.post(
            f"{ADMIN}/{release_id}/review-verification",
            headers=auth(admin_token),
            json=_release_verification().as_dict(),
        )
        response = await app_client.post(
            f"{ADMIN}/{release_id}/approve", headers=auth(admin_token)
        )
        assert response.status_code == 422
        assert response.json()["detail"]["reason"] == (
            PersonalDecisionReleaseValidationCode.RELEASE_EMPTY.value
        )

    async def test_every_release_route_is_under_admin(self) -> None:
        from server import app

        paths = set(app.openapi()["paths"])
        release_paths = {path for path in paths if "personal-decision-release" in path}
        assert len(release_paths) == 9
        assert all(path.startswith("/api/v2/admin/") for path in release_paths)

    def test_only_the_admin_module_reaches_the_release_runtime(self) -> None:
        """Step 8H ships no customer surface. The evaluator has no caller yet.

        The orchestration seam exists so a later milestone can wire a customer
        route to it deliberately; until that milestone is reviewed, nothing in
        the API may call it.
        """
        api_root = BACKEND_ROOT / "app" / "api"
        importers: dict[str, set[str]] = {}
        for path in sorted(api_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "app.domains.personal_decision_release"
                ):
                    names.update(alias.name for alias in node.names)
            if names:
                importers[path.name] = names
        assert set(importers) == {"personal_decision_release_admin.py"}
        assert "evaluate_personal_decision_with_release" not in (
            importers["personal_decision_release_admin.py"]
        )


# ---------------------------------------------------------------------------
# Production inertness
# ---------------------------------------------------------------------------


class TestProductionInertness:
    async def test_a_migrated_and_seeded_database_has_no_release_at_all(self, db_clean) -> None:
        from app.bootstrap import run as seed_reference_data

        factory = get_sessionmaker()
        async with factory() as session:
            await seed_reference_data(session)
            await session.commit()
        async with factory() as session:
            rows = (
                await session.execute(select(PersonalDecisionRelease))
            ).scalars().all()
            assert rows == []
            assert await load_active_personal_decision_release(session) is None

    def test_the_three_static_registries_are_still_empty(self) -> None:
        from app.domains.personal_decision_explanation.rules import (
            PERSONAL_DECISION_EXPLANATION_RULES,
        )
        from app.domains.personal_decision_policy.rules import PERSONAL_DECISION_POLICY_RULES
        from app.domains.personal_decision_semantics.rules import (
            PERSONAL_DECISION_SEMANTIC_RULES,
        )

        assert PERSONAL_DECISION_SEMANTIC_RULES == ()
        assert PERSONAL_DECISION_POLICY_RULES == ()
        assert PERSONAL_DECISION_EXPLANATION_RULES == ()

    def test_the_static_registries_are_still_plain_tuples_not_loaders(self) -> None:
        """A database-backed registry would make the pure layers impure."""
        for module_name in (
            "personal_decision_semantics",
            "personal_decision_policy",
            "personal_decision_explanation",
        ):
            source = (
                BACKEND_ROOT / "app" / "domains" / module_name / "rules.py"
            ).read_text(encoding="utf-8")
            tree = ast.parse(source)
            assignment = next(
                node
                for node in tree.body
                if isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id.startswith("PERSONAL_DECISION_")
            )
            assert isinstance(assignment.value, ast.Tuple)
            assert assignment.value.elts == []

    async def test_production_still_emits_no_action_with_no_release(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            applicability = await _real_step_8b(session)
            release = await load_active_personal_decision_release(session)
        assert release is None
        result = evaluate_personal_decision_with_release(applicability, release)
        assert result.presentation.action is None
        assert result.presentation.verdict_key is None


# ---------------------------------------------------------------------------
# Static architecture guards
# ---------------------------------------------------------------------------


def _production_sources() -> list[tuple[str, ast.Module]]:
    return sorted(_PRODUCTION_TREES.items())


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _parent_map(tree: ast.Module) -> dict[int, ast.AST]:
    """Every node's parent, so a guard can ask how a value is *used*."""
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


#: Built once per module and keyed by file name, so the trees the parents were
#: taken from are the same objects the guards walk.
_PRODUCTION_TREES: dict[str, ast.Module] = {
    path.name: ast.parse(path.read_text(encoding="utf-8"))
    for path in sorted(DOMAIN_DIR.glob("*.py"))
}
_PARENTS: dict[str, dict[int, ast.AST]] = {
    name: _parent_map(tree) for name, tree in _PRODUCTION_TREES.items()
}


def _docstring_node_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _executable_tokens(tree: ast.Module) -> list[tuple[int, str]]:
    skip = _docstring_node_ids(tree)
    tokens: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Name):
            tokens.append((line, node.id))
        elif isinstance(node, ast.Attribute):
            tokens.append((line, node.attr))
        elif isinstance(node, ast.arg):
            tokens.append((line, node.arg))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            tokens.append((line, node.name))
        elif isinstance(node, ast.keyword) and node.arg:
            tokens.append((line, node.arg))
        elif isinstance(node, ast.alias):
            tokens.append((line, node.name))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            tokens.append((line, node.value))
    return tokens


_WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+")

#: Where admin metadata counts are allowed to be produced. Everywhere else a
#: count would be a step towards deciding something by tallying.
_COUNT_SITES = frozenset({
    "release_view",
    "validate_release_evidence",
    "validate_personal_decision_release",
    "active_release",
})


class TestStaticGuards:
    def test_the_module_set_is_exact(self) -> None:
        assert {path.name for path in DOMAIN_DIR.glob("*.py")} == {
            "__init__.py",
            "enums.py",
            "models.py",
            "manifest.py",
            "validation.py",
            "authoring.py",
            "runtime.py",
        }

    def test_the_dependency_direction_is_one_way(self) -> None:
        """Steps 8C to 8F must never learn that releases exist."""
        offenders: list[str] = []
        for domain in PURE_DOMAINS:
            for path in sorted((BACKEND_ROOT / "app" / "domains" / domain).glob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                offenders.extend(
                    f"{domain}/{path.name}: {module}"
                    for module in _imported_modules(tree)
                    if "personal_decision_release" in module
                )
        assert offenders == [], offenders

    def test_step_8g_authoring_does_not_import_step_8h(self) -> None:
        tree = ast.parse(
            (BACKEND_ROOT / "app" / "domains" / "personal_applicability" / "authoring.py")
            .read_text(encoding="utf-8")
        )
        assert not [
            module
            for module in _imported_modules(tree)
            if "personal_decision_release" in module
        ]

    def test_step_8h_depends_only_on_the_layers_it_is_allowed_to(self) -> None:
        allowed = {
            "personal_applicability",
            "personal_decision_aggregation",
            "personal_decision_explanation",
            "personal_decision_policy",
            "personal_decision_release",
            "personal_decision_semantics",
            "evidence",
        }
        seen: set[str] = set()
        for _name, tree in _production_sources():
            seen.update(
                module.split(".")[2]
                for module in _imported_modules(tree)
                if module.startswith("app.domains.")
            )
        assert seen <= allowed, seen - allowed

    def test_no_ai_network_or_unrelated_product_imports(self) -> None:
        banned_prefixes = ("httpx", "requests", "aiohttp", "openai", "google", "anthropic")
        banned_domains = (
            "app.domains.ai_gateway",
            "app.domains.off",
            "app.domains.recommendation",
            "app.domains.alternatives",
            "app.domains.purchase",
            "app.domains.family",
            "app.domains.value",
        )
        offenders: list[str] = []
        for name, tree in _production_sources():
            for module in _imported_modules(tree):
                if module.split(".")[0] in banned_prefixes:
                    offenders.append(f"{name}: {module}")
                if any(module.startswith(prefix) for prefix in banned_domains):
                    offenders.append(f"{name}: {module}")
        assert offenders == [], offenders

    def test_evidence_prose_is_never_read(self) -> None:
        """Direction is a reviewed rule, never something inferred from a paragraph.

        ``evidence_strength`` is deliberately not in this set -- see
        ``test_strength_is_read_only_for_step_8b_membership``. The prose fields
        are absolute: there is no legitimate reason for Step 8H to read a
        sentence a reviewer wrote about the evidence.
        """
        forbidden = {"summary", "scope", "strength_rationale", "notes"}
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in forbidden:
                    offenders.append(f"{name}:{node.lineno} reads .{node.attr}")
        assert offenders == [], offenders

    def test_strength_is_read_only_for_step_8b_membership(self) -> None:
        """Strength may be tested for membership in Step 8B's set. Nothing else.

        The one legitimate question is "would Step 8B accept this exact row",
        and the only shape that answers it is ``in
        PERSONAL_APPLICABILITY_STRENGTHS``. Every other use -- an equality
        against a grade, an ordering, a subscript into a table, an argument to
        a function -- is a step towards deriving a direction, an action, a
        weight or a rank from how strong the evidence looks, which is a
        judgement Step 8H has no authority to make.
        """
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute) or node.attr != "evidence_strength":
                    continue
                parent = _PARENTS[name].get(id(node))
                permitted = (
                    isinstance(parent, ast.Compare)
                    and parent.left is node
                    and len(parent.ops) == 1
                    and isinstance(parent.ops[0], ast.NotIn | ast.In)
                    and isinstance(parent.comparators[0], ast.Name)
                    and parent.comparators[0].id == "PERSONAL_APPLICABILITY_STRENGTHS"
                )
                # A membership test is the whole permitted use. The value may
                # also be quoted back in the refusal message, which is a
                # formatted read and decides nothing.
                in_message = isinstance(parent, ast.FormattedValue)
                if not (permitted or in_message):
                    offenders.append(
                        f"{name}:{node.lineno}: .evidence_strength used as "
                        f"{type(parent).__name__}"
                    )
        assert offenders == [], offenders

    def test_the_strength_allowlist_is_step_8bs_own_set_not_a_copy(self) -> None:
        """Two copies would drift, and the drift that matters approves a dead release."""
        from app.domains.personal_applicability.service import (
            PERSONAL_APPLICABILITY_STRENGTHS as authority,
        )
        from app.domains.personal_decision_release import validation as release_validation

        assert release_validation.PERSONAL_APPLICABILITY_STRENGTHS is authority
        # And no Step 8H module restates the grades as literals of its own.
        for name, tree in _production_sources():
            for line, token in _executable_tokens(tree):
                assert token not in {"strong", "moderate", "limited"}, f"{name}:{line}"

    def test_no_action_member_is_ever_named_in_production(self) -> None:
        """An action may be parsed and copied, never chosen.

        Naming a member is what a ``{SUPPORTING: BUY}`` table or an ``if``
        chain would have to do. Direction members *are* named, in the
        signal-set map, and that is a different thing: it checks that a
        reviewed policy's declared direction set matches the semantics it
        references, and never decides what follows from it.
        """
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "PersonalDecisionAction"
                ):
                    offenders.append(f"{name}:{node.lineno}: PersonalDecisionAction.{node.attr}")
        assert offenders == [], offenders

    def test_an_action_is_never_chosen_by_a_conditional(self) -> None:
        """No ``if`` in Step 8H may produce an action."""
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.If | ast.IfExp):
                    continue
                for inner in ast.walk(node):
                    if (
                        isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == "PersonalDecisionAction"
                    ):
                        offenders.append(f"{name}:{inner.lineno}")
        assert offenders == [], offenders

    def test_no_scoring_ranking_or_voting_vocabulary(self) -> None:
        banned_words = {
            "average",
            "confidence",
            "dominant",
            "grade",
            "magnitude",
            "majority",
            "net",
            "percentage",
            "points",
            "precedence",
            "priority",
            "rank",
            "ranked",
            "ranking",
            "rating",
            "ratio",
            "score",
            "scores",
            "scoring",
            "strongest",
            "sum",
            "tally",
            "threshold",
            "vote",
            "votes",
            "weight",
            "weighted",
            "weights",
            "winner",
        }
        offenders: list[str] = []
        for name, tree in _production_sources():
            for line, token in _executable_tokens(tree):
                for word in _WORD.findall(token):
                    if word.casefold() in banned_words:
                        offenders.append(f"{name}:{line}: {token!r}")
        assert offenders == [], offenders

    def test_counts_exist_only_as_admin_metadata(self) -> None:
        """A count may describe a release. It may never feed a decision."""
        offenders: list[str] = []
        for name, tree in _production_sources():
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if node.name in _COUNT_SITES:
                    continue
                for inner in ast.walk(node):
                    if (
                        isinstance(inner, ast.keyword)
                        and inner.arg
                        and inner.arg.endswith("_checked")
                    ):
                        offenders.append(f"{name}:{inner.value.lineno}: {node.name}")
        assert offenders == [], offenders

    def test_the_pure_service_signatures_are_unchanged(self) -> None:
        """Step 8H sits around Steps 8C to 8F; it does not reshape them."""
        expectations = {
            project_personal_decision_semantics: ["personal_applicability", "rules"],
            aggregate_personal_decision_signals: ["semantics"],
            evaluate_personal_decision_policy: ["aggregation", "rules"],
            present_personal_decision: ["policy", "rules"],
        }
        for function, parameters in expectations.items():
            signature = inspect.signature(function)
            assert list(signature.parameters) == parameters, function.__name__
            assert not inspect.iscoroutinefunction(function), function.__name__
        for function in (
            project_personal_decision_semantics,
            evaluate_personal_decision_policy,
            present_personal_decision,
        ):
            seam = inspect.signature(function).parameters["rules"]
            assert seam.kind is inspect.Parameter.KEYWORD_ONLY
            assert seam.default == ()

    def test_the_orchestrator_is_pure_and_the_loader_is_the_only_async_reader(self) -> None:
        assert not inspect.iscoroutinefunction(evaluate_personal_decision_with_release)
        assert inspect.iscoroutinefunction(load_active_personal_decision_release)
        signature = inspect.signature(evaluate_personal_decision_with_release)
        assert list(signature.parameters) == ["personal_applicability", "release"]
        assert "session" not in signature.parameters

    def test_the_pure_layers_take_no_session_and_know_no_release_id(self) -> None:
        for function in (
            project_personal_decision_semantics,
            aggregate_personal_decision_signals,
            evaluate_personal_decision_policy,
            present_personal_decision,
        ):
            parameters = set(inspect.signature(function).parameters)
            assert not parameters & {"session", "release", "release_id"}

    def test_the_release_manifest_schema_names_no_customer_field(self) -> None:
        from app.domains.personal_decision_release import manifest as manifest_module

        every_key = (
            manifest_module._MANIFEST_KEYS
            | manifest_module._SEMANTIC_KEYS
            | manifest_module._POLICY_KEYS
            | manifest_module._EXPLANATION_KEYS
            | manifest_module._SEMANTIC_IDENTITY_KEYS
        )
        forbidden = {
            "account_id",
            "profile_id",
            "device_id",
            "scan_id",
            "label_snapshot_id",
            "medication",
            "condition",
            "body_facts",
        }
        assert not every_key & forbidden

    def test_the_release_model_carries_no_account_or_device_column(self) -> None:
        columns = set(PersonalDecisionRelease.__table__.columns.keys())
        assert not columns & {"account_id", "device_id", "profile_id", "scan_id"}

    def test_the_status_vocabulary_is_exactly_four_words(self) -> None:
        assert [status.value for status in PersonalDecisionReleaseStatus] == [
            "draft",
            "approved",
            "active",
            "retired",
        ]

    def test_no_transition_reopens_a_reviewed_release(self) -> None:
        from app.domains.personal_decision_release.enums import ALLOWED_RELEASE_TRANSITIONS

        assert ALLOWED_RELEASE_TRANSITIONS[PersonalDecisionReleaseStatus.RETIRED] == frozenset()
        for source, targets in ALLOWED_RELEASE_TRANSITIONS.items():
            assert PersonalDecisionReleaseStatus.DRAFT not in targets, source
        assert PersonalDecisionReleaseStatus.ACTIVE not in (
            ALLOWED_RELEASE_TRANSITIONS[PersonalDecisionReleaseStatus.RETIRED]
        )


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


async def _alembic(command: str, revision: str) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        command,
        revision,
        cwd=BACKEND_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    assert process.returncode == 0, output.decode(errors="replace")


async def test_the_migration_round_trips_and_keeps_the_single_active_invariant(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _publish_claim(session)
        await _activated(session, _manifest())
        await session.commit()

    await sql.dispose_engine()
    upgraded = False
    try:
        await _alembic("downgrade", BASE_REVISION)
        async with sql.get_engine().connect() as connection:
            assert await connection.scalar(
                text("SELECT to_regclass('personal_decision_releases')")
            ) is None
            # Evidence written before the downgrade is untouched by it.
            assert await connection.scalar(
                text("SELECT count(*) FROM evidence_claims")
            ) == 1
        await sql.dispose_engine()
        await _alembic("upgrade", "head")
        upgraded = True
        async with sql.get_engine().connect() as connection:
            index = await connection.scalar(text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_personal_decision_releases_active'"
            ))
            assert index is not None
            assert "UNIQUE" in index
            assert "status" in index and "'active'" in index

            status_check = await connection.scalar(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_personal_decision_releases_status'"
            ))
            for value in ("draft", "approved", "active", "retired"):
                assert value in status_check

            with pytest.raises(IntegrityError):
                await connection.execute(text(
                    "INSERT INTO personal_decision_releases "
                    "(id, release_key, release_version, manifest_schema_version, manifest, "
                    "content_hash, status, created_by, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'for_you.personal_decision', 1, 1, '{}'::jsonb, "
                    "'not-a-hash', 'draft', 'admin', now(), now())"
                ))
    finally:
        if not upgraded:
            await sql.dispose_engine()
            await _alembic("upgrade", "head")
        await sql.dispose_engine()


# ---------------------------------------------------------------------------
# Evidence strength: exactly Step 8B's boundary, and nothing more
# ---------------------------------------------------------------------------


class TestEvidenceStrengthEligibility:
    """Step 8H must accept exactly what Step 8B accepts.

    Strength is read here for one reason: to answer "would Step 8B project this
    exact row". It never becomes a direction, an action, a rank or a weight --
    see ``TestStaticGuards.test_strength_is_read_only_for_step_8b_membership``.
    """

    @pytest.mark.parametrize(
        "strength",
        [
            EvidenceStrength.STRONG.value,
            EvidenceStrength.MODERATE.value,
            EvidenceStrength.LIMITED.value,
        ],
    )
    async def test_every_accepted_strength_stays_eligible(self, db_clean, strength) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session, evidence_strength=strength)
            report = await _validate(session, _manifest())
        assert report.semantic_evidence_checked == 1

    @pytest.mark.parametrize(
        "strength",
        [EvidenceStrength.TRADITIONAL.value, EvidenceStrength.INSUFFICIENT.value],
    )
    async def test_a_strength_step_8b_rejects_blocks_the_release(
        self, db_clean, strength
    ) -> None:
        """Otherwise perfectly valid published evidence, graded outside the set."""
        factory = get_sessionmaker()
        async with factory() as session:
            claim = await _publish_claim(session)
            await session.commit()
            claim_id = claim.id

        # Corrupt only the grade, directly, leaving everything else intact.
        async with factory() as session:
            row = await session.get(EvidenceClaim, claim_id)
            row.evidence_strength = strength
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await _validate(session, _manifest())
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_ELIGIBLE
        )
        assert strength in caught.value.message

    @pytest.mark.parametrize(
        "strength",
        [EvidenceStrength.TRADITIONAL.value, EvidenceStrength.INSUFFICIENT.value],
    )
    async def test_the_real_step_8b_does_not_project_that_evidence_either(
        self, db_clean, strength
    ) -> None:
        """The point of the allowlist: Step 8H's answer must match Step 8B's."""
        factory = get_sessionmaker()
        async with factory() as session:
            claim = await _publish_claim(session)
            await session.commit()
            claim_id = claim.id

        async with factory() as session:
            applicability = await _real_step_8b(session)
        assert applicability.ingredients[0].personal_applicability_status is (
            PersonalApplicabilityStatus.PERSONAL_EVIDENCE_AVAILABLE
        )

        async with factory() as session:
            row = await session.get(EvidenceClaim, claim_id)
            row.evidence_strength = strength
            await session.commit()

        async with factory() as session:
            applicability = await _real_step_8b(session)
        ingredient = applicability.ingredients[0]
        assert ingredient.claims == ()
        assert ingredient.personal_applicability_status is (
            PersonalApplicabilityStatus.NOT_ENOUGH_INFORMATION
        )

    async def test_approval_is_blocked_by_a_disallowed_strength(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            claim = await _publish_claim(session)
            view = await _verified_draft(session, _manifest())
            await session.commit()
            claim_id, release_id = claim.id, uuid.UUID(view["id"])

        async with factory() as session:
            row = await session.get(EvidenceClaim, claim_id)
            row.evidence_strength = EvidenceStrength.INSUFFICIENT.value
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.approve_personal_decision_release(
                    session, release_id, actor="admin.synthetic"
                )
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_ELIGIBLE
        )


# ---------------------------------------------------------------------------
# A semantic rule Step 8B cannot project, even when the citation is fine
# ---------------------------------------------------------------------------


SUBSTANCE_B = "niacinamide"


def _two_rule_bundle(*, source_key_a: str, locator_a: str | None) -> dict:
    """sem.a and sem.b, one policy needing both, one explanation anchored to sem.a."""
    return _manifest(
        [
            _semantic(rule_id="sem.a", claim_key="claim.a", substance_key=SUBSTANCE),
            _semantic(rule_id="sem.b", claim_key="claim.b", substance_key=SUBSTANCE_B),
        ],
        [_policy(identities=[("sem.a", "1"), ("sem.b", "1")])],
        [
            _explanation(
                semantic_rule_id="sem.a",
                claim_key="claim.a",
                substance_key=SUBSTANCE,
                source_key=source_key_a,
                source_locator=locator_a,
            )
        ],
    )


class TestNonAnchorSemanticSource:
    """A valid citation must not vouch for a rule the citation says nothing about.

    The explanation names sem.a's source. sem.b is never displayed -- but the
    policy cannot fire without it, so a sem.b that Step 8B can no longer
    project makes the whole reviewed decision unreachable. Approving on the
    strength of sem.a's still-valid citation would approve a release that
    cannot work.
    """

    async def _setup(self, session) -> None:
        await _publish_claim(
            session, claim_key="claim.a", source_key="src.a", subject_key=SUBSTANCE
        )
        await _publish_claim(
            session, claim_key="claim.b", source_key="src.b", subject_key=SUBSTANCE_B
        )

    async def test_both_rules_eligible_validates(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await self._setup(session)
            report = await _validate(
                session, _two_rule_bundle(source_key_a="src.a", locator_a="section 2")
            )
        assert report.semantic_evidence_checked == 2

    @pytest.mark.parametrize(
        ("label", "override"),
        [
            ("retired source", {"status": SourceStatus.RETIRED.value}),
            ("bad source type", {"source_type": SourceType.MANUFACTURER_CLAIM.value}),
            ("licence note removed", {"license_or_use_note": None}),
            ("url not openable", {"canonical_url": "https://"}),
        ],
    )
    async def test_a_non_anchor_rule_losing_its_only_source_blocks_the_release(
        self, db_clean, label, override
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await self._setup(session)
            await session.commit()

        # Corrupt only sem.b's source. sem.a and the displayed citation are
        # untouched and remain perfectly valid.
        async with factory() as session:
            source = (
                await session.execute(
                    select(EvidenceSource).where(EvidenceSource.source_key == "src.b")
                )
            ).scalar_one()
            for field, value in override.items():
                assert field in EvidenceSource.__table__.columns, field
                setattr(source, field, value)
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await _validate(
                    session, _two_rule_bundle(source_key_a="src.a", locator_a="section 2")
                )
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_ELIGIBLE
        ), label
        assert "sem.b" in caught.value.message

        # And Step 8B agrees: the claim behind sem.b is simply gone.
        async with factory() as session:
            applicability = await _real_step_8b(
                session, substances=(SUBSTANCE, SUBSTANCE_B)
            )
        by_key = {
            ingredient.substance_key: ingredient for ingredient in applicability.ingredients
        }
        assert by_key[SUBSTANCE].claims != ()
        assert by_key[SUBSTANCE_B].claims == ()
        assert by_key[SUBSTANCE_B].personal_applicability_status is (
            PersonalApplicabilityStatus.NOT_ENOUGH_INFORMATION
        )

    async def test_approval_is_blocked_and_the_citation_gate_is_not_what_fired(
        self, db_clean
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await self._setup(session)
            view = await _verified_draft(
                session, _two_rule_bundle(source_key_a="src.a", locator_a="section 2")
            )
            await session.commit()
            release_id = uuid.UUID(view["id"])

        async with factory() as session:
            source = (
                await session.execute(
                    select(EvidenceSource).where(EvidenceSource.source_key == "src.b")
                )
            ).scalar_one()
            source.status = SourceStatus.RETIRED.value
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.approve_personal_decision_release(
                    session, release_id, actor="admin.synthetic"
                )
            row = await session.get(PersonalDecisionRelease, release_id)
            assert row.status == PersonalDecisionReleaseStatus.DRAFT.value
        assert _reason(caught.value) is not (
            PersonalDecisionReleaseValidationCode.EXPLANATION_SOURCE_PATH_NOT_ELIGIBLE
        )
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_ELIGIBLE
        )

    async def test_the_two_source_gates_cost_no_extra_queries(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await self._setup(session)
            await session.commit()

        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        engine = sql.get_engine().sync_engine
        async with factory() as session:
            event.listen(engine, "before_cursor_execute", record)
            try:
                await validate_release_evidence(
                    session,
                    parse_release_manifest(
                        _two_rule_bundle(source_key_a="src.a", locator_a="section 2")
                    ),
                )
            finally:
                event.remove(engine, "before_cursor_execute", record)
        assert len(statements) == 2


# ---------------------------------------------------------------------------
# Persisted-state revalidation at approval and activation
# ---------------------------------------------------------------------------


class TestPersistedStateRevalidation:
    """`status == approved` records that checks passed once, not that they still do.

    Every field those checks read -- the manifest, the hash, the schema column,
    the attestations -- is editable in the database. Activation installs a row
    into production, so it must re-ask, not remember.
    """

    async def _approved_pair(self, factory) -> tuple[uuid.UUID, uuid.UUID]:
        """An active release, and an approved candidate to activate over it."""
        async with factory() as session:
            await _publish_claim(session)
            active = await _activated(session, _manifest())
            candidate = await _approved(session, _manifest())
            await session.commit()
        return uuid.UUID(active["id"]), uuid.UUID(candidate["id"])

    async def _assert_no_switch(self, factory, active_id, candidate_id) -> None:
        async with factory() as session:
            rows = {
                row.id: row.status
                for row in (
                    await session.execute(select(PersonalDecisionRelease))
                ).scalars().all()
            }
        assert rows[active_id] == PersonalDecisionReleaseStatus.ACTIVE.value
        assert rows[candidate_id] == PersonalDecisionReleaseStatus.APPROVED.value
        async with factory() as session:
            loaded = await load_active_personal_decision_release(session)
        assert loaded is not None
        assert loaded.release_id == active_id

    @pytest.mark.parametrize(
        ("label", "mutate"),
        [
            (
                "one checkpoint false",
                lambda block: {**block, "founder_review_completed": False},
            ),
            ("unresolved doubt", lambda block: {**block, "unresolved_doubt": True}),
            ("verification removed", lambda block: None),
            ("verification truncated", lambda block: {"founder_review_completed": True}),
        ],
    )
    async def test_corrupted_verification_blocks_activation(
        self, db_clean, label, mutate
    ) -> None:
        factory = get_sessionmaker()
        active_id, candidate_id = await self._approved_pair(factory)

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, candidate_id)
            row.review_verification = mutate(dict(row.review_verification or {}))
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.activate_personal_decision_release(
                    session, candidate_id, actor="admin.synthetic"
                )
            await session.rollback()
        assert _reason(caught.value) in {
            PersonalDecisionReleaseValidationCode.RELEASE_VERIFICATION_INCOMPLETE,
            PersonalDecisionReleaseValidationCode.RELEASE_UNRESOLVED_DOUBT,
        }, label
        await self._assert_no_switch(factory, active_id, candidate_id)

    async def test_unresolved_doubt_specifically_blocks_activation(self, db_clean) -> None:
        factory = get_sessionmaker()
        active_id, candidate_id = await self._approved_pair(factory)

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, candidate_id)
            row.review_verification = {**row.review_verification, "unresolved_doubt": True}
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.activate_personal_decision_release(
                    session, candidate_id, actor="admin.synthetic"
                )
            await session.rollback()
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.RELEASE_UNRESOLVED_DOUBT
        )
        await self._assert_no_switch(factory, active_id, candidate_id)

    async def test_a_corrupted_schema_column_blocks_activation(self, db_clean) -> None:
        """The manifest and hash are fine; only the column the loader reads is not.

        Runtime already refuses such a row, so activating it would install a
        release production cannot load -- an outage created by an activation
        step that only looked inside the JSON.
        """
        factory = get_sessionmaker()
        active_id, candidate_id = await self._approved_pair(factory)

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, candidate_id)
            row.manifest_schema_version = 999
            await session.commit()

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, candidate_id)
            # The manifest itself is untouched and still hashes correctly.
            assert release_authoring.persisted_manifest(row) is not None
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.activate_personal_decision_release(
                    session, candidate_id, actor="admin.synthetic"
                )
            await session.rollback()
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.RELEASE_SCHEMA_VERSION_UNSUPPORTED
        )
        await self._assert_no_switch(factory, active_id, candidate_id)

    async def test_the_schema_column_is_not_repaired(self, db_clean) -> None:
        factory = get_sessionmaker()
        _active_id, candidate_id = await self._approved_pair(factory)
        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, candidate_id)
            row.manifest_schema_version = 999
            await session.commit()
        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseValidationError):
                await release_authoring.activate_personal_decision_release(
                    session, candidate_id, actor="admin.synthetic"
                )
            await session.rollback()
        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, candidate_id)
            assert row.manifest_schema_version == 999

    async def test_a_corrupted_schema_column_blocks_approval(self, db_clean) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_claim(session)
            view = await _verified_draft(session, _manifest())
            await session.commit()
            release_id = uuid.UUID(view["id"])

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, release_id)
            row.manifest_schema_version = 999
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.approve_personal_decision_release(
                    session, release_id, actor="admin.synthetic"
                )
            await session.rollback()
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.RELEASE_SCHEMA_VERSION_UNSUPPORTED
        )

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, release_id)
            assert row.status == PersonalDecisionReleaseStatus.DRAFT.value
            assert row.manifest_schema_version == 999

    async def test_a_corrupted_manifest_hash_blocks_activation(self, db_clean) -> None:
        factory = get_sessionmaker()
        active_id, candidate_id = await self._approved_pair(factory)

        async with factory() as session:
            row = await session.get(PersonalDecisionRelease, candidate_id)
            row.content_hash = "0" * 64
            await session.commit()

        async with factory() as session:
            with pytest.raises(PersonalDecisionReleaseValidationError) as caught:
                await release_authoring.activate_personal_decision_release(
                    session, candidate_id, actor="admin.synthetic"
                )
            await session.rollback()
        assert _reason(caught.value) is (
            PersonalDecisionReleaseValidationCode.RELEASE_CONTENT_HASH_MISMATCH
        )
        await self._assert_no_switch(factory, active_id, candidate_id)

    async def test_a_healthy_candidate_still_activates(self, db_clean) -> None:
        """The guards above must not have made a correct replacement impossible."""
        factory = get_sessionmaker()
        active_id, candidate_id = await self._approved_pair(factory)

        async with factory() as session:
            activated = await release_authoring.activate_personal_decision_release(
                session, candidate_id, actor="admin.synthetic"
            )
            await session.commit()
        assert activated["status"] == PersonalDecisionReleaseStatus.ACTIVE.value

        async with factory() as session:
            rows = {
                row.id: row.status
                for row in (
                    await session.execute(select(PersonalDecisionRelease))
                ).scalars().all()
            }
        assert rows[active_id] == PersonalDecisionReleaseStatus.RETIRED.value
        assert rows[candidate_id] == PersonalDecisionReleaseStatus.ACTIVE.value
