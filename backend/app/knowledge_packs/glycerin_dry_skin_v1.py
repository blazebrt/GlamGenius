"""Reviewed Step 14D glycerin × dry/tight-skin knowledge specification.

The second governed pack, and therefore the first real test of whether the
multi-pack architecture works. Everything structural here is deliberately the
same shape as the first pack; everything scientific is its own.

This module is inert. It neither writes evidence nor registers decision rules.
Its sole executable responsibility is to compile one exact, already-published
Step 8G entry into the exact Step 8H manifest reviewed for this pack.

**It does not import the first pack.** The Step 14C governance rule is that a
governed pack may not statically import another governed pack, absolutely or
relatively. The AAD and PubMed metadata below are the same reviewed values the
petrolatum pack pins, because the same reviewed AAD locator lists both
ingredients and the same randomized study reports both components — but they
are copied here as literal governed constants, which is a reviewable act, not
reached through an import, which would make selecting one pack execute two.
A test proves the copies are identical to the reviewed originals by reading
that file as text rather than by importing it.

**One substance, two names.** Glycerin and glycerol are the same chemically
defined substance. They are declared here as one identity with one key, never
as two ingredients -- see :data:`SUBSTANCE_KEY` for why the key is ``glycerin``.
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

PACK_ID = "for_you.skin_care.glycerin_dry_skin.v1"

#: ``glycerin``, not ``glycerol``, and not both. This repository already
#: canonicalises this substance: ``app/domains/routines/ontology.py`` declares
#: the ingredient under the key ``glycerin`` with ``glycerol`` and ``glycerine``
#: recorded as its aliases, and gives its INCI name as "Glycerin". The first
#: governed pack follows the same convention -- its ``SUBSTANCE_KEY`` is
#: ``petrolatum``, the key that ontology uses too. Choosing ``glycerol`` here
#: would contradict an existing canonical decision and, worse, would be the
#: first step towards two identities for one molecule, which is precisely what
#: the substance-identity architecture exists to refuse.
SUBSTANCE_KEY = "glycerin"

#: Glycerol is a single molecule, so ``defined_substance`` -- unlike petrolatum,
#: which is a ``mixture``. This is the one identity field where the two packs
#: genuinely differ, and it differs because the chemistry differs.
IDENTITY_ENTITY_KIND = "defined_substance"
IDENTITY_NAME = "Glycerin"
#: "A name as printed in an official register or reference work" -- Glycerin is
#: recorded as a name of this compound in the reviewed government reference.
IDENTITY_NAME_NAMESPACE = "official_reference"
#: ``is_preferred`` in this repository marks the one name GlamGenius treats as
#: this entity's preferred name (``SubstanceIdentity.preferred`` guarantees
#: exactly one per entity). It is not a claim about which title the source
#: prefers -- the reference record's own title is recorded separately, below,
#: and is "Glycerol". The first pack models the same distinction: its preferred
#: name is "Petrolatum" while its source title is "Petrolatum [USP]".
IDENTITY_NAME_PREFERRED = True
IDENTITY_SOURCE_TYPE = "government_reference"
#: The title of the PubChem compound record itself.
IDENTITY_SOURCE_TITLE = "Glycerol"
#: A PubChem *compound* record (CID), which PubChem itself curates from its
#: contributed substance records. That is why the publisher is PubChem here,
#: where the first pack's substance-level (SID) record names its depositor.
IDENTITY_SOURCE_PUBLISHER = "PubChem"
IDENTITY_SOURCE_URL = "https://pubchem.ncbi.nlm.nih.gov/compound/753"
IDENTITY_SOURCE_EXTERNAL_ID = "753"
#: What anchors "glycerin and glycerol are one substance" to an authority
#: rather than to an assumption: the same compound record carries the preferred
#: name Glycerol, the synonym Glycerin, and one CAS registry number.
IDENTITY_SOURCE_PREFERRED_RECORD_NAME = "Glycerol"
IDENTITY_SOURCE_SYNONYM_NAME = "Glycerin"
IDENTITY_CAS_NUMBER = "56-81-5"
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
    "Glycerin is relevant to dry-skin care because dermatologist guidance includes it among "
    "ingredients to look for in creams or ointments for dry skin, and randomized dry-skin "
    "research found the glycerol component improved skin hydration."
)
EVIDENCE_SCOPE = (
    "Ingredient-level, non-medical applicability for a user who reports "
    "care_skin_usual_feel=often_dry_or_tight. It does not establish treatment of a diagnosed "
    "condition, concentration, whole-formula suitability, or the suitability of unrelated "
    "co-ingredients."
)
EVIDENCE_STRENGTH_RATIONALE = (
    "Current dermatologist guidance explicitly includes glycerin among cream or ointment "
    "ingredients for dry skin, and randomized dry-skin research found the glycerol component "
    "improved skin hydration. Moderate is used because the evidence is ingredient/component-"
    "level rather than an exact-product therapeutic trial, and that randomized study was not "
    "designed to evaluate therapeutic benefit."
)

#: The reviewed AAD locator lists the ingredients to look for in a cream or
#: ointment for dry skin, and glycerin is among them alongside petrolatum. Same
#: page, same locator, same metadata policy as the first pack -- copied, not
#: imported, and not re-inferred.
AAD_SOURCE_TYPE = "professional_consensus"
AAD_SOURCE_TITLE = "Dermatologists' top tips for relieving dry skin"
AAD_SOURCE_PUBLISHER = "American Academy of Dermatology Association"
AAD_SOURCE_URL = (
    "https://www.aad.org/public/everyday-care/skin-care-basics/dry/"
    "dermatologists-tips-relieve-dry-skin"
)
AAD_SOURCE_LOCATOR = "What skin care products are best for dry skin? / Ointment or cream"
#: The page reports "Last updated: 1/2/26" and states no publication date. An update
#: date is not a publication date, so this stays null and the update is recorded as a
#: version instead. Optional provenance is left absent when the source does not
#: establish it, rather than inferred into something that looks authoritative.
AAD_SOURCE_PUBLICATION_DATE = None
AAD_SOURCE_VERSION = "Last updated 2026-01-02"
#: Neither reviewed source states the territory its guidance applies to. "global" was
#: an inference, so it is null.
AAD_SOURCE_JURISDICTION = None
AAD_SOURCE_USE_NOTE = (
    "Public source reviewed through its canonical page for citation and verification; "
    "GlamGenius stores metadata and a locator, not reproduced AAD article text."
)

#: The same four-arm randomized crossover study the first pack cites. It is one
#: study reporting on two components; the reviewed reading taken here is the
#: glycerol one, and it is narrow: the glycerol component improved skin
#: hydration. Not that it treats dry skin, repairs barriers, prevents disease,
#: works at any concentration, works in any formulation, or outperforms other
#: moisturisers. The study was explicitly not designed to evaluate therapeutic
#: benefit.
PUBMED_SOURCE_TYPE = "peer_reviewed_research"
PUBMED_SOURCE_TITLE = (
    "Combined effects of glycerol and petrolatum in an emollient cream: A randomized, "
    "double-blind, crossover study in healthy volunteers with dry skin"
)
#: The PubMed record is the open citation location; the article itself carries
#: "© 2019 Wiley Periodicals, Inc." Naming the database as the publisher would
#: misattribute the work.
PUBMED_SOURCE_PUBLISHER = "Wiley Periodicals, Inc."
PUBMED_SOURCE_URL = "https://pubmed.ncbi.nlm.nih.gov/31532576/"
PUBMED_SOURCE_LOCATOR = "Abstract / Conclusions"
PUBMED_SOURCE_PUBLICATION_DATE = "2019-09-18"
PUBMED_SOURCE_VERSION = "PMID 31532576; DOI 10.1111/jocd.13163"
PUBMED_SOURCE_JURISDICTION = None
PUBMED_SOURCE_USE_NOTE = (
    "Bibliographic and abstract page used for citation and reviewer verification; no article "
    "full text is stored or reproduced."
)

SEMANTIC_RULE_ID = "for_you.semantic.skin_care.glycerin.dry_skin"
SEMANTIC_RULE_VERSION = "1"
SEMANTIC_SIGNAL = "supporting"

POLICY_ID = "for_you.policy.skin_care.glycerin.dry_skin.buy"
POLICY_VERSION = "1"
POLICY_SIGNAL_SET = "supporting_only"
POLICY_ACTION = "buy"

EXPLANATION_ID = "for_you.explanation.skin_care.glycerin.dry_skin.buy"
EXPLANATION_VERSION = "1"

#: Distinct from the first pack's reason key, and it must stay that way: Step
#: 14A's inventory refuses two packs that claim the same reason, because a
#: repository that cannot say which pack owns a reason cannot say which reason
#: a customer was shown.
REASON_KEY = "for_you.skin_care.glycerin.dry_skin.dermatologist_guidance"

#: The reviewed intent for the future customer sentence behind REASON_KEY.
#: Recorded here so the scope is reviewable and testable before any copy
#: exists; the wording is an original GlamGenius paraphrase, never AAD text
#: reproduced. No copy is wired in this milestone -- when it is, it lives in a
#: keyed string file like every other user-facing string.
FUTURE_REASON_INTENT = (
    "For dry skin, dermatologist guidance includes glycerin among ingredients to "
    "look for in a cream or ointment."
)

#: Claims the selected AAD citation does not support, and which therefore must
#: never appear in the customer reason attached to it.
#:
#: The hydration finding is the one to watch. It is *true* and it is *reviewed*
#: -- it is why this pack's evidence strength is moderate rather than lower --
#: but it belongs to the PubMed study, and the citation the customer is shown
#: is the AAD locator. Step 8F attaches one reason to one selected source, and
#: the two must say the same thing. Borrowing the study's finding to make the
#: dermatologist-guidance sentence sound stronger would be the exact failure
#: this guard exists to catch.
REASON_CLAIMS_OUT_OF_SCOPE = (
    "improves skin hydration",
    "improve skin hydration",
    "improves hydration",
    "improve hydration",
    "increases hydration",
    "increase hydration",
    "hydrates the skin",
    "hydrate the skin",
    "repairs the barrier",
    "repair the barrier",
    "reduces tewl",
    "reduce tewl",
    "transepidermal water loss",
    "tewl",
    "treats dry skin",
    "treat dry skin",
    "heals dry skin",
    "heal dry skin",
    "clinically proven",
    "safe",
    "safe for everyone",
    "recommended for everyone",
    "better than",
)


class GlycerinDrySkinKnowledgePackError(ValueError):
    """The published entry is not the exact evidence reviewed for Step 14D."""


def _fail(message: str) -> NoReturn:
    raise GlycerinDrySkinKnowledgePackError(message)


def _exact(entry: Mapping[str, object], field: str, expected: object) -> None:
    if entry.get(field) != expected:
        _fail(f"{field} is not the reviewed Step 14D value")


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
    detail, not scientific meaning, so reversing it must produce the same
    manifest and the same hash.
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
        raise GlycerinDrySkinKnowledgePackError(
            "the reviewed pack did not form a valid Step 8H manifest"
        ) from error
    return canonical


__all__ = [
    "FUTURE_REASON_INTENT",
    "PACK_ID",
    "REASON_CLAIMS_OUT_OF_SCOPE",
    "REASON_KEY",
    "GlycerinDrySkinKnowledgePackError",
    "build_release_manifest_from_published_entry",
]
