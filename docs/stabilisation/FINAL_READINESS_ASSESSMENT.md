# Non-payment production-readiness — final assessment

**Baseline commit:** `89c57e5b1f786de3b631d90f29aa257109feb409`
(merge commit of PR #19, before the stabilisation series began).
**Final commit at time of writing:** the tip of `main` after WP6 merges.
**Reviewers:** the owner and the independent reviewer named on each
work-package PR.

This is the closing assessment for the non-payment stabilisation
phase. It re-evaluates the truthful conclusion at the top of
`STABILISATION_REPORT.md` against the state of the tree, the CI,
the docs, and the device sweep evidence (Fix 17).

> **Honest headline.** After Work Packages 1–5, the codebase is in
> materially better shape than at the baseline commit: CI is a
> strict merge gate; governance is documented; media privacy is
> hardened; safety is deterministic; live-provider proofs exist;
> and the documentation is truthful about which fixes are shipped,
> planned, or deferred to a device walk.
>
> **Whether the app is production-ready is a Work Package 6
> outcome, not a Work Package 5 one.** The sweep protocol,
> device matrix, and results template are shipped on this branch.
> The actual walk, at least on the two P0 devices, is an owner
> action. Until at least one clean P0 sweep is recorded, this
> document treats Fix 17 as PARTIAL and the phase as NOT closed.

## 1. Per-fix status

Copied from `STABILISATION_REPORT.md §16` at the time of writing
and re-verified against the live tree.

| Fix | State |
|---|---|
| 0 — Baseline timezone defects | DONE on `main` (PR #19). |
| 3 — CI + governance | DONE on `main` (PR #33, WP1). |
| 4 — Docker reproducibility | DONE on `main` (PR #35, WP2). Verify script exists; the two-cycle physical run is optional evidence (owner action on a Docker host). |
| 5 — Live Gemini validation | DONE on `main` (PR #36, WP3). Owner adds `GEMINI_API_KEY` and dispatches the workflow when a live-provider proof is wanted. |
| 6 — Weather / calendar / push | PARTIAL on `main` (PR #38, WP5). Weather (Open-Meteo, keyless) shipped and live-verified. Push (Expo, keyless) shipped. Calendar (Google) documented-planned with least-privilege scope; manual-entry path already works. |
| 7 — Packing decision | DONE on `main` (PR #37, WP4). Documented as planned, not shipped; catalogue id retained without fulfilment. |
| 8 — Monitoring | PARTIAL on `main` (PR #38, WP5). SDK bootstrap exists; live-monitoring workflow ready to dispatch with 4 Sentry secrets. |
| 9 — Production S3-compatible media | DONE on `main` (PR #35, WP2). Production-refusal guard, presigned URLs, SSE, MinIO integration test. |
| 10 — Architecture inventory + ADRs | DONE on `main` (PR #37, WP4). One-page inventory + five ADRs. |
| 11 — V1 deprecation plan | DONE on `main` (PR #37, WP4). Five-phase plan, opens as its own series after WP6. |
| 12 — Remove stored image base64 prefixes | DONE on `main` (PR #35, WP2). New writes clean; cleanup script idempotent, verified against a real Mongo. |
| 13 — Ingredient rule metadata | DONE on `main` (PR #36, WP3). Every rule id has an evidence row in `INGREDIENT_COVERAGE.md`; enforced by a test. |
| 14 — Structured safety classification | DONE on `main` (PR #36, WP3). Thirteen typed categories, deterministic, model second-opinion is additive-only. |
| 15 — Photo comparison honesty | DONE on `main` (PR #37, WP4). Policy documented; enforcement is Fix 14's classifier. |
| 16 — Metric governance | DONE on `main` (PR #37, WP4). Policy + register; every event has decision-informed / hypothesis / owner / retirement / privacy-cost. |
| 17 — Physical-device UX + a11y | **PARTIAL on this branch (WP6).** Sweep protocol, device matrix, and results template shipped. The actual sweep is an owner action; the phase closes once at least one clean P0 sweep is recorded. |
| 18 — Time to first value ≤ 5 min | DONE on `main` (PR #37, WP4). Policy documented; the timed walk on a real device is measured during the Fix 17 sweep. |
| 19 — Independent review policy | DONE on `main` (PR #33, WP1). |
| 20 — Branching strategy | DONE on `main` (PR #33, WP1). |

## 2. What the codebase now guarantees

Guarantees below are structurally enforced (CI, tests, code), not
just documented:

- Every PR to `main` runs strict CI: lint zero-warning, jest with
  no `--passWithNoTests`, pytest, alembic upgrade + drift check,
  alembic round-trip, authorization + privacy regression, secret
  scan, pip-audit strict (HIGH/CRITICAL, ignore-list documented in
  the workflow file), yarn audit (HIGH/CRITICAL).
- Every PR is squash-merged after one independent human review;
  `main` is linear; the branch-protection setup is documented for
  the owner to enable in GitHub Settings.
- Migrations 0001–0008 are frozen; corrections are new numbered
  migrations; enforcement is `alembic-round-trip` in CI.
- Media storage refuses to boot with `MEDIA_STORAGE_BACKEND=local`
  when `APP_ENV=production` unless an explicit escape hatch is
  set.
- Media writes go through a MIME-sniffing validator with a size
  ceiling; S3 writes carry a server-side-encryption header;
  presigned GET URLs are clamped at `[60, 900]` seconds.
- Historical V1 image prefixes are removable by an idempotent,
  dry-run-default cleanup script.
- Safety classification is deterministic and additive; a model
  second-opinion can never argue us out of a blocker.
- Every ingredient warning rule has an evidence row in
  `INGREDIENT_COVERAGE.md`; a rule cannot fire in production
  without it.
- Every outbound integration is registered in
  `LIVE_INTEGRATIONS.md` with least-privilege scope and provider
  contact.
- Every emitted analytics event has a governance row.

## 3. What is still not production-ready

Honest list of what a public paid launch would still need:

- **Fix 17 (this WP)** — at least one clean P0 device sweep, and
  every P0 finding either fixed or triaged.
- **Fix 6 (calendar)** — Google Calendar integration ships when a
  future work-package adds the OAuth client and the frontend
  connect flow. Manual entries work today.
- **Fix 8 (monitoring live run)** — owner dispatches the
  live-monitoring workflow with real Sentry secrets and records
  the JSON report.
- **Payment mechanics** — completely deferred to a later phase.
  `SUBSCRIPTIONS_AVAILABLE=false`. Razorpay integration exists in
  code but is dormant. This is not a defect; it is the scope the
  brief set.

Nothing else is a public-launch blocker on the non-payment surface
after WP6 closes with a passing sweep.

## 4. Truthful conclusion

- **Private beta.** After WP6 closes with at least one clean P0
  sweep, a controlled private beta on the current `main` is
  defensible. The invite gate, the consent-gated AI paths, and
  the crash-only monitoring do the load-bearing work.
- **Public paid launch.** Not yet. That crossing requires the
  payment work-package that opens after this phase closes, plus
  the calendar integration and the observability live-run recorded.

The claim "GlamGenius is production-ready" is not made anywhere in
this repository, and does not become true when this branch
merges. It becomes true when the sweep, the payment work-package,
and the observability live-run all pass — and the reviewer records
that outcome here in a subsequent PR.

## 5. Owner actions to close the phase

1. Push this branch, open WP6 PR.
2. Walk the two P0 devices in
   [`docs/product/DEVICE_MATRIX.md`](../product/DEVICE_MATRIX.md)
   through
   [`docs/product/DEVICE_SWEEP_PROTOCOL.md`](../product/DEVICE_SWEEP_PROTOCOL.md).
3. For each device, fill in
   [`docs/product/DEVICE_SWEEP_RESULTS_TEMPLATE.md`](../product/DEVICE_SWEEP_RESULTS_TEMPLATE.md)
   under `docs/product/device-sweeps/<date>-<slug>.md` and attach
   the zipped screenshots.
4. Open follow-up fix PRs for any blocker findings; land them
   before the WP6 PR merges.
5. Update this document's §1 Fix 17 row to DONE and §4 truthful
   conclusion to reflect the sweep evidence.
6. Merge.

## 6. Cross-references

- [`STABILISATION_REPORT.md`](../reports/STABILISATION_REPORT.md)
- [`docs/product/DEVICE_SWEEP_PROTOCOL.md`](../product/DEVICE_SWEEP_PROTOCOL.md)
- [`docs/product/DEVICE_MATRIX.md`](../product/DEVICE_MATRIX.md)
- [`docs/product/DEVICE_SWEEP_RESULTS_TEMPLATE.md`](../product/DEVICE_SWEEP_RESULTS_TEMPLATE.md)
- [`docs/engineering/CHECKLIST_MOBILE_UX.md`](../engineering/CHECKLIST_MOBILE_UX.md)
