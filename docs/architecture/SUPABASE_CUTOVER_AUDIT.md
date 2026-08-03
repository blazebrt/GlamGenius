# Supabase V2 Cutover — Architecture Audit

Branch: `architecture/supabase-v2-cutover`
Baseline: `ac4822d3de04e283b3869bc8947e8a7710e99404` (main)

This is the mandatory pre-implementation audit for the Supabase cutover. It
inventories every dependency the current codebase has on the V1 identity
foundation (MongoDB + local JWT + `account_links` bridge + payment stack) and
classifies each one against the target architecture:

```
Expo → Supabase Auth → FastAPI V2 → Supabase PostgreSQL → Supabase Storage
```

## Legend

| Class | Meaning                                                        |
|-------|----------------------------------------------------------------|
| **R** | **Replace** in this PR                                         |
| **T** | **Temporarily retained** only because a follow-up PR removes it |
| **M** | **Move** non-payment functionality into the new architecture   |
| **O** | **Obsolete** — safe to remove                                  |
| **B** | **Blocked** — with a documented reason                         |

---

## 1. Authentication & Identity

| Item                                     | Class | Notes                                                                             |
|------------------------------------------|-------|-----------------------------------------------------------------------------------|
| `backend/security.py` (V1 auth module)   | R     | Whole module replaced by `app/shared/security/supabase_auth.py`.                  |
| `security.get_current_user`              | R     | Removed. `Depends(get_current_supabase_user)` replaces it in V2 routes.           |
| Local `bcrypt` password DB               | R     | Deleted. Supabase Auth owns passwords.                                            |
| Legacy SHA-256 password shim             | R     | Deleted. No V1 users to migrate.                                                  |
| JWT signing with `JWT_SECRET` env        | R     | Replaced by verified Supabase JWT (JWKS + optional HS256 fallback).               |
| `app/shared/security/deps.CurrentAccount`| R     | Rebuilt so `account_id` **is** the Supabase UUID directly.                        |
| `AccountLink` model + `account_links` FK | R     | Renamed to `accounts`. PK becomes the Supabase Auth user UUID directly.           |
| `v1_user_id` field on `AccountLink`      | R     | Deleted. No V1 identity remains.                                                  |
| `v1_user_id` kwarg on services           | R     | Removed from AI gateway, routines, inventory, profile baseline.                   |

## 2. V1 Endpoints Consumed By The Frontend

Extracted from `frontend/src/services/*` and `frontend/app/*`.

| V1 Path                              | Class | Replacement                                                                     |
|--------------------------------------|-------|---------------------------------------------------------------------------------|
| `POST /api/auth/register`            | R     | `POST /api/v2/access/register` (Supabase sign-up + invite redemption)          |
| `POST /api/auth/login`               | R     | Client-only. Frontend uses Supabase JS SDK. Backend never sees passwords.       |
| `GET  /api/users/me`                 | R     | `GET  /api/v2/me`                                                               |
| `PATCH /api/users/me`                | R     | `PATCH /api/v2/profile`                                                         |
| `POST /api/scan/preview`             | O     | Removed. Signed-out teaser is not part of V2 scope.                             |
| `POST /api/scan/analyse`             | R     | `POST /api/v2/scan/analyse`                                                     |
| `GET  /api/scan/history`             | R     | `GET  /api/v2/scan/history`                                                     |
| `POST /api/quiz/submit`              | R     | `POST /api/v2/quiz/submit`                                                      |
| `GET  /api/quiz/questions`           | R     | `GET  /api/v2/quiz/questions`                                                   |
| `GET  /api/plans`                    | O     | Removed. Paid plans are prohibited in this PR.                                  |
| `POST /api/subscription/order`       | O     | Removed. Payment functionality is prohibited.                                   |
| `GET  /api/services`                 | O     | Static salon-idea catalogue. If still needed by product, moves to a JSON asset. |
| `POST /api/recommendations/generate` | R     | `POST /api/v2/style/recommendations`                                            |
| `GET  /api/recommendations/history`  | R     | `GET  /api/v2/style/recommendations/history`                                    |
| `POST /api/admin/invites`            | R     | `POST /api/v2/access/admin/invites`                                             |
| `GET  /api/admin/invites`            | R     | `GET  /api/v2/access/admin/invites`                                             |

## 3. MongoDB Collections

| Collection            | Class | Replacement                                                                 |
|-----------------------|-------|-----------------------------------------------------------------------------|
| `users`               | R     | `accounts` + `profiles` in PostgreSQL. Passwords owned by Supabase Auth.   |
| `scans`               | R     | `scans` table in PostgreSQL (no image base64 stored).                      |
| `style_plans`         | R     | `style_recommendations` table in PostgreSQL.                                |
| `subscription_orders` | O     | Removed. Payment stack deleted.                                             |
| `invite_codes`        | R     | `invites` + `invite_redemptions` tables in PostgreSQL.                     |
| `login_attempts`      | O     | Removed. Supabase Auth handles brute-force protection.                     |
| `preview_attempts`    | O     | Removed with the signed-out preview.                                       |
| `ai_usage`            | R     | `beta_usage_events` table (neutral, non-billing).                          |

## 4. MongoDB / Motor / PyMongo Imports

Every file that imports `database`, `motor`, or `pymongo`:

| File                                                    | Class | Action                                                               |
|---------------------------------------------------------|-------|----------------------------------------------------------------------|
| `backend/database.py`                                   | R     | Deleted.                                                             |
| `backend/security.py`                                   | R     | Deleted.                                                             |
| `backend/invites.py`                                    | R     | Rewritten in PostgreSQL under `app/domains/beta_access/`.            |
| `backend/ai.py`                                         | R     | AI usage moves to PostgreSQL `beta_usage_events`.                    |
| `backend/routes/users.py`                               | R     | Deleted. `/api/v2/me` + `/api/v2/profile` replace it.               |
| `backend/routes/scan.py`                                | R     | Deleted. `/api/v2/scan/*` replaces it.                              |
| `backend/routes/quiz.py`                                | R     | Deleted. `/api/v2/quiz/*` replaces it.                              |
| `backend/routes/recommendations.py`                     | R     | Deleted. `/api/v2/style/*` replaces it.                             |
| `backend/routes/plans.py`                               | O     | Deleted (paid plans prohibited).                                     |
| `backend/routes/services.py`                            | O     | Deleted (static catalogue).                                          |
| `backend/routes/subscription.py`                        | O     | Deleted (payments prohibited).                                       |
| `backend/routes/admin.py`                               | R     | Replaced by `/api/v2/access/admin/*`.                               |
| `backend/scripts/cleanup_v1_scan_image_prefixes.py`     | O     | Deleted with the Mongo `scans` collection.                          |
| `backend/server.py`                                     | R     | Rewritten to mount only `/api/v2` and drop the Mongo lifecycle.     |
| `backend/app/api/v2/privacy.py` (imports `database`)    | R     | Uses PostgreSQL + Supabase Auth admin for account deletion.         |

## 5. `security` Module Usage

Anywhere `from security import ...` or `security.get_current_user` appears:

| Consumer                                    | Class | Action                                                            |
|---------------------------------------------|-------|-------------------------------------------------------------------|
| Every V2 route via `deps.get_current_account`| R    | Replaced by `Depends(get_current_supabase_user)` chain.           |
| V1 `routes/*` files                          | R    | Deleted with the routes.                                          |

## 6. `get_current_user` References

Deleted along with `security.py`. Every V2 dependency chain now originates
from `app.shared.security.supabase_auth.get_current_supabase_user`.

## 7. `v1_user_id` References

Deleted. The following signatures no longer take `v1_user_id`:

- `app.domains.ai_gateway.gateway.run_structured`
- `app.domains.profile.baseline.analyse`
- `app.domains.routines.service.generate_routine`
- `app.domains.routines.service.check_ingredients`
- `app.domains.routines.explanation.*`
- `app.domains.inventory.extraction.extract`

The AI gateway now takes the Supabase UUID (as a plain `str`) for its cost
ledger. Every downstream caller passes `str(current_account.account_id)`.

## 8. `account_links` References

The 15+ `ForeignKey("account_links.id", ...)` declarations across the domain
models are rewritten to `ForeignKey("accounts.id", ...)`. `accounts.id` is a
Postgres `uuid` PK equal to the Supabase Auth user UUID.

The Alembic reset (Section 15) creates the `accounts` table directly with no
intermediate `account_links` step.

## 9. Frontend `/api` V1 Base

`frontend/src/services/api.ts` currently uses `${EXPO_PUBLIC_BACKEND_URL}/api`
as its base for all business calls. This PR ships a scoped Supabase client and
a V2 HTTP client (`/api/v2/…`). The Expo cutover (auth screens, session store,
API paths) is scoped for a follow-up PR by the owner's explicit choice; this
PR ships the backend V2 endpoints and one static test asserting the frontend
must not add new active V1 business paths.

## 10. Authentication, Invite, Profile, Scan, Quiz, Recommendation Deps

| Feature area   | V1 dependency                                                     | V2 replacement                                                     |
|----------------|-------------------------------------------------------------------|--------------------------------------------------------------------|
| Auth           | `security.get_current_user` + Mongo `users`                       | Supabase Auth JWT verification (JWKS + optional HS256 fallback).   |
| Invite         | `backend/invites.py` (Mongo `invite_codes`)                       | `app/domains/beta_access/` (`invites`, `invite_redemptions`).      |
| Profile        | Mongo `users.<profile fields>`                                    | `app/domains/profile/` on PostgreSQL, keyed on Supabase UUID.      |
| Scan           | `routes/scan.py` + Mongo `scans` (stored `image_base64`)          | `app/api/v2/scan.py` + `scans` table, **no image base64 stored**.  |
| Quiz           | `routes/quiz.py` + Mongo `users.preferences`                      | `app/api/v2/quiz.py` + `quiz_submissions` table (versioned schema).|
| Recommendation | `routes/recommendations.py` + Mongo `style_plans`                 | `app/api/v2/style.py` + `style_recommendations` table.             |

## 11. Media Storage

| Backend            | Class | Notes                                                                 |
|--------------------|-------|-----------------------------------------------------------------------|
| Local filesystem   | O     | Kept in codebase only for the `APP_ENV=test` unit-test fixture.       |
| S3 (`boto3`)       | O     | Removed from production configuration. Deleted in Prompt 2.           |
| **Supabase Storage** | **R** | **Sole production media backend.** Private bucket, signed URLs.   |

Justification for a single production backend: the problem statement
requires it, and there is no concrete technical blocker.

## 12. Privacy / Export / Delete

Existing `app/api/v2/privacy.py` calls into Mongo (`database.db`) to erase a
V1 account. It is rewritten as a single authoritative workflow that:

1. Exports all PostgreSQL records for the user.
2. Deletes all Supabase Storage objects under `account/{uuid}/…`.
3. Deletes PostgreSQL rows in FK-safe order.
4. Calls `supabase.auth.admin.delete_user(uuid)` with the service-role key.
5. Records a minimal audit record with the UUID and timestamp only — no PII.

## 13. Current PostgreSQL Models & Migrations

Every existing model file in `app/domains/*/models.py` currently references
`account_links.id`. **All eight** migrations in `backend/migrations/versions/`
build the `account_links`-based schema step by step and include
billing/entitlement/plan tables that this PR prohibits.

Decision (recorded in `docs/stabilisation/SUPABASE_CUTOVER_REPORT.md`): the
eight legacy migrations are deleted from `main`. Git history preserves them.
A single new migration `0001_initial_supabase_schema.py` is autogenerated
against the final model set.

## 14. Payment Dependencies Prohibited By This PR

None of the following may be copied into the new architecture:

- `backend/app/domains/billing/` (whole tree)
- `backend/app/domains/entitlements/` (whole tree)
- `backend/app/api/v2/billing.py`
- `backend/app/api/v2/shopping.py::/entitlements`
- Any Razorpay-related env vars: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`,
  `RAZORPAY_WEBHOOK_SECRET`
- `BILLING_PROVIDER`, `BILLING_GRACE_DAYS`, `SUBSCRIPTIONS_AVAILABLE`,
  `PLUS_MONTHLY_INR`, `PLUS_YEARLY_INR`, `EVENT_PASS_PRICE_INR`,
  `EVENT_PASS_VALID_DAYS`, `ENTITLEMENT_CACHE_SECONDS`
- Migration 0008 (`billing_and_release`)
- Frontend `subscription.tsx`, `paywall.tsx` (removed from active navigation)

Class: **R** — physically removed from the tree in this PR. Prompt 2 does the
final sweep for any stray import.

## 15. Migration Reset Decision

Because no production database state must be preserved:

1. All eight existing Alembic revisions under `backend/migrations/versions/`
   are deleted from this branch.
2. One new revision `0001_initial_supabase_schema.py` is generated from the
   post-cutover model set.
3. `alembic upgrade head` from an empty PostgreSQL database succeeds.
4. `alembic check` reports no drift.
5. `alembic downgrade base && alembic upgrade head` succeeds.
6. The old migrations remain in git history at `ac4822d…` for reference.

## 16. Storage / Local FS / Media

- `MEDIA_STORAGE_BACKEND=local` retained **only** for tests (`APP_ENV=test`).
- `MEDIA_STORAGE_BACKEND=supabase` becomes the production default.
- S3 adapter and env vars are removed.
- Face/person scan photos are **not** stored. Inventory photos **are**.
- Deletion cascades: DB row `→` storage object `→` (if account-level delete)
  Supabase Auth user.

## 17. Beta Usage Controls

Commercial quotas (`FREE_SCANS_PER_MONTH`, `INVITE_SCANS_PER_MONTH`,
`PLUS_MONTHLY_INR`, `Plus` plan flag, "Event Pass") are removed. Replaced by:

| Env variable                             | Default | Meaning                                    |
|------------------------------------------|---------|--------------------------------------------|
| `BETA_AI_REQUESTS_PER_HOUR`              | 60      | AI-call rate limit per account per hour.  |
| `BETA_SCAN_LIMIT_PER_MONTH`              | 60      | Scans per calendar month per account.     |
| `BETA_STYLE_LIMIT_PER_MONTH`             | 60      | Style-recommendation runs per month.      |
| `BETA_SHOPPING_CHECK_LIMIT_PER_MONTH`    | 60      | Shopping decisions per month.             |

Failed AI runs are not counted. Retries with the same idempotency key are
not double-counted. All limits derive from the authenticated Supabase UUID.

## 18. Non-Payment Files That Must Move

| Item                                                | Class | Where it goes                                        |
|-----------------------------------------------------|-------|------------------------------------------------------|
| Salon idea static catalogue in `catalog.py`         | M     | `backend/app/domains/recommendation/catalogue.py`   |
| Weather adapter                                     | M     | Stays. Already V2 (no auth surface).                 |
| Sentry bootstrap                                    | M     | Stays. Runtime concern, no identity coupling.        |
| AI gateway                                          | M     | Stays under `app/domains/ai_gateway/`.               |
| Routines domain                                     | M     | Stays. Only FK target changes to `accounts.id`.     |
| Progress domain                                     | M     | Stays. FK target changes.                            |
| Planning domain                                     | M     | Stays. FK target changes.                            |
| Consent domain                                      | M     | Stays. FK target changes.                            |
| Media domain                                        | M     | Stays. Storage backend switches to Supabase.        |

---

## 19. Summary Of Changes This PR Ships

- **Removed**: `backend/database.py`, `backend/security.py`, `backend/invites.py`,
  `backend/ai.py`, `backend/models.py`, all V1 route files, the whole
  `billing` and `entitlements` domains, `catalog.py`, `backend/scripts/*`,
  all eight legacy Alembic revisions, MongoDB indexes on startup, MongoDB
  test fixtures.
- **Added**: `app/shared/security/supabase_auth.py`,
  `app/domains/beta_access/`, `app/domains/accounts/` (Supabase-keyed),
  `app/domains/scan/`, `app/domains/quiz/`, `app/api/v2/access.py`,
  `app/api/v2/scan.py` (V2 rewrite), `app/api/v2/quiz.py`,
  `app/api/v2/style.py`, `app/domains/media/storage/supabase.py`,
  fresh `migrations/versions/0001_initial_supabase_schema.py`, four new
  docs, updated tests.
- **Modified**: every `app/domains/*/models.py` (FK to `accounts.id`),
  `app/api/v2/*` routes to use `get_current_supabase_user`, `app/config.py`
  to add Supabase env, `requirements.txt` to add supabase-py + PyJWT
  cryptography.

## 20. Prompt-2 Follow-Ups Explicitly Left In Place

These are **T** (temporarily retained) and must be removed by Prompt 2 once
the cutover has landed:

- CI job that runs the old MongoDB integration suite (`test_v1_regression.py`
  is deleted; the CI service definition is left to remove in Prompt 2).
- `docker-compose.yml` still declares a `mongo` service.
- The Expo `frontend/src/services/*` code still contains inactive V1 API
  helpers guarded behind a hard `throw`, pending the frontend cutover PR.
- Old boto3/S3 code paths remain reachable only via `MEDIA_STORAGE_BACKEND=s3`
  and are documented as "removed in Prompt 2".

---

## Approval To Begin Implementation

This audit is the mandatory precondition. Implementation begins in the same
PR under commits scoped to the areas above. No commit in this PR touches:

- Payment providers, checkout, refunds, subscriptions, prices, or paid plans.
- The Expo authentication cutover (deferred to a follow-up PR by owner choice).
- The `main` branch (all work stays on `architecture/supabase-v2-cutover`).
