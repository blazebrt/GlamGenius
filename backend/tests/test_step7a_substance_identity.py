"""Step 7A — canonical substance identity, and everything it refuses to do.

The layer under test answers exactly one question: *what exact substance or
material does this exact reviewed name refer to?* Not whether it is safe, what
it does, how much is present, or whether it is allowed. Most of what follows is
therefore a proof of absence — that the resolver stays silent where a looser
system would guess.

Four things are worth stating up front, because they shape every group below:

* **Published evidence is the only key that opens the door.** A draft, an
  approved-but-unpublished claim, an unverified one, a background source link,
  an unclassified source type, a retired source, a URL nobody can open, a
  missing licence note — each on its own leaves the name unresolvable.
* **Ambiguity is an answer.** Two entities that a reviewer recorded under one
  printed name resolve to AMBIGUOUS, and nothing here breaks the tie.
* **The legacy Care aliases were not copied.** ``tocopheryl acetate`` is not
  vitamin E, ``ceramide np`` is not "ceramides", ``keratin`` is not hydrolysed
  wheat protein, and ``peppermint oil`` is not menthol — however convenient the
  old family map found those equivalences.
* **An invalid row can only ever cost itself.** The Step 6B LIMIT-1 defect —
  narrowing before validating, so an ineligible row hid a valid one behind it —
  is tested for directly.
"""
from __future__ import annotations

import ast
import inspect
import uuid
from pathlib import Path

import pytest
from app.domains.evidence import authoring as evidence_authoring
from app.domains.evidence.enums import (
    EVIDENCE_STRENGTHS,
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
from app.domains.evidence.service import claim_is_public_knowledge_path
from app.domains.evidence.urls import openable_url
from app.domains.substances import authoring as substance_authoring
from app.domains.substances import service as substance_service
from app.domains.substances.enums import EntityKind, NameNamespace, SubstanceStatus
from app.domains.substances.identity_schema import (
    MAX_NAMES_PER_CLAIM,
    SUBSTANCE_IDENTITY_SCHEMA_VERSION,
    build_identity_payload,
    parse_identity,
)
from app.domains.substances.models import Substance, SubstanceName
from app.domains.substances.normalization import MAX_NAME_LENGTH, normalize_name
from app.domains.substances.service import (
    IDENTITY_EVIDENCE_STRENGTHS,
    IDENTITY_SOURCE_TYPES,
    MAX_BATCH_NAMES,
    ResolutionStatus,
    resolve_name,
    resolve_names,
)
from app.shared.database import sql
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import ValidationFailedError
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SUBSTANCES_DIR = BACKEND_ROOT / "app" / "domains" / "substances"

def _code_only(body: str) -> str:
    """The module with its docstrings, strings and comments removed.

    Several tests below assert that a word appears nowhere in this domain —
    ``risk_tier``, ``confidence``, ``embedding``. Those same words appear
    constantly in the prose that explains why the code refuses them, so a raw
    text scan would flag the explanation and force the documentation to be
    written around the test. Tokenising first means the assertions are about
    the code, which is what they were meant to be about.
    """
    import io as _io
    import token as _token
    import tokenize as _tokenize

    kept: list[str] = []
    for tok in _tokenize.generate_tokens(_io.StringIO(body).readline):
        if tok.type in {_token.STRING, _token.COMMENT}:
            continue
        if tok.type == getattr(_token, "FSTRING_START", -1):
            continue
        kept.append(tok.string)
    return " ".join(kept)


VERIFIED = evidence_authoring.VerificationInput(
    source_opened=True,
    founder_verified_fact=True,
    claude_review_completed=True,
    codex_review_completed=True,
    independent_reviews_agree=True,
    adversarial_review_passed=True,
    unresolved_doubt=False,
)


def _name(text_value: str, *, namespace: str = NameNamespace.INCI.value, preferred: bool = False):
    return {
        "name": text_value,
        "namespace": namespace,
        "language_tag": "und",
        "is_preferred": preferred,
    }


async def _draft(
    session,
    *,
    substance_key: str,
    names: list[dict],
    entity_kind: str = EntityKind.DEFINED_SUBSTANCE.value,
    source_type: str = SourceType.INGREDIENT_REFERENCE_DATABASE.value,
    source_url: str = "https://example.org/reference/entry",
    license_or_use_note: str = "Reproduced under the publisher's stated terms of use.",
) -> uuid.UUID:
    """Author one identity draft through the narrow adapter."""
    result = await substance_authoring.create_identity_draft(
        session,
        substance_key=substance_key,
        entity_kind=entity_kind,
        names=names,
        summary=f"Names recorded for {substance_key}.",
        scope="Nomenclature only.",
        evidence_strength=EvidenceStrength.STRONG.value,
        strength_rationale="A named reference work records this nomenclature directly.",
        source_title="Reference entry",
        source_publisher="Example Reference",
        source_type=source_type,
        source_url=source_url,
        license_or_use_note=license_or_use_note,
        author="tester",
    )
    return uuid.UUID(result["claim_id"])


async def _publish(session, claim_id: uuid.UUID) -> None:
    """Walk the real, unmodified evidence workflow to publication."""
    await evidence_authoring.approve(session, claim_id, reviewer="reviewer")
    await evidence_authoring.record_publication_verification(
        session, claim_id, verification=VERIFIED, actor="founder",
    )
    await evidence_authoring.publish(session, claim_id, publisher="founder")


async def _published(session, **kwargs) -> uuid.UUID:
    claim_id = await _draft(session, **kwargs)
    await _publish(session, claim_id)
    await session.commit()
    return claim_id


# ---------------------------------------------------------------------------
# A. Normalization — typographic variation only, never scientific
# ---------------------------------------------------------------------------
class TestNormalization:
    def test_case_variation_normalises_identically(self):
        keys = {normalize_name(v) for v in ("Niacinamide", "niacinamide", "NIACINAMIDE", "NiAcInAmIdE")}
        assert keys == {"niacinamide"}

    def test_surrounding_and_internal_whitespace_collapses(self):
        keys = {
            normalize_name(v)
            for v in ("  Sodium Hyaluronate ", "Sodium  Hyaluronate", "Sodium\tHyaluronate",
                      "Sodium\nHyaluronate", " Sodium Hyaluronate ")
        }
        assert keys == {"sodium hyaluronate"}

    def test_nfkc_compatibility_variation_normalises_identically(self):
        # Fullwidth Latin, a subscript digit and a compatibility ligature all
        # fold under NFKC. The ligature folds to the letters it stands for —
        # three of them for ｄﬄｄ, which is the point: NFKC expands, it does not guess.
        assert normalize_name("Ｎｉａｃｉｎａｍｉｄｅ") == "niacinamide"
        assert normalize_name("Vitamin B₃") == normalize_name("Vitamin B3") == "vitamin b3"
        assert normalize_name("Sunﬂower Seed Oil") == "sunflower seed oil"

    def test_punctuation_is_not_erased(self):
        """The distinction between two butanediols is one comma-and-digit apart."""
        assert normalize_name("1,3-Butanediol") == "1,3-butanediol"
        assert normalize_name("1,3-Butanediol") != normalize_name("1,4-butanediol")
        assert normalize_name("Sodium C14-16 Olefin Sulfonate") == "sodium c14-16 olefin sulfonate"

    def test_separators_are_not_rewritten(self):
        assert normalize_name("vitamin-e") != normalize_name("vitamin e")

    def test_retinal_does_not_become_retinaldehyde(self):
        assert normalize_name("Retinal") == "retinal"
        assert normalize_name("Retinal") != normalize_name("Retinaldehyde")

    def test_no_singularisation_or_stemming(self):
        assert normalize_name("Ceramides") != normalize_name("Ceramide NP")
        assert normalize_name("Ceramides") != normalize_name("Ceramide")

    def test_accents_are_preserved(self):
        assert normalize_name("Café Extract") == "café extract"
        assert normalize_name("Café Extract") != normalize_name("Cafe Extract")

    def test_unusable_inputs_yield_none(self):
        for bad in (None, 42, b"niacinamide", "", "   ", " ", "\t\n"):
            assert normalize_name(bad) is None

    def test_oversized_input_is_refused_not_truncated(self):
        assert normalize_name("a" * MAX_NAME_LENGTH) == "a" * MAX_NAME_LENGTH
        assert normalize_name("a" * (MAX_NAME_LENGTH + 1)) is None

    def test_normalizer_is_pure_and_deterministic(self):
        assert normalize_name("Glycerin") == normalize_name("Glycerin") == "glycerin"
        # Scanned with the docstring removed: the prose *names* the things the
        # code must not do, so scanning it would flag the explanation itself.
        source = _code_only(inspect.getsource(normalize_name))
        for forbidden in ("await", "session", "requests", "httpx", "random", "time.", "datetime"):
            assert forbidden not in source
        # And the module imports exactly one thing, from the standard library.
        imports = {
            line.strip() for line in
            (SUBSTANCES_DIR / "normalization.py").read_text(encoding="utf-8").splitlines()
            if line.startswith(("import ", "from "))
        }
        assert imports == {"from __future__ import annotations", "import unicodedata"}

    async def test_ingredient_list_string_is_not_substring_resolved(self, db_clean):
        """A printed list is one opaque string here, not a bag of names."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
            )
        async with factory() as session:
            assert (await resolve_name(session, "Niacinamide")).status is ResolutionStatus.RESOLVED
            for listy in (
                "Water, Niacinamide, Glycerin",
                "Aqua/Water/Eau, Niacinamide",
                "niacinamide 5%",
                "contains niacinamide",
                "Niacinamide;Glycerin",
            ):
                assert (await resolve_name(session, listy)).status is ResolutionStatus.UNRESOLVED


# ---------------------------------------------------------------------------
# B. The published-evidence gate — every way a name stays unresolvable
# ---------------------------------------------------------------------------
class TestPublishedEvidenceGate:
    async def test_draft_names_are_inert(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _draft(session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)])
            await session.commit()
        async with factory() as session:
            # The rows exist and are visible to authoring...
            assert (await session.execute(select(SubstanceName))).scalars().all()
            # ...and resolve nothing.
            assert (await resolve_name(session, "Glycerin")).status is ResolutionStatus.UNRESOLVED

    async def test_approved_but_unpublished_is_not_enough(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await _draft(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
            await evidence_authoring.approve(session, claim_id, reviewer="reviewer")
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Glycerin")).status is ResolutionStatus.UNRESOLVED

    async def test_rejected_claim_resolves_nothing(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await _draft(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
            await evidence_authoring.reject(
                session, claim_id, reviewer="reviewer", reason="Wrong entity.",
            )
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Glycerin")).status is ResolutionStatus.UNRESOLVED

    async def test_publication_requires_every_verification_checkpoint(self, db_clean):
        """Publication itself refuses; the resolver never sees a half-verified claim."""
        from app.shared.errors.exceptions import ValidationFailedError

        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await _draft(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
            await evidence_authoring.approve(session, claim_id, reviewer="reviewer")
            partial = evidence_authoring.VerificationInput(
                source_opened=True, founder_verified_fact=True,
                claude_review_completed=True, codex_review_completed=True,
                independent_reviews_agree=True, adversarial_review_passed=False,
            )
            await evidence_authoring.record_publication_verification(
                session, claim_id, verification=partial, actor="founder",
            )
            with pytest.raises(ValidationFailedError):
                await evidence_authoring.publish(session, claim_id, publisher="founder")

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("review_status", ReviewStatus.APPROVED.value),
            ("claim_status", None),
            ("published_by", None),
            ("published_at", None),
            ("reviewed_by", None),
            ("reviewed_at", None),
            ("evidence_strength", None),
            ("domain", EvidenceDomain.SKIN_CARE.value),
            ("subject_type", "ingredient"),
            ("subject_key", "something-else"),
            ("claim_type", ClaimType.USAGE_CONTEXT.value),
            ("evidence_tier", EvidenceTier.CLINICALLY_STUDIED.value),
        ],
    )
    async def test_tampering_with_the_claim_makes_it_ineligible(self, db_clean, field, value):
        """Every column the eligibility test reads, broken one at a time."""
        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session:
            assert (await resolve_name(session, "Glycerin")).status is ResolutionStatus.RESOLVED
        async with factory() as session:
            claim = await session.get(EvidenceClaim, claim_id)
            setattr(claim, field, value)
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Glycerin")).status is ResolutionStatus.UNRESOLVED

    async def test_a_graded_claim_cannot_lose_its_rationale(self, db_clean):
        """The database itself refuses, so the resolver never meets this state."""
        from sqlalchemy.exc import IntegrityError

        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session:
            claim = await session.get(EvidenceClaim, claim_id)
            claim.strength_rationale = None
            with pytest.raises(IntegrityError):
                await session.commit()

    async def test_clearing_both_strength_and_rationale_makes_it_ineligible(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session:
            claim = await session.get(EvidenceClaim, claim_id)
            claim.evidence_strength = None
            claim.strength_rationale = None
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Glycerin")).status is ResolutionStatus.UNRESOLVED

    async def test_unresolved_doubt_alone_makes_it_ineligible(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session:
            claim = await session.get(EvidenceClaim, claim_id)
            value = dict(claim.structured_value)
            verification = dict(value["publication_verification"])
            verification["unresolved_doubt"] = True
            value["publication_verification"] = verification
            claim.structured_value = value
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Glycerin")).status is ResolutionStatus.UNRESOLVED

    @pytest.mark.parametrize("checkpoint", [
        "source_opened", "founder_verified_fact", "claude_review_completed",
        "codex_review_completed", "independent_reviews_agree", "adversarial_review_passed",
    ])
    async def test_each_missing_checkpoint_makes_it_ineligible(self, db_clean, checkpoint):
        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session:
            claim = await session.get(EvidenceClaim, claim_id)
            value = dict(claim.structured_value)
            verification = dict(value["publication_verification"])
            verification[checkpoint] = False
            value["publication_verification"] = verification
            claim.structured_value = value
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Glycerin")).status is ResolutionStatus.UNRESOLVED

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("status", SourceStatus.RETIRED.value),
            ("source_type", SourceType.MANUFACTURER_CLAIM.value),
            ("source_type", SourceType.OTHER.value),
            ("canonical_url", "ftp://example.org/entry"),
            ("canonical_url", ""),
            ("license_or_use_note", None),
            ("license_or_use_note", "   "),
        ],
    )
    async def test_breaking_the_source_makes_it_ineligible(self, db_clean, field, value):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session:
            source = (await session.execute(select(EvidenceSource))).scalars().one()
            setattr(source, field, value)
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Glycerin")).status is ResolutionStatus.UNRESOLVED

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("relationship", ClaimSourceRelationship.BACKGROUND.value),
            ("reviewed_by", None),
            ("reviewed_at", None),
        ],
    )
    async def test_breaking_the_source_link_makes_it_ineligible(self, db_clean, field, value):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session:
            link = (await session.execute(select(EvidenceClaimSource))).scalars().one()
            setattr(link, field, value)
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Glycerin")).status is ResolutionStatus.UNRESOLVED

    async def test_retired_substance_resolves_nothing(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session:
            substance = (await session.execute(select(Substance))).scalars().one()
            substance.status = SubstanceStatus.RETIRED.value
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Glycerin")).status is ResolutionStatus.UNRESOLVED

    async def test_unsourced_claim_cannot_even_be_approved(self, db_clean):
        """No source link at all: the existing approval gate refuses first."""
        from app.shared.errors.exceptions import ValidationFailedError

        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await _draft(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
            for link in (await session.execute(select(EvidenceClaimSource))).scalars().all():
                await session.delete(link)
            await session.flush()
            with pytest.raises(ValidationFailedError):
                await evidence_authoring.approve(session, claim_id, reviewer="reviewer")

    @pytest.mark.parametrize("bad_type", [
        SourceType.MANUFACTURER_CLAIM.value,
        SourceType.MANUFACTURER_LABEL.value,
        SourceType.OTHER.value,
        SourceType.SYSTEMATIC_REVIEW.value,
        "not-a-source-type",
    ])
    async def test_authoring_refuses_an_unfit_source_type(self, db_clean, bad_type):
        from app.shared.errors.exceptions import ValidationFailedError

        factory = get_sessionmaker()
        async with factory() as session:
            with pytest.raises(ValidationFailedError):
                await _draft(
                    session, substance_key="glycerin",
                    names=[_name("Glycerin", preferred=True)], source_type=bad_type,
                )

    @pytest.mark.parametrize("bad_url", ["", "   ", "example.org", "ftp://example.org/x", "javascript:alert(1)"])
    async def test_authoring_requires_an_openable_url(self, db_clean, bad_url):
        from app.shared.errors.exceptions import ValidationFailedError

        factory = get_sessionmaker()
        async with factory() as session:
            with pytest.raises(ValidationFailedError):
                await _draft(
                    session, substance_key="glycerin",
                    names=[_name("Glycerin", preferred=True)], source_url=bad_url,
                )

    async def test_authoring_requires_an_explicit_licence_note(self, db_clean):
        from app.shared.errors.exceptions import ValidationFailedError

        factory = get_sessionmaker()
        async with factory() as session:
            with pytest.raises(ValidationFailedError):
                await _draft(
                    session, substance_key="glycerin",
                    names=[_name("Glycerin", preferred=True)], license_or_use_note="  ",
                )

    async def test_identity_source_types_exclude_marketing_and_unclassified(self):
        assert SourceType.OTHER.value not in IDENTITY_SOURCE_TYPES
        assert SourceType.MANUFACTURER_CLAIM.value not in IDENTITY_SOURCE_TYPES
        assert SourceType.MANUFACTURER_LABEL.value not in IDENTITY_SOURCE_TYPES


# ---------------------------------------------------------------------------
# C. The success path — through the real workflow, never a shortcut
# ---------------------------------------------------------------------------
class TestSuccessPath:
    async def test_published_identity_resolves_exactly(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session,
                substance_key="niacinamide",
                names=[
                    _name("Niacinamide", preferred=True),
                    _name("Nicotinamide", namespace=NameNamespace.SCIENTIFIC.value),
                ],
            )
        async with factory() as session:
            for query in ("Niacinamide", "  niacinamide  ", "NICOTINAMIDE"):
                result = await resolve_name(session, query)
                assert result.status is ResolutionStatus.RESOLVED
                assert result.substance_key == "niacinamide"
                assert result.entity_kind == EntityKind.DEFINED_SUBSTANCE.value
                assert result.candidate_substance_keys == ("niacinamide",)

    async def test_a_name_the_claim_never_recorded_stays_unresolved(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
            )
        async with factory() as session:
            for absent in ("Vitamin B3", "niacin", "nicotinic acid", "niacinamid"):
                assert (await resolve_name(session, absent)).status is ResolutionStatus.UNRESOLVED

    async def test_resolution_carries_no_score_or_confidence(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session:
            result = await resolve_name(session, "Glycerin")
        fields = set(vars(result))
        for forbidden in ("score", "confidence", "probability", "similarity", "rank", "distance"):
            assert not any(forbidden in field for field in fields), fields

    async def test_resolution_carries_no_interpretation(self, db_clean):
        """Identity, and nothing that would read as a judgement about it."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session:
            result = await resolve_name(session, "Glycerin")
        for forbidden in (
            "safe", "unsafe", "risk", "benefit", "efficacy", "function", "verdict",
            "grade", "concentration", "dose", "interaction", "absorption", "family",
            "regulatory",
        ):
            assert not any(forbidden in field for field in vars(result)), forbidden

    async def test_batch_resolution_answers_every_input_in_order(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
            await _published(
                session, substance_key="niacinamide", names=[_name("Niacinamide", preferred=True)],
            )
        queries = ["Niacinamide", "", "Glycerin", "not-a-substance", "  glycerin ", "Niacinamide"]
        async with factory() as session:
            results = await resolve_names(session, queries)
        assert [r.query for r in results] == queries
        assert [r.status for r in results] == [
            ResolutionStatus.RESOLVED, ResolutionStatus.UNRESOLVED, ResolutionStatus.RESOLVED,
            ResolutionStatus.UNRESOLVED, ResolutionStatus.RESOLVED, ResolutionStatus.RESOLVED,
        ]
        assert [r.substance_key for r in results] == [
            "niacinamide", None, "glycerin", None, "glycerin", "niacinamide",
        ]

    async def test_an_oversized_batch_is_refused_not_truncated(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            assert len(await resolve_names(session, ["x"] * MAX_BATCH_NAMES)) == MAX_BATCH_NAMES
            with pytest.raises(ValueError):
                await resolve_names(session, ["x"] * (MAX_BATCH_NAMES + 1))

    async def test_empty_batch_is_a_no_op(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            assert await resolve_names(session, []) == []


# ---------------------------------------------------------------------------
# D. Ambiguity — reported, never broken
# ---------------------------------------------------------------------------
class TestAmbiguity:
    async def test_two_entities_under_one_name_are_ambiguous(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="entity.alpha",
                names=[_name("Shared Printed Name", preferred=True)],
            )
            await _published(
                session, substance_key="entity.beta",
                names=[_name("Shared Printed Name", preferred=True)],
            )
        async with factory() as session:
            result = await resolve_name(session, "shared printed name")
        assert result.status is ResolutionStatus.AMBIGUOUS
        assert result.substance_id is None
        assert result.substance_key is None
        assert result.entity_kind is None
        assert result.candidate_substance_keys == ("entity.alpha", "entity.beta")

    async def test_ambiguity_is_not_broken_by_source_count(self, db_clean):
        """Three published paths to one entity, one to another. Still ambiguous."""
        factory = get_sessionmaker()
        async with factory() as session:
            for _ in range(3):
                await _published(
                    session, substance_key="entity.alpha",
                    names=[_name("Shared Printed Name", preferred=True)],
                )
            await _published(
                session, substance_key="entity.beta",
                names=[_name("Shared Printed Name", preferred=True)],
            )
        async with factory() as session:
            result = await resolve_name(session, "shared printed name")
        assert result.status is ResolutionStatus.AMBIGUOUS
        assert result.candidate_substance_keys == ("entity.alpha", "entity.beta")

    async def test_ambiguity_is_not_broken_by_evidence_strength(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            strong = await _published(
                session, substance_key="entity.alpha",
                names=[_name("Shared Printed Name", preferred=True)],
            )
            weak = await _published(
                session, substance_key="entity.beta",
                names=[_name("Shared Printed Name", preferred=True)],
            )
        async with factory() as session:
            claim = await session.get(EvidenceClaim, weak)
            claim.evidence_strength = EvidenceStrength.LIMITED.value
            await session.commit()
        assert strong != weak
        async with factory() as session:
            assert (await resolve_name(session, "Shared Printed Name")).status is ResolutionStatus.AMBIGUOUS

    async def test_ambiguity_is_not_broken_by_entity_kind(self, db_clean):
        """A group and a molecule sharing a name is exactly the case to refuse."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="family.thing", entity_kind=EntityKind.GROUP.value,
                names=[_name("Overloaded Term", preferred=True)],
            )
            await _published(
                session, substance_key="molecule.thing",
                entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
                names=[_name("Overloaded Term", preferred=True)],
            )
        async with factory() as session:
            result = await resolve_name(session, "Overloaded Term")
        assert result.status is ResolutionStatus.AMBIGUOUS
        assert result.entity_kind is None

    async def test_an_ineligible_second_entity_does_not_create_ambiguity(self, db_clean):
        """One published, one draft: a clean RESOLVED, not a hedge."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="entity.alpha",
                names=[_name("Shared Printed Name", preferred=True)],
            )
            await _draft(
                session, substance_key="entity.beta",
                names=[_name("Shared Printed Name", preferred=True)],
            )
            await session.commit()
        async with factory() as session:
            result = await resolve_name(session, "Shared Printed Name")
        assert result.status is ResolutionStatus.RESOLVED
        assert result.substance_key == "entity.alpha"


# ---------------------------------------------------------------------------
# E. Multiple eligible paths to one identity are one answer
# ---------------------------------------------------------------------------
class TestMultiplePathsSameIdentity:
    async def test_two_published_claims_for_one_entity_resolve_once(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
                source_url="https://example.org/reference/one",
            )
            await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
                source_type=SourceType.OFFICIAL_REGULATION.value,
                source_url="https://example.gov/register/entry",
            )
        async with factory() as session:
            result = await resolve_name(session, "Niacinamide")
        assert result.status is ResolutionStatus.RESOLVED
        assert result.substance_key == "niacinamide"
        assert result.candidate_substance_keys == ("niacinamide",)

    async def test_the_same_name_in_two_namespaces_is_one_entity(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="niacinamide",
                names=[
                    _name("Niacinamide", namespace=NameNamespace.INCI.value, preferred=True),
                    _name("Niacinamide", namespace=NameNamespace.COMMON.value),
                ],
            )
        async with factory() as session:
            result = await resolve_name(session, "niacinamide")
        assert result.status is ResolutionStatus.RESOLVED


# ---------------------------------------------------------------------------
# F. An invalid row can only ever cost itself (the Step 6B LIMIT-1 defect)
# ---------------------------------------------------------------------------
class TestInvalidRowCannotHideValidRow:
    async def test_a_newer_ineligible_row_does_not_hide_an_eligible_one(self, db_clean):
        """Narrow-then-validate would return UNRESOLVED here. Validate-then-narrow does not."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
            )
            # A later draft for a different entity under the same printed name.
            await _draft(
                session, substance_key="decoy.entity",
                names=[_name("Niacinamide", preferred=True)],
            )
            await session.commit()
        async with factory() as session:
            result = await resolve_name(session, "Niacinamide")
        assert result.status is ResolutionStatus.RESOLVED
        assert result.substance_key == "niacinamide"

    async def test_many_ineligible_rows_do_not_hide_one_eligible_row(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            for index in range(8):
                await _draft(
                    session, substance_key=f"decoy.{index}",
                    names=[_name("Crowded Name", preferred=True)],
                )
            await session.commit()
            await _published(
                session, substance_key="real.entity",
                names=[_name("Crowded Name", preferred=True)],
            )
        async with factory() as session:
            result = await resolve_name(session, "Crowded Name")
        assert result.status is ResolutionStatus.RESOLVED
        assert result.substance_key == "real.entity"

    async def test_a_name_row_the_claim_no_longer_records_is_ignored(self, db_clean):
        """Manual drift between the index and the evidence fails closed."""
        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
            )
        async with factory() as session:
            substance = (await session.execute(select(Substance))).scalars().one()
            session.add(SubstanceName(
                substance_id=substance.id, identity_claim_id=claim_id,
                name="Smuggled Name", normalized_name="smuggled name",
                namespace=NameNamespace.INCI.value, language_tag="und", is_preferred=False,
            ))
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Smuggled Name")).status is ResolutionStatus.UNRESOLVED
            assert (await resolve_name(session, "Niacinamide")).status is ResolutionStatus.RESOLVED

    async def test_a_name_row_pointed_at_another_substances_claim_is_ignored(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
            )
            await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session:
            glycerin = (await session.execute(
                select(Substance).where(Substance.substance_key == "glycerin")
            )).scalars().one()
            # Glycerin's entity, niacinamide's claim: agreement fails on subject_key.
            session.add(SubstanceName(
                substance_id=glycerin.id, identity_claim_id=claim_id,
                name="Borrowed Name", normalized_name="borrowed name",
                namespace=NameNamespace.INCI.value, language_tag="und", is_preferred=False,
            ))
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Borrowed Name")).status is ResolutionStatus.UNRESOLVED

    async def test_a_drifted_entity_kind_fails_closed(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
            )
        async with factory() as session:
            substance = (await session.execute(select(Substance))).scalars().one()
            substance.entity_kind = EntityKind.GROUP.value
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Niacinamide")).status is ResolutionStatus.UNRESOLVED

    async def test_a_corrupted_payload_fails_closed(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
            )
        async with factory() as session:
            claim = await session.get(EvidenceClaim, claim_id)
            value = dict(claim.structured_value)
            payload = dict(value["substance_identity"])
            payload["schema_version"] = "substance-identity.v99"
            value["substance_identity"] = payload
            claim.structured_value = value
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Niacinamide")).status is ResolutionStatus.UNRESOLVED


# ---------------------------------------------------------------------------
# G. Legacy alias isolation — the old Care family map was NOT copied
# ---------------------------------------------------------------------------
class TestTheSourceMustBeNamed:
    """A citation with no title and no publisher is not provenance."""

    @pytest.mark.parametrize("blank", ["", "   ", "\t", "\n  "])
    async def test_authoring_refuses_a_blank_source_title(self, db_clean, blank):
        factory = get_sessionmaker()
        async with factory() as session:
            with pytest.raises(ValidationFailedError) as caught:
                await substance_authoring.create_identity_draft(
                    session, substance_key="niacinamide",
                    entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
                    names=[_name("Niacinamide", preferred=True)],
                    summary="Names.", scope="Nomenclature only.",
                    evidence_strength=EvidenceStrength.STRONG.value,
                    strength_rationale="A named reference work records this.",
                    source_title=blank, source_publisher="Example Reference",
                    source_type=SourceType.INGREDIENT_REFERENCE_DATABASE.value,
                    source_url="https://example.org/reference/entry",
                    license_or_use_note="Reproduced under the publisher's terms.",
                    author="tester",
                )
        assert caught.value.extra["field"] == "source_title"

    @pytest.mark.parametrize("blank", ["", "   ", "\t", "\n  "])
    async def test_authoring_refuses_a_blank_source_publisher(self, db_clean, blank):
        factory = get_sessionmaker()
        async with factory() as session:
            with pytest.raises(ValidationFailedError) as caught:
                await substance_authoring.create_identity_draft(
                    session, substance_key="niacinamide",
                    entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
                    names=[_name("Niacinamide", preferred=True)],
                    summary="Names.", scope="Nomenclature only.",
                    evidence_strength=EvidenceStrength.STRONG.value,
                    strength_rationale="A named reference work records this.",
                    source_title="Reference entry", source_publisher=blank,
                    source_type=SourceType.INGREDIENT_REFERENCE_DATABASE.value,
                    source_url="https://example.org/reference/entry",
                    license_or_use_note="Reproduced under the publisher's terms.",
                    author="tester",
                )
        assert caught.value.extra["field"] == "source_publisher"

    async def test_authoring_stores_the_stripped_title_and_publisher(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await substance_authoring.create_identity_draft(
                session, substance_key="niacinamide",
                entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
                names=[_name("Niacinamide", preferred=True)],
                summary="Names.", scope="Nomenclature only.",
                evidence_strength=EvidenceStrength.STRONG.value,
                strength_rationale="A named reference work records this.",
                source_title="  Reference entry  ",
                source_publisher="  Example Reference  ",
                source_type=SourceType.INGREDIENT_REFERENCE_DATABASE.value,
                source_url="  https://example.org/reference/entry  ",
                license_or_use_note="Reproduced under the publisher's terms.",
                author="tester",
            )
            await session.commit()
        async with factory() as session:
            source = (await session.execute(select(EvidenceSource))).scalars().one()
        assert source.title == "Reference entry"
        assert source.publisher == "Example Reference"
        assert source.canonical_url == "https://example.org/reference/entry"

    @pytest.mark.parametrize("field", ["title", "publisher"])
    @pytest.mark.parametrize("blank", ["", "   "])
    async def test_blanking_the_source_afterwards_unresolves_the_name(
        self, db_clean, field, blank,
    ):
        """A reader must not depend on the writer having been careful.

        The row was authored correctly and blanked later — by a hand edit, a bad
        import, or anything else. Read-time eligibility has to notice.
        """
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
            )
        async with factory() as session:
            assert (await resolve_name(session, "Niacinamide")).status is ResolutionStatus.RESOLVED

        async with factory() as session:
            source = (await session.execute(select(EvidenceSource))).scalars().one()
            setattr(source, field, blank)
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Niacinamide")).status is ResolutionStatus.UNRESOLVED


class TestOpenableSourceUrl:
    """One validator, and it parses rather than sniffs a prefix."""

    @pytest.mark.parametrize("bad", [
        "", "   ",
        "example.org",                      # no scheme
        "ftp://example.org/x",              # wrong scheme
        "javascript:alert(1)",              # not a citation at all
        "https://", "http://",              # scheme and nothing else
        "https://?x=1",                     # query, still no host
        "https:///path",                    # path, still no host
        "https://exa mple.org/a",           # embedded space
        "https://exa\tmple.org/a",          # embedded tab
        "https://exa\nmple.org/a",          # embedded newline
        "https://example.org/\x00a",        # embedded NUL
        "http://[::1/x",                    # malformed IPv6 authority
        "http://example.org:notaport/x",    # malformed port
    ])
    def test_a_string_that_is_not_an_openable_url_is_refused(self, bad):
        assert openable_url(bad) is None

    @pytest.mark.parametrize("good", [
        "https://example.org",
        "http://example.org/a?b=1#c",
        "https://example.org:8443/entry",
        "https://sub.example.co.in/reference/entry",
        "http://[::1]:8080/x",
    ])
    def test_a_normal_http_url_still_passes(self, good):
        assert openable_url(good) == good

    def test_a_non_string_is_refused(self):
        for value in (None, 123, [], {}, True):
            assert openable_url(value) is None

    @pytest.mark.parametrize("bad", ["https://", "https://?x=1", "https:///path", "example.org"])
    async def test_authoring_refuses_an_unopenable_source_url(self, db_clean, bad):
        factory = get_sessionmaker()
        async with factory() as session:
            with pytest.raises(ValidationFailedError) as caught:
                await _draft(
                    session, substance_key="niacinamide",
                    names=[_name("Niacinamide", preferred=True)], source_url=bad,
                )
        assert caught.value.extra["field"] == "source_url"

    @pytest.mark.parametrize("bad", ["https://", "https://?x=1", "https:///path"])
    async def test_breaking_the_url_afterwards_unresolves_the_name(self, db_clean, bad):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
            )
        async with factory() as session:
            source = (await session.execute(select(EvidenceSource))).scalars().one()
            source.canonical_url = bad
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Niacinamide")).status is ResolutionStatus.UNRESOLVED

    def test_there_is_only_one_url_validator(self):
        """The authoring helper delegates rather than keeping a second opinion."""
        body = inspect.getsource(evidence_authoring._valid_url)
        assert "openable_url" in body
        assert "startswith" not in body


class TestEveryMaterialisedFieldMustAgree:
    """The index is only as trustworthy as its agreement with the evidence."""

    async def test_editing_the_displayed_name_alone_unresolves_it(self, db_clean):
        """The normalised form is untouched; the spelling a shopper sees is not.

        This is the drift a normalised-only comparison cannot see: the row still
        answers the same lookup key while displaying a name no reviewer published.
        """
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
            )
        async with factory() as session:
            row = (await session.execute(select(SubstanceName))).scalars().one()
            row.name = "Niacinamide (edited)"          # normalized_name left alone
            await session.commit()
        async with factory() as session:
            row = (await session.execute(select(SubstanceName))).scalars().one()
            assert row.normalized_name == "niacinamide"
            assert (await resolve_name(session, "Niacinamide")).status is ResolutionStatus.UNRESOLVED

    async def test_editing_the_language_tag_unresolves_it(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
            )
        async with factory() as session:
            row = (await session.execute(select(SubstanceName))).scalars().one()
            row.language_tag = "en"
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Niacinamide")).status is ResolutionStatus.UNRESOLVED

    async def test_editing_the_namespace_unresolves_it(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
            )
        async with factory() as session:
            row = (await session.execute(select(SubstanceName))).scalars().one()
            row.namespace = NameNamespace.COMMON.value
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Niacinamide")).status is ResolutionStatus.UNRESOLVED

    async def test_editing_the_preferred_flag_unresolves_it(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
            )
        async with factory() as session:
            row = (await session.execute(select(SubstanceName))).scalars().one()
            row.is_preferred = False
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Niacinamide")).status is ResolutionStatus.UNRESOLVED

    def test_the_comparison_reads_every_materialised_field(self):
        """Named explicitly, so adding a column without comparing it is visible."""
        body = inspect.getsource(substance_service._name_row_agrees_with_claim)
        for field in ("name.name ==", "normalized_name", "namespace",
                      "language_tag", "is_preferred"):
            assert field in body, field


class TestInsufficientEvidenceEstablishesNothing:
    """A grade of "insufficient" is the reviewer saying the evidence is not there."""

    async def test_an_insufficient_claim_does_not_establish_identity(self, db_clean):
        """Everything else about this path is impeccable, and it still resolves nothing."""
        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await substance_authoring.create_identity_draft(
                session, substance_key="niacinamide",
                entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
                names=[_name("Niacinamide", preferred=True)],
                summary="Names.", scope="Nomenclature only.",
                evidence_strength=EvidenceStrength.INSUFFICIENT.value,
                strength_rationale="The source does not actually settle the nomenclature.",
                source_title="Reference entry", source_publisher="Example Reference",
                source_type=SourceType.INGREDIENT_REFERENCE_DATABASE.value,
                source_url="https://example.org/reference/entry",
                license_or_use_note="Reproduced under the publisher's terms.",
                author="tester",
            )
            await _publish(session, uuid.UUID(claim_id["claim_id"]))
            await session.commit()

        async with factory() as session:
            claim = (await session.execute(select(EvidenceClaim))).scalars().one()
            # The path really is otherwise complete — this is not a published
            # claim failing some other gate.
            assert claim.review_status == ReviewStatus.PUBLISHED.value
            assert claim.claim_status == ClaimStatus.SUPPORTED.value
            assert claim.evidence_strength == EvidenceStrength.INSUFFICIENT.value
            assert claim_is_public_knowledge_path(claim) is True
            assert (await resolve_name(session, "Niacinamide")).status is ResolutionStatus.UNRESOLVED

    @pytest.mark.parametrize("strength", [
        EvidenceStrength.STRONG.value,
        EvidenceStrength.MODERATE.value,
        EvidenceStrength.LIMITED.value,
        EvidenceStrength.TRADITIONAL.value,
    ])
    async def test_every_other_graded_strength_still_establishes_identity(
        self, db_clean, strength,
    ):
        factory = get_sessionmaker()
        async with factory() as session:
            result = await substance_authoring.create_identity_draft(
                session, substance_key="niacinamide",
                entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
                names=[_name("Niacinamide", preferred=True)],
                summary="Names.", scope="Nomenclature only.",
                evidence_strength=strength,
                strength_rationale="A named reference work records this nomenclature.",
                source_title="Reference entry", source_publisher="Example Reference",
                source_type=SourceType.INGREDIENT_REFERENCE_DATABASE.value,
                source_url="https://example.org/reference/entry",
                license_or_use_note="Reproduced under the publisher's terms.",
                author="tester",
            )
            await _publish(session, uuid.UUID(result["claim_id"]))
            await session.commit()
        async with factory() as session:
            assert (await resolve_name(session, "Niacinamide")).status is ResolutionStatus.RESOLVED

    def test_the_identity_strength_set_is_narrower_than_the_global_one(self):
        """Step 7A tightens its own reading; it does not move the shared vocabulary."""
        assert EvidenceStrength.INSUFFICIENT.value not in IDENTITY_EVIDENCE_STRENGTHS
        assert EvidenceStrength.INSUFFICIENT.value in set(EVIDENCE_STRENGTHS)
        assert set(EVIDENCE_STRENGTHS) > IDENTITY_EVIDENCE_STRENGTHS


class TestLegacyAliasIsolation:
    async def test_no_substance_row_exists_without_authoring(self, db_clean):
        """No production seed. Zero canonical names ship until a reviewer writes one."""
        factory = get_sessionmaker()
        async with factory() as session:
            assert (await session.execute(select(Substance))).scalars().all() == []
            assert (await session.execute(select(SubstanceName))).scalars().all() == []

    async def test_reference_data_seed_writes_no_substances(self, db_clean):
        from app.bootstrap import run as seed_reference_data

        factory = get_sessionmaker()
        async with factory() as session:
            await seed_reference_data(session)
            await session.commit()
        async with factory() as session:
            assert (await session.execute(select(Substance))).scalars().all() == []
            assert (await session.execute(select(SubstanceName))).scalars().all() == []

    @pytest.mark.parametrize("legacy_alias", [
        # Four different esters, families and materials that the Care ontology
        # collapses onto one key each. None of them may resolve here.
        "Tocopheryl Acetate", "Tocopherol", "Vitamin E",
        "Ceramide NP", "Ceramide AP", "Ceramide EOP", "Ceramides",
        "Hydrolyzed Wheat Protein", "Hydrolyzed Keratin", "Hydrolyzed Silk", "Keratin",
        "Peppermint Oil", "Menthol",
        "Vitamin B3", "Nicotinamide", "Glycerol", "Glycerine", "Provitamin B5", "Carbamide",
    ])
    async def test_a_legacy_care_alias_resolves_to_nothing(self, db_clean, legacy_alias):
        factory = get_sessionmaker()
        async with factory() as session:
            assert (await resolve_name(session, legacy_alias)).status is ResolutionStatus.UNRESOLVED

    async def test_publishing_one_ester_does_not_publish_the_family(self, db_clean):
        """Tocopheryl acetate is an ester of tocopherol. They are not one entity."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="tocopheryl.acetate",
                names=[_name("Tocopheryl Acetate", preferred=True)],
            )
        async with factory() as session:
            assert (await resolve_name(session, "Tocopheryl Acetate")).status is ResolutionStatus.RESOLVED
            for other in ("Tocopherol", "Vitamin E", "Tocopheryl Linoleate"):
                assert (await resolve_name(session, other)).status is ResolutionStatus.UNRESOLVED

    async def test_publishing_a_group_does_not_publish_its_members(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="ceramides.group", entity_kind=EntityKind.GROUP.value,
                names=[_name("Ceramides", preferred=True)],
            )
        async with factory() as session:
            group = await resolve_name(session, "Ceramides")
            assert group.status is ResolutionStatus.RESOLVED
            assert group.entity_kind == EntityKind.GROUP.value
            for member in ("Ceramide NP", "Ceramide AP", "Ceramide EOP", "Ceramide"):
                assert (await resolve_name(session, member)).status is ResolutionStatus.UNRESOLVED

    async def test_no_marketing_namespace_exists(self):
        assert "marketing" not in {n.value for n in NameNamespace}

    def test_substances_domain_never_imports_the_legacy_ontology(self):
        """Structural, not behavioural: the alias table is not reachable from here."""
        for path in sorted(SUBSTANCES_DIR.glob("*.py")):
            body = _code_only(path.read_text(encoding="utf-8"))
            for forbidden in (
                "routines.ontology", "routines.parser", "routines import ontology",
                "routines import parser", "INGREDIENT_BY_KEY", "INGREDIENTS",
                "IngredientAlias", "ingredient_aliases", "_match_in", "parse_label",
                "parse_declared", "FAMILY_",
            ):
                assert forbidden not in body, f"{path.name} references {forbidden}"

    def test_no_legacy_alias_backfill_migration_exists(self):
        migration = (
            BACKEND_ROOT / "migrations" / "versions" / "z6a7b8c9d0_step7a_substance_identity.py"
        ).read_text(encoding="utf-8")
        for forbidden in ("ingredient_aliases", "INSERT INTO substances", "insert into substances",
                          "INSERT INTO substance_names", "bulk_insert"):
            assert forbidden not in migration, forbidden


# ---------------------------------------------------------------------------
# G2. Anti-sprawl — what this domain is allowed to depend on, by AST
# ---------------------------------------------------------------------------
def _package_of(path: Path) -> str:
    """The dotted package a module file lives in, e.g. ``app.domains.substances``."""
    parts = path.relative_to(BACKEND_ROOT).parts
    return ".".join(parts[:-1])


def _imported_modules(path: Path) -> set[str]:
    """Every module name this file imports, read from the syntax tree.

    A text scan can be defeated by a line break or a string; the tree cannot.
    A relative import is resolved against *this file's own* package, so
    ``from .models import Substance`` inside the substances package is reported
    as ``app.domains.substances.models`` and the same line in another package is
    reported under that package instead.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _package_of(path).split(".")
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                prefix = ".".join([*base, node.module] if node.module else base)
            else:
                prefix = node.module or ""
            if not prefix:
                continue
            found.add(prefix)
            found.update(f"{prefix}.{alias.name}" for alias in node.names)
    return found


#: Everything outside its own package that the substances domain may import.
#: A new entry here is a deliberate widening of what identity is allowed to
#: know about, and should be argued for rather than added in passing.
ALLOWED_IMPORT_PREFIXES = (
    "app.domains.substances",
    "app.domains.evidence",
    "app.shared.database",
    "app.shared.errors",
    "sqlalchemy",
    "dataclasses",
    "collections",
    "enum",
    "typing",
    "unicodedata",
    "uuid",
    "__future__",
)


class TestAntiSprawl:
    @pytest.mark.parametrize("path", sorted(SUBSTANCES_DIR.glob("*.py")), ids=lambda p: p.name)
    def test_the_domain_imports_only_what_identity_needs(self, path):
        for module in sorted(_imported_modules(path)):
            assert module.startswith(ALLOWED_IMPORT_PREFIXES), f"{path.name} imports {module}"

    @pytest.mark.parametrize("path", sorted(SUBSTANCES_DIR.glob("*.py")), ids=lambda p: p.name)
    def test_the_domain_imports_none_of_the_forbidden_neighbours(self, path):
        """Named one by one, so a failure says which boundary was crossed.

        Each of these would pull an *interpretation* into a layer whose whole
        contract is that it makes none: what the AI said, what Open Food Facts
        published, what a supplement does, which Care family a name falls in,
        or what a retailer is selling.
        """
        imported = _imported_modules(path)
        for forbidden in (
            "app.domains.ai_gateway",
            "app.domains.off",
            "app.domains.supplements",
            "app.domains.nutrition",
            "app.domains.routines",
            "app.domains.care",
            "app.domains.product",
            "app.domains.alternatives",
            "app.domains.recommendation",
            "app.domains.purchase",
            "httpx",
            "requests",
            "aiohttp",
            "urllib",
            "urllib.request",
            "socket",
            "google.genai",
        ):
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in imported
            ), f"{path.name} imports {forbidden}"

    def test_nothing_outside_the_domain_depends_on_it_yet(self):
        """Step 7A ships the layer, wired to nothing. Nothing else may change."""
        app_root = BACKEND_ROOT / "app"
        for path in sorted(app_root.rglob("*.py")):
            if SUBSTANCES_DIR in path.parents:
                continue
            imported = _imported_modules(path)
            if path == BACKEND_ROOT / "app" / "shared" / "database" / "registry.py":
                # The one permitted reference: Alembic cannot see a model that
                # is not imported here, and its table would silently never exist.
                assert any(m.startswith("app.domains.substances") for m in imported)
                continue
            assert not any(
                m.startswith("app.domains.substances") for m in imported
            ), f"{path.relative_to(BACKEND_ROOT)} imports the substances domain"

    def test_the_domain_owns_exactly_these_modules(self):
        """A guard against the layer quietly growing a workflow of its own."""
        assert {path.name for path in SUBSTANCES_DIR.glob("*.py")} == {
            "__init__.py", "authoring.py", "enums.py", "identity_schema.py",
            "models.py", "normalization.py", "service.py",
        }


# ---------------------------------------------------------------------------
# H. No AI, and no network
# ---------------------------------------------------------------------------
class TestNoAIAndNoNetwork:
    @pytest.mark.parametrize("path", sorted(SUBSTANCES_DIR.glob("*.py")), ids=lambda p: p.name)
    def test_no_ai_or_network_reference_anywhere_in_the_domain(self, path):
        body = _code_only(path.read_text(encoding="utf-8"))
        for forbidden in (
            "ai_gateway", "google.genai", "google_genai", "genai", "gemini", "Gemini",
            "httpx", "requests", "aiohttp", "urllib", "socket", "openai",
            "embedding", "cosine", "levenshtein", "difflib", "SequenceMatcher",
            "fuzzy", "rapidfuzz", "get_close_matches",
        ):
            assert forbidden not in body, f"{path.name} references {forbidden}"

    async def test_resolution_records_no_ai_run(self, db_clean):
        from app.domains.ai_gateway.models import AIRun

        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session:
            await resolve_names(session, ["Glycerin", "Niacinamide", "unknown thing"])
        async with factory() as session:
            assert (await session.execute(select(AIRun))).scalars().all() == []

    async def test_authoring_records_no_ai_run(self, db_clean):
        from app.domains.ai_gateway.models import AIRun

        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session:
            assert (await session.execute(select(AIRun))).scalars().all() == []
            claim = (await session.execute(select(EvidenceClaim))).scalars().one()
            assert claim.ai_generated is False


# ---------------------------------------------------------------------------
# I. Bounded work — constant queries whatever the batch size
# ---------------------------------------------------------------------------
class TestBoundedQueries:
    async def test_resolution_issues_a_constant_number_of_queries(self, db_clean):
        from sqlalchemy import event

        factory = get_sessionmaker()
        async with factory() as session:
            for index in range(12):
                await _published(
                    session, substance_key=f"entity.{index}",
                    names=[_name(f"Substance Number {index}", preferred=True)],
                )

        statements: list[str] = []

        def _record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        sync_engine = sql.get_engine().sync_engine
        async with factory() as session:
            event.listen(sync_engine, "before_cursor_execute", _record)
            try:
                statements.clear()
                one = await resolve_names(session, ["Substance Number 0"])
                after_one = len(statements)
                statements.clear()
                many = await resolve_names(
                    session, [f"Substance Number {i}" for i in range(12)],
                )
                after_many = len(statements)
            finally:
                event.remove(sync_engine, "before_cursor_execute", _record)

        assert all(r.status is ResolutionStatus.RESOLVED for r in one + many)
        # Two reads for one name, and the identical two for twelve. No per-name loop.
        assert after_one == 2, statements
        assert after_many == after_one, (after_one, after_many)

    async def test_no_result_needs_only_the_first_query(self, db_clean):
        from sqlalchemy import event

        factory = get_sessionmaker()
        statements: list[str] = []

        def _record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        sync_engine = sql.get_engine().sync_engine
        async with factory() as session:
            event.listen(sync_engine, "before_cursor_execute", _record)
            try:
                statements.clear()
                await resolve_names(session, ["nothing here", "nor here"])
            finally:
                event.remove(sync_engine, "before_cursor_execute", _record)
        assert len(statements) == 1, statements

    async def test_all_unusable_names_need_no_query_at_all(self, db_clean):
        from sqlalchemy import event

        factory = get_sessionmaker()
        statements: list[str] = []

        def _record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        sync_engine = sql.get_engine().sync_engine
        async with factory() as session:
            event.listen(sync_engine, "before_cursor_execute", _record)
            try:
                statements.clear()
                results = await resolve_names(session, ["", "   ", "\t"])
            finally:
                event.remove(sync_engine, "before_cursor_execute", _record)
        assert all(r.status is ResolutionStatus.UNRESOLVED for r in results)
        assert statements == []

    async def test_the_lookup_index_is_used_for_the_normalized_key(self, db_clean):
        """The resolver's entry point is an index seek, not a table scan."""
        factory = get_sessionmaker()
        async with factory() as session:
            # Turn the planner's preference for a seq scan off so the plan shows
            # whether an index *exists and is usable*, which is what is at stake.
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            plan = "\n".join(row[0] for row in (await session.execute(text(
                "EXPLAIN SELECT id FROM substance_names WHERE normalized_name = 'niacinamide'"
            ))).all())
        assert "ix_substance_names_normalized" in plan, plan

    def test_the_expected_indexes_are_declared(self):
        indexes = {index.name for index in SubstanceName.__table__.indexes}
        assert {
            "ix_substance_names_normalized",
            "ix_substance_names_substance",
            "ix_substance_names_claim",
        } <= indexes

    async def test_normalized_name_is_not_globally_unique(self, db_clean):
        """A unique index would make the database break ambiguity by insert order."""
        factory = get_sessionmaker()
        async with factory() as session:
            rows = (await session.execute(text(
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'substance_names'"
            ))).scalars().all()
        for definition in rows:
            if "normalized_name" in definition and "UNIQUE" in definition.upper():
                # Only the four-column bookkeeping constraint may be unique.
                assert "identity_claim_id" in definition and "substance_id" in definition, definition


# ---------------------------------------------------------------------------
# J. The ODbL wall — Store B only
# ---------------------------------------------------------------------------
class TestOdblWall:
    def test_substance_tables_are_store_b(self):
        from app.domains.off.models import OFF_SCHEMA, OffBase
        from app.shared.database.registry import Base

        assert Substance.__table__.metadata is Base.metadata
        assert SubstanceName.__table__.metadata is Base.metadata
        assert Substance.__table__.metadata is not OffBase.metadata
        assert SubstanceName.__table__.metadata is not OffBase.metadata
        # And neither table is in Store A's schema, under any name.
        assert Substance.__table__.schema != OFF_SCHEMA
        assert SubstanceName.__table__.schema != OFF_SCHEMA
        assert not any(
            name.endswith(("substances", "substance_names")) for name in OffBase.metadata.tables
        )

    def test_no_off_product_field_names_a_substance(self):
        from app.domains.off.models import OffProduct

        columns = {c.name for c in OffProduct.__table__.columns}
        for forbidden in ("substance_id", "substance_key", "substance", "identity_claim_id"):
            assert forbidden not in columns, forbidden

    @pytest.mark.parametrize("path", sorted(SUBSTANCES_DIR.glob("*.py")), ids=lambda p: p.name)
    def test_the_substances_domain_never_touches_store_a(self, path):
        body = _code_only(path.read_text(encoding="utf-8"))
        for forbidden in (
            "domains.off", "domains import off", "OffProduct", "OffBase",
            "OFF_DATABASE_URL", "OFF_SCHEMA", "off_session", "get_off_engine", "nutriments",
        ):
            assert forbidden not in body, f"{path.name} references {forbidden}"

    def test_no_foreign_key_crosses_into_off(self):
        for table in (Substance.__table__, SubstanceName.__table__):
            for fk in table.foreign_keys:
                assert not fk.target_fullname.startswith("off_"), fk.target_fullname


# ---------------------------------------------------------------------------
# K. Global data — identity is the same fact for everybody
# ---------------------------------------------------------------------------
class TestGlobalData:
    @pytest.mark.parametrize("model", [Substance, SubstanceName], ids=["substances", "substance_names"])
    def test_no_per_person_column(self, model):
        columns = {c.name for c in model.__table__.columns}
        for forbidden in ("account_id", "device_id", "user_id", "profile_id", "household_id"):
            assert forbidden not in columns, forbidden

    @pytest.mark.parametrize("model", [Substance, SubstanceName], ids=["substances", "substance_names"])
    def test_no_interpretation_column(self, model):
        """Identity only. Every one of these is a claim in a context, not a fact here."""
        columns = {c.name for c in model.__table__.columns}
        for forbidden in (
            "family", "function", "common_use", "benefit", "risk", "risk_tier", "safety",
            "safe", "unsafe", "efficacy", "regulatory_status", "concentration", "percentage",
            "dose", "exposure", "absorption", "interaction", "verdict", "grade", "score",
        ):
            assert not any(forbidden in column for column in columns), (forbidden, columns)

    def test_substance_names_carry_no_review_state_of_their_own(self):
        """The claim owns review state. A second copy would drift out of agreement."""
        columns = {c.name for c in SubstanceName.__table__.columns}
        for forbidden in ("review_status", "verified", "approved", "published", "reviewed_by"):
            assert forbidden not in columns, forbidden

    def test_no_source_url_lives_on_a_substance_row(self):
        """Provenance stays in the evidence domain, which is its only authority."""
        for model in (Substance, SubstanceName):
            columns = {c.name for c in model.__table__.columns}
            for forbidden in ("source_url", "canonical_url", "citation", "reference_url",
                              "publisher", "license_or_use_note"):
                assert forbidden not in columns, (model.__tablename__, forbidden)

    def test_no_separate_substance_sources_table(self):
        from app.shared.database.registry import Base

        assert "substance_sources" not in Base.metadata.tables

    def test_neither_table_is_user_owned_for_privacy(self):
        """A person's export must not hand them the reference catalogue.

        Nothing here belongs to anybody: what a name denotes does not change
        when an account is deleted, and there is no account column to key it on.
        """
        from app.domains.privacy import REGISTRY, Classification

        assert REGISTRY["substances"] is Classification.NOT_USER_OWNED
        assert REGISTRY["substance_names"] is Classification.NOT_USER_OWNED

    def test_resolution_takes_no_account_or_device(self):
        parameters = set(inspect.signature(resolve_names).parameters)
        assert parameters == {"session", "names"}
        assert set(inspect.signature(resolve_name).parameters) == {"session", "name"}

    async def test_the_same_name_resolves_identically_for_everybody(self, db_clean):
        """There is no caller context to vary, and the answer proves it."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session_a, factory() as session_b:
            first = await resolve_name(session_a, "Glycerin")
            second = await resolve_name(session_b, "Glycerin")
        assert first.substance_key == second.substance_key == "glycerin"
        assert first.status == second.status


# ---------------------------------------------------------------------------
# L. The schema and its strict parser
# ---------------------------------------------------------------------------
class TestIdentitySchema:
    def test_version_is_pinned(self):
        assert SUBSTANCE_IDENTITY_SCHEMA_VERSION == "substance-identity.v1"

    def test_a_valid_payload_round_trips(self):
        payload = build_identity_payload(
            entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
            names=[_name("Niacinamide", preferred=True), _name("Nicotinamide")],
        )
        identity = parse_identity(payload)
        assert identity is not None
        assert identity.entity_kind == EntityKind.DEFINED_SUBSTANCE.value
        assert [n.normalized_name for n in identity.names] == ["niacinamide", "nicotinamide"]
        assert identity.preferred.name == "Niacinamide"

    def test_the_payload_coexists_with_publication_verification(self):
        payload = build_identity_payload(
            entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
            names=[_name("Niacinamide", preferred=True)],
        )
        payload["publication_verification"] = {"source_opened": True}
        assert parse_identity(payload) is not None

    @pytest.mark.parametrize("bad", [
        None, [], "", 0, {"substance_identity": None}, {"substance_identity": []},
        {}, {"other": {}},
    ])
    def test_a_non_payload_parses_to_none(self, bad):
        assert parse_identity(bad) is None

    def test_an_unknown_schema_version_parses_to_none(self):
        payload = build_identity_payload(
            entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
            names=[_name("Niacinamide", preferred=True)],
        )
        payload["substance_identity"]["schema_version"] = "substance-identity.v2"
        assert parse_identity(payload) is None

    def test_an_unknown_key_inside_the_payload_parses_to_none(self):
        payload = build_identity_payload(
            entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
            names=[_name("Niacinamide", preferred=True)],
        )
        payload["substance_identity"]["risk_tier"] = "low"
        assert parse_identity(payload) is None

    def test_an_unknown_key_inside_a_name_parses_to_none(self):
        payload = build_identity_payload(
            entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
            names=[_name("Niacinamide", preferred=True)],
        )
        payload["substance_identity"]["names"][0]["concentration"] = "5%"
        assert parse_identity(payload) is None

    @pytest.mark.parametrize("names", [
        [],
        [_name("Niacinamide")],                                          # none preferred
        [_name("A", preferred=True), _name("B", preferred=True)],        # two preferred
        [_name("Niacinamide", preferred=True), _name("niacinamide")],    # same key twice
        [{"name": "", "namespace": "inci", "is_preferred": True}],
        [{"name": "   ", "namespace": "inci", "is_preferred": True}],
        [{"name": "X", "namespace": "marketing", "is_preferred": True}],
        [{"name": "X", "namespace": "inci", "is_preferred": "yes"}],
        [{"name": 7, "namespace": "inci", "is_preferred": True}],
        ["Niacinamide"],
        [None],
    ])
    def test_a_malformed_name_set_parses_to_none(self, names):
        assert parse_identity({
            "substance_identity": {
                "schema_version": SUBSTANCE_IDENTITY_SCHEMA_VERSION,
                "entity_kind": EntityKind.DEFINED_SUBSTANCE.value,
                "names": names,
            },
        }) is None

    def test_an_unknown_entity_kind_parses_to_none(self):
        assert parse_identity({
            "substance_identity": {
                "schema_version": SUBSTANCE_IDENTITY_SCHEMA_VERSION,
                "entity_kind": "chemical",
                "names": [_name("X", preferred=True)],
            },
        }) is None

    def test_too_many_names_parses_to_none(self):
        names = [_name(f"Name {i}") for i in range(MAX_NAMES_PER_CLAIM + 1)]
        names[0]["is_preferred"] = True
        assert parse_identity({
            "substance_identity": {
                "schema_version": SUBSTANCE_IDENTITY_SCHEMA_VERSION,
                "entity_kind": EntityKind.DEFINED_SUBSTANCE.value,
                "names": names,
            },
        }) is None

    def test_parsing_is_never_partial(self):
        """One unreadable name discards the whole claim, not just that name."""
        assert parse_identity({
            "substance_identity": {
                "schema_version": SUBSTANCE_IDENTITY_SCHEMA_VERSION,
                "entity_kind": EntityKind.DEFINED_SUBSTANCE.value,
                "names": [_name("Niacinamide", preferred=True), {"name": "X", "namespace": "bad"}],
            },
        }) is None

    def test_building_an_invalid_payload_raises(self):
        with pytest.raises(ValueError):
            build_identity_payload(entity_kind="chemical", names=[_name("X", preferred=True)])

    def test_the_lookup_key_is_never_taken_from_the_caller(self):
        payload = build_identity_payload(
            entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
            names=[_name("  Niacinamide  ", preferred=True)],
        )
        identity = parse_identity(payload)
        assert identity is not None
        assert identity.names[0].normalized_name == "niacinamide"
        assert identity.names[0].name == "  Niacinamide  "

    def test_the_schema_names_no_interpretation_field(self):
        body = _code_only((SUBSTANCES_DIR / "identity_schema.py").read_text(encoding="utf-8"))
        for forbidden in ('"risk"', '"safety"', '"efficacy"', '"concentration"',
                          '"dose"', '"function"', '"benefit"', '"interaction"'):
            assert forbidden not in body, forbidden


# ---------------------------------------------------------------------------
# M. Authoring — the adapter, not a second workflow
# ---------------------------------------------------------------------------
class TestAuthoringAdapter:
    async def test_a_draft_is_created_as_a_draft(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await _draft(
                session, substance_key="niacinamide",
                names=[_name("Niacinamide", preferred=True)],
            )
            claim = await session.get(EvidenceClaim, claim_id)
            assert claim.review_status == ReviewStatus.DRAFT.value
            assert claim.domain == EvidenceDomain.SUBSTANCE.value
            assert claim.subject_type == "substance"
            assert claim.subject_key == "niacinamide"
            assert claim.claim_type == ClaimType.SUBSTANCE_IDENTITY.value
            assert claim.evidence_tier == EvidenceTier.REFERENCE_DATA.value
            assert claim.published_at is None

    async def test_the_key_is_not_derived_from_the_display_name(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _draft(
                session, substance_key="cas.98-92-0",
                names=[_name("Niacinamide", preferred=True)],
            )
            substance = (await session.execute(select(Substance))).scalars().one()
        assert substance.substance_key == "cas.98-92-0"
        assert "niacinamide" not in substance.substance_key

    @pytest.mark.parametrize("bad_key", [
        "", "   ", "Niacinamide", "NIACINAMIDE", "niacin amide", "-leading", ".leading",
        "has/slash", "has:colon", "a" * 121, "naïve",
    ])
    async def test_a_malformed_key_is_refused(self, db_clean, bad_key):
        from app.shared.errors.exceptions import ValidationFailedError

        factory = get_sessionmaker()
        async with factory() as session:
            with pytest.raises(ValidationFailedError):
                await substance_authoring.get_or_create_substance(
                    session, substance_key=bad_key,
                    entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
                )

    async def test_an_entity_cannot_silently_change_kind(self, db_clean):
        from app.shared.errors.exceptions import ValidationFailedError

        factory = get_sessionmaker()
        async with factory() as session:
            await substance_authoring.get_or_create_substance(
                session, substance_key="thing", entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
            )
            await session.commit()
        async with factory() as session:
            with pytest.raises(ValidationFailedError):
                await substance_authoring.get_or_create_substance(
                    session, substance_key="thing", entity_kind=EntityKind.GROUP.value,
                )

    async def test_get_or_create_is_idempotent(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            first = await substance_authoring.get_or_create_substance(
                session, substance_key="thing", entity_kind=EntityKind.MIXTURE.value,
            )
            await session.commit()
            first_id = first.id
        async with factory() as session:
            second = await substance_authoring.get_or_create_substance(
                session, substance_key="thing", entity_kind=EntityKind.MIXTURE.value,
            )
            assert second.id == first_id
            assert len((await session.execute(select(Substance))).scalars().all()) == 1

    async def test_authoring_writes_the_normalized_key_itself(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _draft(
                session, substance_key="niacinamide",
                names=[_name("  NIACINAMIDE  ", preferred=True)],
            )
            await session.commit()
        async with factory() as session:
            row = (await session.execute(select(SubstanceName))).scalars().one()
        assert row.name == "  NIACINAMIDE  "
        assert row.normalized_name == "niacinamide"

    def test_the_adapter_owns_no_review_transitions(self):
        body = _code_only((SUBSTANCES_DIR / "authoring.py").read_text(encoding="utf-8"))
        for forbidden in ("ALLOWED_TRANSITIONS", "def approve", "def publish", "def reject",
                          "_assert_transition", "PUBLISHED.value"):
            assert forbidden not in body, forbidden

    def test_normalized_preview_is_read_only(self):
        assert substance_authoring.normalized_preview("  Vitamin C  ") == "vitamin c"
        assert substance_authoring.normalized_preview("") is None


# ---------------------------------------------------------------------------
# N. No interpretation leaks in — position, concentration, or judgement
# ---------------------------------------------------------------------------
class TestNoInterpretation:
    async def test_position_in_a_list_infers_nothing(self, db_clean):
        """Identity is per-name. There is no list, so there is no position to read."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _published(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
        async with factory() as session:
            first = await resolve_names(session, ["Glycerin", "Water", "Niacinamide"])
            last = await resolve_names(session, ["Niacinamide", "Water", "Glycerin"])
        assert first[0].substance_key == last[2].substance_key == "glycerin"
        assert first[0].status == last[2].status
        assert vars(first[0]) | {"query": None} == vars(last[2]) | {"query": None}

    @pytest.mark.parametrize("path", sorted(SUBSTANCES_DIR.glob("*.py")), ids=lambda p: p.name)
    def test_no_concentration_or_dose_arithmetic_in_the_domain(self, path):
        body = _code_only(path.read_text(encoding="utf-8"))
        for forbidden in (
            "percent", "mg/", "ppm", "threshold", "max_concentration", "allowed_up_to",
            "risk_tier", "safety_score", "hazard",
        ):
            assert forbidden not in body, f"{path.name} references {forbidden}"

    def test_the_resolver_has_no_tie_breaking_vocabulary(self):
        body = _code_only((SUBSTANCES_DIR / "service.py").read_text(encoding="utf-8"))
        for forbidden in ("best_match", "most_likely", "prefer_", "highest", "top_",
                          "confidence", "probability", "score"):
            assert forbidden not in body, forbidden

    def test_only_three_resolution_statuses_exist(self):
        assert {s.value for s in ResolutionStatus} == {"resolved", "ambiguous", "unresolved"}


# ---------------------------------------------------------------------------
# O. Nothing that already worked was changed
# ---------------------------------------------------------------------------
class TestExistingBehaviourUnchanged:
    def test_the_care_ontology_still_has_its_own_aliases(self):
        """Step 7A does not touch Care. Its family map is exactly as it was."""
        from app.domains.routines.ontology import INGREDIENT_BY_KEY

        assert "tocopheryl acetate" in INGREDIENT_BY_KEY["vitamin_e"].aliases
        assert "ceramide np" in INGREDIENT_BY_KEY["ceramides"].aliases
        assert "keratin" in INGREDIENT_BY_KEY["hydrolysed_protein"].aliases
        assert "peppermint oil" in INGREDIENT_BY_KEY["menthol"].aliases

    def test_the_care_parser_still_matches_inside_a_label(self):
        """The old behaviour is intact — and is deliberately not what 7A does."""
        from app.domains.routines.parser import parse_label

        matched = {row.key for row in parse_label("Aqua, Niacinamide, Glycerin")}
        assert "niacinamide" in matched

    def test_care_modules_do_not_import_the_substances_domain(self):
        care_root = BACKEND_ROOT / "app" / "domains" / "routines"
        for path in sorted(care_root.rglob("*.py")):
            assert "domains.substances" not in path.read_text(encoding="utf-8"), path.name

    def test_the_existing_evidence_transitions_are_unchanged(self):
        from app.domains.evidence.authoring import ALLOWED_TRANSITIONS

        assert ALLOWED_TRANSITIONS[ReviewStatus.DRAFT.value] == frozenset(
            {ReviewStatus.APPROVED.value, ReviewStatus.REJECTED.value}
        )
        assert ALLOWED_TRANSITIONS[ReviewStatus.APPROVED.value] == frozenset(
            {ReviewStatus.PUBLISHED.value, ReviewStatus.REJECTED.value}
        )
        assert ALLOWED_TRANSITIONS[ReviewStatus.PUBLISHED.value] == frozenset()

    async def test_assert_claim_approvable_was_not_tightened(self, db_clean):
        """An approved claim still passes the old gate. Old call sites are untouched."""
        from app.domains.evidence.service import assert_claim_approvable

        factory = get_sessionmaker()
        async with factory() as session:
            claim_id = await _draft(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
            # Approved, never published: still enough for the old gate, exactly
            # as it was before Step 7A. Tightening it would have retroactively
            # invalidated every seeded release rule.
            await evidence_authoring.approve(session, claim_id, reviewer="reviewer")
            claim = await session.get(EvidenceClaim, claim_id)
            await assert_claim_approvable(session, claim)   # does not raise
            assert claim.review_status == ReviewStatus.APPROVED.value

    def test_reference_data_is_a_new_tier_not_a_replacement(self):
        from app.domains.evidence.authoring import NON_ASSERTIVE_TIERS
        from app.domains.evidence.enums import EVIDENCE_TIERS, EvidenceTier

        assert EvidenceTier.REFERENCE_DATA.value in EVIDENCE_TIERS
        # Not "an absence of evidence": an approved reference claim is supported.
        assert EvidenceTier.REFERENCE_DATA.value not in NON_ASSERTIVE_TIERS

    def test_the_substance_domain_and_claim_type_were_added_not_swapped(self):
        from app.domains.evidence.enums import CLAIM_TYPES, EVIDENCE_DOMAINS

        assert EvidenceDomain.SUBSTANCE.value in EVIDENCE_DOMAINS
        assert ClaimType.SUBSTANCE_IDENTITY.value in CLAIM_TYPES
        for kept in (EvidenceDomain.SKIN_CARE.value, EvidenceDomain.NUTRITION.value):
            assert kept in EVIDENCE_DOMAINS

    def test_publication_verification_has_one_definition(self):
        """authoring.publish and the resolver read the identical checkpoint list."""
        from app.domains.evidence import service as evidence_service

        body = (BACKEND_ROOT / "app" / "domains" / "evidence" / "authoring.py").read_text(
            encoding="utf-8"
        )
        # authoring.py still *writes* each checkpoint once, which is its job.
        # What it must not do is keep a second copy of the completeness test.
        assert "publication_verification_complete" in body
        assert body.count('"adversarial_review_passed"') == 1
        assert "PUBLICATION_VERIFICATION_CHECKPOINTS = " not in body
        assert set(evidence_service.PUBLICATION_VERIFICATION_CHECKPOINTS) == {
            "source_opened", "founder_verified_fact", "claude_review_completed",
            "codex_review_completed", "independent_reviews_agree",
            "adversarial_review_passed",
        }

    async def test_the_product_result_contract_is_untouched(self, db_clean):
        """No substance field appeared on the verdict response.

        Structural: the Step 6 modules that build it never learned about this
        domain, so no contract change can have leaked in.
        """
        for module in ("product", "alternatives", "nutrition", "off"):
            root = BACKEND_ROOT / "app" / "domains" / module
            for path in sorted(root.rglob("*.py")):
                body = path.read_text(encoding="utf-8")
                assert "domains.substances" not in body, path
                assert "resolve_name" not in body, path

    def test_no_api_route_exposes_the_resolver_yet(self):
        api_root = BACKEND_ROOT / "app" / "api"
        for path in sorted(api_root.rglob("*.py")):
            assert "domains.substances" not in path.read_text(encoding="utf-8"), path

    def test_the_models_are_registered_for_alembic(self):
        body = (BACKEND_ROOT / "app" / "shared" / "database" / "registry.py").read_text(
            encoding="utf-8"
        )
        assert "substances import models" in body
        from app.shared.database.registry import Base

        assert "substances" in Base.metadata.tables
        assert "substance_names" in Base.metadata.tables

    async def test_the_tables_exist_in_the_database(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            connection = await session.connection()
            names = await connection.run_sync(
                lambda sync_conn: sa_inspect(sync_conn).get_table_names()
            )
        assert "substances" in names
        assert "substance_names" in names
