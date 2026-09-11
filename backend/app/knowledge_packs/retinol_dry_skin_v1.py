"""Reviewed Step 14E retinol × dry/tight-skin knowledge specification.

The fourth governed pack, and the second cautionary one. Its whole content is
one narrow reading of current dermatologist guidance: people whose skin is dry
are generally not good candidates for retinoid products, and retinol is a
retinoid. Nothing about acne, pigmentation, wrinkles or any treatment goal is
required, inferred or asserted here.

This module is inert. It neither writes evidence nor registers decision rules.
Its sole executable responsibility is to compile one exact, already-published
Step 8G entry into the exact Step 8H manifest reviewed for this pack.

**Retinol, not "retinoids".** The evidence explains that retinol belongs to
the retinoid family -- that relationship is why dry-skin guidance about
retinoids reaches retinol at all -- but the governed identity stays exactly
``retinol``. Retinal, retinyl palmitate, tretinoin and adapalene are different
substances with different sources, and this pack speaks for none of them.

**Pregnancy is not this pack's business.** The reviewed AAD page also
discusses pregnancy. That boundary lives upstream in
``app/domains/routines/hard_handoff.py`` and stays there: this pack does not
duplicate, inspect, infer or reinterpret it, and the customer reason may not
mention it.

It does not import any other governed pack.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NoReturn

from app.domains.personal_decision_release.manifest import (
    PersonalDecisionReleaseManifestError,
    canonical_manifest,
    manifest_content_hash,
    parse_release_manifest,
)

PACK_ID = "for_you.skin_care.retinol_dry_skin.v1"

#: The key this repository already canonicalises in
#: ``app/domains/routines/ontology.py``, where retinol is one member of a
#: retinoid family that also contains retinaldehyde and retinyl palmitate as
#: their own separate ingredients. Reusing the key keeps that separation.
SUBSTANCE_KEY = "retinol"

IDENTITY_ENTITY_KIND = "defined_substance"
IDENTITY_NAME = "Retinol"
IDENTITY_NAME_NAMESPACE = "official_reference"
IDENTITY_NAME_PREFERRED = True
IDENTITY_SOURCE_TYPE = "government_reference"
IDENTITY_SOURCE_TITLE = "Retinol"
IDENTITY_SOURCE_PUBLISHER = "PubChem"
IDENTITY_SOURCE_URL = "https://pubchem.ncbi.nlm.nih.gov/compound/445354"
IDENTITY_SOURCE_EXTERNAL_ID = "445354"
IDENTITY_CAS_NUMBER = "68-26-8"
#: The family the substance belongs to, recorded because the reviewed evidence
#: is stated about retinoids and reaches retinol through this relationship.
#: It is a note about why the evidence applies, never a second identity and
#: never a claim that the family's members are interchangeable.
IDENTITY_SUBSTANCE_FAMILY = "retinoid"
IDENTITY_SOURCE_USE_NOTE = (
    "Government reference used for identity and citation metadata only; no third-party "
    "source text is reproduced in GlamGenius."
)

CATEGORY = "skin_care"
DOMAIN = "skin_care"
FACT_KEY = "care_skin_usual_feel"
FACT_OPERATOR = "equals_any"
FACT_VALUES = ("often_dry_or_tight",)

EVIDENCE_STRENGTH = "moderate"
EVIDENCE_SUMMARY = (
    "Retinol is relevant to dry-skin care because dermatologist guidance states that people "
    "with skin dryness are generally not good candidates for retinoid products and that "
    "retinol is a retinoid, and reviewed research reports that topical retinoids commonly "
    "cause local irritation including dryness."
)
EVIDENCE_SCOPE = (
    "Ingredient-level, non-medical applicability for a user who reports "
    "care_skin_usual_feel=often_dry_or_tight. It does not establish treatment of a diagnosed "
    "condition, a pregnancy or medication boundary, concentration, whole-formula suitability, "
    "the suitability of unrelated co-ingredients, or anything about other retinoids."
)
EVIDENCE_STRENGTH_RATIONALE = (
    "Current dermatologist guidance states directly that people with skin dryness are "
    "generally not good candidates for retinoid products, and reviewed research on topical "
    "retinoids reports local irritation including dryness as a common effect. Moderate is "
    "used because the evidence is ingredient/family-level rather than an exact-product trial, "
    "and formulation and concentration vary enough that no stronger wording is supportable."
)

#: The selected customer citation. Its own page carries both halves of the
#: reviewed proposition -- that retinol is a type of retinoid, and that people
#: with dryness are generally not good candidates for a retinoid -- which is
#: why one locator can carry the whole reason.
AAD_SOURCE_TYPE = "professional_consensus"
AAD_SOURCE_TITLE = "Retinoid or retinol?"
AAD_SOURCE_PUBLISHER = "American Academy of Dermatology Association"
AAD_SOURCE_URL = "https://www.aad.org/public/everyday-care/skin-care-secrets/anti-aging/retinoid-retinol"
AAD_SOURCE_LOCATOR = "Retinoids are not a fad / Is a retinoid the right choice?"
#: An update date is not a publication date. Same policy as every other pack.
AAD_SOURCE_PUBLICATION_DATE = None
AAD_SOURCE_VERSION = "Last updated 2021-05-25"
AAD_SOURCE_JURISDICTION = None
AAD_SOURCE_USE_NOTE = (
    "Public source reviewed through its canonical page for citation and verification; "
    "GlamGenius stores metadata and a locator, not reproduced AAD article text."
)

#: The evidence basis for "irritation including dryness is common", and only
#: that. It is a review of derivatives, carriers and combinations intended to
#: *reduce* instability and irritation, so it is evidence that the irritation
#: is a known general problem -- not that any particular product irritates any
#: particular person, not that every formulation behaves alike, and not that
#: retinol is categorically unsuitable.
PUBMED_SOURCE_TYPE = "peer_reviewed_research"
PUBMED_SOURCE_TITLE = (
    "Topical retinoids: Novel derivatives, nano lipid-based carriers, and combinations to "
    "improve chemical instability and skin irritation"
)
#: The reviewer supplied the journal rather than a corporate publisher for this
#: article, so the journal is what is recorded. This differs deliberately from
#: the 2019 article the petrolatum and glycerin packs cite, whose own copyright
#: line names "Wiley Periodicals, Inc."; which form should be canonical for
#: this journal is a question for review, not something to guess at here.
PUBMED_SOURCE_PUBLISHER = "Journal of Cosmetic Dermatology"
PUBMED_SOURCE_URL = "https://pubmed.ncbi.nlm.nih.gov/38952060/"
PUBMED_SOURCE_LOCATOR = "Abstract"
PUBMED_SOURCE_PUBLICATION_DATE = "2024-07-01"
PUBMED_SOURCE_VERSION = "PMID 38952060; DOI 10.1111/jocd.16415"
PUBMED_SOURCE_JURISDICTION = None
PUBMED_SOURCE_USE_NOTE = (
    "Bibliographic and abstract page used for citation and reviewer verification; no article "
    "full text is stored or reproduced."
)

SEMANTIC_RULE_ID = "for_you.semantic.skin_care.retinol.dry_skin"
SEMANTIC_RULE_VERSION = "1"
SEMANTIC_SIGNAL = "cautionary"

POLICY_ID = "for_you.policy.skin_care.retinol.dry_skin.skip"
POLICY_VERSION = "1"
POLICY_SIGNAL_SET = "cautionary_only"
POLICY_ACTION = "skip"

EXPLANATION_ID = "for_you.explanation.skin_care.retinol.dry_skin.skip"
EXPLANATION_VERSION = "1"

REASON_KEY = "for_you.skin_care.retinol.dry_skin.dermatologist_guidance"

#: The reviewed intent for the future customer sentence behind REASON_KEY.
#: States what the guidance says, and carries the retinol/retinoid link because
#: without it the sentence would not follow.
FUTURE_REASON_INTENT = (
    "Retinol is a retinoid, and for skin that often feels dry or tight dermatologist "
    "guidance says people with dryness are generally not good candidates for retinoid "
    "products."
)

#: Claims the selected AAD citation does not support in this context, and which
#: must therefore never appear in the customer reason attached to it.
#:
#: Pregnancy heads the list on purpose. The same AAD page really does discuss
#: it, so it is the single most likely thing to leak from source into sentence
#: -- and it is a medical safety boundary owned upstream by the hard-handoff
#: path, not something a dry-skin ingredient reason may restate.
REASON_CLAIMS_OUT_OF_SCOPE = (
    "pregnan",
    "breastfeed",
    "nursing",
    "birth defect",
    "acne",
    "anti-aging",
    "antiaging",
    "wrinkle",
    "fine lines",
    "collagen",
    "pigmentation",
    "dark spots",
    "unsafe",
    "toxic",
    "harmful",
    "damages your skin",
    "everyone",
    "clinically proven",
    "treats",
    "cures",
)


class RetinolDrySkinKnowledgePackError(ValueError):
    """The published entry is not the exact evidence reviewed for this pack."""


def _fail(message: str) -> NoReturn:
    raise RetinolDrySkinKnowledgePackError(message)


def _exact(entry: Mapping[str, object], field: str, expected: object) -> None:
    if entry.get(field) != expected:
        _fail(f"{field} is not the reviewed Step 14E value")


def _required_mapping(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(f"{where} must be an object")
    return value


def _required_sequence(value: object, where: str) -> Sequence[object]:
    if not isinstance(value, list):
        _fail(f"{where} must be a list")
    return value


def _validate_condition(entry: Mapping[str, object]) -> None:
    conditions = _required_sequence(entry.get("conditions"), "conditions")
    if len(conditions) != 1:
        _fail("conditions must contain exactly the one reviewed condition")
    condition = _required_mapping(conditions[0], "condition")
    if set(condition) != {"fact_key", "operator", "values"}:
        _fail("condition fields do not match the reviewed condition")
    _exact(condition, "fact_key", FACT_KEY)
    _exact(condition, "operator", FACT_OPERATOR)
    _exact(condition, "values", list(FACT_VALUES))


def _source_matches(source: Mapping[str, object], expected: Mapping[str, object]) -> bool:
    return all(source.get(field) == value for field, value in expected.items())


def _validate_sources(entry: Mapping[str, object]) -> Mapping[str, object]:
    """Match both reviewed paths by value, and return the AAD one.

    Matching by value rather than by position is what makes source order
    immaterial: the serialized order of two reviewed paths is a serialisation
    detail, not scientific meaning.
    """
    sources = _required_sequence(entry.get("sources"), "sources")
    if len(sources) != 2:
        _fail("sources must contain exactly the two reviewed paths")
    paths = [_required_mapping(source, "source") for source in sources]

    aad_expected: dict[str, object] = {
        "source_type": AAD_SOURCE_TYPE,
        "title": AAD_SOURCE_TITLE,
        "publisher": AAD_SOURCE_PUBLISHER,
        "canonical_url": AAD_SOURCE_URL,
        "locator": AAD_SOURCE_LOCATOR,
        "publication_date": AAD_SOURCE_PUBLICATION_DATE,
        "version_or_revision": AAD_SOURCE_VERSION,
        "jurisdiction": AAD_SOURCE_JURISDICTION,
        "status": "active",
        "license_or_use_note": AAD_SOURCE_USE_NOTE,
    }
    pubmed_expected: dict[str, object] = {
        "source_type": PUBMED_SOURCE_TYPE,
        "title": PUBMED_SOURCE_TITLE,
        "publisher": PUBMED_SOURCE_PUBLISHER,
        "canonical_url": PUBMED_SOURCE_URL,
        "locator": PUBMED_SOURCE_LOCATOR,
        "publication_date": PUBMED_SOURCE_PUBLICATION_DATE,
        "version_or_revision": PUBMED_SOURCE_VERSION,
        "jurisdiction": PUBMED_SOURCE_JURISDICTION,
        "status": "active",
        "license_or_use_note": PUBMED_SOURCE_USE_NOTE,
    }
    aad = [source for source in paths if _source_matches(source, aad_expected)]
    pubmed = [source for source in paths if _source_matches(source, pubmed_expected)]
    if len(aad) != 1 or len(pubmed) != 1:
        _fail("sources do not exactly match the reviewed AAD and PubMed paths")
    if aad[0] is pubmed[0]:
        _fail("the two reviewed source paths must be distinct")
    for source in paths:
        if not isinstance(source.get("source_key"), str) or not source["source_key"].strip():
            _fail("each reviewed source path must carry its generated source_key")
    return aad[0]


def _validate_entry(entry: Mapping[str, object]) -> tuple[str, int, str]:
    if not isinstance(entry, Mapping):
        _fail("entry must be the serialized published Step 8G object")
    exact_values = {
        "review_status": "published",
        "claim_status": "supported",
        "category": CATEGORY,
        "domain": DOMAIN,
        "substance_key": SUBSTANCE_KEY,
        "subject_type": "substance",
        "claim_type": "substance_personal_applicability",
        "evidence_tier": "clinically_studied",
        "ai_generated": False,
        "evidence_strength": EVIDENCE_STRENGTH,
        "claim_version": 1,
        "summary": EVIDENCE_SUMMARY,
        "scope": EVIDENCE_SCOPE,
        "strength_rationale": EVIDENCE_STRENGTH_RATIONALE,
    }
    for field, expected in exact_values.items():
        _exact(entry, field, expected)
    claim_key = entry.get("claim_key")
    if not isinstance(claim_key, str) or not claim_key.strip():
        _fail("claim_key must be the generated nonblank Step 8G identity")
    _validate_condition(entry)
    aad_source = _validate_sources(entry)
    return claim_key, 1, str(aad_source["source_key"])


def build_release_manifest_from_published_entry(
    entry: Mapping[str, object],
) -> dict[str, object]:
    """Compile one exact reviewed Step 8G entry into a canonical Step 8H manifest."""
    claim_key, claim_version, aad_source_key = _validate_entry(entry)
    raw_manifest = {
        "schema_version": 1,
        "semantic_rules": [
            {
                "rule_id": SEMANTIC_RULE_ID,
                "rule_version": SEMANTIC_RULE_VERSION,
                "category": CATEGORY,
                "substance_key": SUBSTANCE_KEY,
                "claim_key": claim_key,
                "claim_version": claim_version,
                "signal": SEMANTIC_SIGNAL,
            }
        ],
        "policy_rules": [
            {
                "policy_id": POLICY_ID,
                "policy_version": POLICY_VERSION,
                "category": CATEGORY,
                "semantic_rule_identities": [
                    {"rule_id": SEMANTIC_RULE_ID, "rule_version": SEMANTIC_RULE_VERSION}
                ],
                "signal_set": POLICY_SIGNAL_SET,
                "has_identity_unresolved": False,
                "has_identity_ambiguous": False,
                "has_personal_evidence_gap": False,
                "action": POLICY_ACTION,
            }
        ],
        "explanation_rules": [
            {
                "explanation_id": EXPLANATION_ID,
                "explanation_version": EXPLANATION_VERSION,
                "policy_id": POLICY_ID,
                "policy_version": POLICY_VERSION,
                "action": POLICY_ACTION,
                "semantic_rule_id": SEMANTIC_RULE_ID,
                "semantic_rule_version": SEMANTIC_RULE_VERSION,
                "substance_key": SUBSTANCE_KEY,
                "claim_key": claim_key,
                "claim_version": claim_version,
                "source_key": aad_source_key,
                "source_locator": AAD_SOURCE_LOCATOR,
                "reason_key": REASON_KEY,
            }
        ],
    }
    try:
        parsed = parse_release_manifest(raw_manifest)
        canonical = canonical_manifest(parsed)
        manifest_content_hash(parsed)
    except (PersonalDecisionReleaseManifestError, ValueError) as error:
        raise RetinolDrySkinKnowledgePackError(
            "the reviewed pack did not form a valid Step 8H manifest"
        ) from error
    return canonical


__all__ = [
    "FUTURE_REASON_INTENT",
    "PACK_ID",
    "REASON_CLAIMS_OUT_OF_SCOPE",
    "REASON_KEY",
    "RetinolDrySkinKnowledgePackError",
    "build_release_manifest_from_published_entry",
]
