# GlamGenius

Personal stylist + skin & hair wellness coach for India.

Not a diagnosis app. No salon cart or checkout — salon visits are suggestions only.
Currently **invite-only**, in a private beta. Nothing is for sale.

## Features

- AI skin / hair check (visible observations only)
- Skin tone → clothing colour recommendations (Indian wardrobe)
- Label ingredients to look for (e.g. salicylic acid for oily / pimple-prone look)
- Nutrition: ingredient → common Indian foods
- Salon ideas without prices or booking
- Free preview without an account: skin tone + top clothing colours
- Invite-only access with a monthly check allowance

### What happens when a check fails

Analysis either produces a real, schema-validated result or it fails openly. There is
no fallback that invents a skin tone, a hair type or a set of colours. A failed check:

- returns `ANALYSIS_UNAVAILABLE` with a reason and specific guidance
- **does not** use one of your monthly checks
- **does not** write anything to your profile
- can be retried with the same photo

## Quick start (Docker)

```bash
cp env.example .env
# set GEMINI_API_KEY in .env
docker compose up --build
```

Starts MongoDB, PostgreSQL, the API and the outbox worker. Database migrations run
automatically on start.

- API: http://localhost:8000/api/health
- V2 health (includes PostgreSQL): http://localhost:8000/api/v2/health

## Backend (local)

```bash
cd backend
cp ../env.example .env
# edit MONGO_URL=mongodb://localhost:27017
# edit POSTGRES_URL=postgresql+asyncpg://glamgenius:glamgenius@localhost:5432/glamgenius_v2
pip install -r requirements.txt
alembic upgrade head
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

## Frontend (Expo)

```bash
cd frontend
echo "EXPO_PUBLIC_BACKEND_URL=http://localhost:8000" > .env
npm install
npx expo start --web
```

For a physical phone, set `EXPO_PUBLIC_BACKEND_URL` to your machine LAN IP.

## Tests

Everything runs in containers — no Python or Node needed on your machine.

```bash
docker compose -f docker-compose.test.yml run --rm backend-tests
```

```bash
docker compose -f docker-compose.test.yml run --rm frontend-tests
```

```bash
docker compose -f docker-compose.test.yml down -v
```

`backend-tests` runs `alembic upgrade head`, then `alembic check` (which fails if a model
has drifted from the migrations), then pytest against the ASGI app in-process. The AI
provider is faked — tests never spend money.

`frontend-tests` runs `tsc --noEmit`, `expo lint` and Jest with React Native Testing
Library.

`backend_test.py` at the repository root is a separate end-to-end suite that needs a
running server, a live database and a real Gemini key. It is not part of the containerised
run.

## Databases

Two, on purpose, during the V2 transition.

| | MongoDB | PostgreSQL |
|---|---|---|
| Owns | users, auth, scans, style plans, invites, rate limits | media, consent, AI runs, appearance digital twin, onboarding, audit, usage ledger, flags, outbox |
| Used by | V1 (`/api`) | V2 (`/api/v2`) |
| Migrations | none | Alembic |

V2 stores no user attributes. `account_links` holds one row per user containing the V1
user id and nothing else identifying; every V2 table hangs off that. Authentication is
entirely V1. The link is created on a user's first V2 request, so there is no migration
and no backfill.

### Migrations

```bash
docker compose exec backend alembic upgrade head
```

```bash
docker compose exec backend alembic downgrade -1
```

```bash
docker compose exec backend alembic revision --autogenerate -m "what changed"
```

Add every new model module to `backend/app/shared/database/registry.py`, or
autogenerate will not see it and the table will silently never be created.

## Feature flags

V2 modules are off unless switched on. Set `V2_FEATURES` in `.env` for the boot default:

```
V2_FEATURES=v2_media,v2_privacy,v2_consent,v2_ai_gateway,v2_profile
```

The `feature_flags` table overrides that at runtime with no redeploy. A route behind a
disabled flag returns 404 — a switched-off feature should look absent, not forbidden.

## API overview

Routes marked 🔒 require an `Authorization: Bearer <token>` header. The token
comes from register or login and is valid 30 days. The caller's identity always
comes from the token, never from the URL or request body.

### V1 — `/api`

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | /api/health | — | Health |
| GET | /api/config/public | — | Prices, free limit, billing availability |
| GET | /api/services | — | Salon ideas |
| GET | /api/salon-ideas | — | Salon suggestions (no pay) |
| GET | /api/quiz/questions | — | Stylist quiz questions |
| POST | /api/scan/preview | — | Free teaser: tone + top colours, saves nothing |
| POST | /api/users | — | Create account (email + password + invite required) |
| POST | /api/auth/register | — | Register, returns token |
| POST | /api/auth/login | — | Login, returns token (rate limited) |
| GET | /api/users/me | 🔒 | Own profile |
| PUT | /api/users/me | 🔒 | Update own profile |
| POST | /api/scan/analyze | 🔒 | Full coach analysis (quota enforced) |
| GET | /api/scan/history | 🔒 | Own scan history |
| GET | /api/scan/trends | 🔒 | Legacy historical scan data (not shown in the Phase 1 UI) |
| POST | /api/quiz/submit | 🔒 | Submit stylist quiz |
| POST | /api/plans/style | 🔒 | Occasion style plan |
| GET | /api/recommendations/history | 🔒 | Own past plans |
| POST | /api/subscription/create-order | 🔒 | Refused while billing is unavailable |
| POST | /api/subscription/confirm | 🔒 | Refused while billing is unavailable |
| GET | /api/subscription/status | 🔒 | Own plan status |

### V2 — `/api/v2`

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | /api/v2/health | — | Health including PostgreSQL |
| GET | /api/v2/config | — | Billing availability, media rules, feature flags |
| GET | /api/v2/me | 🔒 | Profile, account status, consent, usage |
| GET | /api/v2/consent | 🔒 | Current consent state |
| POST | /api/v2/consent | 🔒 | Grant or withdraw consent |
| POST | /api/v2/media/upload | 🔒 | Upload an inventory photo (validated, owned) |
| GET | /api/v2/media/{id} | 🔒 | Asset metadata |
| GET | /api/v2/media/{id}/content | 🔒 | Asset bytes |
| DELETE | /api/v2/media/{id} | 🔒 | Erase the bytes and mark deleted |
| GET | /api/v2/jobs/{id} | 🔒 | Status of a long-running run |
| GET | /api/v2/privacy/export | 🔒 | Everything we hold about you, as JSON |
| DELETE | /api/v2/account | 🔒 | Erase stored photos, request closure |
| GET/PATCH | /api/v2/profile | 🔒 | Read or explicitly update the appearance digital twin |
| POST | /api/v2/profile/baseline-analysis | 🔒 | Optional transient-photo baseline; creates observations only |
| GET | /api/v2/profile/attributes | 🔒 | Attributes with source, confidence and verification |
| GET | /api/v2/profile/observations | 🔒 | Reviewable inferred values |
| POST | /api/v2/profile/observations/{id}/confirm | 🔒 | Confirm an observation |
| POST | /api/v2/profile/observations/{id}/reject | 🔒 | Reject and retain observation history |
| PATCH | /api/v2/profile/observations/{id} | 🔒 | Edit or mark an observation not sure |
| GET | /api/v2/onboarding/status | 🔒 | Resume progressive onboarding |
| POST | /api/v2/onboarding/step | 🔒 | Save or skip one onboarding step |
| POST | /api/v2/onboarding/complete | 🔒 | Finish and return the first useful result |

## Errors

Every error returns the same shape, which the app already understands:

```json
{
  "detail": {
    "code": "ANALYSIS_UNAVAILABLE",
    "message": "We could not analyse this image reliably.",
    "retryable": true,
    "request_id": "8f2c...",
    "allowance_consumed": false,
    "guidance": ["Face a window or other soft light — avoid a bright light behind you"]
  }
}
```

Codes: `ANALYSIS_UNAVAILABLE`, `CONSENT_REQUIRED`, `UNSUPPORTED_MEDIA_TYPE`,
`MEDIA_TOO_LARGE`, `SUBSCRIPTIONS_UNAVAILABLE`, `FEATURE_UNAVAILABLE`,
`VALIDATION_FAILED`, `NOT_FOUND`, `INTERNAL_ERROR`, plus the existing V1 codes
`SCAN_LIMIT`, `AI_RATE_LIMIT`, `PREVIEW_LIMIT` and the `INVITE_*` family.

Every response carries an `X-Request-Id` header, echoed in the error body, so a user's
screenshot is enough to find the request in the logs.

## Privacy

- **Explicit consent comes first.** Signed-in users record consent through
  `/api/v2/consent`; a signed-out preview includes a per-request consent answer. Both scan
  routes refuse the image before provider or allowance work when consent is missing.
- **Face photos are never stored.** Scan images are transient provider input only;
  `scan/analyze` truncates the image to 83 characters before saving a scan record. The
  media API rejects analysis photos entirely. Both boundaries are covered by tests.
- Photos uploaded to your own collection are stored until you delete them.
- Optional onboarding photos follow the same transient rule: only structured,
  reviewable observations are retained. The image itself is never added to profile or
  onboarding state.
- `GET /api/v2/privacy/export` returns everything from both databases as JSON.
- `DELETE /api/v2/account` erases stored photos immediately and marks the account for
  closure. Profile and history removal follows within 30 days.
- Deletions and exports are recorded in `audit_events` with a hashed source address —
  enough to investigate, not a location log.

## Documentation

- [docs/V2_ARCHITECTURE_AND_PHASE_PLAN.md](docs/V2_ARCHITECTURE_AND_PHASE_PLAN.md)
- [docs/v2/PHASE_1_AUDIT.md](docs/v2/PHASE_1_AUDIT.md)
- [PHASE_1_REPORT.md](PHASE_1_REPORT.md)

## Disclaimer

Guidance is for general wellness and personal style from photos — not medical advice.

## Appearance digital twin

Phase 2 adds a structured profile that separates what you entered from what a photo or
future integration suggested. Every value carries its source, confidence, verification
state, timestamps and—where applicable—the AI run that produced it. AI suggestions enter
an observation inbox and cannot overwrite a confirmed value. Rejected observations stay
in history and only new evidence may create a new suggestion.

Onboarding starts with “What are you preparing for?” and can be completed with that one
goal; style, fit, lifestyle and photo steps are optional and resumable. Weight is neither
requested nor required. “My Appearance” shows decision readiness instead of a generic
completion percentage.
