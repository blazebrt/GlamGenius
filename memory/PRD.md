# GlamGenius — Supabase hardening PR (Package A)

## Current focus

Complete the residual Supabase hardening work described in the
14-section brief. Branch `fix/finish-supabase-hardening` off
`73f94d17e0c4c9ce7a293e17732a9b7ed82f4d43` (an ancestor of `main`) has
been prepared with **Package A** committed locally, not pushed. Packages
B, C, D remain open.

## What is in Package A (this branch, committed at 8eeab70)

- **§1 Invite reservation** — new endpoint `POST /api/v2/access/reserve`,
  new model `invite_registration_reservations`, atomic
  `beta.consume_reservation`, migration `0002_invite_reservation`.
- **§2 Registration state** — frontend `registrationState` machine,
  `(auth)/callback` and `(auth)/registration-incomplete` screens,
  Axios 401-vs-403-REGISTRATION_REQUIRED interceptor.
- **§3 RS256 / JWKS** — `tests/test_jwks_asymmetric.py`, 17 tests over
  generated RSA keys.
- **§4 Payment / preview absence** — `SubscriptionsUnavailableError`,
  `ErrorCode.SUBSCRIPTIONS_UNAVAILABLE`, `billingAvailable()`,
  `PreviewView`, `previewInvite`, the `Try a free check` landing CTA and
  the `Create free account` locked panel all removed. New
  `noPaymentRemnants.test.ts` guards.
- **§11 Feature-flag defaults** — `STABLE_BETA_DEFAULTS`,
  `ESSENTIAL_BETA_FLAGS`, startup warning when essentials off.
- **§13 slice** — CI runs invite-reservation + bypass suites a second
  time with `INVITE_REQUIRED=true`.
- **§14 slice** — `docs/stabilisation/SUPABASE_HARDENING_PACKAGE_A.md`.

## Test evidence (this environment, no Postgres)

- Backend: 50/50 pass across `test_jwks_asymmetric.py`,
  `test_feature_flag_defaults.py`, `test_no_legacy_terms.py`.
- Reservation and invite-bypass suites require Postgres — CI runs them.
- Frontend: **179/179** jest tests pass, `yarn typecheck` clean.

## Backlog (not in this PR)

- **Package B** (§5 privacy export, §6 durable deletion state machine,
  §7 storage error differentiation + remove boto3).
- **Package C** (§8 restore ~30 backend test suites, §9 deterministic
  critical journey test, §10 reference-data seed).
- **Package D** (§12 Android native validation via EAS build, iOS if
  simulator available, final report; residual §13 items —
  Android compile job, secret scanning, dep audits).

## Next tasks

1. Owner clicks **Save to GitHub** in the Emergent chat input to push
   `fix/finish-supabase-hardening` to `origin`.
2. Owner opens the PR on
   [github.com/blazebrt/GlamGenius](https://github.com/blazebrt/GlamGenius)
   titled `fix: finish Supabase hardening and mobile validation`.
3. Owner waits for CI to run and pastes the CI URL back so the next
   session can begin Package B on a fresh branch off this one, or on a
   new baseline once this PR merges.
4. Do NOT tick auto-merge. Independent human review required.

## Constraints held throughout

- MongoDB, V1 routes, custom JWTs, `account_links`, `v1_user_id`,
  Razorpay, subscription UI: not reintroduced.
- Direct pushes to `main`: none.
- Payment or billing behaviour: not touched.

## Non-goals in this PR

- Live Supabase project changes (no seed rows, no bucket creation).
- Real Gemini calls.
- Android emulator / EAS Build (Package D).
