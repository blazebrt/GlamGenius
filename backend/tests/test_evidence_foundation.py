from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.bootstrap import run as run_reference_seed
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.evidence.seed import EMA_RETINOID_SOURCE_REF, EVIDENCE_SEED_VERSION
from app.domains.evidence.service import (
    EvidenceApprovalError,
    EvidenceRuleResolutionError,
    RuleEvidenceAssessment,
    assert_claim_approvable,
    assess_rule_evidence,
    evidence_state_for_rule,
)
from app.domains.reference import SeedVersionRecord
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.asyncio


def _source(**overrides):
    values = dict(
        source_key="test.source.v1", source_series_key="test.source", source_type="official_guideline",
        title="Test source", publisher="Test body", jurisdiction="US",
        accessed_at=datetime(2026, 1, 1, tzinfo=UTC), status="active",
    )
    values.update(overrides)
    return EvidenceSource(**values)


def _claim(**overrides):
    values = dict(
        claim_key="test.claim", claim_version=1, domain="skin_care", subject_type="ingredient",
        subject_key="retinoid", claim_type="usage_context", summary="Summary", scope="Scope",
        review_status="draft", regulatory_context="unknown", ai_generated=True,
    )
    values.update(overrides)
    return EvidenceClaim(**values)


async def test_evidence_seed_is_idempotent_and_all_pilot_claims_are_draft(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_reference_seed(session)
        first = (await run_reference_seed(session))["evidence"]
    async with factory() as session:
        second = (await run_reference_seed(session))["evidence"]
        claims = (await session.execute(select(EvidenceClaim))).scalars().all()
        sources = (await session.execute(select(EvidenceSource))).scalars().all()
        claim_sources = (await session.execute(select(EvidenceClaimSource))).scalars().all()
        links = (await session.execute(select(RuleEvidenceLink))).scalars().all()
        audit = (await session.execute(select(SeedVersionRecord).where(SeedVersionRecord.seed_domain == "evidence", SeedVersionRecord.seed_version == EVIDENCE_SEED_VERSION))).scalar_one()
    assert first["seed_version"] == EVIDENCE_SEED_VERSION
    pilot_keys = {"skin.topical_retinoid_pregnancy_regulatory_context", "skin.tretinoin_salicylic_concurrent_irritation_context"}
    pilot_claims = [claim for claim in claims if claim.claim_key in pilot_keys]
    pilot_claim_ids = {claim.id for claim in pilot_claims}
    pilot_sources = [source for source in sources if source.source_key in {EMA_RETINOID_SOURCE_REF, "fda-m006-acne-2021", "fda-tretinoin-nda75264-label"}]
    pilot_links = [link for link in links if link.rule_kind.startswith("ingredient_")]
    assert (len(pilot_sources), len(pilot_claims), len([link for link in claim_sources if link.claim_id in pilot_claim_ids]), len(pilot_links)) == (3, 2, 3, 2)
    assert second["sources"] == 3 and second["claims"] == 2 and second["rows_written"] == 10 and audit.rows_written == 10
    assert all(c.review_status == "draft" and c.ai_generated for c in pilot_claims)
    assert all(link.reviewed_at is None and link.reviewed_by is None for link in pilot_links)


async def test_evidence_seed_rejects_immutable_source_drift(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_reference_seed(session)
        await run_reference_seed(session)
        source = (await session.execute(select(EvidenceSource).where(EvidenceSource.source_key == EMA_RETINOID_SOURCE_REF))).scalar_one()
        source.title = "Tampered title"
        await session.commit()
    async with factory() as session:
        with pytest.raises(ValueError, match="evidence source drift"):
            await run_reference_seed(session)


async def test_evidence_seed_rejects_claim_and_link_provenance_drift(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_reference_seed(session)
        claim = (await session.execute(select(EvidenceClaim).where(EvidenceClaim.claim_key == "skin.topical_retinoid_pregnancy_regulatory_context"))).scalar_one()
        claim.scope = "tampered scope"
        await session.commit()
    async with factory() as session:
        with pytest.raises(ValueError, match="evidence claim drift"):
            await run_reference_seed(session)


async def test_evidence_seed_rejects_claim_source_provenance_drift(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_reference_seed(session)
        claim_source = (await session.execute(select(EvidenceClaimSource).join(EvidenceClaim).where(EvidenceClaim.claim_key == "skin.tretinoin_salicylic_concurrent_irritation_context"))).scalars().first()
        claim_source.locator = "tampered locator"
        await session.commit()
    async with factory() as session:
        with pytest.raises(ValueError, match="claim-source drift"):
            await run_reference_seed(session)


async def test_evidence_seed_rejects_rule_link_provenance_drift(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_reference_seed(session)
        rule_link = (await session.execute(select(RuleEvidenceLink).where(RuleEvidenceLink.rule_kind == "ingredient_compatibility"))).scalars().first()
        rule_link.relationship = "limits"
        await session.commit()
    async with factory() as session:
        with pytest.raises(ValueError, match="rule evidence link drift"):
            await run_reference_seed(session)


async def test_pilot_rule_truth_and_legacy_state(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_reference_seed(session)
        assert await evidence_state_for_rule(session, domain="skin_care", rule_kind="ingredient_contraindication", rule_id="retinoid__pregnancy", rule_version="2026.02.16") == "legacy_curated"
        assert await evidence_state_for_rule(session, domain="skin_care", rule_kind="ingredient_compatibility", rule_id="rule.retinoid_bha", rule_version="phase6-v1") == "legacy_curated"
        assert await evidence_state_for_rule(session, domain="skin_care", rule_kind="ingredient_contraindication", rule_id="retinol__pregnancy", rule_version="2026.02.16") == "legacy_curated"


async def test_retinoid_bha_rule_parity_is_unchanged(db_clean):
    from app.domains.routines.ontology import COMPATIBILITY_RULES, ONTOLOGY_VERSION

    rule = next(item for item in COMPATIBILITY_RULES if item.rule_id == "rule.retinoid_bha")
    assert (rule.rule_id, rule.severity, rule.headline, rule.guidance, ONTOLOGY_VERSION) == (
        "rule.retinoid_bha", "caution", "Retinoid and salicylic acid in the same routine",
        "Both can be drying. Alternating nights is the usual way people manage it.", "phase6-v1",
    )


async def test_approval_requires_human_review_and_reviewed_active_source(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        source = _source(); claim = _claim(); session.add_all([source, claim]); await session.flush()
        session.add(EvidenceClaimSource(claim_id=claim.id, source_id=source.id, relationship="supports")); await session.flush()
        with pytest.raises(EvidenceApprovalError):
            await assert_claim_approvable(session, claim)
        claim.review_status = "approved"; claim.reviewed_by = "reviewer"; claim.reviewed_at = datetime.now(UTC); claim.claim_status = "qualified"; claim.evidence_strength = "limited"; claim.strength_rationale = "Human rationale."
        with pytest.raises(EvidenceApprovalError):
            await assert_claim_approvable(session, claim)
        source_link = (await session.execute(select(EvidenceClaimSource))).scalar_one()
        source_link.reviewed_by = "reviewer"
        source_link.reviewed_at = datetime.now(UTC)
        await assert_claim_approvable(session, claim)


async def test_approved_claim_and_reviewed_link_becomes_evidence_linked(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        source = _source(); claim = _claim(review_status="approved", reviewed_by="reviewer", reviewed_at=datetime.now(UTC), claim_status="supported", evidence_strength="moderate", strength_rationale="Reviewed rationale."); session.add_all([source, claim]); await session.flush()
        link = EvidenceClaimSource(claim_id=claim.id, source_id=source.id, relationship="supports", reviewed_by="reviewer", reviewed_at=datetime.now(UTC)); session.add(link); await session.flush()
        rule = RuleEvidenceLink(domain="skin_care", rule_kind="ingredient_compatibility", rule_id="rule.retinoid_bha", rule_version="phase6-v1", claim_id=claim.id, relationship="supports", reviewed_by="reviewer", reviewed_at=datetime.now(UTC)); session.add(rule); await session.commit()
        assert await evidence_state_for_rule(session, domain="skin_care", rule_kind="ingredient_compatibility", rule_id="rule.retinoid_bha", rule_version="phase6-v1") == "evidence_linked"
        source.status = "retired"; await session.commit()
        assert await evidence_state_for_rule(session, domain="skin_care", rule_kind="ingredient_compatibility", rule_id="rule.retinoid_bha", rule_version="phase6-v1") == "legacy_curated"


async def test_invalid_rule_identity_fails_closed(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        source = _source(); claim = _claim(review_status="approved", reviewed_by="reviewer", reviewed_at=datetime.now(UTC), claim_status="supported", evidence_strength="moderate", strength_rationale="Reviewed rationale."); session.add_all([source, claim]); await session.flush()
        session.add(EvidenceClaimSource(claim_id=claim.id, source_id=source.id, relationship="supports", reviewed_by="reviewer", reviewed_at=datetime.now(UTC)))
        session.add(RuleEvidenceLink(domain="skin_care", rule_kind="ingredient_compatibility", rule_id="rule.test", rule_version="v1", claim_id=claim.id, relationship="supports", reviewed_by="reviewer", reviewed_at=datetime.now(UTC))); await session.commit()
        with pytest.raises(EvidenceRuleResolutionError):
            await evidence_state_for_rule(session, domain="skin_care", rule_kind="ingredient_compatibility", rule_id="rule.test", rule_version="v1")


async def test_valid_rule_path_survives_another_draft_link(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        source = _source(); claim = _claim(review_status="approved", reviewed_by="reviewer", reviewed_at=datetime.now(UTC), claim_status="supported", evidence_strength="moderate", strength_rationale="Reviewed rationale."); session.add_all([source, claim]); await session.flush()
        session.add(EvidenceClaimSource(claim_id=claim.id, source_id=source.id, relationship="supports", reviewed_by="reviewer", reviewed_at=datetime.now(UTC)))
        session.add_all([
            RuleEvidenceLink(domain="skin_care", rule_kind="ingredient_compatibility", rule_id="rule.retinoid_bha", rule_version="phase6-v1", claim_id=claim.id, relationship="supports"),
            RuleEvidenceLink(domain="skin_care", rule_kind="ingredient_compatibility", rule_id="rule.retinoid_bha", rule_version="phase6-v1", claim_id=claim.id, relationship="qualifies", reviewed_by="reviewer", reviewed_at=datetime.now(UTC)),
        ]); await session.commit()
        assert await evidence_state_for_rule(session, domain="skin_care", rule_kind="ingredient_compatibility", rule_id="rule.retinoid_bha", rule_version="phase6-v1") == "evidence_linked"


async def test_superseded_claim_and_background_only_claim_stay_legacy(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        source = _source(); claim = _claim(review_status="approved", reviewed_by="reviewer", reviewed_at=datetime.now(UTC), claim_status="supported", evidence_strength="moderate", strength_rationale="Reviewed rationale."); session.add_all([source, claim]); await session.flush()
        session.add(EvidenceClaimSource(claim_id=claim.id, source_id=source.id, relationship="background", reviewed_by="reviewer", reviewed_at=datetime.now(UTC)))
        rule = RuleEvidenceLink(domain="skin_care", rule_kind="ingredient_compatibility", rule_id="rule.retinoid_bha", rule_version="phase6-v1", claim_id=claim.id, relationship="background", reviewed_by="reviewer", reviewed_at=datetime.now(UTC)); session.add(rule); await session.commit()
        with pytest.raises(EvidenceApprovalError):
            await assert_claim_approvable(session, claim)
        claim.review_status = "superseded"; await session.commit()
        assert await evidence_state_for_rule(session, domain="skin_care", rule_kind="ingredient_compatibility", rule_id="rule.retinoid_bha", rule_version="phase6-v1") == "legacy_curated"


async def test_global_evidence_tables_have_no_account_or_ai_run_columns(db_clean):
    from app.shared.database.registry import Base
    forbidden = {"account_id", "user_id", "inventory_id", "media_id", "ai_run_id"}
    for table_name in ("evidence_sources", "evidence_claims", "evidence_claim_sources", "rule_evidence_links"):
        assert forbidden.isdisjoint(Base.metadata.tables[table_name].columns.keys())


async def test_constraints_reject_invalid_versions_and_relationships(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        session.add(_claim(claim_version=0))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        session.add(_source()); await session.flush()
        session.add(_claim()); await session.flush()
        source = (await session.execute(select(EvidenceSource))).scalar_one(); claim = (await session.execute(select(EvidenceClaim))).scalar_one()
        session.add(EvidenceClaimSource(claim_id=claim.id, source_id=source.id, relationship="supports")); await session.flush()
        session.add(RuleEvidenceLink(domain="skin_care", rule_kind="ingredient_compatibility", rule_id="rule.x", rule_version="v1", claim_id=claim.id, relationship="contradicts"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_source_series_allows_revisions_and_repeated_url(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        session.add_all([
            _source(source_key="series.v1", source_series_key="series", canonical_url="https://example.test/source", publication_date=None),
            _source(source_key="series.v2", source_series_key="series", canonical_url="https://example.test/source", publication_date=None),
        ])
        await session.commit()
        assert len((await session.execute(select(EvidenceSource).where(EvidenceSource.source_series_key == "series"))).scalars().all()) == 2


async def test_source_self_supersession_is_rejected(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        source = _source(); session.add(source); await session.flush()
        source.supersedes_source_id = source.id
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_claim_self_supersession_is_rejected(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        claim = _claim(); session.add(claim); await session.flush()
        claim.supersedes_claim_id = claim.id
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_unknown_parser_input_stays_unmatched():
    from app.domains.routines.parser import parse_label, unmatched_terms

    assert parse_label("not-a-real-cosmetic-ingredient") == []
    assert "not-a-real-cosmetic-ingredient" in unmatched_terms("not-a-real-cosmetic-ingredient")


async def _approved_rule_bundle(session, *, relationship="supports", claim_status="supported"):
    source = _source(source_key=f"test.source.{uuid.uuid4().hex}")
    claim = _claim(
        claim_key=f"test.claim.{uuid.uuid4().hex}",
        review_status="approved", reviewed_by="reviewer",
        reviewed_at=datetime.now(UTC), claim_status=claim_status,
        evidence_strength="moderate", strength_rationale="Reviewed rationale.",
    )
    session.add_all([source, claim])
    await session.flush()
    session.add(EvidenceClaimSource(
        claim_id=claim.id, source_id=source.id, relationship="supports",
        reviewed_by="reviewer", reviewed_at=datetime.now(UTC),
    ))
    link = RuleEvidenceLink(
        domain="skin_care", rule_kind="ingredient_compatibility",
        rule_id="rule.retinoid_bha", rule_version="phase6-v1",
        claim_id=claim.id, relationship=relationship,
        reviewed_by="reviewer", reviewed_at=datetime.now(UTC),
    )
    session.add(link)
    await session.commit()
    return source, claim, link


async def test_rule_evidence_assessment_no_link_and_draft_link_fail_closed(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        empty = await assess_rule_evidence(
            session, domain="skin_care", rule_kind="ingredient_compatibility",
            rule_id="rule.retinoid_bha", rule_version="phase6-v1",
        )
        assert empty == RuleEvidenceAssessment(False, False, False)
        _, _, link = await _approved_rule_bundle(session)
        link.reviewed_at = None
        link.reviewed_by = None
        await session.commit()
        draft = await assess_rule_evidence(
            session, domain="skin_care", rule_kind="ingredient_compatibility",
            rule_id="rule.retinoid_bha", rule_version="phase6-v1",
        )
        assert draft.provenance_present is False
        assert draft.substantive_support_present is False
        assert draft.behavior_evidence_eligible is False


@pytest.mark.parametrize("relationship", ["background", "qualifies", "limits"])
async def test_non_support_relationships_provide_provenance_but_no_substantive_support(
    db_clean, relationship
):
    factory = get_sessionmaker()
    async with factory() as session:
        await _approved_rule_bundle(session, relationship=relationship)
        assessment = await assess_rule_evidence(
            session, domain="skin_care", rule_kind="ingredient_compatibility",
            rule_id="rule.retinoid_bha", rule_version="phase6-v1",
        )
    assert assessment.provenance_present is True
    assert assessment.substantive_support_present is False
    assert assessment.behavior_evidence_eligible is False


async def test_supports_supported_claim_has_substantive_support_but_stays_fail_closed(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _approved_rule_bundle(session, relationship="supports", claim_status="supported")
        assessment = await assess_rule_evidence(
            session, domain="skin_care", rule_kind="ingredient_compatibility",
            rule_id="rule.retinoid_bha", rule_version="phase6-v1",
        )
    assert assessment.provenance_present is True
    assert assessment.substantive_support_present is True
    assert assessment.behavior_evidence_eligible is False
    assert assessment.relationships == ("supports",)


async def test_valid_support_survives_unrelated_draft_link(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        _, claim, _ = await _approved_rule_bundle(session)
        draft_claim = _claim(claim_key=f"test.draft.{uuid.uuid4().hex}")
        session.add(draft_claim)
        await session.flush()
        session.add(RuleEvidenceLink(
            domain="skin_care", rule_kind="ingredient_compatibility",
            rule_id="rule.retinoid_bha", rule_version="phase6-v1",
            claim_id=draft_claim.id, relationship="qualifies",
        ))
        await session.commit()
        assessment = await assess_rule_evidence(
            session, domain="skin_care", rule_kind="ingredient_compatibility",
            rule_id="rule.retinoid_bha", rule_version="phase6-v1",
        )
    assert assessment.provenance_present is True
    assert assessment.substantive_support_present is True


@pytest.mark.parametrize("review_status", ["retired", "superseded"])
async def test_retired_or_superseded_claim_fails_closed(db_clean, review_status):
    factory = get_sessionmaker()
    async with factory() as session:
        _, claim, _ = await _approved_rule_bundle(session)
        claim.review_status = review_status
        await session.commit()
        assessment = await assess_rule_evidence(
            session, domain="skin_care", rule_kind="ingredient_compatibility",
            rule_id="rule.retinoid_bha", rule_version="phase6-v1",
        )
    assert assessment == RuleEvidenceAssessment(False, False, False)


async def test_inactive_source_fails_closed(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        source, _, _ = await _approved_rule_bundle(session)
        source.status = "retired"
        await session.commit()
        assessment = await assess_rule_evidence(
            session, domain="skin_care", rule_kind="ingredient_compatibility",
            rule_id="rule.retinoid_bha", rule_version="phase6-v1",
        )
    assert assessment == RuleEvidenceAssessment(False, False, False)
