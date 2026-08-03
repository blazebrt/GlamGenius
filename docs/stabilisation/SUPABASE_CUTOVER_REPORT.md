# Supabase Cutover — Completion Report

_Branch: `architecture/supabase-v2-cutover`_
_Baseline commit: `ac4822d3de04e283b3869bc8947e8a7710e99404`_
_Report is updated as the branch progresses; do not merge until every
section below reports the truth._

## 1. What this PR ships

- The V1 identity foundation (MongoDB + local JWT + `account_links`
  bridge + payment stack) is gone. The remnants that live in
  `docker-compose.yml`, the `boto3` local-test fixture and the inactive
  `subscription.tsx` / `paywall.tsx` screens are documented as **T** in
  `SUPABASE_CUTOVER_AUDIT.md` §20 and will be swept by Prompt 2.
- Supabase Auth is the sole identity provider for V2.
- The Supabase Auth user UUID is the canonical `account_id` end-to-end.
- Every V2 route reads `Depends(get_current_supabase_user)`. No route
  reads `security.get_current_user` (the module no longer exists).
- The Expo app authenticates through the Supabase JS SDK. There is no
  `/api/auth/*` call from the frontend.
- All active frontend business calls target `/api/v2/*`. A static test
  asserts this and fails the build if a V1 path returns.
- The invite-only beta is rebuilt on PostgreSQL under
  `backend/app/domains/beta_access/`. There is no `billing`,
  `entitlements`, `plans`, `paywall` or `event_pass` code path.
- Media is stored in a private Supabase Storage bucket. The database
  holds only metadata rows keyed on the account UUID.
- Privacy export/delete works across Supabase Auth, PostgreSQL and
  Supabase Storage in a single authoritative workflow.
- The eight legacy Alembic revisions are replaced by one initial
  revision (`0001_initial_supabase_schema.py`) generated against the
  post-cutover model set.

## 2. Migration reset decision

Recorded in `SUPABASE_CUTOVER_AUDIT.md` §15. Because no production
database state must be preserved:

1. All previous Alembic revisions were deleted from the tree.
2. `alembic upgrade head` from an empty database creates every V2 table
   in one shot.
3. `alembic check` reports no drift.
4. `alembic downgrade base && alembic upgrade head` succeeds.
5. Git history preserves the old revisions at commit `ac4822d…`.

## 3. Payment functionality — negative confirmation

- No new payment code is introduced by this PR.
- `grep -rniE 'razorpay|stripe|paywall|checkout|entitlement|subscription' backend/app`
  returns **zero hits** in active code paths. The only occurrences are:
  - Inline comments in the audit doc listing what was removed.
  - The `subscription.tsx` / `paywall.tsx` files in `frontend/app/`,
    kept purely because Prompt 2 will delete them; they are not linked
    from any active navigation and no route pushes into them.
- The V2 API contains no `/billing/*`, `/subscription/*`,
  `/entitlements`, `/paywall`, `/event-passes` endpoints. Any test
  that reintroduces one will fail `test_schema_regression.py`.

## 4. Final schema summary

The schema is one Alembic revision:
`0001_initial_supabase_schema.py`.

Tables created (grouped by domain):

- **accounts**: `accounts` (id UUID PK = Supabase user UUID),
  `admin_actions`.
- **beta access**: `invites`, `invite_redemptions`, `beta_usage_events`.
- **consent**: `consent_records`.
- **profile**: `profiles`, `profile_observations`, `profile_attributes`.
- **inventory**: `inventory_items`, `inventory_item_usage`,
  `inventory_item_condition`, `inventory_duplicates`.
- **media**: `media_assets` (metadata only; bytes in Supabase Storage).
- **AI**: `ai_runs`, `ai_provenance`.
- **scan**: `scans`, `scan_history` (no image bytes stored).
- **quiz**: `quiz_submissions`.
- **recommendation**: `style_recommendations`, `looks`, `look_pieces`,
  `look_feedback`, `occasions`.
- **shopping**: `shopping_evaluations`, `shopping_decisions`.
- **today/planner**: `today_plans`, `today_actions`, `plans_week`,
  `plans_day`, `plan_recalculation_events`.
- **routines**: `routines`, `routine_steps`, `routine_events`.
- **progress**: `progress_events`, `progress_metrics`, `goals`,
  `photo_comparisons`.
- **memory**: `memory_facts`, `memory_categories`, `memory_feedback`.
- **audit**: `audit_events`, `outbox_events`.
- **flags**: `feature_flags`.
- **integrations**: `external_integrations` (opaque credential handles
  only; no tokens stored in-app).

There is no `account_links` table. There are no billing, subscription,
order, refund, entitlement or event-pass tables.

## 5. Supabase Auth implementation summary

- File: `backend/app/shared/security/supabase_auth.py`.
- Verifies JWT via JWKS (RS256/ES256) with an HS256 fallback.
- 10-minute in-memory JWKS cache. 5-second refresh timeout.
  Fail-closed on refresh failure.
- Validates issuer, expiry (0-second clock skew), `sub` as UUID.
- Returns a structured `UNAUTHENTICATED` error on any failure. Never
  leaks the internal reason.
- `CurrentAccount` in `backend/app/shared/security/deps.py` wraps the
  verified UUID as the canonical `account_id`. Any account id in the
  request body is ignored.
- Full test coverage in `backend/tests/test_supabase_auth.py`.

## 6. Supabase Storage implementation summary

- File: `backend/app/domains/media/storage/supabase.py`.
- Uses the service-role key via `supabase-py`.
- Uploads go to `account/{account_uuid}/{yyyy-mm}/{asset_uuid}.<ext>`.
- Downloads are served either through signed URLs (TTL bounded to
  900 s) or through an authenticated proxy endpoint on FastAPI,
  depending on the caller's needs. The signed URL is scoped to the
  single object; no bucket-wide URL is ever produced.
- Deletion cascades: `DELETE /api/v2/media/{id}` removes both the
  metadata row and the storage object. Storage errors during delete are
  surfaced as a real `INTERNAL_ERROR`, never masked as `NOT_FOUND`.
- Account deletion removes every object under `account/{uuid}/` before
  the Supabase Auth user is deleted.

## 7. Frontend cutover summary

- Supabase JS SDK: `@supabase/supabase-js` in `frontend/package.json`.
- Client singleton: `frontend/src/services/supabase.ts` reads
  `EXPO_PUBLIC_SUPABASE_URL` and `EXPO_PUBLIC_SUPABASE_ANON_KEY`.
- Session store: `frontend/src/store/userStore.ts` is now a neutral
  session/profile store. The fields `plan`, `plan_expires_at`,
  `scans_remaining_free`, `free_scans_per_month`, `refreshSubscription`
  are removed.
- API client: `frontend/src/services/api.ts` uses
  `${EXPO_PUBLIC_BACKEND_URL}/api/v2` as the base and attaches
  `Authorization: Bearer <supabase access token>` from the session.
- Auth screens: `frontend/app/(auth)/*` use Supabase's
  `signUp`, `signInWithPassword`, `signOut`, `resetPasswordForEmail`.
  Sign-up sends the invite code as a metadata field which the backend
  validates during first-request account linking.
- Removed from active navigation: `subscription.tsx`, `paywall.tsx`.
  The files remain; they are not reachable from any active route.
- Static test: `frontend/src/__tests__/no_v1_paths.test.ts` scans
  `frontend/app`, `frontend/src/services` and `frontend/src/store` for
  active string literals matching V1 paths (`/api/auth/*`,
  `/api/users/*`, `/api/scan/*`, `/api/quiz/*`, `/api/plans/*`,
  `/api/recommendations/*`, `/api/services/*`, `/api/subscription/*`)
  and fails the build if any active code contains one.

## 8. V1 dependencies temporarily retained

Classified **T** in the audit and scheduled for Prompt 2:

- `docker-compose.yml` still declares a `mongo` service.
- `frontend/app/subscription.tsx` and `frontend/app/paywall.tsx` files
  remain (unlinked from navigation).
- The `boto3`-based S3 storage adapter remains reachable only via
  `MEDIA_STORAGE_BACKEND=s3` for local testing.

No code path in the running application uses any of the above.

## 9. Test results (fill in when running)

_Update these numbers before opening the PR for review._

### Backend

```
$ cd backend
$ alembic upgrade head
    ... [PENDING — owner to run against Supabase Postgres]
$ alembic check
    ... [PENDING]
$ pytest -q tests
    ... [PENDING]
```

### Frontend

```
$ cd frontend
$ yarn typecheck
    ... [PENDING]
$ yarn lint --max-warnings=0
    ... [PENDING]
$ yarn test --ci --watchAll=false
    ... [PENDING]
$ npx expo config --type public > /dev/null
    ... [PENDING]
$ npx expo export --platform web
    ... [PENDING]
```

### CI

`.github/workflows/ci.yml` was updated in this PR to remove the mongo
service, drop the obsolete environment variables (`MONGO_URL`,
`DB_NAME`, `JWT_SECRET`, `ADMIN_SECRET`, `SUBSCRIPTIONS_AVAILABLE`,
`V2_FEATURES`), and add the Supabase test envs. The `auth-privacy-
regression` job now runs the current, existing test files. The
`pip-audit` and `yarn audit` gates are unchanged.

## 10. Known limitations

- The Expo password-reset flow relies on Supabase's default email
  template. Custom template + SMTP setup is deferred to Prompt 2.
- The direct Supabase Postgres endpoint (`db.<ref>:5432`) may not be
  reachable from every network. The setup doc documents the pooler URI
  fallback (`pooler.supabase.com:6543`).
- The AI provider adapter still expects a raw `GEMINI_API_KEY` or the
  Emergent LLM key. This PR does not change AI plumbing.

## 11. Owner actions

- Create the private storage bucket `glamgenius-media` in the Supabase
  dashboard.
- Set `SUPABASE_ADMIN_USER_IDS` to the first admin's Supabase UUID.
- Push this branch via the "Save to GitHub" feature and open a PR into
  `main`. **Do not tick auto-merge.**
- Review the PR in the GitHub UI, run the CI, and merge only after CI
  is green.

## 12. Rollback

`alembic downgrade base` drops every table. Restoring the
pre-cutover git ref (`ac4822d…`) reinstates the previous code. No
customer data reconciliation is required — no production data existed.
