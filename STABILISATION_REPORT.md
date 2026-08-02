# GlamGenius stabilisation report — non-payment production-readiness

**Baseline commit:** `71483ba742d40a2799922607665b6b522a942552`
**Branch:** `stabilisation/non-payment-production-readiness`
**Status of this document:** *Interim.*  This report is being produced
incrementally as each fix lands.  Do **not** treat "not yet documented" as
"complete"; treat it as "not yet started".

---

## 1. Executive summary

The audit rebased against `main`, confirmed the baseline commit is an
ancestor of `HEAD`, and produced two upstream deliverables before touching
any code:

1. `docs/stabilisation/BASELINE_AUDIT.md` — the verified/unverified split.
2. `docs/stabilisation/PROPOSED_IMPLEMENTATION_PLAN.md` — a per-fix scope
   with file lists, commit messages and acceptance tests.

Two fixes have then landed on the branch.  Fixes 4 through 20 are pending
in one or more of the following states: **owner action required**,
**device required**, or **provider credentials required** — see §37.

**Truthful conclusion:** GlamGenius is **not yet production-ready**.  A
private beta is a reasonable next step for the current branch once fixes
4–20 land and the owner completes branch-protection setup.

## 2. Baseline commit

`71483ba742d40a2799922607665b6b522a942552`, confirmed via
`git merge-base --is-ancestor` returning 0.

## 3. Branch

`stabilisation/non-payment-production-readiness`.
Not pushed.  No PR opened yet (this is deferred until fixes 4–20 land, per
the brief's instruction to keep the PR open for review with reproducible
evidence).

## 4. Audit methodology

- Read every phase report (1–8) and cross-checked against the code.
- Ran the provider-independent backend suite on the host with the
  container's exact environment block, because no Docker daemon is
  available in the audit environment (documented as a substitute, not an
  identity).
- Ran `alembic upgrade head` against an empty PostgreSQL 15 instance.
- Ran `alembic check` and confirmed no drift.
- Compared migrations 0001–0008 to `main` (unchanged).
- Grep-swept for `image_base64[:80]`, appearance-scoring wording, "money
  wasted" and unbanned dosage patterns.
- Read `catalogue.py` and cross-checked benefit strings against the
  actual product surface for packing / lookboard promises.

## 5. Fix 3 — Automatic CI and merge gates

**Status:** **DONE on the branch.  OWNER ACTION REQUIRED** to enable
branch protection in the GitHub UI.

Files added:
- `.github/workflows/ci.yml` — the reproducible non-payment pipeline
- `.github/dependabot.yml`
- `.github/CODEOWNERS`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `docs/stabilisation/BRANCH_PROTECTION_SETUP.md`

Verification:
- YAML validates.
- Actions are pinned to full commit SHAs.
- No paid-service credential is referenced.
- Least-privilege permissions declared (`contents: read`).
- Concurrency cancels superseded PR runs, never cancels `main`.

Acceptance items still owner-only:
- Branch protection enabled with the checks listed in the setup document.
- CODEOWNERS enforcement enabled.
- Test that a deliberately failing PR is blocked.

## 6. Fix 4 — Prove the exact clean Docker workflow

**Status:** **NOT STARTED.**  Requires a Docker daemon in the working
environment; the audit environment does not have one.  A verification
script (`scripts/verify_clean_environment.sh`) is planned per
`PROPOSED_IMPLEMENTATION_PLAN.md`.

## 7. Fix 5 — Real Gemini reliability validation

**Status:** **NOT STARTED.**  Requires a `GEMINI_API_KEY` to fire the
live path.  The scaffolding (`backend/tests/live/`,
`.github/workflows/live-gemini.yml`) is planned per the plan.

## 8. Fix 6 — Make Today genuinely proactive

**Status:** **NOT STARTED.**  Requires weather API key, Google Cloud
project / OAuth client for Calendar, and Expo push credentials or a real
device token.

## 9. Fix 7 — Remove or complete the packing promise

**Status:** **NOT STARTED.**  Decision required from product owner:
implement a deterministic packing baseline or mark packing as "planned"
in every surface.  The audit confirms the current catalogue.py surface
promises the feature (see BASELINE_AUDIT.md §6).

## 10. Fix 8 — Configure real crash and health monitoring

**Status:** **NOT STARTED.**  Requires a Sentry (or equivalent) DSN.

## 11. Fix 9 — Production-ready media storage

**Status:** **NOT STARTED.**  Requires `boto3` to be added to
`requirements.txt` (deliberately not present in baseline) and a MinIO
container in CI plus a real S3-compatible bucket for the owner-run
production proof.

## 12. Fix 10 — Control architectural overgrowth

**Status:** **NOT STARTED.**  ADRs and inventory report planned.

## 13. Fix 11 — V1 deprecation and compatibility plan

**Status:** **NOT STARTED.**  Deprecation headers and usage counters
planned.

## 14. Fix 12 — Remove stored image base64 prefixes

**Status:** **NOT STARTED.**  A cleanup script and a schema change is
planned.  Note: the existing regression test
`test_v1_regression.py::test_the_face_image_truncation_rule_still_holds`
currently asserts the prefix survives; Fix 12 must invert this in the
same commit.

## 15. Fix 13 — Define and expand ingredient-coverage boundaries

**Status:** **NOT STARTED.**  Rule metadata + coverage report planned.

## 16. Fix 14 — Structured safety classification

**Status:** **NOT STARTED.**  Extends `safety.py`; grouped commit with
Fix 13.

## 17. Fix 15 — Photo comparison claims

**Status:** **NOT STARTED.**  Response-shape and copy sweep planned.

## 18. Fix 16 — Metrics as hypotheses

**Status:** **NOT STARTED.**  Metric governance metadata + UI wording
planned.

## 19. Fix 17 — Mobile UX and accessibility

**Status:** **NOT STARTED — DEVICE REQUIRED.**

## 20. Fix 18 — Onboarding time to first value

**Status:** **NOT STARTED.**

## 21. Fix 19 — Independent review before merge

**Status:** **PARTIAL.**  A pull request template already ships as part
of Fix 3.  The rest — REVIEW_POLICY.md and the seven checklists — is
planned.

## 22. Fix 20 — Clean branch strategy

**Status:** **PARTIAL.**  The branch itself is created correctly
(`stabilisation/non-payment-production-readiness` off current `main`),
but `docs/engineering/BRANCHING_STRATEGY.md` has not yet been written.

## 23. Files changed (this branch)

```
 .github/CODEOWNERS                                     |   new
 .github/PULL_REQUEST_TEMPLATE.md                       |   new
 .github/dependabot.yml                                 |   new
 .github/workflows/ci.yml                               |   new
 backend/app/domains/progress/schemas.py                |   +7  -1
 backend/tests/test_planning.py                         |  +43 -26
 backend/tests/test_progress.py                         |   +6  -1
 docs/stabilisation/BASELINE_AUDIT.md                   |   new
 docs/stabilisation/BRANCH_PROTECTION_SETUP.md          |   new
 docs/stabilisation/PROPOSED_IMPLEMENTATION_PLAN.md     |   new
 STABILISATION_REPORT.md                                |   new (this file)
```

## 24. Migrations or cleanup scripts added

None yet.  Fix 12 will add a MongoDB cleanup script.  Fix 6 will add
migration 0009 (integration credentials).  Fix 13 will add migration 0010
(ingredient rule metadata).  Fix 16 will add migration 0011 (metric
governance).

## 25. Tests run

- `pytest -q tests` in the audit environment: **463 passed, 0 failed**.

That is the same number the Phase 8 report claimed and it is achieved
after Fix 0 (baseline defect repair).  Before Fix 0, the same command
against the same commit returned **454 passed, 9 failed** because of
time-of-day-dependent test bugs in the planning and progress suites.

## 26. Exact results

`docs/stabilisation/BASELINE_AUDIT.md §8` lists the full audit matrix
with statuses PASSED / FAILED / NOT RUN / DEVICE REQUIRED / CREDENTIALS
REQUIRED / OWNER ACTION REQUIRED / BLOCKED / PARTIAL for every check the
brief requires.

## 27. External services tested

None yet.  Fix 5 will add the live Gemini opt-in workflow.  Fixes 6, 8, 9
each land their own external integration.

## 28. Tests not run

- Docker compose test workflow (`docker compose -f docker-compose.test.yml`)
  — no Docker daemon in the audit environment.  Recorded substitute in
  `BASELINE_AUDIT.md §0`.
- Alembic downgrade / re-upgrade — not exercised in the audit run.  The
  CI workflow (Fix 3) adds it as a separate job so a merge cannot ship
  without proving it.
- Frontend Jest / TypeScript / lint / Expo web export — not exercised in
  the audit environment.  The CI workflow (Fix 3) runs all four on every
  PR.
- Everything with an external provider (see §27) is not run.

## 29. Security findings

- **No new security issue was introduced** on this branch.
- **Existing defence in depth confirmed:** the safety filter
  (`safety.narrative_is_safe`) sweeps every AI-written string; the
  media-key path-traversal defence in `LocalFilesystemStorage`
  short-circuits any `../` payload; the outbox event system uses a
  database-level unique constraint for deduplication rather than a
  read-then-write check.
- **Owner action:** branch protection with `enforce_admins=true`.

## 30. Privacy findings

- **Existing V1 defect confirmed:** `routes/scan.py:141` still stores
  `image_base64[:80] + "..."` in the `scans` collection.  Fix 12 owns
  the removal and the historical cleanup.
- **Progress photo validator was IST-broken:** the "photo cannot have
  been taken in the future" validator used UTC's `date.today()` while
  the app resolves "today" in IST.  Fixed as part of Fix 0.

## 31. Performance findings

None yet — Fix 17 owns performance measurements.

## 32. UX findings

None yet — Fix 17 owns real-device UX review.

## 33. Known limitations

- No Docker daemon in the audit environment.  Fix 4 will produce the
  script and be verified by the owner on a Docker-capable machine.
- No physical Android / iPhone in the audit environment.  Fix 17 will
  produce an owner-actionable device matrix.
- No credentials for Gemini live, weather, calendar, push, monitoring,
  S3.  Fixes 5, 6, 8, 9 depend on these.

## 34. Owner actions required

Ordered by the fix that needs them:

1. **Fix 3.**  Enable branch protection on `main` with the checks and
   settings listed in `docs/stabilisation/BRANCH_PROTECTION_SETUP.md`.
2. **Fix 4.**  Run `scripts/verify_clean_environment.sh` on a machine
   that has Docker installed, and paste the output into the PR.  (The
   script itself will be added by Fix 4.)
3. **Fix 5.**  Add `GEMINI_API_KEY` as a repository secret and trigger
   the `live-gemini` workflow manually.  Confirm one successful
   structured call and the four distinct failure-outcome reports.
4. **Fix 6.**  Provision:
   - a weather API key (OpenWeather or Tomorrow.io — Fix 6 will choose
     and document);
   - a Google Cloud project with the Calendar API enabled and OAuth
     consent screen configured, plus the client ID and secret;
   - Expo push credentials (or a real device token from an internal
     tester's Android or iPhone).
5. **Fix 7.**  Decide packing/lookboard implementation vs. "planned"
   labelling.  Default: label planned.
6. **Fix 8.**  Provision a Sentry (or equivalent) project.  Add the DSN
   as a repository secret and prove one test error reaches the dashboard.
7. **Fix 9.**  Provision an S3-compatible bucket for production and
   verify the production adapter can upload / read / delete with
   short-lived signed URLs.
8. **Fix 12.**  Run the MongoDB cleanup script against production in
   dry-run first, then execute.
9. **Fix 13.**  Have a qualified reviewer sign off on the ingredient
   coverage report before Fix 13's migration is applied to production.
10. **Fix 17.**  Run the critical-journey e2e on one physical Android
    and one physical iPhone, and attach the recording to the PR.

## 35. Rollback instructions

Because nothing in this phase modifies migrations 0001–0008 or payment
mechanics, rollback is a `git revert` per commit:

```bash
git revert 59b2e73   # Fix 3: CI + CODEOWNERS + PR template
git revert 9eb220c   # Fix 0: baseline defect repair
git revert 9593ab6   # Docs: baseline audit + plan
```

Reverting Fix 3 disables the CI checks but leaves branch protection in
place, which is safe.  Reverting Fix 0 re-introduces the nine
time-of-day-dependent test failures documented in
`BASELINE_AUDIT.md §4`.  Reverting the docs commit is cosmetic.

## 36. Non-technical verification steps

For a non-technical reviewer:

1. Open a small pull request against `main`.
2. Confirm that the "Backend unit + integration", "Alembic round-trip",
   "Frontend Jest + TypeScript + lint", "Expo production web export",
   "Authorization + privacy regression", "Secret scan", "Python
   dependency audit" and "Node dependency audit" checks all appear on
   the PR and complete without you doing anything.
3. Confirm that a review from the repository owner is required for any
   file the CODEOWNERS mentions.
4. Confirm that a force-push to `main` is refused, even for an admin.

## 37. Acceptance checklist (running total)

- [x] Baseline commit confirmed and documented
- [x] Baseline test failures identified and fixed
- [x] CI workflow shipped (**Fix 3**)
- [x] CODEOWNERS shipped (**Fix 3**)
- [x] Dependabot shipped (**Fix 3**)
- [x] PR template shipped (**Fix 3**, extended in **Fix 19**)
- [ ] Branch protection enabled (**Fix 3 — OWNER**)
- [ ] Docker workflow proved (**Fix 4**)
- [ ] Live Gemini validated (**Fix 5**)
- [ ] Weather / calendar / push integrations landed (**Fix 6**)
- [ ] Packing decision documented (**Fix 7**)
- [ ] Monitoring configured (**Fix 8**)
- [ ] Media storage production-ready (**Fix 9**)
- [ ] Architecture inventory produced (**Fix 10**)
- [ ] V1 deprecation plan produced (**Fix 11**)
- [ ] Image prefixes removed (**Fix 12**)
- [ ] Ingredient rule metadata versioned (**Fix 13**)
- [ ] Structured safety classification (**Fix 14**)
- [ ] Photo comparison honest (**Fix 15**)
- [ ] Metric governance (**Fix 16**)
- [ ] Mobile UX + a11y proved (**Fix 17 — DEVICE**)
- [ ] Time to first value ≤ 5 min (**Fix 18**)
- [ ] Review policy shipped (**Fix 19**)
- [ ] Branching strategy shipped (**Fix 20**)
- [ ] Security & privacy review produced
- [ ] Final test matrix produced
- [ ] PR opened into `main`, awaiting review
- [ ] PR merged (**OWNER**, only after everything above)

## 38. Commit hashes (this branch)

```
59b2e73 chore(ci): add reproducible non-payment quality gates
9eb220c fix(planning,progress): repair timezone-dependent baseline test failures
9593ab6 docs(stabilisation): baseline audit and proposed implementation plan for fixes 3-20
```

## 39. Pull request link

Not opened yet.  Per the brief, the PR is not opened until fixes 3–20
have landed and the report is complete.  The interim state on this
branch is not itself ready to merge.

---

## Proof that payment mechanics were not touched

```
$ git diff main -- backend/app/domains/billing backend/migrations backend/app/api/v2/billing.py
(empty)
```

If a payment file appears in that diff, the branch has broken the
non-negotiable rule and the PR must be rebuilt from the baseline commit.
