# Phase 7 — Progress, Long-Term Memory, and Meaningful Gamification

Making GlamGenius improve over time while leaving the user in complete control of what it
remembers — and without ever producing a number that rates them.

---

## 1. Baseline

Verified before any Phase 7 code was written, on `main` with Phase 6 merged.

| | |
|---|---|
| Baseline commit | `bc6e9e0` (merge of PR #16, Phase 6) |
| Branch | `claude/code-handover-prep-8uhjje`, restarted from `origin/main` |

Phases 1–6 confirmed complete and passing:

| Check | Result |
|---|---|
| `alembic upgrade head` | clean |
| `alembic check` | no drift |
| Backend pytest | **322 passed** |
| `tsc --noEmit` | clean |
| `expo lint` | 0 errors, 3 pre-existing warnings |
| Jest | **151 passed** |

### One pre-existing test-isolation issue, found and reported rather than fixed

Running the full backend suite repeatedly against one long-lived database eventually fails
`test_privacy.py::test_signed_out_preview_requires_explicit_consent` with a 429. The cause is
not a regression: the signed-out preview route is rate-limited to three attempts per IP per
window, and that counter lives in Mongo (`preview_attempts`). The intended workflow —
`docker compose run`, with throwaway tmpfs databases — never hits it, because each run starts
from an empty database.

I did **not** modify the test. It is not named by this phase, and weakening a privacy test to
make a local run tidier would be the wrong trade. The workaround for a long-lived database is
one line:

```bash
docker exec <mongo-container> mongosh glamgenius_test --eval 'db.preview_attempts.deleteMany({})'
```

Worth fixing properly one day by having the suite clear that collection between runs.

### The same environment deviation as Phases 4–6, stated again

`docker compose -f docker-compose.test.yml run --rm backend-tests` still cannot build here:
`deb.debian.org` and Docker Hub's blob CDN are blocked by the egress policy. The **databases
are the exact containers the compose file specifies** (`mongo:6`, `postgres:16-alpine`, pulled
through the permitted `mirror.gcr.io`); the test process runs on the host with the identical
environment block. On a machine with normal registry access the documented commands run this
work unchanged.

---

## 2. The decision the whole phase rests on

The brief's first constraint is one line:

> **Do not create an overall attractiveness score.**

Every appearance app drifts into one. It is the most requested feature and the most harmful,
and "we decided not to" is worth nothing eighteen months later when somebody adds a dashboard
headline. So Phase 7 makes it **structurally impossible**:

1. Every metric declares the **domains** it reads (`inventory`, `usage`, `looks`, …).
2. `registry.validate_registry()` — which runs at import, not in a test — rejects any metric
   whose inputs name another metric.
3. A composite of the thirteen metrics is therefore not expressible. Somebody adding
   `overall_score` with `inputs=("wardrobe_readiness", "outfit_variety")` cannot import the
   module.

A test proves the guard fires rather than merely existing: it constructs exactly that metric
and asserts the import-time validator raises.

The same technique is applied to the wording. `FORBIDDEN_METRIC_WORDS` bans "attractiveness",
"beauty score", "overall score", "how good you look" and eight others from every metric's own
label, formula and explanation.

---

## 3. Every displayed number has a documented formula

`MetricDefinition` has **no defaults for any of the six required fields**. A metric without a
formula, formula version, declared inputs, missing-data behaviour, explanation or update
frequency does not compile. There is a seventh required field this phase added:
`not_a_measure_of`, because for most of these the misreading is more harmful than the number.

The thirteen metrics, all at formula version v1:

| Metric | Direction | Why that direction |
|---|---|---|
| Wardrobe Readiness | higher better | |
| Wardrobe Utilisation | higher better | |
| Outfit Variety | **neutral** | Wearing a favourite combination often is a fine way to dress |
| Occasion Preparedness | higher better | |
| Routine Consistency | higher better | Counts *days*, so a heavy day cannot paper over a gap |
| Product Expiry Risk | lower better | |
| Value to Recover | lower better | Framed as value to *use*, never money lost |
| Purchase Efficiency | higher better | |
| Inventory Balance | **neutral** | There is no correct shape for a wardrobe |
| Travel Readiness | higher better | |
| Seasonal Readiness | higher better | |
| No-Buy Progress | higher better | A reset is recorded plainly, never as a failure |
| How you said you felt | **neutral** | Only ever what the user typed |

Three of the thirteen are neutral on purpose. `inventory_balance` returns counts and refuses
to produce a score at all — reporting a "balanced wardrobe" target would be a judgement dressed
as a measurement.

### Missing data is never zero

This is the detail most likely to be got wrong, and it matters. A metric that cannot be
computed returns `status="unavailable"` with `value=None` and the missing inputs **named**.
Returning `0.0` would render as a real, bad score, and nobody reading it could tell the
difference between "you have done nothing" and "we do not know yet".

The frontend honours it: an unavailable metric shows a dash and its reason, and draws **no bar
at all** — a zero-width bar and a zero-value bar look identical to a person, and one of them
is an accusation.

### Reproducible, not merely plausible

Every `metric_events` row stores its formula version *and the actual input counts the formula
consumed*. Given a stored event you can redo the arithmetic by hand and get the same number —
against the formula that was live at the time, because `metric_definitions` is keyed on
`(key, formula_version)` and changing a formula adds a row rather than rewriting history.

Duplicate events are prevented by a SHA-256 hash of account, metric, formula version, period
and inputs, under a **unique constraint**. Two concurrent jobs cannot both pass a check and
both insert, because there is no check — there is a constraint.

---

## 4. Memory the user actually controls

Every fact carries all eight things the brief requires: the fact, its source, confidence, when
it was created, when it was last reinforced, the user's verification state, its linked
evidence, and its deletion state.

The hard promise is *deleted memory no longer affects recommendations*. Two mechanisms:

**One door.** `memory.active_facts()` is the only accessor for anything that influences what a
user sees. It filters deleted facts, rejected facts, disabled categories and low-confidence
hunches **at the query level**, so a consumer cannot forget a filter — there is no unfiltered
accessor to forget. The Memory Control screen uses
`all_facts_including_deleted()`, named at that length precisely so nobody reaches for it by
accident.

**A tombstone, not a flag.** Deletion keeps the row so the audit trail survives, but blanks the
`fact` text. Even a consumer that bypassed the accessor entirely would find an empty string.
That is belt and braces on purpose: the promise is worth more than the row.

Other decisions worth stating:

- **Reinforcement, not duplication.** The same observation seen five times becomes one fact we
  are more sure of, not five identical rows.
- **A rejected fact is not relearned.** The user's answer stands until they change it.
- **A correction outranks anything inferred** — it goes to full confidence, because they are
  the authority on themselves.
- **A disabled category stops both writing and reading** without destroying anything, so it can
  be switched back on intact. Feedback into a disabled category returns "we did not store
  anything" rather than storing it quietly.
- **An inferred fact never reaches full confidence.** Only a person confirming it does that.

The export includes deleted facts — with empty text and their revision history — because an
export that quietly omits deletions is not an honest export.

---

## 5. Photo comparison that refuses

> If conditions are not comparable, explain why. **Do not create fake visual progress.**

Every misleading before-and-after ever made was built out of different lighting. So the
comparability check runs six conditions — body area, lighting, angle, framing, image quality
and time gap — and **defaults to refusal**: `comparable` is false unless every blocking check
passes, so a check that is somehow skipped fails closed.

Conditions are **recorded by the user when they take the photo**, not inferred from the image.
Guessing lighting from pixels would be another model whose failures nobody could see, and the
user knows whether they stood by the same window. Those fields are required in the schema, so
a photo we would have to guess about cannot be saved as comparable.

Every check is evaluated rather than short-circuited, so a refusal lists everything that
differed — and it comes with specific guidance ("stand in the same spot by the same window")
rather than a bare "not comparable".

Both ends are enforced: a gap under 7 days shows normal day-to-day variation, and over 400 days
too much else has changed for a side-by-side to mean much.

---

## 6. Gamification that rewards something real

> Reward meaningful behaviour. Do not reward meaningless app openings. Use mature wording.

Three mechanisms rather than three intentions:

**A closed list.** `REWARDABLE_BEHAVIOURS` contains nine actions, none of them an engagement
metric. `NEVER_REWARDABLE` names the ten that must never become one — `app_opened`,
`daily_login`, `time_in_app`, `invited_a_friend` — and both the import-time validator and the
service layer refuse them.

**A banned-word sweep.** Milestone copy is checked against `FORBIDDEN_WORDS`, covering the
childish register ("badge", "trophy", "level up", "congratulations") and the shaming one
("finally", "at last", "you failed").

**Anti-abuse in the database.** Every gamification event carries a hash of the real-world thing
that happened, under a unique constraint. Logging the same action twenty times counts once. A
test submits it five times and asserts one row.

Wording is flat by design: "Five unused products back in use" states a fact rather than
cheering. The UI uses a tick, not a trophy.

Streaks exist only where a run of days genuinely is the thing being measured — a no-buy period.
Routine Consistency deliberately does *not* use one, because a streak turns a missed Tuesday
into a failure.

---

## 7. Database — migration `0007_progress_and_memory`

Seventeen tables, forward-only and purely additive. Nothing migrations 0001–0006 created is
renamed, altered or dropped.

**One deliberate departure from the brief's table list.** The brief lists `appearance_goals`,
which already exists from Phase 2 — and this migration does not extend it. That table is a
*projection* of a profile attribute: `profile.service.sync_projections` deletes and rebuilds
every row in it whenever the profile is patched. Attaching months of goal history to it would
lose that history silently on an unrelated profile edit. Phase 7 adds `progress_goals` with an
optional `appearance_goal_id` link instead, so a declared goal can be promoted into a tracked
one without either table changing shape.

Reviewed reference data (`metric_definitions`, `milestone_rules`) is seeded from the domain
modules rather than copied, so the formula a user is shown and the formula the engine ran are
the same string by construction. Seeded on upgrade: **13 metric definitions, 12 milestone
rules**.

---

## 8. API — all behind `v2_progress`, 404 when off

```
GET  /api/v2/progress                     GET  /api/v2/progress/metrics
GET  /api/v2/progress/metrics/{key}       POST /api/v2/progress/self-report
POST /api/v2/progress/photos              GET  /api/v2/progress/comparisons
GET/POST /api/v2/goals                    PATCH /api/v2/goals/{id}
GET  /api/v2/memory                       PATCH /api/v2/memory/{id}
DELETE /api/v2/memory/{id}                GET  /api/v2/memory/export
PATCH /api/v2/memory/categories/{cat}     POST /api/v2/memory/feedback
GET  /api/v2/milestones                   POST /api/v2/milestones/{id}/acknowledge
```

Ownership always comes from the signed token. Every request schema is `extra="forbid"` — an
injected `account_id` is a 422, tested.

The `/progress` response carries `no_overall_score: true` as a field, so a client rendering it
cannot accidentally imply otherwise.

---

## 9. UI

**You** now holds: My Appearance · **Progress** · **Memory** · Improve · Privacy · Settings.

**Progress** (`app/progress.tsx`) — weekly and monthly views, explainable metric cards with the
formula on tap, goals, photo comparisons where valid, milestones, and a "not enough
information yet" section that is honest rather than empty. It opens with the "no single number"
note, because a screen of percentages invites the eye to average them.

**Memory** (`app/memory.tsx`) — every fact with its source and why it is held, confirm/correct/
forget on each, category switches, and export. Every fact states outright whether it is
**currently shaping suggestions**: "we remember this" and "we are acting on this" are different
claims, and someone deciding whether to delete needs the second one.

Deleting is one tap with no "are you sure?" nag. Talking somebody out of deleting their own
data is a dark pattern.

Accessibility: every metric and goal carries an `accessibilityLabel` with its value in words,
bars are hidden from screen readers as decorative, the formula disclosure exposes
`accessibilityState={{ expanded }}`, and category switches use `accessibilityRole="switch"`.

---

## 10. Verification

| Check | Result |
|---|---|
| `alembic upgrade head` on a **fresh** database | clean, 0001 → 0007 |
| `alembic downgrade -1` then re-upgrade | clean |
| `alembic check` | No new upgrade operations detected |
| Backend pytest | **408 passed** (322 baseline + 86 new) |
| `tsc --noEmit` | clean |
| `expo lint` | 0 errors, 3 pre-existing warnings (unchanged) |
| Jest | **184 passed** (151 baseline + 33 new) |

Also verified: with `v2_progress` removed from `V2_FEATURES` every Phase 7 route returns 404
while Phases 1–6 keep working; no V1 file is touched; the `image_base64[:80]` truncation in
`routes/scan.py` is byte-identical to `main`; no dependency was added.

### What the 86 backend tests cover

Every metric formula · metric versioning · missing data · duplicate metric events · time-series
calculations · photo comparability · memory source · memory deletion · memory export · memory
authorization · user correction · goal progress · streak calculations · no-buy progress ·
gamification anti-abuse · disabled-memory categories · privacy controls.

The ones that matter most:

- `test_the_registry_rejects_a_metric_built_from_other_metrics` — builds the forbidden
  composite and asserts the import-time guard raises
- `test_a_deleted_fact_stops_influencing_recommendations` — deletes through the API, then
  checks the accessor every consumer uses *and* that the text is gone
- `test_the_same_action_cannot_be_counted_twice` — submits one action five times, gets one row
- `test_a_milestone_rewarding_engagement_is_rejected` — tries to add an "opened the app" rule

The 33 frontend tests cover accessible progress charts: values in words, unavailable metrics
that never render as zero, and every screen-reader label.

---

## 11. Acceptance criteria

| Criterion | Where |
|---|---|
| Every displayed score has a documented formula | `MetricDefinition` has no defaults; `test_every_metric_documents_all_six_required_things` |
| There is no universal attractiveness score | `validate_registry` forbids metric-of-metrics; `test_the_registry_rejects_a_metric_built_from_other_metrics` |
| Users can inspect and delete memory | `/api/v2/memory`, Memory screen; `test_a_remembered_fact_carries_everything_the_brief_requires` |
| Deleted memory no longer affects recommendations | `memory.active_facts` + text blanking; `test_a_deleted_fact_stops_influencing_recommendations` |
| Photo comparisons require comparable conditions | `comparison.compare` defaults to refusal; eleven comparability tests |
| Gamification rewards useful behaviour | `REWARDABLE_BEHAVIOURS` / `NEVER_REWARDABLE`; `test_no_milestone_rewards_opening_the_app` |
| Value to Recover is constructive and transparent | `test_value_to_recover_is_framed_constructively` |
| Metric events are reproducible | inputs + formula version stored; `test_a_metric_event_stores_the_inputs_it_used` |
| All relevant tests pass | 408 backend, 184 frontend |
| `PHASE_7_REPORT.md` exists | this file |
| The phase is committed | `feat(v2): add explainable progress and controlled long-term memory` |

---

## 12. Turning it on, and turning it off

```bash
cp env.example .env          # V2_FEATURES now includes v2_progress
alembic upgrade head         # applies 0007 and seeds the metric contracts
```

Remove `v2_progress` from `V2_FEATURES` and every Phase 7 route returns 404. The
`feature_flags` table overrides the environment at runtime without a redeploy. Nothing in
Phases 1–6 changes either way.

---

## 13. What Phase 7 deliberately does not do

- **No overall score, and no way to add one.** Not a policy — a structural constraint.
- **No inferred confidence.** "How you said you felt" is only ever what the user typed.
- **No photo analysis.** Comparability is decided from conditions the user recorded, not from
  a model reading the image.
- **No engagement rewards.** No app opens, no logins, no time-in-app, no referrals.
- **No "are you sure?" on deletion.** Nagging somebody out of deleting their own data is a
  dark pattern.
- **No dependency added.** Everything in this phase is standard library, SQLAlchemy, Pydantic
  and FastAPI, all already present.

Stopping after Phase 7, as instructed.
