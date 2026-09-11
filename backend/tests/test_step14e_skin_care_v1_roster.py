"""Step 14E — the frozen Skin-Care V1 roster, and the closure of Step 14.

Five governed packs, and this module is the authority that says so. It has
four jobs.

**Qualify the three new packs scientifically.** Identity, applicability,
evidence metadata, and — the one most easily lost — that each customer-facing
reason stays inside what its own selected citation supports. Two of the three
sit next to bodies of literature they deliberately do not rely on (fragrance
allergy; retinoid pregnancy advice), and the guards here exist because those
are precisely the claims most likely to leak from source into sentence.

**Prove the roster is exactly five.** Not at least five. An inventory that had
silently lost a pack would satisfy a minimum, and losing a pack is the failure
the inventory exists to catch.

**Prove the machinery is still generic.** The Step 14C builder compiles all
five without a line about any of them, and selecting one executes exactly one.

**Prove the engine still refuses to guess.** A state carrying two of these
packs' semantic rules matches no reviewed policy, and that is a governed
outcome rather than a defect. Nothing here adds combination inference, and a
test asserts nothing anywhere declares one.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import uuid
from pathlib import Path
from types import ModuleType

import pytest
from app.domains.personal_applicability.schema import MAX_PERSONAL_APPLICABILITY_CONDITIONS
from app.domains.personal_applicability.service import PERSONAL_APPLICABILITY_STRENGTHS
from app.domains.personal_decision_aggregation import PersonalSignalSet
from app.domains.personal_decision_policy import (
    PersonalDecisionAction,
    PersonalDecisionPolicyCategory,
    PersonalDecisionPolicyRule,
    build_policy_index,
)
from app.domains.personal_decision_release.manifest import (
    canonical_manifest,
    manifest_content_hash,
    parse_release_manifest,
)
from app.domains.personal_lens.service import SKIN_BODY_FACT_KEYS
from app.domains.profile.registry import ATTRIBUTE_REGISTRY
from app.domains.routines.ontology import INGREDIENTS
from app.domains.substances.enums import ENTITY_KINDS, NAME_NAMESPACES
from app.knowledge_packs import fragrance_dry_skin_v1 as fragrance
from app.knowledge_packs import glycerin_dry_skin_v1 as glycerin
from app.knowledge_packs import petrolatum_dry_skin_v1 as petrolatum
from app.knowledge_packs import retinol_dry_skin_v1 as retinol
from app.knowledge_packs import salicylic_acid_oily_skin_v1 as salicylic
from app.knowledge_packs.inspection import (
    COMPILER_ATTRIBUTE,
    discover_pack_sources,
    inspect_packs,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
PACK_DIRECTORY = BACKEND_ROOT / "app" / "knowledge_packs"
GENERIC_BUILDER = REPOSITORY_ROOT / "scripts" / "build_knowledge_pack_release.py"
FREEZE_DOCUMENT = REPOSITORY_ROOT / "docs" / "architecture" / "SKIN_CARE_V1_KNOWLEDGE_ROSTER.md"

# ---------------------------------------------------------------------------
# The frozen roster
# ---------------------------------------------------------------------------
#: The complete Skin-Care V1 roster. Five, exactly. Adding a sixth pack is a
#: knowledge-operations decision with its own review, not something that
#: happens by dropping a file into the package.
REVIEWED_ROSTER: tuple[tuple[str, ModuleType], ...] = (
    ("app.knowledge_packs.petrolatum_dry_skin_v1", petrolatum),
    ("app.knowledge_packs.glycerin_dry_skin_v1", glycerin),
    ("app.knowledge_packs.fragrance_dry_skin_v1", fragrance),
    ("app.knowledge_packs.retinol_dry_skin_v1", retinol),
    ("app.knowledge_packs.salicylic_acid_oily_skin_v1", salicylic),
)
REVIEWED_PACK_COUNT = 5
REVIEWED_MODULES = frozenset(module for module, _ in REVIEWED_ROSTER)
NEW_PACKS: tuple[ModuleType, ...] = (fragrance, retinol, salicylic)

#: Frozen since their own milestones. Adding three packs must not move either.
PETROLATUM_CONTENT_HASH = "be1fbbf8ae6435e18fdcfba86d85d7697a6257959bc2c6c75c452bcd434e75be"
GLYCERIN_CONTENT_HASH = "bddeafc51b92ecdbe8d204a819cea390babeb72953afd8f25fe1ce9a54ff5a3f"

#: Stable fixture identities, so every reported hash is reproducible, and the
#: same ones the two merged packs' golden fixtures use — so five manifests
#: differ only by the reviewed content of the packs themselves.
FIXTURE_CLAIM_KEY = "personal-applicability:skin_care:generated"
FIXTURE_AAD_SOURCE_KEY = "generated.aad.source"
FIXTURE_SECOND_SOURCE_KEY = {
    "app.knowledge_packs.retinol_dry_skin_v1": "generated.pubmed.source",
    "app.knowledge_packs.salicylic_acid_oily_skin_v1": "generated.delphi.source",
}


# ---------------------------------------------------------------------------
# Fixture mechanics, shared so three near-identical builders do not appear
# ---------------------------------------------------------------------------
def _source(pack: ModuleType, prefix: str, source_key: str) -> dict[str, object]:
    """One reviewed source path, read off the pack's own declared constants."""
    return {
        "source_id": str(uuid.uuid4()),
        "source_key": source_key,
        "source_type": getattr(pack, f"{prefix}_SOURCE_TYPE"),
        "title": getattr(pack, f"{prefix}_SOURCE_TITLE"),
        "publisher": getattr(pack, f"{prefix}_SOURCE_PUBLISHER"),
        "canonical_url": getattr(pack, f"{prefix}_SOURCE_URL"),
        "locator": getattr(pack, f"{prefix}_SOURCE_LOCATOR"),
        "publication_date": getattr(pack, f"{prefix}_SOURCE_PUBLICATION_DATE"),
        "version_or_revision": getattr(pack, f"{prefix}_SOURCE_VERSION"),
        "jurisdiction": getattr(pack, f"{prefix}_SOURCE_JURISDICTION"),
        "status": "active",
        "license_or_use_note": getattr(pack, f"{prefix}_SOURCE_USE_NOTE"),
        "reviewed_by": "reviewer",
        "reviewed_at": "2026-09-07T00:00:00+00:00",
    }


def _conditions(pack: ModuleType) -> list[dict[str, object]]:
    if pack is salicylic:
        return [
            {
                "fact_key": pack.FEEL_FACT_KEY,
                "operator": pack.FACT_OPERATOR,
                "values": list(pack.FEEL_FACT_VALUES),
            },
            {
                "fact_key": pack.SENSITIVITY_FACT_KEY,
                "operator": pack.FACT_OPERATOR,
                "values": list(pack.SENSITIVITY_FACT_VALUES),
            },
        ]
    return [
        {
            "fact_key": pack.FACT_KEY,
            "operator": pack.FACT_OPERATOR,
            "values": list(pack.FACT_VALUES),
        }
    ]


def _sources(pack: ModuleType) -> list[dict[str, object]]:
    if pack is fragrance:
        return [_source(pack, "AAD", FIXTURE_AAD_SOURCE_KEY)]
    if pack is retinol:
        return [
            _source(pack, "AAD", FIXTURE_AAD_SOURCE_KEY),
            _source(pack, "PUBMED", "generated.pubmed.source"),
        ]
    if pack is salicylic:
        return [
            _source(pack, "AAD", FIXTURE_AAD_SOURCE_KEY),
            _source(pack, "DELPHI", "generated.delphi.source"),
        ]
    raise AssertionError(f"no fixture sources defined for {pack.__name__}")


def _valid_entry(
    pack: ModuleType,
    *,
    claim_key: str = FIXTURE_CLAIM_KEY,
    reversed_sources: bool = False,
    reversed_conditions: bool = False,
) -> dict[str, object]:
    sources = _sources(pack)
    if reversed_sources:
        sources.reverse()
    conditions = _conditions(pack)
    if reversed_conditions:
        conditions.reverse()
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
        "conditions": conditions,
        "sources": sources,
        "verification": {},
        "reviewed_by": "reviewer",
        "reviewed_at": "2026-09-07T00:00:00+00:00",
        "published_by": "publisher",
        "published_at": "2026-09-07T00:00:00+00:00",
        "supersedes_claim_id": None,
        "rejection_reason": None,
    }


def _pack_error(pack: ModuleType) -> type[Exception]:
    (name,) = (n for n in dir(pack) if n.endswith("KnowledgePackError"))
    return getattr(pack, name)


def _pack_source(pack: ModuleType) -> Path:
    return PACK_DIRECTORY / f"{pack.__name__.rsplit('.', 1)[1]}.py"


def _executable_source(path: Path) -> str:
    """The file's code with every docstring removed.

    A raw-text scan of a file whose docstring explains what it refuses to do
    finds those refusals and calls them violations.
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


def _ontology() -> dict[str, object]:
    return {ingredient.key: ingredient for ingredient in INGREDIENTS}


def _run_builder(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GENERIC_BUILDER), *argv],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


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
print(json.dumps({{"status": status, "imported": recorder.seen}}))
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


@pytest.fixture()
def entry_files(tmp_path: Path) -> dict[str, Path]:
    """One deterministic published entry per pack, on disk."""
    from tests.test_step8i_first_production_knowledge_pack import (
        _valid_entry as petrolatum_entry,
    )
    from tests.test_step14d_glycerin_dry_skin_pack import _valid_entry as glycerin_entry

    written: dict[str, Path] = {}
    for module_name, pack in REVIEWED_ROSTER:
        if pack is petrolatum:
            payload = petrolatum_entry()
        elif pack is glycerin:
            payload = glycerin_entry()
        else:
            payload = _valid_entry(pack)
        path = tmp_path / f"{module_name.rsplit('.', 1)[1]}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        written[pack.PACK_ID] = path
    return written


# ---------------------------------------------------------------------------
# The roster itself
# ---------------------------------------------------------------------------
class TestTheFrozenRoster:
    def test_the_committed_inventory_is_exactly_the_reviewed_five(self) -> None:
        result = inspect_packs()
        assert result.errors == ()
        assert result.ok is True
        assert len(result.packs) == REVIEWED_PACK_COUNT
        assert {descriptor.module for descriptor in result.packs} == REVIEWED_MODULES

    def test_there_is_no_sixth_pack_on_disk(self) -> None:
        # The inventory is what the inspector discovers, so a sixth file would
        # have to be caught here rather than by the roster tuple above.
        discovered = {f"app.knowledge_packs.{path.stem}" for path in discover_pack_sources()}
        assert discovered == REVIEWED_MODULES

    def test_every_pack_id_is_unique(self) -> None:
        ids = [descriptor.pack_id for descriptor in inspect_packs().packs]
        assert len(set(ids)) == REVIEWED_PACK_COUNT
        assert set(ids) == {pack.PACK_ID for _, pack in REVIEWED_ROSTER}

    def test_every_reason_key_is_unique(self) -> None:
        keys = [descriptor.reason_key for descriptor in inspect_packs().packs]
        assert len(set(keys)) == REVIEWED_PACK_COUNT
        assert set(keys) == {pack.REASON_KEY for _, pack in REVIEWED_ROSTER}

    def test_every_semantic_policy_and_explanation_id_is_unique(self) -> None:
        for field in ("SEMANTIC_RULE_ID", "POLICY_ID", "EXPLANATION_ID", "SUBSTANCE_KEY"):
            values = [getattr(pack, field) for _, pack in REVIEWED_ROSTER]
            assert len(set(values)) == REVIEWED_PACK_COUNT, field

    def test_every_pack_declares_a_valid_compiler(self) -> None:
        for descriptor in inspect_packs().packs:
            assert descriptor.compiler_name == COMPILER_ATTRIBUTE

    def test_the_ci_governance_command_reports_exactly_five(self) -> None:
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
        assert payload["pack_count"] == REVIEWED_PACK_COUNT

    def test_the_roster_covers_both_directions_and_both_actions(self) -> None:
        # The point of choosing these five: V1 exercises supporting and
        # cautionary, BUY and SKIP, dry and oily, one- and two-fact
        # applicability. A roster that only ever said BUY would not have
        # tested the machinery at all.
        signals = {pack.SEMANTIC_SIGNAL for _, pack in REVIEWED_ROSTER}
        actions = {pack.POLICY_ACTION for _, pack in REVIEWED_ROSTER}
        assert signals == {"supporting", "cautionary"}
        assert actions == {"buy", "skip"}

    @pytest.mark.parametrize(("module_name", "pack"), REVIEWED_ROSTER)
    def test_every_pack_declares_the_step_14a_contract(
        self, module_name: str, pack: ModuleType
    ) -> None:
        assert pack.PACK_ID
        assert pack.DOMAIN == "skin_care"
        assert pack.CATEGORY == "skin_care"
        assert pack.REASON_KEY
        assert callable(getattr(pack, COMPILER_ATTRIBUTE))


# ---------------------------------------------------------------------------
# Identity, for each new pack
# ---------------------------------------------------------------------------
class TestFragranceIdentity:
    def test_the_canonical_key_is_the_repository_key(self) -> None:
        assert fragrance.SUBSTANCE_KEY == "fragrance"
        assert "fragrance" in _ontology()

    def test_the_existing_ontology_still_agrees(self) -> None:
        entry = _ontology()["fragrance"]
        assert entry.display_name == "Fragrance"
        assert entry.inci_name == "Parfum"
        for alias in ("parfum", "perfume", "aroma"):
            assert alias in entry.aliases, alias

    def test_the_label_name_matches_the_ontology(self) -> None:
        assert _ontology()["fragrance"].inci_name == fragrance.IDENTITY_LABEL_NAME

    def test_it_is_a_mixture_and_never_a_defined_substance(self) -> None:
        # A fragrance formula can be a complex mixture declared as one label
        # word. Calling it a defined substance would assert a molecule.
        assert fragrance.IDENTITY_ENTITY_KIND == "mixture"
        assert fragrance.IDENTITY_ENTITY_KIND in ENTITY_KINDS

    def test_no_cas_number_and_no_external_id_are_invented(self) -> None:
        assert fragrance.IDENTITY_CAS_NUMBER is None
        assert fragrance.IDENTITY_SOURCE_EXTERNAL_ID is None

    def test_the_name_namespace_is_common_not_inci(self) -> None:
        # The FDA describes the English label word as the common or usual
        # name. This pack claims no INCI register authority.
        assert fragrance.IDENTITY_NAME_NAMESPACE == "common"
        assert fragrance.IDENTITY_NAME_NAMESPACE in NAME_NAMESPACES
        source = _executable_source(_pack_source(fragrance))
        assert "inci" not in source.lower()

    def test_the_fda_identity_authority_is_exact(self) -> None:
        assert fragrance.IDENTITY_SOURCE_TYPE == "government_reference"
        assert fragrance.IDENTITY_SOURCE_PUBLISHER == "U.S. Food and Drug Administration"
        assert fragrance.IDENTITY_SOURCE_TITLE == "Fragrances in Cosmetics"
        assert fragrance.IDENTITY_NAMING_SOURCE_TITLE == "Cosmetic Ingredient Names"

    def test_the_fda_urls_are_pinned_whole_not_by_prefix(self) -> None:
        # This assertion used to be `startswith("https://www.fda.gov/cosmetics/")`,
        # and a wrong canonical path sailed through it: the nomenclature page
        # lives under "cosmetics-labeling", plural, and the singular spelling
        # was just as good a prefix match. A URL is the citation a reader
        # follows, so it is pinned whole.
        assert fragrance.IDENTITY_SOURCE_URL == (
            "https://www.fda.gov/cosmetics/cosmetic-ingredients/fragrances-cosmetics"
        )
        assert fragrance.IDENTITY_NAMING_SOURCE_URL == (
            "https://www.fda.gov/cosmetics/cosmetics-labeling/cosmetic-ingredient-names"
        )

    def test_the_nomenclature_url_uses_the_plural_labeling_segment(self) -> None:
        # Stated separately because it is the exact character the correction
        # turned on, and a reader of this suite should not have to diff two
        # long strings to see it.
        assert "/cosmetics-labeling/" in fragrance.IDENTITY_NAMING_SOURCE_URL
        assert "/cosmetic-labeling/" not in fragrance.IDENTITY_NAMING_SOURCE_URL


class TestRetinolIdentity:
    def test_the_canonical_key_is_the_repository_key(self) -> None:
        assert retinol.SUBSTANCE_KEY == "retinol"
        assert "retinol" in _ontology()

    def test_the_existing_ontology_still_agrees(self) -> None:
        entry = _ontology()["retinol"]
        assert entry.display_name == "Retinol"
        assert entry.inci_name == "Retinol"

    def test_the_government_identity_is_exact(self) -> None:
        assert retinol.IDENTITY_ENTITY_KIND == "defined_substance"
        assert retinol.IDENTITY_SOURCE_PUBLISHER == "PubChem"
        assert retinol.IDENTITY_SOURCE_EXTERNAL_ID == "445354"
        assert retinol.IDENTITY_CAS_NUMBER == "68-26-8"
        assert retinol.IDENTITY_SOURCE_URL.endswith("/445354")

    @pytest.mark.parametrize(
        "other", ["retinaldehyde", "retinyl_palmitate", "adapalene", "tretinoin"]
    )
    def test_it_does_not_speak_for_any_other_retinoid(self, other: str) -> None:
        # The family relationship is why the evidence reaches retinol. It is
        # not a claim that the family's members are interchangeable, and the
        # governed key stays exactly retinol.
        assert other != retinol.SUBSTANCE_KEY
        assert other not in _executable_source(_pack_source(retinol))

    def test_the_family_is_recorded_as_a_note_not_a_second_identity(self) -> None:
        assert retinol.IDENTITY_SUBSTANCE_FAMILY == "retinoid"
        assert retinol.SUBSTANCE_KEY == "retinol"


class TestSalicylicIdentity:
    def test_the_canonical_key_is_the_repository_key(self) -> None:
        assert salicylic.SUBSTANCE_KEY == "salicylic_acid"
        assert "salicylic_acid" in _ontology()

    def test_the_existing_ontology_still_agrees(self) -> None:
        entry = _ontology()["salicylic_acid"]
        assert entry.inci_name == "Salicylic Acid"
        for alias in ("bha", "salicylic", "beta hydroxy acid"):
            assert alias in entry.aliases, alias

    def test_bha_remains_an_alias_and_never_a_second_identity(self) -> None:
        ontology = _ontology()
        for spelling in ("bha", "beta_hydroxy_acid", "sodium_salicylate", "salicylates"):
            assert spelling not in ontology, spelling

    def test_the_government_identity_is_exact(self) -> None:
        assert salicylic.IDENTITY_ENTITY_KIND == "defined_substance"
        assert salicylic.IDENTITY_SOURCE_PUBLISHER == "PubChem"
        assert salicylic.IDENTITY_SOURCE_EXTERNAL_ID == "338"
        assert salicylic.IDENTITY_CAS_NUMBER == "69-72-7"
        assert salicylic.IDENTITY_SOURCE_URL.endswith("/338")


# ---------------------------------------------------------------------------
# Applicability
# ---------------------------------------------------------------------------
class TestApplicability:
    @pytest.mark.parametrize("pack", [fragrance, retinol])
    def test_the_dry_skin_packs_use_the_exact_reviewed_fact(self, pack: ModuleType) -> None:
        assert pack.FACT_KEY == "care_skin_usual_feel"
        assert pack.FACT_OPERATOR == "equals_any"
        assert pack.FACT_VALUES == ("often_dry_or_tight",)

    def test_salicylic_requires_both_reviewed_facts(self) -> None:
        assert salicylic.FEEL_FACT_KEY == "care_skin_usual_feel"
        assert salicylic.FEEL_FACT_VALUES == ("often_oily",)
        assert salicylic.SENSITIVITY_FACT_KEY == "care_skin_sensitivity"
        assert salicylic.SENSITIVITY_FACT_VALUES == ("rarely_reactive",)
        assert len(salicylic.REVIEWED_CONDITIONS) == 2

    def test_two_conditions_are_within_the_governed_limit(self) -> None:
        assert len(salicylic.REVIEWED_CONDITIONS) <= MAX_PERSONAL_APPLICABILITY_CONDITIONS

    @pytest.mark.parametrize(("module_name", "pack"), REVIEWED_ROSTER)
    def test_no_pack_invents_a_profile_key(self, module_name: str, pack: ModuleType) -> None:
        keys = (
            {pack.FEEL_FACT_KEY, pack.SENSITIVITY_FACT_KEY}
            if pack is salicylic
            else {pack.FACT_KEY}
        )
        for key in keys:
            assert key in ATTRIBUTE_REGISTRY, key
            assert key in SKIN_BODY_FACT_KEYS, key

    @pytest.mark.parametrize(("module_name", "pack"), REVIEWED_ROSTER)
    def test_every_value_is_one_the_registry_allows(
        self, module_name: str, pack: ModuleType
    ) -> None:
        pairs = (
            [(pack.FEEL_FACT_KEY, pack.FEEL_FACT_VALUES),
             (pack.SENSITIVITY_FACT_KEY, pack.SENSITIVITY_FACT_VALUES)]
            if pack is salicylic
            else [(pack.FACT_KEY, pack.FACT_VALUES)]
        )
        for key, values in pairs:
            for value in values:
                assert value in ATTRIBUTE_REGISTRY[key].choices, (key, value)

    @pytest.mark.parametrize("uncovered", ["sometimes_reactive", "often_reactive", "not_sure"])
    def test_salicylic_records_the_sensitivity_values_it_does_not_reach(
        self, uncovered: str
    ) -> None:
        # The boundary is declared rather than left implicit in an absence, so
        # a reviewer can see what V1 deliberately does not cover.
        assert uncovered in salicylic.SENSITIVITY_VALUES_NOT_COVERED
        assert uncovered in ATTRIBUTE_REGISTRY["care_skin_sensitivity"].choices


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
class TestEvidence:
    @pytest.mark.parametrize(
        ("pack", "expected"),
        [(fragrance, "limited"), (retinol, "moderate"), (salicylic, "moderate")],
    )
    def test_the_reviewed_evidence_strength(self, pack: ModuleType, expected: str) -> None:
        assert expected == pack.EVIDENCE_STRENGTH
        assert pack.EVIDENCE_STRENGTH in PERSONAL_APPLICABILITY_STRENGTHS

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_no_pack_claims_strong(self, pack: ModuleType) -> None:
        assert pack.EVIDENCE_STRENGTH != "strong"

    def test_fragrance_strength_is_limited_and_says_why(self) -> None:
        rationale = fragrance.EVIDENCE_STRENGTH_RATIONALE.lower()
        assert "limited is used" in rationale
        assert "no population sensitisation data is relied on" in rationale

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_the_scope_states_the_exact_reviewed_fact(self, pack: ModuleType) -> None:
        scope = pack.EVIDENCE_SCOPE.lower()
        assert "ingredient-level" in scope
        assert "non-medical" in scope
        assert "concentration" in scope
        assert "whole-formula" in scope
        assert "co-ingredients" in scope

    def test_the_salicylic_scope_states_both_facts_are_required(self) -> None:
        scope = salicylic.EVIDENCE_SCOPE
        assert "care_skin_usual_feel=often_oily" in scope
        assert "care_skin_sensitivity=rarely_reactive" in scope
        assert "Both facts are required" in scope

    def test_the_fragrance_scope_denies_an_allergy_diagnosis(self) -> None:
        assert "does not establish an allergy or sensitivity diagnosis" in fragrance.EVIDENCE_SCOPE

    def test_the_retinol_scope_denies_a_pregnancy_or_medication_boundary(self) -> None:
        assert "pregnancy or medication boundary" in retinol.EVIDENCE_SCOPE

    def test_the_retinol_summary_is_pinned_whole(self) -> None:
        # Pinned entire rather than blacklisting one phrase. The defect this
        # replaces was an attribution -- the summary credited the PubMed
        # abstract with "irritation including dryness" when its locator reports
        # cutaneous irritation in general and says nothing about dryness. A
        # blacklist would have caught that one wording; pinning the sentence
        # means any future re-attribution has to be re-reviewed.
        assert retinol.EVIDENCE_SUMMARY == (
            "Retinol is relevant to dry-skin care because dermatologist guidance states "
            "that people with skin dryness are generally not good candidates for retinoid "
            "products and identifies retinol as a retinoid. A reviewed scientific article "
            "separately reports that topical retinoids often lead to cutaneous irritation."
        )

    def test_the_retinol_strength_rationale_is_pinned_whole(self) -> None:
        assert retinol.EVIDENCE_STRENGTH_RATIONALE == (
            "Two paths support this, and they support different things. Current "
            "dermatologist guidance states directly that people with skin dryness are "
            "generally not good candidates for retinoid products, and identifies retinol as "
            "a retinoid; that is where dryness comes from. Reviewed scientific literature "
            "separately reports that topical retinoids often lead to cutaneous irritation, "
            "which is general rather than dryness-specific. Moderate is used because the "
            "evidence is ingredient/family-level rather than an exact-product trial, and "
            "formulation and concentration vary enough that no stronger wording is "
            "supportable."
        )

    @pytest.mark.parametrize("text_field", ["EVIDENCE_SUMMARY", "EVIDENCE_STRENGTH_RATIONALE"])
    def test_dryness_is_never_attributed_to_the_research_path(self, text_field: str) -> None:
        # The two paths say different things and the reviewed prose must keep
        # them apart: AAD is where dryness comes from, the article is where
        # general cutaneous irritation comes from.
        text = getattr(retinol, text_field).lower()
        for smuggled in (
            "irritation including dryness",
            "including dryness",
            "dryness as a common effect",
            "research reports that topical retinoids commonly cause local irritation "
            "including dryness",
        ):
            assert smuggled not in text, (text_field, smuggled)

    def test_both_retinol_evidence_paths_are_present_and_distinguished(self) -> None:
        summary = retinol.EVIDENCE_SUMMARY.lower()
        rationale = retinol.EVIDENCE_STRENGTH_RATIONALE.lower()
        # The AAD half: dryness, specifically, plus the family link.
        assert "skin dryness" in summary
        assert "retinol as a retinoid" in summary
        # The article half: general cutaneous irritation, and said separately.
        assert "cutaneous irritation" in summary
        assert "separately" in summary
        # And the rationale states which path carries which.
        assert "that is where dryness comes from" in rationale
        assert "general rather than dryness-specific" in rationale

    def test_the_customer_reason_may_still_speak_of_dryness(self) -> None:
        # It is attached to the AAD locator, which states dryness directly.
        # The boundary restricts what the *article* may be credited with, not
        # what the pack may say at all.
        assert "dry" in retinol.FUTURE_REASON_INTENT.lower()

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_no_summary_characterises_or_promises(self, pack: ModuleType) -> None:
        summary = pack.EVIDENCE_SUMMARY.lower()
        for forbidden in ("cures", "guaranteed", "best", "superior", "miracle", "always"):
            assert forbidden not in summary, forbidden


class TestReviewedSourceMetadata:
    def test_exact_fragrance_aad_metadata(self) -> None:
        assert fragrance.AAD_SOURCE_TYPE == "professional_consensus"
        assert fragrance.AAD_SOURCE_TITLE == "Dermatologists' top tips for relieving dry skin"
        assert fragrance.AAD_SOURCE_PUBLISHER == "American Academy of Dermatology Association"
        assert fragrance.AAD_SOURCE_VERSION == "Last updated 2026-01-02"
        assert "fragrance-free" in fragrance.AAD_SOURCE_LOCATOR
        assert "Stop using" in fragrance.AAD_SOURCE_LOCATOR

    def test_fragrance_reuses_the_already_reviewed_aad_page(self) -> None:
        # Same page as the two merged packs, a different locator on it. The
        # URL is copied from reviewed source, never re-derived.
        assert fragrance.AAD_SOURCE_URL == petrolatum.AAD_SOURCE_URL
        assert fragrance.AAD_SOURCE_URL == glycerin.AAD_SOURCE_URL
        assert fragrance.AAD_SOURCE_LOCATOR != petrolatum.AAD_SOURCE_LOCATOR

    def test_exact_retinol_aad_metadata(self) -> None:
        assert retinol.AAD_SOURCE_TYPE == "professional_consensus"
        assert retinol.AAD_SOURCE_TITLE == "Retinoid or retinol?"
        assert retinol.AAD_SOURCE_PUBLISHER == "American Academy of Dermatology Association"
        assert retinol.AAD_SOURCE_VERSION == "Last updated 2021-05-25"
        assert retinol.AAD_SOURCE_URL.startswith("https://www.aad.org/")

    def test_exact_retinol_research_metadata(self) -> None:
        # Every bibliographic field pinned whole. The publisher previously held
        # the *journal* title, which no assertion here contradicted -- the only
        # check was that it was not "PubMed", and a journal name passes that.
        assert retinol.PUBMED_SOURCE_TYPE == "peer_reviewed_research"
        assert retinol.PUBMED_SOURCE_TITLE == (
            "Topical retinoids: Novel derivatives, nano lipid-based carriers, and "
            "combinations to improve chemical instability and skin irritation"
        )
        assert retinol.PUBMED_SOURCE_PUBLISHER == "Wiley Periodicals LLC"
        assert retinol.PUBMED_SOURCE_URL == "https://pubmed.ncbi.nlm.nih.gov/38952060/"
        assert retinol.PUBMED_SOURCE_PUBLICATION_DATE == "2024-07-01"
        assert retinol.PUBMED_SOURCE_VERSION == "PMID 38952060; DOI 10.1111/jocd.16415"
        assert retinol.PUBMED_SOURCE_LOCATOR == "Abstract"
        assert retinol.PUBMED_SOURCE_JURISDICTION is None

    def test_the_journal_is_never_recorded_as_the_publisher(self) -> None:
        # A journal is where an article appeared; a publisher is who published
        # it. Conflating them misattributes the work exactly as naming the
        # database would.
        assert retinol.PUBMED_SOURCE_PUBLISHER != "Journal of Cosmetic Dermatology"
        assert "Journal of" not in retinol.PUBMED_SOURCE_PUBLISHER
        assert "Journal of" not in salicylic.DELPHI_SOURCE_PUBLISHER

    def test_the_two_wiley_articles_record_their_own_corporate_forms(self) -> None:
        # The entity was renamed between the 2019 and 2024 articles. Each is
        # recorded as its own article states it rather than normalised to look
        # consistent, so neither is silently restated as the other.
        assert retinol.PUBMED_SOURCE_PUBLISHER == "Wiley Periodicals LLC"
        assert petrolatum.PUBMED_SOURCE_PUBLISHER == "Wiley Periodicals, Inc."
        assert glycerin.PUBMED_SOURCE_PUBLISHER == "Wiley Periodicals, Inc."

    def test_exact_salicylic_aad_metadata(self) -> None:
        assert salicylic.AAD_SOURCE_TYPE == "professional_consensus"
        assert salicylic.AAD_SOURCE_TITLE == "How to control oily skin"
        assert salicylic.AAD_SOURCE_VERSION == "Last updated 2024-09-03"
        assert "10 do's and don'ts from dermatologists" in salicylic.AAD_SOURCE_LOCATOR

    def test_exact_salicylic_delphi_metadata(self) -> None:
        assert salicylic.DELPHI_SOURCE_TYPE == "peer_reviewed_research"
        assert salicylic.DELPHI_SOURCE_PUBLISHER == "Elsevier Inc."
        assert salicylic.DELPHI_SOURCE_PUBLICATION_DATE == "2025-04-14"
        assert salicylic.DELPHI_SOURCE_URL == "https://pubmed.ncbi.nlm.nih.gov/40233838/"
        assert "PMID 40233838" in salicylic.DELPHI_SOURCE_VERSION
        assert "10.1016/j.jaad.2025.04.021" in salicylic.DELPHI_SOURCE_VERSION
        assert "Delphi consensus study" in salicylic.DELPHI_SOURCE_TITLE

    @pytest.mark.parametrize(
        ("pack", "prefix"),
        [
            (fragrance, "AAD"),
            (retinol, "AAD"),
            (retinol, "PUBMED"),
            (salicylic, "AAD"),
            (salicylic, "DELPHI"),
        ],
    )
    def test_no_jurisdiction_is_ever_inferred(self, pack: ModuleType, prefix: str) -> None:
        assert getattr(pack, f"{prefix}_SOURCE_JURISDICTION") is None

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_an_update_date_never_becomes_a_publication_date(self, pack: ModuleType) -> None:
        assert pack.AAD_SOURCE_PUBLICATION_DATE is None
        assert pack.AAD_SOURCE_VERSION.startswith("Last updated ")

    def test_a_database_is_never_named_as_a_publisher(self) -> None:
        assert "PubMed" not in retinol.PUBMED_SOURCE_PUBLISHER
        assert "PubMed" not in salicylic.DELPHI_SOURCE_PUBLISHER

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_every_declared_source_type_is_eligible_for_personal_applicability(
        self, pack: ModuleType
    ) -> None:
        from app.domains.personal_applicability.service import (
            PERSONAL_APPLICABILITY_SOURCE_TYPES,
        )

        for name in dir(pack):
            if name.endswith("_SOURCE_TYPE") and not name.startswith("IDENTITY"):
                assert getattr(pack, name) in PERSONAL_APPLICABILITY_SOURCE_TYPES, name


# ---------------------------------------------------------------------------
# Rules, and the absence of any inference between them
# ---------------------------------------------------------------------------
class TestDecisionRules:
    @pytest.mark.parametrize(
        ("pack", "signal", "signal_set", "action"),
        [
            (fragrance, "cautionary", "cautionary_only", "skip"),
            (retinol, "cautionary", "cautionary_only", "skip"),
            (salicylic, "supporting", "supporting_only", "buy"),
        ],
    )
    def test_the_exact_reviewed_rule_shape(
        self, pack: ModuleType, signal: str, signal_set: str, action: str
    ) -> None:
        assert signal == pack.SEMANTIC_SIGNAL
        assert signal_set == pack.POLICY_SIGNAL_SET
        assert action == pack.POLICY_ACTION
        assert pack.SEMANTIC_RULE_VERSION == "1"
        assert pack.POLICY_VERSION == "1"
        assert pack.EXPLANATION_VERSION == "1"

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_the_compiled_rules_carry_the_reviewed_values(self, pack: ModuleType) -> None:
        manifest = pack.build_release_manifest_from_published_entry(_valid_entry(pack))
        (semantic,) = manifest["semantic_rules"]
        (policy,) = manifest["policy_rules"]
        (explanation,) = manifest["explanation_rules"]
        assert semantic["signal"] == pack.SEMANTIC_SIGNAL
        assert semantic["substance_key"] == pack.SUBSTANCE_KEY
        assert policy["signal_set"] == pack.POLICY_SIGNAL_SET
        assert policy["action"] == pack.POLICY_ACTION
        assert explanation["reason_key"] == pack.REASON_KEY
        assert explanation["source_locator"] == pack.AAD_SOURCE_LOCATOR

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_every_gap_flag_is_false_for_the_reviewed_single_pack_policy(
        self, pack: ModuleType
    ) -> None:
        (policy,) = pack.build_release_manifest_from_published_entry(
            _valid_entry(pack)
        )["policy_rules"]
        assert policy["has_identity_unresolved"] is False
        assert policy["has_identity_ambiguous"] is False
        assert policy["has_personal_evidence_gap"] is False

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_the_policy_binds_only_its_own_semantic_rule(self, pack: ModuleType) -> None:
        (policy,) = pack.build_release_manifest_from_published_entry(
            _valid_entry(pack)
        )["policy_rules"]
        assert policy["semantic_rule_identities"] == [
            {"rule_id": pack.SEMANTIC_RULE_ID, "rule_version": pack.SEMANTIC_RULE_VERSION}
        ]

    @pytest.mark.parametrize(("module_name", "pack"), REVIEWED_ROSTER)
    def test_no_pack_declares_a_signal_to_action_inference(
        self, module_name: str, pack: ModuleType
    ) -> None:
        # "supporting means BUY" and "cautionary means SKIP" are the two rules
        # this architecture must never learn. Each pack's action is an
        # authored constant, and nothing may branch on the signal to reach it.
        tree = ast.parse(_executable_source(_pack_source(pack)))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.If, ast.IfExp, ast.Match)):
                continue
            names = {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}
            assert not names & {"SEMANTIC_SIGNAL", "POLICY_ACTION", "POLICY_SIGNAL_SET"}, module_name

    @pytest.mark.parametrize(("module_name", "pack"), REVIEWED_ROSTER)
    def test_no_pack_mentions_another_packs_signal_direction_as_a_rule(
        self, module_name: str, pack: ModuleType
    ) -> None:
        source = _executable_source(_pack_source(pack))
        for forbidden in ("majority", "weight", "score", "confidence", "threshold", "fallback"):
            assert forbidden not in source.lower(), (module_name, forbidden)


class TestTheEngineStillRefusesToGuess:
    """The positive safety proof: two reviewed rules do not make a decision.

    The five reviewed policies each target exactly one semantic rule identity.
    A product carrying two of these substances presents a *different* governed
    state, and the exact-match index has nothing for it. The engine then
    answers NOT_ENOUGH_DECISION_POLICY, which is the correct answer and not a
    defect to fix here: any combination rule would be a new global product
    policy nobody has reviewed.
    """

    @staticmethod
    def _policy_rules() -> list[PersonalDecisionPolicyRule]:
        rules = []
        for _, pack in REVIEWED_ROSTER:
            rules.append(
                PersonalDecisionPolicyRule(
                    policy_id=pack.POLICY_ID,
                    policy_version=pack.POLICY_VERSION,
                    category=PersonalDecisionPolicyCategory(pack.CATEGORY),
                    semantic_rule_identities=frozenset(
                        {(pack.SEMANTIC_RULE_ID, pack.SEMANTIC_RULE_VERSION)}
                    ),
                    signal_set=PersonalSignalSet(pack.POLICY_SIGNAL_SET),
                    has_identity_unresolved=False,
                    has_identity_ambiguous=False,
                    has_personal_evidence_gap=False,
                    action=PersonalDecisionAction(pack.POLICY_ACTION),
                )
            )
        return rules

    def test_the_five_reviewed_policies_index_without_conflict(self) -> None:
        index = build_policy_index(self._policy_rules())
        assert len(index) == REVIEWED_PACK_COUNT

    def test_each_policy_targets_exactly_one_semantic_rule(self) -> None:
        for rule in self._policy_rules():
            assert len(rule.semantic_rule_identities) == 1

    def test_two_supporting_rules_together_match_no_reviewed_policy(self) -> None:
        index = build_policy_index(self._policy_rules())
        combined = frozenset(
            {
                (petrolatum.SEMANTIC_RULE_ID, petrolatum.SEMANTIC_RULE_VERSION),
                (glycerin.SEMANTIC_RULE_ID, glycerin.SEMANTIC_RULE_VERSION),
            }
        )
        target = (
            PersonalDecisionPolicyCategory.SKIN_CARE,
            combined,
            PersonalSignalSet.SUPPORTING_ONLY,
            False,
            False,
            False,
        )
        assert index.get(target) is None

    def test_a_supporting_and_a_cautionary_rule_together_match_no_reviewed_policy(self) -> None:
        index = build_policy_index(self._policy_rules())
        combined = frozenset(
            {
                (petrolatum.SEMANTIC_RULE_ID, petrolatum.SEMANTIC_RULE_VERSION),
                (fragrance.SEMANTIC_RULE_ID, fragrance.SEMANTIC_RULE_VERSION),
            }
        )
        for signal_set in PersonalSignalSet:
            target = (
                PersonalDecisionPolicyCategory.SKIN_CARE,
                combined,
                signal_set,
                False,
                False,
                False,
            )
            assert index.get(target) is None, signal_set

    def test_a_mixed_signal_set_matches_no_reviewed_policy(self) -> None:
        index = build_policy_index(self._policy_rules())
        for _, pack in REVIEWED_ROSTER:
            target = (
                PersonalDecisionPolicyCategory.SKIN_CARE,
                frozenset({(pack.SEMANTIC_RULE_ID, pack.SEMANTIC_RULE_VERSION)}),
                PersonalSignalSet.MIXED,
                False,
                False,
                False,
            )
            assert index.get(target) is None, pack.PACK_ID

    def test_a_gap_flag_changes_the_target_and_loses_the_match(self) -> None:
        # An unresolved co-ingredient is a different governed state, so the
        # single-pack policy no longer applies. That is the fail-closed
        # behaviour, stated as a test rather than assumed.
        index = build_policy_index(self._policy_rules())
        target = (
            PersonalDecisionPolicyCategory.SKIN_CARE,
            frozenset({(salicylic.SEMANTIC_RULE_ID, salicylic.SEMANTIC_RULE_VERSION)}),
            PersonalSignalSet.SUPPORTING_ONLY,
            True,
            False,
            False,
        )
        assert index.get(target) is None

    def test_the_production_policy_registry_is_still_empty(self) -> None:
        from app.domains.personal_decision_policy.rules import PERSONAL_DECISION_POLICY_RULES

        assert PERSONAL_DECISION_POLICY_RULES == ()


# ---------------------------------------------------------------------------
# The selected-citation boundary
# ---------------------------------------------------------------------------
class TestSelectedCitationBoundary:
    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_the_selected_citation_is_always_the_aad_source(self, pack: ModuleType) -> None:
        (explanation,) = pack.build_release_manifest_from_published_entry(
            _valid_entry(pack)
        )["explanation_rules"]
        assert explanation["source_key"] == FIXTURE_AAD_SOURCE_KEY
        assert explanation["source_locator"] == pack.AAD_SOURCE_LOCATOR

    @pytest.mark.parametrize("pack", [retinol, salicylic])
    def test_the_second_source_is_never_the_selected_citation(self, pack: ModuleType) -> None:
        (explanation,) = pack.build_release_manifest_from_published_entry(
            _valid_entry(pack)
        )["explanation_rules"]
        assert explanation["source_key"] != FIXTURE_SECOND_SOURCE_KEY[pack.__name__]

    @pytest.mark.parametrize(("module_name", "pack"), REVIEWED_ROSTER)
    def test_the_reason_intent_carries_no_out_of_scope_claim(
        self, module_name: str, pack: ModuleType
    ) -> None:
        intent = pack.FUTURE_REASON_INTENT.lower()
        for claim in pack.REASON_CLAIMS_OUT_OF_SCOPE:
            assert claim not in intent, (module_name, claim)

    def test_the_fragrance_reason_says_only_what_the_aad_locator_supports(self) -> None:
        intent = fragrance.FUTURE_REASON_INTENT.lower()
        assert "dermatologist guidance" in intent
        assert "fragrance-free" in intent

    def test_the_fragrance_reason_makes_no_allergy_or_safety_claim(self) -> None:
        guard = {claim.lower() for claim in fragrance.REASON_CLAIMS_OUT_OF_SCOPE}
        for claim in ("allergy", "allergic", "irritant", "unsafe", "toxic"):
            assert claim in guard, claim

    def test_the_retinol_reason_carries_the_family_link_and_nothing_more(self) -> None:
        intent = retinol.FUTURE_REASON_INTENT.lower()
        assert "retinol is a retinoid" in intent
        assert "dermatologist guidance" in intent
        assert "not good candidates" in intent

    @pytest.mark.parametrize(
        "claim", ["pregnan", "acne", "anti-aging", "wrinkle", "collagen", "pigmentation"]
    )
    def test_the_retinol_guard_names_the_claims_its_own_source_page_also_discusses(
        self, claim: str
    ) -> None:
        # The AAD page really does cover pregnancy and anti-ageing. That is
        # exactly why they are named here: the risk is leakage from a source
        # into a sentence, not invention out of nowhere.
        guard = {c.lower() for c in retinol.REASON_CLAIMS_OUT_OF_SCOPE}
        assert claim in guard

    def test_the_retinol_reason_never_mentions_pregnancy(self) -> None:
        assert "pregnan" not in retinol.FUTURE_REASON_INTENT.lower()
        assert "pregnan" not in retinol.EVIDENCE_SUMMARY.lower()

    def test_the_salicylic_reason_says_only_the_oiliness_proposition(self) -> None:
        intent = salicylic.FUTURE_REASON_INTENT.lower()
        assert "reduce oiliness" in intent
        assert "dermatologist guidance" in intent

    @pytest.mark.parametrize(
        "claim", ["acne", "guaranteed", "safe for sensitive skin", "will not irritate", "consensus"]
    )
    def test_the_salicylic_guard_covers_tolerance_and_acne_claims(self, claim: str) -> None:
        guard = {c.lower() for c in salicylic.REASON_CLAIMS_OUT_OF_SCOPE}
        assert claim in guard

    def test_the_rarely_reactive_boundary_never_becomes_a_reassurance(self) -> None:
        # The condition narrows who the pack reaches. Turning it into "safe
        # for you" in the customer sentence would invert its meaning.
        intent = salicylic.FUTURE_REASON_INTENT.lower()
        for forbidden in ("rarely reactive", "sensitive", "tolerate", "safe"):
            assert forbidden not in intent, forbidden

    @pytest.mark.parametrize(("module_name", "pack"), REVIEWED_ROSTER)
    def test_no_reason_intent_advises_the_person(
        self, module_name: str, pack: ModuleType
    ) -> None:
        intent = pack.FUTURE_REASON_INTENT.lower()
        for forbidden in ("you should", "we recommend", "buy this", "avoid this", "stop using"):
            assert forbidden not in intent, (module_name, forbidden)


# ---------------------------------------------------------------------------
# Compiler fail-closed behaviour
# ---------------------------------------------------------------------------
class TestCompilersFailClosed:
    @pytest.mark.parametrize("pack", NEW_PACKS)
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("review_status", "draft"),
            ("claim_status", "refuted"),
            ("category", "hair_care"),
            ("domain", "hair_care"),
            ("subject_type", "product"),
            ("claim_type", "substance_safety"),
            ("evidence_tier", "anecdotal"),
            ("ai_generated", True),
            ("claim_version", 2),
            ("summary", "Rewritten."),
            ("scope", "Anything goes."),
            ("strength_rationale", "Because it seems right."),
            ("substance_key", "something_else"),
            ("evidence_strength", "strong"),
        ],
    )
    def test_a_changed_reviewed_field_is_refused(
        self, pack: ModuleType, field: str, value: object
    ) -> None:
        entry = _valid_entry(pack)
        entry[field] = value
        with pytest.raises(_pack_error(pack)):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize("pack", NEW_PACKS)
    @pytest.mark.parametrize(
        "field",
        [
            "source_type", "title", "publisher", "canonical_url", "locator",
            "publication_date", "version_or_revision", "jurisdiction", "status",
            "license_or_use_note",
        ],
    )
    def test_a_changed_source_field_is_refused(self, pack: ModuleType, field: str) -> None:
        entry = _valid_entry(pack)
        entry["sources"][0][field] = "changed"
        with pytest.raises(_pack_error(pack)):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_an_inferred_publication_date_is_refused(self, pack: ModuleType) -> None:
        entry = _valid_entry(pack)
        entry["sources"][0]["publication_date"] = "2026-01-02"
        with pytest.raises(_pack_error(pack)):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_an_inferred_jurisdiction_is_refused(self, pack: ModuleType) -> None:
        entry = _valid_entry(pack)
        entry["sources"][0]["jurisdiction"] = "global"
        with pytest.raises(_pack_error(pack)):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_a_blank_source_key_is_refused(self, pack: ModuleType) -> None:
        entry = _valid_entry(pack)
        entry["sources"][0]["source_key"] = "   "
        with pytest.raises(_pack_error(pack)):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_an_extra_source_is_refused(self, pack: ModuleType) -> None:
        entry = _valid_entry(pack)
        entry["sources"].append(dict(entry["sources"][0]))
        with pytest.raises(_pack_error(pack)):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_a_missing_source_is_refused(self, pack: ModuleType) -> None:
        entry = _valid_entry(pack)
        entry["sources"] = entry["sources"][:-1]
        with pytest.raises(_pack_error(pack)):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize("pack", NEW_PACKS)
    @pytest.mark.parametrize("claim_key", ["", "   ", None, 7])
    def test_a_missing_claim_key_is_refused(self, pack: ModuleType, claim_key: object) -> None:
        entry = _valid_entry(pack)
        entry["claim_key"] = claim_key
        with pytest.raises(_pack_error(pack)):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize("pack", NEW_PACKS)
    @pytest.mark.parametrize("entry", ["", 7, None, [], "a string"])
    def test_a_non_object_entry_is_refused(self, pack: ModuleType, entry: object) -> None:
        with pytest.raises(_pack_error(pack)):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize("pack", [fragrance, retinol])
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("fact_key", "care_skin_sensitivity"),
            ("operator", "equals"),
            ("values", ["often_oily"]),
            ("values", ["comfortable"]),
            ("values", ["often_dry_or_tight", "often_oily"]),
        ],
    )
    def test_a_changed_dry_skin_condition_is_refused(
        self, pack: ModuleType, field: str, value: object
    ) -> None:
        entry = _valid_entry(pack)
        entry["conditions"][0][field] = value
        with pytest.raises(_pack_error(pack)):
            pack.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_every_pack_refuses_every_other_packs_entry(self, pack: ModuleType) -> None:
        for _, other in REVIEWED_ROSTER:
            if other is pack:
                continue
            entry = (
                _valid_entry(other)
                if other in NEW_PACKS
                else _foreign_entry(other)
            )
            with pytest.raises(_pack_error(pack)):
                pack.build_release_manifest_from_published_entry(entry)


def _foreign_entry(pack: ModuleType) -> dict[str, object]:
    from tests.test_step8i_first_production_knowledge_pack import _valid_entry as petrolatum_entry
    from tests.test_step14d_glycerin_dry_skin_pack import _valid_entry as glycerin_entry

    return petrolatum_entry() if pack is petrolatum else glycerin_entry()


class TestSalicylicTwoConditionBoundary:
    """The condition that keeps this pack narrower than its own headline."""

    def test_the_reviewed_conditions_are_pinned_to_literals(self) -> None:
        # Anchored to literals, not to the pack's own constants. Every
        # fixture-derived test here builds its entry *from* those constants,
        # so widening one would move the goalposts for all of them at once and
        # the suite would keep passing while the reviewed applicability
        # silently grew. Mutation testing found exactly that hole.
        assert set(salicylic.REVIEWED_CONDITIONS) == {
            ("care_skin_usual_feel", "equals_any", ("often_oily",)),
            ("care_skin_sensitivity", "equals_any", ("rarely_reactive",)),
        }

    def test_the_exact_literal_reviewed_entry_compiles(self) -> None:
        entry = _valid_entry(salicylic)
        entry["conditions"] = [
            {
                "fact_key": "care_skin_usual_feel",
                "operator": "equals_any",
                "values": ["often_oily"],
            },
            {
                "fact_key": "care_skin_sensitivity",
                "operator": "equals_any",
                "values": ["rarely_reactive"],
            },
        ]
        manifest = salicylic.build_release_manifest_from_published_entry(entry)
        assert len(manifest["policy_rules"]) == 1

    @pytest.mark.parametrize("widened", ["sometimes_reactive", "often_reactive", "not_sure"])
    def test_a_literal_entry_widened_to_another_reactivity_is_refused(
        self, widened: str
    ) -> None:
        entry = _valid_entry(salicylic)
        entry["conditions"] = [
            {
                "fact_key": "care_skin_usual_feel",
                "operator": "equals_any",
                "values": ["often_oily"],
            },
            {
                "fact_key": "care_skin_sensitivity",
                "operator": "equals_any",
                "values": ["rarely_reactive", widened],
            },
        ]
        with pytest.raises(salicylic.SalicylicAcidOilySkinKnowledgePackError):
            salicylic.build_release_manifest_from_published_entry(entry)

    def test_both_conditions_together_compile(self) -> None:
        manifest = salicylic.build_release_manifest_from_published_entry(
            _valid_entry(salicylic)
        )
        assert len(manifest["semantic_rules"]) == 1

    def test_condition_order_does_not_change_the_manifest(self) -> None:
        forward = salicylic.build_release_manifest_from_published_entry(_valid_entry(salicylic))
        backward = salicylic.build_release_manifest_from_published_entry(
            _valid_entry(salicylic, reversed_conditions=True)
        )
        assert forward == backward

    def test_condition_order_does_not_change_the_hash(self) -> None:
        forward = manifest_content_hash(
            parse_release_manifest(
                salicylic.build_release_manifest_from_published_entry(_valid_entry(salicylic))
            )
        )
        backward = manifest_content_hash(
            parse_release_manifest(
                salicylic.build_release_manifest_from_published_entry(
                    _valid_entry(salicylic, reversed_conditions=True)
                )
            )
        )
        assert forward == backward

    def test_the_oily_condition_alone_is_refused(self) -> None:
        # The single-condition entry is a wider claim than the one reviewed.
        entry = _valid_entry(salicylic)
        entry["conditions"] = [entry["conditions"][0]]
        with pytest.raises(salicylic.SalicylicAcidOilySkinKnowledgePackError):
            salicylic.build_release_manifest_from_published_entry(entry)

    def test_the_sensitivity_condition_alone_is_refused(self) -> None:
        entry = _valid_entry(salicylic)
        entry["conditions"] = [entry["conditions"][1]]
        with pytest.raises(salicylic.SalicylicAcidOilySkinKnowledgePackError):
            salicylic.build_release_manifest_from_published_entry(entry)

    def test_no_conditions_at_all_are_refused(self) -> None:
        entry = _valid_entry(salicylic)
        entry["conditions"] = []
        with pytest.raises(salicylic.SalicylicAcidOilySkinKnowledgePackError):
            salicylic.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize("value", ["sometimes_reactive", "often_reactive", "not_sure"])
    def test_another_reactivity_value_is_refused(self, value: str) -> None:
        entry = _valid_entry(salicylic)
        entry["conditions"][1]["values"] = [value]
        with pytest.raises(salicylic.SalicylicAcidOilySkinKnowledgePackError):
            salicylic.build_release_manifest_from_published_entry(entry)

    def test_widening_the_reactivity_values_is_refused(self) -> None:
        entry = _valid_entry(salicylic)
        entry["conditions"][1]["values"] = ["rarely_reactive", "sometimes_reactive"]
        with pytest.raises(salicylic.SalicylicAcidOilySkinKnowledgePackError):
            salicylic.build_release_manifest_from_published_entry(entry)

    @pytest.mark.parametrize("value", ["often_dry_or_tight", "comfortable", "mixed", "not_sure"])
    def test_another_skin_feel_value_is_refused(self, value: str) -> None:
        entry = _valid_entry(salicylic)
        entry["conditions"][0]["values"] = [value]
        with pytest.raises(salicylic.SalicylicAcidOilySkinKnowledgePackError):
            salicylic.build_release_manifest_from_published_entry(entry)

    def test_a_duplicated_condition_is_refused(self) -> None:
        entry = _valid_entry(salicylic)
        entry["conditions"] = [entry["conditions"][0], dict(entry["conditions"][0])]
        with pytest.raises(salicylic.SalicylicAcidOilySkinKnowledgePackError):
            salicylic.build_release_manifest_from_published_entry(entry)

    def test_a_third_condition_is_refused(self) -> None:
        entry = _valid_entry(salicylic)
        entry["conditions"].append(
            {"fact_key": "care_skin_usual_feel", "operator": "equals_any", "values": ["mixed"]}
        )
        with pytest.raises(salicylic.SalicylicAcidOilySkinKnowledgePackError):
            salicylic.build_release_manifest_from_published_entry(entry)


class TestSourceOrderIsImmaterial:
    @pytest.mark.parametrize("pack", [retinol, salicylic])
    def test_reversed_source_order_produces_the_same_manifest(self, pack: ModuleType) -> None:
        forward = pack.build_release_manifest_from_published_entry(_valid_entry(pack))
        backward = pack.build_release_manifest_from_published_entry(
            _valid_entry(pack, reversed_sources=True)
        )
        assert forward == backward

    @pytest.mark.parametrize("pack", [retinol, salicylic])
    def test_reversed_source_order_still_selects_the_aad_citation(
        self, pack: ModuleType
    ) -> None:
        (explanation,) = pack.build_release_manifest_from_published_entry(
            _valid_entry(pack, reversed_sources=True)
        )["explanation_rules"]
        assert explanation["source_key"] == FIXTURE_AAD_SOURCE_KEY


class TestCompiledManifests:
    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_it_parses_through_the_step_8h_authority(self, pack: ModuleType) -> None:
        parsed = parse_release_manifest(
            pack.build_release_manifest_from_published_entry(_valid_entry(pack))
        )
        assert len(parsed.semantic_rules) == 1
        assert len(parsed.policy_rules) == 1
        assert len(parsed.explanation_rules) == 1

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_the_emitted_document_is_already_canonical(self, pack: ModuleType) -> None:
        manifest = pack.build_release_manifest_from_published_entry(_valid_entry(pack))
        assert manifest == canonical_manifest(parse_release_manifest(manifest))

    @pytest.mark.parametrize("pack", NEW_PACKS)
    def test_compilation_is_deterministic(self, pack: ModuleType) -> None:
        first = pack.build_release_manifest_from_published_entry(_valid_entry(pack))
        second = pack.build_release_manifest_from_published_entry(_valid_entry(pack))
        assert first == second

    def test_all_five_manifests_hash_differently(self) -> None:
        hashes = set()
        for _, pack in REVIEWED_ROSTER:
            entry = _valid_entry(pack) if pack in NEW_PACKS else _foreign_entry(pack)
            hashes.add(
                manifest_content_hash(
                    parse_release_manifest(
                        pack.build_release_manifest_from_published_entry(entry)
                    )
                )
            )
        assert len(hashes) == REVIEWED_PACK_COUNT

    def test_the_two_frozen_hashes_have_not_moved(self) -> None:
        for pack, expected in ((petrolatum, PETROLATUM_CONTENT_HASH),
                               (glycerin, GLYCERIN_CONTENT_HASH)):
            actual = manifest_content_hash(
                parse_release_manifest(
                    pack.build_release_manifest_from_published_entry(_foreign_entry(pack))
                )
            )
            assert actual == expected, pack.PACK_ID


# ---------------------------------------------------------------------------
# The generic compiler, across all five
# ---------------------------------------------------------------------------
class TestGenericCompilerAcrossTheRoster:
    @pytest.mark.parametrize(("module_name", "pack"), REVIEWED_ROSTER)
    def test_the_generic_builder_compiles_every_pack(
        self, module_name: str, pack: ModuleType, entry_files: dict[str, Path]
    ) -> None:
        completed = _run_builder("--pack-id", pack.PACK_ID, str(entry_files[pack.PACK_ID]))
        assert completed.returncode == 0, completed.stderr
        assert "content_hash=" in completed.stdout

    @pytest.mark.parametrize(("module_name", "pack"), REVIEWED_ROSTER)
    def test_selecting_one_pack_executes_exactly_that_pack(
        self, module_name: str, pack: ModuleType, entry_files: dict[str, Path]
    ) -> None:
        result = _probe_imports(["--pack-id", pack.PACK_ID, str(entry_files[pack.PACK_ID])])
        assert result["status"] == 0
        assert result["imported"] == [module_name]

    def test_an_unknown_pack_id_executes_none_of_them(
        self, entry_files: dict[str, Path]
    ) -> None:
        any_entry = next(iter(entry_files.values()))
        result = _probe_imports(["--pack-id", "for_you.skin_care.nothing.v1", str(any_entry)])
        assert result["status"] != 0
        assert result["imported"] == []

    def test_the_generic_builder_was_taught_nothing_about_any_pack(self) -> None:
        # If the builder needed a line about fragrance, retinol or salicylic
        # acid, Step 14C was not generic and this milestone would disprove it.
        source = _executable_source(GENERIC_BUILDER).lower()
        for forbidden in (
            "fragrance", "retinol", "salicylic", "petrolatum", "glycerin", "glycerol",
        ):
            assert forbidden not in source, forbidden

    def test_the_generic_builder_names_no_pack_module_or_id(self) -> None:
        source = _executable_source(GENERIC_BUILDER)
        for module_name, pack in REVIEWED_ROSTER:
            assert module_name not in source, module_name
            assert pack.PACK_ID not in source, pack.PACK_ID
        assert source.count("import_module") == 1

    def test_the_refusal_for_an_unknown_id_lists_every_real_pack(
        self, entry_files: dict[str, Path]
    ) -> None:
        any_entry = next(iter(entry_files.values()))
        completed = _run_builder("--pack-id", "for_you.skin_care.nothing.v1", str(any_entry))
        assert completed.returncode != 0
        for _, pack in REVIEWED_ROSTER:
            assert pack.PACK_ID in completed.stderr, pack.PACK_ID


# ---------------------------------------------------------------------------
# Governance the roster must keep
# ---------------------------------------------------------------------------
class TestGovernance:
    def test_no_pack_imports_another_governed_pack(self) -> None:
        from tests.test_generic_knowledge_pack_compiler import _cross_pack_imports

        checked = 0
        for path in discover_pack_sources():
            own = f"app.knowledge_packs.{path.stem}"
            offenders = _cross_pack_imports(path.read_text(encoding="utf-8"), own_module=own)
            assert offenders == [], (path.name, offenders)
            checked += 1
        assert checked == REVIEWED_PACK_COUNT

    @pytest.mark.parametrize(("module_name", "pack"), REVIEWED_ROSTER)
    def test_the_only_application_import_is_the_manifest_authority(
        self, module_name: str, pack: ModuleType
    ) -> None:
        tree = ast.parse(_pack_source(pack).read_text(encoding="utf-8"))
        app_imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
                app_imports.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app."):
                        app_imports.add(alias.name)
        assert app_imports == {"app.domains.personal_decision_release.manifest"}, module_name

    def test_the_package_root_is_not_a_registry(self) -> None:
        # Structural, not a substring scan: the root's own docstring says it is
        # "not imported by application startup", and a raw-text search for
        # "import" would read that promise as the violation.
        tree = ast.parse((PACK_DIRECTORY / "__init__.py").read_text(encoding="utf-8"))
        body = [node for node in tree.body if not isinstance(node, ast.Expr)]
        assert body == [], "the package root must remain a docstring and nothing else"
        for node in ast.walk(tree):
            assert not isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.Call))

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

    @pytest.mark.parametrize(("module_name", "pack"), REVIEWED_ROSTER)
    @pytest.mark.parametrize(
        "forbidden",
        [
            "requests", "httpx", "urllib", "socket", "aiohttp",
            "sqlalchemy", "asyncpg", "AsyncSession", "select(", "create_engine",
            "open(", "Path(", "os.environ", "getenv", "subprocess",
            "supabase", "sentry", "gemini", "googleapis", "render.com",
            "activate(", "deactivate(", "publish(", "prepare(", "publish_release",
            "hard_handoff",
        ],
    )
    def test_every_pack_reaches_for_nothing_at_import_time(
        self, module_name: str, pack: ModuleType, forbidden: str
    ) -> None:
        assert forbidden not in _executable_source(_pack_source(pack)), (module_name, forbidden)

    @pytest.mark.parametrize(("module_name", "pack"), REVIEWED_ROSTER)
    def test_module_scope_runs_no_calls(self, module_name: str, pack: ModuleType) -> None:
        tree = ast.parse(_pack_source(pack).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
                continue
            for child in ast.walk(node):
                assert not isinstance(child, ast.Call), (module_name, ast.dump(node)[:100])

    def test_the_medical_handoff_path_is_untouched_by_every_pack(self) -> None:
        # Pregnancy, breastfeeding, medication and diagnosed-condition
        # safeguards stay upstream. Retinol is the pack most tempted to
        # duplicate them, and it does not.
        # Judged on executable source. The retinol pack's docstring names
        # hard_handoff deliberately -- to record where the pregnancy boundary
        # lives and that this pack does not touch it -- and a raw-text scan
        # would call that explanation the violation.
        for _, pack in REVIEWED_ROSTER:
            source = _executable_source(_pack_source(pack))
            assert "hard_handoff" not in source, pack.PACK_ID
            assert "routines" not in source, pack.PACK_ID
            assert "handoff" not in source.lower(), pack.PACK_ID

    def test_no_pack_touches_customer_copy(self) -> None:
        for _, pack in REVIEWED_ROSTER:
            assert "for_you_copy" not in _pack_source(pack).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The freeze contract
# ---------------------------------------------------------------------------
class TestTheFreezeDocument:
    def test_the_freeze_document_exists(self) -> None:
        assert FREEZE_DOCUMENT.is_file()

    def test_it_states_the_closure_condition_and_that_there_is_no_next_step(self) -> None:
        text = FREEZE_DOCUMENT.read_text(encoding="utf-8")
        assert "There is no Step 14F" in text
        assert "Step 14 is complete when this exact five-pack roster passes independent review" in text

    def test_it_names_every_pack_in_the_roster(self) -> None:
        text = FREEZE_DOCUMENT.read_text(encoding="utf-8")
        for _, pack in REVIEWED_ROSTER:
            assert pack.SUBSTANCE_KEY in text, pack.SUBSTANCE_KEY

    def test_it_does_not_claim_comprehensive_coverage(self) -> None:
        text = FREEZE_DOCUMENT.read_text(encoding="utf-8").lower()
        assert "not the entire universe of skin-care knowledge" in text
        for forbidden in ("comprehensive dermatology", "complete coverage", "every formula"):
            assert forbidden not in text, forbidden

    @pytest.mark.parametrize(
        "deferred",
        [
            "hyaluronic acid", "dimethicone", "glycolic acid", "lactic acid", "urea",
            "niacinamide", "azelaic acid", "benzoyl peroxide", "vitamin C",
            "zinc oxide", "titanium dioxide",
        ],
    )
    def test_it_carries_the_deferred_knowledge_operations_backlog(self, deferred: str) -> None:
        assert deferred in FREEZE_DOCUMENT.read_text(encoding="utf-8"), deferred

    @pytest.mark.parametrize("state", ["sometimes_reactive", "often_reactive"])
    def test_it_records_the_reactivity_states_v1_does_not_cover(self, state: str) -> None:
        assert state in FREEZE_DOCUMENT.read_text(encoding="utf-8"), state

    def test_it_records_the_independently_verified_canonical_urls(self) -> None:
        # The document used to say four URLs were constructed and unverified.
        # Independent review has since confirmed them, and one was wrong; the
        # document must now carry the verified strings rather than the caveat.
        text = FREEZE_DOCUMENT.read_text(encoding="utf-8")
        for url in (
            "https://www.aad.org/public/everyday-care/skin-care-secrets/anti-aging/retinoid-retinol",
            "https://www.aad.org/public/everyday-care/skin-care-basics/dry/oily-skin",
            "https://www.fda.gov/cosmetics/cosmetic-ingredients/fragrances-cosmetics",
            "https://www.fda.gov/cosmetics/cosmetics-labeling/cosmetic-ingredient-names",
        ):
            assert url in text, url
        assert "independently verified" in text.lower()
        assert "NOT verified" not in text

    def test_it_does_not_claim_this_environment_opened_the_sources(self) -> None:
        # Still true, and still stated: the egress block is unchanged. The
        # verification is the reviewer's, and the document says whose it is.
        text = FREEZE_DOCUMENT.read_text(encoding="utf-8")
        assert "refused at CONNECT" in text
        assert "nothing here rests on a page this branch opened" in text

    def test_it_records_the_verified_identities_and_revisions(self) -> None:
        text = FREEZE_DOCUMENT.read_text(encoding="utf-8")
        for fact in ("445354", "68-26-8", "338", "69-72-7",
                     "2026-01-02", "2021-05-25", "2024-09-03"):
            assert fact in text, fact

    def test_it_records_the_corrected_publisher_and_why(self) -> None:
        text = FREEZE_DOCUMENT.read_text(encoding="utf-8")
        assert "Wiley Periodicals LLC" in text
        assert "Elsevier Inc." in text
        assert "journal" in text.lower() and "not the publisher" in text.lower()

    def test_it_keeps_the_salicylic_boundary_described_as_a_restriction(self) -> None:
        # Must never drift into sounding like evidence of tolerance.
        text = FREEZE_DOCUMENT.read_text(encoding="utf-8")
        assert "conservative eligibility boundary, not a scientific finding" in text
        assert "guarantees anyone will tolerate anything" in text

    def test_it_records_that_not_enough_information_remains_legitimate(self) -> None:
        text = FREEZE_DOCUMENT.read_text(encoding="utf-8")
        assert "NOT_ENOUGH_INFORMATION" in text
        assert "NOT_ENOUGH_DECISION_POLICY" in text

    def test_no_deferred_candidate_became_a_sixth_pack(self) -> None:
        keys = {pack.SUBSTANCE_KEY for _, pack in REVIEWED_ROSTER}
        for deferred in (
            "hyaluronic_acid", "dimethicone", "niacinamide", "glycolic_acid", "lactic_acid",
            "azelaic_acid", "benzoyl_peroxide", "ceramides", "urea", "zinc_oxide",
            "titanium_dioxide", "squalane",
        ):
            assert deferred not in keys, deferred

    def test_fragrance_preference_is_not_used_as_evidence(self) -> None:
        # care_fragrance_preference is a preference, not a scientific fact. No
        # pack may reach for it.
        for _, pack in REVIEWED_ROSTER:
            assert "care_fragrance_preference" not in _pack_source(pack).read_text(
                encoding="utf-8"
            ), pack.PACK_ID
