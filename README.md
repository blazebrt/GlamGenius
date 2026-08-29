# GlamGenius

GlamGenius is a personal appearance decision system for India. It brings an
owned wardrobe, care shelf, routines, events and preferences into a calm daily
plan, starting with what the customer already owns.

The primary experience is **Today · Style · Care · Plan · You**. It keeps
customer-facing decisions grounded in explicit inventory, context and
preferences; AI may contribute bounded, reviewable observations, never an
unreviewable authority.

## Features

- **Today** — an owned-first outfit and the few things worth attention
- **Style** — occasion looks and purchase decisions from Wardrobe, Shoes and Accessories
- **Care** — Skin Care, Hair Care, Perfumes and Supplements inventory; routines and upkeep
- **Plan** — weekly planning, events and optional Google Calendar context
- **You** — appearance context, progress, memory, privacy and account settings

All seven inventory categories remain first-class: Wardrobe, Shoes,
Accessories, Skin Care, Hair Care, Perfumes and Supplements. GlamGenius does
not provide an attractiveness score, medical diagnosis, supplement dosing,
salon booking, marketplace, checkout or billing.

## Quick start (Docker)

```bash
cp env.example .env
# set GEMINI_API_KEY in .env
docker compose up --build
```

Starts PostgreSQL, the API and the outbox worker. Database migrations run
automatically on start.

- V2 health (includes PostgreSQL): http://localhost:8000/api/v2/health

## Backend (local)

```bash
cd backend
cp ../env.example .env
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


## Databases

One: PostgreSQL via Supabase.

| | PostgreSQL |
|---|---|
| Owns | auth (via Supabase), media, consent, AI runs, appearance digital twin, onboarding, complete appearance inventory, audit, usage ledger, flags, outbox |
| Used by | V2 (`/api/v2`) |
| Migrations | Alembic |

Authentication is handled securely via Supabase Auth.


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
V2_FEATURES=v2_media,v2_privacy,v2_consent,v2_ai_gateway,v2_profile,v2_inventory,v2_recommendations,v2_shopping_decisions,v2_today,v2_planner,v2_routines,v2_progress
```

The `feature_flags` table overrides that at runtime with no redeploy. A route behind a
disabled flag returns 404 — a switched-off feature should look absent, not forbidden.

## API overview

Routes marked 🔒 require an `Authorization: Bearer <token>` header. The token
comes from register or login and is valid 30 days. The caller's identity always
comes from the token, never from the URL or request body.

### V2 — `/api/v2`

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | /api/v2/health | — | Health including PostgreSQL |
| GET | /api/v2/config | — | Media rules and feature flags |
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
| POST | /api/v2/inventory/extract | 🔒 | Create an unverified draft from one owned inventory image |
| POST/GET | /api/v2/inventory/items | 🔒 | Create or browse owned inventory with pagination and filters |
| GET/PATCH/DELETE | /api/v2/inventory/items/{id} | 🔒 | Read, correct or archive one owned item |
| POST | /api/v2/inventory/items/{id}/confirm | 🔒 | Explicitly confirm an extracted draft |
| POST | /api/v2/inventory/items/{id}/usage | 🔒 | Log deterministic usage history |
| POST | /api/v2/inventory/items/{id}/condition | 🔒 | Record a condition change |
| GET | /api/v2/inventory/search | 🔒 | Search by category, brand, colour, ingredient, occasion and more |
| GET/POST | /api/v2/inventory/duplicates | 🔒 | Review duplicate candidates (`/{id}/resolve` resolves one) |
| GET | /api/v2/inventory/expiring | 🔒 | Deterministic expiry and period-after-opening results |
| GET | /api/v2/inventory/low-use | 🔒 | Low-Use Products with a visible rule |
| GET | /api/v2/inventory/value-to-recover | 🔒 | Transparent, explicitly estimated Value to Recover |
| GET | /api/v2/inventory/summary | 🔒 | Category counts and attention summary |
| GET | /api/v2/style/occasion-types | 🔒 | The 16 occasions and the questions each one needs |
| POST/GET | /api/v2/occasions | 🔒 | Save or list the events you are dressing for |
| GET/PATCH | /api/v2/occasions/{id} | 🔒 | Read or correct one saved occasion |
| POST | /api/v2/style/occasion | 🔒 | Style me: up to three different looks from what you own |
| GET | /api/v2/looks/{id} | 🔒 | One look with its pieces, reasoning and history |
| POST | /api/v2/looks/{id}/revise | 🔒 | Rebuild a look away from what you pushed back on |
| POST | /api/v2/looks/{id}/swap-item | 🔒 | Replace one slot with another item you own |
| POST | /api/v2/looks/{id}/feedback | 🔒 | Loved, worn, saved or not for me |
| GET | /api/v2/shopping/roi-model | 🔒 | The Appearance ROI formula and its weights |
| POST | /api/v2/shopping/evaluate | 🔒 | Should I buy this: Buy, Wait or Skip |
| GET | /api/v2/shopping/evaluations/{id} | 🔒 | One evaluation with the full calculation |
| POST | /api/v2/shopping/evaluations/{id}/decision | 🔒 | Record what you actually did |
| GET | /api/v2/today | 🔒 | Today's plan, served from cache unless something changed |
| POST | /api/v2/today/regenerate | 🔒 | Rebuild today on purpose |
| POST | /api/v2/today/actions/{id}/complete | 🔒 | Tick something off |
| POST | /api/v2/today/outfit/swap | 🔒 | Change one piece without rebuilding the day |
| POST | /api/v2/today/feedback | 🔒 | What you wore, and what you thought |
| POST | /api/v2/today/clarify | 🔒 | Answer the one question the plan asked |
| POST | /api/v2/today/items/unavailable | 🔒 | "I can't wear that today" |
| POST | /api/v2/today/weather | 🔒 | Record the weather yourself |
| POST | /api/v2/today/events | 🔒 | Add a commitment by hand |
| GET/PATCH | /api/v2/today/notifications | 🔒 | Notification preferences and history |
| GET | /api/v2/planner/week | 🔒 | The Monday-to-Sunday week |
| POST | /api/v2/planner/week/generate | 🔒 | Build or rebuild the week |
| PATCH | /api/v2/planner/day/{date} | 🔒 | Move, regenerate or annotate one day |
| POST | /api/v2/planner/day/{date}/lock | 🔒 | Lock a day so a rebuild leaves it alone |
| GET | /api/v2/integrations/calendar/status | 🔒 | What is connected, and what it holds |
| POST | /api/v2/integrations/calendar/connect | 🔒 | Connect a calendar source |
| DELETE | /api/v2/integrations/calendar | 🔒 | Disconnect, and stop using its events |
| GET | /api/v2/integrations/providers | 🔒 | Which sources exist and which are usable |
| POST | /api/v2/shelf/analyse | 🔒 | Re-read your Skin Care and Hair Care labels |
| GET | /api/v2/shelf/summary | 🔒 | Your whole shelf, counted rather than scored |
| GET | /api/v2/shelf/expiring | 🔒 | What is running out, and what has no date recorded |
| GET | /api/v2/shelf/low-use | 🔒 | Products sitting unused |
| GET | /api/v2/shelf/value-to-recover | 🔒 | An estimate, scoped to the shelf |
| POST | /api/v2/routines/generate | 🔒 | Build routines from products you already own |
| GET | /api/v2/routines/today | 🔒 | Only the routines due right now |
| POST | /api/v2/routines/steps/{id}/complete | 🔒 | Tick a step off for a day |
| GET | /api/v2/routines/consistency | 🔒 | How it is going. No streaks |
| GET | /api/v2/routines/improve | 🔒 | Everything the Improve screen shows |
| GET/POST | /api/v2/routines/observations | 🔒 | Notes in your own words, never interpreted |
| POST | /api/v2/ingredients/check | 🔒 | Check a label or a list against the reviewed rules |
| POST | /api/v2/ingredients/confirm | 🔒 | Confirm a low-confidence label read |
| GET | /api/v2/ingredients/{key} | 🔒 | The reviewed note for one ingredient |
| GET | /api/v2/perfume/recommendation | 🔒 | Which perfume you own suits today |
| GET | /api/v2/supplements/summary | 🔒 | Supplement inventory. Dates only, never a dose |
| GET | /api/v2/nutrition/appearance-suggestions | 🔒 | Food context, filtered to what you eat |
| GET/PATCH | /api/v2/nutrition/preferences | 🔒 | Diet, focus nutrients, on or off |
| GET/PATCH | /api/v2/nutrition/hydration | 🔒 | Hydration reminders. No target volume |
| GET | /api/v2/progress | 🔒 | Every metric for the week or month, with its formula |
| GET | /api/v2/progress/metrics | 🔒 | Every metric this product can show, and how each is worked out |
| GET | /api/v2/progress/metrics/{key} | 🔒 | One metric: formula, value now, and history |
| POST | /api/v2/progress/self-report | 🔒 | How you felt, in your own words |
| POST | /api/v2/progress/photos | 🔒 | Keep a photo for comparison, with its conditions |
| GET | /api/v2/progress/comparisons | 🔒 | A side-by-side, only if the conditions allow one |
| GET/POST | /api/v2/goals | 🔒 | Goals, with where you started recorded |
| PATCH | /api/v2/goals/{id} | 🔒 | Update or complete a goal |
| GET | /api/v2/memory | 🔒 | What we remember, why, and where it came from |
| PATCH | /api/v2/memory/{id} | 🔒 | Correct or confirm something we remember |
| DELETE | /api/v2/memory/{id} | 🔒 | Forget it. Stops affecting suggestions immediately |
| GET | /api/v2/memory/export | 🔒 | Everything held, including what was deleted |
| PATCH | /api/v2/memory/categories/{cat} | 🔒 | Switch a category of memory off. Nothing is destroyed |
| POST | /api/v2/memory/feedback | 🔒 | Tell us what you thought; we say what we learned |
| GET | /api/v2/milestones | 🔒 | What you have reached, and what is on the list |
| POST | /api/v2/milestones/{id}/acknowledge | 🔒 | Dismiss a milestone |
| POST | /api/v2/support | 🔒 | Ask for help with relevant account context attached |
| POST | /api/v2/stylist-review | 🔒 | Ask a human stylist to look at something |
| GET | /api/v2/tryon/status | 🔒 | Whether virtual try-on exists yet. It does not |

## Commercial boundary

There is no marketplace, checkout or billing surface. Operations — backup,
restore and monitoring — are in [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

## Progress and memory

**There is no overall score, and one cannot be added.** Every metric declares the *domains* it
reads, and `registry.validate_registry()` — which runs at import — rejects any metric whose
inputs name another metric. A composite of the thirteen is not expressible, which is the only
honest way to promise this in a codebase other people will keep editing.

Every metric carries a formula, a formula version, its required inputs, what it does when they
are missing, an explanation, an update frequency, and what it is *not* a measure of. Those are
required fields with no defaults, so an undocumented metric does not compile. Every stored
metric event keeps the formula version and the actual input counts, so an old number can be
checked by hand against the formula that produced it.

**Missing data is never zero.** An uncomputable metric reports `unavailable` and names what it
is waiting for. A zero would read as a real, bad score.

**Memory is the user's.** Every fact carries its source, confidence, verification state and
linked evidence, and can be corrected, confirmed, deleted or switched off by category.
`memory.active_facts()` is the single accessor for anything that influences a recommendation
and filters deleted, rejected and disabled facts at the query level; deletion also blanks the
text, so nothing can act on it even by mistake.

**Photo comparisons refuse by default.** Six conditions — area, lighting, angle, framing, image
quality and time gap — must all hold, and the conditions are recorded by the user rather than
guessed from the image. A refusal explains every reason and says how to take a comparable photo
next time.

**Gamification rewards useful behaviour only.** The rewardable list contains no app opens, no
logins and no time-in-app; events are deduplicated by a unique constraint so nothing can be
farmed; and milestone copy is swept for both childish and shaming wording.

## Routines and shelf intelligence

Routines are built from products the user already owns. A required step with nothing to fill
it becomes a gap that names a *category* — "a gentle face wash" — never a brand and never a
link. Optional steps with nothing owned are left out entirely.

Two boundaries are structural rather than aspirational:

**Every warning names a reviewed rule.** A `Finding` cannot be constructed without a
`rule_id`, and those ids are rows seeded into `compatibility_rules` and friends by migration
`0006`. The model is handed rule ids and asked for wording; a note whose id did not actually
fire is discarded. It cannot introduce a warning, change a severity, or reorder a step.

**No diagnosis, no dosage.** `app/domains/routines/safety.py` sweeps every generated string —
AI-written *and* deterministic — against a banned-term list and a bare-dosage regex. A failure
discards the wording, never the result underneath. Questions that belong with a clinician get
a professional-consultation boundary instead of an answer. Supplements are tracked as
inventory: name, brand, dates, and what the user said it is for. Nothing else.

Nutrition is appearance-adjacent food context with Indian examples, off by default. Diet is a
constraint, not a suggestion — and when a nutrient's usual sources are all excluded by
someone's diet, the app says so rather than dropping it in silence.

## Today and the weekly planner

Today answers one question — *what should I wear or do to look prepared today?* — and it is
deliberately a short list, not a dashboard. Optional modules (skincare, hair, perfume,
hydration, nutrition, shopping) appear only when they have something relevant to say, and
each one carries the reason it showed up.

**It does not run an AI for every user every morning.** The outfit comes from the same
deterministic engine as Style Me. Every material input — the date, the weather, your calendar,
what is available, what you wore recently — is hashed into a cache key. If the hash has not
moved, the stored plan is returned untouched. When it does move, one row is written to
`plan_recalculation_events` saying which input changed, so a plan that changed under you can
always be explained.

**Dates are yours, not the server's.** At 20:00 UTC it is already tomorrow in India, so every
date is resolved in your timezone (`Asia/Kolkata` by default, or from your city).

The planner runs Monday to Sunday and builds days in order, so each day knows what the days
before it are already using. Locked days are never touched by a rebuild. Repetition is
*shown*, not forbidden — wearing the same trousers twice in a week is normal.

### Calendar and weather

Both go through a provider abstraction, and the source that ships working is **you**: add the
events and the weather that matter and nothing leaves the app. Other providers are declared so
the API can report them honestly as "known, not connected" rather than pretending.

**No access token is ever stored in the app database.** `external_integrations.credential_ref`
is an opaque handle only, and `GET /api/v2/integrations/calendar/status` says so in its
response. Disconnecting actually disconnects: events that came from that connection stop
feeding your plans, and anything you typed yourself is left alone.

### Notifications

At most **one** proactive appearance notification a day by default, with quiet hours in your
local time. Every notification is deduplicated on a content hash, so the same thing is never
sent twice. Suppressed notifications are recorded with the reason, so "why didn't I hear about
that" is answerable.

## Style Me and Should I buy this?

Two workflows, one rule: **we never invent something you own.**

Every "owned" piece in a look is a row in your inventory that you confirmed. Photo-extracted
drafts are not used until you confirm them, and anything a look needs that you do not own is
labelled as an optional addition with no brand and no price attached.

The looks themselves are chosen without an AI. Filtering, compatibility scoring, outfit
assembly and ranking are deterministic functions of your confirmed inventory and profile. A
language model is asked only to phrase the result, and its wording is discarded if it breaks
the language rules. When the provider is unavailable the looks are identical and the
explanation is written from your own recorded details — the response says which happened in
`explanation_source`.

### Appearance ROI

`Should I buy this?` returns Buy, Wait or Skip from a published formula:

```
roi = sum(factor value x factor weight) / sum(weight of the factors that could be scored)
```

Buy at 0.65 and above, Wait from 0.45, Skip below. Two overrides the arithmetic cannot
outvote: something closely matching what you already own can never be a Buy, and neither can
something that creates no new outfit combinations. A factor with no data — a missing price —
is dropped and the rest reweighted, so missing information lowers confidence rather than the
score. `GET /api/v2/shopping/roi-model` returns every factor and weight.

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
`MEDIA_TOO_LARGE`, `FEATURE_UNAVAILABLE`, `VALIDATION_FAILED`, `NOT_FOUND`,
`INTERNAL_ERROR`, plus the existing `AI_RATE_LIMIT` and `INVITE_*` family.

Every response carries an `X-Request-Id` header, echoed in the error body, so a user's
screenshot is enough to find the request in the logs.

## Privacy

- **Explicit consent comes first.** Signed-in users record consent through
  `/api/v2/consent`; protected routes refuse optional photo analysis before provider
  work when consent is missing.
- **Appearance photos are reviewable observations, not a hidden authority.** Optional
  onboarding photos are transient input; their structured observations remain separate
  from confirmed customer facts.
- Photos uploaded to your own collection are stored until you delete them.
- Optional onboarding photos follow the same transient rule: only structured,
  reviewable observations are retained. The image itself is never added to profile or
  onboarding state.
- `GET /api/v2/privacy/export` returns everything from both databases as JSON.
- `DELETE /api/v2/account` erases stored photos immediately and marks the account for
  closure. Profile and history removal follows within 30 days.
- Deletions and exports are recorded in `audit_events` with a hashed source address —
  enough to investigate, not a location log.

## Going to production

The sequence, in order. Steps 1 and 2 are the ones that catch mistakes early.

```bash
# 1. Configure the production environment (see env.example, and
#    docs/FOUNDER_PRODUCTION_INPUTS.md for what only you can supply)

# 2. Check what is still missing — key names and statuses only, no secrets
cd backend && python -m app.release_readiness      # exit 0 = ready, 1 = not

# 3. Run migrations
alembic upgrade head

# 4. Deploy the API
# 5. Deploy the frontend

# 6. Schedule the hourly notification worker on your host — see
#    docs/OPERATIONS.md §6. Nothing in this repository can verify it exists.
python -m app.workers.notifications                 # one manual cycle

# 7. If Google Calendar is enabled, connect one real account and confirm a sync
# 8. If live weather is enabled, confirm a real forecast reaches Today
# 9. Run the staging smoke tests
```

Steps 7 and 8 are verification, not configuration. Code existing for an
integration is not the same as that integration being live, and the readiness
report will not claim otherwise: an integration that is switched off reads
`disabled_optional`, and the notification scheduler always reads
`requires_host_scheduler` because the application genuinely cannot see the
host's crontab.

## Documentation

- [docs/V2_ARCHITECTURE_AND_PHASE_PLAN.md](docs/V2_ARCHITECTURE_AND_PHASE_PLAN.md)
- [docs/v2/PHASE_1_AUDIT.md](docs/v2/PHASE_1_AUDIT.md)
- [PHASE_1_REPORT.md](PHASE_1_REPORT.md)
- [PHASE_2_REPORT.md](PHASE_2_REPORT.md)
- [PHASE_3_REPORT.md](PHASE_3_REPORT.md)

## Disclaimer

Guidance is for general style and care decisions — not medical advice.

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

## Complete appearance inventory

Phase 3 adds a structured inventory for Wardrobe, Shoes, Accessories, Skin Care,
Hair Care, Perfumes and Supplements. Manual entries are confirmed user facts; AI
photo extraction creates a draft that must be reviewed. Every item is owned, versioned,
searchable and linked only to media belonging to the same account.

Expiry and period-after-opening dates, low-use rules, duplicate candidates and Value to
Recover are deterministic and explain their inputs. Missing prices remain missing rather
than being guessed. Supplement entries are inventory records only and never produce dosage
advice. Multi-item shelf, wardrobe and video capture remains behind the disabled
`v2_inventory_batch` flag until quality is proven.
