# CLAUDE.md

> **Read `PRODUCT_CONSTITUTION.md` first, in every session, before deciding anything.**
> It governs what this product is, what it must never become, and what is already built
> and must not be rebuilt. On any product question it outranks this file; this file stays
> the map of the code as it actually is today.
>
> **Writing or changing any user-facing string? `LEGAL_RULES.md` governs it.**
> Every legal risk in this product lives in sentences.
>
> **Touching Open Food Facts data? Read `docs/architecture/ODBL_DATA_WALL.md` first.**
> Their licence is share-alike. Combining their data with ours into one database
> obliges us to publish ours — the whole product — openly. Two stores, joined only
> in memory, on barcode. Never write one into the other.

GlamGenius is a decision engine for everything that enters or touches the human body —
food, cosmetics, supplements, cookware and salon upkeep. The customer scans a product;
the app decides Buy / Wait / Skip and says why. India only. It is **not** a diagnosis
app. `PRODUCT_CONSTITUTION.md` is the authority on what it is; this file describes the
code that exists so far, which is a body-and-appearance manager built on what the
customer already owns.

This file is the map of what the repository actually is. The rules below are not
suggestions; they hold for every task unless the user changes them here.

---

## 1. The stack, as it is today

| Piece | What it actually is |
| --- | --- |
| Database | **PostgreSQL 16** via SQLAlchemy 2.x (async, `asyncpg`). **There is no MongoDB.** |
| Migrations | **Alembic** (`backend/alembic.ini`, `backend/migrations/`) |
| API | **FastAPI**, one app, every route under `/api/v2` |
| Auth | **Supabase Auth** — JWTs verified against JWKS. No local password store |
| Media | **Supabase Storage** in production, local filesystem adapter for dev/test |
| AI | **Google Gemini** (`google-genai`), reached only through the AI gateway domain |
| App | **Expo / React Native** (`frontend/`), expo-router, TypeScript, Zustand |
| Monitoring | **Sentry** (optional — a missing DSN is a no-op, never a startup failure) |

The Mongo runtime is gone for good: `pymongo`, `motor.motor_asyncio` and `MONGO_URL` are
**forbidden strings** in backend source, enforced by `backend/tests/test_no_legacy_terms.py`
and the "Legacy and payment absence" job in `.github/workflows/ci.yml`. Never reintroduce
them.

Billing is different, and the distinction matters. The **Razorpay integration** was
removed and is not coming back, so `razorpay`, `checkout` and `paywall` remain forbidden
strings. But the business model **does** include paid family subscriptions and health
modes, so payment tables are not permanently prohibited — see `PRODUCT_CONSTITUTION.md`,
"Free vs paid". What is true today is narrower: the same tests still reject a
`subscription`, `payment` or `invoice` table (`backend/tests/test_schema_regression.py`)
and the same CI job still greps for billing terms. Those gates were written for the
Razorpay removal, and they must be revisited deliberately — as their own change, with its
own review — before any billing work can land. Do not quietly weaken them in passing, and
do not treat them as a statement that the product will never charge.

## 2. Where things live

```
backend/
  server.py                  # thin composition root (~100 lines): Sentry, router, CORS, startup checks
  alembic.ini                # script_location = migrations; the URL comes from POSTGRES_URL
  pytest.ini                 # testpaths = tests, session-scoped event loop
  pyproject.toml             # Ruff config (line-length 120, py311)
  requirements.txt
  migrations/versions/       # 23 Alembic revisions, one linear chain
  app/
    config.py                # every env var, plus validate_production_configuration()
    release.py               # `python -m app.release` — lock, migrate, seed, verify
    release_readiness.py     # `python -m app.release_readiness` — honest readiness report
    api/v2/                  # 24 route modules mounted by api/v2/__init__.py (health.py is an empty orphan)
    domains/                 # 37 domain packages — see §3
    workers/                 # notifications.py, account_deletion.py — see §6
    bootstrap/               # reference_data.py — versioned seed catalogue
    shared/
      database/              # sql.py (engine/session), base.py (Base, mixins), registry.py
      security/              # supabase_auth.py, deps.py (get_current_account, require_flag)
      flags/                 # feature flags: DB row > V2_FEATURES env > stable beta default
      errors/                # exceptions, handlers, codes
      observability/         # logging, request_id, sentry_bootstrap, sentry_privacy
      validation/media.py
  tests/                     # 102 pytest modules (~1,800 tests) + conftest.py
frontend/
  app/                       # expo-router routes; (tabs)/ is Today · Style · Care · Plan · You
  src/services/              # api.ts, apiV2.ts (typed V2 client), supabase.ts, notify.ts
  src/strings/               # every user-facing string, keyed. No copy lives in a component
  src/components/            # per-area component folders
  src/store/                 # zustand stores
  src/__tests__/             # 42 Jest suites (~415 tests)
docs/                        # architecture, engineering checklists, ADRs, operations, reports
```

`backend/server.py` is **not** where the logic is. It wires the app together and runs
startup checks. Real work lives under `backend/app/`.

## 3. The domains

All 37 packages under `backend/app/domains/`:

| Domain | Owns |
| --- | --- |
| `identity` | The `accounts` table — one row per Supabase user |
| `consent` | What the user agreed to, and when. Enforced server-side |
| `privacy` | Versioned data export + the account-deletion job state machine |
| `audit` | Audit trail for sensitive operations |
| `analytics` | Product analytics events |
| `beta_access` | Invites, redemptions, monthly beta usage limits (cost control, not billing) |
| `media` | Uploads, ownership, deletion; storage adapters (`storage/supabase.py`, `storage/local.py`) |
| `ai_gateway` | The one controlled path to Gemini. Every AI run is recorded |
| `profile` | The appearance digital twin: attributes, observations inbox, onboarding, baseline |
| `inventory` | All seven categories: Wardrobe, Shoes, Accessories, Skin Care, Hair Care, Perfumes, Supplements. **Multi-item capture: one shelf photo, one tap per candidate** |
| `scan` | Photo analysis history. **No image is ever stored** — see §4 |
| `quiz` | Versioned style quiz questions and submissions |
| `recommendation` | Occasion styling and the decision engine (candidates, ranking, ROI, explanation) |
| `purchase` | "Should I buy this?" — care, fragrance and style purchase verdicts, decision memory |
| `routines` | Routine compilation, shelf, perfume, adherence, and `safety.py` — the medical boundary |
| `care` | Care context, deterministic decisions, guidance, home care, **maintenance timing (VC-06)**, the ten environment rules and their precedence |
| `supplements` | **Owned-supplement label facts, component overlap, safety boundaries (VC-07)** |
| `nutrition` | Opt-in appearance-adjacent food context. No diets, no calories, no RDA maths. **`grading/` is the Indian food grading engine: six gates in order, never a weighted average** |
| `evidence` | Release-owned evidence provenance, claims, rule support and applicability |
| `reference` | Versioned global reference data written by the seed bootstrap |
| `planning` | **Today engine, weekly planner, events, calendar sync, weather/air quality (Indian NAQI from CPCB breakpoints), notifications** |
| `progress` | Explainable metrics, milestones, comparison and controlled long-term memory |
| `system` | `system_worker_status` — worker heartbeats and last-error state |
| `off` | Store A: the Open Food Facts copy, behind the ODbL wall — see §5a |
| `product` | Barcode scanning: anonymous device identity, our product record, scan events, label transcription |
| `substance_interpretation` | Step 7C category-specific projection of published evidence onto already-resolved canonical substance identities; no identity resolution, scoring or verdicts |
| `personal_lens` | Step 8A read-only FOR YOU context projection from trusted user-declared non-medical Profile facts; hard-handoff first; no score, verdict or evidence matching |
| `personal_applicability` | Step 8B read-only join of exact Step 7C substance identities, Step 8A body facts and reviewed published personal-applicability evidence; no inference, score, verdict or persistence |
| `personal_decision_semantics` | Step 8C pure deterministic mapping from an exact Step 8B applicable claim version to an explicit reviewed supporting or cautionary direction; production registry deliberately empty; no prose parsing, strength arithmetic, counting, aggregation, score or verdict |
| `personal_decision_aggregation` | Step 8D pure deterministic aggregation of exact Step 8C semantic mappings into distinct rule provenance, structural mapping coverage and an unweighted signal set; duplicates do not create weight; no score, conflict resolution or verdict |
| `personal_decision_policy` | Step 8E pure deterministic exact versioned policy over eligible Step 8D state; policy matches the exact semantic-rule identity/version set and structural upstream gap flags; no generic signal→action mapping; production registry empty; no explanation, API or persistence |

Every ORM model module must be imported in `backend/app/shared/database/registry.py`.
A model that is not imported there is invisible to Alembic and its table is silently
never created.

## 4. Privacy: face photos are never stored

`POST /api/v2/scan/analyse` (handler `analyse_scan` in `backend/app/api/v2/scan.py`) sends
the image to Gemini and then **drops it**. There is no truncation step and no image
column: `backend/app/domains/scan/models.py` persists only the structured `analysis`,
the provider/model/latency provenance and a failure reason. Adding any column, field or
log line that would retain image bytes — whole, truncated or hashed — is wrong.

The same rule applies to the profile baseline path
(`backend/app/domains/profile/baseline.py`) and to every inventory, purchase and
fragrance extraction path: the base64 goes to the gateway and nowhere else.

Related invariants worth knowing before touching that route:

- Consent is checked server-side against the recorded consent row, never a request field.
- A provider failure does **not** consume the caller's beta allowance.

## 5. Language: observations, never diagnosis

Never add medical or diagnostic language anywhere — prompts, screens, docs or tests.
Observations only ("looks dry"), never conditions ("you have eczema").

This is enforced in code, not just by convention:

- `backend/app/domains/routines/safety.py` holds `DIAGNOSTIC_TERMS`, `PROFESSIONAL_BOUNDARY`
  and `ROUTINE_DISCLAIMER`, and `narrative_is_safe` screens every AI-written string before
  it is stored or shown. It fails closed.
- `backend/app/domains/routines/safety_classifier.py`, `.../nutrition/safety.py` and
  `.../supplements/engine.py` carry the same boundary for their areas.
- Disclaimers are per-domain constants (`ROUTINE_DISCLAIMER`, `SUPPLEMENT_DISCLAIMER`,
  `NUTRITION_DISCLAIMER`, and the literal `"disclaimer"` keys in planning, profile and
  progress responses). They stay in the response. Do not remove or soften one.

No **personalised** dosage advice. Stating what a label declares, and what a published
source says about absorption, is required — it is the product's core differentiator.
Telling a specific person what to take is prohibited.

No calorie counting. No deficiency claims.

The medical boundary is not the only one a sentence can cross. `LEGAL_RULES.md` holds
the six writing rules for every user-facing string — state rather than characterise,
cite rather than assert, compare products rather than advise the person, never mock a
brand, state missing data rather than fill it, and show the source with every negative
statement. Read it before writing or changing any copy.

## 5a. The ODbL wall: two stores that never become one

Open Food Facts data is licensed under ODbL, which is **share-alike**. If their
database and ours are combined into one derived database, we are obliged to publish
the combined thing openly — the absorption knowledge base, the thresholds, the
scores, the decision memory. The whole product, given away.

So they stay apart:

- **Store A** (`backend/app/domains/off/`) holds Open Food Facts data and nothing
  else: barcode, product name, brand, ingredients, nutrition values, categories,
  images. Its own `MetaData`, its own engine (`OFF_DATABASE_URL`), outside the main
  Alembic chain.
- **Store B** is everything else — the rest of this repository.
- They meet **only in memory, at query time, on barcode**
  (`app/domains/off/join.py`). The pair is discarded with the response.

Never do any of these. Each one creates a derived database:

- Add a column of ours to a Store A table, or an Open Food Facts field to one of ours.
- Add a foreign key in either direction between the stores.
- Build a view, cache or table spanning both.
- Copy `nutriments` into a scoring table to make a query faster. This is the one
  that looks like an optimisation and is a licence breach.

Four things enforce it: separate metadata, a separate connection, the `OFF_FIELDS`
allowlist in `app/domains/off/wall.py`, and a write guard on the Store A session.
`backend/tests/test_odbl_data_wall.py` holds them up. **If one of those tests fails,
do not adjust the test** — the failure means the change would put the product under
ODbL.

Attribution is a licence condition, not copy: every surface showing this data renders
"Contains information from Open Food Facts, made available under the Open Database
License (ODbL)", verbatim. Every API call carries an identifying User-Agent, which
Open Food Facts requires.

Full explanation: `docs/architecture/ODBL_DATA_WALL.md`.

## 6. Running things

### Tests — the definition of done

Everything runs in containers; the host needs neither Python nor Node:

```bash
docker compose -f docker-compose.test.yml run --rm backend-tests    # alembic upgrade + check + pytest
docker compose -f docker-compose.test.yml run --rm frontend-tests   # typecheck + lint + jest
```

Directly, against a disposable PostgreSQL:

```bash
cd backend
export POSTGRES_URL=postgresql+asyncpg://glamgenius:glamgenius@localhost:5432/glamgenius_test
alembic upgrade head
pytest -q tests
ruff check .          # zero warnings is the merge gate
```

```bash
cd frontend
yarn install --frozen-lockfile
yarn typecheck
yarn lint --max-warnings=0
yarn test --ci --watchAll=false
```

The suite needs a **real** PostgreSQL — faking it would not prove the migration works.
Supabase Auth and Gemini are stubbed by fixtures in `backend/tests/conftest.py`; no test
makes a live call to either.

There is no `backend_test.py`. The backend suite is the 92 modules in `backend/tests/` —
about 1,330 tests, roughly six minutes against a local PostgreSQL.

### The API

```bash
docker compose up --build            # PostgreSQL + API + account-deletion worker
# or
cd backend && uvicorn server:app --reload --port 8000
```

Health: `GET /api/v2/health` (served from `backend/app/api/v2/config.py`, not `health.py`).

### Migrations

Alembic, one linear chain, `backend/migrations/versions/`. The current head is
`b8c9d0e1f2` (Step 8B personal-applicability evidence vocabulary).
`0001_initial_glamgenius_v2.py` is the consolidated greenfield baseline.

```bash
cd backend
alembic revision --autogenerate -m "short description"
alembic upgrade head
alembic check              # must report no drift against the ORM metadata
alembic downgrade base && alembic upgrade head    # CI runs this round-trip
```

Rules: the URL always comes from `POSTGRES_URL`, never from `alembic.ini`. A merged
migration is frozen — correct it with a new revision, never by editing the old file.
`down_revision` must point at the real current head. Every migration needs a working
`downgrade()`. `backend/migrations/**` is owner-review-only in CODEOWNERS. The full bar
is `docs/engineering/CHECKLIST_MIGRATION.md`.

### The notification worker

It is a **scheduled batch, not a daemon**, and this repository does not schedule it:

```bash
python -m app.workers.notifications        # one cycle, run from backend/
```

A host cron (or systemd timer, or managed scheduled job) must invoke it **once per
hour**. Each run finds accounts with notifications and native push enabled, compiles
Today through the same compiler `GET /api/v2/today` uses, and queues at most the one
delivery that account's preferences allow. It is repeat-safe (the delivery is claimed and
committed before the Expo call), isolated per account (one failure rolls back and the
batch continues), and never sends late catch-ups. Details: `docs/OPERATIONS.md` §6.

The other worker is long-running and **is** in `docker-compose.yml`:

```bash
python -m app.workers.account_deletion     # polls deletion jobs, SELECT … FOR UPDATE SKIP LOCKED
```

Seeding reference data: `python -m app.bootstrap.reference_data` (idempotent).
Full release sequence: `python -m app.release`.

## 7. Standing rules

**Dependencies and scope.** Never add a dependency unless the task names it — ask first.
Never change, rename or reformat files the current task doesn't name.

**Tests.** Never delete, skip or weaken an existing test to make it pass.

**Secrets.** Environment variables only (see `env.example` and `backend/app/config.py`).
Never hardcoded, never logged, never returned in a response.

**Outside services.** For any outside service — Supabase, Gemini, Google Calendar,
Open-Meteo, Expo Push, Sentry — read the current official docs before writing code. Never
write integration code from memory. Google Calendar and Open-Meteo are optional and off
by default; `validate_production_configuration()` in `backend/app/config.py` refuses to
start production with a half-configured one.

**Definition of done.** A task is finished only when the verification commands in §6 run
clean and the backend suite passes. "The code looks right" is not finished.

**Communication.** The user is not a coder. Explain everything in plain English. If a
decision needs their input, ask one clear question with the options spelled out.

## 8. Where to read more

- `PRODUCT_CONSTITUTION.md` — what the product is and is not. Read it first
- `LEGAL_RULES.md` — how every user-facing string must be written
- `README.md` — product overview and quick start
- `docs/OPERATIONS.md` — running it in production, including the worker schedule
- `docs/engineering/` — review policy, branching, checklists (migration, privacy, security,
  AI safety, evidence, external integration, mobile UX)
- `docs/architecture/ODBL_DATA_WALL.md` — why Open Food Facts data lives in its own
  database, and what would happen if it did not
- `docs/engineering/adrs/` — why the non-obvious choices were made
- `docs/reports/` — the phase and stabilisation reports (historical records)
- Feature-area specs: `docs/VC-05_GOOGLE_CALENDAR.md`, `docs/VC-06_MAINTENANCE.md`,
  `docs/VC-07_SUPPLEMENT_SAFE_UTILITY.md`, `docs/VC_08_FINAL_INFORMATION_ARCHITECTURE.md`,
  `docs/VC_09_CROSS_DOMAIN_NOTIFICATIONS.md`

`docs/engineering/ARCHITECTURE.md` still describes the pre-cutover topology (Mongo, a V1
route surface, Razorpay). Trust this file over it.
