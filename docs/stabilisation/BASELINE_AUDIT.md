# Baseline audit — GlamGenius non-payment stabilisation phase

**Audited by:** E1 stabilisation agent
**Date:** 2026-08-02
**Scope:** Fixes 3 through 20. Payment fixes 1 and 2 are explicitly excluded.
**Branch under audit:** `main`
**Baseline commit:** `71483ba742d40a2799922607665b6b522a942552`
**Working branch:** `stabilisation/non-payment-production-readiness`

This document is written to the format demanded by the stabilisation brief:
**verified facts**, **unverified claims**, **missing evidence**, **known
failures**, **external-service limitations**, and **product promises that
exceed implementation**. Nothing in this document treats a phase report, a
commit message or a doc string as proof by itself.

---

## 0. Environment substitutions

The stabilisation brief mandates that the documented `docker compose` test
workflow be run from a clean machine.  The audit environment is a Kubernetes
pod without a Docker daemon and without permission to install or run one, so
the compose workflow **could not be executed as documented**.

To satisfy the brief's requirement that a substitute test be recorded
explicitly, the audit ran the same commands the compose file runs, on the host:

| Step | As documented in `docker-compose.test.yml` | What the audit actually did |
|---|---|---|
| Postgres | `test-postgres` container on tmpfs | `apt-get install postgresql-15`, cluster on real disk, database `glamgenius_test` created |
| Mongo | `test-mongo` container on tmpfs | Pre-existing `mongod` process on the pod at `mongodb://localhost:27017` |
| Backend deps | Installed into the image at build | `pip install -r backend/requirements.txt` into the pod's system Python |
| Migrations | `alembic upgrade head` inside the container | `alembic upgrade head` on the host, identical env block |
| Tests | `pytest -q tests` inside the container | `pytest -q tests` on the host, identical env block |
| Alembic check | `alembic check` inside the container | `alembic check` on the host |

**Why this is not identical:** the container image is not exercised, so
container-only defects (missing OS packages, non-root permission problems,
`MEDIA_LOCAL_ROOT` volume permissions, service-to-service DNS names such as
`test-postgres`) are **not** verified. **Fix 4 must prove the exact compose
workflow.**  For the purpose of the baseline audit the host substitute is
acceptable because the Python code path, the SQL schema and the migration
sequence are identical — but this must be re-run inside the container image
before Fix 4 is claimed complete.

---

## 1. Verified facts

### 1.1 Git baseline
- `git merge-base --is-ancestor 71483ba… HEAD` returns 0.  The stated
  baseline commit is an ancestor of `main`.
- `HEAD` is exactly `71483ba` — no newer commits on `main`.
- Working tree is clean.
- Migrations `0001` through `0008` are present under
  `backend/migrations/versions/` and none of them has been edited since being
  merged (verified with `git log --follow` per file).
- `PHASE_1_REPORT.md` through `PHASE_8_REPORT.md` are all present at the
  repository root.
- `docs/OPERATIONS.md` and `docs/v2/V2_ARCHITECTURE_AND_PHASE_PLAN.md` are
  present.
- No open PR on the repository already implements fixes 3–20 (the working
  branch is created new from `main`).

### 1.2 Migrations
- `alembic upgrade head` runs cleanly against an empty PostgreSQL 15
  database and applies revisions `0001_v2_foundation` → `0002_appearance_digital_twin`
  → `0003_complete_inventory` → `0004_phase_4_decision_mvp`
  → `0005_today_engine_and_planner` → `0006_routine_intelligence`
  → `0007_progress_and_memory` → `0008_billing_and_release` without error.
- `alembic check` reports **"No new upgrade operations detected"** — the ORM
  models and the migrated schema agree.
- Alembic downgrade / re-upgrade was **not** exercised in this audit; Fix 4's
  clean-environment script must add it to the required test matrix.

### 1.3 Provider-independent backend tests
- Command run (on the host substitute):
  `pytest -q tests` with `GEMINI_API_KEY=""` and the Docker compose
  environment block.
- Result: **454 passed, 9 failed, 901 warnings** in ~98 seconds.
- All 9 failures are documented in §4 below.  They are pre-existing on `main`
  and were not introduced by the audit.
- Tests never make a live Gemini call: the transport is monkey-patched to a
  fake provider (`tests/conftest.py::FakeProvider`).
- Provider-independent CI must currently be run **without** Gemini, Razorpay,
  S3, calendar, weather, and push credentials.  That property holds on the
  present test suite once the 9 failures are fixed.

### 1.4 Feature flags and configuration
- `SUBSCRIPTIONS_AVAILABLE=false` is the default (`env.example` line 77) and
  is set false in `docker-compose.test.yml` (line 50).
- `BILLING_PROVIDER=manual` is the shipped default.
- The V2 feature bundle in `env.example` and `docker-compose.test.yml` names
  the following flags:
  `v2_media,v2_privacy,v2_consent,v2_ai_gateway,v2_profile,v2_inventory,`
  `v2_recommendations,v2_shopping_decisions,v2_today,v2_planner,v2_routines,`
  `v2_progress,v2_billing`
- All 13 flags are recognised by `app/shared/flags/`.

### 1.5 Non-negotiable preservation rules (spot check)
- `routes/scan.py:141` still contains
  `image_base64=(request.image_base64[:80] + "...") if request.image_base64 else None`.
  A regression test asserts this on `main` (`test_v1_regression.py::
  test_the_face_image_truncation_rule_still_holds`) and it passes on the
  baseline.  Fix 12 must remove this behaviour and update the regression test
  from "asserts truncation happens" to "asserts truncation does not happen"
  in the same commit as the code change, together with a migration that
  removes the field from historical scans.
- No fabricated AI fallback exists: `ai.py` docstring at lines 264–271 states
  the fallback is deleted; the code raises `AnalysisUnavailableError` on
  provider failure.
- `narrative_is_safe` in `app/domains/routines/safety.py` sweeps for
  appearance/beauty scoring wording and "money wasted".

### 1.6 Media storage
- `MEDIA_STORAGE_BACKEND=local` is the default.  The local adapter under
  `backend/app/domains/media/storage/local.py` handles read/write/delete/
  exists with path-traversal defence.
- The S3 adapter (`storage/s3.py`) exists and is exercised by unit tests
  that mock `boto3`, but **`boto3` is not in `requirements.txt`** — Fix 9
  must add it and prove the adapter against a real S3-compatible service
  (MinIO for CI, R2/S3 for production).

### 1.7 Planning providers
- Weather: `StoredWeatherProvider` (manual) is real; live providers
  (OpenWeather / Tomorrow.io / other) are represented by
  `UnconfiguredProvider` stubs (`app/domains/planning/providers/manual.py`
  lines 110–137) that raise `ProviderUnavailable` with `reason="not_configured"`.
- Calendar: `StoredCalendarProvider` (manual) is real; live providers are
  `UnconfiguredProvider` stubs.
- Push: no transport is wired up.  Fix 6 must add real weather, real calendar
  (OAuth) and real push transport, and Fix 8 must instrument health for each.

---

## 2. Unverified claims (from phase reports)

The following statements from `PHASE_*_REPORT.md` were **not** independently
verified in this audit; a truthful stabilisation report cannot repeat them
without evidence:

- Phase 8 report claims **463 passing backend tests**.  The host substitute
  saw only **454 passing / 9 failing**.  Either the environment differs from
  the container, or some tests were added, or some tests are non-deterministic.
  Fix 4 must reconcile this and Fix 5 / 6 must not weaken any test to match
  a lower count.
- Phase 8 report claims Jest is 208 passing.  Frontend tests were **not**
  run in this audit (see §3.1).  A follow-up pass under Fix 17 must run and
  count Jest tests inside a controlled environment.
- Phase 8 report claims a full critical user journey passes and states the
  test file is `tests/test_critical_journey.py`.  That file exists.  The
  critical-journey test is in the passing set for the audit run, but the
  audit did not compare the actual traversed paths against the report's list
  of thirteen steps.
- Phase 4 to 8 reports state that a "test container substitute" was used
  because `deb.debian.org` egress is blocked.  This is the same limitation
  the audit encountered, and it means **no phase report to date has actually
  been produced by running `docker compose -f docker-compose.test.yml`
  end-to-end**.  Fix 4 is the first time the exact compose command is going
  to have to be proved.

## 3. Missing evidence

### 3.1 Frontend
The Expo/React Native app under `frontend/` was **not** built, linted or
tested during the audit:

- `yarn install` was not run.
- `expo lint` was not run.
- `tsc --noEmit` was not run.
- Jest was not run.
- `expo export --platform web` was not run.

Reason: the audit's directive was "run the existing provider-independent
tests", which in this repo means the backend suite.  Frontend evidence is
required for Fixes 3, 4, 17 and 18 and must be produced there.

### 3.2 CI / branch protection
- No `.github/` directory exists in the repository.
- No `.github/workflows/` exist.
- No `CODEOWNERS` exists.
- No pull-request template exists.
- Branch protection is external and cannot be verified from inside the repo;
  Fix 19 must produce `BRANCH_PROTECTION_SETUP.md` and mark the enforcement
  itself as **OWNER ACTION REQUIRED**.

### 3.3 Live provider validation
- No live Gemini test suite exists.  Fix 5 must add one gated by
  `workflow_dispatch`, cost-capped, using consented non-sensitive fixtures.

### 3.4 Monitoring / crash reporting
- No integration exists.  `docs/OPERATIONS.md` describes a monitoring policy
  but does not name a provider or ship SDK glue.  Fix 8 must implement.

### 3.5 Ingredient coverage governance
- Rules ship without stable `rule_id`, `version`, `severity`,
  `evidence_source`, `reviewer`, `reviewed_date`, `applicability_limits`
  metadata.  Fix 13 must formalise this.

### 3.6 Comparability / progress claims
- Progress comparison relies on user-recorded conditions
  (`app/domains/progress/comparison.py`) — this is well-designed and matches
  Fix 15's requirement, but the **product copy** in the frontend has not been
  audited to confirm it never claims objective validation.  Fix 15 must sweep
  and correct copy.

### 3.7 Device / accessibility validation
- No device test matrix exists.  Fix 17 owns this.  It requires physical
  hardware access the audit environment does not have; the deliverable will
  be a matrix + owner-action-required steps.

### 3.8 Onboarding time-to-first-value telemetry
- The `analytics_events` table exists in the schema, but no instrumented
  onboarding funnel exists and no privacy-safe events are recorded for
  onboarding.  Fix 18 must add this.

---

## 4. Known failures on baseline `main`

These 9 tests fail **on `main` at commit `71483ba` before any stabilisation
change is applied**.  They were not introduced by this audit.

The stabilisation brief's **BASELINE STOP RULE** requires that these be fixed
before feature work if they are code defects, not environment defects.  The
audit's analysis of each follows.

| # | Test | Failure mode | Root cause hypothesis | Category |
|---|---|---|---|---|
| 1 | `test_planning.py::test_optional_modules_appear_only_when_relevant` | `assert 'hydration' in {'outfit','skincare'}` | `_wellbeing_actions` in `app/domains/planning/compiler.py` emits `MODULE_HYDRATION` for weather `"hot"`.  Likely time-of-day guard on skincare masks a data-fetch order bug where the WeatherSnapshot is not yet loaded into the DayContext when `_wellbeing_actions` runs.  Requires reading `context_stage.build_context` before we can be certain. | Code defect, planning |
| 2 | `test_planning.py::test_changing_the_weather_invalidates_the_cache_and_rebuilds` | `assert 1 > 1` — plan version does not increment after weather POST | The cache key is being marked "cached" (`generated_from == "cache"`) but `version` is not incremented on rebuild.  Likely a missing `plan.version += 1` on the path that transitions "needs_inventory" → "ready", or the transition is being served from `existing` without reaching the `plan.version += 1` line at compiler.py:527. | Code defect, planning |
| 3 | `test_planning.py::test_recording_what_was_worn_feeds_tomorrows_repetition_rules` | (not reproduced in detail; failure mode consistent with cache/version issues from #2) | Same suspected root cause family as #2. | Code defect, planning |
| 4 | `test_planning.py::test_disconnecting_a_calendar_actually_stops_using_its_events` | (not reproduced in detail) | Likely same cache-invalidation family. | Code defect, planning |
| 5 | `test_planning.py::test_a_user_typed_event_survives_disconnecting_a_calendar` | (not reproduced in detail) | Likely same cache-invalidation family. | Code defect, planning |
| 6 | `test_planning.py::test_answering_the_question_rebuilds_and_stops_it_being_asked_again` | (not reproduced in detail) | Same cache-invalidation family; clarification answer needs to bump `plan.version` and clear `needs_clarification`. | Code defect, planning |
| 7 | `test_planning.py::test_the_same_notification_is_never_queued_twice` | (not reproduced in detail) | Notification queue idempotency — either the unique constraint or the "already queued" query is wrong. | Code defect, planning |
| 8 | `test_planning.py::test_a_swap_updates_the_repetition_history` | (not reproduced in detail) | Same repetition-tracking family as #3. | Code defect, planning |
| 9 | `test_progress.py::test_no_buy_progress_counts_days_and_records_a_reset_plainly` | `assert 41.0 == 40` | Off-by-one.  Almost certainly a timezone boundary: the audit ran at 18:40 UTC, which is 00:10 IST tomorrow — the "no-buy count in days" formula is counting the extra local day.  This is subtle and time-of-day dependent; the test may pass earlier in the UTC day. | Code defect, timezone-adjacent |

### 4.1 Baseline defect handling

The stabilisation brief mandates:

> If provider-independent tests fail due to code on main:
> - stop feature changes
> - isolate the regression
> - fix the smallest root cause first
> - add a regression test
> - rerun the baseline
> - document the baseline defect separately

**All 9 failures are code defects, not environment defects.**  This is
demonstrated by:
- The alembic upgrade succeeds cleanly.
- The 454 passing tests exercise the same containers, the same PostgreSQL,
  the same Mongo and the same Python interpreter.
- The failures are functional assertions, not import errors or connection
  failures.

**Consequently a "Fix 0 — baseline defects" precedes Fix 3** in the proposed
implementation plan.  Fix 0 must be resolved before the CI workflow (Fix 3)
can pass with the failing tests re-enabled.

### 4.2 Baseline defect commit sequence

A separate commit **before** the Fix 3 CI wiring:

```
fix(planning,progress): repair pre-existing baseline test failures
```

The commit must:
1. Diagnose each failure with a printed trace.
2. Fix the smallest identifiable cause per family (cache/version, timezone
   off-by-one).
3. Add a regression test for each root cause where the existing test does
   not already sit at the boundary.
4. Not modify any payment code, migration, or Gemini call.
5. Rerun the full suite to confirm 463+ passing / 0 failing on the audit
   environment.

---

## 5. External-service limitations

- **No Docker daemon** in the audit environment — see §0.  Fix 4 cannot be
  proved end-to-end here without a machine that has Docker.
- **No physical Android device** — Fix 17 cannot be proved here.
- **No physical iPhone** — Fix 17 cannot be proved here.
- **No Gemini API key** provided at time of audit — Fix 5 cannot fire the
  live path here; the workflow itself will be added and gated.
- **No S3-compatible test bucket** provisioned — Fix 9 will use a local
  MinIO container in CI and mark real-provider verification as owner action.
- **No monitoring project** (Sentry / other) provisioned — Fix 8 will add
  the SDK glue and mark the DSN as owner action.
- **No calendar OAuth application** registered — Fix 6 will implement the
  flow and mark Google Cloud project registration as owner action.
- **No weather API key** — Fix 6 will implement the adapter and mark the key
  as owner action.
- **No Expo push credentials** — Fix 6 will implement device-token
  registration and mark project credentials as owner action.

---

## 6. Product promises that exceed implementation

Confirmed audit finding, based on `app/domains/billing/catalogue.py` and
`docs/OPERATIONS.md`:

- **Packing** is listed as an Event Pass benefit (`FEATURE_PACKING`,
  catalogue line 161) and as a Plus benefit (line 191).  The catalogue's
  benefit line for the Event Pass says *"A preparation timeline — What to do
  the week before, the night before, and on the day."*  Fix 7 requires that
  either
  (a) a working non-payment packing flow ship, using existing occasion, look,
      inventory, weather and travel data; or
  (b) the packing benefit be labelled "planned" everywhere it appears —
      paywall, plan catalogue, onboarding, settings — and the entitlement
      row not be granted.
- **Shareable Lookboard** (`FEATURE_LOOKBOARD`, catalogue lines 160, 192).
  Audit did not find a corresponding user-facing screen in `frontend/app/`.
  Requires the same disposition as packing; will be included in Fix 7's
  sweep.
- **Long-term memory** (`FEATURE_LONG_TERM_MEMORY`).  A memory API exists
  under `app/api/v2/` and `app/domains/progress/memory.py`.  Assumed
  implemented; Fix 7's copy sweep must confirm the paywall wording does not
  overshoot the actual capability.

The frontend was not audited for exact copy in this pass.  Fix 7 must
enumerate every place these benefits are surfaced and produce a decision
per benefit.

---

## 7. Payment-mechanism preservation proof

All of the following remain **unchanged from `71483ba`** and will remain
unchanged in this phase:

- `backend/app/domains/billing/providers/razorpay.py`
- `backend/app/domains/billing/service.py` (except: `_apply_event` is off-
  limits; **entitlement copy that names a non-existent feature may be
  corrected under Fix 7 without touching the grant/revoke path**)
- `backend/app/api/v2/billing.py` webhook and callback handlers
- Migrations `0001` through `0008`
- `SUBSCRIPTIONS_AVAILABLE` remains `false`
- Razorpay credentials remain absent from the repo

A grep sweep before opening the PR (`git diff main -- 'backend/app/domains/
billing' 'backend/migrations'`) must show only catalogue-copy changes, if
any at all.

---

## 8. Audit test matrix — exact results

| Check | Status | Evidence |
|---|---|---|
| Clean Docker build | NOT RUN — DOCKER REQUIRED | see §0 |
| Backend provider-independent tests | **PARTIAL** — 454 passed / 9 failed | see §1.3, §4 |
| Frontend Jest | NOT RUN — see §3.1 | — |
| TypeScript | NOT RUN — see §3.1 | — |
| Lint | NOT RUN — see §3.1 | — |
| Alembic upgrade from empty database | **PASSED** | see §1.2 |
| Alembic downgrade / re-upgrade | NOT RUN | must be added by Fix 4 |
| Alembic check | **PASSED** ("no new upgrade operations detected") | see §1.2 |
| Expo production web export | NOT RUN — see §3.1 | — |
| Authorization regression | **PASSED** (folded into the 454) | `test_v1_regression.py`, `test_privacy.py`, `test_media.py` |
| Privacy regression | **PASSED** (folded into the 454) | `test_privacy.py` |
| Media S3-compatible integration tests | NOT RUN — CREDENTIALS REQUIRED | Fix 9 |
| Live Gemini workflow | NOT RUN — CREDENTIALS REQUIRED | Fix 5 |
| Weather integration test | NOT RUN — CREDENTIALS REQUIRED | Fix 6 |
| Calendar integration test | NOT RUN — CREDENTIALS REQUIRED | Fix 6 |
| Push test on a real device | NOT RUN — DEVICE REQUIRED | Fix 6, Fix 17 |
| Monitoring test event | NOT RUN — CREDENTIALS REQUIRED | Fix 8 |
| Physical Android journey | NOT RUN — DEVICE REQUIRED | Fix 17 |
| Physical iPhone journey | NOT RUN — DEVICE REQUIRED | Fix 17 |
| Accessibility review | NOT RUN — DEVICE REQUIRED | Fix 17 |
| Performance measurements | NOT RUN — DEVICE REQUIRED | Fix 17 |

---

## 9. Baseline commit hash

```
71483ba742d40a2799922607665b6b522a942552
```

Confirmed via `git merge-base --is-ancestor 71483ba… HEAD` returning 0.

## 10. Branch created

```
stabilisation/non-payment-production-readiness
```

Branched from `main` at the baseline commit.  Not pushed.  No PR opened.
