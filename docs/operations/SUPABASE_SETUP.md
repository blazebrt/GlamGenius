# Supabase Setup — Operator Runbook

_Last updated: 2026-01. Audience: the person setting up a fresh
Supabase project for GlamGenius, either for local development or for a
new environment._

This document assumes zero prior state. If you already have a running
project, jump to §6 for the environment-variable checklist.

## 1. Create the Supabase project

1. Sign in to https://supabase.com/dashboard.
2. **New project.** Choose the region closest to your users. For an
   India-first product, `ap-south-1` is the natural default.
3. Set a strong **database password**. This becomes the `postgres`
   user's password in the direct connection string. Store it in a
   password manager — the dashboard cannot reveal it again.
4. Wait for the project to finish provisioning (~2 minutes).

## 2. Grab the connection details

**Dashboard → Project Settings → API.**

| Copy value              | Env var                      |
|-------------------------|------------------------------|
| Project URL             | `SUPABASE_URL`               |
| `anon` `public` key     | `SUPABASE_ANON_KEY`          |
| `service_role` key      | `SUPABASE_SERVICE_ROLE_KEY`  |
| JWT Secret (legacy HS256)| `SUPABASE_JWT_SECRET`       |

**Dashboard → Project Settings → Database → Connection string.**

Two URIs are shown. Take **both**:

- **Direct** (port `5432`, host `db.<ref>.supabase.co`) — fastest, but
  requires the CI/host to reach the Supabase IPv4 address. Use this for
  local development.
- **Transaction pooler** (port `6543`, host
  `aws-0-<region>.pooler.supabase.com`) — required if your host cannot
  reach `db.<ref>.supabase.co` on IPv4, and preferred for serverless
  runtimes.

Set `POSTGRES_URL` to the direct URI. Keep the pooler URI in the same
password manager entry as `POSTGRES_URL_POOLER` for the fallback.

If Alembic connections start failing with `EHOSTUNREACH` or
`getaddrinfo failed`, switch `POSTGRES_URL` to the pooler URI. In the
pooler URI the username is `postgres.<project-ref>`, not `postgres`.

## 3. Create the private storage bucket

**Dashboard → Storage → New bucket.**

- Name: `glamgenius-media` (must equal `SUPABASE_STORAGE_BUCKET`).
- Public: **off**. This is a private bucket.
- File size limit: `10 MB` (matches `MEDIA_MAX_BYTES` in the app).
- Allowed MIME types: leave blank (FastAPI validates MIME server-side).

You do **not** need to write Storage RLS policies. All reads and writes
go through FastAPI, using the service-role key. The Expo client never
touches Storage directly.

## 4. Run the initial migration

From a checkout of the repo with `SUPABASE_URL` and `POSTGRES_URL` set
in `backend/.env`:

```bash
cd backend
python -m pip install -r requirements.txt
alembic upgrade head
alembic check    # must report "No new upgrade operations detected."
```

This creates every table used by V2. There are no legacy migrations to
apply — the schema is a single, clean initial revision.

If `alembic upgrade head` fails with an SSL certificate error on the
Supabase host, add `?sslmode=require` to `POSTGRES_URL`.

## 5. Seed the first admin

1. In the Supabase dashboard → **Authentication → Users → Add user**.
   Create the user account you want to use for admin operations. Note
   the user's UUID.
2. Add that UUID to `SUPABASE_ADMIN_USER_IDS` in your deployment env.
   Multiple admins are comma-separated:

   ```
   SUPABASE_ADMIN_USER_IDS=771846b8-e033-4dac-94ed-f03bbafb88bc,<second-admin-uuid>
   ```
3. Restart the FastAPI process. That user can now call
   `POST /api/v2/access/admin/invites` to create invite codes.

There is no admin password, no admin header, no `ADMIN_SECRET` env var.
The set of admins is a static server-side list of Supabase UUIDs.

## 6. Environment variables — full checklist

Copy `env.example` to `backend/.env` and fill:

| Var                              | Purpose                                                                        |
|----------------------------------|--------------------------------------------------------------------------------|
| `SUPABASE_URL`                   | Public Supabase project URL. Shared with Expo.                                 |
| `SUPABASE_ANON_KEY`              | Public JWT. Shared with Expo.                                                  |
| `SUPABASE_SERVICE_ROLE_KEY`      | Server-only. Never sent to Expo.                                               |
| `SUPABASE_JWT_ISSUER`            | Defaults to `${SUPABASE_URL}/auth/v1`. Override only if Supabase changes it.   |
| `SUPABASE_JWKS_URL`              | Defaults to `${SUPABASE_URL}/auth/v1/.well-known/jwks.json`.                   |
| `SUPABASE_JWT_SECRET`            | HS256 fallback. Optional if the project ships RS256.                           |
| `SUPABASE_STORAGE_BUCKET`        | Private bucket name (must equal the one created in §3).                        |
| `SUPABASE_ADMIN_USER_IDS`        | Comma-separated UUIDs allowed to perform admin actions.                        |
| `POSTGRES_URL`                   | Direct or pooler URI (see §2).                                                 |
| `GEMINI_API_KEY` / Emergent LLM  | Either a Gemini key or the Emergent universal LLM key.                          |
| `INVITE_REQUIRED`                | `true` in prod. `false` locally to sign up without a code.                     |
| `BETA_AI_REQUESTS_PER_HOUR`      | Server-side abuse control. Default 60.                                         |
| `BETA_SCAN_LIMIT_PER_MONTH`      | Server-side abuse control. Default 60.                                         |
| `BETA_STYLE_LIMIT_PER_MONTH`     | Server-side abuse control. Default 60.                                         |
| `BETA_SHOPPING_CHECK_LIMIT_PER_MONTH`| Server-side abuse control. Default 60.                                     |
| `ALLOWED_ORIGINS`                | Explicit CORS allowlist. No `*`.                                               |
| `REQUIRE_ANALYSIS_CONSENT`       | `true` in prod. Enforced by every scan/analysis endpoint.                      |
| `CONSENT_VERSION`                | ISO date of the current consent copy.                                          |
| `MEDIA_STORAGE_BACKEND`          | `supabase` in prod. `local` only for tests.                                    |

For Expo, the only Supabase values that must ship in the bundle are
`SUPABASE_URL` and `SUPABASE_ANON_KEY`, exposed as
`EXPO_PUBLIC_SUPABASE_URL` and `EXPO_PUBLIC_SUPABASE_ANON_KEY` in
`frontend/.env`.

## 7. Local development

```bash
# Backend
cd backend
cp ../env.example .env
# edit .env with the values from §2, §3, §5
pip install -r requirements.txt
alembic upgrade head
uvicorn server:app --reload --host 0.0.0.0 --port 8001

# Frontend
cd ../frontend
cat > .env <<'EOF'
EXPO_PUBLIC_BACKEND_URL=http://localhost:8001
EXPO_PUBLIC_SUPABASE_URL=https://<your-ref>.supabase.co
EXPO_PUBLIC_SUPABASE_ANON_KEY=<anon-key>
EOF
yarn install
yarn typecheck
yarn lint --max-warnings=0
yarn test --ci --watchAll=false
npx expo start --web
```

For a physical device, set `EXPO_PUBLIC_BACKEND_URL` to the machine's
LAN IP, not `localhost`.

## 8. CI setup

CI does not touch a real Supabase project. It runs against a
GitHub-Actions-managed ephemeral Postgres and stubs Supabase Auth with
signed test tokens. The workflow file is `.github/workflows/ci.yml`.
Required secrets: none. All test values are non-sensitive constants.

## 9. Rollback

Since no production data exists, rollback is just:

1. `alembic downgrade base` — drops every table.
2. Redeploy the pre-cutover git ref if the app also has to be reverted.
3. Delete and recreate the Supabase project if a full reset is wanted.

No customer data reconciliation is required.

## 10. Known limitations

- Supabase Storage signed-URL TTL is bounded to **900 seconds** in the
  code. Larger downloads must be chunked by the client, not proxied.
- The direct `db.<ref>.supabase.co:5432` endpoint is IPv4-only in some
  regions. If your host does not have IPv4 egress, use the pooler URI.
- Password reset flow currently relies on Supabase's own email templates.
  Customising the "reset your password" template requires configuring
  the SMTP provider in the Supabase dashboard.
