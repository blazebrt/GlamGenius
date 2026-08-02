# GlamGenius — non-payment stabilisation (fixes 3–20)

## Original problem statement

Read the attached stabilisation prompt completely.  Baseline audit only
first, then produce a proposed implementation and commit sequence for
fixes 3 through 20.  Do not modify code until the audit is complete.
Payment fixes 1 and 2 are explicitly excluded.  Do not modify Razorpay,
payment webhooks, refunds, recurring-payment logic, payment
entitlements, or billing migrations.  Do not merge anything
automatically.

## Baseline

- Commit: `71483ba742d40a2799922607665b6b522a942552`
- Branch: `stabilisation/non-payment-production-readiness`

## Architecture (unchanged in this phase)

- FastAPI backend, one process, `/api` (V1 Mongo) + `/api/v2` (V2 Postgres)
- Expo / React Native frontend (React 19, Expo 54)
- PostgreSQL 16 (V2), MongoDB 6 (V1)
- Alembic migrations 0001–0008 unchanged
- `SUBSCRIPTIONS_AVAILABLE=false` (kept)

## User personas (unchanged in this phase)

- Indian consumer looking for a personal stylist + skin/hair coach
- Invite-only private beta users
- (Payment personas not exercised in this phase.)

## Core requirements (static)

- Provider-independent tests must run without paid credentials
- Migrations 0001–0008 must not be edited
- No fabricated AI fallbacks, no appearance scoring, no medical
  diagnosis, no supplement dosage, no "money wasted"
- Media, account and cross-account isolation must hold

## What's been implemented (this branch)

- 2026-08-02 — **Fix 0**: repaired nine time-of-day-dependent baseline
  test failures in `test_planning.py` and `test_progress.py`.  Also
  fixed a real app bug in `ProgressPhotoInput` schema that used UTC's
  `date.today()` where the app uses IST.  Verification:
  `pytest -q tests` → 463 passed / 0 failed.
- 2026-08-02 — **Fix 3**: added reproducible non-payment CI at
  `.github/workflows/ci.yml`, plus CODEOWNERS, Dependabot config, PR
  template, and `docs/stabilisation/BRANCH_PROTECTION_SETUP.md`
  (owner-action).
- 2026-08-02 — Docs: `BASELINE_AUDIT.md`,
  `PROPOSED_IMPLEMENTATION_PLAN.md`, `STABILISATION_REPORT.md` (interim).

## Prioritised backlog

**P0 — must land before public paid launch**

- Fix 4 (Docker workflow proved)
- Fix 5 (Live Gemini validation gated by `workflow_dispatch`)
- Fix 6 (Real weather, calendar OAuth, push transport)
- Fix 7 (Packing decision: implement or mark planned)
- Fix 8 (Monitoring: crash + backend + health)
- Fix 9 (Production media storage: boto3, MinIO CI, hardened validators)
- Fix 12 (Remove stored image base64 prefixes + cleanup script)
- Fix 13/14 (Ingredient rule metadata, structured safety classification)
- Fix 17 (Real-device UX + accessibility)

**P1 — should land before controlled private beta grows**

- Fix 10 (Architecture inventory + ADRs)
- Fix 11 (V1 deprecation plan)
- Fix 15/16 (Photo comparison and metric governance)
- Fix 18 (Onboarding time-to-first-value under 5 min)
- Fix 19 (Independent review policy)
- Fix 20 (Branching strategy doc)

**P2 — clean-up**

- MongoDB test isolation for `preview_attempts` cleanup between runs
- Pydantic `.dict()` → `.model_dump()` sweep
- FastAPI `on_event` → `lifespan` migration

## Next tasks (in the exact order in
`docs/stabilisation/PROPOSED_IMPLEMENTATION_PLAN.md`)

1. Owner: enable branch protection on `main` per
   `docs/stabilisation/BRANCH_PROTECTION_SETUP.md`.
2. Fix 4 — Docker verification script.
3. Fix 5 — Live Gemini opt-in workflow.
4. Fix 6 — Weather + Calendar + Push (needs credentials).
5. Fix 7 — Packing decision.
6. Fix 8 — Monitoring (needs Sentry DSN).
7. Fix 9 — Media storage (needs S3 credentials for production; MinIO in CI).
8. Fix 12 — Remove image prefixes.
9. Fix 13/14 — Ingredient + safety.
10. Fix 15/16 — Progress claims + metric governance.
11. Fix 10/11 — Architecture and V1 deprecation.
12. Fix 17 — Device UX (needs devices).
13. Fix 18 — Onboarding.
14. Fix 19/20 — Review policy, branching strategy.
15. Security & privacy review, final test matrix, then open PR.

## Non-goals in this phase

Payments (Razorpay orders, subscription webhooks, refunds, recurring
billing, checkout mechanics).  Kept in a separate later phase.
