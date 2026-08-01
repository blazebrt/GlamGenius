# GlamGenius V2 — Architecture & Phase Plan

**Status:** Proposal awaiting product-owner approval. No application code has been changed.
**Date:** 2026-08-01
**Baseline commit:** `e4bed57` (main) — *"Merge pull request #10 … Make GlamGenius invite-only for private testing"*

---

## 1. Plain-English summary

GlamGenius today is a photo-based advice app: you take a picture, the AI writes you a
styling and skin/hair plan, and the app saves that plan. It knows nothing about what you
actually own.

The V2 mission is a different product: a **Personal Appearance Operating System** that
knows your wardrobe, shoes, accessories, beauty shelf, hair shelf, perfumes and
supplements, and uses that knowledge to answer "what do I wear today", "am I ready for
Saturday's wedding", "should I buy this", and "what am I not using".

That is not a re-skin. It requires a real inventory database, a real metrics engine, and a
much stricter relationship with the AI. It is also **not** a rewrite — the existing app
keeps running the whole way through.

The plan below does it in twelve phases. Nothing is deleted until its replacement is
working. Each phase ships on its own, behind a feature flag, with its own tests and its
own report.

---

## 2. Where the code is today

Verified by reading the repository at commit `e4bed57`, not assumed.

### 2.1 Backend

```
backend/
  server.py        FastAPI app, /api prefix, CORS, Mongo index creation on startup
  settings.py      All env config, one module, no Pydantic Settings
  database.py      Motor client + db handle (6 lines)
  models.py        Pydantic request/response models (Mongo-shaped, no ORM)
  security.py      bcrypt + legacy SHA-256 verify, JWT, login lockout, preview quota,
                   monthly scan quota, user sanitising
  ai.py            Gemini client, prompts, JSON parsing, AI rate limits, fallbacks
  invites.py       Invite-code validation and consumption
  catalog.py       Static salon-idea catalogue
  routes/          health, users, scan, quiz, plans, recommendations, services,
                   subscription, admin
```

**What is good and worth keeping:** the route split is already clean and domain-shaped.
`get_current_user` is a single, correct auth gate — identity comes from the token, never
from the URL or body. Rate limiting, login lockout, invite gating and CORS allow-listing
are all real and thoughtfully commented. The image-truncation privacy rule is enforced at
the one place it matters.

**What is missing for V2:** no PostgreSQL, no ORM, no Alembic, no migrations of any kind,
no object storage, no Redis, no job queue, no outbox, no feature flags, no `/api/v2`.

### 2.2 Frontend

```
frontend/
  app/                 expo-router file routes
    (auth)/welcome     sign-in / sign-up
    (tabs)/            home, services, scan-tab, history, profile
    scan.tsx           32 KB — the largest screen by far
    get-advice, recommendations, style-quiz, subscription, service-details
  src/
    services/api.ts    axios instance, token interceptor, 401 handling, error helpers
    services/notify.ts
    store/userStore.ts Zustand — session, profile, subscription refresh
    store/planStore.ts Zustand — small
    theme/colors.ts    Full design system: colours, Playfair/Inter type scale,
                       spacing, radius, web-safe shadows
    components/ErrorBoundary.tsx
```

TypeScript is already `strict: true`. Zustand, expo-router and a genuine design system are
all in place — the UX rebuild has a real foundation and does not start from zero.

**What is missing:** there is exactly one shared component (`ErrorBoundary`). Every screen
styles itself inline. There is no component library, no typed API client (axios calls are
untyped and scattered through screens), and no tests of any kind.

### 2.3 Tests and tooling

| Thing | Reality |
|---|---|
| `backend_test.py` | 64 KB script using `requests` + `PIL`. Needs a **live server, live Mongo and a live Gemini key**. Not pytest, not runnable in CI as-is. |
| `tests/` | Contains one empty `__init__.py`. |
| Frontend tests | None. No Jest, no `jest-expo`, no React Native Testing Library. |
| Lint | `expo lint` exists; no CI runs it. |
| Type check | No `tsc --noEmit` script. |
| CI | No workflow files. |
| `test_result.md` | Artefact of a previous agent harness. Not a test runner. |

### 2.4 Local machine constraints

Python and Node are **not installed** on the development machine (`python` resolves to the
Microsoft Store stub; `node` and `npm` are absent). Docker Desktop **is** installed.

**Decision (owner-approved): all tests run in Docker.** Nothing gets installed on the
host. This is recorded in §7.

---

## 3. Where the current code conflicts with the V2 mission

These are pre-existing behaviours, not regressions. They are listed here so they are fixed
deliberately, in a named phase, rather than silently.

### C1 — The AI fabricates results and charges the user for them

`_fallback_coach()` in `backend/ai.py:277` returns a hardcoded plan — *"wheatish", "warm",
"deep teal", "mustard", skin_score 72* — and `analyze_image_with_gemini` returns it
whenever Gemini is unconfigured **or throws any exception** (`ai.py:590`, `ai.py:594`).
The caller cannot distinguish it from a real analysis.

`backend/routes/scan.py:125` then increments `scans_used_this_month` regardless. So a user
whose analysis failed is shown invented facts about their own face **and** loses one of
their monthly checks. Those invented facts are then written into their profile
(`scan.py:104-122` copies `skin_tone`, `undertone`, `face_shape`, `skin_type` from the
response into the user record).

Directly violates: *"Never return a generic fallback as if it were a real personalised
result"*, *"do not consume user allowance"*, *"do not save fabricated observations"*,
*"AI must not silently create facts"*.

**Fixed in Phase 1.**

### C2 — A universal appearance score exists

`wellness_scores.overall_score` is requested from the model (`ai.py:91-97`), stored on
every scan, returned by `GET /api/scan/history`, and plotted over time by
`GET /api/scan/trends`. `skin_score`, `hair_score` and `style_readiness_score` sit
alongside it.

Directly violates: *"Do not create one universal attractiveness or appearance score"*, and
every one of the *"every displayed number must have a deterministic formula, a metric
version, an explanation, visible inputs, missing-data handling"* requirements — these
numbers are invented by a language model with none of those properties.

**Fixed in Phase 3**, with a migration path for existing history (§8.3).

### C3 — No schema validation on AI output

`_parse_llm_json` (`ai.py:266`) strips code fences and calls `json.loads`. Whatever shape
comes back is passed straight to the frontend. A malformed or partial response either
throws (→ C1's fallback) or silently returns a half-empty object.

Violates: *"All AI outputs must pass strict schema validation before being returned to the
frontend"*.

**Fixed in Phase 1.**

### C4 — Two competing rulebooks

The repository's own `CLAUDE.md` predates the V2 master instructions. Most of it still
holds and is worth keeping (the image-truncation rule, the no-medical-language rule, the
never-weaken-a-test rule). One clause needs reconciling: *"Never add a new dependency
unless the task names it"* — the V2 instructions do name PostgreSQL, pgvector, Redis and
S3-compatible storage, so those are authorised, but the file should say so explicitly.

**Fixed in Phase 0.**

### C5 — Inventory does not exist

There is no model, table, route or screen for wardrobe, shoes, accessories, beauty, hair,
perfumes or supplements. This is the single largest gap between the current product and
the mission. Everything in the mission statement — utilisation, value to recover, duplicate
purchase detection, occasion preparedness — depends on it.

**Built in Phase 2.**

---

## 4. Target V2 architecture

### 4.1 Shape: modular monolith

One FastAPI application, one deployable, internally divided by domain. No microservices.

```
backend/
  app/
    core/            settings, logging, errors, feature flags, clock, ids
    db/
      mongo.py       existing Motor handle (V1, unchanged)
      sql.py         SQLAlchemy async engine + session (V2)
      migrations/    Alembic
    storage/         ObjectStorage protocol + local + S3 adapters
    events/          domain-event outbox: publish, relay worker, handlers
    jobs/            queue abstraction + Redis-backed worker
    ai/
      client.py      Gemini transport (moved from ai.py, unchanged behaviour)
      envelope.py    provenance wrapper — every AI output is wrapped
      schemas/       versioned Pydantic response schemas
      prompts/       versioned prompt templates
      errors.py      typed AI failures (never a fabricated result)
    metrics/
      registry.py    metric definitions keyed by name + version
      definitions/   one module per metric, each a pure function
    domains/
      inventory/     models, repo, service, routes  (7 categories)
      outfits/
      routines/
      planning/
      shopping/
      memory/
      entitlements/
    api/
      v1/            thin re-export of today's routers — behaviour frozen
      v2/            new routers, all behind feature flags
  server.py          mounts /api (v1) and /api/v2
```

`backend/routes/*.py` keeps working untouched behind `api/v1`. Nothing about the running
app changes on day one.

### 4.2 Two databases, on purpose

| | MongoDB (V1) | PostgreSQL (V2) |
|---|---|---|
| Owns | users, auth, scans, style_plans, invites, rate-limit counters | inventory, outfits, routines, plans, metrics, AI observations, outbox |
| Status | Authoritative during transition. Not deleted. | Authoritative for everything new. |
| Migration | None in Phase 0–8. Considered only in Phase 9+. | Alembic from day one. |

**Identity bridge.** V2 tables key off `user_id UUID`, which is the same UUID string V1
already stores in Mongo's `users.id`. On the first V2 request from a user, a
`bridge_users` row is upserted from the token-resolved Mongo document. Auth stays entirely
V1 (JWT + Mongo) until Phase 9. No dual-write, no sync job, no consistency problem — one
direction only, and only for the foreign-key anchor.

**Why not one database.** Migrating auth and billing is high-risk work that delivers zero
user-visible value. Inventory is relational (items → images → usage events → outfits) and
belongs in Postgres. Doing both at once is a big-bang migration, which the instructions
forbid.

### 4.3 API versioning

- `/api/*` — V1, frozen. Bug fixes only. No new features. Removed no earlier than Phase 9,
  and only after the app has shipped on V2 for a full release cycle.
- `/api/v2/*` — everything new.
- `GET /api/v2/features` returns the flag set so the app can hide unfinished modules
  without shipping a new binary.
- Feature flags come from one env var, `V2_FEATURES` (comma-separated), read through
  `core/flags.py`. Default is empty — a fresh deployment exposes nothing new.

### 4.4 Storage abstraction

```python
class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> StoredObject: ...
    async def get_url(self, key: str, expires_in: int = 3600) -> str: ...
    async def delete(self, key: str) -> None: ...
```

`LocalFilesystemStorage` for development (writes under `STORAGE_LOCAL_PATH`, served by a
signed-path route). `S3CompatibleStorage` for production. Chosen by `STORAGE_BACKEND` env.

Inventory photos are user-owned product photos, not face photos — they are stored properly.
**The face-image truncation rule is untouched:** `scan/analyze` continues to truncate to 80
characters, and no V2 code path stores a face image.

### 4.5 Redis, jobs, outbox

- **Redis** — cache for computed metrics and for rate-limit counters (which currently live
  in Mongo with TTL indexes; migrating them is a Phase 6 nicety, not a requirement).
- **Job queue** — a thin `JobQueue` protocol over Redis lists, with an in-process worker
  started by a separate compose service. Deliberately not Celery: one dependency, no broker
  configuration, and the workload (thumbnailing, metric recomputation, expiry sweeps) is
  small and idempotent.
- **Outbox** — `outbox_events` table written in the *same transaction* as the domain change.
  A relay loop publishes to the queue and marks rows dispatched. This is what makes
  "recompute Wardrobe Readiness after an item is added" reliable instead of best-effort.

### 4.6 The AI integrity layer

Every AI call returns an envelope. There is no path that returns bare model output.

```python
class AIEnvelope(BaseModel, Generic[T]):
    data: T                        # already validated against a versioned schema
    source: Literal["gemini", "deterministic", "user_provided"]
    confidence: float              # 0.0–1.0
    verification_status: Literal["unverified", "user_confirmed", "user_rejected"]
    model_version: str             # e.g. "gemini-2.5-flash"
    prompt_version: str            # e.g. "coach.v3"
    schema_version: str            # e.g. "coach_analysis.v2"
    created_at: datetime
```

Persisted observations go to `ai_observations` carrying the same seven fields, so any fact
the app ever shows can be traced to the model, prompt and schema that produced it.

**The failure contract.** When analysis fails there is no fallback object. The service
raises `AIAnalysisError` and the route returns a structured error:

```json
{
  "detail": {
    "code": "AI_ANALYSIS_FAILED",
    "reason": "unreadable_image",
    "message": "We couldn't read that photo clearly enough to analyse it.",
    "guidance": [
      "Face a window or other soft light — avoid a bright light behind you",
      "Hold the camera at arm's length, roughly at eye level",
      "Remove sunglasses and pull hair back from your face"
    ],
    "retryable": true,
    "allowance_consumed": false
  }
}
```

The allowance counter is only incremented **after** a validated result exists. Nothing is
written to the profile or to `ai_observations` on failure.

**Deterministic-first.** These never touch an LLM: ingredient conflicts, expiry
calculations, purchase duplication, outfit compatibility, weather rules, wardrobe usage,
every scoring formula, entitlements, routine scheduling. The LLM is used for reasoning,
explanation, summarisation and presentation copy only — and its output is decoration over
deterministic facts, never the source of them.

### 4.7 The metrics engine

No metric is ever computed inline in a route. Every metric is a registered pure function.

```python
@register_metric("wardrobe_readiness", version="1.0.0")
def wardrobe_readiness(inputs: WardrobeReadinessInputs) -> MetricResult:
    ...
```

```python
class MetricResult(BaseModel):
    key: str
    version: str              # bump on any formula change; old snapshots keep their version
    value: float | None       # None when inputs are insufficient — never a fake zero
    display: str
    formula: str              # human-readable, shown in the UI
    inputs: dict[str, Any]    # the actual numbers used, shown in the UI
    missing_inputs: list[str] # what we could not see, and what it would take to fix
    explanation: str
    computed_at: datetime
```

Results are cached in Redis and snapshotted to `metric_snapshots` for progress tracking.
Because the version travels with the snapshot, changing a formula never silently rewrites
a user's history.

**Approved metric set** (from the master instructions, no additions): Wardrobe Readiness,
Wardrobe Utilisation, Outfit Variety, Occasion Preparedness, Routine Consistency, Product
Expiry Risk, Value to Recover, Purchase Efficiency, Inventory Balance, Travel Readiness,
Seasonal Readiness, User-Reported Confidence.

**Explicitly not built:** any composite "appearance score", any single number summarising
the user.

### 4.8 Language rules, enforced not just documented

A lint test asserts that the banned vocabulary — *money wasted, bad wardrobe, failed
routine, ugly, unattractive, poor appearance* — appears in no prompt, no schema field name,
no user-facing string in the backend, and no string literal in the frontend. It runs in the
normal test suite, so a regression fails the build rather than reaching a user.

---

## 5. Inventory data model

The heart of V2. Sketched here so the shape is agreed before Phase 2 starts.

**One `items` table, not seven.** All seven categories share the same lifecycle (acquire →
use → assess value → retire), the same images, the same usage events, and the same metrics.
Seven near-identical tables would mean seven copies of every query and every metric. Type
differences live in a validated JSONB `attributes` column with a per-category Pydantic
schema — so the flexibility does not become a free-for-all.

```
items
  id                uuid pk
  user_id           uuid  (bridge to V1 user)
  category          enum: wardrobe | shoes | accessories | beauty | hair | perfume | supplement
  subcategory       text            -- 'kurta', 'sneakers', 'serum', 'edp'
  name              text
  brand             text
  colors            text[]
  attributes        jsonb           -- category-specific, schema-validated
  purchase_date     date
  purchase_price_minor  integer     -- paise. never a float for money
  currency          char(3) default 'INR'
  opened_at         date            -- beauty/hair/perfume: starts the PAO clock
  expiry_at         date
  pao_months        integer         -- period-after-opening
  status            enum: active | archived | gifted | used_up | to_recover
  created_at, updated_at

item_images        id, item_id, storage_key, width, height, content_hash, created_at
item_usage_events  id, item_id, user_id, used_at, context, source
outfits            id, user_id, name, occasion, season, created_at
outfit_items       outfit_id, item_id, role
routines           id, user_id, kind (skin|hair), time_of_day, cadence, active
routine_steps      id, routine_id, position, item_id, instruction
routine_logs       id, routine_id, user_id, completed_at, skipped_steps
metric_snapshots   id, user_id, metric_key, metric_version, value, inputs jsonb, computed_at
ai_observations    id, user_id, subject_type, subject_id, kind, payload jsonb,
                   source, confidence, verification_status,
                   model_version, prompt_version, schema_version, created_at
outbox_events      id, aggregate_type, aggregate_id, event_type, payload jsonb,
                   created_at, dispatched_at
bridge_users       user_id uuid pk, email_hash, created_at
```

Category-specific `attributes`, illustrative:

- **wardrobe** — `fit`, `fabric`, `sleeve`, `neckline`, `formality`, `season[]`, `pattern`
- **shoes** — `heel_height_mm`, `closure`, `sole`, `formality`, `weather_suitability[]`
- **accessories** — `metal`, `stone`, `size`, `formality`
- **beauty / hair** — `inci_ingredients[]`, `spf`, `volume_ml`, `texture`, `actives[]`
- **perfume** — `concentration`, `notes[]`, `longevity_hours`, `season[]`, `occasion[]`
- **supplement** — `form`, `serving_size`, `servings_total`, `servings_remaining`

`inci_ingredients[]` is what makes deterministic ingredient-conflict checking possible in
Phase 7 without asking a language model anything.

**pgvector:** not introduced in Phase 2. It earns its place only when semantic
retrieval creates clear value — realistically Phase 4 (finding compatible items by
description) and Phase 8 (memory recall). Deferred until then, with a note in the Phase 4
plan rather than speculative infrastructure now.

---

## 6. Navigation and UX target

Current tabs → target tabs:

| Today | Inventory | Style Me | Planner | You |
|---|---|---|---|---|
| new | new | evolves from `scan-tab` + `get-advice` | new | absorbs `profile` + `history` |

Inside **You**: My Appearance · Improve · Progress · Memory · Subscription · Privacy · Settings.

Design commitments, taken from the master instructions and applied concretely:

- **One primary question per screen.** Today asks "what do I wear?" and nothing else. It
  is not a dashboard of twelve cards.
- **Progressive disclosure.** Metric explanations, formulas and inputs live behind a tap on
  the number, not permanently on screen.
- **No chat-first homepage.**
- **Confidence is visible.** Anything AI-sourced carries its confidence and a way to
  confirm or reject it, which is what moves `verification_status` off `unverified`.
- The existing `src/theme/colors.ts` design system is kept — Playfair Display headings,
  Inter body, the warm neutral palette. It is already premium, minimal and editorial. What
  is missing is a component library, built in Phase 2 as inventory needs it and reused
  onward, so no screen re-invents a card, a field or an empty state.

A note on the existing screens: `app/scan.tsx` is 32 KB of screen-local logic. It is not
refactored speculatively — it gets absorbed into Style Me in Phase 4, when there is
somewhere for its logic to go.

---

## 7. Testing strategy (Docker-only)

Owner decision: **nothing is installed on the host machine.**

```
docker-compose.yml         dev stack: mongo, postgres, redis, backend, worker
docker-compose.test.yml    ephemeral stack + a test runner service
```

**Backend.** pytest + `pytest-asyncio`, driving the app through `httpx.ASGITransport` — no
live server, no bound port. Postgres and Mongo are real containers, torn down per run.
Gemini is faked at the transport boundary by default; a marked subset can run against the
real API when a key is supplied.

```bash
docker compose -f docker-compose.test.yml run --rm backend-tests
```

**Frontend.** `jest-expo` + React Native Testing Library in a `node:20` container, with
`tsc --noEmit` and `eslint` in the same service.

```bash
docker compose -f docker-compose.test.yml run --rm frontend-tests
```

**`backend_test.py` is kept, not deleted.** It becomes an opt-in end-to-end smoke test
against a running stack. Its assertions get ported into pytest incrementally, phase by
phase, as each area is touched — never by weakening what it already checks.

Every phase must include: unit tests, integration tests, API authorization tests (every new
V2 route gets a "wrong user cannot reach it" test), negative tests, and regression tests
proving V1 still answers.

---

## 8. Migration, rollback and safety

### 8.1 Forward migrations
Alembic from Phase 0. Every phase that touches the schema ships its migration in the same
commit. Migrations are additive — new tables and nullable columns only. No V2 phase drops
or renames a V1 Mongo collection.

### 8.2 Rollback
Each phase is rollback-safe by construction:
1. Turn the feature flag off — the V2 module disappears from the API and the app.
2. `alembic downgrade -1` if the schema needs reverting.
3. `git revert <commit>` as a last resort.

Because V1 is untouched, rolling back a V2 phase can never break the running product.

### 8.3 The `overall_score` retirement (Phase 3)
Historical scans keep their stored `wellness_scores` — deleting user history is not
acceptable. But:
- `GET /api/scan/trends` stops returning `overall_score`.
- The prompt stops requesting `wellness_scores` and the schema stops accepting it.
- The Progress screen plots versioned deterministic metrics instead.
- Old scans render with a plain note that these numbers came from an earlier method and are
  no longer updated.

### 8.4 Safety rules carried into every phase
Not a medical app. No diagnosis of skin, hair, scalp, nutrition, allergy or any medical
condition. Supplements are tracked as inventory only — never prescribed, never dosed, no
disease claims, no pregnancy-related medical recommendations, no drug-interaction claims
without a reviewed safety rule. Sensitive cases get a neutral "worth asking a qualified
professional" and nothing more. The existing no-medical-language rule in `CLAUDE.md` is
kept and extended to every new prompt and screen.

---

## 9. The phases

Each phase ends with: all relevant tests run, lint and type-check clean, README and
`env.example` updated, migrations added, `PHASE_N_REPORT.md` written, and one commit on a
local branch. **No phase starts before its predecessor is approved.**

---

### Phase 0 — Foundations and the test harness
*No user-visible change. Everything else depends on this.*

**Builds**
- PostgreSQL + Redis added to `docker-compose.yml`; `docker-compose.test.yml` created
- SQLAlchemy async engine, session dependency, Alembic initialised with an empty baseline
- `app/core/`: Pydantic Settings (replacing loose `os.environ` reads), structured error
  envelope, feature flags, `GET /api/v2/features`
- `/api/v2` router mounted, empty, flag-gated
- `ObjectStorage` protocol + local filesystem adapter
- `AIEnvelope` and `MetricResult` base schemas (definitions only, no consumers yet)
- Outbox table + relay skeleton; job queue protocol + Redis worker service
- pytest harness, `jest-expo` + RNTL harness, `tsc --noEmit` and lint scripts
- `CLAUDE.md` reconciled with the V2 master instructions (C4)
- The banned-vocabulary lint test

**Acceptance**
- `docker compose up` starts mongo, postgres, redis, backend, worker
- Every existing V1 route answers exactly as before — proven by regression tests
- `alembic upgrade head` and `downgrade base` both run clean
- Both test suites run green in Docker with no host installs
- With `V2_FEATURES` empty, `/api/v2/*` is invisible

**Out of scope:** any domain table, any new user-facing behaviour.

---

### Phase 1 — AI integrity
*Fixes C1, C2 (partly), C3. The highest-value correctness work in the plan.*

**Builds**
- Versioned Pydantic schemas for every AI response; strict validation before anything
  reaches the frontend
- Versioned prompt templates with explicit `prompt_version`
- `_fallback_coach` deleted from the success path — analysis failure raises
  `AIAnalysisError`
- Structured failure response with specific photo-quality guidance (§4.6)
- Allowance incremented only after a validated result; nothing written to the profile on
  failure
- `ai_observations` table; every persisted AI fact carries all seven provenance fields
- App-side retry flow with the returned guidance, and clear "this is an AI observation,
  is it right?" confirmation UI

**Acceptance**
- With `GEMINI_API_KEY` unset, `/api/scan/analyze` returns a clear error — never a plan
- Gemini timeout, malformed JSON and schema-violating JSON each return the structured
  error, and `scans_used_this_month` is provably unchanged
- No hardcoded style advice remains anywhere in `backend/`
- Every stored observation carries source, confidence, verification status, model version,
  prompt version, schema version and creation time

**Out of scope:** removing `wellness_scores` (Phase 3 — it needs replacements first).

---

### Phase 2 — Inventory core
*The biggest phase. The seven categories become real.*

**Builds**
- Full schema from §5 with Alembic migrations
- `/api/v2/inventory` CRUD for all seven categories, with per-category attribute validation
- Image upload through the storage abstraction; thumbnails via the job queue
- Usage logging (`item_usage_events`)
- Inventory tab: category list, item detail, add/edit flow, camera and gallery capture
- The shared component library the rest of the UI will reuse
- Bulk-add flow, because an empty inventory is the product's cold-start problem

**Acceptance**
- All seven categories create, read, update, archive and list
- A user cannot read or modify another user's items — tested per route
- Images survive a container restart; no face images stored anywhere
- Input-size limits and rate limits on upload
- Inventory works with the flag on and is entirely absent with it off

**Out of scope:** metrics, recommendations, outfits.

---

### Phase 3 — Deterministic metrics engine
*Retires the invented scores. Fixes C2 completely.*

**Builds**
- Metric registry and `MetricResult` plumbing
- First metric set: Wardrobe Utilisation, Inventory Balance, Product Expiry Risk, Value to
  Recover, Wardrobe Readiness
- `metric_snapshots` + recomputation triggered off outbox events
- `GET /api/v2/metrics` returning full explanations, formulas, inputs and missing-data notes
- A metric-explanation UI component: tap any number, see how it was calculated
- `overall_score` removal per §8.3

**Acceptance**
- Every displayed number has a formula, a version, an explanation, visible inputs and
  defined missing-data handling
- No composite appearance score exists anywhere
- Insufficient inputs produce "not enough information yet" plus what would fix it — never a
  fake zero
- Changing a formula bumps its version and leaves historical snapshots intact
- Golden-value unit tests for every formula

---

### Phase 4 — Style Me
*The first "decide for me" feature.*

**Builds** — deterministic outfit compatibility (colour, formality, season, weather),
occasion presets, outfit builder and saved outfits, weather integration, LLM used only to
explain a deterministically-chosen outfit. Outfit Variety and Occasion Preparedness metrics.
`app/scan.tsx` absorbed here. pgvector evaluated for semantic item matching.

**Acceptance** — recommendations use only items the user owns; no outfit is ever chosen by
the LLM; every suggestion explains itself; the missing-piece case is handled honestly.

---

### Phase 5 — Shopping evaluation
*"Should I buy this?" — the clearest commercial hook.*

**Builds** — deterministic duplicate detection against owned inventory, gap analysis,
cost-per-wear projection from real usage data, Purchase Efficiency metric, a
consider-this-purchase flow, and wishlist tracking.

**Acceptance** — duplicate detection is deterministic and explainable; every verdict cites
the specific owned items behind it; language stays constructive throughout (no "money
wasted").

---

### Phase 6 — Today and Planner
**Builds** — the Today screen (one question, one answer), weekly planning, calendar and
occasion input, routine scheduling, notifications, Travel Readiness and Seasonal Readiness.
Rate-limit counters move from Mongo TTL to Redis.

---

### Phase 7 — Beauty and hair routines
**Builds** — routine builder from owned products, deterministic ingredient-conflict rules
from a reviewed rule table (never from an LLM), expiry and PAO tracking with proactive
alerts, Routine Consistency metric, supplement tracking as pure inventory.

**Acceptance** — every conflict warning traces to a reviewed rule; no dosage advice, no
disease claims, no diagnosis; sensitive cases route to "ask a professional".

---

### Phase 8 — You
**Builds** — My Appearance, Improve, Progress (versioned metric history), Memory
(long-term preference and feedback store, pgvector if warranted), Privacy (export and
delete), Subscription and entitlements V2, Settings.

---

### Phase 9 — Navigation cutover and V1 deprecation
**Builds** — the five-tab structure becomes the default, old routes redirect, V1 endpoints
marked deprecated with a removal date, and the first honest assessment of whether the Mongo
→ Postgres migration for users and auth is worth doing.

---

### Phase 10 — Appearance-related nutrition and hydration
**Builds** — hydration tracking, appearance-supportive food suggestions (general wellness
framing only, no prescriptions, no medical claims).

---

### Phase 11 — Future integrations
Virtual try-on and professional integrations. Scoped when Phases 0–10 are live and there is
real usage data to design against. Deliberately not planned in detail now.

---

## 10. Cost

| Source | Control |
|---|---|
| Gemini calls | Already rate-limited per user per hour. Phase 1 removes the case where a *failed* call still costs a user their allowance. Deterministic-first means most V2 features cost nothing per use. |
| Postgres | One small instance. The data is text and metadata; the volume is low. |
| Object storage | The real growth item — inventory photos. Thumbnails generated on upload; originals capped by size limit; per-user quota introduced in Phase 2. |
| Redis | Small. Cache and queue only. |

The V2 direction is *cheaper per user than V1*, because metrics and recommendations are
computed, not generated.

---

## 11. Decisions the product owner needs to make

None are blocking Phase 0. Listed with a recommendation so they can be answered quickly
when they arrive.

| # | Decision | Needed by | Recommendation |
|---|---|---|---|
| 1 | Production object storage provider (S3, R2, Spaces) | Phase 2 deploy | Cloudflare R2 — S3-compatible, no egress fees |
| 2 | Per-user photo quota | Phase 2 | 200 items, 3 photos each, to start |
| 3 | Weather data provider | Phase 4 | Open-Meteo — free, no key |
| 4 | Does Plus gate inventory, or only recommendations? | Phase 5 | Inventory free, decisions paid — the inventory is what makes leaving expensive |
| 5 | Keep the invite-only gate through V2? | Phase 9 | Yes, until Phase 9 |

---

## 12. What happens next

This document is a proposal. On approval, work begins on **Phase 0** and stops at its end,
with `PHASE_0_REPORT.md` and a commit on a local branch for review.

Standing decisions already made:
- All tests run in Docker; nothing is installed on the host machine.
- Work is committed to local branches only; nothing is pushed to GitHub without the owner.
