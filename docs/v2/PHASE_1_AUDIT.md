# Phase 1 — Repository Audit

**Audited at:** commit `e4bed57` (main), before any Phase 1 change.
**Method:** every file listed below was read, not inferred. Line references are to the
pre-Phase-1 tree.

---

## 1. Frontend structure

Expo 54 / React Native 0.81 / expo-router 6 / React 19. TypeScript `strict: true`.

```
frontend/app/
  _layout.tsx            Stack, font loading, ErrorBoundary, session bootstrap
  index.tsx              Landing / routing decision
  (auth)/welcome.tsx     Sign-in and sign-up, invite code entry
  (tabs)/_layout.tsx     5 tabs: Home · Ideas · Check · History · Profile
  (tabs)/home.tsx        Quick actions grid, plan banner
  (tabs)/services.tsx    Salon ideas list
  (tabs)/scan-tab.tsx    Scan entry point
  (tabs)/history.tsx     Past scans + trends chart
  (tabs)/profile.tsx     Profile fields, plan card, upgrade button
  scan.tsx               32 KB — camera, upload, processing, results
  get-advice.tsx         Occasion → style plan
  recommendations.tsx    Past plans
  style-quiz.tsx         6-question quiz
  subscription.tsx       Monthly / yearly paywall
  service-details.tsx
frontend/src/
  services/api.ts        axios instance, token interceptor, 401 → welcome
  services/notify.ts     cross-platform alert wrapper
  store/userStore.ts     Zustand: session, profile, subscription refresh
  store/planStore.ts     Zustand: last scan
  theme/colors.ts        Full design system
  components/ErrorBoundary.tsx
```

**Findings**

| # | Finding |
|---|---|
| F1 | Exactly **one** shared component exists (`ErrorBoundary`). Every screen styles itself inline. There is no card, button, field, empty-state or error-state primitive to reuse. |
| F2 | The API client is untyped. `api.get('/users/me')` returns `any`; screens index into `res.data.analysis` with no contract. |
| F3 | `scan.tsx` holds camera control, upload, polling animation, error branching and the entire results renderer in one 32 KB file. |
| F4 | Failure handling exists but is shallow: `notify('Check failed', 'Could not complete analysis. Please try again.')` (`scan.tsx:159`). No distinction between a timeout, a bad photo, and the provider being down, and no guidance on what to change. |
| F5 | `api.ts` already has two good helpers — `errorMessage()` preserves server-written text, `isRateLimited()` detects 429. The pattern to build on exists. |

---

## 2. Backend structure

FastAPI, single app, already split by domain. Runs as `uvicorn server:app` with
`backend/` as the working directory, so modules import flat (`from settings import ...`).

```
backend/
  server.py       App, /api router, CORS, Mongo index creation on startup
  settings.py     ~25 env vars read with os.environ.get, module-level constants
  database.py     Motor client + db handle (6 lines)
  models.py       11 Pydantic models, Mongo-shaped
  security.py     Password hashing, JWT, lockout, quotas, user sanitising
  ai.py           646 lines: Gemini client, prompts, parsing, rate limits, fallbacks
  invites.py      Invite validation and atomic consumption
  catalog.py      Static salon/quiz catalogue
  routes/         health, users, scan, quiz, plans, recommendations,
                  services, subscription, admin
```

**Findings**

| # | Finding |
|---|---|
| F6 | The route split is clean and genuinely domain-shaped. V2 should extend it, not replace it. |
| F7 | `settings.py` uses bare `os.environ.get` with string parsing. No validation — a malformed `AI_REQUESTS_PER_HOUR` raises `ValueError` at import and the container crash-loops with no useful message. |
| F8 | No `app/` package, no dependency-injection layer, no repository pattern. Routes talk to `db` directly. Fine at this size; needs structure before V2 domains land. |
| F9 | Only one FastAPI entrypoint exists. V2 must mount into it rather than create a second. |

---

## 3. MongoDB models

No ODM. Pydantic models are serialised with `.dict()` and inserted directly.

| Collection | Shape | Indexes (created in `server.py:53-73`) |
|---|---|---|
| `users` | `UserProfile` — 25 fields incl. `password_hash`, `plan`, `scans_used_this_month`, `scan_month_key` | `id` unique, `email` |
| `scans` | `ScanResult` — `user_id`, `scan_type`, truncated `image_base64`, `analysis` blob | `user_id` |
| `style_plans` | `StylePlan` — `user_id`, `occasion`, `plan` blob | `user_id` |
| `subscription_orders` | order dict | `user_id` |
| `invite_codes` | code, label, max_uses, uses, active | `code` unique, `active` |
| `login_attempts` | key, created_at | `key`, TTL on `created_at` |
| `preview_attempts` | key, created_at | `key`, TTL on `created_at` |
| `ai_usage` | user_id, created_at | `user_id`, TTL on `created_at` |

**Findings**

| # | Finding |
|---|---|
| F10 | Primary keys are application-generated UUID **strings** (`str(uuid.uuid4())`), not Mongo `_id`. This is convenient for V2: the same UUID can key Postgres rows without any migration. |
| F11 | `analysis` is an unvalidated free-form dict. Whatever the model returned is what got stored. |
| F12 | TTL indexes are used correctly for rate-limit cleanup — a good pattern already in place. |

---

## 4. Authentication

`security.py`. JWT (HS256, 30-day expiry), subject is the user UUID.

- `get_current_user` (`security.py:73`) is the **single** auth gate. Identity comes from the
  signed token — never from the URL or body. Routes that still take a `{user_id}` path
  parameter call `_require_same_user` to reject cross-user access (`security.py:171`).
- Passwords: bcrypt with explicit 72-byte truncation. A legacy unsalted SHA-256 scheme is
  still accepted for pre-bcrypt accounts and transparently upgraded on next login
  (`users.py:92-97`).
- Brute-force protection counts failures per email **and** per IP, with the IP limit
  deliberately 4× looser so shared networks don't cause collateral lockouts.

**Findings**

| # | Finding |
|---|---|
| F13 | This layer is sound. Phase 1 reuses `get_current_user` unchanged rather than building a second auth path. |
| F14 | No refresh tokens and no revocation list — a stolen token is valid for 30 days. Out of scope for Phase 1; noted for a later security phase. |
| F15 | `_legacy_hash_password` keeps `JWT_SECRET` load-bearing for old password verification, so `JWT_SECRET` can never be rotated without locking out un-migrated accounts. Noted, not addressed. |

---

## 5. Subscription state

**The backend already knows billing is off. The frontend does not ask.**

- `POST /api/subscription/confirm` raises `403 SUBSCRIPTIONS_UNAVAILABLE` as its **first
  statement** (`subscription.py:59-70`). Every line after it is dead code kept for a future
  paid launch.
- `GET /api/subscription/status` returns `"subscriptions_available": false` and
  `"invite_only": true` (`subscription.py:126-127`).
- `GET /api/config/public` does **not** expose `subscriptions_available` at all.

Meanwhile the app still sells:

| Location | What the user sees |
|---|---|
| `subscription.tsx:97` | **"Start monthly"** button — ₹249/month |
| `subscription.tsx:109` | **"Start yearly"** button — ₹1999/year, "BEST VALUE" |
| `subscription.tsx:92` | "Unlimited skin & hair checks" |
| `home.tsx:20` | **"Go Plus"** quick action |
| `profile.tsx:108` | **"Upgrade to Plus"** button |
| `scan.tsx:153` | **"Go Plus"** in the out-of-checks dialog |

**Finding F16 — this is the most user-visible defect in the repository.** Tapping any of
these runs `buy()` (`subscription.tsx:41`), which creates an order, calls confirm, receives
the 403, and shows **"Payment failed. Please try again."** The user is invited to pay, and
then told their payment failed. Nothing was ever going to succeed. Fixed in Phase 1 Step 6.

---

## 6. Scan routes

`routes/scan.py`.

| Route | Auth | Behaviour |
|---|---|---|
| `POST /api/scan/preview` | none | Invite code required. Calls Gemini, returns partial result, **stores nothing**. |
| `POST /api/scan/analyze` | 🔒 | AI quota → monthly quota → Gemini → store scan → update profile → increment counter |
| `GET /api/scan/history` | 🔒 | Own scans, newest 50 |
| `GET /api/scan/trends` | 🔒 | Own score series, oldest 30 |

**Findings**

| # | Finding |
|---|---|
| F17 | Order of operations in `analyze_scan` is: analyse (line 93) → store (101) → **copy AI output into the user profile** (103-122) → increment allowance (125). Because analysis can never fail (see §7), the increment always runs. |
| F18 | `scan.py:104-122` writes `skin_tone`, `undertone`, `face_shape`, `skin_type`, `hair_type`, `skin_concerns` and `hair_concerns` from the AI response straight onto the user record, with no provenance and no way to tell them apart from user-entered values. |
| F19 | The privacy truncation at `scan.py:98` is correct and intact: `image_base64[:80] + "..."` → 83 stored characters. Phase 1 does not touch it. |

---

## 7. AI fallback behaviour — the central defect

`ai.py`.

`_fallback_coach(scan_type)` (`ai.py:277-...`) returns a fully-populated, entirely invented
analysis:

```
skin_tone      "wheatish"        undertone       "warm"
skin_type      "combination"     hair_type       "wavy"
skin_score     72                hair_score      70
overall_score  72                style_readiness 74
colours        deep teal, mustard, maroon
observation    "Mild uneven tone and everyday dullness are common…"
```

It is returned in **three** situations:

1. `GEMINI_API_KEY` unset or `google-genai` not installed → `ai.py:590`
2. Any exception during the Gemini call → `ai.py:591-594`
3. Same two paths again in `generate_style_plan` via the nested `_fb()` → `ai.py:641, 644`

The response carries `meta.image_quality_notes = "Used a gentle default plan because AI
analysis was unavailable."` — the only signal, buried in a metadata field the app never
renders.

**Findings**

| # | Finding |
|---|---|
| F20 | **The user is shown invented facts about their own face and told nothing.** With no API key configured, *every single analysis* takes this path. |
| F21 | **The user is charged for it.** `scan.py:125` increments `scans_used_this_month` because the fallback looks like success. |
| F22 | **The invented facts are written into the profile** and persist. Two failed scans and the user's stored skin tone is "wheatish" and their undertone is "warm" — permanently, invisibly, and wrongly. |
| F23 | `_parse_llm_json` (`ai.py:266`) strips code fences and calls `json.loads`. There is **no schema validation**. A partial or malformed object is passed to the frontend as-is. |
| F24 | No AI call is recorded anywhere. No latency, no model version, no prompt version, no cost, no failure reason. Debugging a bad result is impossible after the fact. |
| F25 | `wellness_scores.overall_score` is a universal appearance score with no formula, version or explanation. Out of Phase 1 scope — it needs deterministic replacements first — and is scheduled for the metrics phase. |

Rate limiting itself is real and correct: `_assert_ai_quota` (`ai.py:234`) enforces an
hourly ceiling per user, 10 free / 60 Plus, backed by a Mongo TTL collection.

---

## 8. Image handling

| Aspect | Current state |
|---|---|
| Transport | Base64 JSON string in the request body |
| Size limit | **None.** No max length on `image_base64`, no request-body cap |
| MIME validation | **None.** Any string is accepted and forwarded to Gemini |
| Storage | Face images deliberately truncated to 83 chars — correct, must stay |
| Object storage | Does not exist |
| Ownership model | N/A — nothing is stored |

**Finding F26** — a caller can post an arbitrarily large base64 string. It is forwarded to
Gemini, which is a real cost and availability exposure. Phase 1 adds size and MIME
validation on the V2 media path; a body-size cap on the V1 scan route is added as well.

---

## 9. Tests

| Asset | Reality |
|---|---|
| `backend_test.py` | 64 KB, 1 800+ lines, `requests` + `PIL`. Requires a **live server, live Mongo and a live Gemini key**. Genuinely thorough coverage of V1 behaviour — including a `PROTECTED_ROUTES` table asserting every protected route rejects anonymous callers. |
| `tests/` | One empty `__init__.py`. |
| Frontend | No test framework installed at all. |
| Lint | `expo lint` script exists. Nothing runs it. |
| Types | No `tsc --noEmit` script. |
| CI | No workflow files. |
| `test_result.md` | 15 KB status file from a previous agent harness. Not executable. |

**Finding F27** — `backend_test.py` is valuable and must not be deleted or weakened. It is
kept as an opt-in end-to-end suite. Phase 1 adds a *separate* pytest suite that runs against
the ASGI app in-process with no network, no live server, and a faked AI provider.

---

## 10. Docker configuration

`docker-compose.yml`: two services.

- `mongo` — `mongo:6`, port 27017 exposed, named volume, `restart: unless-stopped`
- `backend` — built from `backend/Dockerfile`, port 8000, `env_file: .env`,
  `depends_on: mongo`

`backend/Dockerfile`: `python:3.11-slim`, installs `build-essential`, pip-installs
requirements, copies source, runs uvicorn.

**Findings**

| # | Finding |
|---|---|
| F28 | `depends_on: mongo` without a healthcheck — the backend can start before Mongo accepts connections. Motor tolerates this lazily, so it has not bitten yet. |
| F29 | No Postgres, no Redis, no worker, no test service. |
| F30 | No frontend service; Expo is expected to run on the host. **The host has no Node installed**, so all frontend tooling in Phase 1 must be containerised. |
| F31 | `build-essential` is installed and never removed — it exists only because `bcrypt` may need to compile. Left alone; not a Phase 1 concern. |

---

## 11. Environment variables

`env.example` documents 21 variables across 6 groups: Mongo, Gemini, JWT, pricing, login
protection, preview throttling, AI rate limits, CORS allow-list, invite gating.

**Findings**

| # | Finding |
|---|---|
| F32 | The file is unusually well commented — each block explains *why* in plain English. Phase 1 additions follow the same style. |
| F33 | Insecure-by-default values are present: `JWT_SECRET=change-me-to-a-long-random-string`, `ADMIN_SECRET=change-me-admin-secret`. `settings.py:17` also falls back to a hardcoded `"glamgenius-dev-secret-change-me"` when `JWT_SECRET` is unset — so a deployment that forgets it gets a **publicly known signing key**. Phase 1 adds a startup check. |
| F34 | `.gitignore` covers `.env` correctly, though the environment-file block is duplicated eight times from a broken append (`-e` literals at lines 80-111). Cosmetic; cleaned up in Phase 1 since it touches nothing else. |

---

## 12. Current feature flags

**None exist.** Behaviour is switched by three boolean-ish env vars read at import time:

- `INVITE_ONLY` (`settings.py:23`)
- `ALLOWED_ORIGINS_IS_DEFAULT` (`settings.py:65`) — a derived warning flag
- implicit: `bool(GEMINI_API_KEY)` gates real AI vs. fallback

There is no runtime toggle, no per-user rollout, and no way to disable an incomplete module
without a redeploy. **Finding F35** — Phase 1 introduces a real flag store.

---

## 13. Current error handling

| Layer | Pattern |
|---|---|
| Backend | `HTTPException` raised inline. Two shapes coexist: a bare string (`detail="Email and password required"`) and a structured dict (`detail={"code": ..., "message": ...}`). |
| Structured codes in use | `PREVIEW_LIMIT`, `SCAN_LIMIT`, `AI_RATE_LIMIT`, `SUBSCRIPTIONS_UNAVAILABLE`, `INVITE_REQUIRED`, `INVITE_INVALID`, `INVITE_INACTIVE`, `INVITE_EXHAUSTED` |
| Global handlers | **None.** An unhandled exception returns FastAPI's default 500 with no code and no request correlation. |
| Frontend | `errorMessage(err, fallback)` reads `detail` as string or `detail.message`, so it already handles both shapes. `isRateLimited(err)` checks 429 or `detail.code === 'AI_RATE_LIMIT'`. |
| Logging | `logging.basicConfig` at INFO, plain format. No request IDs, no correlation, no structured output. |

**Findings**

| # | Finding |
|---|---|
| F36 | The `{code, message}` convention is good and already understood by the frontend. Phase 1 standardises on it rather than inventing a new envelope, so existing screens keep working. |
| F37 | No request correlation ID, so a user reporting "it failed" cannot be traced to a log line. |

---

## 14. What Phase 1 changes, in order of user impact

1. **Analysis can fail honestly.** `_fallback_coach` is deleted. Failure returns
   `ANALYSIS_UNAVAILABLE` with retry guidance, consumes no allowance, and writes nothing to
   the profile. *(F20, F21, F22)*
2. **Payment buttons stop lying.** Backend config becomes the source of truth; unavailable
   actions are unreachable and relabelled with private-beta language. *(F16)*
3. **AI output is schema-validated** before it reaches the app. *(F23)*
4. **Every AI call is recorded** — provider, model, prompt version, schema version, status,
   latency, failure type, estimated cost. *(F24)*
5. **PostgreSQL, Alembic and the V2 module layout** land alongside an untouched MongoDB.
6. **Media gets real storage** with MIME and size validation, ownership checks and deletion.
   *(F26)*
7. **Privacy controls**: consent, export, media deletion, account deletion, audit trail.
8. **Frontend trust states** replace the single generic failure toast. *(F4)*
9. **A real test suite** runs in Docker with no host toolchain. *(F27, F30)*

The original scope assessment placed removal of `overall_score` *(F25)* after Phase 1.
The final trust review superseded that decision: new prompts no longer request composite
scores and the Phase 1 UI no longer displays them. The legacy response field and history
route remain readable only for compatibility with historical records. Token revocation
*(F14)*, `JWT_SECRET` rotation *(F15)*, inventory, Today engine and the shopping engine
remain outside Phase 1.
