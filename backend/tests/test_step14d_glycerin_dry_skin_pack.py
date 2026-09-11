"""Step 14D — the second reviewed knowledge pack, and the first multi-pack proof.

Two things are under test here and they are worth keeping apart.

The first is **this pack's science**: that the identity is one substance under
the key this repository already canonicalises, that the profile fact is the
ordinary non-diagnostic care declaration, that the two reviewed source paths
carry exactly the metadata that was reviewed, and — the one most easily lost —
that the customer-facing reason stays inside what its own selected citation
supports. The hydration finding is real, reviewed, and the reason for this
pack's evidence strength; it is also *not* what the AAD locator says, and the
sentence attached to that locator may not borrow it.

The second is **the architecture**: that two governed packs coexist with
distinct identities, and that the generic Step 14C compiler selects one and
executes exactly one. That property is what makes a third pack cheap and a
thirtieth possible, and it is proved with a meta-path recorder rather than by
reading the tool's source.

Scientific qualification lives here rather than in the generic compiler's test
module on purpose. The compiler is generic; a pack owns its own science.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from app.domains.personal_decision_release.manifest import (
    canonical_manifest,
    manifest_content_hash,
    parse_release_manifest,
)
from app.domains.profile.registry import ATTRIBUTE_REGISTRY
from app.domains.routines.ontology import INGREDIENTS
from app.knowledge_packs import glycerin_dry_skin_v1 as pack
from app.knowledge_packs import petrolatum_dry_skin_v1 as first_pack
from app.knowledge_packs.inspection import (
    COMPILER_ATTRIBUTE,
    discover_pack_sources,
    inspect_packs,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
GENERIC_BUILDER = REPOSITORY_ROOT / "scripts" / "build_knowledge_pack_release.py"
PACK_SOURCE = BACKEND_ROOT / "app" / "knowledge_packs" / "glycerin_dry_skin_v1.py"
FIRST_PACK_SOURCE = BACKEND_ROOT / "app" / "knowledge_packs" / "petrolatum_dry_skin_v1.py"

GLYCERIN_PACK_ID = "for_you.skin_care.glycerin_dry_skin.v1"
PETROLATUM_PACK_ID = "for_you.skin_care.petrolatum_dry_skin.v1"

#: The petrolatum manifest hash reviewed since Step 8I. Adding a second pack
#: must not move it, and this module asserts that from the outside.
PETROLATUM_CONTENT_HASH = "be1fbbf8ae6435e18fdcfba86d85d7697a6257959bc2c6c75c452bcd434e75be"

#: Stable fixture identities, so the reported glycerin hash is reproducible.
#: They are deliberately the same generated identities the petrolatum golden
#: fixture uses: holding them equal means the two manifests differ only by the
#: reviewed content of the packs themselves, which is what a reviewer wants to
#: be looking at.
FIXTURE_CLAIM_KEY = "personal-applicability:skin_care:generated"
FIXTURE_AAD_SOURCE_KEY = "generated.aad.source"
FIXTURE_PUBMED_SOURCE_KEY = "generated.pubmed.source"


# ---------------------------------------------------------------------------
# The reviewed published entry
# ---------------------------------------------------------------------------
def _source(*, aad: bool, source_key: str | None = None) -> dict[str, object]:
    if aad:
        return {
            "source_id": str(uuid.uuid4()),
            "source_key": source_key or FIXTURE_AAD_SOURCE_KEY,
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
        "source_key": source_key or FIXTURE_PUBMED_SOURCE_KEY,
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
    claim_key: str = FIXTURE_CLAIM_KEY,
    aad_source_key: str = FIXTURE_AAD_SOURCE_KEY,
    pubmed_source_key: str = FIXTURE_PUBMED_SOURCE_KEY,
    reversed_sources: bool = False,
) -> dict[str, object]:
    sources = [
        _source(aad=True, source_key=aad_source_key),
        _source(aad=False, source_key=pubmed_source_key),
    ]
    if reversed_sources:
        sources.reverse()
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
        "sources": sources,
        "verification": {},
        "reviewed_by": "reviewer",
        "reviewed_at": "2026-09-07T00:00:00+00:00",
        "published_by": "publisher",
        "published_at": "2026-09-07T00:00:00+00:00",
        "supersedes_claim_id": None,
        "rejection_reason": None,
    }


@pytest.fixture()
def entry_file(tmp_path: Path) -> Path:
    path = tmp_path / "glycerin_entry.json"
    path.write_text(json.dumps(_valid_entry(), indent=2), encoding="utf-8")
    return path


@pytest.fixture()
def petrolatum_entry_file(tmp_path: Path) -> Path:
    from tests.test_step8i_first_production_knowledge_pack import (
        _valid_entry as petrolatum_entry,
    )

    path = tmp_path / "petrolatum_entry.json"
    path.write_text(json.dumps(petrolatum_entry(), indent=2), encoding="utf-8")
    return path


def _run_builder(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GENERIC_BUILDER), *argv],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


#: A fresh interpreter with a meta-path recorder in front of the import system,
#: reporting which governed pack modules were actually reached for. A recorder,
#: not a blocker: the question is what the tool asked for, not what it could be
#: prevented from getting.
_IMPORT_PROBE = '''
import json, sys, importlib.util

class Recorder:
    def __init__(self):
        self.seen = []

    def find_spec(self, name, path=None, target=None):
        if name.startswith("app.knowledge_packs.") and name != "app.knowledge_packs.inspection":
            self.seen.append(name)
        return None

recorder = Recorder()
sys.meta_path.insert(0, recorder)

spec = importlib.util.spec_from_file_location("builder_under_probe", {builder!r})
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

import contextlib, io
captured = io.StringIO()
with contextlib.redirect_stderr(captured), contextlib.redirect_stdout(io.StringIO()):
    status = builder.main({argv!r})
print(json.dumps({{
    "status": status,
    "imported": recorder.seen,
    "stderr": captured.getvalue(),
}}))
'''


def _probe_imports(argv: list[str]) -> dict:
    code = _IMPORT_PROBE.format(builder=str(GENERIC_BUILDER), argv=argv)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _executable_source(path: Path) -> str:
    """The file's code with every docstring and comment removed.

    A raw-text scan of a file whose docstring explains what it refuses to do
    finds those refusals and calls them violations. Parsing and re-emitting
    leaves only what actually runs.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body.pop(0)
            if not node.body:
                node.body.append(ast.Pass())
    return ast.unparse(ast.fix_missing_locations(tree))


def _module_constants(path: Path) -> dict[str, object]:
    """Module-scope literal constants, read from source without importing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            found[target.id] = ast.literal_eval(node.value)
        except ValueError:
            continue
    return found


# ---------------------------------------------------------------------------
# 1-2. Substance identity, and the glycerin/glycerol synonym authority
# ---------------------------------------------------------------------------
class TestSubstanceIdentity:
    def test_the_substance_key_is_the_repository_canonical_key(self) -> None:
        assert pack.SUBSTANCE_KEY == "glycerin"

    def test_the_key_matches_the_existing_canonical_ingredient_identity(self) -> None:
        # This repository already decided this substance's canonical key, in
        # the routines ontology. The pack follows that decision rather than
        # making a second one -- exactly as the first pack's key follows the
        # ontology's "petrolatum".
        ontology = {ingredient.key: ingredient for ingredient in INGREDIENTS}
        assert pack.SUBSTANCE_KEY in ontology
        assert first_pack.SUBSTANCE_KEY in ontology

    def test_glycerol_is_an_alias_of_glycerin_not_a_second_identity(self) -> None:
        # The synonym relationship is the whole reason one key is correct. If
        # the ontology ever promoted glycerol to its own ingredient, this pack
        # would be asserting an identity the repository no longer holds.
        ontology = {ingredient.key: ingredient for ingredient in INGREDIENTS}
        glycerin = ontology["glycerin"]
        assert "glycerol" in glycerin.aliases
        assert "glycerol" not in ontology, "glycerol must not exist as a separate ingredient"

    def test_the_pack_declares_one_substance_key_and_never_glycerol_as_a_key(self) -> None:
        constants = _module_constants(PACK_SOURCE)
        assert constants["SUBSTANCE_KEY"] == "glycerin"
        # "Glycerol" appears in the pack only as reference-record metadata and
        # in reviewed source prose -- never as an identity key of its own.
        assert constants["IDENTITY_SOURCE_PREFERRED_RECORD_NAME"] == "Glycerol"

    def test_the_entity_kind_is_a_defined_substance(self) -> None:
        # Glycerol is one molecule. Petrolatum is a mixture. This is the one
        # identity field where the two packs legitimately differ.
        assert pack.IDENTITY_ENTITY_KIND == "defined_substance"
        assert first_pack.IDENTITY_ENTITY_KIND == "mixture"

    def test_the_entity_kind_is_a_real_member_of_the_governed_vocabulary(self) -> None:
        from app.domains.substances.enums import ENTITY_KINDS, NAME_NAMESPACES

        assert pack.IDENTITY_ENTITY_KIND in ENTITY_KINDS
        assert pack.IDENTITY_NAME_NAMESPACE in NAME_NAMESPACES

    def test_the_preferred_name_is_the_label_facing_name(self) -> None:
        # is_preferred in this repository marks the one name GlamGenius treats
        # as the entity's own preferred name, not the source's record title.
        assert pack.IDENTITY_NAME == "Glycerin"
        assert pack.IDENTITY_NAME_PREFERRED is True
        assert pack.IDENTITY_SOURCE_TITLE == "Glycerol"

    def test_the_identity_authority_is_the_reviewed_pubchem_compound_record(self) -> None:
        assert pack.IDENTITY_SOURCE_TYPE == "government_reference"
        assert pack.IDENTITY_SOURCE_PUBLISHER == "PubChem"
        assert pack.IDENTITY_SOURCE_EXTERNAL_ID == "753"
        assert pack.IDENTITY_SOURCE_URL == "https://pubchem.ncbi.nlm.nih.gov/compound/753"
        assert pack.IDENTITY_SOURCE_EXTERNAL_ID in pack.IDENTITY_SOURCE_URL

    def test_the_synonym_relationship_is_anchored_to_that_record(self) -> None:
        assert pack.IDENTITY_SOURCE_PREFERRED_RECORD_NAME == "Glycerol"
        assert pack.IDENTITY_SOURCE_SYNONYM_NAME == "Glycerin"
        assert pack.IDENTITY_CAS_NUMBER == "56-81-5"

    def test_no_inci_authority_is_claimed(self) -> None:
        # An INCI namespace would be a claim about a register this pack did not
        # verify. The declared namespace is the reference record it did.
        constants = _module_constants(PACK_SOURCE)
        assert pack.IDENTITY_NAME_NAMESPACE == "official_reference"
        assert not any(key.startswith("IDENTITY_INCI") for key in constants)


# ---------------------------------------------------------------------------
# 3. The profile fact
# ---------------------------------------------------------------------------
class TestProfileApplicability:
    def test_the_exact_reviewed_profile_fact(self) -> None:
        assert pack.FACT_KEY == "care_skin_usual_feel"
        assert pack.FACT_OPERATOR == "equals_any"
        assert pack.FACT_VALUES == ("often_dry_or_tight",)

    def test_the_fact_is_an_existing_registered_attribute(self) -> None:
        assert pack.FACT_KEY in ATTRIBUTE_REGISTRY, "the pack must not add a profile attribute"

    def test_the_value_is_one_the_registry_actually_allows(self) -> None:
        spec = ATTRIBUTE_REGISTRY[pack.FACT_KEY]
        for value in pack.FACT_VALUES:
            assert value in spec.choices, value

    def test_it_reuses_the_first_packs_declaration_exactly(self) -> None:
        assert pack.FACT_KEY == first_pack.FACT_KEY
        assert pack.FACT_OPERATOR == first_pack.FACT_OPERATOR
        assert pack.FACT_VALUES == first_pack.FACT_VALUES

    @pytest.mark.parametrize(
        "forbidden",
        ["eczema", "atopic", "dermatitis", "xerosis", "symptom", "disease", "patient", "cure"],
    )
    def test_the_reviewed_strings_carry_no_diagnostic_vocabulary(self, forbidden: str) -> None:
        # Scoped to the strings that make claims, not the whole file: the scope
        # field legitimately *denies* treating a diagnosed condition, and a
        # raw-text scan would read that denial as the violation.
        for field in (
            pack.FUTURE_REASON_INTENT,
            pack.EVIDENCE_SUMMARY,
            pack.IDENTITY_NAME,
        ):
            assert forbidden not in field.lower(), (forbidden, field)

    def test_the_scope_denies_treatment_of_a_diagnosed_condition(self) -> None:
        # The one place "diagnosed" belongs is the sentence refusing it.
        assert "does not establish treatment of a diagnosed condition" in pack.EVIDENCE_SCOPE

    def test_the_reason_intent_is_a_declaration_not_a_condition(self) -> None:
        # The user context is an ordinary care declaration -- "my skin often
        # feels dry or tight" -- never a reported or inferred condition.
        assert "dry or tight" in "".join(pack.FACT_VALUES).replace("_", " ")


# ---------------------------------------------------------------------------
# 4. Evidence strength, summary and scope
# ---------------------------------------------------------------------------
class TestEvidence:
    def test_the_strength_is_moderate_and_never_strong(self) -> None:
        assert pack.EVIDENCE_STRENGTH == "moderate"

    def test_the_summary_is_the_reviewed_narrow_proposition(self) -> None:
        summary = pack.EVIDENCE_SUMMARY.lower()
        assert "glycerin" in summary
        assert "dermatologist guidance" in summary
        assert "improved skin hydration" in summary

    def test_the_summary_claims_no_treatment_or_superiority(self) -> None:
        summary = pack.EVIDENCE_SUMMARY.lower()
        for forbidden in (
            "treats", "cures", "prevents", "repairs all", "every concentration",
            "every formulation", "superior", "best",
        ):
            assert forbidden not in summary, forbidden

    def test_the_scope_states_every_reviewed_limitation(self) -> None:
        scope = pack.EVIDENCE_SCOPE.lower()
        assert "ingredient-level" in scope
        assert "non-medical" in scope
        assert "care_skin_usual_feel=often_dry_or_tight" in scope
        assert "concentration" in scope
        assert "whole-formula" in scope
        assert "co-ingredients" in scope
        assert "diagnosed" in scope

    def test_the_strength_rationale_names_both_reviewed_paths_and_the_limit(self) -> None:
        rationale = pack.EVIDENCE_STRENGTH_RATIONALE.lower()
        assert "dermatologist guidance" in rationale
        assert "glycerol component improved skin hydration" in rationale
        assert "ingredient/component-" in rationale
        assert "not" in rationale and "therapeutic benefit" in rationale


# ---------------------------------------------------------------------------
# 5-8. The two reviewed source paths
# ---------------------------------------------------------------------------
class TestReviewedSources:
    def test_exact_aad_metadata(self) -> None:
        assert pack.AAD_SOURCE_TYPE == "professional_consensus"
        assert pack.AAD_SOURCE_TITLE == "Dermatologists' top tips for relieving dry skin"
        assert pack.AAD_SOURCE_PUBLISHER == "American Academy of Dermatology Association"
        assert pack.AAD_SOURCE_LOCATOR == (
            "What skin care products are best for dry skin? / Ointment or cream"
        )
        assert pack.AAD_SOURCE_VERSION == "Last updated 2026-01-02"
        assert pack.AAD_SOURCE_URL.startswith("https://www.aad.org/")

    def test_exact_pubmed_metadata(self) -> None:
        assert pack.PUBMED_SOURCE_TYPE == "peer_reviewed_research"
        assert pack.PUBMED_SOURCE_TITLE.startswith(
            "Combined effects of glycerol and petrolatum in an emollient cream:"
        )
        assert pack.PUBMED_SOURCE_PUBLISHER == "Wiley Periodicals, Inc."
        assert pack.PUBMED_SOURCE_LOCATOR == "Abstract / Conclusions"
        assert pack.PUBMED_SOURCE_PUBLICATION_DATE == "2019-09-18"
        assert pack.PUBMED_SOURCE_VERSION == "PMID 31532576; DOI 10.1111/jocd.13163"
        assert pack.PUBMED_SOURCE_URL == "https://pubmed.ncbi.nlm.nih.gov/31532576/"

    def test_the_database_is_not_named_as_the_publisher(self) -> None:
        assert "PubMed" not in pack.PUBMED_SOURCE_PUBLISHER

    def test_no_optional_provenance_is_inferred(self) -> None:
        # An update date is not a publication date, and neither source states a
        # territory. Absent stays absent rather than becoming something that
        # merely looks authoritative.
        assert pack.AAD_SOURCE_PUBLICATION_DATE is None
        assert pack.AAD_SOURCE_JURISDICTION is None
        assert pack.PUBMED_SOURCE_JURISDICTION is None

    @pytest.mark.parametrize(
        "field",
        [
            "AAD_SOURCE_TYPE", "AAD_SOURCE_TITLE", "AAD_SOURCE_PUBLISHER", "AAD_SOURCE_URL",
            "AAD_SOURCE_LOCATOR", "AAD_SOURCE_PUBLICATION_DATE", "AAD_SOURCE_VERSION",
            "AAD_SOURCE_JURISDICTION", "AAD_SOURCE_USE_NOTE",
            "PUBMED_SOURCE_TYPE", "PUBMED_SOURCE_TITLE", "PUBMED_SOURCE_PUBLISHER",
            "PUBMED_SOURCE_URL", "PUBMED_SOURCE_LOCATOR", "PUBMED_SOURCE_PUBLICATION_DATE",
            "PUBMED_SOURCE_VERSION", "PUBMED_SOURCE_JURISDICTION", "PUBMED_SOURCE_USE_NOTE",
        ],
    )
    def test_the_shared_source_metadata_is_a_faithful_copy_of_the_reviewed_original(
        self, field: str
    ) -> None:
        # Copied as governed literals rather than imported. Reading the other
        # pack's *source text* proves the copy is faithful without either pack
        # depending on the other.
        mine = _module_constants(PACK_SOURCE)
        theirs = _module_constants(FIRST_PACK_SOURCE)
        assert field in mine and field in theirs, field
        assert mine[field] == theirs[field], field

    def test_exactly_two_evidence_paths_are_accepted(self) -> None:
        entry = _valid_entry()
        assert len(entry["sources"]) == 2
        manifest = pack.build_release_manifest_from_published_entry(entry)
        assert len(manifest["explanation_rules"]) == 1

    def test_a_third_source_is_refused(self) -> None:
        entry = _valid_entry()
        entry["sources"].append(_source(aad=True, source_key="extra.aad.source"))
        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)

    def test_a_single_source_is_refused(self) -> None:
        entry = _valid_entry()
        entry["sources"] = [_source(aad=True)]
        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)


# ---------------------------------------------------------------------------
# 9-12. The reviewed decision rules
# ---------------------------------------------------------------------------
class TestDecisionRules:
    def test_the_exact_semantic_rule(self) -> None:
        assert pack.SEMANTIC_RULE_ID == "for_you.semantic.skin_care.glycerin.dry_skin"
        assert pack.SEMANTIC_RULE_VERSION == "1"
        assert pack.SEMANTIC_SIGNAL == "supporting"
        (rule,) = pack.build_release_manifest_from_published_entry(
            _valid_entry()
        )["semantic_rules"]
        assert rule["rule_id"] == pack.SEMANTIC_RULE_ID
        assert rule["substance_key"] == "glycerin"
        assert rule["signal"] == "supporting"

    def test_the_exact_policy_rule(self) -> None:
        assert pack.POLICY_ID == "for_you.policy.skin_care.glycerin.dry_skin.buy"
        assert pack.POLICY_VERSION == "1"
        assert pack.POLICY_SIGNAL_SET == "supporting_only"
        assert pack.POLICY_ACTION == "buy"
        (policy,) = pack.build_release_manifest_from_published_entry(
            _valid_entry()
        )["policy_rules"]
        assert policy["signal_set"] == "supporting_only"
        assert policy["action"] == "buy"
        assert policy["has_identity_unresolved"] is False
        assert policy["has_identity_ambiguous"] is False
        assert policy["has_personal_evidence_gap"] is False

    def test_the_policy_binds_only_this_packs_semantic_rule(self) -> None:
        (policy,) = pack.build_release_manifest_from_published_entry(
            _valid_entry()
        )["policy_rules"]
        assert policy["semantic_rule_identities"] == [
            {"rule_id": pack.SEMANTIC_RULE_ID, "rule_version": pack.SEMANTIC_RULE_VERSION}
        ]

    def test_the_exact_explanation_rule(self) -> None:
        assert pack.EXPLANATION_ID == "for_you.explanation.skin_care.glycerin.dry_skin.buy"
        assert pack.EXPLANATION_VERSION == "1"
        (explanation,) = pack.build_release_manifest_from_published_entry(
            _valid_entry()
        )["explanation_rules"]
        assert explanation["action"] == "buy"
        assert explanation["semantic_rule_id"] == pack.SEMANTIC_RULE_ID
        assert explanation["policy_id"] == pack.POLICY_ID
        assert explanation["reason_key"] == pack.REASON_KEY

    def test_the_reason_key_is_unique_to_this_pack(self) -> None:
        assert pack.REASON_KEY == (
            "for_you.skin_care.glycerin.dry_skin.dermatologist_guidance"
        )
        assert pack.REASON_KEY != first_pack.REASON_KEY

    def test_every_governed_identifier_differs_from_the_first_pack(self) -> None:
        for field in (
            "PACK_ID", "SUBSTANCE_KEY", "REASON_KEY",
            "SEMANTIC_RULE_ID", "POLICY_ID", "EXPLANATION_ID",
        ):
            assert getattr(pack, field) != getattr(first_pack, field), field

    def test_the_signal_and_action_are_authored_constants_not_a_computation(self) -> None:
        # The BUY belongs to this pack's reviewed policy, bound to this pack's
        # one semantic rule. It is an authored fact, not a generic rule about
        # the word "supporting", so nothing in the module may branch on either
        # name to arrive at an action.
        constants = _module_constants(PACK_SOURCE)
        assert constants["SEMANTIC_SIGNAL"] == "supporting"
        assert constants["POLICY_ACTION"] == "buy"
        tree = ast.parse(_executable_source(PACK_SOURCE))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.If, ast.IfExp, ast.Match)):
                continue
            names = {
                child.id for child in ast.walk(node) if isinstance(child, ast.Name)
            }
            assert not names & {"SEMANTIC_SIGNAL", "POLICY_ACTION"}, ast.dump(node)[:120]


# ---------------------------------------------------------------------------
# 13-15. The selected-citation boundary
# ---------------------------------------------------------------------------
class TestSelectedCitationBoundary:
    def test_the_selected_citation_is_the_aad_source(self) -> None:
        entry = _valid_entry()
        (explanation,) = pack.build_release_manifest_from_published_entry(
            entry
        )["explanation_rules"]
        assert explanation["source_key"] == FIXTURE_AAD_SOURCE_KEY
        assert explanation["source_locator"] == pack.AAD_SOURCE_LOCATOR

    def test_the_selected_citation_is_never_the_pubmed_source(self) -> None:
        entry = _valid_entry()
        (explanation,) = pack.build_release_manifest_from_published_entry(
            entry
        )["explanation_rules"]
        assert explanation["source_key"] != FIXTURE_PUBMED_SOURCE_KEY

    def test_the_future_reason_intent_says_only_what_aad_supports(self) -> None:
        intent = pack.FUTURE_REASON_INTENT.lower()
        assert "dermatologist guidance" in intent
        assert "glycerin" in intent
        assert "cream or ointment" in intent

    @pytest.mark.parametrize("claim", pack.REASON_CLAIMS_OUT_OF_SCOPE)
    def test_the_reason_intent_carries_no_out_of_scope_claim(self, claim: str) -> None:
        assert claim not in pack.FUTURE_REASON_INTENT.lower(), claim

    def test_the_guard_actually_names_the_hydration_finding(self) -> None:
        # The guard is only worth having if it covers the claim most likely to
        # be borrowed. That claim is the study's, and it is not AAD's.
        out_of_scope = {claim.lower() for claim in pack.REASON_CLAIMS_OUT_OF_SCOPE}
        assert "improves skin hydration" in out_of_scope
        assert "increases hydration" in out_of_scope

    def test_the_guard_also_names_the_claims_no_source_here_supports(self) -> None:
        out_of_scope = {claim.lower() for claim in pack.REASON_CLAIMS_OUT_OF_SCOPE}
        for claim in ("treats dry skin", "heals dry skin", "safe", "recommended for everyone"):
            assert claim in out_of_scope, claim

    def test_the_hydration_finding_remains_in_the_evidence_basis(self) -> None:
        # It is not censored -- it is reviewed evidence, it is why the strength
        # is moderate, and it stays in the summary and the rationale. It simply
        # does not travel to the sentence attached to the AAD locator.
        assert "improved skin hydration" in pack.EVIDENCE_SUMMARY.lower()
        assert "improved skin hydration" in pack.EVIDENCE_STRENGTH_RATIONALE.lower()
        assert "hydration" not in pack.FUTURE_REASON_INTENT.lower()

    def test_the_reason_intent_never_advises_the_person(self) -> None:
        intent = pack.FUTURE_REASON_INTENT.lower()
        for forbidden in ("you should", "your skin", "we recommend", "buy this", "apply"):
            assert forbidden not in intent, forbidden


# ---------------------------------------------------------------------------
# 16. Source order is immaterial
# ---------------------------------------------------------------------------
class TestSourceOrderIsImmaterial:
    def test_reversed_source_order_produces_the_same_manifest(self) -> None:
        forward = pack.build_release_manifest_from_published_entry(_valid_entry())
        backward = pack.build_release_manifest_from_published_entry(
            _valid_entry(reversed_sources=True)
        )
        assert forward == backward

    def test_reversed_source_order_produces_the_same_hash(self) -> None:
        forward = manifest_content_hash(
            parse_release_manifest(
                pack.build_release_manifest_from_published_entry(_valid_entry())
            )
        )
        backward = manifest_content_hash(
            parse_release_manifest(
                pack.build_release_manifest_from_published_entry(
                    _valid_entry(reversed_sources=True)
                )
            )
        )
        assert forward == backward

    def test_reversed_source_order_still_selects_the_aad_citation(self) -> None:
        (explanation,) = pack.build_release_manifest_from_published_entry(
            _valid_entry(reversed_sources=True)
        )["explanation_rules"]
        assert explanation["source_key"] == FIXTURE_AAD_SOURCE_KEY


# ---------------------------------------------------------------------------
# 17. The compiler fails closed on every reviewed boundary
# ---------------------------------------------------------------------------
class TestCompilerFailsClosed:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("review_status", "draft"),
            ("claim_status", "refuted"),
            ("category", "hair_care"),
            ("domain", "hair_care"),
            ("substance_key", "glycerol"),
            ("substance_key", "petrolatum"),
            ("subject_type", "product"),
            ("claim_type", "substance_safety"),
            ("evidence_tier", "anecdotal"),
            ("ai_generated", True),
            ("evidence_strength", "strong"),
            ("evidence_strength", "weak"),
            ("claim_version", 2),
            ("summary", "Glycerin treats dry skin."),
            ("scope", "Anything goes."),
            ("strength_rationale", "Because it seems right."),
        ],
    )
    def test_a_changed_entry_field_is_refused(self, field: str, value: object) -> None:
        entry = _valid_entry()
        entry[field] = value
        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)

    def test_the_substance_key_glycerol_is_refused_specifically(self) -> None:
        # Worth its own test: "glycerol" is the same molecule and the wrong
        # key. Accepting it would be the beginning of two identities.
        entry = _valid_entry()
        entry["substance_key"] = "glycerol"
        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("fact_key", "care_skin_sensitivity"),
            ("operator", "equals"),
            ("values", ["comfortable"]),
            ("values", ["often_oily"]),
            ("values", ["often_dry_or_tight", "often_oily"]),
        ],
    )
    def test_a_changed_condition_is_refused(self, field: str, value: object) -> None:
        entry = _valid_entry()
        entry["conditions"][0][field] = value
        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)

    def test_extra_condition_fields_are_refused(self) -> None:
        entry = _valid_entry()
        entry["conditions"][0]["extra"] = "x"
        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)

    def test_two_conditions_are_refused(self) -> None:
        entry = _valid_entry()
        entry["conditions"].append(dict(entry["conditions"][0]))
        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize(
        "field",
        [
            "source_type", "title", "publisher", "canonical_url", "locator",
            "publication_date", "version_or_revision", "jurisdiction", "status",
            "license_or_use_note",
        ],
    )
    def test_a_changed_aad_source_field_is_refused(self, field: str) -> None:
        entry = _valid_entry()
        entry["sources"][0][field] = "changed"
        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize(
        "field",
        [
            "source_type", "title", "publisher", "canonical_url", "locator",
            "publication_date", "version_or_revision", "jurisdiction", "status",
            "license_or_use_note",
        ],
    )
    def test_a_changed_pubmed_source_field_is_refused(self, field: str) -> None:
        entry = _valid_entry()
        entry["sources"][1][field] = "changed"
        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)

    def test_an_inferred_aad_publication_date_is_refused(self) -> None:
        entry = _valid_entry()
        entry["sources"][0]["publication_date"] = "2026-01-02"
        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)

    def test_an_inferred_jurisdiction_is_refused(self) -> None:
        entry = _valid_entry()
        entry["sources"][0]["jurisdiction"] = "global"
        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)

    def test_a_blank_source_key_is_refused(self) -> None:
        entry = _valid_entry()
        entry["sources"][0]["source_key"] = "   "
        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize("claim_key", ["", "   ", None, 7])
    def test_a_missing_claim_key_is_refused(self, claim_key: object) -> None:
        entry = _valid_entry()
        entry["claim_key"] = claim_key
        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize("entry", ["", 7, None, [], "a string"])
    def test_a_non_object_entry_is_refused(self, entry: object) -> None:
        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(entry)

    def test_the_first_packs_entry_is_refused_by_this_pack(self) -> None:
        # The strongest boundary test there is: two packs, one compiler each,
        # and neither will compile the other's reviewed evidence.
        from tests.test_step8i_first_production_knowledge_pack import (
            _valid_entry as petrolatum_entry,
        )

        with pytest.raises(pack.GlycerinDrySkinKnowledgePackError):
            pack.build_release_manifest_from_published_entry(petrolatum_entry())

    def test_this_packs_entry_is_refused_by_the_first_pack(self) -> None:
        with pytest.raises(first_pack.FirstProductionKnowledgePackError):
            first_pack.build_release_manifest_from_published_entry(_valid_entry())


# ---------------------------------------------------------------------------
# 18. The compiled manifest is a valid Step 8H manifest
# ---------------------------------------------------------------------------
class TestCompiledManifest:
    def test_it_parses_through_the_step_8h_authority(self) -> None:
        manifest = pack.build_release_manifest_from_published_entry(_valid_entry())
        parsed = parse_release_manifest(manifest)
        assert len(parsed.semantic_rules) == 1
        assert len(parsed.policy_rules) == 1
        assert len(parsed.explanation_rules) == 1

    def test_the_emitted_document_is_already_canonical(self) -> None:
        manifest = pack.build_release_manifest_from_published_entry(_valid_entry())
        assert manifest == canonical_manifest(parse_release_manifest(manifest))

    def test_compilation_is_deterministic(self) -> None:
        first = pack.build_release_manifest_from_published_entry(_valid_entry())
        second = pack.build_release_manifest_from_published_entry(_valid_entry())
        assert first == second
        assert manifest_content_hash(parse_release_manifest(first)) == manifest_content_hash(
            parse_release_manifest(second)
        )

    def test_the_manifest_hash_differs_from_the_first_packs(self) -> None:
        from tests.test_step8i_first_production_knowledge_pack import (
            _valid_entry as petrolatum_entry,
        )

        mine = manifest_content_hash(
            parse_release_manifest(
                pack.build_release_manifest_from_published_entry(_valid_entry())
            )
        )
        theirs = manifest_content_hash(
            parse_release_manifest(
                first_pack.build_release_manifest_from_published_entry(petrolatum_entry())
            )
        )
        assert mine != theirs
        assert theirs == PETROLATUM_CONTENT_HASH


# ---------------------------------------------------------------------------
# The two-pack inventory, on the real committed files
# ---------------------------------------------------------------------------
class TestTwoPackInventory:
    """That these two packs coexist with distinct identities.

    Step 14D proved it when they were the whole inventory. Step 14E froze a
    five-pack roster, so "the inventory is exactly two" is no longer true and
    the exact-count authority moved to
    ``tests/test_step14e_skin_care_v1_roster.py`` and
    ``tests/test_knowledge_pack_inspection.py``.

    What this class asserted is unchanged in substance and still worth
    holding: both of *these* packs are present, valid, and distinct from each
    other. The assertions are scoped to that pair rather than to the size of
    the inventory around them.
    """

    def test_the_committed_inventory_is_valid_and_contains_both_packs(self) -> None:
        result = inspect_packs()
        assert result.errors == ()
        assert result.ok is True
        ids = {descriptor.pack_id for descriptor in result.packs}
        assert {GLYCERIN_PACK_ID, PETROLATUM_PACK_ID} <= ids

    def test_both_pack_ids_are_the_reviewed_ones_and_are_distinct(self) -> None:
        ids = [descriptor.pack_id for descriptor in inspect_packs().packs]
        assert GLYCERIN_PACK_ID in ids
        assert PETROLATUM_PACK_ID in ids
        assert GLYCERIN_PACK_ID != PETROLATUM_PACK_ID
        assert len(set(ids)) == len(ids), "every committed pack id must be unique"

    def test_both_reason_keys_are_distinct(self) -> None:
        keys = [descriptor.reason_key for descriptor in inspect_packs().packs]
        assert len(set(keys)) == len(keys), "every committed reason key must be unique"
        assert pack.REASON_KEY in keys
        assert first_pack.REASON_KEY in keys
        assert pack.REASON_KEY != first_pack.REASON_KEY

    def test_both_packs_declare_a_valid_compiler(self) -> None:
        for descriptor in inspect_packs().packs:
            assert descriptor.compiler_name == COMPILER_ATTRIBUTE

    def test_the_ci_governance_command_passes_with_both_packs_present(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/inspect_knowledge_packs.py", "--json"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        payload = json.loads(completed.stdout)
        assert payload["status"] == "ok"
        assert payload["errors"] == []
        reported = {entry["pack_id"] for entry in payload["packs"]}
        assert {GLYCERIN_PACK_ID, PETROLATUM_PACK_ID} <= reported
        assert payload["pack_count"] == len(payload["packs"])

    def test_the_new_pack_is_reported_with_its_real_identity(self) -> None:
        (descriptor,) = (
            d for d in inspect_packs().packs if d.pack_id == GLYCERIN_PACK_ID
        )
        assert descriptor.module == "app.knowledge_packs.glycerin_dry_skin_v1"
        assert descriptor.domain == pack.DOMAIN
        assert descriptor.category == pack.CATEGORY
        assert descriptor.reason_key == pack.REASON_KEY


# ---------------------------------------------------------------------------
# The generic compiler selects one pack and executes exactly one
# ---------------------------------------------------------------------------
class TestGenericCompilerIsolation:
    def test_the_generic_builder_compiles_the_new_pack(self, entry_file: Path) -> None:
        completed = _run_builder("--pack-id", GLYCERIN_PACK_ID, str(entry_file))
        assert completed.returncode == 0, completed.stderr
        assert "content_hash=" in completed.stdout

    def test_the_generic_builder_still_compiles_the_first_pack(
        self, petrolatum_entry_file: Path
    ) -> None:
        completed = _run_builder("--pack-id", PETROLATUM_PACK_ID, str(petrolatum_entry_file))
        assert completed.returncode == 0, completed.stderr
        assert PETROLATUM_CONTENT_HASH in completed.stdout

    def test_the_generic_builder_output_equals_this_packs_own_compiler(
        self, entry_file: Path, tmp_path: Path
    ) -> None:
        output = tmp_path / "manifest.json"
        completed = _run_builder(
            "--pack-id", GLYCERIN_PACK_ID, str(entry_file), "--output", str(output)
        )
        assert completed.returncode == 0, completed.stderr
        emitted = json.loads(output.read_text(encoding="utf-8"))
        entry = json.loads(entry_file.read_text(encoding="utf-8"))
        assert emitted == pack.build_release_manifest_from_published_entry(entry)

    def test_selecting_glycerin_executes_only_glycerin(self, entry_file: Path) -> None:
        result = _probe_imports(["--pack-id", GLYCERIN_PACK_ID, str(entry_file)])
        assert result["status"] == 0, result["stderr"]
        assert result["imported"] == ["app.knowledge_packs.glycerin_dry_skin_v1"]

    def test_selecting_petrolatum_executes_only_petrolatum(
        self, petrolatum_entry_file: Path
    ) -> None:
        result = _probe_imports(["--pack-id", PETROLATUM_PACK_ID, str(petrolatum_entry_file)])
        assert result["status"] == 0, result["stderr"]
        assert result["imported"] == ["app.knowledge_packs.petrolatum_dry_skin_v1"]

    def test_an_unknown_pack_id_executes_neither(self, entry_file: Path) -> None:
        result = _probe_imports(["--pack-id", "for_you.skin_care.nothing.v1", str(entry_file)])
        assert result["status"] != 0
        assert result["imported"] == []

    def test_the_generic_builder_was_not_taught_about_glycerin(self) -> None:
        # If the tool needed a pack-specific change to recognise the second
        # pack, Step 14C was not generic and this milestone proved the opposite
        # of what it claims.
        source = _executable_source(GENERIC_BUILDER).lower()
        for forbidden in ("glycerin", "glycerol", "petrolatum"):
            assert forbidden not in source, forbidden

    def test_the_generic_builder_holds_no_pack_registry(self) -> None:
        # It may name the inspector -- that is infrastructure, and asking it
        # what exists is the whole design. What it may not name is a pack.
        source = _executable_source(GENERIC_BUILDER)
        for path in discover_pack_sources():
            assert f"app.knowledge_packs.{path.stem}" not in source, path.stem
        assert GLYCERIN_PACK_ID not in source
        assert PETROLATUM_PACK_ID not in source
        assert source.count("import_module") == 1

    def test_the_refusal_for_an_unknown_id_lists_both_real_packs(
        self, entry_file: Path
    ) -> None:
        completed = _run_builder(
            "--pack-id", "for_you.skin_care.nothing.v1", str(entry_file)
        )
        assert completed.returncode != 0
        assert GLYCERIN_PACK_ID in completed.stderr
        assert PETROLATUM_PACK_ID in completed.stderr


# ---------------------------------------------------------------------------
# Governance: no cross-pack import, and the pack stays inert
# ---------------------------------------------------------------------------
class TestNoCrossPackImport:
    def test_the_new_pack_imports_no_other_governed_pack(self) -> None:
        source = PACK_SOURCE.read_text(encoding="utf-8")
        assert "petrolatum_dry_skin_v1" not in source
        for forbidden in (
            "import app.knowledge_packs.petrolatum_dry_skin_v1",
            "from app.knowledge_packs.petrolatum_dry_skin_v1",
            "from app.knowledge_packs import petrolatum",
            "from .petrolatum",
            "from . import petrolatum",
            "from ..knowledge_packs.petrolatum",
        ):
            assert forbidden not in source, forbidden

    def test_the_step_14c_static_rule_passes_for_every_committed_pack(self) -> None:
        # The merged rule itself, over the real inventory, now that there are
        # two files that could reach for each other.
        from tests.test_generic_knowledge_pack_compiler import _cross_pack_imports

        checked = 0
        for path in discover_pack_sources():
            own = f"app.knowledge_packs.{path.stem}"
            offenders = _cross_pack_imports(path.read_text(encoding="utf-8"), own_module=own)
            assert offenders == [], (path.name, offenders)
            checked += 1
        # Both of these packs must be among what was checked. The exact size
        # of the committed roster is Step 14E's authority, not this module's.
        stems = {path.stem for path in discover_pack_sources()}
        assert {"glycerin_dry_skin_v1", "petrolatum_dry_skin_v1"} <= stems
        assert checked == len(stems)

    def test_the_only_application_import_is_the_manifest_authority(self) -> None:
        tree = ast.parse(PACK_SOURCE.read_text(encoding="utf-8"))
        app_imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
                app_imports.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app."):
                        app_imports.add(alias.name)
        assert app_imports == {"app.domains.personal_decision_release.manifest"}


class TestInertness:
    @pytest.mark.parametrize(
        "forbidden",
        [
            "requests", "httpx", "urllib", "socket", "aiohttp",
            "sqlalchemy", "asyncpg", "AsyncSession", "select(", "create_engine",
            "open(", "Path(", "os.environ", "getenv", "subprocess",
            "supabase", "sentry", "gemini", "googleapis", "render.com",
            # Release verbs in call form. Bare "publish" would match the wholly
            # legitimate source field "publisher".
            "activate(", "deactivate(", "publish(", "prepare(", "publish_release",
        ],
    )
    def test_the_pack_reaches_for_nothing_at_import_time(self, forbidden: str) -> None:
        assert forbidden not in _executable_source(PACK_SOURCE), forbidden

    def test_module_scope_runs_no_calls(self) -> None:
        tree = ast.parse(PACK_SOURCE.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
                continue
            for child in ast.walk(node):
                assert not isinstance(child, ast.Call), ast.dump(node)[:120]

    def test_importing_the_package_root_imports_no_pack(self) -> None:
        code = (
            "import sys, app.knowledge_packs; "
            "print([m for m in sys.modules if m.startswith('app.knowledge_packs.')])"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "[]"
