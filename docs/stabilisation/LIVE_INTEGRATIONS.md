# Live integrations register (Fix 6 + Fix 8, WP5)

Every outbound network dependency the app has today, one row per
provider, with:

- **Purpose** — what the app uses it for.
- **Credential** — the environment variable(s), if any.
- **Least-privilege scope** — the smallest allowed permission set.
- **Docs URL + access date** — the documentation page the reviewer
  read, and when they read it.
- **Retention** — what the provider keeps and for how long.
- **Business contact** — a human who can reach support.
- **Live workflow** — the manually-dispatched workflow that proves
  the integration works end-to-end.
- **User consent** — where the user opts in.

If a provider is not on this list, the app does not call it.

---

## 1. Weather — Open-Meteo (default)

| | |
|---|---|
| **Purpose** | Current temperature / humidity / precipitation for a coarse coordinate, to bucketise as `hot` / `humid` / `cold` / `mild` / `rain`. |
| **Credential** | **None.** Free tier, no account, no API key. |
| **Least-privilege scope** | N/A — public endpoint. |
| **Docs URL + access date** | https://open-meteo.com/en/docs — accessed 2026-08-03. |
| **Retention** | Open-Meteo publishes no per-caller retention policy; the endpoint is stateless. |
| **Business contact** | https://open-meteo.com/en/contact |
| **Live workflow** | `.github/workflows/live-weather.yml` (manual dispatch). |
| **User consent** | Coarse location is user-provided. `WEATHER_PROVIDER=null` disables all outbound calls. |
| **Fallback** | `NullWeatherProvider` returns `unknown`, and the routine engine treats "no weather" as neutral. |

## 2. Weather — paid providers (documented alternative, not shipped)

| | |
|---|---|
| **Purpose** | Higher-resolution current weather; commercial SLA. |
| **Credential** | `WEATHER_API_KEY` (would-be — no such adapter ships yet). |
| **Adapter** | Not shipped. The `WeatherProvider` protocol accepts a second class implementing `.fetch(lat, lon)`; a paid provider lands as `<Provider>WeatherProvider` selected via `WEATHER_PROVIDER=<name>`. |
| **Live workflow** | Reuses `.github/workflows/live-weather.yml`. |
| **When to add** | If Open-Meteo's rate limit becomes a real production constraint (unlikely at the beta scale). Track under WP7+. |

## 3. Push notifications — Expo Push

| | |
|---|---|
| **Purpose** | Send reminders (routine-time, low-inventory, routine change) to a user's Expo Push Token. |
| **Credential** | **None** for send. The Expo Push Token proves device ownership; a leaked token only reaches that one device. |
| **Least-privilege scope** | N/A — no key. |
| **Docs URL + access date** | https://docs.expo.dev/push-notifications/sending-notifications/ — accessed 2026-08-03. |
| **Retention** | Expo does not persist the message body beyond delivery; delivery receipts (24 hours) are addressable via a receipt id. |
| **Business contact** | https://expo.dev/contact |
| **Live workflow** | Deferred — a live push test needs a real device with a real Expo Push Token. Covered under WP6's device sweep. |
| **User consent** | The Expo Push Token is obtained by the client only after the user grants the OS-level push permission with a pre-prompt. |

## 4. Calendar — Google Calendar (planned, not shipped)

| | |
|---|---|
| **Status** | **Planned.** Not shipped in this phase. |
| **Purpose** | Read the user's next 7 days of calendar events for occasion-aware suggestions. **Read-only.** |
| **Credential** | Google OAuth client id + client secret; per-user access + refresh tokens. |
| **Least-privilege scope** | `https://www.googleapis.com/auth/calendar.events.readonly` — **readonly**. No `.events` (write), no `.calendars`, no `.settings`. |
| **Docs URL + access date** | https://developers.google.com/calendar/api/v3/reference/events/list — accessed 2026-08-03. |
| **Retention** | Cache event start/end/title in `calendar_events` (V2) for the 7-day forward window; older rows expire. |
| **Business contact** | https://cloud.google.com/support |
| **Live workflow** | Deferred — needs a Google OAuth client and a real user consent. Added when Fix 6 ships this integration as an active feature. |
| **User consent** | Explicit "connect calendar" step in the app, with a revoke button. Revoke deletes the token and every cached event on the same call. |
| **Alternative** | The V2 `calendar_events` table already accepts `provider="manual"` entries; a user can add events by hand today without any provider integration. |

## 5. Monitoring — Sentry

| | |
|---|---|
| **Purpose** | Crash reports (5xx on backend, JS crashes on the mobile app). Not tracing, not profiling, not session replay. |
| **Credential** | `SENTRY_BACKEND_DSN` (backend), `EXPO_PUBLIC_SENTRY_DSN` (mobile). Both are DSNs, not secrets in the classical sense — leakage lets someone forge an event, not read events. Rotate on suspicion. |
| **Least-privilege scope** | DSN with `event:write` only. The live-monitoring workflow additionally requires a `SENTRY_LOOKUP_TOKEN` with `event:read` for read-back — only used inside the manually-dispatched workflow, never at runtime. |
| **Docs URL + access date** | https://docs.sentry.io/platforms/python/integrations/fastapi/ — accessed 2026-08-03. |
| **Retention** | Per-organisation Sentry setting (typically 30 or 90 days). Record what your org is set to. |
| **Business contact** | https://sentry.io/support/ |
| **Live workflow** | `.github/workflows/live-monitoring.yml` — sends two synthetic events, verifies both via the Sentry REST API, and asserts the scrubber removed a synthetic secret field. |
| **User consent** | Crash reporting is on by default. Both the backend and the frontend scrub PII before send (see `sentry_privacy.py` and `frontend/src/monitoring.ts`). |

---

## Owner actions (repo secrets to configure before the live workflows dispatch)

| Workflow | Repo secret | Where to get it |
|---|---|---|
| `live-gemini.yml` (WP3) | `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| `live-monitoring.yml` (this WP) | `SENTRY_BACKEND_DSN` | Sentry project → Settings → Client Keys (DSN) |
| `live-monitoring.yml` | `SENTRY_LOOKUP_TOKEN` | Sentry → User Settings → Auth Tokens → create with `event:read` scope on the project |
| `live-monitoring.yml` | `SENTRY_ORG` | Sentry organisation slug |
| `live-monitoring.yml` | `SENTRY_PROJECT` | Sentry project slug |
| `live-weather.yml` | *(none needed)* | Open-Meteo is keyless. |

## Change control

New rows here are added in the same PR as the code that emits the
outbound call. `CODEOWNERS` covers `docs/stabilisation/**`, so a
provider addition goes through owner review.

## Payment mechanics

Nothing in this document authorises a payment integration.
Razorpay is out of scope for the non-payment stabilisation phase.
`SUBSCRIPTIONS_AVAILABLE=false` remains.
