# Phase 5 — Today Engine and Weekly Planner

Turning the Phase 4 outfit and occasion intelligence into recurring daily value: a Today
screen that answers one question, and a Monday-to-Sunday planner.

---

## 1. Baseline

Verified before any Phase 5 code was written, on `main` with Phase 4 merged.

| | |
|---|---|
| Baseline commit | `8a698c7` (merge of PR #14, Phase 4) |
| Phase 4 commit inside it | `196f91b` |
| Branch | `claude/code-handover-prep-8uhjje`, restarted from `origin/main` |

Phases 1–4 confirmed complete and passing:

| Check | Result |
|---|---|
| `alembic upgrade head` | clean |
| `alembic check` | no drift |
| Backend pytest | **170 passed** |
| `tsc --noEmit` | clean |
| `expo lint` | 0 errors, 3 pre-existing warnings |
| Jest | **83 passed** |

### Same environment deviation as Phase 4, stated again

`docker compose -f docker-compose.test.yml run --rm backend-tests` still cannot build here:
`deb.debian.org` and Docker Hub's blob CDN are blocked by the egress policy. The **databases
are the exact containers the compose file specifies** (`mongo:6`, `postgres:16-alpine`, pulled
through the permitted `mirror.gcr.io`); the test process runs on the host with the identical
environment block and the same three commands in the same order. On a machine with normal
registry access the documented commands run this work unchanged.

---

## 2. Two decisions worth stating up front

### The providers are abstractions with a working manual source

The brief asks for a weather-provider abstraction and a calendar-provider abstraction. It does
not ask to integrate a specific service, and this project forbids writing integration code for
an outside service from memory or adding a dependency the task does not name.

So: the abstractions are real and typed (`providers/base.py`), and the source that **ships
working** is the user. You add the events and the weather that matter, and the entire Today
engine and weekly planner work end to end with no outside account and no API key. Google,
Apple and Outlook are declared in the registry so the API can report them honestly as "known,
not connected" — `UnconfiguredProvider` raises rather than returning invented data, and a test
asserts it never claims to be available.

This is a product position as much as a technical one. Plenty of people will never want to
connect a work calendar, and the app should be fully useful to them.

### No access token is stored in the database

`external_integrations.credential_ref` is an opaque handle, never a token. Access and refresh
tokens are secrets, and this project keeps secrets in environment configuration and secret
stores — not in application tables, where a data export, a log line or a backup would carry
them. The status endpoint returns `stores_credentials: false` on every integration, and a test
asserts it.

---

## 3. What was built

### A new domain

`backend/app/domains/planning/`, built on Phase 2's profile, Phase 3's inventory and Phase 4's
deterministic styling engine.

| File | What it does |
|---|---|
| `clock.py` | What "today" means for a person: timezone, week start, seasons |
| `providers/base.py` | The two provider protocols and their value objects |
| `providers/manual.py` | The working providers, plus the honest unconfigured seam |
| `context.py` | The context service, the occasion inference, and the cache key |
| `compiler.py` | The daily-plan compiler and the contextual modules |
| `weekly.py` | Monday-to-Sunday generation, locks, and day swaps |
| `notifications.py` | Dedup, daily cap, quiet hours |
| `service.py` | Ownership, persistence, serialisation |
| `models.py` / `schemas.py` | Thirteen tables and the validated contracts |

### Today opens from cache

Every material input is hashed into `daily_plans.cache_key`: the date, timezone, occasion,
dress code, weather, event titles and times, available items, unavailable items, recent wear,
and the confirmed profile. The current time is deliberately **not** in the hash — including it
would make every request look like a change and the cache would never hit.

Matching hash → the stored plan is returned untouched, `generated_from: "cache"`.
Different hash → rebuild, and one row into `plan_recalculation_events` naming the trigger.

Measured on a fresh stack over real HTTP: **cold 112 ms, cached 27 ms.** The acceptance
criterion is two seconds.

### It never runs an AI per user per morning

The outfit comes from the Phase 4 deterministic engine — filtering, compatibility scoring,
assembly and ranking, all pure arithmetic over confirmed inventory. A test asserts the fake
provider records **zero calls** across generating a full week, opening Today, and forcing a
regenerate.

### Today is a short list, not a dashboard

Priority ≤ 40 is the opening list: the outfit, the single most important appearance action, a
weather adjustment, and the upcoming commitment. Everything else is collapsed behind one
disclosure.

Optional modules produce a row **only when they have something to say**, and each carries the
reason it appeared:

* skincare and hair only when you own those products;
* perfume only when you own one and the day is formal enough to warrant it;
* hydration only when the weather is hot or humid;
* nutrition on Mondays only, and only if you set a goal;
* shopping only when a Wait verdict is still sitting undecided.

### Progressive automation

High confidence acts. Low confidence asks **one** question, with the reason it is worth
answering, and only when the answer would change the plan. It never asks what it already
knows: a user-confirmed event or a recorded dress code produces no question. Answering writes
the answer back to the event or the weather and rebuilds.

Occasion inference from event titles is deliberately conservative — a multi-word phrase like
"client meeting" is a strong signal, a single common word like "work" is weak and falls below
the clarification threshold.

### Repetition, corrected mid-build

The first implementation treated everything worn in the last seven days as equally "recent".
That is wrong, and the tests caught it: by Thursday the entire wardrobe counts as recent and
the signal means nothing, so consecutive days came out identical.

It now stores **when** each item was last worn and decays the penalty by recency — worn
yesterday is heavily marked down, worn six days ago barely at all. Only the last two days
count as "too soon", and if every option still repeats, the engine retries once with those
items excluded outright before giving up. Wearing the same trousers again after five days is
normal; tomorrow is the thing worth avoiding.

The week is built **in date order** so each day sees what the days before it are using.
Building days independently produces the same shirt four times, because each day is
individually right.

### The planner

Monday to Sunday. Lock a day and a rebuild leaves it alone — and a locked day also refuses to
be regenerated on its own, rather than silently ignoring the request. Days can be swapped
within a week; a locked day cannot be swapped onto, and worn history never moves, because what
you actually wore on a past day is a fact.

Moving a day is two taps rather than a drag: pick the day, pick where it goes. Same outcome,
works with one thumb, and usable with a screen reader — which a drag is not.

---

## 4. Database

One forward-only migration: **`0005_today_engine_and_planner`**, revising
`0004_phase_4_decision_mvp`. Purely additive — migrations 0001–0004 untouched, nothing
renamed, altered or dropped, so an existing deployment upgrades with no data movement.

Thirteen tables: `external_integrations`, `calendar_events`, `weather_snapshots`,
`daily_plans`, `daily_plan_inputs`, `daily_plan_actions`, `weekly_plans`, `weekly_plan_days`,
`outfit_schedule`, `laundry_state_events`, `notification_preferences`,
`notification_deliveries`, `plan_recalculation_events`.

Every one hangs off `account_links.id`. No table stores user identity, and V1 authentication
was not touched.

---

## 5. Frontend

Primary navigation is now **Today · Inventory · Style Me · Planner · You**, with Style Me as
the raised centre action. Home and Today are one screen — opening the app answers "what do I
wear today" rather than presenting a menu. The skin check, salon ideas and history remain
routes, reached from Today and from You; every entry point that used to land on Home now lands
on Today.

| File | What |
|---|---|
| `app/(tabs)/today.tsx` | The merged Home/Today screen |
| `app/(tabs)/planner.tsx` | The seven-day planner |
| `src/components/today/TodayPieces.tsx` | Outfit card, actions, clarification, states |
| `src/components/planner/PlannerPieces.tsx` | Day cards, move bar, repetition, laundry |

Loading, stale and offline are three separate states, not one spinner. A failed refresh keeps
the plan already on screen rather than blanking it, and says it is showing the saved one.

---

## 6. Tests

### Backend — 62 new, `backend/tests/test_planning.py`

Every listed area is covered: timezone handling, India timezones, weather changes, calendar
sync, revoked calendar access, duplicate events, daily-plan idempotency, cache invalidation,
event-driven recomputation, outfit repetition, unavailable items, laundry state, locked days,
notification deduplication, offline/cached Today, the swap workflow, low-confidence
clarification, and integration authorization.

Worth calling out:

* 20:00 UTC resolves to **tomorrow** in `Asia/Kolkata`, and day bounds are built in the user's
  zone (midnight IST is 18:30 UTC the previous day);
* a bad timezone string or unknown city falls back rather than raising;
* a second `GET /today` is served from cache and does not bump the version;
* changing the weather invalidates the cache, rebuilds, and writes a recalculation event;
* an item marked `in_wash` is dropped, and returns once `available_from` passes;
* a locked day survives a week rebuild **and** refuses a solo regenerate;
* quiet hours across midnight, the daily cap of one, and dedup returning the *first* decision;
* the planner records zero AI calls.

### Frontend — 34 new, across two suites

`today.test.tsx` and `planner.test.tsx` cover loading, stale, offline, cache attribution, the
one-question card, owned-versus-optional rendering, ticking actions off, collapsed optional
modules, the empty-wardrobe state, all seven modules, lock/unlock, regenerate hidden on locked
days, the move bar excluding locked days and itself, repetition surfaced not forbidden,
laundry, and a banned-language sweep.

### Exact results

| Check | Baseline (Phase 4) | After Phase 5 |
|---|---|---|
| `alembic upgrade head` | clean | clean |
| `alembic check` | no drift | no drift |
| Backend pytest | 170 passed | **232 passed** |
| `tsc --noEmit` | clean | clean |
| `expo lint` | 0 errors, 3 warnings | 0 errors, **same 3 warnings** |
| Jest | 83 passed | **117 passed** |
| Production Expo web export | succeeded | succeeded, new routes emitted |
| Fresh-stack smoke | 31/31 | **34/34** |

The fresh-stack smoke migrated an empty database through all five migrations, started a real
uvicorn server, and drove the whole feature over HTTP: nine inventory items, a cold and a
cached Today open (112 ms / 27 ms), a weather change, an unavailable item, a swap that survived
re-opening, a calendar connect with a duplicate ignored, a disconnect that stopped events
feeding plans, a seven-day week with no two consecutive days identical, a locked day surviving
a rebuild, a day swap, and notification preferences.

### `backend_test.py` — reported honestly, again

**9/18 passed**, the same nine failures as the established baseline. The nine are all
Gemini-dependent (Face Scan, Hair Scan, Recommendations Advice, Quiz Submit, signed-out
preview, free-check quota, no-anonymous-scanning, AI rate limiting, Plus ceiling) and fail with
`ANALYSIS_UNAVAILABLE` because no key is configured here.

This was measured against `5194d9a` (pre-Phase-4) and against the Phase 4 branch during that
phase, both 9/18 with the identical nine. It is a pre-existing environment limitation, **not**
something Phase 5 broke, and it is not being called passing. Worth re-running with a real
`GEMINI_API_KEY` before merge.

---

## 7. Security review

| Concern | How it is handled |
|---|---|
| Identity | Always from the signed token. Never from a path, query or body. |
| Ownership | Every read and write scoped to the account; another account's id returns 404. |
| `account_id` in a body | All schemas are `extra="forbid"` — a 422. |
| OAuth tokens | Never accepted, stored or returned. `credential_ref` is an opaque handle. |
| Revocation | Disconnecting revokes the integration **and** the events that came through it, scoped by integration id. Events you typed are untouched. |
| Swap injection | Goes through the Phase 4 swap, which re-fetches the item scoped to the account and requires active, confirmed and correct-category. |
| Laundry state | `set_item_state` verifies ownership before writing. |
| Notifications | Content-hashed dedup with a unique constraint; suppressed rows recorded with a reason. |
| Flags | Fail-closed. `v2_today` and `v2_planner` default off and return 404 when disabled. |
| Language | The Phase 4 banned-term boundary still holds; a test sweeps every plan and week response. |

## 8. Cost review

* **Zero AI calls** for Today, for a regenerate, and for a full week. Asserted by a test.
* A repeat `GET /today` is a cache hit: no recomputation, no writes beyond the source marker.
* Weekly generation is seven deterministic compilations, flushed in order.
* Plans are keyed one row per account per day by unique constraint — no duplicate accumulation.
* Notifications are capped at one a day by default and deduplicated before anything is sent.

---

## 9. Verification steps

```bash
docker compose -f docker-compose.test.yml run --rm backend-tests
docker compose -f docker-compose.test.yml run --rm frontend-tests
docker compose -f docker-compose.test.yml down -v
```

Expect 232 backend tests and 117 frontend tests to pass, `alembic check` clean.

To see it running:

```bash
cp env.example .env          # V2_FEATURES now includes v2_today and v2_planner
docker compose up --build
```

Sign in, add a few wardrobe items and confirm them, then open Today. Add the weather or an
event to watch the plan rebuild. Open Planner and generate the week. None of it needs a Gemini
key — that is the point.

---

## 10. Limitations

* **No live weather or calendar service is integrated.** The abstractions and the manual
  providers are complete; a real adapter is a registry entry plus one class, and needs the
  current official docs for whichever service is chosen.
* **Occasion inference is keyword-based**, not a model. It is conservative by design, and
  anything below the confidence threshold is asked about rather than assumed.
* **One timezone in practice.** Every Indian city maps to `Asia/Kolkata`. Other IANA zones are
  accepted and work, but there is no timezone picker in the UI yet.
* **Notifications are queued, not delivered.** The decision layer — dedup, cap, quiet hours —
  is complete and tested, and rows land in `notification_deliveries` with status and reason.
  No push transport is wired, because that is a device-token integration and no dependency for
  it was named.
* **Moving a day is two taps, not a drag gesture.** Deliberate: it is more accessible and works
  better one-handed.
* **The laundry panel in the week view lists states, not item names**, because the week
  serialiser does not resolve archived-item names.
* **No packing plan yet.** The brief asks for a *foundation*: `outfit_schedule` and the
  travel/vacation occasions give a packing view everything it needs, but no packing UI ships in
  this phase.
* **The "novel situation" LLM path is not enabled.** The seam exists and `daily_plans.used_llm`
  records it, but no flag turns it on, so it is always false today. Shipping it off is the
  honest position: the deterministic path is good, and an unused AI call is a cost with no
  benefit.

---

## 11. Acceptance checklist

| Criterion | Status |
|---|---|
| Today opens from cache in under two seconds | ✅ 27 ms measured over HTTP |
| The screen is not an overwhelming dashboard | ✅ short primary list, rest collapsed, modules conditional |
| The primary outfit uses owned inventory where possible | ✅ confirmed inventory only, asserted |
| Plans recalculate after material context changes | ✅ cache key + `plan_recalculation_events` |
| Users can swap an item without regenerating everything | ✅ and the swap survives re-opening |
| Users can create a complete weekly plan | ✅ Monday to Sunday |
| Calendar access can be disconnected | ✅ and its events stop feeding plans |
| Notifications are controlled and deduplicated | ✅ cap, quiet hours, content hash |
| Relevant optional modules appear contextually | ✅ each with its reason |
| All relevant tests pass | ✅ 232 backend, 117 frontend |
| `PHASE_5_REPORT.md` exists | ✅ |
| The phase is committed | ✅ |

## 12. Preservation

Migrations 0001–0004 unchanged; no V2 table or route renamed; no history rewritten; PostgreSQL
still owns V2; V1 auth not migrated; no duplicated identity; no ownership check bypassed; no
`user_id` from a body; AI-extracted inventory still does not auto-verify; no fabricated
fallbacks; no appearance scores; billing still unavailable; still seven inventory categories;
"Money Wasted" appears nowhere; **no new dependencies** in `backend/requirements.txt` or
`frontend/package.json`.

## 13. Review round

Eight findings were raised on PR #15 by an automated reviewer. All eight were real
and all eight are fixed, each with a regression test.

**P1 — the planner refetched forever.** `useFocusEffect` had `plan` in its dependency
list; every successful load produced a new object, changed the callback, re-ran the
effect and fetched again, for as long as the tab was focused. The "have we loaded
once" bit now lives in a ref, so the callback identity is stable. Today had a milder
version of the same thing — a mount effect *and* a focus effect — which doubled the
first request; the redundant one is gone.

**P1 — swapping two days moved the whole day, not the outfit.** Exchanging
`daily_plan_id` dragged each day's date-specific facts with it, so a Monday/Friday
swap showed Friday's weather and Friday's meeting under Monday's heading, and
`/today?plan_date=` disagreed with the planner. Only the `look_id` moves now; each
`DailyPlan` stays attached to its own date, and both plans are re-keyed to the
current context so the arrangement survives.

**P2 — the schedule kept stale item ids after a swap.** `OutfitSchedule.item_ids`
feeds `recent_wear`, so later days penalised the garment the user took *off* and
could recommend the one they had just chosen. The schedule now follows the swap.

**P2 — the cache key ignored garment content and the draft count.** Editing a
colour, or adding a draft, left the hash unchanged and served the old outfit and
the old reasoning. Item content and `draft_count` are now hashed.

**P2 — the cache key ignored the time of day.** Routine modules branch on it, so a
plan first built in the evening stayed a cache hit all the next morning and never
gained its morning skincare. The part of day (four buckets, not a clock reading) is
now in the hash.

**P2 — the manual-event dedup id used `hash()`**, which is salted per interpreter
process, so the same event reposted after a restart slipped past deduplication.
It is a SHA-256 digest now.

**P2 — the notification queue had no production caller.** Only tests reached it, so
no `NotificationDelivery` was ever created and changing preferences did nothing.
`compile_day` now queues the day's notification through the full decision layer.
Delivery to a device is still a separate transport and remains out of scope.

**P2 — keyword matching was substring, not word-boundary**, despite the comment
claiming otherwise: "networking" matched "work" and "shooting" matched "shoot",
silently changing the formality of a day. It uses word boundaries now.

**Stop after Phase 5.**
