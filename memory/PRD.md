# GlamGenius — PRD (as of Package B/C/D)

## Original problem

Complete the remaining Supabase hardening for GlamGenius on branch
`fix/finish-supabase-hardening-bcd`, preserving Package A (invite
reservation, canonical Supabase UUID identity, RS256/JWKS, payment sweep,
MongoDB removal). Deliver: complete privacy export, durable
account-deletion state machine, Supabase Storage hardening + S3 removal,
restored regression coverage, deterministic critical journey, versioned
reference-data seed, native mobile validation, complete blocking CI, and
final documentation + PR.

Baseline SHA: `643568d939e28a65254c69f45d441367b3ccaed7`.

## Architecture

```
Expo Android/iOS → Supabase Auth → FastAPI V2 → Supabase Postgres + Storage
```

- Supabase Auth is the sole identity provider.
- Account primary key = Supabase Auth UUID.
- V2 routes only; no V1 identity bridge, no payment code.
- Supabase Storage is the only production media backend; local backend
  restricted to tests and non-production `APP_ENV`.

## User personas

- **Beta invitee** — receives an invite, redeems it, gets an account.
- **Registered beta user** — full seven-category inventory, styling,
  routines, planning, progress, memory, privacy export/deletion.
- **Admin** — creates invites, reads reservation stats.

## Core requirements (static)

- Invite-only access.
- Seven inventory categories: wardrobe, shoes, accessories, beauty shelf,
  hair shelf, perfumes, supplements.
- Privacy export must include every account-owned domain.
- Account deletion must be durable, retryable, and delete Supabase Auth
  identity **last**.
- No payment / subscription / billing / paywall / event pass.
- No MongoDB / Motor / PyMongo.

## What's implemented in this PR (Package B/C/D)

Delivered on `fix/finish-supabase-hardening-bcd` on 2026-02-15:

### Backend
- Privacy export service (`app/domains/privacy/export.py`) + registry
  (`app/domains/privacy/__init__.py`) covering 12 domain groups + all
  seven inventory categories.
- Account-deletion state machine (`app/domains/privacy/models.py`,
  `deletion_service.py`, worker at `app/workers/account_deletion.py`) with
  9 states + lease-based concurrency + retry.
- New Alembic revision `0003_account_deletion_jobs`.
- New privacy routes: `GET /api/v2/privacy/export`,
  `DELETE /api/v2/privacy/account`,
  `GET /api/v2/privacy/account-deletion`,
  `POST /api/v2/privacy/account-deletion/cancel`.
- Storage hardening: typed exceptions (Missing / Unauthorized / Timeout /
  Unavailable / Misconfigured / InvalidResponse), signed URL TTL clamp,
  `list_prefix` + `delete_prefix`, S3 adapter and `boto3` removed.
- Reference-data bootstrap (`app/bootstrap/__init__.py`,
  `python -m app.bootstrap.reference_data`) — seven inventory categories,
  ingredients + aliases + compatibility rules, progress metrics +
  milestones, feature-flag defaults.
- Register-route fix so `InviteRedemption.account_id` FK is satisfied
  (account row created before consuming the reservation).

### Tests
- `test_privacy_export.py` (7), `test_privacy_api.py` (4),
  `test_account_deletion_state_machine.py` (9),
  `test_storage_hardening.py` (13), `test_no_s3_boto3.py` (5),
  `test_reference_data_seed.py` (7), `test_critical_journey.py` (1).
- Updated Package A tests to reflect the two-step reserve+register flow
  and the intentional 404 (not 403) admin surface.
- Rate-limiter reset fixture to prevent inter-test 429 pollution.
- **Local suite: 152 passed, 0 failed.**

### CI
- `mobile-android-bundle` job (blocking) — verifies the Expo shell
  compiles for Android via Metro export.
- Backend job now runs `python -m app.bootstrap.reference_data` twice to
  prove idempotency.
- `expo-export` renamed to "shared-code smoke test" so it can no longer
  be mistaken for mobile validation.

### Documentation
- New: `docs/stabilisation/SUPABASE_HARDENING_PACKAGES_BCD.md` — full
  per-item mapping of the task specification to the code.
- New: `docs/stabilisation/PR_BODY.md` — pre-filled PR description with
  the owner-action mobile checklist.
- Updated: `SUPABASE_HARDENING_REPORT.md` (PARTIAL → DONE for
  §5, §7, §8, §9, §11, §12).
- Updated: `SUPABASE_CUTOVER_REPORT.md`, `SUPABASE_TARGET_ARCHITECTURE.md`,
  `SUPABASE_HARDENING_PACKAGE_A.md` — remove active-source "Prompt 2"
  references.

## Backlog (owner actions before merge)

- `eas build --platform android --profile preview` → attach the APK URL
  and walk the on-device checklist in the PR description.
- iOS validation OR the truthful "iOS native E2E not available" line.
- Fill in the CI run URL, head SHA and job statuses in the PR description
  after the last CI run.
- Human reviewer sign-off. **Do not auto-merge.**

## Next tasks (post-merge)

- Add scheduled live smokes for Gemini and Supabase Storage (owner
  provisions the sandbox project).
- Custom Supabase password-reset email template + SMTP setup.
- Domain-specific regression coverage on top of the critical journey
  (styling, planning, routines, progress, memory) as time allows.
