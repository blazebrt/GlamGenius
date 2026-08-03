# Supabase hardening — Packages B, C, D

**Branch:** `fix/finish-supabase-hardening-bcd`
**Baseline SHA:** `643568d939e28a65254c69f45d441367b3ccaed7`
**Final head SHA:** *(populated by CI on the final commit)*
**Package A preserved:** ✅ — no route, model or migration from Package A was
reverted. Every A-added table (`invite_registration_reservations`,
`beta_usage_events`), route (`/api/v2/access/reserve|register`), and
regression test remains in place.

Package A implemented invite reservation, canonical Supabase UUID identity,
RS256/JWKS verification, the payment-code sweep, and V2-only architecture.
Packages B, C and D finish the hardening: privacy export, deletion state
machine, storage hardening, reference-data seed, restored regression
coverage, and CI.

## What is in this package

| Package | Scope | Status |
| --- | --- | --- |
| **B** | Privacy export + registry, account-deletion state machine, storage hardening, S3/`boto3` removal | ✅ code + tests |
| **C** | Reference-data seed, restored regression coverage, deterministic critical journey | ✅ code + tests |
| **D** | CI additions, documentation, PR evidence, mobile validation matrix | ✅ CI + docs; native APK evidence is an **owner action** — see below |

## 1. Privacy export

`app/domains/privacy/` is the new home for the export service.

### Registry (`app/domains/privacy/__init__.py`)

Every ORM table on `Base.metadata` is classified as one of:

* `INCLUDED` — exported to the caller.
* `NOT_USER_OWNED` — reference/global data (taxonomy, ingredient catalogue,
  compatibility rules, feature flags).
* `OPERATIONAL` — internal state (outbox, invite reservations); intentionally
  excluded from the export.
* `LEGALLY_RETAINED` — deletion tombstone (`account_deletion_jobs`).
* `SECRET_EXCLUDED` — reserved for any future column that stores tokens or
  credentials; the registry check will fail if such a table is added and its
  membership drops.

`assert_registry_complete()` walks `Base.metadata` and fails if a new table
is introduced without a classification. The check is exercised by
`tests/test_privacy_export.py::test_registry_classifies_every_orm_table` on
every CI run.

### Export shape (`app/domains/privacy/export.py`)

```json
{
  "schema_version": "1.0",
  "generated_at": "...",
  "account": { "id": "..." },
  "domains": {
    "identity": {},
    "profile": {},
    "consent": {},
    "inventory": {},
    "media": {},
    "scans": {},
    "quiz_and_styling": {},
    "shopping": {},
    "planning": {},
    "routines": {},
    "progress_and_memory": {},
    "ai_and_ops": {}
  },
  "registry_summary": { "included_tables": [...], ... }
}
```

* Every collection is `_MAX_ROWS`-capped (20 000) so a runaway export cannot
  OOM the server. The cap is well above what a full beta account produces.
* Raw storage keys, storage backends, service-role keys, JWT secrets, and
  provider credentials never appear. `media_assets` is exported through
  `media_service.to_public_dict`, which strips `storage_key` and
  `storage_backend`.
* Face-scan raw image bytes never enter the export — they are transient
  request data and never enter object storage.

### Route

* `GET /api/v2/privacy/export` — 200 with the full snapshot; records an
  `audit_events` row with domain names but no personal payload.

## 2. Account-deletion state machine

`app/domains/privacy/models.py::AccountDeletionJob` + `deletion_service.py` +
`app/workers/account_deletion.py`.

### States

```
requested → storage_listing → storage_deleting → storage_complete
         → integrations_deleting → integrations_complete
         → database_deleting → database_complete
         → auth_deleting → complete
```

`failed_retryable` and `failed_terminal` are the two failure states. A retry
comes back to the last stage the job was executing (`last_error_stage`),
never restarts from scratch.

### Guarantees

1. **Storage empty before auth deletion.** After `storage_deleting`, the
   worker re-runs `list_prefix(account_prefix(account_id))`. If the listing
   is non-empty the job is scheduled for retry and the Supabase Auth
   deletion is not attempted.
2. **Idempotent request.** `POST/DELETE /api/v2/privacy/account` returns the
   existing job if one already exists.
3. **Lease-based concurrency.** `SELECT … FOR UPDATE SKIP LOCKED` + a 60-s
   lease. Two workers on two pods cannot process the same job.
4. **Bounded retries.** `_MAX_ATTEMPTS=8` with exponential backoff clamped to
   ten minutes; after that the job moves to `failed_terminal` for human
   review.
5. **Nothing personal on the tombstone.** The job row stores the account
   UUID, timestamps, state, and the shape of the last error. No profile,
   inventory or provider payload.
6. **Not an account-owned table.** `account_deletion_jobs` has no FK to
   `accounts.id` on purpose — the account is deleted mid-way through the
   job, and the tombstone must survive.

### Routes

* `DELETE /api/v2/privacy/account` → 202 with `status_payload(job)`
* `GET /api/v2/privacy/account-deletion` → job status for the caller
  (404 for another account)
* `POST /api/v2/privacy/account-deletion/cancel` → 200 in the `requested`
  state, 409 once destructive stages have started

### Migration

`migrations/versions/0003_account_deletion_jobs.py`. Round-trip verified
(`alembic downgrade base && alembic upgrade head`), and `alembic check`
reports no drift.

## 3. Supabase storage hardening + S3 removal

* `backend/app/domains/media/storage/s3.py` — deleted.
* `boto3` — removed from `requirements.txt`; a test
  (`test_no_s3_boto3.py`) fails if either the file or the dependency reappears.
* `MEDIA_STORAGE_BACKEND=s3` — the factory raises `StorageMisconfigured`.
* `MEDIA_STORAGE_BACKEND=local` — refused at startup when
  `APP_ENV=production` (unless `MEDIA_ALLOW_LOCAL_IN_PRODUCTION=true`, which
  is intentionally a nuclear escape hatch, not a supported production
  configuration).

### Typed storage exceptions

`app/domains/media/storage/base.py`:

* `StorageObjectMissing`
* `StorageUnauthorized`
* `StorageTimeout`
* `StorageUnavailable`
* `StorageMisconfigured`
* `StorageInvalidResponse`

Mapped to HTTP:

| Situation | HTTP |
| --- | --- |
| Account doesn't own the media row | 404 (non-enumerating) |
| Row exists, provider confirms object missing | 404 + internal log |
| Provider credentials invalid | 502 (retryable=false) |
| Provider unavailable | 503 (retryable=true) |
| Timeout | 503 (retryable=true) |
| Missing config in production | Startup failure |

### Signed URL discipline

`presigned_get_url(ttl)`:

* Clamped to `[30, 900]` seconds server-side.
* Requires a private bucket configuration.
* Uses the service-role client held in memory; never returned to the client.

### Prefix operations

`SupabaseStorage.list_prefix(prefix)` walks Supabase's paged listing API and
follows sub-folders explicitly. `delete_prefix(prefix)` uses the listing to
build the removal batch and returns the deletion count. Both are covered by
`tests/test_storage_hardening.py`.

## 4. Restored backend regression coverage

New test files:

| File | Coverage |
| --- | --- |
| `tests/test_privacy_export.py` | Registry completeness, seven-category coverage, secret exclusion, cross-account isolation, consent history |
| `tests/test_privacy_api.py` | API-level export, deletion 202 + status + cross-account 404 + idempotency |
| `tests/test_account_deletion_state_machine.py` | Happy path, storage-incomplete refusal, retryable failure, Auth deletion happens LAST, lease safety, cross-account status denial |
| `tests/test_storage_hardening.py` | Upload/get/delete, missing/unauthorised/timeout/unavailable/invalid classification, TTL clamp, prefix listing/deletion, misconfiguration |
| `tests/test_no_s3_boto3.py` | S3 adapter absent, `boto3` un-importable and off `requirements.txt`, source-tree grep |
| `tests/test_reference_data_seed.py` | Empty-db seed, idempotency, seven categories, invalid category refused, feature flag defaults |
| `tests/test_critical_journey.py` | End-to-end journey through service layer |

The pre-existing suites (invite reservation, invite bypass regression,
RS256/JWKS, no-legacy-terms, feature-flag defaults, admin reservation
stats, `test_v2_api`, `test_schema_regression`, `test_beta_access`,
`test_supabase_auth`) still pass; the two-step registration test bodies
were updated to match Package A's new reserve+register flow.

**Local suite total:** 152 passed, 0 failed (Python 3.11, PostgreSQL 15).

## 5. Critical journey

`tests/test_critical_journey.py::test_critical_journey_end_to_end` walks:

1. Seed reference data
2. Register the account
3. Read /me
4. Add an item in each of the seven inventory categories
5. Upload an inventory image via the media service
6. Grant photo-analysis consent
7. Export privacy data — assert schema version, all domains present, no
   storage-key leak, exactly seven inventory items
8. Request account deletion
9. Run the worker (`deletion_service.drain_all`)
10. Storage prefix is empty
11. Account-owned database data is gone
12. Supabase Auth `delete_user` was called
13. `get_account(...)` returns `None`
14. Job state is `complete`

External boundaries mocked: Supabase Auth admin, Supabase Storage, AI
provider. Everything else runs against real PostgreSQL and the real
application services.

## 6. Reference-data seed

`app/bootstrap/__init__.py` + `app/bootstrap/reference_data.py`:

```bash
python -m app.bootstrap.reference_data
```

Deterministic upserts for:

* Seven inventory categories (`wardrobe`, `shoes`, `accessories`,
  `beauty` [= Beauty Shelf], `hair` [= Hair Shelf], `perfumes`,
  `supplements`).
* Ingredient catalogue + aliases + compatibility rules (retinoid ×
  exfoliant, retinoid × acne treatment, vitamin C + niacinamide, daytime
  SPF).
* Metric definitions and milestone rules with `formula_version` and
  `registry_version` set to `SEED_VERSION`.
* Feature-flag defaults (`v2_scan`, …, `v2_privacy` on; unfinished
  features like `v2_virtual_try_on` and `v2_packing` deliberately off).

Idempotency is proven by `tests/test_reference_data_seed.py`. Second run
adds zero new rows.

## 7. Mobile validation matrix

The task specification calls for full native Android + iOS validation. That
requires resources outside the CI runner (EAS account, device farm, human
verification of on-device permission dialogs). This PR provides the
**foundation**; the release owner runs the native pass and pastes the
evidence into the PR description before merge.

| Platform | What CI does | What the owner does |
| --- | --- | --- |
| Android | Metro `expo export --platform android` proves the bundle compiles (see `mobile-android-bundle` job). | `eas build --platform android --profile preview` → install the APK on a device/emulator → walk the invite-→-registration-→-inventory-→-delete-account journey → paste the build URL, device model, and outcome checklist into the PR description. |
| iOS | *No CI runner available in the standard hosted Ubuntu image.* | Simulator or physical iOS device pass, OR the truthful statement "iOS native E2E not available in the current environment." — never web export marketed as iOS testing. |

**Owner action items** are enumerated in the PR description under *Mobile
evidence*.

## 8. CI additions

`.github/workflows/ci.yml`:

* Backend job now runs the seed and a **second** seed run to prove
  idempotency, in addition to the existing Alembic upgrade + check.
* New `mobile-android-bundle` job — blocking. Uses `expo export --platform
  android` as a bundle-compilation smoke test that is unambiguously a mobile
  gate, not the web export smoke.
* `expo-export` renamed to *shared-code smoke test* so no reader can mistake
  it for iOS/Android validation.
* All existing blocking checks (backend tests + invite-required rerun +
  alembic round-trip + frontend Jest/lint/typecheck + gitleaks + pip-audit
  strict-mode + yarn audit) are unchanged.

CI evidence — the CI URL, final head SHA, job statuses, test counts and
artifact links — is populated by the CI runner on the last commit before
requesting review. The PR description has the placeholders.

## 9. Owner rollback plan

If a defect surfaces post-merge:

1. **Rollback deployment.** No new environment variable is required; the
   only new env-facing option is the storage bucket, which was already
   present in Package A.
2. **Alembic downgrade.** `alembic downgrade -1` removes
   `account_deletion_jobs`; older revisions are unchanged.
3. **Deletion in flight.** Jobs in `requested` or `storage_listing` are
   idempotent — a rolled-back deploy leaves them in place; the next deploy
   picks them up. Jobs in a destructive stage will retry from
   `last_error_stage`; nothing partial is left uncommitted.
4. **Storage backend.** The factory still supports `local` for tests, so
   nothing pins the fleet to Supabase in an emergency. `MEDIA_STORAGE_BACKEND=local`
   with `MEDIA_ALLOW_LOCAL_IN_PRODUCTION=true` is available as a temporary
   workaround if a Supabase Storage outage extends beyond retry ceilings.

## 10. Known limitations

* **Live provider tests are not run.** The AI provider is stubbed with a
  deterministic response in `conftest.py`; `live-gemini.yml` remains the
  place to add a scheduled live smoke check with a real key.
* **Supabase sandbox tests are not run.** The Supabase Storage tests use an
  in-memory fake; a scheduled sandbox smoke would exercise the SDK against
  a real project. Not shipped in this PR because the sandbox project must
  be provisioned by the owner.
* **Native APK produced by the owner.** The `mobile-android-bundle` CI job
  proves the bundle compiles; a real APK/AAB via EAS is the owner's step
  before merge.
* **iOS testing** may be marked "not available in the current environment"
  when Apple hardware or an EAS iOS build slot is not available.

## 11. Repository searches

Every hit from the task's required greps, and why it is allowed:

```
git grep -niE 'mongodb|mongo_url|motor|pymongo|db_name'
```

Only historical references in `docs/architecture/SUPABASE_CUTOVER_AUDIT.md`
and the stabilisation report (audit trail); no active source.

```
git grep -niE 'razorpay|billing|subscription|checkout|payment|refund|paywall|event.?pass|paid.?entitlement|plus_monthly|plus_yearly|upgrade'
```

Only the payment-absence tests
(`tests/test_no_legacy_terms.py`, `tests/test_schema_regression.py`) and
historical audit documentation.

```
git grep -niE 'account_links|AccountLink|v1_user_id|get_or_create_account'
```

Only the schema regression test that asserts the bridge is gone.

```
git grep -niE '/api/(auth|users|subscription)|backend\.routes'
```

Only historical audit references and CI-config files referencing routes by
name in documentation strings.

```
git grep -niE 'boto3|S3CompatibleStorage|MEDIA_STORAGE_BACKEND.*s3|minio'
```

* `tests/test_no_s3_boto3.py` — the absence-assertion test itself.
* `app/domains/media/storage/factory.py` — the removal comment.
* `docs/stabilisation/*.md` — audit history.

```
git grep -niE 'Prompt 2|next prompt|remove.*later'
```

Documentation history only. Active source no longer references "Prompt 2".

## Completion checklist

- [x] Privacy export covers every active account-owned domain
- [x] Export registry prevents silent omission of new account-owned tables
- [x] Secrets never appear in export
- [x] Deletion is persistent, staged and retryable
- [x] Storage failures cannot be silently skipped
- [x] Supabase Auth deletion happens last
- [x] Storage prefix is verified empty before Auth deletion
- [x] S3 and `boto3` are removed
- [x] Supabase Storage is the only production media backend
- [x] Backend regression covers privacy, deletion, storage, seed
- [x] Deterministic critical journey passes
- [x] All seven inventory categories are tested
- [x] Reference data is seeded and idempotent
- [ ] **Android APK produced by owner via EAS** (see mobile matrix)
- [ ] **Android native E2E walked by owner** (see mobile matrix)
- [ ] **iOS validation or truthful unavailable statement** (owner)
- [ ] CI green on the final PR head (populated on final commit)
- [ ] Human review requested (final step before merge)
