# fix: complete privacy, storage, regression and mobile hardening

## Summary

Packages B, C and D of the Supabase hardening work. Finishes the privacy
export, adds the durable account-deletion state machine, hardens Supabase
Storage and removes the S3/`boto3` adapter, seeds reference data, restores
regression coverage, adds the deterministic critical journey, and updates
CI. Package A (invite reservation, canonical Supabase UUID identity,
RS256/JWKS, payment sweep, MongoDB removal) is fully preserved.

Full narrative and per-item mapping is in
[`docs/stabilisation/SUPABASE_HARDENING_PACKAGES_BCD.md`](docs/stabilisation/SUPABASE_HARDENING_PACKAGES_BCD.md).

**Risk: High** — changes touch privacy, deletion, storage and CI on a
private-beta production surface.

## Baseline

- **Baseline SHA:** `643568d939e28a65254c69f45d441367b3ccaed7`
- **Final head SHA:** *(populated by the CI runner on the final commit)*
- Baseline is preserved as an ancestor of this branch.

## Migration changes

- New Alembic revision `0003_account_deletion_jobs` — adds the
  `account_deletion_jobs` table for the deletion state machine.
- Verified: `alembic upgrade head` → `alembic check` → `alembic downgrade
  base` → `alembic upgrade head` → `alembic check` all clean locally.

## Rollback plan

1. `alembic downgrade -1` removes `account_deletion_jobs`; older revisions
   unchanged.
2. Jobs in `requested` or `storage_listing` are idempotent — a rolled-back
   deploy leaves them; the next deploy resumes.
3. Jobs already in a destructive stage carry `last_error_stage` and resume
   on redeploy; no in-flight state is silently lost.
4. `MEDIA_STORAGE_BACKEND=local` with `MEDIA_ALLOW_LOCAL_IN_PRODUCTION=true`
   remains an emergency workaround for a Supabase Storage outage.

## Privacy design

* `GET /api/v2/privacy/export` — versioned (`schema_version: "1.0"`),
  covers 12 top-level domains (identity, profile, consent, inventory,
  media, scans, quiz_and_styling, shopping, planning, routines,
  progress_and_memory, ai_and_ops).
* Registry classifies every ORM table (`INCLUDED`, `NOT_USER_OWNED`,
  `OPERATIONAL`, `LEGALLY_RETAINED`, `SECRET_EXCLUDED`). A test enumerates
  `Base.metadata` and fails if a new table has no classification.
* `storage_key`, `storage_backend`, service-role keys, JWT secrets and
  raw face-scan bytes never appear.

## Deletion design

* Persistent job in `account_deletion_jobs` with nine advancing states
  (`requested → storage_listing → storage_deleting → storage_complete →
  integrations_deleting → integrations_complete → database_deleting →
  database_complete → auth_deleting → complete`).
* `SELECT … FOR UPDATE SKIP LOCKED` + 60 s lease prevents duplicate workers.
* Exponential-backoff retry, `_MAX_ATTEMPTS=8`, terminal after that.
* Supabase Auth deletion happens **last** and only after storage listing
  confirms the account prefix is empty.
* Cross-account status reads return 404.

## Storage design

* Supabase Storage is the only production media backend.
* S3 adapter + `boto3` removed; `test_no_s3_boto3.py` fails if either
  reappears.
* Typed exceptions (`StorageObjectMissing`, `StorageUnauthorized`,
  `StorageTimeout`, `StorageUnavailable`, `StorageMisconfigured`,
  `StorageInvalidResponse`) mapped to correct HTTP status.
* Signed URL TTL clamped `[30, 900]` s server-side.
* `list_prefix` + `delete_prefix` walk paged listings across nested
  folders.

## Seed design

`python -m app.bootstrap.reference_data` — idempotent upserts for:

* Seven canonical inventory categories.
* Ingredient catalogue + aliases + compatibility rules (retinoid ×
  exfoliant, retinoid × BP, VC + niacinamide, daytime SPF).
* Progress metric definitions + milestone rules (with `formula_version`
  and `registry_version`).
* Feature-flag defaults (private-beta features on, unfinished features
  off).

## Test evidence

**Local suite:** `152 passed, 0 failed` (Python 3.11 + PostgreSQL 15).

| Suite | Count |
| --- | --- |
| `test_privacy_export.py` | 7 |
| `test_privacy_api.py` | 4 |
| `test_account_deletion_state_machine.py` | 9 |
| `test_storage_hardening.py` | 13 |
| `test_no_s3_boto3.py` | 5 |
| `test_reference_data_seed.py` | 7 |
| `test_critical_journey.py` | 1 |
| `test_supabase_auth.py` | 8 |
| `test_beta_access.py` | 7 |
| `test_v2_api.py` | 22+ |
| `test_invite_reservation.py` | 5+ |
| `test_invite_bypass_regression.py` | 10 |
| `test_no_legacy_terms.py` | 19 |
| `test_schema_regression.py` | 4 |
| `test_admin_reservation_stats.py` | 2 |
| `test_feature_flag_defaults.py` | 6 |
| `test_jwks_asymmetric.py` | 5+ |

Frontend: unchanged from Package A (Jest / TypeScript / lint / expo config
/ web export all pass on head).

## Android evidence (owner action)

- [ ] `eas build --platform android --profile preview` — build URL:
- [ ] APK installed on device/emulator — device model / Android version:
- [ ] Startup + Supabase config load — pass/fail:
- [ ] Invite reservation → sign-up → email confirm → deep-link return →
      finalise registration — pass/fail:
- [ ] Force-close / reopen preserves the registered state — pass/fail:
- [ ] Seven-category inventory add + edit + archive — pass/fail:
- [ ] Photo capture / gallery selection consent flow — pass/fail:
- [ ] Privacy export → download JSON — pass/fail:
- [ ] Account deletion → status polling → deleted-identity denial —
      pass/fail:
- [ ] Cross-account isolation with two accounts — pass/fail:
- [ ] Accessibility spot-check (labels, touch targets, large text) —
      pass/fail:

## iOS status (owner action)

Fill in one of:

- [ ] iOS simulator/device pass — device / iOS version / outcome:
- [ ] `iOS native E2E not available in the current environment.`

## CI links

- **CI run URL:** *(populated on final push)*
- **Overall status:** *(populated on final push)*
- Backend tests: —
- Alembic round-trip: —
- Frontend Jest / lint / typecheck: —
- Expo web export smoke: —
- **mobile-android-bundle** (blocking Android bundle check): —
- Secret scan (gitleaks): —
- pip-audit (strict): —
- yarn audit (high+): —

## Known limitations

- Live provider tests are not run in this PR's CI. `live-gemini.yml` and
  `live-monitoring.yml` remain the place to add scheduled live smokes.
- Supabase sandbox tests are not run — the storage tests use an
  in-memory fake. A sandbox smoke run against a real Supabase project is
  an owner action that requires provisioning a sandbox project.
- Native APK production and on-device walkthrough are owner actions (see
  Android evidence checklist above).

## Tests not run

- Live Gemini/AI provider calls (would require a real API key).
- Live Supabase Storage calls (would require a sandbox project).
- Full iOS matrix (requires Apple hardware / EAS iOS slot).

## Reviewer requested

A human reviewer must approve this PR before merge. **Do not auto-merge.**

<!--
When the reviewer is happy, tag them; the PR remains open until a human
signs off and the mobile checklist is filled in.
-->
