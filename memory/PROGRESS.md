# Supabase V2 Cutover — Session Progress Tracker

**Repo**: https://github.com/blazebrt/GlamGenius
**Branch (this session)**: `architecture/supabase-v2-cutover` (checked out locally in `/app`)
**Baseline commit (verified ancestor of HEAD)**: `ac4822d3de04e283b3869bc8947e8a7710e99404`
**Latest main HEAD when session started**: `236ef9d6` (Merge #40 from `legacy-remover`, CI marked failure)

> **Read me first if you resume in a fresh session.** This file is the single
> source of truth for what has already been done, what is in flight, and what
> is next. Do **not** redo completed steps. Cross-check with `git log` and
> `git status` in `/app` before writing any code.

---

## 1. Ground truth: what is already merged on `main`

PR #40 (`legacy-remover` → `main`) merged the bulk of the cutover. The
following are **DONE** and must not be re-implemented:

- Backend Supabase Auth dependency
  (`backend/app/shared/security/supabase_auth.py`) — JWKS + HS256 fallback,
  issuer + expiry + `sub` claim validation.
- V1 backend deleted: no `backend/routes/`, no `database.py`, no
  `security.py`, no `invites.py`, no `ai.py`, no `models.py`.
- V2-only FastAPI surface under `backend/app/api/v2/` (23 route modules).
- Domains present under `backend/app/domains/`:
  `ai_gateway, analytics, audit, beta_access, consent, identity, inventory,
  media, planning, profile, progress, quiz, recommendation, routines, scan`.
- Supabase Storage adapter
  (`backend/app/domains/media/storage/supabase.py`).
- Supabase admin client (`backend/app/shared/supabase_client.py`).
- Single clean initial migration:
  `backend/migrations/versions/0001_initial_supabase_schema.py`. No legacy
  Alembic revisions remain.
- Backend tests:
  `test_supabase_auth.py`, `test_beta_access.py`, `test_v2_api.py`,
  `test_schema_regression.py`.
- Audit doc: `docs/architecture/SUPABASE_CUTOVER_AUDIT.md`.
- `env.example` fully Supabase-shaped, no Razorpay/plan settings.
- `backend/requirements.txt` includes `supabase==2.31.0`,
  `pyjwt[crypto]==2.13.0`, `asyncpg`, `sqlalchemy[asyncio]`.

## 2. Credentials the owner supplied

Stored **only** in `/app/backend/.env` (git-ignored). Never commit.

| Var                          | Value                                                                                     |
|------------------------------|-------------------------------------------------------------------------------------------|
| SUPABASE_URL                 | `https://llttywcumaqxzonvrhug.supabase.co`                                                |
| SUPABASE_ANON_KEY            | `eyJhbGciOiJIUzI1NiIs...` (HS256, iat 2026-11-30, exp 2036-11-27, `role=anon`)            |
| SUPABASE_SERVICE_ROLE_KEY    | `eyJhbGciOiJIUzI1NiIs...` (`role=service_role`) — server-side only                        |
| POSTGRES_URL (direct)        | `postgresql://postgres:Ravi225207%40@db.llttywcumaqxzonvrhug.supabase.co:5432/postgres`  |
| First admin UUID             | `771846b8-e033-4dac-94ed-f03bbafb88bc` (seed as first `SUPABASE_ADMIN_USER_IDS`)          |
| Storage bucket               | `glamgenius-media` (private) — must be created by owner in Supabase dashboard              |

**Owner still to confirm/provide**:
- Real admin email (currently placeholdered as `admin@glamgenius.dev`).
- Whether AI provider is Emergent LLM key or a raw `GEMINI_API_KEY`.
- Whether direct `db.*:5432` works from CI network; fallback is the
  pooler URI at port `6543` (`aws-0-<region>.pooler.supabase.com`).

## 3. What THIS session has done on `architecture/supabase-v2-cutover`

_Update this list after every commit-worthy change._

- [x] Cloned repo into `/app`, confirmed baseline ancestor.
- [x] Created branch `architecture/supabase-v2-cutover` from current `main`.
- [x] Wrote `/app/memory/PROGRESS.md` (this file).
- [ ] CI fix (`.github/workflows/ci.yml`) — remove mongo service + obsolete env vars, add Supabase envs, drop refs to deleted tests.
- [ ] Doc: `docs/architecture/SUPABASE_TARGET_ARCHITECTURE.md`.
- [ ] Doc: `docs/architecture/SUPABASE_AUTH_SECURITY.md`.
- [ ] Doc: `docs/operations/SUPABASE_SETUP.md`.
- [ ] Doc: `docs/stabilisation/SUPABASE_CUTOVER_REPORT.md`.
- [ ] Frontend: add `@supabase/supabase-js` to `frontend/package.json`.
- [ ] Frontend: `frontend/src/services/supabase.ts` (client singleton, session helpers).
- [ ] Frontend: rewrite `frontend/src/services/api.ts` to V2 base + Supabase bearer token.
- [ ] Frontend: rewrite `frontend/src/store/userStore.ts` — Supabase session store, drop plan/paywall fields.
- [ ] Frontend: rewrite auth screens under `frontend/app/(auth)/`.
- [ ] Frontend: remove `subscription.tsx`, `paywall.tsx` from active navigation (files stay for Prompt 2 to delete).
- [ ] Frontend: static Jest test asserting no active V1 endpoint calls remain.
- [ ] Backend: seed the first admin from `SUPABASE_ADMIN_USER_IDS`.
- [ ] Verify `alembic upgrade head` runs against local Postgres (Docker) and against Supabase Postgres.
- [ ] Verify `pytest -q backend/tests` passes locally with test envs.
- [ ] Frontend: `yarn typecheck && yarn lint --max-warnings=0 && yarn test --ci`.

## 4. Files known to be problematic (must-touch list)

- `.github/workflows/ci.yml` — mongo service, `MONGO_URL`, `DB_NAME`,
  `JWT_SECRET`, `ADMIN_SECRET`, `SUBSCRIPTIONS_AVAILABLE`, `V2_FEATURES`,
  and the `auth-privacy-regression` job references deleted tests
  (`test_privacy.py`, `test_media.py`, `test_v1_regression.py`,
  `test_config_flags_and_billing.py`, `test_critical_journey.py`).
- `frontend/src/services/api.ts` — still V1 base `${URL}/api`.
- `frontend/src/store/userStore.ts` — still uses `plan`, `plan_expires_at`,
  `scans_remaining_free`, `free_scans_per_month`, `refreshSubscription`.
- `frontend/app/subscription.tsx`, `frontend/app/paywall.tsx` — must not
  appear in active navigation.
- `docker-compose.yml` — still declares `mongo` service. Retained as **T**
  (Prompt 2 removes it) per the audit doc §20.

## 5. Explicitly OUT OF SCOPE for this PR

- Any payment/subscription/Razorpay code. **Never re-add**.
- Full Expo screen redesign — keep functional flows working; do not remove
  screens beyond the paywall/subscription entry points.
- Rewriting existing backend business logic. Only the identity/auth
  boundary changes; domain code stays as-is with the FK already retargeted
  to `accounts.id` by PR #40.

## 6. How to resume in a fresh session

```bash
# 1. Clone (only if /app is empty)
cd /tmp && git clone https://github.com/blazebrt/GlamGenius.git gg && \
  shopt -s dotglob && mv -f /tmp/gg/* /app/ && cd /app

# 2. Check out the working branch
git checkout architecture/supabase-v2-cutover 2>/dev/null || \
  git checkout -b architecture/supabase-v2-cutover origin/main

# 3. Read this file
cat /app/memory/PROGRESS.md

# 4. Diff against main to see exactly what THIS session has committed
git log --oneline main..HEAD
git diff --stat main..HEAD

# 5. Continue from the first unchecked box in §3.
```

## 7. Final PR checklist (do not merge; owner reviews)

- [ ] Backend tests green locally.
- [ ] Frontend `typecheck` + `lint` + `jest` green locally.
- [ ] `alembic upgrade head` from empty DB green locally.
- [ ] `alembic check` reports no drift.
- [ ] Grep confirms no active V1 `/api/...` business paths in `frontend/`.
- [ ] Grep confirms no `plan`, `plan_expires_at`, `scans_remaining_free`,
      `refreshSubscription`, `Razorpay`, `billing`, `paywall` in active code.
- [ ] All 4 required docs exist and cross-reference each other.
- [ ] Push branch via **Save to GitHub** feature (agent cannot push directly).
- [ ] Open PR on GitHub UI. Do NOT tick auto-merge.
