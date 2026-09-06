"""Reviewed Step 8I petrolatum × dry/tight-skin knowledge specification.

This module is deliberately inert.  It neither writes evidence nor registers
decision rules.  Its sole executable responsibility is to compile one exact,
already-published Step 8G entry into the exact Step 8H manifest reviewed for
this pack.
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

PACK_ID = "for_you.skin_care.petrolatum_dry_skin.v1"

SUBSTANCE_KEY = "petrolatum"
IDENTITY_ENTITY_KIND = "mixture"
IDENTITY_NAME = "Petrolatum"
IDENTITY_NAME_NAMESPACE = "official_reference"
IDENTITY_NAME_PREFERRED = True
IDENTITY_SOURCE_TYPE = "government_reference"
IDENTITY_SOURCE_TITLE = "Petrolatum [USP]"
IDENTITY_SOURCE_PUBLISHER = "PubChem / ChemIDplus"
IDENTITY_SOURCE_URL = "https://pubchem.ncbi.nlm.nih.gov/substance/135345390"
IDENTITY_SOURCE_EXTERNAL_ID = "0008009038"
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
    "Petrolatum is relevant to dry-skin care because it can reduce moisture loss and is "
    "included by dermatologists among ingredients to look for in creams or ointments for "
    "dry skin."
)
EVIDENCE_SCOPE = (
    "Ingredient-level, non-medical applicability for a user who reports "
    "care_skin_usual_feel=often_dry_or_tight. It does not establish treatment of a diagnosed "
    "condition, concentration, whole-formula suitability, or the suitability of unrelated "
    "co-ingredients."
)
EVIDENCE_STRENGTH_RATIONALE = (
    "Current dermatologist guidance explicitly includes petrolatum among cream or ointment "
    "ingredients for dry skin, and randomized dry-skin research found the petrolatum component "
    "improved barrier function through reduced transepidermal water loss. Moderate is used "
    "because the evidence is ingredient/component-level rather than an exact-product "
    "therapeutic trial."
)

AAD_SOURCE_TYPE = "professional_consensus"
AAD_SOURCE_TITLE = "Dermatologists' top tips for relieving dry skin"
AAD_SOURCE_PUBLISHER = "American Academy of Dermatology Association"
AAD_SOURCE_URL = (
    "https://www.aad.org/public/everyday-care/skin-care-basics/dry/"
    "dermatologists-tips-relieve-dry-skin"
)
AAD_SOURCE_LOCATOR = "What skin care products are best for dry skin? / Ointment or cream"
AAD_SOURCE_PUBLICATION_DATE = "2026-01-02"
AAD_SOURCE_VERSION = "Last updated 2026-01-02"
AAD_SOURCE_JURISDICTION = "global"
AAD_SOURCE_USE_NOTE = (
    "Public source reviewed through its canonical page for citation and verification; "
    "GlamGenius stores metadata and a locator, not reproduced AAD article text."
)

PUBMED_SOURCE_TYPE = "peer_reviewed_research"
PUBMED_SOURCE_TITLE = (
    "Combined effects of glycerol and petrolatum in an emollient cream: A randomized, "
    "double-blind, crossover study in healthy volunteers with dry skin"
)
PUBMED_SOURCE_PUBLISHER = "PubMed"
PUBMED_SOURCE_URL = "https://pubmed.ncbi.nlm.nih.gov/31532576/"
PUBMED_SOURCE_LOCATOR = "Abstract / Conclusions"
PUBMED_SOURCE_PUBLICATION_DATE = "2019-09-18"
PUBMED_SOURCE_VERSION = "PMID 31532576; DOI 10.1111/jocd.13163"
PUBMED_SOURCE_JURISDICTION = "global"
PUBMED_SOURCE_USE_NOTE = (
    "Bibliographic and abstract page used for citation and reviewer verification; no article "
    "full text is stored or reproduced."
)

SEMANTIC_RULE_ID = "for_you.semantic.skin_care.petrolatum.dry_skin"
SEMANTIC_RULE_VERSION = "1"
SEMANTIC_SIGNAL = "supporting"

POLICY_ID = "for_you.policy.skin_care.petrolatum.dry_skin.buy"
POLICY_VERSION = "1"
POLICY_SIGNAL_SET = "supporting_only"
POLICY_ACTION = "buy"

EXPLANATION_ID = "for_you.explanation.skin_care.petrolatum.dry_skin.buy"
EXPLANATION_VERSION = "1"
REASON_KEY = "for_you.skin_care.petrolatum.dry_skin.moisture_loss"


class FirstProductionKnowledgePackError(ValueError):
    """The published entry is not the exact evidence reviewed for Step 8I."""


def _fail(message: str) -> NoReturn:
    raise FirstProductionKnowledgePackError(message)


def _exact(entry: Mapping[str, object], field: str, expected: object) -> None:
    if entry.get(field) != expected:
        _fail(f"{field} is not the reviewed Step 8I value")


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
        raise FirstProductionKnowledgePackError(
            "the reviewed pack did not form a valid Step 8H manifest"
        ) from error
    return canonical


__all__ = [
    "FirstProductionKnowledgePackError",
    "PACK_ID",
    "build_release_manifest_from_published_entry",
]
