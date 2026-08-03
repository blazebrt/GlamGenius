# Supabase Target Architecture

_Last updated: 2026-01. Branch: `architecture/supabase-v2-cutover`._

## 1. One-line summary

```
Expo → Supabase Auth → FastAPI V2 → Supabase PostgreSQL → Supabase Storage
```

Supabase is the identity provider, application database and media storage.
FastAPI is the business-logic and security boundary. The Expo client never
writes sensitive product-domain tables directly.

## 2. Component diagram

```
┌────────────────────┐
│  Expo (React       │  Supabase JS SDK (auth only)
│  Native + Web)     │──────────────────────────────────────────┐
│                    │                                          │
│                    │  Bearer <supabase JWT>                   │
│                    │──────────────────────────────────────►   │
└────────────────────┘                                       │  │
                                                             ▼  ▼
                                            ┌───────────────────────────────┐
                                            │  FastAPI V2 (/api/v2/*)       │
                                            │  - Verifies Supabase JWT      │
                                            │  - Authorises by account UUID │
                                            │  - Enforces beta usage limits │
                                            │  - Runs AI gateway            │
                                            └──────┬──────────────┬─────────┘
                                                   │              │
                                     asyncpg / SQLA│              │supabase-py (service role)
                                                   ▼              ▼
                                       ┌────────────────┐  ┌──────────────────────┐
                                       │ Supabase       │  │ Supabase Storage     │
                                       │ PostgreSQL     │  │ (private bucket      │
                                       │ (single DB)    │  │  `glamgenius-media`) │
                                       └────────────────┘  └──────────────────────┘
```

## 3. Trust boundaries

| Boundary                              | What crosses it                                | What is enforced                                                                                                       |
|---------------------------------------|------------------------------------------------|------------------------------------------------------------------------------------------------------------------------|
| Expo → Supabase Auth                  | email, password, invite code                   | Supabase Auth signs a short-lived JWT and returns a rotating refresh token. Passwords never reach FastAPI.             |
| Expo → FastAPI                        | Business API calls with `Authorization: Bearer`| FastAPI validates the JWT against JWKS + issuer + expiry + `sub`. Any account id in the request body is ignored.       |
| FastAPI → Supabase PostgreSQL         | Parametrised SQL via `asyncpg`                 | All rows join through `accounts.id = <supabase uuid from token>`. Cross-account reads/writes are impossible in code.  |
| FastAPI → Supabase Storage            | Uploads/reads scoped to `account/{uuid}/…`     | Uses the service-role key. Client cannot see it. Signed URLs are short-lived and account-scoped.                       |
| FastAPI → Supabase Auth Admin API     | `admin.delete_user(uuid)` on account deletion  | Only reachable from the authenticated privacy endpoint, only for the caller's own UUID.                                |

**The service-role key never leaves the backend.** The Expo bundle receives
only `SUPABASE_URL` and `SUPABASE_ANON_KEY`. A leak of either is not a
privilege escalation because the anon key gains no product data — every
product row goes through FastAPI, which validates the JWT independently.

## 4. Data ownership

- **Auth**: Supabase Auth is the source of truth for `email`, `password_hash`,
  `email_verified`, refresh tokens, brute-force lockouts. FastAPI holds no
  password material and no login-attempt table.
- **Product**: PostgreSQL tables are the source of truth for profiles,
  inventory, scans, style, shopping decisions, today/planner, routines,
  progress, memory, consent, invites, media metadata. Every table has an
  `account_id UUID REFERENCES accounts(id) ON DELETE CASCADE` (or an
  audited nullable variant where the row survives account deletion, e.g.
  audit trails).
- **Media bytes**: Supabase Storage (`glamgenius-media` private bucket)
  holds the object bytes. PostgreSQL holds the metadata row.

## 5. What the client cannot do

- Cannot write directly to any product-domain table. Postgres URL is never
  exposed to Expo. The anon key is scoped to Supabase Auth endpoints only;
  no PostgREST is used.
- Cannot supply its own `account_id`. It is derived only from the verified
  `sub` claim of the JWT.
- Cannot select its own beta usage limits. `BETA_*` env vars are
  server-side.
- Cannot upload arbitrary MIME types. FastAPI validates both `Content-Type`
  and the actual byte-signature magic bytes before creating a metadata row.
- Cannot see anyone else's storage objects. Object paths are scoped by
  UUID and every read is proxied by FastAPI.

## 6. Feature-flag policy

`feature_flags` is a PostgreSQL table. Boot defaults come from `V2_FEATURES`
in `.env`; the table overrides at runtime with no redeploy. A disabled
route returns `404`, never `403` — a switched-off feature should look
absent, not forbidden.

**No feature flag ever gates a payment feature.** Payment functionality is
architecturally removed, not toggled.

## 7. What is not in this architecture

- No MongoDB.
- No local JWT signing (`JWT_SECRET` unused in code).
- No dual-write bridge to any legacy datastore.
- No V1 identity bridge (`account_links`, `v1_user_id`).
- No payment provider (Razorpay, Stripe or otherwise).
- No S3 in production. The `boto3` adapter and its dependency were
  removed in Package B; Supabase Storage is the only production media
  backend.
- No paid plans, entitlements, event passes, paywalls.

## 8. Rollout & rollback posture

Because there is no production database state, rollout is a
migration-plus-deploy of an empty PostgreSQL schema. Rollback is either
`alembic downgrade base` (schema goes away) or restoring the pre-cutover
git ref. There is no user data to reconcile in either direction.

## 9. Related documents

- `docs/architecture/SUPABASE_CUTOVER_AUDIT.md` — inventory of every V1
  dependency removed or moved.
- `docs/architecture/SUPABASE_AUTH_SECURITY.md` — JWT validation contract.
- `docs/operations/SUPABASE_SETUP.md` — how to provision a fresh
  Supabase project from zero.
- `docs/stabilisation/SUPABASE_CUTOVER_REPORT.md` — the completion
  evidence for this PR.
