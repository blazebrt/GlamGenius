# Phase 1 Report — Production Foundation and Trust Reset

**Phase:** 1 of the V2 plan
**Baseline:** `e4bed57` (main)
**Branch:** `v2/phase-1-foundation` (pulled from GitHub; final corrections remain local)
**Date:** 2026-08-01

---

## 1. Plain-English summary

Three things were true of GlamGenius before this phase, and none of them are true now.

**The app made things up.** When the AI could not analyse a photo — because the key was
missing, the provider timed out, or anything else went wrong — the app quietly substituted
a fixed, invented result: skin tone "wheatish", undertone "warm", hair "wavy", scores in
the low seventies, colours deep teal and mustard. The user was shown these as facts about
their own face. **With no API key configured, every single analysis took this path.** Worse,
those invented facts were written permanently into the user's profile, and the user was
charged one of their monthly checks for the privilege.

That code is deleted. Analysis now either produces a real, validated result or fails
openly, with a specific reason, specific advice, no charge, and nothing written to the
profile.

**The app sold something that could not be bought.** The membership screen offered
₹249/month and ₹1999/year with "Start monthly" and "Start yearly" buttons. The backend
refuses every subscription on principle while the product is invite-only. So tapping
either button created an order, got refused, and showed the user **"Payment failed.
Please try again."** They were invited to pay, and then told their payment failed. Nothing
was ever going to succeed. The backend is now the single source of truth for whether
anything is for sale, and while it says no, the app offers nothing.

**Nothing was written down.** No record existed of any AI call — no model, no timing, no
cost, no reason for a failure. Investigating a bad result after the fact was impossible.
Every call is now recorded.

Underneath those three fixes, this phase also lays the foundation the rest of V2 needs:
PostgreSQL alongside the untouched MongoDB, migrations, a media store with real ownership
rules, privacy controls, feature flags, and — for the first time in this repository — an
automated test suite that runs on a machine with neither Python nor Node installed.

---

## 2. Architecture decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Modular monolith under `backend/app/`, mounted into the existing FastAPI app** | The brief warned against duplicate entrypoints. `server.py` stays the only one; V1 routers keep their `/api` prefix and V2 mounts at `/api/v2` beside them. |
| D2 | **PostgreSQL for V2 only; MongoDB untouched** | Migrating auth and billing is high-risk work with zero user-visible value. Inventory-shaped data is relational and belongs in Postgres. Doing both at once is the big-bang migration the instructions forbid. |
| D3 | **`account_links` holds the V1 user id and nothing else** | The brief said not to duplicate users carelessly. V2 stores no email, no plan, no password — one row per user, keyed on the existing Mongo UUID, created lazily on first V2 request. No migration, no backfill, no sync job, no second source of truth. |
| D4 | **AI gateway records on its own database session** | An AI call happened whether or not the surrounding request succeeds, and the ledger should say so. It also means V1 routes, which hold no Postgres session, needed no plumbing change to be recorded. |
| D5 | **Recording failures are swallowed and logged** | A ledger that can break the feature it measures is worse than no ledger. |
| D6 | **Prompts stay in `ai.py`; the gateway is generic** | A prompt belongs with the feature that owns it. Moving 145 lines of prompt text for tidiness would have risked transcription errors for no benefit. |
| D7 | **`extra="allow"` on nested schema blocks, strict on required fields** | Two failure modes pull in opposite directions: validate too loosely and garbage reaches the user; too tightly and a good answer is thrown away, which now means an error for no reason. Structure and substance are required; free text is bounded, not enumerated; unknown extra fields survive so nothing the UI renders is silently dropped. |
| D8 | **Profile vocabulary check on write-back** | An odd one-off answer ("iridescent lavender") is still shown to the user but is not stored. Junk cannot become a permanent fact about someone. |
| D9 | **MIME sniffed from magic bytes; dimensions read from headers** | A caller-supplied `Content-Type` is worth nothing. Reading dimensions from header bytes rather than decoding avoids running an image parser on untrusted input. |
| D10 | **Missing and not-yours both return 404** | A 403 would confirm that another user's asset id exists — a free enumeration oracle. |
| D11 | **Storage bytes deleted before the row is marked** | If the row were marked first and storage then failed, the image would be invisible but still on disk — the worst outcome for something a user asked to be removed. This order leaves it visible and retryable instead. |
| D12 | **Disabled feature returns 404, not 403** | A switched-off module should look absent, not like something the caller lacks permission for. |
| D13 | **Structured errors go in `detail`, reusing the V1 shape** | The frontend's `errorMessage()` already reads `detail.message`. Keeping the shape meant no existing screen had to change to keep working. |
| D14 | **Consent enforcement ships OFF** | Turning it on before the app has collected answers would lock out every existing account. The switch is built, wired and tested; flipping it is an environment change. |
| D15 | **Tests share one event loop** | Motor binds its client to the first loop that drives it. A loop per test kills everything after the first. One loop also matches how the app really runs. |
| D16 | **S3 adapter implemented, `boto3` not installed** | Production-only, tens of megabytes, and Phase 1 runs on the local adapter. The import is lazy and the missing package produces a clear configuration error rather than a boot crash. |

---

## 3. Database changes

**MongoDB: no changes.** Not a collection, not an index, not a document. V1 owns it and
keeps it.

**PostgreSQL: ten new tables**, all created by migration `0001_v2_foundation`.

| Table | Holds |
|---|---|
| `account_links` | One row per user: the V1 user id, account status, deletion request time |
| `consents` | Append-only consent record — type, granted, wording version, when, from where |
| `media_assets` | Uploaded photos: storage key, type, size, SHA-256, dimensions, purpose, status |
| `ai_runs` | Every AI call: account, feature, provider, model, prompt version, schema version, status, failure type, latency, validation result, token counts, estimated cost, whether allowance was consumed, request id |
| `ai_run_outputs` | Validated payloads with confidence and verification status |
| `feature_flags` | Runtime module switches |
| `audit_events` | Sensitive operations, with a hashed source address |
| `usage_ledger` | Itemised allowance consumption, with credits possible |
| `domain_outbox` | Transactional event queue |
| `app_events` | Product analytics, kept separate from audit so telemetry can be pruned without deleting evidence |

Notes: consents and the usage ledger are append-only by design — granting and revoking
both write new rows, so "when did they agree, and to which wording" is always answerable.
`usage_ledger.quantity` accepts negatives, which is how a wrongly charged check gets
credited back.

---

## 4. API changes

### New — `/api/v2`

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v2/health` | Health including PostgreSQL |
| GET | `/api/v2/config` | Billing availability, media rules, consent state, feature flags |
| GET | `/api/v2/me` | Profile, account status, consent, usage, media count |
| GET/POST | `/api/v2/consent` | Read and record consent |
| POST | `/api/v2/media/upload` | Validated, owned upload |
| GET | `/api/v2/media/{id}` | Asset metadata |
| GET | `/api/v2/media/{id}/content` | Asset bytes |
| DELETE | `/api/v2/media/{id}` | Erase bytes, mark deleted, audit |
| GET | `/api/v2/jobs/{id}` | Status of a long-running run |
| GET | `/api/v2/privacy/export` | Everything from both databases, as JSON |
| DELETE | `/api/v2/account` | Erase photos, withdraw consent, request closure |

### Changed — V1

| Route | Change |
|---|---|
| `POST /api/scan/analyze` | Fails openly instead of fabricating. Image size cap added. Profile write-back now vocabulary-checked. Writes a usage-ledger entry on success. |
| `POST /api/scan/preview` | Same failure behaviour. Image size cap added. |
| `POST /api/plans/style`, `POST /api/quiz/submit` | Fail openly instead of fabricating a plan. |
| `POST /api/subscription/create-order` | Now refuses at the **first** step. Previously created an order and only refused at confirm — the cause of the "Payment failed" message. |
| `POST /api/subscription/confirm` | Refusal now driven by `SUBSCRIPTIONS_AVAILABLE` rather than hardcoded. Billing code below it is intact and reachable when the flag flips. |
| `GET /api/config/public` | Now includes `subscriptions_available`, so the signed-out screen can tell before quoting a price. |
| All routes | Structured error envelope, `X-Request-Id` on every response, global handlers. |

### Three documented deviations from the brief

1. **`GET /api/v2/media/{id}` returns metadata; bytes come from `/content`.** One route
   cannot sensibly return both a JSON description and an image body, and the app needs the
   metadata before deciding whether to fetch the bytes.
2. **`GET /api/v2/jobs/{id}` is backed by `ai_runs`.** There is no separate job queue in
   Phase 1 because nothing yet runs asynchronously; inventing an empty queue to satisfy a
   route shape would be scaffolding with no load on it. The one slow, costly, worth-polling
   thing is an AI run, so that is what a "job" is. The route keeps its shape when real
   background work arrives.
3. **`GET`/`POST /api/v2/consent` added.** "Consent before photo analysis" is a Phase 1
   requirement and none of the listed routes can record consent.

---

## 5. UI changes

**Membership screen** — rewritten. While billing is unavailable it shows a private-beta
view: what is included, the real monthly allowance ("3 of 50 used"), and no prices, no
purchase buttons, and no "unlimited checks" claim. The paid plans are still in the file and
reappear the moment `SUBSCRIPTIONS_AVAILABLE=true`.

**Home** — the "Go Plus" quick action becomes "Membership" while billing is off. The plan
chip reads "Private beta · N checks left this month" instead of "Free · Unlimited checks".
The "Upgrade" link is hidden.

**Profile** — "Upgrade to Plus" is replaced with "Full access is included with your invite
— nothing to pay." The button only exists when the backend says billing is open.

**Scan** — failure is now a first-class screen state rather than a toast over a half-built
result. Three different failures get three different screens: a photo we could not read
(with the server's specific guidance and a retake button), our provider being down (which
says plainly that it is our problem, not their photo), and everything else. Retry re-sends
the same photo rather than making the user take a new one.

Before a photo leaves the app, the scan screen now shows an explicit consent control that
says the photo is sent for this analysis and is not stored. Signed-in consent is persisted;
signed-out previews carry a per-request answer. The backend independently enforces both.
Composite appearance scores and score-trend cards are no longer requested or shown.

**New: eight reusable trust states** in `src/components/TrustStates.tsx` — uploading,
processing, retrying, low-quality image, provider unavailable, analysis failed, deletion
confirmation, beta feature unavailable. Built on the existing design system.

One detail worth calling out: the "this did not use one of your checks" reassurance only
appears when the server explicitly confirmed it. Telling someone their check was refunded
when it was not would be worse than saying nothing.

---

## 6. Files changed

**Modified (19):** `README.md`, `env.example`, `docker-compose.yml`,
`backend/Dockerfile`, `backend/requirements.txt`, `backend/ai.py`, `backend/server.py`,
`backend/routes/{health,plans,quiz,scan,subscription}.py`,
`frontend/package.json`, `frontend/package-lock.json`, `frontend/app/_layout.tsx`,
`frontend/app/scan.tsx`, `frontend/app/subscription.tsx`,
`frontend/app/(tabs)/{home,profile}.tsx`

**Added — backend (43 files):** `backend/app/` (config, `shared/` for database, errors,
events, flags, observability, security, validation; `domains/` for identity, consent,
media, ai_gateway, entitlements, audit, analytics; `api/v2/`; `workers/`),
`backend/alembic.ini`, `backend/migrations/`, `backend/pytest.ini`, `backend/tests/`

**Added — frontend (7 files):** `src/services/apiV2.ts`, `src/services/failure.ts`,
`src/store/configStore.ts`, `src/components/TrustStates.tsx`, `src/__tests__/` (4 suites),
`jest.setup.js`

**Added — docs/infra (3):** `docs/v2/PHASE_1_AUDIT.md`, `PHASE_1_REPORT.md`,
`docker-compose.test.yml`

**Deleted:** `_fallback_coach()` and the nested `_fb()` in `backend/ai.py` — roughly 200
lines of fabricated analysis.

---

## 7. Migrations added

`backend/migrations/versions/0001_v2_foundation.py` — creates all ten tables with indexes
and foreign keys. Reversible: `downgrade()` drops them in reverse dependency order.

Verified in a container against a real PostgreSQL 16:

```
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_v2_foundation, V2 foundation tables
No new upgrade operations detected.
```

That second line is `alembic check`, which fails if the models have drifted from the
migration. It runs before pytest on every test run, so drift cannot land unnoticed.

---

## 8. Tests run, and the actual results

Everything ran in Docker. This machine has no Python and no Node installed.

### Backend

```bash
docker compose -f docker-compose.test.yml run --rm backend-tests
```

```
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_v2_foundation, V2 foundation tables
No new upgrade operations detected.
93 passed, 161 warnings in 22.19s
```

**93 passed, 0 failed.** Coverage by file: `test_v1_regression.py` 25 (including a 15-case
parametrised authorization table), `test_ai_gateway.py` 17, `test_media.py` 17,
`test_config_flags_and_billing.py` 15, `test_privacy.py` 14, `test_database.py` 5.

The 161 warnings are pre-existing Pydantic v1 `.dict()` deprecations in V1 routes and a
JWT key-length notice from the short test secret. None are errors and none come from new
code.

### Frontend

```bash
docker compose -f docker-compose.test.yml run --rm frontend-tests
```

```
✖ 6 problems (0 errors, 6 warnings)
Test Suites: 4 passed, 4 total
Tests:       42 passed, 42 total
```

**42 passed, 0 failed** — `failure.test.ts` 14, `TrustStates.test.tsx` 16,
`subscriptionScreen.test.tsx` 7, `configStore.test.ts` 5.
`tsc --noEmit` clean. `expo lint` 0 errors; the 6 remaining
warnings are pre-existing `react-hooks/exhaustive-deps` notices on mount-only effects, a
pattern already used throughout the codebase, plus one unused import in an untouched file.

### Live stack

```bash
docker compose up -d --build
```

```
backend-1  | INFO  [alembic.runtime.migration] Running upgrade  -> 0001_v2_foundation
backend-1  | server: V2: postgres=up storage=local features=v2_media,v2_privacy,v2_consent,v2_ai_gateway
backend-1  | INFO:     Application startup complete.
worker-1   | outbox_relay_started poll=2.0s
```

The stack was cold-started against a fresh PostgreSQL volume. The worker waits for the
migrated API healthcheck, eliminating the initial outbox-table race found during smoke
testing.

Endpoints verified by request, not assumption:

- `GET /api/health` → `{"status":"healthy","llm_configured":false,"gemini_ready":false}`
- `GET /api/v2/health` → `{"status":"healthy","postgres":"up",...}`
- `GET /api/v2/config` → `consent_required: true`, `subscriptions_available: false`,
  `face_photos_stored: false`
- `GET /api/config/public` → `subscriptions_available: false`

`npx expo export --platform web` also completed successfully and statically rendered all
21 routes.

### What was not tested

No test calls the real Gemini API — the provider is faked at the transport boundary in
every container test. The separate `backend_test.py` end-to-end suite was updated to grant
explicit test consent but could not be executed because this machine has no Gemini key.
That live-provider acceptance check remains pending; it is not represented as a pass.
There is no automated end-to-end test driving the actual app UI; the frontend tests are
component and store level, supplemented by the production web export.

---

## 9. Known limitations

1. **Account deletion is a request, not a purge.** Stored photos are erased immediately
   and irreversibly, consent is withdrawn, and the account is marked. The V1 user document
   and scan history are **not** destroyed. A single unconfirmed API call should not be able
   to permanently erase an account; that needs a confirmation flow and an operator-side
   purge with a grace period. The API response says exactly what happened and what is
   pending, rather than implying more than it did.
2. **The V1 hourly rate limit still counts a failed call.** A failure does not consume the
   monthly *allowance* — that is the acceptance criterion and it is tested — but it does
   consume one slot in the hourly burst limiter. That limiter exists to cap spend, and a
   provider error can still have cost tokens, so this is defensible; it is noted rather
   than hidden.
3. **S3 storage is untested against a real bucket.** The adapter is complete and its
   configuration errors are tested, but `boto3` is not installed and no live S3-compatible
   service has been exercised.
4. **The outbox has no handlers.** Events are written and the relay runs; an event with no
   handler is marked dispatched and dropped. Correct for now — nothing is waiting on one.
5. **Token revocation still absent.** A stolen token is valid for 30 days. Pre-existing,
   out of Phase 1 scope, noted in the audit as F14.
6. **`JWT_SECRET` cannot be rotated** without locking out accounts that still have
   pre-bcrypt password hashes, because the legacy verifier uses it. Pre-existing (F15).
7. **The live Gemini end-to-end check is pending.** `backend_test.py` remains intact and
   has been brought forward for the new consent contract, but a real key is required to
   execute its success-path assertions. The container suite covers provider success,
   timeout, invalid output and failure accounting with a deterministic fake transport.

---

## 10. Security considerations

**Improved by this phase:**

- Upload size and MIME are validated, with the type sniffed from magic bytes rather than
  trusted from the caller's label. A file whose contents contradict its label is refused.
- Uploads are capped **while streaming**, so an unbounded body is never buffered into
  memory.
- The base64 image field on the V1 scan routes now has a ceiling. Previously a single
  request could push an arbitrarily large payload to a paid provider.
- Path traversal is blocked in the local storage adapter, twice: keys are rejected if
  malformed, and every resolved path is checked to be inside the media root.
- Storage keys are generated from UUIDs only, never from the uploaded filename, and are
  never returned to any caller.
- Media ownership is enforced in exactly one function; missing and not-yours both 404.
- A startup check now logs a loud error when `JWT_SECRET` is left at the repository
  default, which would otherwise mean anyone could forge a token for any account.
- Every response carries a request id, so a user's report can be traced to a log line.
- Stack traces never reach a caller; the global handler logs them and returns a code.
- Audit records store a salted hash of the source address, not the address.
- The password hash is excluded from the privacy export — tested.

**Unchanged and still sound:** JWT auth via the single `get_current_user` gate, bcrypt
password hashing, login lockout per email and per IP, invite gating, CORS allow-listing,
and the face-image truncation rule (verified at exactly 83 characters by test).

---

## 11. Cost considerations

Net effect: **this phase reduces spend.**

- Failed analyses no longer consume a user's allowance, so a user whose analysis failed
  can retry without having lost anything — and we no longer pay twice for one result.
- Oversized payloads are rejected before reaching the provider. There was previously no
  limit at all.
- A 45-second timeout caps the cost of a hanging call.
- Every call's estimated cost is recorded in `ai_runs`, so spend is now measurable per
  feature, per model and per user for the first time.

New running costs: one PostgreSQL container (small — the data is text and metadata) and
one worker process (idle, polling every two seconds). Media storage is the item that grows;
it is capped per upload and the per-user quota lands with the inventory phase.

---

## 12. Rollback instructions

Each step is independent; take only the one you need.

**Turn off the new endpoints** (fastest, no deploy):

```bash
docker compose exec backend python -c "print('set V2_FEATURES= in .env, then restart')"
```

Set `V2_FEATURES=` empty in `.env` and restart. All V2 feature routes return 404. V1 is
unaffected.

**Revert the database:**

```bash
docker compose exec backend alembic downgrade base
```

Drops all ten V2 tables. MongoDB is untouched, so V1 keeps working throughout.

**Revert the code:**

```bash
git revert <commit hash below>
```

**Restore the old billing screens only:** set `SUBSCRIPTIONS_AVAILABLE=true` in `.env`.
The paid plans reappear — though the backend will then genuinely try to process orders, so
only do this when billing is really ready.

Because MongoDB and V1 are untouched, no rollback here can break the running product.

---

## 13. How to verify this yourself (no technical knowledge needed)

**Start it up.**

```bash
docker compose up --build
```

Wait for `Application startup complete`.

**1. The app no longer invents results.** Open `.env`, blank out the Gemini key so it reads
`GEMINI_API_KEY=`, and restart. Take a skin check in the app. Before this phase you would
have been shown a complete, confident analysis — skin tone "wheatish", colours deep teal
and mustard — all invented. Now you get a screen saying photo checks are unavailable, that
it is our problem and not your photo, and that your check has not been used. Check your
remaining checks on the Profile screen before and after: the number does not move.

**2. The app no longer sells anything.** Open the membership screen. There are no prices,
no "Start monthly", no "Start yearly", and no "unlimited checks". It says you are in the
private beta with full access included, and shows how many checks you have used this month.
Before this phase, tapping "Start monthly" showed "Payment failed. Please try again."

**3. Your photos are yours.** In the app, upload a photo, then delete it. It is gone
immediately — not hidden, actually erased from storage.

**4. You can see everything we hold.** Visit `/api/v2/privacy/export` while signed in. It
returns one JSON document with your profile, scans, plans, consents, media, AI runs and
audit trail. Your password is not in it.

**5. Nothing about V1 broke.** Sign in, edit your profile, view your history, browse salon
ideas. All unchanged.

**Run the tests yourself** — you do not need Python or Node installed:

```bash
docker compose -f docker-compose.test.yml run --rm backend-tests
```

```bash
docker compose -f docker-compose.test.yml run --rm frontend-tests
```

You should see `93 passed` and `42 passed`.

---

## 14. Acceptance criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | No AI failure produces a fake personal result | ✅ | `_fallback_coach` deleted; 8 tests in `test_ai_gateway.py` assert failure returns a structured error and that none of "wheatish", "deep teal", "mustard" appears in a failure response |
| 2 | All V2 AI results are schema validated | ✅ | Every call goes through `run_structured`, which validates against a versioned Pydantic schema before returning; `test_schema_violating_response_is_a_failure` |
| 3 | PostgreSQL and Alembic work in local development | ✅ | `alembic upgrade head` in the live stack and the test stack; `alembic check` reports no drift; `test_database.py` confirms all ten tables |
| 4 | Existing V1 functionality still starts | ✅ | Live stack boots clean; 24 regression tests in `test_v1_regression.py`; `/api/health` verified by request |
| 5 | MongoDB has not been destructively removed | ✅ | Not one collection, index or document changed. V1 still reads and writes it |
| 6 | Users cannot access another user's media | ✅ | `test_another_user_cannot_read_your_media`, `test_another_user_cannot_delete_your_media` — both 404, not 403 |
| 7 | A user can delete an uploaded media asset | ✅ | `test_owner_can_delete_and_the_bytes_are_really_gone` asserts the file is gone from disk |
| 8 | Subscription buttons hidden/disabled when billing unavailable | ✅ | 7 tests in `subscriptionScreen.test.tsx`; backend refuses at first step (`test_creating_an_order_is_refused_at_the_first_step`) |
| 9 | The frontend gives a useful retry path after failure | ✅ | `TrustStates.test.tsx` (16 tests); retry re-sends the same photo; server guidance and explicit consent are displayed |
| 10 | All relevant tests pass | ⚠️ | 93 backend + 42 frontend, 0 failures; tsc clean; lint 0 errors; production web export passed. Live-provider `backend_test.py` awaits a Gemini key |
| 11 | README and env.example updated | ✅ | Both rewritten with V2 sections, test commands, error contract, privacy statement |
| 12 | PHASE_1_REPORT.md exists | ✅ | This document |
| 13 | The phase is committed | ✅ | Hash in §15 |

Additional Phase 1 implementation requirements met: repository audit at
[docs/v2/PHASE_1_AUDIT.md](../v2/PHASE_1_AUDIT.md) (37 findings); modular V2 structure
with no duplicate entrypoint; all ten requested tables; media abstraction with local and
S3 adapters; AI gateway recording all twelve required fields; privacy controls with
consent, export, deletion and audit; eight frontend trust states; the full Step 9
provider-independent test list. The credential-gated live Gemini run is the only pending
acceptance evidence.

---

## 15. Commit

```
da713c6  feat(v2): establish production foundation and trusted AI pipeline
34c712e  feat(v2): enforce Phase 1 trust boundaries
```

The report's own commit-hash line is filled in by a follow-up commit, since a commit
cannot contain its own hash.

Branch `v2/phase-1-foundation`. The original Phase 1 commits were pulled from GitHub;
the final trust-boundary correction is local and has not been pushed.

**Stopping here.** Phase 2 does not begin without your approval.
