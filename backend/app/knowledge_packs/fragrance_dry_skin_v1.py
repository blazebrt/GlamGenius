"""Reviewed Step 14E fragrance × dry/tight-skin knowledge specification.

The third governed pack, and the first **cautionary** one. Everything before it
pointed at BUY; this one is the proof that the same machinery carries a
direction away from a product as faithfully as one towards it.

This module is inert. It neither writes evidence nor registers decision rules.
Its sole executable responsibility is to compile one exact, already-published
Step 8G entry into the exact Step 8H manifest reviewed for this pack.

**What this pack does not say.** It does not say the customer is allergic to
anything, that fragrance is unsafe, that every fragrance chemical behaves the
same way, or that anyone should stop using a product. It says one narrow
thing: for a person who reports that their skin often feels dry or tight,
dermatologist guidance points at fragrance-free skin care, so fragrance on an
ingredient list is a cautionary signal for that person.

**Fragrance is a mixture, and that is the whole identity story.** The FDA
explains that a fragrance formula can be a complex mixture of natural and
synthetic ingredients, and that a cosmetic label may declare all of it as the
single word "Fragrance". Modelling it as one molecule with one CAS number
would assert a chemical identity nobody has established -- see
:data:`IDENTITY_ENTITY_KIND`.

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

PACK_ID = "for_you.skin_care.fragrance_dry_skin.v1"

#: The key this repository already canonicalises in
#: ``app/domains/routines/ontology.py``: display "Fragrance", label-facing name
#: "Parfum", aliases parfum / perfume / aroma. One identity, reused.
SUBSTANCE_KEY = "fragrance"

#: ``mixture``, never ``defined_substance``. A fragrance formula can be a
#: complex mixture of natural and synthetic ingredients that a label may
#: declare as the one word "Fragrance". There is no single molecule here, so
#: there is deliberately no CAS number and no external numeric identifier: an
#: id would assert a discrete chemical identity that does not exist.
IDENTITY_ENTITY_KIND = "mixture"
IDENTITY_NAME = "Fragrance"
#: ``common``, not ``official_reference`` and emphatically not ``inci``. The
#: FDA describes the English label word as the ingredient's common or usual
#: name; it is not a register entry, and this pack claims no INCI authority.
IDENTITY_NAME_NAMESPACE = "common"
IDENTITY_NAME_PREFERRED = True
#: The label-facing name the existing ontology records for this identity, and
#: the parenthetical the FDA's own common-name example uses.
IDENTITY_LABEL_NAME = "Parfum"
IDENTITY_SOURCE_TYPE = "government_reference"
IDENTITY_SOURCE_TITLE = "Fragrances in Cosmetics"
IDENTITY_SOURCE_PUBLISHER = "U.S. Food and Drug Administration"
IDENTITY_SOURCE_URL = "https://www.fda.gov/cosmetics/cosmetic-ingredients/fragrances-cosmetics"
#: The second FDA page, which carries the common/usual-name example
#: "Fragrance (Parfum)". Recorded because the naming claim rests on it.
#:
#: The path segment is "cosmetics-labeling", plural. The singular form
#: reads just as plausibly and is what this URL was first guessed as; the
#: plural is what independent review confirmed, and a test now pins the
#: whole string rather than its prefix.
IDENTITY_NAMING_SOURCE_TITLE = "Cosmetic Ingredient Names"
IDENTITY_NAMING_SOURCE_URL = (
    "https://www.fda.gov/cosmetics/cosmetics-labeling/cosmetic-ingredient-names"
)
#: No CAS number, no external id. See IDENTITY_ENTITY_KIND.
IDENTITY_CAS_NUMBER = None
IDENTITY_SOURCE_EXTERNAL_ID = None
IDENTITY_SOURCE_USE_NOTE = (
    "Government reference used for identity and citation metadata only; no third-party "
    "source text is reproduced in GlamGenius."
)

CATEGORY = "skin_care"
DOMAIN = "skin_care"
FACT_KEY = "care_skin_usual_feel"
FACT_OPERATOR = "equals_any"
FACT_VALUES = ("often_dry_or_tight",)

#: ``limited``, not moderate. The dry-skin direction is directly recommended by
#: current professional guidance, which is enough to author a cautionary
#: signal -- but nothing here reaches for the broader fragrance-sensitisation
#: literature, because that literature is about populations and allergens and
#: this claim is about one person's reported dry skin. Inflating one into the
#: other is the mistake this value refuses to make.
EVIDENCE_STRENGTH = "limited"
EVIDENCE_SUMMARY = (
    "Fragrance is relevant to dry-skin care because dermatologist guidance recommends "
    "fragrance-free skin-care products for dry skin and lists fragrance among the ingredients "
    "to stop using on dry skin."
)
EVIDENCE_SCOPE = (
    "Ingredient-level, non-medical applicability for a user who reports "
    "care_skin_usual_feel=often_dry_or_tight. It does not establish an allergy or sensitivity "
    "diagnosis, that fragrance is unsafe, that every fragrance ingredient behaves alike, "
    "concentration, whole-formula suitability, or the suitability of unrelated co-ingredients."
)
EVIDENCE_STRENGTH_RATIONALE = (
    "Current dermatologist guidance for dry skin recommends fragrance-free products and names "
    "fragrance among the ingredients to stop using. Limited is used because that guidance is "
    "the whole of the reviewed basis: no population sensitisation data is relied on, the "
    "declared label word covers a mixture whose composition varies by product, and no "
    "concentration or exact-product trial supports it."
)

#: The same reviewed AAD page the first two packs pin -- a different locator on
#: it, because this pack's direction comes from the fragrance-free
#: recommendation and the stop list rather than the ingredients-to-look-for
#: list. The URL and metadata policy are copied from those reviewed packs, not
#: imported and not re-inferred.
AAD_SOURCE_TYPE = "professional_consensus"
AAD_SOURCE_TITLE = "Dermatologists' top tips for relieving dry skin"
AAD_SOURCE_PUBLISHER = "American Academy of Dermatology Association"
AAD_SOURCE_URL = (
    "https://www.aad.org/public/everyday-care/skin-care-basics/dry/"
    "dermatologists-tips-relieve-dry-skin"
)
AAD_SOURCE_LOCATOR = (
    "What skin care products are best for dry skin? / Gentle, fragrance-free skin care "
    "products; Dry skin? Stop using skin care products that contain..."
)
#: The page reports a last-updated value and states no publication date. An
#: update date is not a publication date, so this stays null and the update is
#: recorded as a version instead.
AAD_SOURCE_PUBLICATION_DATE = None
AAD_SOURCE_VERSION = "Last updated 2026-01-02"
#: The source does not state the territory its guidance applies to.
AAD_SOURCE_JURISDICTION = None
AAD_SOURCE_USE_NOTE = (
    "Public source reviewed through its canonical page for citation and verification; "
    "GlamGenius stores metadata and a locator, not reproduced AAD article text."
)

SEMANTIC_RULE_ID = "for_you.semantic.skin_care.fragrance.dry_skin"
SEMANTIC_RULE_VERSION = "1"
SEMANTIC_SIGNAL = "cautionary"

POLICY_ID = "for_you.policy.skin_care.fragrance.dry_skin.skip"
POLICY_VERSION = "1"
POLICY_SIGNAL_SET = "cautionary_only"
POLICY_ACTION = "skip"

EXPLANATION_ID = "for_you.explanation.skin_care.fragrance.dry_skin.skip"
EXPLANATION_VERSION = "1"

REASON_KEY = "for_you.skin_care.fragrance.dry_skin.dermatologist_guidance"

#: The reviewed intent for the future customer sentence behind REASON_KEY. It
#: states what the guidance recommends. It does not tell the person anything
#: about their own body, and it does not characterise the ingredient.
FUTURE_REASON_INTENT = (
    "For dry skin, dermatologist guidance recommends fragrance-free skin-care products."
)

#: Claims the selected AAD citation does not support. The first four are the
#: dangerous ones: this pack sits next to a real body of allergy literature it
#: deliberately does not rely on, and borrowing that literature's conclusions
#: into a sentence cited to a dry-skin care page would be an unsourced medical
#: claim about a specific person.
REASON_CLAIMS_OUT_OF_SCOPE = (
    "you are allergic",
    "allergic",
    "allergy",
    "sensitised",
    "sensitized",
    "causes irritation",
    "cause irritation",
    "irritant",
    "unsafe",
    "toxic",
    "harmful",
    "damages",
    "damage your skin",
    "everyone",
    "all fragrance",
    "clinically proven",
    "banned",
)


class FragranceDrySkinKnowledgePackError(ValueError):
    """The published entry is not the exact evidence reviewed for this pack."""


def _fail(message: str) -> NoReturn:
    raise FragranceDrySkinKnowledgePackError(message)


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
    """Match the one reviewed evidence path by value, and return it.

    One source, deliberately. The FDA pages establish what the label word
    means and are declared above as this pack's identity authority; the
    personal dry-skin direction rests on the AAD guidance alone, and the
    evidence entry says so rather than padding itself with provenance that
    supports a different claim.
    """
    sources = _required_sequence(entry.get("sources"), "sources")
    if len(sources) != 1:
        _fail("sources must contain exactly the one reviewed path")
    source = _required_mapping(sources[0], "source")
    expected: dict[str, object] = {
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
    if not _source_matches(source, expected):
        _fail("the source does not exactly match the reviewed AAD path")
    if not isinstance(source.get("source_key"), str) or not source["source_key"].strip():
        _fail("the reviewed source path must carry its generated source_key")
    return source


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
        raise FragranceDrySkinKnowledgePackError(
            "the reviewed pack did not form a valid Step 8H manifest"
        ) from error
    return canonical


__all__ = [
    "FUTURE_REASON_INTENT",
    "PACK_ID",
    "REASON_CLAIMS_OUT_OF_SCOPE",
    "REASON_KEY",
    "FragranceDrySkinKnowledgePackError",
    "build_release_manifest_from_published_entry",
]
