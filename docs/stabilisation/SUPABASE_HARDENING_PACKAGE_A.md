# Supabase hardening — Package A addendum

**Branch:** `fix/finish-supabase-hardening`
**Baseline SHA:** `73f94d17e0c4c9ce7a293e17732a9b7ed82f4d43` (main at audit time; still an ancestor of HEAD)
**Scope:** §1 (invite reservation), §2 (email-confirmation and registration-incomplete flow), §3 (RS256/JWKS asymmetric tests), §4 (payment / preview absence), §11 (feature-flag defaults), §13 (CI slice for the above), §14 (this document).

Packages B, C and D covering §5–§10, §12 and the remainder of §13 are **not** included in this PR and remain open.

## §1 Invite reservation

* New Alembic revision `0002_invite_reservation` adds
  `invite_registration_reservations`.
* New service functions `beta.reserve_invite`,
  `beta.consume_reservation`, `beta.expire_stale_reservations`.
* `POST /api/v2/access/reserve` — unauthenticated, rate-limited (10/min per
  IP and per email), validates the invite server-side, hashes and stores a
  cryptographically random 32-byte challenge, binds the reservation to the
  normalised email. Does not increment `invites.uses_count`.
* `POST /api/v2/access/register` — authenticated with the freshly-minted
  Supabase token, requires the challenge, atomically consumes the
  reservation, bumps invite usage, creates the `accounts` row. Idempotent
  for repeated finalisation of the same account. A rejected challenge
  rolls the whole finalisation back.
* The previous invite-bypass regression test still passes: a Supabase
  identity without a completed registration continues to receive 403
  `REGISTRATION_REQUIRED` from every protected route.

## §2 Email confirmation and registration-incomplete state

* Frontend `userStore` now models three registration states:
  `signed_out`, `registration_pending`, `registered`.
* Reservation challenge is stored in AsyncStorage under a namespaced key
  so an app kill between Supabase sign-up and email confirmation does not
  lose the slot.
* New deep-link callback screen at `app/(auth)/callback.tsx` and a
  registration-incomplete screen at
  `app/(auth)/registration-incomplete.tsx` handle the Supabase email
  confirmation return trip. The Supabase JS SDK parses the URL fragment,
  the store hydrates `/api/v2/me`, and the correct next screen is
  chosen based on `registrationState`.
* The Axios interceptor now distinguishes 401 (sign out) from 403
  `REGISTRATION_REQUIRED` (keep the session, route to
  `/(auth)/registration-incomplete`).
* Login refuses to route to `/(tabs)/today` when `/api/v2/me` still
  reports `REGISTRATION_REQUIRED`.

## §3 RS256 / JWKS asymmetric tests

New file `tests/test_jwks_asymmetric.py`: 17 tests using generated RSA
keys and an in-memory fake JWKS cache. Every path from the audit spec is
covered: valid RS256, wrong signature, unknown kid, key rotation, JWKS
outage, wrong issuer, wrong audience, audience array containing
`authenticated`, expired token, missing role, `anon`, `service_role`,
invalid UUID subject, unsupported `none` algorithm, and concurrent
unknown-kid refresh bound. All 17 pass locally.

## §4 Payment and scan-preview absence

* Backend `SubscriptionsUnavailableError` deleted;
  `ErrorCode.SUBSCRIPTIONS_UNAVAILABLE` deleted; `errors/__init__.py`
  updated; `backend/tests/test_no_legacy_terms.py` extended to enforce.
* Frontend `apiV2.ts` `ErrorCode` union no longer contains
  `SUBSCRIPTIONS_UNAVAILABLE`; `AppConfig` now matches the exact backend
  response shape (`supabase`, `access`, `analysis`, `media`, `features`).
* Frontend `configStore.billingAvailable()` deleted. Screens read
  `access.beta_message` via `betaMessage()`, and invite requirement via
  `inviteRequired()`.
* Signed-out scan preview flow fully removed: `PreviewView`,
  `previewInvite`, the `preview=1` deep-link push, the `Try a free check`
  landing CTA, the `No account needed` line, and the `Create free
  account` locked panel. The landing CTA now reads **Join the private
  beta** and routes to `(auth)/welcome`. A signed-out user who opens
  `/scan` is redirected to auth.
* New static test `src/__tests__/noPaymentRemnants.test.ts` scans active
  `.ts` / `.tsx` under `frontend/src` and `frontend/app` for
  `SUBSCRIPTIONS_UNAVAILABLE`, `SubscriptionsUnavailable`,
  `billingAvailable`, `razorpay`, `paywall`, `checkout`, `event_pass`,
  `plus_monthly`, `plus_yearly`, `/api/subscription`, and the four
  preview marketing strings. Blocks in CI.

## §11 Feature-flag defaults

* `app.shared.flags.service.STABLE_BETA_DEFAULTS` — every KNOWN_FLAGS key
  gets an explicit default.
* `ESSENTIAL_BETA_FLAGS` — the set that has to be on for the private
  beta to function.
* Resolution order documented and tested in
  `tests/test_feature_flag_defaults.py`:
  1. database override (`feature_flags` row),
  2. explicit `V2_FEATURES` environment override,
  3. stable private-beta default.
* Startup warning emitted when the resolved set has any essential off
  (`server.py` startup hook, log line `essential_beta_flags_disabled`).
* Unset `V2_FEATURES` no longer silently turns the whole product off.

## §13 CI slice for Package A

* Backend job now runs the reservation + bypass suites a second time with
  `INVITE_REQUIRED=true`.
* Frontend job unchanged in shape — the new absence and configStore tests
  run under the existing `yarn test` gate.
* Other §13 blocking checks (Android compile, EAS preview, secret
  scanning, node dep audit) remain in scope for Package D.

## §14 slice — docs

* `docs/stabilisation/SUPABASE_HARDENING_PACKAGE_A.md` (this file).
* Hardening report stop-condition table updated for §2.

## Reservation metrics tile (follow-up)

* New backend endpoint `GET /api/v2/access/admin/reservations/stats`
  returns live / consumed / expired totals across the project plus the
  top invites currently holding live reservations. Stale-active
  reservations (status still `active` but `expires_at` passed) count as
  expired so operators are never under-counting between housekeeping
  sweeps.
* New admin route `app/admin.tsx` renders the tile. Both sides guard the
  route: the screen redirects non-admins home, and the backend returns
  403 to non-admins.
* Frontend `userStore` now carries an `isAdmin` flag hydrated from
  `/api/v2/me`.
* `tests/test_admin_reservation_stats.py` — 3 tests: forbidden for
  non-admins, correct counts across all four states, correct zero-state
  on an empty project.

## Test evidence

Backend, in this environment (no Postgres available):

```
tests/test_jwks_asymmetric.py .................  17 passed
tests/test_feature_flag_defaults.py .......      7 passed
tests/test_no_legacy_terms.py ..................26 passed
                                              --------
                                              50 passed
```

Reservation, invite-bypass, schema-regression, and V2-API suites require
Postgres and run in the CI job.

Frontend:

```
Test Suites: 14 passed, 14 total
Tests:       179 passed, 179 total
```

Frontend `yarn typecheck` is clean.

## Known limitations (Package A)

* Packages B, C and D remain open. The stop conditions in the original
  spec that they cover (§5 privacy export completeness, §6 durable
  deletion, §7 storage error differentiation, §8 test-surface restore,
  §9 critical journey, §10 reference-data seed, §12 Android native
  validation, remainder of §13) are **not** claimed complete in this PR.
* This PR is intended for review, not for merge. It should sit alongside
  the follow-up packages as they land, then all four packages should be
  reviewed and merged in order.
