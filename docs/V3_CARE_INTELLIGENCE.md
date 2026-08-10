# V3-03 Skin & Hair Care Intelligence

**Phase:** V3-03.4 Minimum-Effective Care Routine Planning Foundation
**Baseline requested and audited:** `5c486aff6a2efab154686a7f7e7a30081733ceaa`
**Branch:** `v3/v3-03-4-care-routine-planning`
**Status:** Pure deterministic Care routine planning is implemented but not activated in routine APIs, stored routines, Today, or `routines_today`; no schema, dependency, frontend, or Evidence change is included.

## 1. Executive summary

GlamGenius already has a useful deterministic shelf and routine foundation. It
can classify owned Skin (`beauty`) and Hair (`hair`) products, parse a limited
ingredient ontology, apply reviewed compatibility rules, exclude products that
match a user-declared allergy, calculate expiry/low-use findings, and compile
owned products into five routine shapes. It also has a separate Context Engine
for weather, air quality, regional climate, and events.

The current system is not yet a Care decision engine. Routine compilation reads
owned products, the confirmed canonical ProfileAttribute `allergies` through
the existing shelf boundary, and a coarse climate string. It does
not consume the normalized `DayContext`, user-declared Skin/Hair care facts,
structured observations, or event importance/timing. Hair has first-class
slots but not first-class personalization. V3-02 Evidence Provenance is merged
at this baseline, but production has zero approved Evidence claims and current
Care rules remain legacy-curated deterministic rules.

The canonical user-entered allergy is ProfileAttribute `allergies`; it is the
current ShelfContext/routine input. The existing
`UserConstraint(kind="allergy")` remains a synchronized projection retained by
Profile infrastructure, not the current ShelfContext source. Care adds no
second allergy field or matching path.

The permanent design is therefore a fact assembler plus a deterministic Care
decision layer in front of the existing compiler. V3-03.1 now implements only
account-owned declarative Skin/Hair context, shared Care preferences, registry
validation, an in-memory CareContext, a read-only Context adapter, exact legacy
Hair fallback, shelf fact reuse, and structured missing/source contracts. It
does not change recommendation behavior.

## 2. V3-03 mission

Care must answer “what should I use from what I own today?” with the minimum
effective routine, clear safety boundaries, environmental adjustments,
event-readiness support, low decision fatigue, and high use of owned products.
It must never diagnose, prescribe treatment, invent ingredients or product
facts, turn every empty slot into a purchase, or expose an attractiveness
score. The permanent owned-first policy hierarchy is:

1. hard safety and expiry exclusions;
2. confirmed user constraints;
3. confirmed owned safe, suitable products;
4. routine simplification and current context (environment, events, effort);
5. an unresolved required gap only when a genuine core step remains;
6. Purchase Intelligence later, never as a V3-03 foundation side effect.

## 3. Current repository truth

The internal inventory key `beauty` and labels such as `Beauty Shelf` are
legacy compatibility names. The future customer taxonomy is **Skin Care**,
**Hair Care**, **Care**, **Routine**, and **Shelf**. Renaming the internal key is
out of scope for V3-03.

No new table, migration, API, dependency, frontend file, prompt, RAG layer,
or recommendation behavior is introduced by this audit.

## 4. Existing Skin architecture

The exact current Skin product model is:

- `backend/app/domains/inventory/models.py:BeautyProductDetail`
  (`beauty_product_details`, one-to-one with `inventory_items`):
  `product_type`, `size`, `opened_date`, `expiry_date`,
  `period_after_opening_months`, `purpose`, `ingredients_text`,
  `active_ingredients`, `use_frequency`, `routine_position`, and
  `remaining_percent`.
- `backend/app/domains/inventory/models.py:InventoryItem` owns the account,
  category (`beauty`), display name, brand, verification state, confidence,
  status, usage, condition, and source metadata.
- `backend/app/domains/inventory/service.py` validates and persists detail and
  attribute values, expiry events, usage, condition, confirmation, and archive
  state. `backend/app/domains/inventory/extraction.py` creates `draft` rows
  from visible evidence; user confirmation is required before they become
  confirmed facts.
- `backend/app/domains/routines/parser.py` parses `ingredients_text`,
  declared `active_ingredients`, and extracted values into `ParsedIngredient`
  rows. Unknown terms stay unmatched.
- `backend/app/domains/routines/ontology.py` owns the reviewed Skin ingredient
  families, slots, and compatibility reference data. It is not a user profile.

Skin-specific user context is currently limited to generic profile attributes
(`skin_tone`, `undertone`, and `visible_skin_characteristics`), a free-text
appearance goal, and the generic `allergies` constraint. None of the Skin
comfort, dryness/oiliness, sensitivity tendency, fragrance preference, or
routine-effort facts are consumed by routine compilation.

## 5. Existing Hair architecture

The exact current Hair product model is:

- `backend/app/domains/inventory/models.py:HairProductDetail`
  (`hair_product_details`, one-to-one with `inventory_items`): the same
  product/expiry/ingredient/usage fields as Skin.
- `backend/app/domains/inventory/models.py:InventoryItem` with category
  `hair` supplies ownership, verification, confidence, status, usage, and
  source metadata.
- `backend/app/domains/routines/ontology.py:HAIR_SLOTS` and
  `PRODUCT_TYPE_SLOTS` map Hair product type wording to pre-wash oil, shampoo,
  scalp care, conditioner, mask, leave-in, heat protectant, and styling.
- `backend/app/domains/routines/rules.py` includes Hair protein/protein and
  silicone/buildup rules, but these are product/ingredient rules rather than a
  Hair profile.

The profile baseline can hold `hair_type`, `hair_texture`, and `hair_density`
as photo observations or user attributes, but those values have no controlled
Care vocabulary and are not read by `compile_routine`. Wash frequency,
chemical/colour processing, heat-styling frequency, scalp comfort, frizz
sensitivity, and styling preference are missing.

## 6. Existing routine architecture

The compiler is `backend/app/domains/routines/compiler.py`; persistence and
orchestration are in `backend/app/domains/routines/service.py`; shelf assembly
is in `backend/app/domains/routines/shelf.py`; rules are in
`backend/app/domains/routines/rules.py`; slots and climate notes are in
`backend/app/domains/routines/ontology.py`; request/response contracts are in
`backend/app/domains/routines/schemas.py`; wording boundaries are in
`backend/app/domains/routines/safety.py` and `safety_classifier.py`.

`compile_all()` can produce `morning`, `evening`, `wash_day`, `weekly`, and
`event`. `routines_today()` exposes only the time-relevant routines (and
weekend extras), so the product does not force all five onto the user at once.

### Current slots and truth

| Routine | Required slots | Optional slots | Frequency source |
| --- | --- | --- | --- |
| morning | cleanser, moisturiser, sunscreen | toner, treatment, eye | static `Every morning` |
| evening | cleanser, moisturiser | exfoliant, toner, treatment, eye, face_oil | static `Every evening`; exfoliant is two/three times weekly |
| wash_day | shampoo, conditioner | pre_wash_oil, scalp_care, leave_in, heat_protectant, styling | static `On the days you wash your hair` |
| weekly | none | exfoliant, hair_mask | static `Once or twice a week`; empty routine omitted |
| event | none | cleanser, moisturiser, sunscreen, heat_protectant, styling | static `The day of something that matters` |

Skin is `beauty` internally; Hair is `hair`. The event template can draw from
both categories but does not receive a canonical Event or a date/time.

### Frequency, ranking, gaps, alternatives, safety

- Frequency is template text plus two/three-times-weekly overrides for
  `exfoliant` and `hair_mask`; no user wash cadence, treatment collision, or
  adherence-derived frequency exists.
- `rank_for_slot()` considers only owned products already mapped to the slot.
  It sorts expired products last, prefers items expiring soon, then low-use
  items, then remaining percentage, then display name. It is auditable but not
  personalized to Skin/Hair facts or event context.
- Only required empty slots become gaps. Optional empty slots are omitted.
  Gap text names a generic category, never a brand or link. This is the current
  anti-upsell behavior.
- Alternatives are static plain-language fallbacks (“skip the heat tool” when
  there is no heat protectant, for example). They are not selected from a
  structured owned-product substitute graph.
- Findings always carry a deterministic `rule_id`. Allergy exclusions,
  confirmed ingredient compatibility, expiry, duplicate-slot, low-use, missing
  required slot, and unconfirmed ingredient findings are implemented.
- `routine.explanation` can ask AI to rewrite an already-made plan, but the
  schema cannot change product choice, severity, order, or rule IDs. The
  deterministic routine remains the source of truth.

## 7. Existing environmental integration

V3-01's environmental owner is `backend/app/domains/planning/context.py` and
`backend/app/domains/planning/environment.py`. `DayContext` gathers account
events, stored weather, AQI, profile city, owned inventory, and missing-data
notes. Its `ClimateContext` separates `season`/`calendar_prior` from observed
`temperature_band`, `moisture_regime`, `daily_regime`, `condition`,
`observed_signals`, `confidence`, `reason`, and regional source metadata.

Persisted inputs are `WeatherSnapshot` and `AirQualitySnapshot` in
`backend/app/domains/planning/models.py`; manual APIs are in
`backend/app/api/v2/today.py` and `backend/app/domains/planning/service.py`.
The current dimensions available to a future Care adapter are:

| Dimension | Current state | Care use now |
| --- | --- | --- |
| temperature | `temp_min_c`, `temp_max_c`, `temperature_band` | available in Context; not consumed by routines |
| humidity | weather field and `moisture_regime` | available in Context; routine API accepts only coarse climate |
| precipitation | chance and `wet`/`rain_likely` signal | available in Context; not consumed by routines |
| UV | optional `uv_index` | stored/available; no Care rule consumes it |
| AQI | normalized AQI snapshot | stored/available; no medical or Care adjustment |
| condition | normalized weather condition | available; routines accept a separate `climate` string |
| daily regime | e.g. `warm_wet` | available; not passed to routine compiler |
| confidence/reason | deterministic Context metadata | available; not represented in routine decisions |

There must be one Context Engine. Do not create `SkinWeather`, `HairWeather`,
or `CareClimateProvider`. Care should consume a read-only adapter over a
`DayContext`/snapshot reference.

## 8. Existing Event integration

Events are owned by Planning: `CalendarEvent` and `DayEvent` live in
`backend/app/domains/planning/models.py` and `context.py`; creation and
correction are exposed through `backend/app/api/v2/today.py` and
`backend/app/api/v2/integrations.py`. Events have title, start/end, location,
occasion key, dress-code hint, inference confidence, confirmation state, and
source/status.

`DayContext.primary_event` selects the most formal event for the outfit planner.
No event is passed into `generate_routines()` or `compile_all()`. The current
`event` routine is therefore a generic template, not Event Ready Care. There is
no importance field, maintenance countdown, skin/hair preparation timeline, or
event-specific owned-product plan in the baseline.

## 9. Existing observation/adherence model

`UserReportedObservation` in `backend/app/domains/routines/models.py` and the
`POST/GET /api/v2/routines/observations` routes in
`backend/app/api/v2/routines.py` store the user's `area`, date, note, and
optional item ID verbatim. `record_observation()` only runs the safety boundary
classifier to decide whether to attach a professional-boundary response; it
does not translate “my scalp feels itchy” into a diagnosis or profile fact.

`RoutineAdherence` records completion, date, and an optional note per routine
step. `shelf.consistency()` reports activity over a window and explicitly says
that missing a day is not a failure; no streak or shame language is used.
Adherence is not yet used to simplify or re-rank a routine.

## 10. Existing ingredient/evidence integration

On the audited local baseline, the ingredient authority is the deterministic
ontology in `backend/app/domains/routines/ontology.py`, seeded into global
reference tables by `backend/app/bootstrap/__init__.py` and represented by
`Ingredient`, `IngredientAlias`, `IngredientRule`, `CompatibilityRuleRow`, and
`ContraindicationRule` in `backend/app/domains/routines/models.py`.
Account-owned parsed facts are `ProductIngredient` rows. A low-confidence read
is exposed as `needs_confirmation` and cannot trigger compatibility warnings;
only confirmed families are used by `_rule_applies()`.

V3-02 is present on the authoritative baseline at
`backend/app/domains/evidence/{__init__.py,enums.py,models.py,seed.py,service.py}`
with migration `backend/migrations/versions/f3e02e1b7a91_add_v3_02_evidence_provenance.py`
and architecture documentation in `docs/V3_EVIDENCE_FOUNDATION.md`. The
global models are `EvidenceSource`, `EvidenceClaim`, `EvidenceClaimSource`,
and `RuleEvidenceLink`; the pilot seed is draft-only and production currently
has zero approved claims. Existing Care behavior therefore remains
**legacy-curated deterministic** until an approved evidence link is explicitly
eligible. V3-03.0 consumes this architecture on the read-only design seam and
does not modify V3-02 code.

## 11. Current weaknesses

1. `compile_routine()` is both decision logic and rendering shape; it has no
   separate Care fact set or decision object.
2. Skin and Hair profile context is mostly missing or unstructured.
3. The routines path accepts a coarse request/profile `climate` string instead
   of `DayContext`; humidity, precipitation, UV, AQI, daily regime, and
   confidence are dropped.
4. Season and climate are duplicated in `LifestyleContext`, routine schemas,
   perfume queries, and Context Engine contracts.
5. Product role and ingredient behavior are adjacent but not represented as
   separate decision inputs.
6. Routine load, duplicate purpose, frequency collision, effort, and user
   feedback are not modeled.
7. Hair wash cadence, chemical/colour processing, heat habits, scalp comfort,
   humidity/frizz sensitivity, and event styling are absent.
8. Event Ready has no Care plan, countdown, or maintenance ownership.
9. There are no structured reason codes or categorical Care confidence values.
10. There is no recommendation snapshot containing inputs, rules, context
    reference, or provenance.
11. The canonical allergy declaration is ProfileAttribute `allergies`; the
    existing `UserConstraint(kind="allergy")` is its synchronized projection.
12. V3-02 provenance exists, but the binary provenance state is not yet strong
    enough for Care behavior: a `background` relationship can count as linked
    without proving behavior eligibility.

## 12. Non-diagnostic boundary

Care facts are user declarations, product facts, observations, environmental
facts, and derived decisions. They are not diagnoses. The app may say “you
reported discomfort; consider pausing this product” or “the routine may be too
complex.” It must not say “you have eczema,” “your scalp condition is,” or
infer disease from a note, photo, weather value, or ingredient.

The profile vocabulary must use understandable declarations such as
`often_dry_or_tight`, `sometimes_reactive`, `wavy`, or `not_sure`. It must not
contain disease, severity-of-disease, treatment-stage, or condition-score
fields. Unknown is a first-class value and is never converted to a negative.

## 13. Skin fact taxonomy

The implemented Skin taxonomy is declarative and controlled, not diagnostic. These
keys belong in `backend/app/domains/profile/registry.py`, section `care_skin`,
and persist through the canonical `profile_attributes` table.

| Fact | Controlled values | Source/priority | Consumer |
| --- | --- | --- | --- |
| `care_skin_usual_feel` | `comfortable`, `often_dry_or_tight`, `often_oily`, `mixed`, `not_sure` | explicit user declaration; never photo-inferred | routine texture and step minimization |
| `care_skin_sensitivity` | `rarely_reactive`, `sometimes_reactive`, `often_reactive`, `not_sure` | explicit user declaration or explicit update | soft comfort preference; never a diagnosis |
| fragrance preference | shared Care preference | explicit user declaration | product ranking/filtering |
| preferred routine effort | shared Care preference | explicit user declaration | minimization only; cannot override safety |
| known allergy/avoidance | canonical `ProfileAttribute("allergies")`; projected to `UserConstraint(kind="allergy")`; the ProfileAttribute value is read by the existing ShelfContext when ingredient fact is confirmed | explicit user declaration | hard exclusion |
| professional restriction/prescribed topical | not stored in V3-03.1; dedicated sensitive design required | future explicit flow only | future deterministic safety gate |
| event skin-prep preference | shared event preference, not a skin condition | explicit user declaration/event | Event Care plan |

`skin_tone`, `undertone`, and `visible_skin_characteristics` remain Profile
appearance facts. They are not silently reinterpreted as Care conditions.

## 14. Hair fact taxonomy

| Fact | Controlled values | Source/priority | Consumer |
| --- | --- | --- | --- |
| `care_hair_pattern` | `straight`, `wavy`, `curly`, `coily`, `not_sure` | explicit declaration; not AI-observable | wash/styling fit |
| `care_hair_strand_characteristic` | `fine`, `medium`, `coarse`, `not_sure` | explicit declaration; not AI-observable | product weight and minimization |
| `care_hair_density` | `low`, `medium`, `high`, `not_sure` | explicit declaration; no photo diagnosis | styling effort only |
| `care_hair_wash_frequency` | `daily`, `several_times_week`, `weekly`, `less_than_weekly`, `variable`, `not_sure` | explicit declaration | wash-day scheduling |
| `care_hair_processing` | controlled list: `['none']`, `['not_sure']`, or one or more of `coloured`, `bleached`, `relaxed`, `permed_or_texturised`; empty list is invalid | explicit declaration; no legacy fallback | gentle sequencing and event prep |
| `care_heat_styling_frequency` | `never`, `occasional`, `frequent`, `daily`, `not_sure` | explicit declaration | heat-protection reminder |
| `care_scalp_usual_feel` | `comfortable`, `often_dry_or_tight`, `often_oily`, `sometimes_uncomfortable`, `not_sure` | explicit user declaration only; observations remain verbatim | comfort-aware simplification |
| `care_humidity_frizz_sensitivity` | `low`, `moderate`, `high`, `not_sure` | explicit declaration | environment styling adjustment |
| `care_hair_styling_preference` | `air_dry`, `heat_style`, `protective_style`, `mixed`, `not_sure` | explicit declaration | event and effort ranking |

`hair_type`, `hair_texture`, and `hair_density` from the existing Profile remain
legacy Appearance attributes. They are not deleted, renamed, or synchronized
into Care. Only these exact normalized fallback maps are allowed when the
corresponding `care_hair_*` key is absent: `hair_type` → `care_hair_pattern`
(`straight`, `wavy`, `curly`, `coily`), `hair_texture` →
`care_hair_strand_characteristic` (`fine`, `medium`, `coarse`), and
`hair_density` → `care_hair_density` (`low`, `medium`, `high`). Case and outer
whitespace may be normalized; synonyms and fuzzy mappings are forbidden.
Legacy fallback requires `verification_state="confirmed"` and an exact value;
`not_sure`, rejected, superseded, and unverified candidates are ignored.
`care_hair_processing` has no legacy fallback. New Care keys are explicitly not
AI-observable.

## 15. Shared Care preferences

Shared Care preferences also belong in canonical `ProfileAttribute` rows under
section `care_preferences`, rather than a second preference table:

- `care_routine_effort`: `minimal`, `balanced`, `detailed`, `not_sure`;
- `care_fragrance_preference`: `fragrance_free_preferred`, `no_preference`,
  `likes_fragrance`, `not_sure`;
- `care_event_preparation_effort`: `minimal`, `balanced`, `detailed`, `not_sure`.

These are soft factors. They can remove optional steps or choose among safe
owned alternatives; they can never override an allergy, expiry exclusion,
confirmed incompatibility, or a missing safety fact. Owned-first is a hard
GlamGenius product policy, not a user preference and not a stored attribute.

## 16. Stable facts vs temporary observations

Stable facts are explicit, account-owned declarations intended to remain until
the user edits or reviews them: Skin feel, Hair pattern, strand character,
wash frequency, processing, heat habits, and shared Care preferences.

Observations are dated, verbatim experiences such as “this stung yesterday,”
“hair felt dry,” or “rain made my hair frizzy.” They remain in
`UserReportedObservation` and do not mutate profiles. A later deterministic
policy may temporarily avoid the linked product, reduce optional complexity,
or ask the user whether to pause it. It may not infer allergy, disease, or
medical improvement.

Adherence is separate again: it records what the user did. Preference records
what the user likes. Observation records what the user noticed. Outcome claims
are not inferred.

## 17. Hard constraints vs soft preferences

Hard constraints are applied first: canonical ProfileAttribute `allergies`
projected through the existing ShelfContext and matched to a confirmed
ingredient; expired product; confirmed deterministic incompatibility;
and any future explicitly reviewed professional restriction. Hard constraints
produce a blocking decision and a reason code.

Soft factors include routine effort, texture preference, humidity preference,
event styling, usage recency, and duplicate purpose. They only rank or simplify
the set that remains after hard constraints. There is no single Care score.

## 18. Environment ownership

Planning/Context owns date, timezone, weather snapshots, AQI snapshots, regional
calendar priors, normalized conditions, confidence, and missing-data reasons.
Inventory owns product ownership, expiry, usage, and confirmation. Profile/Care
owns user declarations. Care consumes these through a read-only adapter.

Observed humidity, precipitation, temperature, UV, AQI, and daily regime outrank
season. Season is supporting context and remains `unknown` where V3-01 has no
reviewed regional profile. No universal `summer -> light skincare`,
`winter -> heavy skincare`, or `monsoon -> anti-frizz` rule is allowed.
Environmental adjustments must remain narrow and non-medical: texture/load,
sunscreen reminder context, heat-tool protection, or rain/humidity styling.

## 19. Evidence ownership

Evidence is global reference data, never account-owned. On the audited V3-02
baseline, Care may reference `EvidenceSource`, `EvidenceClaim`,
`EvidenceClaimSource`, and `RuleEvidenceLink`; it must not copy whole evidence
documents into routines or profiles.

Current mode is legacy curated deterministic rules with V3-02 provenance
available for audit. Future mode is `domain_rule + approved reviewed evidence
link`. Draft claims are never eligible for behavior.

## 20. Care decision pipeline

The permanent pipeline should be:

1. load account-owned stable Skin/Hair declarations and shared preferences;
2. load recent verbatim observations and adherence signals;
3. load the read-only `DayContext`/environment snapshot;
4. load confirmed owned Skin/Hair products, expiry, availability, usage;
5. load confirmed product ingredients and product roles;
6. apply hard exclusions before any ranking;
7. apply deterministic compatibility and placement constraints;
8. determine required core, contextual, optional, redundant, and pauseable
   steps separately for Skin and Hair;
9. rank safe owned products with deterministic, explainable factors;
10. minimize unnecessary steps using effort and current load;
11. apply environment and event adjustments with confidence/missing-data gates;
12. identify unresolved required gaps without shopping language;
13. emit structured decisions, reason codes, confidence, versions, and missing
    information;
14. let AI only rewrite or summarize the already-determined output.

`compile_routine()` should remain a renderer/persistence compiler after a
future Care decision layer exists. It should not become a god-function.

## 21. Routine minimization

Every future step must be classifiable as `required_core`, `contextual`,
`optional`, `redundant`, or `temporarily_skip`. The decision must explain why
the step is present, which owned product fills it, and what changes if it is
skipped. A routine load view should count active and occasional purposes,
frequency collisions, duplicate purpose, and effort without inventing a
medical threshold or gamified score.

The current compiler already omits empty optional slots and only reports
required gaps. V3-03.1 should add the fact/reason contract, not change that
behavior.

## 22. Hair equal-depth decision

Hair must use the same pipeline depth as Skin: stable context, observations,
confirmed product facts, ingredient compatibility, expiry/ownership, environment,
event, effort, and reason codes. Hair decisions must cover wash cadence,
pre-wash, shampoo, conditioner, mask, leave-in, heat protection, styling,
humidity/rain, heat styling, chemical/colour-treated context, scalp/product
separation, overlap, event preparation, maintenance timing, and feedback.

The wash-day slot list is a useful rendering vocabulary, not a complete Hair
intelligence architecture.

## 23. Event Ready integration

Planning owns the canonical Event. Care consumes an Event reference and emits an
`EventCarePlan` as part of a shared Event Ready plan. It should use date/time,
importance, environment, owned products, heat styling, simple preparation
timeline, and maintenance timing. It must not fork a separate Skin or Hair
event engine.

The initial seam should be read-only and additive: no change to existing
routine output until an approved V3-03.x behavior phase.

## 24. Maintenance boundary

Maintenance means Skin/Hair/grooming timing and preparation ideas. It does not
mean salon marketplace, booking, checkout, provider comparison, or price
shopping. The customer-facing concept should be “Skin & Hair Maintenance
Ideas.” Legacy `salon_suggestions` terminology, if found in later branches,
must be audited and migrated semantically rather than copied into Care.

## 25. Purchase boundary

Care may emit an explainable `unresolved_gap` (“no required sun-protection step
is recorded”) but must not choose a product, merchant, or purchase action.
Purchase Intelligence owns Buy/Wait/Skip evaluation using inventory, redundancy,
ingredient utility, and user constraints.

## 26. Home Care boundary

No home remedies, household chemistry, or recipes are part of V3-03. Home Care
can later plug into a separate evidence-aware library with its own safety gate.

## 27. Nutrition boundary

Care does not prescribe foods, nutrients, deficiency corrections, or treatment.
The existing Nutrition module remains a separate domain and may only expose a
future structured, safe signal to Care.

## 28. Supplement boundary

Care does not recommend supplement dosage, biotin, vitamin D, zinc, or
deficiency treatment. Existing supplement inventory may be checked for expiry
and label boundaries only; no Care rule should turn it into a prescription.

## 29. Product Quality boundary

Care may use confirmed ingredients, product role, expiry, ownership, usage, and
verification state. It must not evaluate formulation quality, laboratory
verification, packaging stability, claim truthfulness, or value-for-money.
Those belong to a future Product Quality domain.

## 30. AI boundary

The permanent boundary is:

```text
deterministic facts + deterministic rules + eligible provenance
                         ↓
                   Care decision
                         ↓
                    AI explanation
```

AI may summarize, rewrite, make instructions easier to read, and ask for
missing facts. It may not diagnose, invent ingredients/interactions/frequency,
invent evidence or weather, or override deterministic safety. Existing
`routines/explanation.py` is the correct shape to preserve.

## 31. Privacy/data ownership matrix

| Fact | Owner | Scope | Source | Consumer | Privacy class |
| --- | --- | --- | --- | --- | --- |
| Care ProfileAttribute facts | Profile canonical store | account/profile | explicit user declaration | Care assembler/decisions | existing `profile_attributes`, INCLUDED, exportable/deletable |
| Allergy declaration | Profile / `ProfileAttribute("allergies")` | account/profile | explicit user declaration | current ShelfContext/routine input | existing profile privacy, INCLUDED, exportable/deletable |
| Allergy projection | `UserConstraint(kind="allergy")` | account | `profile.service.sync_projections()` | retained by existing Profile infrastructure; not current ShelfContext source | existing `user_constraints`, INCLUDED, exportable/deletable |
| Legacy Hair appearance attributes | Profile canonical store | account/profile | user/photo candidate | legacy fallback only | existing `profile_attributes`, INCLUDED |
| User observations | Routines/Care seam | account | verbatim user note | temporary adaptation | INCLUDED, exportable/deletable |
| Owned products/detail rows | Inventory | account | user or confirmed extraction | shelf/Care | INCLUDED, exportable/deletable |
| Product ingredients | Routines/Ingredient Intelligence | account link to item | confirmed label/user fact | compatibility | INCLUDED; draft state retained |
| Environment snapshot | Planning/Context | account/date | stored provider/manual input | Care/Today | INCLUDED, exportable/deletable |
| Event | Planning | account | user/calendar integration | Event Ready | INCLUDED, exportable/deletable |
| Routine and adherence | Routines | account | deterministic output/user action | simplification/feedback | INCLUDED, exportable/deletable |
| Ingredient/rule ontology | Routines/reference | global | code-reviewed seed | deterministic rules | NOT_USER_OWNED |
| Evidence claims/links | Evidence | global | reviewed sources | eligible rule activation | NOT_USER_OWNED |
| Care decisions/explanations | Care | account snapshot | derived from the above | UI/AI wording | INCLUDED, exportable/deletable |

No secret, token, provider credential, raw photo bytes, or global evidence
document belongs in a Care ProfileAttribute. No new privacy registry entries
are required for V3-03.1 because profile tables are already `INCLUDED`.

## 32. Versioning/provenance

The minimum independently auditable versions are:

- `care_engine_version`;
- `domain_rule_version`;
- `routine_compiler_version`;
- `evidence_claim/link version` (V3-02 is merged);
- `plan_date`, `weather_snapshot_id`, `air_quality_snapshot_id`, and the
  normalized `ClimateContext` fields. There is no persisted complete
  `context_snapshot_id` in the current `DayContext` contract;
- product fact source, verification state, and source/model schema version.

A future recommendation snapshot should preserve input references, rule IDs and
versions, the exact current environment references, provenance eligibility
state, and decision reasons. It must not duplicate global evidence text into
every routine row. A formal immutable Care/Context snapshot is later work.

## 33. Decision reason-code contract

V3-03.1 establishes only fact-source and missing-information vocabulary; it
does not pretend that recommendation reasons are already emitted. AI may only
verbalize deterministic codes. Foundation codes are:

`confirmed_user_fact`, `legacy_confirmed_fact`, `confirmed_owned_product`,
`confirmed_ingredient`, `environment_available`, `environment_missing`,
`event_available`, `event_inferred`, `observation_available`,
`missing_skin_context`, `missing_hair_context`, `unconfirmed_product`,
`unconfirmed_ingredient`, `unknown_value`, `missing`.

Fact-source labels may additionally be represented as
`care_user_declared`, `legacy_profile_confirmed`, `inventory_confirmed`,
`ingredient_confirmed`, `user_observation`, `context_observed`,
`context_normalized`, `event_confirmed`, `event_inferred`, and `missing`.

Later behavior phases may add `required_core_step`, `compatibility_rule`,
`temporarily_skip`, `redundant_step`, and `unresolved_gap`. No
pseudo-scientific aggregate score is exposed.

## 34. Confidence/missing-data policy

V3-03.1 does not calculate an overall Care confidence. It preserves each
input's provenance: `ProfileAttribute.source`, `verification_state`, and
`confidence`; InventoryItem verification/confidence; ProductIngredient
confirmation; `DayContext.climate.confidence/reason`; and event inference
confidence. The assembler emits source categories and explicit missing codes.

Unknown remains unknown. Missing hair texture is not straight; missing
ingredient concentration is not low; missing pregnancy status is not “not
pregnant.” Low-confidence or draft product facts cannot drive a safety warning.
Missing context should produce a missing-information code, not a guessed
negative. A later deterministic decision phase may define categorical decision
confidence when there is an actual Care decision to evaluate.

These states remain distinct: missing means no usable Care declaration or exact
legacy fallback; explicit unknown means a confirmed user value of `not_sure`;
unverified means a candidate exists but has not been confirmed and is unusable.
For `care_hair_processing`, missing is an absent ProfileAttribute, explicit
none is `value=["none"]` with `source="user_declared"` and confirmed
verification, explicit unknown is `value=["not_sure"]` with the same trusted
source/state, and `value=[]` is invalid rather than another state.

## 35. P0/P1/LATER

### P0 before invite beta

Controlled Care ProfileAttribute declarations; Skin/Hair equal-depth fact
structure; explicit `not_sure`; existing profile API reuse; Context adapter;
fact assembler; unknown/missing handling; legacy Hair precedence; existing
shelf fact reuse; source/reason contract; privacy regression coverage; and
account isolation.

### P1

Richer environment/event adjustments, feedback-driven simplification,
maintenance timing, more reviewed ingredient contexts, and an auditable
recommendation snapshot immediately before adaptive Care behavior is persisted.

### LATER

Routine behavior changes, formal evidence activation, advanced formulation
reasoning, Product Quality, Home Care, shopping recommendations,
Nutrition/Supplements signals, and any clinical or diagnostic capability (which
remains prohibited).

## 36. V3-03.1 implemented scope

V3-03.1 deliberately implements only:

1. add controlled Care keys to `backend/app/domains/profile/registry.py`;
2. reuse `PATCH /api/v2/profile` and `apply_attributes()`;
3. expose additive registry `choices` metadata for clients;
4. generalize the registry validator for controlled list item choices,
   canonical scalar/list values, order-preserving deduplication, and list
   sentinel exclusivity. A reusable AttributeSpec invariant such as
   `min_items=1`/`allow_empty=false` applies only to list attributes that opt in;
5. add in-memory CareContext schemas, a DayContext adapter, and a read-only
   fact assembler using existing shelf boundaries;
6. add legacy Hair candidate precedence and explicit `not_sure` semantics;
7. add source/missing-information codes and privacy/export/deletion,
   isolation, and non-diagnostic regression tests.

It does not add a persistent Care model, migration, new Care CRUD API, alter
`compile_all()` output, create evidence claims, add shopping, change the
frontend, or introduce an AI safety decision.

## 37. V3-03.1 implementation files

These are the implementation and focused-test files for the foundation:

- `backend/app/domains/care/__init__.py` — domain boundary and public contracts.
- `backend/app/domains/care/schemas.py` — in-memory CareContext/fact contracts;
  no persistence models.
- `backend/app/domains/care/service.py` — `build_care_context()` fact assembly
  only.
- `backend/app/domains/care/context_adapter.py` — read-only `DayContext` → Care
  environment projection.
- `backend/app/domains/care/reasons.py` — fact-source and missing-information
  codes, not decision confidence.
- `backend/app/domains/profile/registry.py` — controlled Care keys and choices.
- `backend/app/api/v2/profile.py` — additive registry `choices`, `min_items`,
  and `exclusive_choices` metadata on the existing endpoint.
- `backend/tests/test_domain_care_context.py` — assembler and isolation.
- `backend/tests/test_care_profile_attributes.py` — registry, API reuse,
  `not_sure`, precedence, export, and deletion.
- `backend/tests/test_care_boundaries.py` — context mismatch, observations,
  drafts, unknowns, no diagnosis, no shopping, and no AI safety decisions.

## 38. V3-03.1 migration status

The Care assembler uses the generic read-only `profile_service.get_profile()`
lookup. A missing profile produces empty fact maps and never creates profile
persistence; the assembler performs zero database writes. A successful
database-backed assembly is regression-tested for shelf confirmation and draft
semantics, ingredient confirmation, allergy flow, environment/event
projection, and account isolation. A day with no primary event is valid and
does not emit a missing-event entry. `CareFact.value` freezes collection values
to immutable tuples, including the `care_hair_processing` list.

**Migration: NONE.** `AppearanceProfile`, `ProfileAttribute`,
`ProfileChangeEvent`, and the existing profile export/deletion path already
provide account ownership, controlled validation, provenance, and revision
history. Do not create `skin_care_profiles`, `hair_care_profiles`,
`care_preferences`, or a generic Care JSON table. If a projection is ever
justified by measured query/performance evidence, it must behave like
`StylePreference`, `FitPreference`, or `LifestyleContext`: keyed from
`profile_id`, derived from canonical `ProfileAttribute`, and without
independent provenance or version history.

## 39. V3-03.1 implemented test contract

The focused suite proves or specifies:

- Care registry keys have exact controlled vocabularies and are not
  AI-observable;
- `PATCH /profile` uses `apply_attributes()` and invalid values are rejected;
- canonical `ProfileAttribute("allergies")` remains the allergy source and its
  synchronized `UserConstraint(kind="allergy")` projection stays consistent;
- Care uses the existing ShelfContext allergy result and creates no duplicate
  allergy storage or matching engine;
- explicit `care_*` facts require `source="user_declared"` and
  `verification_state="confirmed"`; photo, inferred, integration, and
  AI-generated candidates are ignored;
- `source_ai_run_id` alone never makes a Care fact trusted;
- explicit `value="not_sure"` is stored with `verification_state="confirmed"`;
- `verification_state="not_sure"` is not treated as confirmed;
- scalar choices canonicalize case-insensitively to the registered value;
- list choices canonicalize item values, preserve order, deduplicate, reject
  invalid items, and enforce sentinel exclusivity;
- `care_hair_processing=[]` is rejected; an absent attribute means missing,
  `['none']` means explicit none, and `['not_sure']` means explicit uncertainty;
- `care_hair_processing` accepts `['coloured', 'relaxed']` but rejects
  `['none', 'coloured']` and `['not_sure', 'bleached']`;
- existing `style_experimentation` choice behavior remains compatible;
- profile version and `ProfileChangeEvent` update through existing machinery;
- no new Care table, migration, CRUD API, or privacy classification exists;
- Care attributes export/delete with ProfileAttribute/account deletion;
- global Evidence remains `NOT_USER_OWNED`;
- account A cannot assemble account B facts;
- mismatched `DayContext.account_id` is rejected and current environment
  references are reused without a provider;
- missing weather stays missing and snapshot IDs are preserved;
- exact legacy Hair maps work only for the three documented families;
- legacy synonyms/fuzzy values, processing fallbacks, and unverified candidates
  remain missing/unknown;
- explicit `care_hair_*` values, including explicit `not_sure`, outrank legacy
  candidates;
- observations remain verbatim and do not mutate ProfileAttribute;
- adherence does not mutate preferences;
- drafts/unconfirmed ingredients cannot drive safety facts;
- owned-first remains system policy; no shopping, attractiveness score,
  diagnosis, or AI safety decision is emitted;
- current routine output remains byte/structure equivalent.

## 40. Explicitly deferred work

Deferred: Care recommendation behavior, migrations, new APIs, frontend work,
evidence claim approval, background-link activation changes, evidence releases,
routine-load scoring, richer Hair compatibility, event care plans,
maintenance timing, Home Care, Nutrition/Supplements integration, Product
Quality, purchase ranking, RAG, vector search, LLM safety decisions, and new
dependencies.

## 41. Approved CTO/CPO decisions

1. Allergy remains canonical `ProfileAttribute("allergies")` with its source,
   provenance, and profile revision history. Existing
   `UserConstraint(kind="allergy")` remains a synchronized projection
   retained by existing Profile infrastructure, not the current ShelfContext
   allergy source. V3-03.1 creates no additional allergy storage;
   professional restrictions, pregnancy, and prescribed topical context remain
   deferred.
2. `not_sure` is an explicit confirmed user value.
3. Planning owns Events; Care consumes them through `DayContext`; no
   `EventCarePlan` persistence is created in V3-03.1.
4. Evidence provenance presence and behavior eligibility remain separate;
   implementation waits until Care behavior consumes Evidence.
5. Recommendation snapshots are P1 and required immediately before meaningful
   adaptive Care behavior is persisted.
6. Customer-facing naming is Skin Care, Hair Care, Care, and Shelf; internal
   `beauty` compatibility remains during V3-03.

## 42. Implemented Care ProfileAttribute contract

Care declarations are not new ORM models. V3-03.1 adds controlled keys to
`backend/app/domains/profile/registry.py`; values are stored in the existing
`profile_attributes` table through `apply_attributes()`:

| Area | Keys |
| --- | --- |
| Skin | `care_skin_usual_feel`, `care_skin_sensitivity` |
| Hair | `care_hair_pattern`, `care_hair_strand_characteristic`, `care_hair_density`, `care_hair_wash_frequency`, `care_hair_processing`, `care_heat_styling_frequency`, `care_scalp_usual_feel`, `care_humidity_frizz_sensitivity`, `care_hair_styling_preference` |
| Shared | `care_routine_effort`, `care_fragrance_preference`, `care_event_preparation_effort` |

All listed keys are controlled scalar choices except
`care_hair_processing`, whose registry `kind` is `list` with controlled item
choices and the sentinel invariants described below.

Each key inherits the canonical `ProfileAttribute` fields for value, source,
confidence, verification state, review metadata, and `source_ai_run_id`;
`AppearanceProfile.version` and `ProfileChangeEvent` provide revision history.
The registry supplies label, section, kind, and controlled `choices` metadata,
including controlled item choices for list attributes. Scalar and list choices
are canonicalized to the exact registered value and list values are
deduplicated after canonicalization while preserving user order. Sentinel list
values `['none']` and `['not_sure']` are mutually exclusive with all other
values. For `care_hair_processing`, an empty list is rejected by the registry;
missing is represented by an absent ProfileAttribute, not `[]`.
Existing list attributes remain unchanged unless their own AttributeSpec opts
into the non-empty invariant.
Care keys are not AI-observable, are non-diagnostic, and do not represent
attractiveness or medical status. `owned_product_priority` is not a profile
fact: owned-first is a hard product policy.

An explicit user selection of `not_sure` is stored as
`value="not_sure", verification_state="confirmed", source="user_declared"`.
The literal verification state `not_sure` is never treated as confirmed.
For V3-03.1, every explicit `care_*` fact is usable only when
`source="user_declared"` and `verification_state="confirmed"`. A present
`source_ai_run_id` does not make a fact trusted; photo, inferred, integration,
or AI-generated candidates remain unusable until explicitly confirmed through
the user-declaration path.

## 43. Implemented CareContext in-memory schema

The Care domain owns an implemented in-memory assembly contract, not a
persistence table.
`CareContext` may contain:

- Skin and Hair fact maps, shared preferences, and per-fact source/reason data;
- verbatim `UserReportedObservation` values and separate adherence summaries;
- confirmed owned products, draft counts, confirmed ingredients, and low-use
  facts gathered through the existing shelf boundary;
- a read-only environment projection from `DayContext`, including weather and
  air-quality snapshot IDs plus normalized climate fields;
- the Planning-owned event seam (`primary_event` and relevant event values);
- explicit missing-information codes.

The environment projection may expose only present values among
`weather_snapshot_id`, `air_quality_snapshot_id`, `condition`, `temp_min_c`,
`temp_max_c`, `humidity`, `precipitation_chance`, `uv_index`, `aqi`,
`aqi_index_system`, `aqi_category`, `climate_region`, `calendar_prior`,
`season`, `temperature_band`, `moisture_regime`, `daily_regime`,
`climate_confidence`, and `climate_reason`; it never synthesizes missing data.

It does not persist a Care row, calculate overall Care confidence, choose a
product, create a routine, call a provider, call AI, or write observations back
to ProfileAttribute.

## 44. Legacy Hair precedence

For each Care Hair key the assembler applies this strict order:

1. confirmed explicit `care_hair_*` ProfileAttribute;
2. if absent, an exact-mappable confirmed legacy appearance value in memory;
3. otherwise missing/unknown.

The explicit Care value always wins, including confirmed `not_sure`; it must
not fall back to a legacy value. The only exact normalized legacy maps are:

| Legacy key | Care key | Accepted values |
| --- | --- | --- |
| `hair_type` | `care_hair_pattern` | `straight`, `wavy`, `curly`, `coily` |
| `hair_texture` | `care_hair_strand_characteristic` | `fine`, `medium`, `coarse` |
| `hair_density` | `care_hair_density` | `low`, `medium`, `high` |

Case and outer whitespace may be normalized. No synonyms, fuzzy matching, or
lossy mappings such as `thin` → `fine`, `4c` → `coily`, or `wavy-curly` →
`wavy` are allowed in V3-03.1. Legacy fallback requires
`verification_state="confirmed"`; unverified, `not_sure` verification state,
rejected, and superseded candidates remain unusable. There is no legacy
fallback for `care_hair_processing`, which is a controlled list attribute.

The assembler may normalize a fallback only in memory. It never writes
`care_hair_*`, changes verification state, promotes an observation, creates a
`UserConstraint`, or mutates the profile.

## 45. Implemented `build_care_context(...)` service contract

The service is an account-scoped assembler and accepts the existing context seam only:

```text
build_care_context(session, account_id, *, day_context: DayContext) -> CareContext
```

It must reject a `DayContext` whose `account_id` does not equal `account_id`.
It consumes `plan_date`, `weather`, `air_quality`, `climate`, `events`,
`primary_event`, `weather_snapshot_id`, and `air_quality_snapshot_id` from that
object. It does not independently accept `event_id`, `as_of`, weather/season/
climate parameters, query another provider, or invent a `context_snapshot_id`.

## 46. V3-03.1 privacy and provenance

Care ProfileAttributes use the existing profile privacy ownership, export, and
deletion behavior; V3-03.1 adds zero privacy classifications. Inventory,
ingredient, observation, event, and environment facts retain their current
account ownership and per-input provenance. Global ontology/rule and Evidence
claim/link rows remain `NOT_USER_OWNED`.

AI may receive only the minimum deterministic facts needed to explain an
already-made decision and cannot write ProfileAttribute values. The canonical
allergy owner is ProfileAttribute `allergies`, and that value is the current
ShelfContext/routine input. The existing `UserConstraint(kind="allergy")` is
its synchronized projection and remains valid Profile infrastructure, not the
current ShelfContext source. Care creates no duplicate allergy field or
matching engine. Professional, pregnancy, and prescribed-topical context remain
deferred until separately approved.

## 47. Later behavior-phase evidence activation semantics

A later V3-03 behavior phase, immediately before Evidence affects Care
behavior, must introduce and enforce the distinction in the evidence-consuming
seam:

```text
provenance_present = approved source/claim/link is attached
behavior_evidence_eligible = the link relationship and claim status permit
                              this rule to affect behavior
```

V3-03.1 does not modify Evidence activation. `background` may make
`provenance_present=true` for auditability but must not
make `behavior_evidence_eligible=true` for a safety or behavior rule. Only an
approved claim, an eligible relationship, a live rule version, and a confirmed
fact set may activate behavior. This is a design requirement only; V3-03.0
does not modify V3-02 code.

## 48. Proposed data flow

```text
User declarations ─┐
Observations ───────┤
Owned products ────┤
Ingredients ───────┤
Context Engine ────┤──> Care Fact Set ──> Deterministic Care Rules
Event ─────────────┤                              │
Evidence state ────┘                              ▼
                                      Care Decision / Event Care Plan
                                                   ▼
                                      Routine compiler / explanation
```

V3-03.3 now activates this flow for routine generation and Today safety checks.
The compiler remains a routines-owned adapter consumer; it does not import Care
decision types directly.

## 49. Current duplicate-truth/drift risks

- Profile `LifestyleContext.climate`, routine `ShelfContext.climate`, request
  `CLIMATES`, perfume `weather`/`season`, and V3-01 `ClimateContext` can all
  describe “weather” with different vocabularies.
- Routine generation does not consume `DayContext`; environment can therefore
  disagree between Today and Care.
- `beauty`/`hair` product role lives in inventory detail, slot mapping, and
  display labels without a separate Care role contract.
- Existing legacy Hair appearance attributes and future Care Hair
  ProfileAttributes could diverge; explicit Care keys therefore outrank only
  exact-mappable confirmed legacy candidates, with no silent database sync.
- `item_expiry_events` (user-declared) and `product_expiry_events` (computed)
  are distinct and must not be conflated.
- Ontology code and seeded reference tables must remain one source of truth;
  V3-02 evidence tables are global and separate from account data.

## 50. Current Skin fact coverage

| Fact | Status |
| --- | --- |
| owned Skin products | implemented (`InventoryItem` + `BeautyProductDetail`) |
| product role/slot | implemented, deterministic parser mapping |
| expiry/opened date | implemented |
| usage/remaining/low-use | implemented |
| ingredient text and parsed families | implemented, confidence-aware |
| canonical user-declared allergies | implemented via ProfileAttribute `allergies`; `UserConstraint(kind="allergy")` is synchronized projection |
| usual skin feel | missing |
| oiliness/dryness tendency | missing |
| sensitivity tendency | missing |
| fragrance preference | missing for Care |
| routine effort | missing for Care |
| structured discomfort observation | free-text only, verbatim |
| UV/humidity/AQI-aware Skin decision | missing; Context values exist |
| event Skin prep | missing |
| approved Evidence linkage | infrastructure implemented; approved claim count is zero |

## 51. Current Hair fact coverage

| Fact | Status |
| --- | --- |
| owned Hair products | implemented (`InventoryItem` + `HairProductDetail`) |
| wash-day slots | implemented |
| shampoo/conditioner required core | implemented |
| mask/pre-wash/leave-in/heat/styling | implemented as optional slots |
| protein/silicone compatibility | implemented as deterministic product rules |
| expiry/usage/low-use | implemented |
| hair pattern/strand/density | partial: generic Profile/photo observation only |
| wash frequency | missing |
| chemical/colour treatment | missing |
| heat-styling habit | missing |
| scalp comfort | missing; only free text observations |
| humidity/frizz sensitivity | missing |
| event styling needs | missing from Care |
| maintenance timing | missing |
| approved Evidence linkage | infrastructure implemented; approved claim count is zero |

## 52. Current Care preference coverage

There is no dedicated Care preference model. Generic style preferences and
`LifestyleContext.routine` exist, and the canonical allergy ProfileAttribute is
projected to `UserConstraint`, but the three controlled Care preference keys are
not yet structured ProfileAttributes. They must not be inferred from adherence or purchase
behavior. Owned-first is a system policy, not a preference key.

## 53. Current medical/safety-context coverage

The baseline stores the canonical user-declared allergy value in ProfileAttribute
`allergies`; `profile.service.sync_projections()` maintains the existing
`UserConstraint(kind="allergy")` projection. The ShelfContext/routine path
reads confirmed profile attributes through `shelf_attributes()` and its
`SHELF_ATTRIBUTES` tuple (including `"allergies"`), creating
`ShelfContext.allergies` directly from that ProfileAttribute value. It uses the
result as a hard product exclusion when the parsed ingredient is confirmed.
Care reuses that shelf boundary rather than querying UserConstraint
independently or implementing a second matching engine.
`safety.py` and `safety_classifier.py` block diagnostic/treatment wording and
route health-like observations to a professional boundary. No structured
pregnancy, prescribed topical, professional restriction, or diagnosis field is
owned by Care. That absence is safer than a generic medical JSON field.

## 54. Current environmental dimensions consumed by Care

Routine generation now gathers the V3-01 `DayContext` to assemble the
account-scoped Care context, but Care eligibility still consumes none of its
`temperature`, `humidity`, `precipitation`, `UV`, `AQI`, `daily_regime`, or
confidence dimensions. The request/Profile coarse `climate` value remains a
legacy compiler-notes input only.

## 55. Current user-observation behavior

`POST /api/v2/routines/observations` stores the user's exact note, area, date,
and optional product link. A professional boundary may be attached, but the
note is never rewritten into a diagnosis or profile fact. The routine compiler
does not yet read these observations when selecting or simplifying products.

## 56. Current routine minimization behavior

Owned-first ranking, required-only gaps, omission of empty optional slots,
duplicate-slot info, low-use findings, static alternatives, and
`routines_today()` time/weekend filtering are implemented. Active treatment
load, frequency collision, duplicate purpose, effort tolerance, observation
pause behavior, and explanation reason codes are missing.

## 57. Current Hair wash-day behavior

Wash day is emitted only when Hair products provide at least one owned step.
It orders pre-wash oil → shampoo → scalp care → conditioner → leave-in → heat
protectant → styling; shampoo and conditioner are required. A weekly hair mask
is a separate optional routine. Product ranking is slot/expiry/low-use/
remaining/name based, with no wash cadence, Hair profile, humidity, rain,
chemical-treatment, heat-habit, event, or maintenance input.

## 58. Current Event Care behavior

The compiler has a generic `event` template, but Event Ready is not wired to
Care. Event records are used by Planning/Today for outfit context only. There
is no event importance, countdown, skin/hair preparation timeline, maintenance
task, or event-specific safety/ownership decision.

## 59. Current evidence integration behavior

The audited baseline has the V3-02 global evidence package and draft pilot
links, but approved production claim count is zero. Findings still use
code-owned/seeded deterministic ingredient and compatibility rules; confirmed
ingredients are the only facts that can trigger compatibility warnings. The
future activation seam must distinguish provenance presence from
`behavior_evidence_eligible`; draft or `background` links do not activate Care
behavior.

## 60. Current backend/package validation

V3-03.1 changes the registry, profile metadata response, Care foundation, and
focused tests. The dependency files remain unchanged:

- `backend/requirements.txt` unchanged;
- `frontend/package.json` unchanged;
- `frontend/yarn.lock` unchanged.

The expected implementation diff contains no migration, ORM model, privacy
registry, frontend, or dependency file; V3-03.3 intentionally modifies the
routine/compiler/planning integration seams.

## 62. V3-03.2 Care Decision Safety Foundation

V3-03.2 adds the pure deterministic `CareDecisionSet` boundary at version
`v3-03.2`. `ProductCareDecision` evaluates only recorded expiry and the
canonical allergy/ingredient confirmation facts. Expired products are hard
blocked; confirmed allergy matches are hard blocked; unconfirmed potential
allergens remain eligible with an ingredient-confirmation advisory; and
expiring-soon products remain eligible with an informational advisory.

`CoreSlotDecision` derives required Skin and Hair slots from the canonical
`SKIN_SLOTS` and `HAIR_SLOTS` ontology definitions. A core slot is filled only
when an eligible owned product fills it. Optional empty slots do not create
gaps. The engine returns all eligible candidates and performs no ranking,
winner selection, purchase recommendation, or product minimization.

The engine is pure and does not query Evidence, call a provider, access a
session, or alter routine/compiler/API output. Care profile facts,
environment, events, observations, and adherence remain behaviorally inert in
this phase. No migrations, tables, privacy classifications, or dependencies
were added.

The reusable routine allergy matcher now exposes confirmed and unconfirmed
ingredient hits while preserving `allergy_findings()` and
`excluded_by_allergy()` behavior.

`RuleEvidenceAssessment` is an internal, immutable, fail-closed seam with
`provenance_present`, `substantive_support_present`, and
`behavior_evidence_eligible`. Reviewed valid links establish provenance;
only reviewed `supports` links to supported claims establish substantive
support; and behavior eligibility remains false until structured scope and
applicability evaluation exists. Background, qualifies, and limits links never
count as substantive support. Current production approved claim count remains
zero, and Evidence does not affect current Care decisions.

## 63. Final recommendation

`V3-03.2 READY FOR INDEPENDENT REVIEW`

The authoritative `main` baseline was verified at `94dee83d…`. The
Care Context foundation is implemented without recommendation behavior,
Evidence activation, or merge.

## 64. V3-03.3 Care Safety Activation

V3-03.3 activates the V3-03.2 decision set at the existing user-facing Care
surfaces. Routine generation gathers the authenticated account's `DayContext`,
builds `CareContext`, evaluates one deterministic `CareDecisionSet`, and passes
only its eligible product IDs to the existing compiler. Expired and confirmed-
allergy products are excluded; low-confidence possible allergens remain
eligible and produce confirmation information rather than an avoid finding.
Confirmed-allergy skips are tracked separately from expiry blocks, required
gaps distinguish blocked owned products from absent products, and optional
blocked products are omitted without becoming shopping gaps. Existing
`rank_for_slot()` ordering remains unchanged within the eligible set.

Stored routines and recommendation runs now record `care-v3-03.3`; run inputs
include Care versions, blocked/advisory counts, and current weather/air-quality
snapshot IDs. Responses add an additive `care_safety` summary. `GET
/routines/today` builds the current Care decision and omits saved routines that
would serve a newly blocked product, returning `refresh_required` without
writing or regenerating anything.

Today fresh compilation uses the same Care decision truth for Skin/Hair cards
and appearance actions. Expired products use constructive “set aside” copy,
confirmed allergy blocks use profile-constraint copy, and eligible
expiring-soon products receive a softer date reminder. The Care decision
fingerprint is a generic material extension to the existing Today cache key,
so ingredient confirmation changes invalidate the same-day plan. Daily plan
inputs record the Care versions, fingerprint, blocked count, and confirmation
advisory count.

Care eligibility remains independent of humidity, UV, AQI, temperature, season,
daily regime, profile-personalization facts, events, compatibility ontology
warnings, and Evidence. No new table, column, migration, privacy
classification, dependency, or frontend field was added.

`V3-03.3 IMPLEMENTED — READY FOR INDEPENDENT REVIEW`

## 65. V3-03.3.1 Safety Activation Invariant + Integration Test Closure

The Today Care action path now requires `CareContext` and `CareDecisionSet`.
There is no production fallback that can independently decide Skin/Hair
expiry or allergy eligibility, and `_module_material()` always sources Skin and
Hair routine rows from Care eligibility. Perfume remains on its separate
inventory path. The legacy expired-product `use_or_replace` branch and its
contradictory “use it now” copy were removed entirely.

Database-backed regressions now exercise the real account-scoped path through
`DayContext`, `CareContext`, `CareDecisionSet`, `RoutineEligibility`, compiler,
persistence, and serialization. Coverage includes expired-only and
expired-plus-valid routine generation, confirmed and unconfirmed allergen
behavior, routine audit persistence, the stale stored-routine expiry and
allergy gates, read-only safety checks, Today expiry/allergy/filtering actions,
Care decision cache invalidation after ingredient confirmation, cache-hit
control, DailyPlan Care inputs, and account isolation.

V3-03.3.1 keeps the same hard blocks, advisories, ranking boundary, engine
identity, and zero environmental/profile/Event/Evidence behavior. No raw
Skin/Hair Today fallback remains.

## 66. V3-03.3.2 Stored Ingredient Precedence + Lifecycle Closure

Runtime ingredient authority is explicit:

`confirmed persisted user fact` > `current product fact` > `unconfirmed persisted extraction candidate`.

The shelf has a shared `build_fresh()` primitive for current Inventory details.
The runtime `build()` overlay can add stored-only candidates and promote
confirmed persisted rows, but an unconfirmed stored row cannot overwrite a
current fact's confidence, source, matched text, or position. Shelf analysis
reconciles persisted rows against the fresh parse, so stale unconfirmed rows
are deleted while confirmed rows survive re-analysis. The post-analysis
summary is built from a refreshed account-scoped context after the write.

`V3-03.3.2 READY FOR FINAL INDEPENDENT REVIEW`

## 67. V3-03.4 Minimum-Effective Care Routine Planning Foundation

V3-03.4 adds a pure `CareRoutinePlan` contract over the existing
`CareContext` and authoritative `CareDecisionSet`. The planner uses canonical
`SKIN_SLOTS`, `HAIR_SLOTS`, and `SLOT_BY_KEY` definitions and keeps one global
active product per slot while retaining other eligible owned products as
alternatives.

`care_routine_effort` is the only newly activated personalization fact:

- `minimal` activates required slots only;
- `balanced` activates required slots plus optional slots represented by the
  user's own usage history (`last_used_at` or `usage_count`);
- `detailed` activates every optional slot with an eligible owned candidate;
- missing effort defaults transparently to balanced with
  `system_default_missing`, while explicit `not_sure` defaults to balanced with
  `system_default_not_sure`.

Selection is continuity-first: most recent use, then usage count, then a stable
display-name/UUID fallback. Expiry advisories, low-use status, price, brand,
remaining percentage, environment, physiology/profile facts, events, Evidence,
and AI do not influence this plan. Blocked products are excluded; unconfirmed
ingredient advisories remain eligible. Required slots without an eligible
candidate remain active gaps, while optional slots without candidates are
inactive and never gaps.

The immutable plan has a stable SHA-256 fingerprint over plan fields only.
Account/date mismatches fail before planning. This is a foundation contract;
V3-03.5 will be the deliberate runtime activation phase. Existing routine
compiler behavior, API responses, stored rows, Today, and `routines_today`
remain unchanged.

`V3-03.4 READY FOR INDEPENDENT REVIEW`

## 68. V3-03.5 Minimum-Effective Care Routine Activation

V3-03.5 makes `CareRoutinePlan` authoritative at runtime. The service layer
adapts the immutable Care plan into the routines-owned `RoutineSelectionPlan`
projection; the pure routine compiler renders those directives without
importing Care dataclasses. Active slots use exactly the plan-selected owned
item, inactive optional slots are omitted, required gaps retain the existing
generic gap copy, and contradictions fail loudly. The legacy
`rank_for_slot()` path remains only for callers that intentionally compile
without a selection projection.

Routine generation now computes one plan per account/date, stores routines and
recommendation runs as `care-v3-03.5`, and records the plan version,
fingerprint, effort/source, active-slot counts, and gap counts in existing JSON
audit inputs. The same plan drives all routine kinds, so a canonical slot uses
the same selected product in morning, evening, weekly, wash-day, or event
compositions where present.

Today Skin/Hair routine actions consume active selected Care items rather than
the first eligible shelf row. Its material cache key includes both the Care
safety decision fingerprint and `routine_plan_fingerprint`, so effort changes,
continuity changes, and optional-slot activation changes recompute the same-day
plan. Safety actions and advisory copy remain separate from selection.

`routines_today()` compares saved material steps with the current deterministic
plan and requires refresh for plan drift or pre-03.5 Care routines. It never
regenerates, persists, increments versions, or creates recommendation runs on
GET. Climate/environment, Evidence, AI, and new Care science remain outside
selection; eligibility, selection, and rendering remain separate boundaries.

No schema, migration, dependency, frontend, or public response changes were
introduced.

## 69. V3-03.5.1 Canonical Today Cache-Key Closure

Today compilation, Today outfit pinning, and weekly outfit pinning now share
the Planning-owned `DayCareMaterial` builder and `material_cache_key()`
authority. The builder performs CareContext assembly, CareDecisionSet
evaluation, CareRoutinePlan selection, and both material fingerprints exactly
once per planning path. Pinning therefore preserves the user's arrangement
against the current full material state without dropping the routine-plan
fingerprint or freezing future invalidation.

No locked-day semantics, Care selection semantics, version constants, schema,
dependency, frontend, Evidence, AI, or public response behavior changed.

## 70. V3-03.6 Locked-Day Care Freshness & Safety Override

A locked Today/Planner day suppresses automatic full-day replacement but does
not suppress current Care material. `CareDecisionSet` and `CareRoutinePlan`
fingerprints are checked independently from the locked full-day cache key,
before an ordinary cache hit can short-circuit the request.

When Care material changes, only Skin/Hair Today actions and the Care audit
inputs are refreshed. Existing completion marks are preserved for equivalent
actions, the DailyPlan version advances once, and one Care-specific
recalculation event records the refresh. The locked full-day cache key is
intentionally not repinned, preventing unrelated stale weather, event, or
outfit context from being falsely marked current. Those weather/event lock
semantics remain unchanged and are outside this phase.

Weekly generation now performs the same non-force Care freshness check for a
locked linked DailyPlan without replacing its weekly row, look, outfit
schedule, or lock state. Explicit `regenerate_locked=true` and direct planner
regeneration behavior remain unchanged. No schema, migration, dependency,
frontend, Evidence, AI, or public response changes are introduced.

## 71. V3-03.7 Durable Routine Identity & Adherence Preservation

Routine rendering rows are replaceable; adherence history is not. A current
`RoutineStep` UUID identifies one rendering row, while the durable logical
identity of a completed step is `routine_id + canonical slot + done_on`.
Within a routine, `RoutineStep(routine_id, slot)` is unique and regeneration
reconciles rows by slot, preserving the UUID when the slot survives while
updating its current material in place.

`RoutineAdherence.slot` is backfilled from the step that recorded the
completion. Its `step_id` is provenance only: it is nullable and uses
`ON DELETE SET NULL`, so removing a current optional step keeps the historical
completion. Recreating that slot can reattach the current step without making
a duplicate same-day row. Adherence is not inventory usage and completing a
step does not update inventory usage counters or timestamps.

This persistence foundation is the prerequisite for later wash cadence,
feedback-driven simplification, and routine-history interpretation. The
migration refuses ambiguous duplicate slots and refuses a downgrade that
would silently discard detached history.

## 72. V3-03.8 User-Grounded Hair Wash Cadence

Hair wash cadence determines **when** an already-selected Hair routine is
relevant. `CareRoutinePlan` continues to determine **what** product is shown.
The only scheduling authority is the trusted, confirmed canonical
`care_hair_wash_frequency` Care fact. Durable completed adherence from a
`wash_day` routine, limited to the `shampoo` or `conditioner` core slots,
provides the historical anchor; retired routines remain valid history and
future dates are ignored.

`daily`, `several_times_week`, and `weekly` map deterministically to one-,
two-, and seven-day intervals. `less_than_weekly`, `variable`, `not_sure`,
and a missing declaration remain unscheduled rather than being guessed. A
weekly or several-times-week declaration with no history needs an anchor and
does not fall back to a calendar weekend. Weekend is no longer Hair wash
authority; weekly extras retain their existing weekend behavior.

Cadence has its independently auditable `v3-03.8` version and fingerprint.
It is included in the canonical Today cache key, persisted as additive Care
audit inputs, and participates in the existing locked-day partial Care
freshness check. No Hair pattern, scalp, environment, Evidence, AI, product
selection, inventory-usage, schema, migration, dependency, or frontend signal
changes wash timing.
