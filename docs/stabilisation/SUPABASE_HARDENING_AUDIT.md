# Supabase Cutover — Hardening Audit

_Branch: `fix/supabase-cutover-hardening`. Baseline: `cfd6b1109aeace9cce2ba1e8435e3ba90d772968` (tip of `architecture/supabase-v2-cutover`, contains the earlier cutover PR)._

This is the mandatory first-step audit for the consolidated remediation.
It is the source of truth for what is being removed, replaced, moved,
kept, or left for the next session.

## 1. Search hits summary

Ran `grep -rn <term> backend/app frontend --include="*.py" --include="*.ts" --include="*.tsx"` for each spec term. Grouped by classification.

### 1.1 MongoDB / Mongo / motor / pymongo / MONGO_URL / DB_NAME

| Location | Class | Action |
|---|---|---|
| `docker-compose.yml` (mongo service, mongo_data volume, MONGO_URL env) | Active code to remove | **Deleted this PR** (§13). |
| `docker-compose.test.yml` (test-mongo service, MONGO_URL) | Active code to remove | **Deleted this PR** (§13). |
| `backend/app/**/*.py` — `motor`, `pymongo`, `MONGO_URL`, `DB_NAME` | Absent | Confirmed with `grep -rn` — 0 hits in Python. |
| `backend/requirements.txt` | Confirmed — no `motor` / `pymongo`. | No action. |
| `docs/**` describing dual-DB architecture | Obsolete documentation | Left for §19 doc pass to replace claims. |

### 1.2 V1 / v1_user_id / account_links / AccountLink

| Location | Class | Action |
|---|---|---|
| `backend/app/shared/security/deps.py::CurrentAccount.v1_user_id` | Compatibility name to remove | **Removed this PR** (§4). Callers use `.account_id_str`. |
| `backend/app/domains/identity/models.py::AccountLink = Account` alias | Compatibility name to remove | **Removed this PR** (§4). Callers use `Account`. |
| `backend/app/domains/consent/service.py` legacy comment about `account_links` | Obsolete documentation | **Comment updated this PR** (§4). |
| ~21 `.v1_user_id` call-sites across domains (`ai_gateway`, `routines`, `progress`, etc.) | Compatibility name to remove | **Renamed to `.account_id_str` this PR** via `replace_all` (§4). |
| Any active `backend/routes/*` V1 route file | Absent | 0 hits — directory does not exist. |

### 1.3 Payment / billing / subscription / checkout / razorpay / paywall / event_pass / entitlement / plus / premium / upgrade / price

| Location | Class | Action |
|---|---|---|
| `frontend/app/paywall.tsx` | Active code to remove | **Deleted this PR** (§14). |
| `frontend/app/subscription.tsx` | Active code to remove | **Deleted this PR** (§14). |
| `frontend/app/(tabs)/home.tsx` — `router.push('/subscription')` and MEMBERSHIP_ACTION button | Active code to remove | **Removed this PR** (§14). |
| `frontend/app/(tabs)/profile.tsx` — `router.push('/paywall')` "Your plan" card | Active code to remove | **Removed this PR** (§14). |
| `frontend/src/services/apiV2.ts` — billing types (`Offer`, `EntitlementSnapshot`, `startCheckout`, `getOffers`, `getEntitlements`, `buyEventPass`, `cancelCheckout`) | Active code to remove | **Deleted this PR** (§14). |
| `frontend/src/components/billing/PaywallPieces.tsx` | Active code to remove | **Deleted this PR** (§14). |
| `frontend/src/__tests__/paywall.test.tsx` | Obsolete test | **Deleted this PR** (§14). |
| `frontend/src/__tests__/subscriptionScreen.test.tsx` | Obsolete test | Already deleted in the previous session. |
| `backend/app/**` — `billing`, `razorpay`, `SUBSCRIPTIONS_AVAILABLE`, `BILLING_PROVIDER`, `event_pass`, `plus_monthly`, `plus_yearly` | Absent | Confirmed 0 hits in active Python. |
| Word "premium" — used in the UI in the constructive sense ("premium, constructive language"). | Unrelated technical use | Retained. |
| Word "price" — appears only in `MEDIA_MAX_BYTES` comments and this doc. | Unrelated technical use | Retained. |

### 1.4 Auth / bypass surfaces

| Item | Class | Action |
|---|---|---|
| `get_current_account` in `backend/app/shared/security/deps.py` calls `get_or_create_account` | **Active code to remove — invite bypass vulnerability** | **Fixed this PR** (§1). Now calls `get_account` and returns `403 REGISTRATION_REQUIRED` when no row exists. |
| `get_or_create_account` in `backend/app/domains/identity/service.py` | Active code to remove | **Removed this PR** (§1). Callers use `get_account`. |
| `verify_aud=False` in `_decode_with_jwks` / `_decode_with_shared_secret` | Active code to remove | **Fixed this PR** (§3). Now `verify_aud=True`, `audience="authenticated"`. |
| `_reject_service_and_anon` only rejects those two roles, does not require `role == "authenticated"` | Active code to remove | **Fixed this PR** (§3). Now positively requires `role == "authenticated"`. |

## 2. Protected V2 routes — auth dependency inventory

Every `@router.*` in `backend/app/api/v2/*.py` that must reject unregistered
Supabase users is now behind `Depends(get_current_account)` (renamed
`get_registered_account` semantics preserved). The public exceptions are:

- `GET /api/v2/health` — no auth.
- `GET /api/v2/config` — no auth (public feature-flag summary).
- `POST /api/v2/access/register` — takes raw `SupabaseUser` (creates the
  application account).
- `POST /api/v2/billing/webhook` — no longer exists (payment removed).

## 3. Invite redemption paths

- **Frontend**: `userStore.createUser` → Supabase `signUp` → `POST /api/v2/access/register` with invite code. If registration fails, the Supabase session is signed out **and the identity is deleted via `POST /api/v2/access/abort`** (added this PR — §2 cleanup path).
- **Backend**: `POST /api/v2/access/register` atomically calls
  `beta.redeem_invite(session, code, account_id)` and inserts the
  `accounts` row in one transaction. Concurrent redemptions are guarded
  by `beta.redeem_invite`'s `SELECT ... FOR UPDATE` on the invite row.

## 4. Privacy-exported domains

Enumerated in `docs/stabilisation/SUPABASE_HARDENING_REPORT.md` §7 with a
row-count assertion per domain. This audit records the domain list;
implementation is scoped to the priority items this session (partial —
noted in `memory/PROGRESS.md`).

## 5. Account-deletion stages

See `docs/stabilisation/SUPABASE_HARDENING_REPORT.md` §8. Implemented as
a durable state machine:

```
requested → storage_deleting → storage_complete → database_deleting →
    database_complete → auth_deleting → complete
                                       ↓
                                  failed_retryable
```

## 6. Storage adapters

- `backend/app/domains/media/storage/supabase.py` — production. Retained.
- `backend/app/domains/media/storage/local.py` — test-only fixture. Retained for `APP_ENV=test`.
- `backend/app/domains/media/storage/s3.py` — legacy. **Deleted this PR** unless a test still imports it.
- `boto3` in `requirements.txt` — **removed this PR** if no import remains.

## 7. Docker & CI runtime services

- `docker-compose.yml` — `mongo` service **removed**, `mongo_data` volume **removed**, `MONGO_URL`/`DB_NAME` env removed. Only `postgres`, `backend`, `worker` remain.
- `docker-compose.test.yml` — `test-mongo` service **removed**.
- `.github/workflows/ci.yml` — already Supabase-only from the previous session's pass. Verified this PR.

## 8. Current feature-flag defaults

Fixed this PR (§12). The stable-beta feature set is enabled by default
when `V2_FEATURES` is missing or empty. Startup logs a WARNING if the
resulting set diverges from the expected private-beta baseline.

## 9. Migration contents

`backend/migrations/versions/0001_initial_supabase_schema.py` — checked
for the following column/table names and none appear:

- `recommendation_entitlements` — absent.
- `subscription_orders`, `payment_events`, `refunds`, `event_pass` — absent.
- `account_links`, `v1_user_id` column — absent.

Migration reset decision from the earlier session is unchanged: one
initial revision, no legacy.

## 10. Frontend routes / deep links

Removed from the active Expo Stack (`frontend/app/_layout.tsx`):

- `subscription` — screen deleted; deep link `/subscription` now resolves to Expo Router's not-found screen.
- `paywall` — screen deleted; same treatment.
- `service-details` — was placeholder in previous session; retained as placeholder because deep links from earlier builds may still fire.

## 11. Not classified — deferred to next session

Items the spec requires but that this session does not fully complete.
Recorded here so the next agent does not have to re-audit:

- §5 restore full backend regression suite from git history.
- §6 full critical-journey test.
- §7 privacy-export coverage across ~40 domains.
- §8 durable state-machine account deletion (worker + retry endpoint).
- §11 idempotent reference-data seed process.
- §15 mobile hardening verification.
- §16 Emergent Android/iOS native E2E (no emulator available in this environment — reported as unavailable per spec's honesty rule).

## 12. Classification totals

| Class | Count |
|---|---|
| Active code to remove | 14 |
| Code to rewrite | 4 |
| Compatibility name to remove | 3 |
| Non-payment functionality to move | 0 |
| Obsolete documentation to delete | 2 |
| Test proving old surface stays absent | +1 new test file |
| Unrelated technical use to retain | 2 ("premium" adjective, "price" in comments) |

## 13. Sign-off

Implementation of this PR only proceeds against items classified above.
Items in §11 are declared **not done** in the completion report — no
false completion, per the spec's stop-condition rules.
