# Phase 6 — Beauty, Hair, Perfume, Supplement and Appearance-Nutrition Intelligence

Turning a catalogued shelf into safe, useful routines built out of things the user already
owns — without the app becoming a diagnosis tool or a calorie tracker.

---

## 1. Baseline

Verified before any Phase 6 code was written, on `main` with Phase 5 merged.

| | |
|---|---|
| Baseline commit | `fc82dff` (merge of PR #15, Phase 5) |
| Branch | `claude/code-handover-prep-8uhjje`, restarted from `origin/main` |

Phases 1–5 confirmed complete and passing:

| Check | Result |
|---|---|
| `alembic upgrade head` | clean |
| `alembic check` | no drift |
| Backend pytest | **232 passed** |
| `tsc --noEmit` | clean |
| `expo lint` | 0 errors, 3 pre-existing warnings |
| Jest | **117 passed** |

### The same environment deviation as Phases 4 and 5, stated again

`docker compose -f docker-compose.test.yml run --rm backend-tests` still cannot build here:
`deb.debian.org` and Docker Hub's blob CDN are blocked by the egress policy. The **databases
are the exact containers the compose file specifies** (`mongo:6`, `postgres:16-alpine`, pulled
through the permitted `mirror.gcr.io`); the test process runs on the host with the identical
environment block and the same commands in the same order. On a machine with normal registry
access the documented commands run this work unchanged.

---

## 2. The one decision the whole phase rests on

The brief says two things that pull in opposite directions:

> The LLM explains results. **Do not let the LLM invent ingredient safety rules.**

A prompt cannot enforce that. "Never invent a rule" in a system prompt is a request, and a
model under pressure will produce something plausible anyway. So Phase 6 puts the rule in the
**structure** rather than in the prompt:

1. A warning is a `Finding`, and `Finding` **cannot be constructed without a `rule_id`**
   (`rules.py`). A warning with no reviewed rule behind it is not expressible in the type.
2. Every `rule_id` the engine can emit is enumerable — `rules.all_rule_ids()` — and a test
   asserts every warning produced from a realistic shelf is in that set.
3. Those same rules are **seeded into the database** by migration `0006`, imported from
   `ontology.py` rather than copied, so the code and the rows cannot drift apart.
4. The model is handed rule ids and asked for wording. `explanation.explain_findings` drops
   any note whose `rule_id` is not one that actually fired. That is a set-membership test, not
   a judgement call.
5. Every string the model returns is swept by `safety.narrative_is_safe` before storage. One
   diagnostic or dosage phrase discards the whole narrative — never the result underneath.

The order of operations in `service.py` is therefore always: gather confirmed facts → let the
deterministic engine decide → **write what it decided** → *then*, optionally, ask a model to
phrase it. Step 4 can fail, time out, be switched off, or be rejected by the sweep, and the
routine the user sees is the same routine in plainer words.

A test proves it: with the provider throwing, the generated routines have identical kinds and
identical step order to a run with the model switched off entirely.

---

## 3. The safety boundary, in code rather than in prose

`safety.py` is the file that keeps a styling app from drifting into being a medical one. It
fails closed and it is deliberately blunt.

**Banned language** — three lists (`DIAGNOSTIC_TERMS`, `PRESCRIPTIVE_TERMS`, `APPEARANCE_TERMS`)
plus a regex for bare dosages (`\b\d+\s?(mg|mcg|iu|g|ml)\b`). `narrative_is_safe` is applied
to AI output *and* to the reviewed deterministic strings, because a review that happened once
does not survive a careless edit six months later.

**Bluntness has a cost, and we paid it rather than weakening the check.** The phrase `"you have "`
is banned outright, because that is how a diagnosis starts. That also catches perfectly
innocent sentences, so three of our own strings were reworded around it:

| Was | Now |
|---|---|
| "You have nothing recorded for this step." | "Nothing is recorded for this step yet." |
| "If you have none, skip the heat tool…" | "With none on the shelf, skip the heat tool…" |
| "Add one you already own if you have it…" | "Add one you already own if there is one…" |

The same happened with nutrition: the obvious disclaimer wording — "not calorie tracking, not
a way of diagnosing anything" — trips the sweep on `calorie` and `diagnos`. Narrowing the
sweep so a disclaimer could pass would have weakened it for everything else, so the disclaimer
is worded to make the same promise without naming what it rules out: *"nothing here counts or
totals what you eat, and it is not a way of working out what is wrong."*

**Professional boundary** — `needs_professional()` matches on the user's own words and is
deliberately generous. A false positive shows a polite "talk to a doctor"; a false negative
lets the product answer something it has no business answering. A user's observation is still
saved verbatim; the app just does not pretend to have an answer.

**Supplements** — the set of things the app may say is closed and defined in code: expiry
status, a missing date, and a pointer at a professional when the user's own recorded purpose
reads like a health question. No dosage. No effect. No interactions. The response literally
lists what we do not do.

---

## 4. What was built

### Reviewed knowledge (`ontology.py`, `nutrition.py`)

- **44 ingredients** with INCI names, families and plain-language summaries
- **127 aliases**, because labels print `Sodium Hyaluronate`, `hyaluronic acid`, `Hyaluronan`
  and `HA` interchangeably and matching one spelling reads a full shelf as empty
- **9 pairwise compatibility rules**, **8 climate rules**, **8 perfume context rules**,
  **10 nutrient rules** with everyday Indian foods
- **5 routine templates**: morning, evening, wash day, weekly extras, before an event

One entry is worth calling out. Vitamin C + niacinamide is one of the most repeated cautions
online, and the evidence does not support it. The rule is filed at `info` severity and the
guidance says so, with a note tracing the claim to a 1960s study on unformulated heat-stressed
ingredients. The app corrects the myth rather than echoing it. A test pins that severity.

### The deterministic engine

`parser.py` reads labels with longest-alias-first matching and word boundaries, so `ha` does
not match inside `shampoo`. Everything it produces carries a confidence and a
`needs_confirmation` flag.

**A low-confidence read never raises a warning.** `_rule_applies` looks only at
`confirmed_families`. A photo-read ingredient is surfaced as "please confirm this" and starts
driving rules only after a human says yes — and a re-read of the label never un-confirms what
a person deliberately confirmed. A wrong warning is worse than a missing one.

`rules.py` covers allergies, ingredient overlap, compatibility, duplicate slots, missing
required slots, expiry, low use, climate, and owned-first ranking. Two ranking details:

- a product **running out is preferred**, because using it up is the point
- an expired product **drops to the bottom rather than disappearing**, because hiding it makes
  the user think they still have a working one

`compiler.py` builds the five routines. A required step with nothing owned becomes a **gap that
names a category, never a product** — no brand, no link. An optional step with nothing owned is
left out entirely rather than shown empty. Every step carries all seven things the brief asks
for: the owned product, why, order, frequency, required/optional, a safety note, and what to do
if it is unavailable.

### Shelf intelligence (`shelf.py`)

Current products, duplicate categories, routine overlap, missing categories, ingredients
already owned, expiring, low use, Value to Recover scoped to the shelf, and consistency.

**No overall shelf score.** A single number would be a judgement of somebody's choices and
there is no honest way to compute one. Counts only. Value to Recover reuses the Phase 3
estimator unchanged and reports a missing price as missing rather than inventing one.

**Consistency has no streaks.** A streak turns a missed Tuesday into a failure, and this is a
routine, not a game. Days followed out of days looked at, and the copy says missing a day is
not a problem.

### Perfume (`perfume.py`)

Ranks bottles the user owns against occasion, weather, time, season, style and recent usage. A
scent worn in the last three days goes down the list so the collection actually gets used, and
the user's own tags beat any convention we could apply.

It explicitly refuses to claim chemistry: *"We make no claim about how a fragrance behaves on
your skin — we have no way to know that."* Where the reason is genuinely just convention, it
says so in those words.

### Nutrition (`nutrition.py`)

Appearance-adjacent food context, filtered by diet, with Indian foods throughout. No calories,
no targets, no deficiency claims. Off by default — nobody gets food suggestions because they
catalogued a shampoo.

Diet is a **constraint, not a suggestion**: a vegetarian is never shown fish, a vegan never
shown paneer, a Jain diet also excludes root vegetables. And when a nutrient's common sources
are *all* excluded by someone's diet, the app says so and points at a dietitian rather than
dropping the nutrient in silence.

The collagen entry is recorded plainly because collagen supplements are widely marketed on a
claim the evidence does not support: collagen you eat is broken down like any other protein.

### Database — migration `0006_routine_intelligence`

Eighteen tables, forward-only and purely additive. Nothing migrations 0001–0005 created is
renamed, altered or dropped.

They split two ways. **Reviewed reference data** (`ingredients`, `ingredient_aliases`,
`ingredient_rules`, `compatibility_rules`, `contraindication_rules`, `routine_templates`,
`perfume_context_rules`, `appearance_nutrition_rules`) has no `account_id` and is seeded from
the domain modules. **User data** is account-scoped and starts empty.

`product_expiry_events` is deliberately distinct from Phase 3's `item_expiry_events`: that one
records dates the user *declared*, this one records what the engine *computed* from them, with
the rule id that produced each.

Seeded on upgrade: 44 ingredients, 127 aliases, 44 ingredient notes, 9 compatibility rules,
2 contraindication rules, 5 routine templates, 8 perfume rules, 10 nutrition rules.

### API — all behind `v2_routines`, 404 when off

```
POST   /api/v2/shelf/analyse              GET /api/v2/shelf/summary
GET    /api/v2/shelf/expiring             GET /api/v2/shelf/low-use
GET    /api/v2/shelf/value-to-recover
POST   /api/v2/routines/generate          GET /api/v2/routines/today
POST   /api/v2/routines/steps/{id}/complete
GET    /api/v2/routines/consistency       GET /api/v2/routines/improve
POST   /api/v2/routines/observations      GET /api/v2/routines/observations
POST   /api/v2/ingredients/check          POST /api/v2/ingredients/confirm
GET    /api/v2/ingredients/{key}
GET    /api/v2/perfume/recommendation
GET    /api/v2/supplements/summary
GET    /api/v2/nutrition/appearance-suggestions
GET/PATCH /api/v2/nutrition/preferences   GET/PATCH /api/v2/nutrition/hydration
```

Ownership always comes from the signed token. Every request schema is `extra="forbid"`, so an
injected `account_id` is a 422 rather than something that might quietly be trusted — tested.

### UI

- **Inventory → Your shelf** (`app/shelf.tsx`): beauty & hair, perfumes, supplements
- **Today**: the routine due right now, an optional perfume, an optional food idea — each
  rendering `null` when there is nothing to say
- **You → Improve** (`app/improve.tsx`): routine overview, consistency, products needing
  attention, expiring, low use, missing categories, ingredients awaiting confirmation

Every component returns `null` on empty rather than an encouraging placeholder. The brief is
explicit that a user who has not populated a module should not be shown it, and an empty card
on Today every morning is how people learn to scroll past a screen.

Warnings render the `rule_id` on screen. If we cannot name the rule, we should not be warning.

---

## 5. Verification

All commands run from the repository root unless noted.

| Check | Result |
|---|---|
| `alembic upgrade head` | clean, 0001 → 0006 |
| `alembic check` | **No new upgrade operations detected** |
| Backend pytest | **322 passed** (232 baseline + 90 new) |
| `tsc --noEmit` | clean |
| `expo lint` | 0 errors, 3 pre-existing warnings (unchanged) |
| Jest | **151 passed** (117 baseline + 34 new) |

`backend_test.py` (V1) is covered by `tests/test_v1_regression.py`, which passes — including
`test_the_face_image_truncation_rule_still_holds`. The `image_base64[:80]` truncation in
`routes/scan.py` is untouched; stored values remain 83 characters.

### What the 90 backend tests cover

Ingredient aliases · compatibility rules · allergy constraints · expiry calculations · routine
order · duplicate categories · owned-first ranking · perfume context · supplement safety
boundaries · **no dosage advice** · **no diagnosis** · nutrition diet preferences · vegetarian
alternatives · unsupported medical claims · climate adjustment · low-confidence extraction ·
user confirmation · routine completion · authentication on every route · cross-account access.

Three of them are the ones that matter most:

- `test_every_warning_the_engine_can_raise_names_a_reviewed_rule` — the acceptance criterion,
  tested rather than asserted
- `test_the_model_cannot_invent_a_safety_rule` — the model returns a fabricated `rule_id`
  alongside a real one; only the real one survives
- `test_nothing_a_route_returns_ever_contains_banned_language` — one sweep over every Phase 6
  response body

The 34 frontend tests cover accessible routine UI: every step ticked by label, checkbox state
exposed to a screen reader, gaps that cannot be ticked, and every empty state.

---

## 6. Acceptance criteria

| Criterion | Where |
|---|---|
| Every conflict warning references a deterministic reviewed rule | `Finding` requires `rule_id`; `test_every_warning_the_engine_can_raise_names_a_reviewed_rule` |
| Existing owned products are preferred | `rank_for_slot`; `test_only_owned_products_can_ever_fill_a_step` |
| Products suggested only for meaningful gaps | required slots only; `test_a_missing_required_step_becomes_a_gap_that_names_a_category_not_a_product` |
| No supplement dosage advice | `safety.PRESCRIPTIVE_TERMS` + `_DOSE_PATTERN`; `test_no_dosage_language_survives_the_safety_sweep` |
| No medical diagnosis | `safety.DIAGNOSTIC_TERMS`; `test_no_diagnostic_language_survives_the_safety_sweep` |
| Nutrition remains appearance focused | `nutrition.py`; `test_nutrition_never_counts_calories` |
| Perfume uses context and owned inventory | `perfume.recommend`; `test_perfume_is_chosen_from_bottles_the_user_owns_and_never_invented` |
| Every routine step explains why it exists | `RoutineStep.why`; `test_every_step_explains_why_it_exists` |
| Low-confidence extraction requires confirmation | `CONFIRMATION_THRESHOLD`; `test_a_low_confidence_read_never_raises_a_conflict_warning` |
| All relevant tests pass | 322 backend, 151 frontend |
| `PHASE_6_REPORT.md` exists | this file |
| The phase is committed | `feat(v2): add safe appearance routines and shelf intelligence` |

---

## 7. Turning it on, and turning it off

```bash
cp env.example .env          # V2_FEATURES now includes v2_routines
alembic upgrade head         # applies 0006 and seeds the reviewed rules
```

Remove `v2_routines` from `V2_FEATURES` and every Phase 6 route returns 404 — a switched-off
feature looks like it does not exist, not like something the caller is missing access to. The
`feature_flags` table overrides the environment at runtime without a redeploy. Nothing in
Phases 1–5 changes either way.

---

## 8. What Phase 6 deliberately does not do

- **No product recommendations to buy.** Gaps name a category. There is no brand, no link, no
  price anywhere in this phase.
- **No scalp or skin condition detection**, at any confidence, under any wording.
- **No supplement interactions**, even the well-known ones. That is a pharmacist's job.
- **No hydration target.** A litre figure would be a health instruction.
- **No outside service integration.** No dependency was added; every module in this phase is
  standard library, SQLAlchemy, Pydantic and FastAPI, all already present.

Stopping after Phase 6, as instructed.
