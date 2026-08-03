# Monitoring (Fix 8)

GlamGenius uses [Sentry](https://sentry.io) for **crash reporting only**.
Performance monitoring, replay, profiling, and session logs are
deliberately not enabled in this phase — those are separate product
decisions.

## What is captured

**Backend (FastAPI)**
- Every unhandled exception in a request handler
- Every HTTP 5xx response, including the ones the app itself returns
- Errors raised during startup (before the `Started server process`
  line appears in the log)

**Frontend (Expo React Native)**
- Uncaught JavaScript errors on any screen
- Native crashes (Android and iOS)
- React render errors — the root component is wrapped with
  `Sentry.wrap()` when a DSN is present, so a crash inside the tree
  becomes an event rather than a white screen

## What is scrubbed before leaving the process

Both sides apply the same denylist:

| Category | Examples of matched keys | What happens |
|---|---|---|
| Image content | `image_base64`, `photo_bytes`, `raw` (long base64-shaped) | Redacted whole |
| Ingredient lists | `ingredients`, `ingredient_list` | Redacted whole |
| Long-term memory | `memory_facts`, `memory` | Redacted whole |
| Payment identifiers | `razorpay_order_id`, `payment_*`, `receipt` | Redacted whole |
| Tokens | `jwt`, `token`, `authorization`, `api_key`, `secret`, `password` | Redacted whole |
| Personal contact | Any string containing an email address, an Indian phone number, or a JWT-shaped value | Only the matching span is redacted; the rest of the string is preserved |

The scrubber is a **`before_send`** hook, so nothing leaves either
process until it has been rewritten.  Sentry's own server-side
scrubbing is a second layer of defence.

## Turning monitoring on

Two independent DSNs — one for each side.  Setting the DSN turns
monitoring on; leaving it empty leaves monitoring off and the app never
opens a Sentry socket.

### Backend

Add to `backend/.env` (this file is gitignored):

```
SENTRY_BACKEND_DSN=<paste your FastAPI DSN here>
SENTRY_ENVIRONMENT=development   # or "staging" / "production"
```

Restart the backend:

```
sudo supervisorctl restart backend
```

You should see one `Sentry initialised (environment=…)` line in the
backend log.

### Frontend

Add to `frontend/.env` (also gitignored):

```
EXPO_PUBLIC_SENTRY_DSN=<paste your React Native DSN here>
EXPO_PUBLIC_SENTRY_ENVIRONMENT=development
```

Rebuild your development client or run `npx expo start --dev-client`
so the new environment variable is embedded in the bundle.

## Verifying that events actually arrive

**Do this once**, from a laptop that has both DSNs configured.  You do
not need to do it again on every deploy.

**Backend**

1. Set `SENTRY_TEST_ENDPOINT=1` in `backend/.env`.
2. Restart the backend.
3. `curl http://localhost:8001/api/__sentry_test`
4. Open the Sentry dashboard → the FastAPI project → **Issues**.
5. Within a minute or two, an event appears titled `RuntimeError`.
6. Click into it and confirm the values do **not** contain
   `test@example.com` or the sample JWT — they should read `[Redacted by
   application]`.
7. Unset `SENTRY_TEST_ENDPOINT` and restart.

**Frontend**

1. Add a temporary button somewhere visible with an `onPress` that
   calls `throw new Error("Sentry test test@example.com")`.
2. Run `npx expo start --dev-client` and open the app on a real Android
   device (Expo Go cannot report native crashes; a development client
   built with `eas build --profile development` can).
3. Tap the button.
4. In Sentry → the React Native project → **Issues**, the event
   appears.  Confirm `test@example.com` was scrubbed.
5. Remove the button before shipping.

**Native crash (advanced, optional)**

`Sentry.nativeCrash()` intentionally terminates the process.  Only
useful once you have a production-style build to test with; not needed
before a beta.

## Turning monitoring off

Simply remove the two DSN lines from `.env` and restart.  Nothing else
changes — the code path with a missing DSN is exercised in production
every time the app starts before an owner has configured monitoring.

## What owner action remains

- [x] Backend DSN in `backend/.env`
- [x] Frontend DSN in `frontend/.env`
- [ ] **OWNER:** run the two verification steps above **once** to
      confirm your Sentry account is receiving events
- [ ] **OWNER:** configure Sentry's server-side scrubbers as a second
      layer (dashboard → **Settings → Security & Privacy → Data
      Scrubber**).  The defaults are already conservative; enable
      **"Prevent storing of IP Addresses"** and add
      `[credit-card]`, `[password]`, `[api-key]` to the sensitive-field
      list.
- [ ] **OWNER:** during Fix 6 (real integrations), add the frontend
      Sentry auth token to EAS Build as `SENTRY_AUTH_TOKEN` so source
      maps upload automatically.  Never put that token in any `.env`
      file that ships with the app bundle.
