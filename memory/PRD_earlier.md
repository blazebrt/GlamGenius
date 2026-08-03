# GlamGenius — non-payment stabilisation (fixes 3–20)

## Original problem statement

Continue the non-payment production-readiness work from the current
state of `main` after PR #19. Do not repeat completed work. Do not
call the product production-ready. Do not modify payment mechanics.
Complete the remaining work through separate, reviewable work packages;
open a focused PR per package; stop before beginning the next.

Baseline (verified via `git merge-base --is-ancestor`):
`89c57e5b1f786de3b631d90f29aa257109feb409`.

## Architecture (unchanged in this phase)

- FastAPI backend, one process, `/api` (V1 Mongo) + `/api/v2` (V2 Postgres)
- Expo / React Native frontend (React 19, Expo 54)
- PostgreSQL 16 (V2), MongoDB 6 (V1)
- Alembic migrations 0001–0008 unchanged (frozen)
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
- No auto-merge during the stabilisation phase
- Independent human review before any merge to `main`

## What's been implemented

- 2026-08-02 — **Fix 0** on prior branch (`stabilisation/non-payment-production-readiness`):
  repaired nine time-of-day-dependent test failures in
  `test_planning.py` and `test_progress.py`. Also fixed a real app bug
  in `ProgressPhotoInput` schema that used UTC's `date.today()` where
  the app uses IST. Merged as part of PR #19
  (`89c57e5b1f786de3b631d90f29aa257109feb409`).
- 2026-08-02 — **Fix 3, first pass** (merged in PR #19): CI workflow,
  CODEOWNERS, Dependabot, PR template,
  `docs/stabilisation/BRANCH_PROTECTION_SETUP.md`. Gaps identified
  post-merge: `continue-on-error` on lint and web export,
  `--passWithNoTests`, advisory `pip-audit`/`yarn audit`, major-tag
  action pins, incorrect `gh api -f` fields.
- 2026-08-03 — **Work Package 1, this branch**
  (`stabilisation/01-governance-ci-cleanup`): finished Fix 3
  (strict CI gates, SHA-pinned actions, corrected branch-protection
  API command, CI self-test doc); finished Fix 19 (review policy,
  seven checklists, expanded PR template); finished Fix 20 (branching
  strategy); audited `.emergent/` and `memory/PRD.md`
  (`docs/stabilisation/EMERGENT_HOSTING_AUDIT.md`) and hardened the
  cron dispatcher against Authorization forwarding on cross-host
  redirects; replaced the stale `STABILISATION_REPORT.md` with an
  evidence-based report.

## Prioritised backlog

**P0 — must land before public paid launch**

- Fix 4 (Docker workflow proved) — Work Package 2
- Fix 5 (Live Gemini validation, cost-capped opt-in workflow) — Work Package 3
- Fix 6 (Real weather, calendar OAuth, push transport) — Work Package 5
- Fix 7 (Packing decision: implement or mark planned) — Work Package 4
- Fix 8 (Monitoring: real event proof, alert destination, uptime) — Work Package 5
- Fix 9 (Production media storage: boto3, MinIO CI, hardened validators) — Work Package 2
- Fix 12 (Remove stored image base64 prefixes + cleanup script) — Work Package 2
- Fix 13/14 (Ingredient rule metadata, structured safety classification) — Work Package 3
- Fix 17 (Real-device UX + accessibility) — Work Package 6

**P1 — should land before controlled private beta grows**

- Fix 10 (Architecture inventory + ADRs) — Work Package 4
- Fix 11 (V1 deprecation plan) — Work Package 4
- Fix 15/16 (Photo comparison and metric governance) — Work Package 4
- Fix 18 (Onboarding time-to-first-value under 5 min) — Work Package 4

**P2 — clean-up**

- MongoDB test isolation for `preview_attempts` cleanup between runs
- Pydantic `.dict()` → `.model_dump()` sweep
- FastAPI `on_event` → `lifespan` migration

## Next tasks

1. **Owner:** review the PR for Work Package 1 and enable branch
   protection on `main` per
   `docs/stabilisation/BRANCH_PROTECTION_SETUP.md`, then run the CI
   self-test throwaway-PR procedure in
   `docs/engineering/CI_SELF_TEST.md`.
2. Only after Work Package 1's PR is reviewed and resolved, open
   **Work Package 2** on branch `stabilisation/02-containers-media-privacy`
   (Fix 4, Fix 9, Fix 12).
3. Work Package 3 (`stabilisation/03-ai-safety-evidence`) — Fix 5, Fix 13, Fix 14.
4. Work Package 4 (`stabilisation/04-product-truth-simplification`) —
   Fix 7, Fix 10, Fix 11, Fix 15, Fix 16, Fix 18.
5. Work Package 5 (`stabilisation/05-live-integrations-observability`) —
   Fix 6, Fix 8.
6. Work Package 6 (`stabilisation/06-device-ux-release-evidence`) —
   Fix 17 and the final non-payment readiness report.

## Non-goals in this phase

Payments (Razorpay orders, subscription webhooks, refunds, recurring
billing, checkout mechanics). Kept in a separate later phase, not
opened until Work Package 6 closes.
