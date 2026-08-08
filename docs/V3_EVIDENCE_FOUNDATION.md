# V3-02 Evidence & Ingredient Intelligence Foundation

**Status:** architecture audit only (V3-02.0)
**Baseline:** `main` at `53296f8a2cac39ca1cfafb7805a4197c056d5a8d`
**Branch:** `v3/v3-02-evidence-foundation`

This document records the repository truth at the V3-02 baseline and proposes a
future evidence contract. It does not add tables, migrations, endpoints,
dependencies, parsers, safety rules, RAG, or product behaviour.

## 1. Executive summary

GlamGenius already has a useful deterministic ingredient/routine foundation. The
runtime ontology in `backend/app/domains/routines/ontology.py` is the canonical
parser/rule input; `backend/app/bootstrap/__init__.py` mirrors much of it into
global reference tables. User product facts are separate and account-owned.
Low-confidence extraction is explicitly draft-only, and warnings carry a
deterministic `rule_id`.

The current system is curated internal knowledge, not a structured scientific
evidence system. Words such as “reviewed” in comments describe internal curation
and safety review; they do not prove clinical verification, regulatory approval,
or independent validation. Existing operational rules should therefore be
classified as `legacy_curated` until a future evidence record is deliberately
linked and approved. No evidence should be backfilled merely from prose already
in the repository.

The recommended permanent shape is a shared Evidence domain containing source
identity, claims, links, provenance, review lifecycle, and supersession. Skin,
Hair, Home Care, Nutrition, Supplements, Product Quality, and Purchase
Intelligence remain owning domains for behaviour. Evidence is an input and audit
trail, not a generic rule engine or product-quality score.

## 2. Current repository truth

### 2.1 Current data-flow

```mermaid
flowchart TD
    A[ontology.py and nutrition.py] --> B[bootstrap seed]
    B --> C[(global reference tables)]
    D[User labels / declared ingredients] --> E[parser.py]
    E --> F[ProductIngredient draft or confirmed fact]
    F --> G[routines rules and shelf engine]
    C --> G
    H[AI extraction] --> I[account-owned draft inventory]
    I --> F
    G --> J[routine / ingredient / shelf APIs]
    K[AI explanation] --> J
    L[privacy registry and export] --> M[account-owned export]
    C -. excluded from account export .-> L
```

The AI gateway may extract or explain structured data, but it is not the source
of the deterministic compatibility, contraindication, sensitivity, or routine
rules. `backend/app/domains/routines/explanation.py` validates explanatory
language after the deterministic result exists; it does not choose products or
invent warnings.

### 2.2 Exact paths audited

| Concern | Current path(s) | Finding |
| --- | --- | --- |
| Ingredient ontology | `backend/app/domains/routines/ontology.py` | In-process canonical catalogue, aliases, families, compatibility, slots, climate and perfume conventions. |
| Relational routine/reference models | `backend/app/domains/routines/models.py` | `Ingredient`, `IngredientAlias`, `IngredientRule`, `CompatibilityRuleRow`, `ContraindicationRule`, `RoutineTemplate`, `PerfumeContextRule`, `AppearanceNutritionRule`, `ProductIngredient`, routine provenance models. |
| Additional reference models | `backend/app/domains/reference/__init__.py` | Seed audit, routine steps, supplement context, contraindication and sensitivity rows, inventory subtype definitions. |
| Ingredient parser | `backend/app/domains/routines/parser.py` | Normalises text, longest-first alias matching, confidence and confirmation boundary, unmatched terms. |
| Rule evaluation | `backend/app/domains/routines/rules.py` | Deterministic allergy, compatibility, expiry, slot, low-use and unconfirmed findings; every finding has `rule_id`. |
| Shelf/product intelligence | `backend/app/domains/routines/shelf.py`, `backend/app/domains/routines/service.py`, `backend/app/api/v2/shelf.py` | Confirmed inventory only drives compatibility; overlap, expiry, low-use and supplement flags are computed. |
| Routine generation | `backend/app/domains/routines/compiler.py`, `service.py`, `schemas.py`, `api/v2/routines.py` | Compiles owned products into routines and records `knowledge_version`/engine metadata. |
| Nutrition | `backend/app/domains/routines/nutrition.py`, `api/v2/routines.py` | Qualitative nutrient associations and common foods with diet filtering; no quantities or RDA/EAR/TUL engine. |
| Supplement context | `backend/app/bootstrap/__init__.py`, `backend/app/domains/reference/__init__.py`, `backend/app/domains/routines/shelf.py`, `safety.py`, `api/v2/routines.py` | General inventory/wellness context, expiry and professional-boundary flags; no dosage, deficiency diagnosis or prescribing. |
| Product extraction | `backend/app/domains/inventory/extraction.py`, `inventory/service.py`, `inventory/models.py`, `api/v2/inventory.py` | AI extraction creates account-owned draft records with model/prompt/schema provenance and confirmation state. |
| Product ingredient persistence | `backend/app/domains/routines/models.py`, `api/v2/routines.py` | `ProductIngredient` is account-owned and linked to canonical ingredient keys; `source`, `confidence`, `needs_confirmation`, `confirmed_at`. |
| Product Check / shopping | `backend/app/domains/recommendation/service.py`, `orchestrator.py`, `explanation.py`, `roi.py`, `models.py`, `api/v2/shopping.py` | Purchase evaluation is account-owned and versioned by `ROI_VERSION`; it is not evidence-backed product quality. |
| Style compatibility | `backend/app/domains/recommendation/compatibility.py`, `ranking.py`, `orchestrator.py` | Apparel/style compatibility, not ingredient safety. Keep ownership separate. |
| Reference seed | `backend/app/bootstrap/__init__.py`, CLI wrapper `backend/app/bootstrap/reference_data.py` | Idempotent, versioned seed; `SEED_VERSION = "2026.02.16"`; records `seed_version_records`. |
| Privacy/export | `backend/app/domains/privacy/__init__.py`, `export.py`, `deletion_service.py`, `api/v2/privacy.py` | Explicit table registry separates `INCLUDED`, operational, secret and `NOT_USER_OWNED`; global reference tables are excluded from account export. |
| Critical/regression tests | `backend/tests/test_domain_routines.py`, `test_reference_data_seed.py`, `test_critical_journey*.py`, `test_privacy*.py` | Tests cover seed parity/idempotence, parser/rule boundaries, account isolation and export classification. |

## 3. Current source-of-truth matrix

| Knowledge | Current code source | DB table | Seed source | Runtime consumer | Truth classification |
| --- | --- | --- | --- | --- | --- |
| Ingredients | `ontology.INGREDIENTS` | `ingredients` | `seed_ingredients()` mirrors ontology | parser, routines service, ingredient API | Code-owned curated catalogue mirrored to DB |
| Aliases / INCI spellings | `ontology.alias_index()` | `ingredient_aliases` | `seed_ingredients()` | parser and ingredient lookup | Derived from ontology; DB mirror |
| Ingredient families | `Ingredient.family` in ontology | `ingredients.family` | ontology mirror | compatibility, overlap, slot/routine logic | Curated categorical data |
| Single-ingredient notes | `IngredientRule` model exists; no active ontology/seed consumer found | `ingredient_rules` registered/excluded in privacy registry | No active seed function found | No current runtime consumer found | Stale/dead model surface; not evidence |
| Pairwise compatibility | `ontology.COMPATIBILITY_RULES` | `compatibility_rules` | `seed_ingredients()` | `rules.compatibility_findings()` | Curated internal rules; operational |
| Contraindications | `CONTRAINDICATION_DEFS` in `bootstrap/__init__.py` | `ingredient_contraindication_rules` | `seed_contraindications()` | Seed/test/reference consumers; not a generic external evidence link | Curated safety rules |
| Sensitivity | `SENSITIVITY_DEFS` in `bootstrap/__init__.py` | `ingredient_sensitivity_rules` | `seed_sensitivities()` | Seed/test/reference consumers; softer cautions | Curated sensitivity rules |
| Routine templates | `ROUTINE_TEMPLATE_DEFS`/`ROUTINE_STEP_DEFS` in bootstrap | `routine_templates`, `routine_template_steps` | `seed_routine_templates()` | compiler/service/UI | Curated product behaviour |
| Skin/Hair steps | bootstrap step definitions plus ontology slots | template tables and compiled account routines | `seed_routine_templates()` | compiler and routine API | Curated operational guidance |
| Appearance nutrition | `NUTRIENT_RULES`/food lists in `nutrition.py`; `AppearanceNutritionRule` model is not actively seeded | `appearance_nutrition_rules` registered as global | No active appearance-rule seed found | `nutrition.suggestions()` reads module constants | Hardcoded qualitative curated content |
| Supplement context | `SUPPLEMENT_CONTEXT_DEFS` in bootstrap and safety constants | `supplement_context_rules`, `supplement_safety_flags` | `seed_supplement_context()` | shelf and supplement API | Curated general context; user flags are derived/account-owned |
| Perfume context | `PERFUME_RULES` in ontology and `PERFUME_CONTEXT_DEFS` in bootstrap | `perfume_context_rules` | `seed_perfume_context()` | perfume service/API | Curated convention, explicitly not chemistry evidence |
| Product ingredient matching | parser plus user details | `product_ingredients` | Not global-seeded; created from account products | shelf/rules/ingredient APIs | User-confirmed or draft user fact |
| Product quality / shopping | recommendation modules and ROI constants | `purchase_evaluations`, factors, decisions | No evidence seed | shopping API | Account-owned evaluation, not verified evidence |

### 3.1 Duplicate and drift risks

There are two intentional but fragile representations of some reference data:
the executable ontology/constants and relational seed tables. The seed tests
currently protect ingredient/alias/compatibility parity, and seed version rows
protect replay visibility. However, `IngredientRule`, `ContraindicationRule`,
and `AppearanceNutritionRule` in `routines/models.py` are not the same as the
active `domains/reference` tables/constants and appear to be legacy surfaces.
The architecture must not add a third copy in an Evidence table.

The current version mechanism is fragmented: `phase6-v1` ontology/routine
versions, date-like `SEED_VERSION`, planning/recommendation engine versions,
AI prompt/schema versions, and API/export schema versions. These are useful
provenance fragments but cannot answer the complete question “which source and
evidence release produced this recommendation?”

No AI-generated safety rule or evidence claim was found. AI outputs are stored
as account-owned `ai_runs`/outputs or draft inventory and are filtered by
structured schemas and safety checks.

## 4. Existing ingredient architecture

`ontology.Ingredient` contains `key`, display name, INCI name, family, summary,
common use and aliases. The alias index includes the canonical key, display and
INCI forms plus explicit aliases. `parser._match_in()` sorts aliases by length,
uses case-normalised text and word boundaries, removes matched text before
shorter matching, and preserves label position. `parse_product()` gives the
user-declared active list precedence over label text and de-duplicates by
canonical key.

The relational `Ingredient` table mirrors the ontology. `ProductIngredient` is
not global knowledge: it belongs to an account and inventory item, stores the
matched text/position, confidence and source, and requires confirmation below
the threshold. Unknown label text is surfaced by `unmatched_terms()` rather
than mapped to a nearby ingredient. This is the correct boundary to preserve.

### Ingredient gap classification

| Future attribute | V3-02 foundation | V3-03 Skin/Hair | V3-05 Product Quality | Decision |
| --- | --- | --- | --- | --- |
| Canonical INCI identity and stable key | Yes: evidence subject seam and stable identity contract | No new behaviour | Label/formulation joins later | Extend identity deliberately, do not duplicate parser keys |
| Marketing aliases / regional spellings | Yes: provenance-aware alias ownership contract | No | Label ingestion may add candidates | Unknown remains unknown |
| Ingredient family | Yes: controlled subject reference | Domain-owned | Formulation interpretation later | Keep family out of EvidenceClaim prose |
| Skin applicability / hair applicability | No behaviour yet; claim subject type only | Yes | Product context may qualify | Domain rule, not generic evidence field |
| Rinse-off vs leave-on | No | Yes | Product formulation context | Domain/product seam |
| Functional categories | Claim vocabulary candidate | Yes | Yes for formulation assessment | Controlled category, never arbitrary safety prose only |
| Concentration known/unknown | No | Domain rule input later | Yes, label/formulation provenance | Evidence cannot infer concentration |
| pH / vehicle relevance | No | Domain rule input later | Product formulation | Must remain unknown when absent |
| Fragrance/allergen context | Subject/claim category only | Yes | Label verification | Jurisdiction-sensitive and not universal |
| Cosmetic / OTC / regulated / professional context | Yes: controlled regulatory-context vocabulary | Domain handling | Product jurisdiction | Required boundary before stronger claims |

## 5. Existing nutrition architecture

`backend/app/domains/routines/nutrition.py` defines qualitative
`NutrientRule` records and common Indian `Food` sources. `suggestions()` resolves
diet aliases, filters foods, returns rule IDs, context and a safety disclaimer,
and rejects unsafe language. There are no numerical nutrient quantities,
RDA/EAR/TUL values, deficiency diagnoses, calorie targets, or food-composition
lookups. `NutritionPreference` and `HydrationPreference` are account-owned
constraints/preferences. V3-02 must preserve this behaviour and classify it as
`legacy_curated_general_wellness`, not promote it to approved nutrition
evidence. Quantitative nutrition belongs to V3-04.

## 6. Existing supplement architecture

Supplement inventory uses `inventory_items` plus account-owned
`supplement_details` and `supplement_safety_flags`. Seeded global context rows
are `SupplementContextRule` with `context_key`, guidance, `safety_category` and
seed version. The safety module permits only inventory dates, general context,
professional-boundary wording and pregnancy flags. It explicitly rejects dose,
effect, diagnosis, treatment, prescription and medication-substitution claims.
V3-02 may define provenance seams; it must not add dosing, deficiency treatment,
or prescribing.

## 7. Existing parser and confirmation architecture

The parser uses `SOURCE_USER`, `SOURCE_LABEL`, and `SOURCE_EXTRACTED` with base
confidence `1.0`, `0.85`, and `0.55`; `CONFIRMATION_THRESHOLD = 0.6`. A low
confidence row is returned as `needs_confirmation` and cannot drive a
compatibility warning until the user confirms it. Position is retained for
label parsing; declared ingredients have no position. Duplicate canonical keys
are collapsed in `parse_product()`, with declared values winning.

V3-02.1 should keep parser output as a deterministic candidate fact. LLMs may
assist extraction into a draft, but an unknown token must remain unknown and
must never be nearest-neighbour mapped into `ingredients`.

## 8. Domain ownership decision

### Evidence domain owns

Source identity and metadata, source version/publication dates, jurisdiction,
source type, citations/locators, structured claim provenance, evidence
classification, review lifecycle, supersession and evidence-release history.

### Domain owners retain behaviour

Skin/Hair/Home Care own ingredient behaviour, placement, compatibility,
frequency, order, sensitivity, environment interaction and action guidance.
Nutrition owns nutrient requirements, food composition, serving contribution and
diet filtering later. Supplements own label quantities, duplicate exposure,
RDA/TUL comparison and regulatory context later. Product Quality owns
formulation, label/claim verification, packaging/stability, lab provenance and
value/overlap. Purchase Intelligence owns user-facing purchase decisions.

Evidence may qualify or support a domain rule; it does not become a god-domain
containing every domain's behaviour.

## 9. Proposed shared Evidence model (design only)

The following is a minimum relational contract for V3-02.1. Names are proposed,
not implemented.

### 9.1 EvidenceSource

**Owner:** Evidence domain. **Purpose:** one stable, deduplicated identity for an
external reference or product-provenance source.

Required fields:

* `id` (UUID primary key), `source_key` (stable unique key), `source_type`
  (controlled enum), `publisher`, `title`, `jurisdiction`, `canonical_url`;
* `publication_date`, `version_or_revision`, `accessed_at`, `status`,
  `license_or_use_note`, `created_at`, `updated_at`;
* optional `superseded_by_source_id`, `last_reviewed_at`,
  `next_review_due_at`.

Constraints/indexes: unique `source_key`; canonical URL uniqueness only when
present; indexes on `(source_type, jurisdiction, status)` and
`(next_review_due_at, status)`; no copied source document body.

Versioning/review: source revision is metadata, not an evidence-strength score.
An updated regulation or label is a new source revision linked by
`superseded_by_source_id`; approval is a separate claim lifecycle decision.

Consumers: domain rule review tools and read-only domain services. It does not
own ingredient behaviour, product scores, user observations, or account data.

### 9.2 EvidenceClaim

**Owner:** Evidence domain, with domain-owner review. **Purpose:** one structured
assertion whose scope and status can be audited without pretending that source
count equals certainty.

Required fields:

* `id`, unique `claim_key`, `domain`, `subject_type`, `subject_key`,
  `claim_type`, `summary`, `scope`;
* `evidence_strength`, `claim_status`, `review_status`, `knowledge_version`,
  `rule_version`, `created_at`, `updated_at`;
* `reviewed_at`, `reviewed_by` (or an auditable reviewer identity),
  `last_reviewed_at`, `next_review_due_at`, optional `superseded_by_claim_id`;
* `ai_generated` (provenance flag), `drafted_by_run_id` when applicable,
  `regulatory_context`, and a structured `structured_value` only for controlled
  claim types.

Constraints/indexes: unique `claim_key`; foreign-key self-reference for
supersession; indexes on `(domain, subject_type, subject_key, claim_status)`,
`(review_status, next_review_due_at)`, and `(knowledge_version, rule_version)`.
Claims marked `approved` must have at least one valid source link and a human
approval record. `ai_generated=true` is never sufficient for approval.

Consumers: domain-specific rule layers may select active approved claims within
their domain and scope. EvidenceClaim does not itself decide safety action,
dosage, compatibility, product quality, or a recommendation.

### 9.3 EvidenceClaimSource

**Owner:** Evidence domain. **Purpose:** many-to-many link between a claim and
the sources that support, qualify, limit, contradict or contextualise it.

Required fields: `claim_id`, `source_id`, `relationship`, `locator`,
`review_note`, `created_at`, `reviewed_at`, `reviewed_by`.

Constraints: unique `(claim_id, source_id, relationship, locator)`; foreign keys
to both records; `relationship` is controlled (`supports`, `qualifies`,
`limits`, `contradicts`, `background`). A claim cannot be approved if its links
are missing, inactive, or have no locator/review note where a locator is
available. Store concise structured summaries and short legally appropriate
quotes only; never bulk-copy papers, guidelines or paid databases.

## 10. Controlled vocabularies

Source types: `official_regulation`, `official_guideline`,
`government_reference`, `systematic_review`, `peer_reviewed_research`,
`professional_consensus`, `ingredient_reference_database`,
`manufacturer_label`, `manufacturer_technical_document`, `manufacturer_claim`,
`independent_lab_report`, `traditional_reference`, `other`.

Evidence strength: `strong`, `moderate`, `limited`, `traditional_uncertain`,
`insufficient`. This is a qualitative classification, never a numerical trust
score and never derived from source count.

Claim status: `supported`, `qualified`, `conflicting`, `unsupported`.

Review lifecycle: `draft`, `reviewed`, `approved`, `superseded`, `retired`.
Approval requires human/domain-owner review; AI-generated drafts may be
reviewed but cannot self-promote.

Source status: `active`, `superseded`, `retired`, `unavailable`.

Product provenance seam (future Product Quality):
`independent_lab_verified`, `manufacturer_coa_or_test`,
`regulatory_information_verified`, `ingredient_label_verified`,
`manufacturer_claim_only`, `insufficient_evidence`. This represents provenance,
not a product score.

Regulatory context: `cosmetic`, `otc_or_regulated`, `professional_guidance_required`,
`jurisdiction_sensitive`, `unknown`.

## 11. Review, AI and safety boundary

AI may summarise, explain, translate, or draft candidate source/claim records.
AI may not invent safety rules, interactions, clinical claims, RDA values, upper
limits, lab results or regulatory approvals. AI may not promote a draft to
`approved`. Approval must be represented by a human reviewer identity,
timestamp, lifecycle transition and valid source links. New safety-critical
domain rules should require an approved evidence link after the migration
parity period; existing rules remain operational but are labelled
`legacy_curated` until reviewed. No current rule may be retroactively described
as clinically reviewed without evidence.

## 12. Versioning, supersession and staleness

V3-02.1 should introduce an explicit evidence release identifier alongside the
existing independent versions:

* evidence release: the published set of active claims/sources;
* source revision: publisher revision/publication metadata;
* claim version: immutable revision of one structured assertion;
* rule version: domain rule code/data version consuming the claim;
* seed version: current bootstrap application marker;
* recommendation run: records selected evidence release, rule version and
  engine version at execution time.

Rows should be immutable by revision where practical. Superseding a source or
claim creates a new revision and points back with `superseded_by`; it does not
rewrite the historical record. Selection must exclude `superseded`, `retired`,
expired or out-of-scope claims. `last_reviewed_at` and `next_review_due_at`
support staleness reporting; V3-02.0 does not build a scheduler.

The recommendation audit answer then becomes: account/run date, engine/rule
version, evidence release, selected claim IDs, and source revisions. Git history
is supplemental, not the only provenance.

## 13. Copyright and data-use boundary

Ingestion stores source metadata, canonical URL, license/use note, structured
internal facts, and precise locators. It does not store entire copyrighted
documents, scraped corpora, or wholesale guideline text. Short quotations are
allowed only when legally and operationally appropriate. Paid/proprietary
nutrition or research references require an explicit licence decision before
being used as a source.

## 14. Privacy boundary

EvidenceSource, EvidenceClaim and links are global reference data and should be
classified `NOT_USER_OWNED`/operational in the privacy registry. They must never
contain account IDs, user health observations, reactions, owned products, or
personal confirmations. `ProductIngredient`, confirmations, reactions, owned
products, routine feedback, shopping decisions and AI runs remain account-owned
and included according to the existing privacy registry/export. A future
evidence link can reference a global ingredient key, but it must not copy a
user's label or health-like observation into global tables.

## 15. RAG decision

Do not make a vector database or RAG system the source of truth for safety or
evidence. Structured reviewed evidence must remain authoritative:

```text
structured reviewed evidence
          ↓
optional semantic retrieval
          ↓
AI explanation with claim/source IDs
```

The unsafe shape is random retrieved text followed by an LLM deciding what is
safe. Semantic search may improve discovery later, but it cannot approve claims,
replace controlled status, or bypass domain rules. No vector database,
embeddings, LangChain, LlamaIndex, scraping or live research API belongs in
V3-02.0.

## 16. V3-02.1 migration strategy (design only)

1. Add EvidenceSource, EvidenceClaim and EvidenceClaimSource infrastructure,
   lifecycle constraints and a global privacy classification.
2. Add a deterministic evidence-release/selection service with no runtime
   behaviour change when no link exists.
3. Link a small, explicitly reviewed subset of existing compatibility,
   contraindication and sensitivity rules; mark all unlinked rows
   `legacy_curated`.
4. Preserve current ontology, parser, seed and rule outputs. Compare old and
   evidence-linked outputs for parity before enabling any new gate.
5. Record selected evidence/rule release IDs on future recommendation runs.
6. After parity and human review, require approved evidence for newly introduced
   safety-critical rules; do not rewrite every existing table immediately.

No current table is replaced, and no current runtime rule is deleted by this
strategy.

### Proposed exact V3-02.1 files

* `backend/app/domains/evidence/__init__.py`
* `backend/app/domains/evidence/models.py`
* `backend/app/domains/evidence/enums.py`
* `backend/app/domains/evidence/service.py`
* `backend/app/domains/evidence/schemas.py`
* `backend/app/domains/evidence/repository.py`
* `backend/app/shared/database/registry.py` (model registration only)
* `backend/app/domains/privacy/__init__.py` (global classification)
* `backend/app/bootstrap/__init__.py` (evidence seed/version hook only)
* a new Alembic migration under `backend/migrations/versions/`
* `backend/tests/test_domain_evidence.py`
* `backend/tests/test_evidence_lifecycle.py`
* `backend/tests/test_evidence_seed.py`
* `backend/tests/test_evidence_privacy.py`
* `backend/tests/test_evidence_parity.py`

These are proposals, not files created in V3-02.0.

### Proposed V3-02.1 tests

At minimum:

* unique source and claim IDs; required source/version metadata;
* approved claims reject missing, inactive or unreviewed source links;
* unknown evidence status, lifecycle or relationship values are rejected;
* superseded/retired claims cannot be selected as active;
* AI/draft claims cannot drive safety behaviour or become approved without a
  human transition;
* rule-to-evidence links resolve and preserve domain ownership;
* legacy rules remain backward-compatible and output-parity tests pass;
* source revisions/supersession are auditable;
* deterministic, idempotent seed and evidence-release selection;
* no cross-account contamination and correct global-vs-account privacy export;
* unknown ingredient label text remains unknown through extraction and parser
  boundaries.

## 17. Explicitly deferred work

* **V3-03:** new Skin/Hair ingredient behaviour, formulation context,
  concentration/pH/vehicle handling, and expanded care rules.
* **V3-04:** authoritative quantitative nutrition references, RDA/EAR/TUL,
  food composition, serving contribution and diet calculations.
* **V3-05:** product-quality scoring, claim verification, label/formulation
  provenance, lab/COA workflows, packaging/stability and purchase intelligence
  changes.
* Home Care/Indian remedies library, live research ingestion, scraping, RAG,
  embeddings, dosage or medical/deficiency logic.

## 18. Open risks requiring CTO/CPO approval

1. Decide which existing curated rules receive human/domain-owner review first;
   do not imply that all current “reviewed” comments are scientific evidence.
2. Approve source licensing and retention rules before nutrition or proprietary
   research ingestion.
3. Approve jurisdiction policy for cosmetic, OTC, professional and traditional
   claims.
4. Decide whether evidence-linked rules are advisory-only initially or become a
   hard requirement for new safety-critical rules after parity.
5. Decide the immutable evidence-release retention period and audit visibility.
6. Confirm Product Quality provenance statuses are separate from quality scores.
7. Confirm domain owners and human approvers for Skin/Hair, Nutrition,
   Supplements and Product Quality.

## 19. V3-02.0 scope and validation

This audit changed documentation only. It created no migration, table, endpoint,
frontend change, production dependency, RAG/vector component, or runtime rule.
The required validation for this phase is repository-level:

```text
git diff main...HEAD
git status --short
```

The final commit must contain only this document and use:

```text
docs: define V3-02 evidence foundation
```

The branch is intended for a draft PR against `main`; it must not be merged and
V3-02.1/V3-03 must not begin in this task.
