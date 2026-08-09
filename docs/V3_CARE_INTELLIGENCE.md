# V3-03 Skin & Hair Care Intelligence

**Phase:** V3-03.0 architecture and audit only
**Baseline requested and audited:** `94dee83d0b36d2a3cef9b02eb8badde00110d464`
**Branch:** `v3/v3-03-care-intelligence-foundation`
**Status:** no Care implementation, migration, dependency, frontend, or recommendation change is included in this phase.

## 1. Executive summary

GlamGenius already has a useful deterministic shelf and routine foundation. It
can classify owned Skin (`beauty`) and Hair (`hair`) products, parse a limited
ingredient ontology, apply reviewed compatibility rules, exclude products that
match a user-declared allergy, calculate expiry/low-use findings, and compile
owned products into five routine shapes. It also has a separate Context Engine
for weather, air quality, regional climate, and events.

The current system is not yet a Care decision engine. Routine compilation reads
owned products, a free-text allergy list, and a coarse climate string. It does
not consume the normalized `DayContext`, user-declared Skin/Hair care facts,
structured observations, event importance/timing, or evidence provenance. Hair
has first-class slots but not first-class personalization. The local baseline
also predates the V3-02 Evidence Provenance implementation: there is no
`backend/app/domains/evidence` package and no approved Evidence claim to
consume on this baseline.

The permanent design is therefore a fact assembler plus a deterministic Care
decision layer in front of the existing compiler. V3-03.1 should add only
account-owned declarative Skin/Hair context, shared Care preferences, privacy
coverage, a Context adapter, and a reason/confidence contract. It should not
change recommendation behavior yet.

## 2. V3-03 mission

Care must answer “what should I use from what I own today?” with the minimum
effective routine, clear safety boundaries, environmental adjustments,
event-readiness support, low decision fatigue, and high use of owned products.
It must never diagnose, prescribe treatment, invent ingredients or product
facts, turn every empty slot into a purchase, or expose an attractiveness
score. The permanent decision order is:

1. hard safety and expiry exclusions;
2. user-declared constraints;
3. current environmental context;
4. confirmed owned products and confirmed ingredients;
5. current routine compatibility and load;
6. effort/preferences;
7. event context;
8. approved or legacy-curated rule adjustments;
9. an unresolved gap only when a genuine required step remains.

## 3. Current repository truth

The checkout's local `origin/main` was stale at the V3-01 merge commit
`53296f8a2cac39ca1cfafb7805a4197c056d5a8d`. The requested V3-02 merge object
was fetched and verified before this branch was rebased, so the authoritative
audit baseline is `94dee83d0b36d2a3cef9b02eb8badde00110d464`.

The internal inventory key `beauty` and labels such as `Beauty Shelf` are
legacy compatibility names. The future customer taxonomy is **Skin Care**,
**Hair Care**, **Care**, **Routine**, and **Shelf**. Renaming the internal key is
out of scope for V3-03.0.

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
11. Existing generic `UserConstraint` is account-owned but can mix allergy and
    non-medical constraints without a Care-specific privacy contract.
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

The proposed Skin taxonomy is declarative and controlled, not diagnostic.

| Fact | Controlled values | Source/priority | Consumer |
| --- | --- | --- | --- |
| usual skin feel | `comfortable`, `often_dry_or_tight`, `often_oily`, `mixed`, `not_sure` | explicit user declaration; never photo-inferred | routine texture and step minimization |
| sensitivity tendency | `rarely_reactive`, `sometimes_reactive`, `often_reactive`, `not_sure` | explicit user declaration or explicit update | soft comfort preference; never a diagnosis |
| fragrance preference | shared Care preference | explicit user declaration | product ranking/filtering |
| preferred routine effort | shared Care preference | explicit user declaration | minimization only; cannot override safety |
| known allergy/avoidance | existing account `UserConstraint(kind=allergy)`; matched only when ingredient fact is confirmed | explicit user declaration | hard exclusion |
| professional restriction/prescribed topical | not stored in V3-03.1; dedicated sensitive design required | future explicit flow only | future deterministic safety gate |
| event skin-prep preference | shared event preference, not a skin condition | explicit user declaration/event | Event Care plan |

`skin_tone`, `undertone`, and `visible_skin_characteristics` remain Profile
appearance facts. They are not silently reinterpreted as Care conditions.

## 14. Hair fact taxonomy

| Fact | Controlled values | Source/priority | Consumer |
| --- | --- | --- | --- |
| hair pattern | `straight`, `wavy`, `curly`, `coily`, `not_sure` | explicit declaration | wash/styling fit |
| strand characteristic | `fine`, `medium`, `coarse`, `not_sure` | explicit declaration | product weight and minimization |
| density declaration | `low`, `medium`, `high`, `not_sure` | explicit declaration; no photo diagnosis | styling effort only |
| wash frequency | `daily`, `several_times_week`, `weekly`, `less_than_weekly`, `variable`, `not_sure` | explicit declaration/usage later | wash-day scheduling |
| chemical processing | `none`, `coloured`, `bleached`, `relaxed`, `permed_or_texturised`, `multiple`, `not_sure` | explicit declaration | gentle sequencing and event prep |
| heat-styling frequency | `never`, `occasional`, `frequent`, `daily`, `not_sure` | explicit declaration/usage later | heat-protection reminder |
| usual scalp feel | `comfortable`, `often_dry_or_tight`, `often_oily`, `sometimes_uncomfortable`, `not_sure` | explicit declaration or observation, never diagnosis | comfort-aware simplification |
| humidity/frizz sensitivity | `low`, `moderate`, `high`, `not_sure` | explicit declaration/observation | environment styling adjustment |
| styling preference | `air_dry`, `heat_style`, `protective_style`, `mixed`, `not_sure` | explicit declaration | event and effort ranking |

`hair_type`, `hair_texture`, and `hair_density` from the existing Profile may
be imported only as unconfirmed legacy candidates; they do not silently fill
these controlled fields.

## 15. Shared Care preferences

One account-owned `CarePreference` should hold preferences common to Skin and
Hair rather than duplicating them in two profile tables:

- `routine_effort`: `minimal`, `balanced`, `detailed`, `not_sure`;
- `fragrance_preference`: `fragrance_free_preferred`, `no_preference`,
  `likes_fragrance`, `not_sure`;
- `event_preparation_effort`: `minimal`, `balanced`, `detailed`, `not_sure`;
- `owned_product_priority`: `use_owned_first`, `balanced`, `not_sure`.

These are soft factors. They can remove optional steps or choose among safe
owned alternatives; they can never override an allergy, expiry exclusion,
confirmed incompatibility, or a missing safety fact.

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

Hard constraints are applied first: user-declared allergy/avoidance matched to a
confirmed ingredient; expired product; confirmed deterministic incompatibility;
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
| Skin profile facts | Care | account | explicit user declaration | Skin decisions | INCLUDED, exportable/deletable |
| Hair profile facts | Care | account | explicit user declaration | Hair decisions | INCLUDED, exportable/deletable |
| Shared Care preferences | Care | account | explicit user declaration | both domains | INCLUDED, exportable/deletable |
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
document belongs in an account-owned Care row.

## 32. Versioning/provenance

The minimum independently auditable versions are:

- `care_engine_version`;
- `domain_rule_version`;
- `routine_compiler_version`;
- `evidence_claim/link version` (when V3-02 is merged);
- `context_snapshot_id` and normalized context version;
- product fact source, verification state, and source/model schema version.

A future recommendation snapshot should preserve input references, rule IDs and
versions, environment snapshot reference, provenance eligibility state, and
decision reasons. It must not duplicate global evidence text into every routine
row.

## 33. Decision reason-code contract

Reason codes are stable machine values; AI may only verbalize them. Initial
codes:

`owned_product`, `required_core_step`, `contextual_step`, `optional_step`,
`user_preference`, `environment_humidity`, `environment_precipitation`,
`temperature_context`, `uv_context`, `aqi_context`, `event_context`,
`compatibility_rule`, `user_declared_allergy`, `expiry`, `unconfirmed_fact`,
`user_observation`, `routine_load`, `redundant_step`, `temporarily_skip`,
`missing_information`, `unresolved_gap`, `unknown_not_negative`.

Each decision should carry the code, source IDs/versions, affected product or
slot, and a short deterministic explanation. No pseudo-scientific aggregate
score is exposed.

## 34. Confidence/missing-data policy

Use categorical confidence only when the meaning is deterministic:

- `high`: confirmed user/product fact plus a reviewed deterministic rule and a
  current Context snapshot where required;
- `moderate`: confirmed facts with a legacy-curated rule or partial current
  context;
- `limited`: missing/unknown profile, unconfirmed product fact, unavailable
  environment, or inferred event.

Unknown remains unknown. Missing hair texture is not straight; missing
ingredient concentration is not low; missing pregnancy status is not “not
pregnant.” Low-confidence or draft product facts cannot drive a safety warning.
Missing context should produce `missing_information`, not a guessed negative.

## 35. P0/P1/LATER

### P0 before invite beta

Account-owned Skin and Hair declarative context; shared Care preferences;
controlled vocabularies with `not_sure`; privacy export/deletion; a Care fact
assembler; Context Engine adapter; reason codes; categorical confidence;
unknown/missing handling; confirmed ingredient and expiry gates; observation
separation; Hair equal-depth coverage; and an Event Ready seam without a
behavior change.

### P1

Richer environment/event adjustments, feedback-driven simplification,
maintenance timing, more reviewed ingredient contexts, and an auditable
recommendation snapshot.

### LATER

Formal evidence releases/retrieval, advanced formulation reasoning, Product
Quality, Home Care, shopping recommendations, Nutrition/Supplements signals,
and any clinical or diagnostic capability (which remains prohibited).

## 36. V3-03.1 proposed scope

V3-03.1 should be deliberately small:

1. add account-owned Skin and Hair context rows with controlled enums;
2. add one account-owned shared Care preference row;
3. assemble those rows with existing confirmed inventory, ingredients,
   observations, and a read-only Context adapter;
4. add structured reason/confidence/missing-information schemas;
5. add privacy registry/export/deletion coverage;
6. add isolation and boundary tests.

It must not alter `compile_all()` output, create evidence claims, add shopping,
change the frontend, or introduce an AI safety decision.

## 37. V3-03.1 exact proposed files

These are proposed files only; none are created in V3-03.0:

- `backend/app/domains/care/__init__.py` — domain boundary and public contracts.
- `backend/app/domains/care/models.py` — `SkinCareProfile`,
  `HairCareProfile`, and `CarePreference`.
- `backend/app/domains/care/schemas.py` — controlled enums, patch/request
  validation, and explicit `not_sure` values.
- `backend/app/domains/care/service.py` — account-owned CRUD and
  `build_care_context(...)`; no recommendation engine.
- `backend/app/domains/care/context_adapter.py` — read-only projection from
  `DayContext` and Context snapshots; no weather provider.
- `backend/app/domains/care/reasons.py` — reason-code and categorical
  confidence constants.
- `backend/app/domains/privacy/__init__.py`, `export.py`, and
  `deletion_service.py` — registry, export, and deletion coverage.
- `backend/app/shared/database/registry.py` — model registration only.
- `backend/migrations/versions/<revision>_add_care_context_profiles.py` — one
  migration for the three account-owned rows; exact revision is assigned by
  the migration workflow.
- `backend/tests/test_domain_care_context.py` — isolation, controlled values,
  assembler, and unknown behavior.
- `backend/tests/test_care_privacy.py` — export/deletion and global-data
  separation.
- `backend/tests/test_care_boundaries.py` — non-diagnosis, AI, safety, purchase,
  and attractiveness invariants.

## 38. V3-03.1 proposed migration

One additive migration should create:

1. `skin_care_profiles`: `id`, `account_id` (unique FK with cascade), nullable
   enum-backed declaration columns, `source`, `verification_state`,
   `last_reviewed_at`, `version`, timestamps, and an account index.
2. `hair_care_profiles`: equivalent account-owned row with Hair declarations.
3. `care_preferences`: one account-owned row with shared preference enums,
   source, verification state, version, timestamps, and account index.

All declaration columns are nullable at storage but serialize explicit
`not_sure` when the user chooses it; absence remains missing. No pregnancy,
prescribed topical, diagnosis, severity, or generic medical JSON field is
created. Existing `user_constraints(kind='allergy')` remains the source of
user-declared allergy data until a separately reviewed sensitive-data design
earns a dedicated table.

## 39. V3-03.1 proposed tests

The future test suite must prove:

- Skin/Hair profiles are account-isolated;
- controlled vocabularies reject invented labels and preserve `not_sure`;
- unknown never becomes a negative or a diagnosis;
- existing global rules/evidence are never account-owned;
- Care profiles export and delete with the account;
- environment comes from Context Engine and no second weather provider exists;
- low-confidence/draft ingredient facts cannot drive warnings;
- observations remain verbatim and only produce temporary, non-diagnostic
  inputs;
- Hair has first-class fact coverage;
- hard constraints outrank effort/preferences;
- routine effort cannot override safety;
- no attractiveness score, shopping recommendation, medical claim, or AI safety
  decision is emitted;
- current routine output is unchanged by the foundation.

## 40. Explicitly deferred work

Deferred: Care recommendation behavior, migrations, new APIs, frontend work,
evidence claim approval, background-link activation changes, evidence releases,
routine-load scoring, richer Hair compatibility, event care plans,
maintenance timing, Home Care, Nutrition/Supplements integration, Product
Quality, purchase ranking, RAG, vector search, LLM safety decisions, and new
dependencies.

## 41. CTO/CPO decisions still required

1. Approve whether existing generic `UserConstraint` is sufficient for allergy
   ownership or a separately governed sensitive restriction table is needed.
2. Approve the controlled vocabularies and whether `not_sure` is rendered as a
   first-class user choice.
3. Approve the Event Ready contract and ownership of an eventual `EventCarePlan`.
4. Approve the evidence activation distinction between provenance presence and
   behavior eligibility.
5. Approve whether routine recommendation snapshots are P1 or required before
   behavior changes.
6. Confirm customer-facing migration timing from legacy “Beauty Shelf” labels
   to Skin Care without changing internal compatibility keys.

## 42. Proposed model detail: SkinCareProfile

| Property | Proposal |
| --- | --- |
| owner/table | Care domain; `skin_care_profiles` |
| scope | one account row; account FK cascade; not global |
| fields | `usual_skin_feel`, `sensitivity_tendency`; `source`, `verification_state`, `version`, `last_reviewed_at`, timestamps |
| enums | values in section 13; `not_sure` allowed |
| nullable/required | account required; declarations nullable until asked; explicit `not_sure` is valid |
| indexes | unique `(account_id)`; no global lookup index |
| provenance | `user_declared` only for V3-03.1; imported legacy facts remain `unverified` |
| consumer | Care fact assembler and future Skin decision layer |
| privacy | `INCLUDED`, exportable and deletable; no AI read unless a user-visible decision requests it |
| does not represent | disease, diagnosis, severity, treatment, medication, pregnancy, or attractiveness |

## 43. Proposed model detail: HairCareProfile

| Property | Proposal |
| --- | --- |
| owner/table | Care domain; `hair_care_profiles` |
| scope | one account row; account FK cascade; not global |
| fields | `hair_pattern`, `strand_characteristic`, `density`, `wash_frequency`, `chemical_processing`, `heat_styling_frequency`, `usual_scalp_feel`, `humidity_frizz_sensitivity`, `styling_preference`; source, verification state, version, review/timestamps |
| enums | values in section 14; `not_sure` allowed |
| nullable/required | account required; every declaration optional until relevant |
| indexes | unique `(account_id)` |
| provenance | explicit user declaration; legacy/photo values are candidates, not confirmed Care facts |
| consumer | Care fact assembler and future Hair decision layer |
| privacy | `INCLUDED`, exportable/deletable; AI cannot infer or promote values |
| does not represent | hair-loss diagnosis, scalp disease, medical severity, or appearance judgment |

## 44. Proposed model detail: CarePreference

| Property | Proposal |
| --- | --- |
| owner/table | Care domain; `care_preferences` |
| scope | one account row; account FK cascade; shared by Skin and Hair |
| fields | `routine_effort`, `fragrance_preference`, `event_preparation_effort`, `owned_product_priority`, source, verification state, version, review/timestamps |
| enums | values in section 15; `not_sure` allowed |
| nullable/required | account required; fields nullable until relevant |
| indexes | unique `(account_id)` |
| provenance | explicit user declaration; no AI write without confirmation |
| consumer | ranking/minimization and Event Ready adapter |
| privacy | `INCLUDED`, exportable/deletable; AI access only as input to an already deterministic explanation |
| does not represent | medical restrictions, diagnosis, attractiveness, budget, or purchase intent |

## 45. Proposed `build_care_context(...)` service contract

The service is an assembler, not a recommendation engine:

```text
build_care_context(
    session,
    account_id,
    *,
    day_context: DayContext | None,
    event_id: UUID | None,
    as_of: date,
) -> CareContext
```

`CareContext` contains `skin_context`, `hair_context`, shared `preferences`,
recent verbatim observations, adherence summary, confirmed owned product
facts, confirmed ingredient facts, expiry/availability, an environment
snapshot reference, an optional Event reference, `missing_information`, and
categorical confidence/reason metadata. It does not choose a product, create a
routine, emit a gap, call AI, or call a weather provider.

## 46. Proposed V3-03.1 field-level privacy and provenance

All three proposed rows are account-owned and exportable/deletable. Normal
profile values are not secret, but they may be sensitive in context; access is
account-authorized and audit logged. AI receives only the minimum fields needed
to explain a deterministic decision and never writes them directly.

Allergy remains in existing `user_constraints` and is `INCLUDED`; the future
professional/prescribed/pregnancy context is intentionally **not implemented**
until necessity, optionality, retention, export, AI access, and deterministic
safety effects are approved. Global rule/ingredient/evidence rows remain
`NOT_USER_OWNED`.

## 47. Proposed evidence activation semantics

Future V3-03.1 must introduce a distinction in the evidence-consuming seam:

```text
provenance_present = approved source/claim/link is attached
behavior_evidence_eligible = the link relationship and claim status permit
                              this rule to affect behavior
```

`background` may make `provenance_present=true` for auditability but must not
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

The current flow bypasses the Care Fact Set and calls `compile_all()` from a
`ShelfContext`; the target flow is documented, not implemented.

## 49. Current duplicate-truth/drift risks

- Profile `LifestyleContext.climate`, routine `ShelfContext.climate`, request
  `CLIMATES`, perfume `weather`/`season`, and V3-01 `ClimateContext` can all
  describe “weather” with different vocabularies.
- Routine generation does not consume `DayContext`; environment can therefore
  disagree between Today and Care.
- `beauty`/`hair` product role lives in inventory detail, slot mapping, and
  display labels without a separate Care role contract.
- Profile hair observations and future Hair declarations could diverge unless
  legacy values remain candidates with provenance.
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
| user-declared allergies | implemented via generic Profile/UserConstraint |
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
`LifestyleContext.routine` exist, and `UserConstraint` stores user-declared
allergies, but routine effort, fragrance preference, minimal-routine choice,
owned-first preference, and event-preparation effort are not structured Care
facts. These should not be inferred from adherence or purchase behavior in
V3-03.0.

## 53. Current medical/safety-context coverage

The baseline stores user-declared allergies as generic account constraints and
uses them as a hard product exclusion when the parsed ingredient is confirmed.
`safety.py` and `safety_classifier.py` block diagnostic/treatment wording and
route health-like observations to a professional boundary. No structured
pregnancy, prescribed topical, professional restriction, or diagnosis field is
owned by Care. That absence is safer than a generic medical JSON field.

## 54. Current environmental dimensions consumed by Care

The routines engine consumes only an optional coarse `climate` value from the
request or Profile (`hot`, `humid`, `cold`, `dry`, `rainy`). It does not consume
the V3-01 `DayContext` dimensions `temperature`, `humidity`, `precipitation`,
`UV`, `AQI`, `daily_regime`, or confidence. Existing climate notes are static
rules keyed to `hot`, `humid`, or `cold`, including a hot pre-wash-oil note.

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

V3-03.0 changes only this document. Therefore the dependency files remain
unchanged:

- `backend/requirements.txt` unchanged;
- `frontend/package.json` unchanged;
- `frontend/yarn.lock` unchanged.

The expected diff is one file: `docs/V3_CARE_INTELLIGENCE.md`.

## 61. Final recommendation

`V3-03.0 READY FOR ARCHITECTURE REVIEW`

The authoritative `main` baseline was verified at `94dee83d…`. The
architecture audit is complete and does not authorize V3-03.1 implementation or
merge.
