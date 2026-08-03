# Proposed implementation plan — fixes 3 through 20

**Baseline commit:** `71483ba742d40a2799922607665b6b522a942552`
**Branch:** `stabilisation/non-payment-production-readiness`
**Excluded:** payment fixes 1 and 2 (Razorpay, webhooks, refunds, recurring
billing, subscription-state transitions, entitlement grants/revocations
caused by payments, checkout mechanics, Razorpay credentials).

This plan is the mandatory precondition to writing any code, as required by
the brief's workflow section.  Each fix below lists:
- **Scope** — what changes and what does not
- **Files** — the specific files to add or edit
- **Commit message** — the exact grouping the brief specifies
- **Acceptance test list** — the assertions that must pass
- **Owner action required?** — where credentials or hardware are needed

Ordering follows the brief's suggested commit grouping, with one addition:
**Fix 0 (baseline defect repair)** precedes Fix 3, as required by the
brief's `BASELINE STOP RULE` (see BASELINE_AUDIT.md §4).

---

## Fix 0 — Repair pre-existing baseline test failures

### Scope
Nine tests fail on `main` at the baseline commit (see `BASELINE_AUDIT.md
§4`).  All are code defects.  Fix 0 must produce the smallest correct fix
per root-cause family, add regression coverage where the existing test
sits above the actual boundary, and rerun the suite to 100% passing.

**Explicitly excluded:** any change to payment code, migrations 0001–0008,
Gemini transport, or product copy.  The catalogue and paywall are not
touched.

### Files (expected — will be confirmed at implementation time)
- `backend/app/domains/planning/compiler.py`
  (version-bump path, wellbeing/hydration emission)
- `backend/app/domains/planning/service.py`
  (cache-invalidation on weather/calendar/clarification writes)
- `backend/app/domains/planning/notifications.py`
  (deduplication idempotency)
- `backend/app/domains/progress/*.py` — one file, off-by-one in the
  "no-buy count" formula near a timezone boundary
- `backend/tests/test_planning.py` and `backend/tests/test_progress.py` —
  additional narrower regression assertions, no weakening of existing
  assertions

### Commit
```
fix(planning,progress): repair pre-existing baseline test failures
```

### Acceptance
- `pytest -q tests` reports `0 failed`, matching or exceeding the previous
  passing count of 454.
- No test is deleted, skipped, or weakened.
- `alembic check` continues to report no drift.
- `git diff main -- backend/app/domains/billing backend/migrations` is
  empty.

### Owner action required?
No.

---

## Fix 3 — Automatic CI and merge gates

### Scope
Add GitHub Actions workflows that run on pull requests and pushes to `main`.
Include: backend tests (with Postgres + Mongo services), Alembic upgrade
from empty, Alembic check, Jest, TypeScript, ESLint, Expo web export,
secret scanning, Python and Node dependency vulnerability scanning, and a
focused authorization/privacy regression job.

### Files
- `.github/workflows/ci.yml` — main provider-independent pipeline, jobs:
  `backend-tests`, `alembic`, `frontend-tests`, `typescript`, `lint`,
  `expo-web-export`, `secret-scan`, `pip-audit`, `npm-audit`,
  `auth-privacy-regression`
- `.github/workflows/dependencies.yml` — Dependabot fallback if
  `dependabot.yml` is unusable in this repo layout
- `.github/dependabot.yml`
- `.github/CODEOWNERS`
- `.github/PULL_REQUEST_TEMPLATE.md` (see also Fix 19)
- `docs/stabilisation/BRANCH_PROTECTION_SETUP.md` — the exact GitHub UI
  steps for the repository owner
- Actions pinned to full commit SHAs where practical (`actions/checkout@
  <sha>`, `actions/setup-python@<sha>`, `actions/setup-node@<sha>`,
  `github/super-linter@<sha>` if used).

CODEOWNERS coverage:
- `backend/app/domains/billing/**` → owner + a security reviewer
- `backend/app/api/v2/billing.py` → owner + a security reviewer
- `backend/migrations/**` → owner
- `backend/app/domains/ai_gateway/**` → owner
- `backend/app/domains/routines/safety.py` → owner
- `backend/app/domains/progress/memory.py` → owner
- `backend/routes/scan.py` → owner
- `.github/**` → owner

### Commit
```
chore(ci): add reproducible non-payment quality gates
```

### Acceptance
- A PR triggers each CI job and the workflow file exists at the correct
  path.
- A deliberately introduced failing test (in a local verification branch,
  not committed) causes the CI to fail.
- CI does not require paid-service credentials for the provider-independent
  jobs.  Gemini, Razorpay, S3, calendar, weather, push, and monitoring
  credentials are **not** referenced in the provider-independent workflow.
- `BRANCH_PROTECTION_SETUP.md` marks the enforcement step **OWNER ACTION
  REQUIRED**.

### Owner action required?
Enabling branch protection in the GitHub UI is owner action.

---

## Fix 4 — Prove the exact clean Docker workflow

### Scope
Repair and verify the documented compose workflow from an empty machine.
Add a `verify_clean_environment.sh` script that spins volumes down, builds
fresh, migrates, tests, lints, exports, and cleans up.

### Files
- `docker-compose.yml`, `docker-compose.test.yml` — minor: pin Python to
  `python:3.11-slim` (already there), Node to `node:20-alpine` (already
  there), keep tmpfs, ensure the backend test image is self-contained.
- `backend/Dockerfile` — add `HEALTHCHECK`, non-root user for runtime.
- `scripts/verify_clean_environment.sh` — the ordered sequence in the
  brief's Fix 4 section
- `scripts/README.md` — Windows / macOS / Linux runbook
- `backend/tests/conftest.py` — teardown that clears the
  `preview_attempts` collection between runs so a long-lived database does
  not leak rate-limit counters (the known "long-lived database test
  isolation weakness" the brief calls out).
- `docs/stabilisation/DOCKER_VERIFICATION.md` — the output log from a
  clean run (produced during Fix 4 implementation).

### Commit
```
build(dev): prove clean container development and tests
```

### Acceptance
- `docker compose -f docker-compose.test.yml run --rm backend-tests` passes
  from empty caches and empty volumes.
- `docker compose -f docker-compose.test.yml run --rm frontend-tests`
  passes similarly.
- Two consecutive clean runs pass.
- A third run against deliberately retained data does not fail because
  test state (e.g., `preview_attempts`) leaked between runs.
- The audit environment cannot fully prove this (no Docker daemon).  A
  substitute is documented and the deliverable includes the exact steps
  and expected output.

### Owner action required?
Only if the audit environment lacks Docker: the final proof is owner-run.

---

## Fix 5 — Real Gemini reliability validation

### Scope
Add a separate opt-in live-provider validation suite triggered by
`workflow_dispatch`.  Provider-independent CI continues to pass without a
key.

### Files
- `backend/tests/live/` — a separate directory, ignored by default pytest
  runs unless `LIVE_GEMINI=1`
  - `test_gemini_baseline_appearance.py`
  - `test_gemini_inventory_extraction.py`
  - `test_gemini_product_label.py`
  - `test_gemini_error_handling.py`
  - `test_gemini_recommendation_explanation.py`
- `backend/pytest.ini` — new markers `live_gemini`, `costly`
- `.github/workflows/live-gemini.yml` — manual `workflow_dispatch`
  workflow with concurrency guards, cost caps, and secrets-off-forks
  protection
- `backend/tests/live/fixtures/` — consented non-sensitive project-owned
  test images (documented licence)
- `docs/stabilisation/LIVE_GEMINI_VALIDATION.md` — how to run, what it
  costs, what to redact

### Commit
```
test(ai): add controlled live Gemini validation
```

### Acceptance
- `pytest tests/` (default) passes without `GEMINI_API_KEY`.
- `pytest tests/live/` requires the key and refuses to run otherwise.
- 429, timeout, invalid JSON, and validation failure are reported as
  distinct outcomes (not collapsed).
- No customer image ever ships in the fixtures.
- The workflow refuses to run on untrusted PRs from forks.

### Owner action required?
`GEMINI_API_KEY` secret must be added to the repository by an owner for the
live workflow to run.

---

## Fix 6 — Make Today genuinely proactive

### Scope
Add **one** real weather adapter, **one** real calendar adapter, **one**
real push transport.  Manual providers remain as fallback.  Every piece
uses current official documentation and records the source in
`docs/stabilisation/INTEGRATIONS.md`.

### Files (draft — subject to provider choices)
- `backend/app/domains/planning/providers/openweather.py` (or similar)
- `backend/app/domains/planning/providers/google_calendar.py`
- `backend/app/domains/planning/notifications.py` — attach an
  `ExpoPushTransport` implementation
- `backend/app/api/v2/integrations.py` — connect / disconnect / status
  routes
- `backend/migrations/versions/0009_integrations_credentials.py` — new
  table `integration_credentials` for OAuth token storage
- Frontend: connection UI in `frontend/src/components/today/`
- `docs/stabilisation/INTEGRATIONS.md`

### Commit
```
feat(integrations): connect weather calendar and push safely
```

### Acceptance
- Today can use real weather without manual entry.
- Today can consume real synced calendar events.
- One push notification is proven to reach a test device (owner action).
- Disconnecting calendar stops future sync and revokes/removes the stored
  credential.
- Provider outages show stale/manual states, not fabricated data.
- All integrations remain optional.

### Owner action required?
- Weather API key.
- Google Cloud project with Calendar API enabled and OAuth consent screen
  configured; client ID / client secret.
- Expo project credentials for push (or a real device token from the app).

---

## Fix 7 — Remove or complete the packing promise

### Scope
Audit every place packing (and the shareable lookboard) is presented as an
available feature.  Choose per feature: implement now or mark "planned".

### Files
- `backend/app/domains/billing/catalogue.py` — update benefit copy for
  `FEATURE_PACKING` and `FEATURE_LOOKBOARD` if marked planned; **do not
  touch grant/revoke code**
- `backend/tests/test_billing.py` — new assertion sweeping benefit strings
  against "coming soon" / "planned" markers
- Frontend: onboarding, settings, plan comparison, event pass screens
- `docs/stabilisation/PACKING_DECISION.md` — the decision and rationale
- If implemented: new module `backend/app/domains/packing/` with
  deterministic non-LLM baseline; UI screen; owner-scoped authorization
  test.

### Commit
```
fix(product): align packing promises with implementation
```

### Acceptance
- No interface claims packing exists unless a user can complete the flow.
- Tests sweep product copy for unsupported promises.
- The chosen decision and rationale are documented.
- Payment mechanics are untouched.

### Owner action required?
No, but the decision (build vs. mark planned) requires product owner input;
default to "mark planned" unless the audit-environment budget allows
implementation of the deterministic baseline.

---

## Fix 8 — Configure real crash and health monitoring

### Scope
Wire a production-capable monitoring service (Sentry recommended given
existing docs) into both frontend and backend, plus a health endpoint that
distinguishes healthy from degraded.

### Files
- `backend/requirements.txt` — add `sentry-sdk[fastapi]`
- `backend/app/shared/observability/monitoring.py` — init + scrubbers
- `backend/server.py` — install monitoring middleware
- `backend/app/api/v2/health.py` (new or extend existing) — combined
  health report
- `frontend/src/monitoring.ts` + `frontend/app/_layout.tsx` — Sentry RN
  init
- `docs/stabilisation/MONITORING.md`
- Scrubbing tests: `backend/tests/test_monitoring_scrubbers.py`

### Commit
```
feat(ops): configure privacy-safe monitoring and health
```

### Acceptance
- A test frontend crash is visible in monitoring (owner-run proof).
- A test backend exception is visible in monitoring (owner-run proof).
- Source maps resolve the frontend stack.
- Automated tests demonstrate that image bytes, ingredient lists, memory
  facts, access tokens, and payment identifiers are scrubbed.
- Health endpoint reports "degraded" when Postgres/Mongo/Redis/worker/
  outbox/provider readiness is not green.

### Owner action required?
Sentry (or chosen provider) DSN.

---

## Fix 9 — Production-ready media storage

### Scope
Add `boto3`, prove the existing S3 adapter against MinIO in CI, add
integration tests, tighten MIME/size/dimension validation, and refuse
local media storage in production unless explicitly acknowledged.

### Files
- `backend/requirements.txt` — add `boto3` and `pillow` (for dimension and
  decompression-bomb checks)
- `backend/app/shared/validation/media.py` — file-signature MIME
  detection, decompression-bomb guard, EXIF stripping
- `backend/app/domains/media/service.py` — orphan-object cleanup on
  transaction rollback
- `backend/app/domains/media/storage/s3.py` — small hardening
- `backend/tests/test_media_s3.py` (new) — integration tests against
  MinIO in CI
- `.github/workflows/ci.yml` — add MinIO service to the media job
- `docs/stabilisation/MEDIA_STORAGE.md` — lifecycle, backup, restore, CDN
  strategy

### Commit
```
feat(media): complete production object storage
```

### Acceptance
- Two accounts cannot access each other's media (existing test extended).
- Deleting a media asset removes/tombstones the DB row and the object.
- A failed DB transaction does not leave uncontrolled orphan objects.
- Invalid MIME, oversized, malformed, and decompression-bomb files are
  rejected.
- Production configuration can use S3 without code changes.

### Owner action required?
Production bucket + credentials.  CI uses MinIO with generated ephemeral
credentials.

---

## Fix 10 — Control architectural overgrowth

### Scope
Produce a truthful inventory of the system, remove **only** proven dead
code, and write ADRs for the choices that are keeping the system where it
is.

### Files
- `docs/stabilisation/ARCHITECTURE_INVENTORY.md`
- `docs/adr/` — new directory
  - `0001-modular-monolith.md`
  - `0002-mongodb-v1-coexistence.md`
  - `0003-postgres-v2-ownership.md`
  - `0004-event-outbox.md`
  - `0005-provider-abstractions.md`
  - `0006-memory-and-metrics.md`
  - `0007-new-table-rules.md`
- Static-analysis output archived in `docs/stabilisation/` (vulture,
  ts-prune, unused-exports reports)
- Any proven dead-code removals in separate small commits with test
  coverage

### Commit
```
refactor(architecture): remove proven dead code and document boundaries
```

### Acceptance
- Truthful inventory produced.
- Dead-code removals supported by tests.
- No existing user data lost.
- New-table rule documented.

### Owner action required?
No.

---

## Fix 11 — V1 deprecation and compatibility plan

### Scope
Do not delete V1.  Add deprecation headers, add usage telemetry, and
document a route-by-route status.

### Files
- `backend/routes/*.py` — add `Deprecation` / `Sunset` headers to
  replaceable V1 routes
- `backend/app/shared/observability/v1_usage.py` — a privacy-safe usage
  counter (route + timestamp, no payload)
- `docs/stabilisation/V1_DEPRECATION_PLAN.md`
- `frontend/src/services/` — ensure new flows call V2 exclusively

### Commit
```
refactor(v1): add safe deprecation controls
```

### Acceptance
- Every active V1 route has a documented status.
- Frontend V1 calls are known and intentional.
- No new V2 feature depends on the legacy V1 coach schema.

### Owner action required?
No.

---

## Fix 12 — Remove stored image base64 prefixes

### Scope
The V1 `image_base64[:80] + "..."` truncation in `routes/scan.py:141` and
its regression test must both change together.  A MongoDB cleanup task
must remove existing fields on historical documents.

### Files
- `backend/routes/scan.py` — remove `image_base64` field from the stored
  `ScanResult`
- `backend/models.py` — remove `image_base64` from the `ScanResult`
  Pydantic model
- `backend/scripts/cleanup_scan_image_prefixes.py` (new) — idempotent
  MongoDB cleanup, dry-run supported, counts documents changed, never
  logs the removed content
- `backend/tests/test_v1_regression.py` — invert the existing test
  (`test_the_face_image_truncation_rule_still_holds` → assert **no**
  `image_base64` field survives)
- `backend/tests/test_no_image_bytes_stored.py` (new) — repository-wide
  sweep on newly written scans

### Commit
```
fix(privacy): remove stored image prefixes
```

### Acceptance
- New scans store no image content or base64 fragment outside the media
  service.
- The cleanup script is idempotent and reports counts.
- Analysis and history routes continue to work.

### Owner action required?
Running the cleanup script against production is owner action.

---

## Fix 13 — Define and expand ingredient-coverage boundaries

### Scope
Every deterministic safety/compatibility rule gets `rule_id`, `version`,
`severity`, `evidence_source`, `reviewer`, `reviewed_date`,
`applicability_limits`, and `status`.  A coverage report documents what is
supported and, more importantly, what is not.

### Files
- `backend/app/domains/routines/rules.py` — extend Rule dataclass and the
  seed loader
- `backend/migrations/versions/0010_rule_metadata.py` — add columns and
  backfill from constants
- `backend/tests/test_ingredient_rules.py` (extend) — enforce metadata on
  every active rule; enforce no duplicate aliases; enforce OCR-like
  misspellings do not silently match
- `docs/stabilisation/INGREDIENT_COVERAGE.md`
- Frontend: label extraction UI must show "coverage is limited"

### Commit
```
feat(safety): version ingredient evidence and structured safety
```

### Acceptance
- Every active warning has reviewed evidence metadata.
- Unsupported ingredients return "not covered", not "safe".
- Low-confidence extraction cannot trigger a warning.

### Owner action required?
No.

---

## Fix 14 — Structured safety classification (folded into Fix 13's commit family)

### Scope
Keep banned-word filters as a secondary defence.  Add structured
classification with typed result, allow/block/replace outcome, machine-
readable reason codes, safe replacement copy.

### Files
- `backend/app/domains/routines/safety.py` — extend beyond banned-word
  matching to a rule-classifier that returns typed outcomes
- `backend/tests/test_safety_classifier.py`

### Commit (grouped with Fix 13)
```
feat(safety): version ingredient evidence and structured safety
```

### Acceptance
- Unsafe content without banned keywords is still blocked.
- Safe disclaimers are not accidentally blocked.
- Every blocked output has a reason code.

### Owner action required?
No.

---

## Fix 15 — Photo-comparison claims and metric governance (with Fix 16)

### Scope
Sweep the frontend and API responses so nothing claims objective validation
of visual progress.  Keep the deterministic `compare` module unchanged
except for surfacing the user-confirmed / technically-checked distinction
in every response.

### Files
- `backend/app/domains/progress/comparison.py` — response shape includes
  `check_kind: "user_confirmed" | "technical" | "unknown"`
- `backend/app/api/v2/progress.py` — response wording
- Frontend: progress screens
- `docs/stabilisation/PROGRESS_CLAIMS.md`

### Commit
```
fix(progress): clarify photo and metric limitations
```

### Acceptance
- A comparison cannot be labelled objectively validated when only user
  input supports it.
- UI states the limitation.
- No automatic claim of improvement or decline is generated.

### Owner action required?
No.

---

## Fix 16 — Metrics as hypotheses (grouped with Fix 15's commit)

### Scope
Add product-governance metadata per metric.  Rewrite metric UI wording.
Add controls: hide, explain, "does not feel accurate".  Privacy-safe
analytics events.  No overall score.

### Files
- `backend/app/domains/progress/metrics.py` — metadata dataclass
- `backend/migrations/versions/0011_metric_governance.py`
- `backend/app/api/v2/progress.py`
- Frontend: metric detail screens
- `backend/tests/test_metrics_governance.py`

### Commit (grouped with Fix 15)
```
fix(progress): clarify photo and metric limitations
```

### Acceptance
- Every metric names the decision it supports.
- Unvalidated metrics are labelled experimental.
- Users can hide or challenge a metric.

### Owner action required?
No.

---

## Fix 17 — Prove mobile UX and accessibility

### Scope
Real device validation matrix.  Cannot be fully proven in the audit
environment; deliverable is the matrix, the automation that can run in CI,
and an owner-action-required checklist for physical devices.

### Files
- `frontend/e2e/` — Detox or Maestro flows for the critical journey (no
  payments)
- `frontend/src/__tests__/accessibility.test.tsx` — automated a11y checks
  using the RN accessibility APIs
- `docs/stabilisation/DEVICE_UX_REPORT.md`
- `docs/stabilisation/ACCESSIBILITY_CHECKLIST.md`

### Commit
```
test(mobile): add real-device UX and accessibility evidence
```

### Acceptance
- Critical journey completes on at least one Android and one iPhone
  (owner-run).
- Accessibility blockers fixed or listed.
- Visual regressions reviewed.
- Performance recorded (numbers, not guesses).

### Owner action required?
Physical devices required.

---

## Fix 18 — Reduce setup burden and time to first value

### Scope
Measure the current onboarding.  Redesign so a new user gets one honest
useful result under five minutes.  Progressive inventory onboarding.  Do
**not** reduce the seven inventory categories.

### Files
- `backend/app/api/v2/onboarding.py` — a "first useful result" endpoint
  that accepts a minimal profile and returns something honest
- `backend/app/domains/analytics/` — privacy-safe funnel events
- Frontend: rework onboarding into progressive steps
- `docs/stabilisation/ONBOARDING_TARGETS.md`

### Commit
```
feat(onboarding): reduce time to first value
```

### Acceptance
- Documented minimal inputs unlock a first useful result.
- All seven inventory categories remain available.
- Setup time and abandonment measurable.

### Owner action required?
No.

---

## Fix 19 — Require independent review before merge

### Scope
Add PR template, checklists, policy document.  CODEOWNERS is added under
Fix 3; the policy is authored here.

### Files
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/checklists/security.md`
- `.github/checklists/privacy.md`
- `.github/checklists/migration.md`
- `.github/checklists/ai-safety.md`
- `.github/checklists/mobile-ux.md`
- `.github/checklists/external-integration.md`
- `docs/engineering/REVIEW_POLICY.md`

### Commit
```
docs(engineering): require independent review and clean branches
```

### Acceptance
- The stabilisation PR remains open for review.
- Contains reproducible evidence.
- No statement says "reviewed" unless a human reviewer actually did.

### Owner action required?
Branch protection and required-reviewer settings in the GitHub UI.

---

## Fix 20 — Clean branch strategy (folded into Fix 19's commit)

### Scope
Document branch naming and lifecycle rules.  Enforce that stabilisation is
on its own branch.

### Files
- `docs/engineering/BRANCHING_STRATEGY.md`

### Commit (grouped with Fix 19)
```
docs(engineering): require independent review and clean branches
```

### Acceptance
- Policy document exists.
- Branch is `stabilisation/non-payment-production-readiness` (verified
  above).

### Owner action required?
No.

---

## Cross-cutting security & privacy audit deliverable

Independent of the numbered fixes:

- `docs/stabilisation/SECURITY_PRIVACY_REVIEW.md` — the labelled table the
  brief requires, produced after Fixes 3, 8, 9, 12, 13, 14, 15, 18 land.

## Final report

- `STABILISATION_REPORT.md` in the repository root, produced last, with
  the 39-point structure the brief specifies.

## Pull-request rules

- No merge is automatic.
- The PR is opened into `main` from `stabilisation/non-payment-production-
  readiness`.
- The PR description contains: baseline commit, branch name, commits,
  test-matrix results, external integrations tested, device tests
  completed, security/privacy findings, known limitations, owner actions
  required, and rollback steps.

## Stop conditions

The agent stops after opening the PR.  Payment work does **not** begin in
this phase.
