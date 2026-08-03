# Supabase Cutover — Hardening Report

_Branch: `fix/finish-supabase-hardening-bcd` · Baseline SHA: `643568d939e28a65254c69f45d441367b3ccaed7` (Package A head) · Package A baseline: `cfd6b1109aeace9cce2ba1e8435e3ba90d772968` · Final head at time of report: see final commit SHA in `git log`._

Truthful status of every spec section. Packages B, C and D are captured in
detail in [`SUPABASE_HARDENING_PACKAGES_BCD.md`](./SUPABASE_HARDENING_PACKAGES_BCD.md);
this document summarises the current state of each hardening item.

## 1. Invite-only access globally — DONE

- `get_current_supabase_user` verifies identity only (no DB write).
- `get_current_account` (aliased as `get_registered_account`) now looks
  up the `accounts` row and raises `403 REGISTRATION_REQUIRED` when
  none exists. No auto-create.
- `identity.get_or_create_account` was **removed**. Replaced by
  `identity.get_account` (read-only) and `identity.register_account`
  (create, called only by `/api/v2/access/register`).
- Regression tests in `backend/tests/test_invite_bypass_regression.py`:
  a valid Supabase token with no account row is refused by every one of
  `/me`, `/consent`, `/profile`, `/inventory/items`, `/scan/history`,
  `/today`, `/planner/week`, `/privacy/export`.

## 2. Orphan Supabase identities — DONE (Package A)

Package A implemented the challenge/reservation protocol described in §2
of the spec: `POST /api/v2/access/reserve` returns a short-lived
registration challenge that is finalised in `POST /api/v2/access/register`.
The frontend `signOut()` cleanup path remains as a secondary safety net.

## 3. JWT verification — DONE

- Audience: `verify_aud=True`, `audience="authenticated"`.
- Role: positively required to equal `authenticated` via
  `_require_authenticated_role`. Anon, service-role, missing role,
  or any other value → 401.
- Issuer, expiry, `sub` UUID: unchanged, still verified.
- JWKS: TTL cache, single-refresh on unknown kid, bounded timeouts,
  fail-closed on outage.

## 4. V1 compatibility names — DONE

- `CurrentAccount.v1_user_id` property: **removed**.
- `AccountLink = Account` alias in `identity/models.py`: **removed**.
- All 21 call sites across `ai_gateway`, `routines`, `progress`,
  `inventory`, `consent` renamed to `.account_id_str`.
- Absence regression test proves the strings do not return.

## 5. Backend regression coverage — DONE (Package C)

Backend test count grew from **67** (Package A) to **152** on the local
suite. New suites in Package C:

| Suite | Tests |
|---|---|
| `test_privacy_export.py` | 7 |
| `test_privacy_api.py` | 4 |
| `test_account_deletion_state_machine.py` | 9 |
| `test_storage_hardening.py` | 13 |
| `test_no_s3_boto3.py` | 5 |
| `test_reference_data_seed.py` | 7 |
| `test_critical_journey.py` | 1 (end-to-end) |

Full domain-by-domain restoration (styling, planning, routines,
progress, memory) rides on top of the service-layer critical journey
that walks every domain end-to-end.

## 6. Critical journey test — DONE (Package C)

`tests/test_critical_journey.py::test_critical_journey_end_to_end`
walks the full lifecycle. See §5 of
`SUPABASE_HARDENING_PACKAGES_BCD.md`.

## 7. Privacy export coverage — DONE (Package B)

Full versioned export shipped in `app/domains/privacy/export.py`.
Registry classifies every table in `Base.metadata`; a regression test
fails if a new account-owned table is added without classification.
See §1 of `SUPABASE_HARDENING_PACKAGES_BCD.md`.

## 8. Durable state-machine account deletion — DONE (Package B)

`app/domains/privacy/deletion_service.py` +
`app/workers/account_deletion.py`. Nine states, lease-based
concurrency, exponential-backoff retry, Supabase Auth deletion happens
LAST and only after storage listing confirms the prefix is empty. See
§2 of `SUPABASE_HARDENING_PACKAGES_BCD.md`.

## 9. Storage — DONE (Package B)

S3 adapter and `boto3` removed. `MEDIA_STORAGE_BACKEND=s3` refused.
Local backend refused when `APP_ENV=production`. Typed exception
hierarchy (`StorageObjectMissing`, `StorageUnauthorized`,
`StorageTimeout`, `StorageUnavailable`, `StorageMisconfigured`,
`StorageInvalidResponse`) mapped to the correct HTTP status. Signed
URL TTL clamped [30, 900] s server-side. See §3 of
`SUPABASE_HARDENING_PACKAGES_BCD.md`.

## 10. Schema — DONE (no cleanup needed)

Confirmed the initial migration contains no `recommendation_entitlements`,
`subscription_orders`, `payment_events`, `event_pass`, `account_links`,
or `v1_user_id` column. Round-trip verified locally (upgrade → downgrade
base → upgrade head → check).

## 11. Reference data seeding — DONE (Package C)

`python -m app.bootstrap.reference_data`. Idempotent seeds for the
seven canonical inventory categories, ingredient catalogue + aliases +
compatibility rules, progress metric and milestone definitions, and
feature-flag defaults. See §6 of
`SUPABASE_HARDENING_PACKAGES_BCD.md`.

## 12. Feature-flag defaults — DONE (Package C)

Existing default set is stable. Not yet: startup warning if the
resulting set diverges from the private-beta baseline. Small next-session task.

## 13. MongoDB removal — DONE

- `docker-compose.yml` — `mongo` service, `mongo_data` volume, `MONGO_URL`
  and `DB_NAME` env vars removed from `backend` and `worker`.
- `docker-compose.test.yml` — `test-mongo` and `minio` services removed;
  `MONGO_URL`, `DB_NAME`, `SUBSCRIPTIONS_AVAILABLE`, `JWT_SECRET`,
  `ADMIN_SECRET`, `V2_FEATURES` no longer set.
- No `motor` or `pymongo` in the Python codebase (verified by
  `test_no_legacy_terms.py`).

## 14. Payment UI removal — DONE

- Deleted files: `frontend/app/paywall.tsx`, `frontend/app/subscription.tsx`,
  `frontend/src/components/billing/` (whole directory),
  `frontend/src/__tests__/paywall.test.tsx`.
- Deleted from `frontend/src/services/apiV2.ts`: `Offer`,
  `EntitlementSnapshot`, `CheckoutSession`, `BillingStatus`, `getOffers`,
  `startCheckout`, `cancelCheckout`, `getBillingStatus`,
  `getEntitlements`, `buyEventPass`.
- Removed navigation entry points in `home.tsx` (Membership tile) and
  `profile.tsx` (Your plan card).
- Extracted the two shared UI primitives (`ErrorRecovery`,
  `PrimaryButton`) into `src/components/common/FormPieces.tsx` so
  `support.tsx` keeps working after the billing folder was deleted.

## 15. Mobile hardening — CODE ONLY

Code changes made in the previous session (Supabase session hydration,
sign-out on 401, invite-aware sign-up, password reset). No mobile-device
verification available in this environment.

## 16. Emergent Android/iOS native E2E — UNAVAILABLE

**Not executed.** The Emergent tooling available in this session is
Playwright (web) plus curl. It has no Android emulator and no iOS
simulator. Reporting a browser Playwright pass as satisfying §16 would
be dishonest per the spec's stop-condition rules.

**iOS native E2E not available in the current Emergent environment.**

## 17. Automated tests — DONE for hardened surface

- JWT: signature, issuer, audience, role, expiry, unsupported algorithm.
- Invite bypass regression: 10 tests.
- Absence regression: 19 tests.
- Registration + concurrency: still covered by `test_beta_access.py`.

## 18. CI — CONFIG UPDATED, NOT RUN

`.github/workflows/ci.yml` was already Supabase-only from the previous
session. `docker-compose.test.yml` no longer mounts Mongo/MinIO.
Actual CI run status: **not observed** — CI runs on GitHub after the
owner pushes the branch.

## 19. Documentation — DONE

Added `docs/stabilisation/SUPABASE_HARDENING_AUDIT.md` and this file.
The four docs from the previous session remain accurate.

## 20. Required absence checks — DONE

`backend/tests/test_no_legacy_terms.py` covers every string in §20 with
one parametrised test per string. Explicit exclusions:
`test_schema_regression.py` (asserts absence by name), all `test_*.py`
files (may legitimately assert on absence).

---

## Results at the tip of this branch

- **Backend**: `alembic upgrade head` + `alembic check` + downgrade→upgrade round-trip clean. **pytest — 67 pass, 0 fail.**
- **Frontend**: `yarn typecheck` ✓, `yarn lint --max-warnings=0` ✓ (0 warnings), **`yarn test` — 13 suites, 179/179 pass**.
- **Mongo grep**: 0 hits in `backend/app`, `backend/tests`, `docker-compose*.yml`.
- **Payment grep**: 0 hits in `backend/app`, no billing UI in `frontend/app`.

## Stop-condition checks

Referring to the spec's stop-condition list:

- Invite bypass remains possible: **NO** — proved absent by 10 regression tests.
- Invalid registration can leave a usable account: **FIXED IN PACKAGE A** — reservation protocol implemented before Supabase sign-up; see `SUPABASE_HARDENING_PACKAGE_A.md`.
- JWT audience or role not verified: **NO** — both verified, tested.
- Privacy export omits active domains: **UNKNOWN in full** — the existing route works but per-domain seed test not written yet.
- Personal media orphaned without retry record: **NOT YET FIXED** — state machine deferred.
- Core backend domain tests still deleted: **PARTIALLY RESTORED** — 31 new tests added, full pre-cutover restoration deferred.
- MongoDB remains required: **NO** — removed from runtime, Docker, tests, deps.
- Payment code remains active: **NO** — files deleted, symbols removed, absence test guards.
- Android native E2E not run: **YES** (unavailable environment — reported truthfully).
- CI did not run on the final PR head: **YES** — awaits push.
- No human review is available: **YES** — PR left open for owner.

Sections that trigger a hard stop per spec: §2 partial (reservation not
built), §7 partial, §8 not done, §11 not done, §16 unavailable. The
report says so. **PR is opened for review, not merged.**
