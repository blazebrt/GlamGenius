"""Reviewed Step 14E salicylic acid × oily + rarely-reactive knowledge specification.

The fifth and last pack of the frozen Skin-Care V1 roster, and the first one
whose applicability needs **two** facts to be true at once.

This module is inert. It neither writes evidence nor registers decision rules.
Its sole executable responsibility is to compile one exact, already-published
Step 8G entry into the exact Step 8H manifest reviewed for this pack.

**Why two conditions.** The reviewed guidance says two things in the same
breath: ingredients such as salicylic acid can help reduce oiliness, *and*
they may be too harsh for some skin. A pack that keyed only on oily skin would
carry the first half and quietly drop the second. So the reviewed applicability
is narrower than "salicylic acid is good for oily skin": it is oily skin **and**
a reported sensitivity of ``rarely_reactive``.

That second condition is a **conservative eligibility boundary, not a
scientific finding.** Nothing here claims that ``rarely_reactive`` guarantees
anyone will tolerate anything. It exists so that this launch pack does not
reach ``sometimes_reactive``, ``often_reactive`` or ``not_sure`` users while
the same guidance warns the ingredient can be too harsh. For those users the
honest answer for now is that there is not enough information -- which is a
governed outcome, not a gap to paper over.

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

PACK_ID = "for_you.skin_care.salicylic_acid_oily_skin.v1"

#: The key this repository already canonicalises in
#: ``app/domains/routines/ontology.py``, where "bha", "salicylic" and "beta
#: hydroxy acid" are recorded as aliases of this one identity rather than as
#: separate ingredients. Reusing the key keeps that decision intact.
SUBSTANCE_KEY = "salicylic_acid"

IDENTITY_ENTITY_KIND = "defined_substance"
IDENTITY_NAME = "Salicylic Acid"
IDENTITY_NAME_NAMESPACE = "official_reference"
IDENTITY_NAME_PREFERRED = True
IDENTITY_SOURCE_TYPE = "government_reference"
IDENTITY_SOURCE_TITLE = "Salicylic Acid"
IDENTITY_SOURCE_PUBLISHER = "PubChem"
IDENTITY_SOURCE_URL = "https://pubchem.ncbi.nlm.nih.gov/compound/338"
IDENTITY_SOURCE_EXTERNAL_ID = "338"
IDENTITY_CAS_NUMBER = "69-72-7"
IDENTITY_SOURCE_USE_NOTE = (
    "Government reference used for identity and citation metadata only; no third-party "
    "source text is reproduced in GlamGenius."
)

CATEGORY = "skin_care"
DOMAIN = "skin_care"

#: Both conditions are mandatory, and both are existing controlled care
#: declarations. No profile key is added by this pack.
FACT_OPERATOR = "equals_any"
FEEL_FACT_KEY = "care_skin_usual_feel"
FEEL_FACT_VALUES = ("often_oily",)
SENSITIVITY_FACT_KEY = "care_skin_sensitivity"
SENSITIVITY_FACT_VALUES = ("rarely_reactive",)

#: The reviewed conditions as a set of (fact_key, operator, values). A set,
#: because ``all_of`` is a conjunction: two conditions in the other order are
#: the same reviewed applicability, and order is a serialisation detail.
REVIEWED_CONDITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (FEEL_FACT_KEY, FACT_OPERATOR, FEEL_FACT_VALUES),
    (SENSITIVITY_FACT_KEY, FACT_OPERATOR, SENSITIVITY_FACT_VALUES),
)

#: The sensitivity values this pack deliberately does not reach. Recorded so
#: the boundary is reviewable and testable rather than implicit in an absence.
SENSITIVITY_VALUES_NOT_COVERED = ("sometimes_reactive", "often_reactive", "not_sure")

EVIDENCE_STRENGTH = "moderate"
EVIDENCE_SUMMARY = (
    "Salicylic acid is relevant to oily-skin care because dermatologist guidance states that "
    "ingredients such as salicylic acid can help reduce oiliness, and a peer-reviewed Delphi "
    "study of cosmetic dermatologists reached consensus on salicylic acid for oily skin."
)
EVIDENCE_SCOPE = (
    "Ingredient-level, non-medical applicability for a user who reports "
    "care_skin_usual_feel=often_oily and care_skin_sensitivity=rarely_reactive. Both facts are "
    "required. It does not establish treatment of a diagnosed condition, tolerance for any "
    "individual, suitability for users who report other sensitivity values, concentration, "
    "whole-formula suitability, or the suitability of unrelated co-ingredients."
)
EVIDENCE_STRENGTH_RATIONALE = (
    "Current dermatologist guidance for oily skin names salicylic acid among ingredients that "
    "can help reduce oiliness, and an independent peer-reviewed Delphi consensus of cosmetic "
    "dermatologists reached agreement on salicylic acid for oily skin. Moderate is used "
    "because two independent professional paths agree while the evidence remains "
    "ingredient-level rather than an exact-product trial, and because the same guidance warns "
    "the ingredient can be too harsh for some skin, which the reviewed applicability narrows "
    "for rather than resolves."
)

#: The selected customer citation.
AAD_SOURCE_TYPE = "professional_consensus"
AAD_SOURCE_TITLE = "How to control oily skin"
AAD_SOURCE_PUBLISHER = "American Academy of Dermatology Association"
AAD_SOURCE_URL = "https://www.aad.org/public/everyday-care/skin-care-basics/dry/oily-skin"
AAD_SOURCE_LOCATOR = (
    "10 do's and don'ts from dermatologists / choose products labeled oil free and "
    "noncomedogenic"
)
#: An update date is not a publication date. Same policy as every other pack.
AAD_SOURCE_PUBLICATION_DATE = None
AAD_SOURCE_VERSION = "Last updated 2024-09-03"
AAD_SOURCE_JURISDICTION = None
AAD_SOURCE_USE_NOTE = (
    "Public source reviewed through its canonical page for citation and verification; "
    "GlamGenius stores metadata and a locator, not reproduced AAD article text."
)

#: The second, independent professional path: a two-round Delphi study in which
#: 62 dermatologists at 43 centres reached consensus on salicylic acid for oily
#: skin. The same study also discusses acne; that is not this pack's
#: proposition and is not borrowed into it.
DELPHI_SOURCE_TYPE = "peer_reviewed_research"
DELPHI_SOURCE_TITLE = (
    "Skincare ingredients recommended by cosmetic dermatologists: A Delphi consensus study"
)
DELPHI_SOURCE_PUBLISHER = "Elsevier Inc."
DELPHI_SOURCE_URL = "https://pubmed.ncbi.nlm.nih.gov/40233838/"
DELPHI_SOURCE_LOCATOR = "Abstract / Results"
#: The electronic publication date, which is when the article first published.
#: The print issue (J Am Acad Dermatol 2025 Dec;93(6):1509-1525) is recorded in
#: the version string rather than substituted for it.
DELPHI_SOURCE_PUBLICATION_DATE = "2025-04-14"
DELPHI_SOURCE_VERSION = (
    "PMID 40233838; DOI 10.1016/j.jaad.2025.04.021; J Am Acad Dermatol 2025 Dec;93(6):1509-1525"
)
DELPHI_SOURCE_JURISDICTION = None
DELPHI_SOURCE_USE_NOTE = (
    "Bibliographic and abstract page used for citation and reviewer verification; no article "
    "full text is stored or reproduced."
)

SEMANTIC_RULE_ID = "for_you.semantic.skin_care.salicylic_acid.oily_skin"
SEMANTIC_RULE_VERSION = "1"
SEMANTIC_SIGNAL = "supporting"

POLICY_ID = "for_you.policy.skin_care.salicylic_acid.oily_skin.buy"
POLICY_VERSION = "1"
POLICY_SIGNAL_SET = "supporting_only"
POLICY_ACTION = "buy"

EXPLANATION_ID = "for_you.explanation.skin_care.salicylic_acid.oily_skin.buy"
EXPLANATION_VERSION = "1"

REASON_KEY = "for_you.skin_care.salicylic_acid.oily_skin.dermatologist_guidance"

#: The reviewed intent for the future customer sentence behind REASON_KEY.
#: Scoped to the oiliness proposition the selected AAD locator carries, and to
#: nothing else -- not the Delphi consensus, and not the acne context either
#: source sits beside.
FUTURE_REASON_INTENT = (
    "For oily skin, dermatologist guidance says salicylic acid can help reduce oiliness."
)

#: Claims the selected AAD citation does not support, and which must therefore
#: never appear in the customer reason attached to it.
#:
#: The tolerance claims matter most here. ``rarely_reactive`` is the product
#: being careful about who this pack reaches; it is not evidence that anyone
#: will tolerate the ingredient, and a reason that turned the boundary into a
#: reassurance would invert its meaning.
REASON_CLAIMS_OUT_OF_SCOPE = (
    "treats acne",
    "treat acne",
    "prevents acne",
    "prevent acne",
    "acne",
    "unclogs pores",
    "unclog pores",
    "blackheads",
    "safe for sensitive skin",
    "will not irritate",
    "won't irritate",
    "guaranteed",
    "clinically proven",
    "consensus",
    "dermatologists agree",
    "cures",
    "heals",
)


class SalicylicAcidOilySkinKnowledgePackError(ValueError):
    """The published entry is not the exact evidence reviewed for this pack."""


def _fail(message: str) -> NoReturn:
    raise SalicylicAcidOilySkinKnowledgePackError(message)


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


def _validate_conditions(entry: Mapping[str, object]) -> None:
    """Both reviewed conditions must be present, exactly, and nothing else.

    Matched as a set rather than by position: ``all_of`` is a conjunction, so
    the two conditions in either serialized order are the same reviewed
    applicability. Anything missing, extra, duplicated or altered is refused --
    an entry carrying only the oily-skin fact is a *different, wider* claim
    than the one that was reviewed, and it is the one this pack most needs to
    refuse.
    """
    conditions = _required_sequence(entry.get("conditions"), "conditions")
    if len(conditions) != len(REVIEWED_CONDITIONS):
        _fail("conditions must contain exactly the two reviewed conditions")
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for raw in conditions:
        condition = _required_mapping(raw, "condition")
        if set(condition) != {"fact_key", "operator", "values"}:
            _fail("condition fields do not match the reviewed conditions")
        fact_key = condition.get("fact_key")
        operator = condition.get("operator")
        values = condition.get("values")
        if not isinstance(fact_key, str) or not isinstance(operator, str):
            _fail("condition fields do not match the reviewed conditions")
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            _fail("condition values must be a list of strings")
        seen.add((fact_key, operator, tuple(values)))
    if seen != set(REVIEWED_CONDITIONS):
        _fail("conditions are not exactly the two reviewed conditions")


def _source_matches(source: Mapping[str, object], expected: Mapping[str, object]) -> bool:
    return all(source.get(field) == value for field, value in expected.items())


def _validate_sources(entry: Mapping[str, object]) -> Mapping[str, object]:
    """Match both reviewed paths by value, and return the AAD one."""
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
    delphi_expected: dict[str, object] = {
        "source_type": DELPHI_SOURCE_TYPE,
        "title": DELPHI_SOURCE_TITLE,
        "publisher": DELPHI_SOURCE_PUBLISHER,
        "canonical_url": DELPHI_SOURCE_URL,
        "locator": DELPHI_SOURCE_LOCATOR,
        "publication_date": DELPHI_SOURCE_PUBLICATION_DATE,
        "version_or_revision": DELPHI_SOURCE_VERSION,
        "jurisdiction": DELPHI_SOURCE_JURISDICTION,
        "status": "active",
        "license_or_use_note": DELPHI_SOURCE_USE_NOTE,
    }
    aad = [source for source in paths if _source_matches(source, aad_expected)]
    delphi = [source for source in paths if _source_matches(source, delphi_expected)]
    if len(aad) != 1 or len(delphi) != 1:
        _fail("sources do not exactly match the reviewed AAD and Delphi paths")
    if aad[0] is delphi[0]:
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
    _validate_conditions(entry)
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
        raise SalicylicAcidOilySkinKnowledgePackError(
            "the reviewed pack did not form a valid Step 8H manifest"
        ) from error
    return canonical


__all__ = [
    "FUTURE_REASON_INTENT",
    "PACK_ID",
    "REASON_CLAIMS_OUT_OF_SCOPE",
    "REASON_KEY",
    "SalicylicAcidOilySkinKnowledgePackError",
    "build_release_manifest_from_published_entry",
]
