# What GlamGenius needs from you before production

Everything on this list is a value or a decision that engineering cannot
legitimately invent. Anything not on this list — schema, cron implementation,
OAuth architecture, cache settings, domain modelling — is an engineering
responsibility and is already handled.

Verify at any point with:

```bash
cd backend && python -m app.release_readiness
```

Exit `0` means ready for the feature set that is configured. Exit `1` lists what
is still missing. It prints key names and statuses only, never secret values.

A value left exactly as it appears in `env.example` — anything like
`your_anon_key_here` — counts as missing, not as configured. That is deliberate:
forgetting a single key is the easiest mistake to make here, and a check that
called it ready would be worse than no check at all.

---

## A. Required before production — 9 items

Without these the API refuses to start in a `production` or `staging` tier.
That refusal is deliberate: a half-configured deployment is worse than none.

### 1. Supabase project

**What:** A production Supabase project, giving four values: project URL, anon
key, service-role key, and a private storage bucket name.
**Why:** Supabase Auth is the identity system, its Postgres is the database, and
its Storage holds inventory media.
**Where configured:** `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
`SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_STORAGE_BUCKET`.
**Secret?** The service-role key yes, absolutely — it bypasses row security and
must never reach the app bundle. The anon key is public by design.
**Can the app launch without it?** No.
**Optional feature only?** No.

### 2. Production database URL

**What:** The connection string for that project's Postgres.
**Why:** Everything the product remembers lives there.
**Where configured:** `POSTGRES_URL`.
**Secret?** Yes — it contains a password.
**Can the app launch without it?** No. A `localhost` URL is rejected in
production.
**Optional feature only?** No.

### 3. Gemini API key

**What:** A Google AI Studio / Gemini API key.
**Why:** It writes the explanations attached to decisions the deterministic
engines have already made. It never makes a safety or product decision itself.
**Where configured:** `GEMINI_API_KEY`.
**Secret?** Yes.
**Can the app launch without it?** No.
**Optional feature only?** No.

### 4. Sentry DSN

**What:** A backend Sentry project DSN.
**Why:** Errors in production are otherwise invisible. The scrubber already
strips emails, phone numbers, tokens and storage keys before anything is sent.
**Where configured:** `SENTRY_BACKEND_DSN`.
**Secret?** Treat as secret.
**Can the app launch without it?** No.
**Optional feature only?** No.

### 5. Real Privacy Policy URL

**What:** A published, reachable privacy policy page.
**Why:** It is linked from the app, and the product handles face scans and
health-adjacent preferences. The placeholder is rejected in production.
**Where configured:** `PRIVACY_POLICY_URL`.
**Secret?** No.
**Can the app launch without it?** No.
**Optional feature only?** No.

### 6. Real Support URL

**What:** A published support or contact page.
**Why:** Customers need a way to reach you. The placeholder is rejected.
**Where configured:** `SUPPORT_URL`.
**Secret?** No.
**Can the app launch without it?** No.
**Optional feature only?** No.

### 7. Production origin (your domain)

**What:** The exact origin(s) the app is served from, e.g.
`https://app.yourdomain.com`.
**Why:** CORS. The development default (`localhost`) is rejected in production,
and wildcards are refused outright.
**Where configured:** `ALLOWED_ORIGINS`.
**Secret?** No.
**Can the app launch without it?** No.
**Optional feature only?** No.

### 8. Hosting platform and scheduler

**What:** A decision, not a value: where the API runs, and what will invoke the
hourly notification worker.
**Why:** The repository deliberately does not pick a scheduler. Without one,
proactive notifications never fire — the rest of the product still works.
**Where configured:** Your platform's scheduled-job feature, cron, or a systemd
timer, running `python -m app.workers.notifications` once per hour. See
`docs/OPERATIONS.md` §6.
**Secret?** No.
**Can the app launch without it?** Yes — but push stays silent.
**Optional feature only?** No.

### 9. First admin account

**What:** The Supabase Auth user UUID of the first admin, created through the
Supabase dashboard.
**Why:** Someone has to be able to issue the first invite codes.
**Where configured:** `SUPABASE_ADMIN_USER_IDS`.
**Secret?** No.
**Can the app launch without it?** Yes, but nobody can administer the beta.
**Optional feature only?** No.

---

## B. Only if you enable Google Calendar — 3 items

Leave `GOOGLE_CALENDAR_ENABLED=false` and none of this is needed. Manual events
remain fully supported, and readiness reports the integration as
`disabled_optional` rather than failing.

### 10. Google OAuth client ID and secret

**What:** A Google Cloud OAuth 2.0 Web application client.
**Why:** To read the customer's primary calendar, read-only, with their consent.
**Where configured:** `GOOGLE_CALENDAR_CLIENT_ID`,
`GOOGLE_CALENDAR_CLIENT_SECRET`.
**Secret?** The secret, yes.
**Can the app launch without it?** Yes, with the integration off.
**Optional feature only?** Yes.

### 11. Registered redirect URI

**What:** The callback URL registered in the Google console, matching the
deployed API exactly.
**Why:** Google refuses the exchange if it does not match character for
character.
**Where configured:** `GOOGLE_CALENDAR_REDIRECT_URI`.
**Secret?** No.
**Can the app launch without it?** Yes, with the integration off.
**Optional feature only?** Yes.

### 12. Supabase Vault enabled

**What:** Confirmation that Vault is available on the Supabase project, so
`GOOGLE_CALENDAR_CREDENTIAL_STORE=supabase_vault` can be set.
**Why:** Refresh tokens live in Vault; the database keeps only an opaque
reference. There is deliberately no plaintext fallback, so the integration will
not start without it.
**Where configured:** `GOOGLE_CALENDAR_CREDENTIAL_STORE`.
**Secret?** No — the setting is a mode name.
**Can the app launch without it?** Yes, with the integration off.
**Optional feature only?** Yes.

---

## C. Only if you enable live weather and air quality — 1 item

Leave `LIVE_ENVIRONMENT_PROVIDER` empty and `OPEN_METEO_MODE=disabled`, and the
planner falls back to regional climate reasoning. It never invents a forecast.

### 13. Open-Meteo commercial credential

**What:** A commercial Open-Meteo API key, plus your confirmation that
commercial use is licensed.
**Why:** Repository policy refuses `evaluation` mode in staging and production,
and `commercial` mode refuses to start without the key. This is a licensing
decision, not a technical one, which is why it is yours.
**Where configured:** `LIVE_ENVIRONMENT_PROVIDER=open_meteo`,
`OPEN_METEO_MODE=commercial`, `OPEN_METEO_API_KEY`.
**Secret?** Yes.
**Can the app launch without it?** Yes, with live environment off.
**Optional feature only?** Yes.

---

## D. Only for real-device verification

Not required to launch; required to *prove* two things actually work end to end.

### 14. EAS project id

**What:** Run `eas init` to create the project, which writes
`expo.extra.eas.projectId` into `frontend/app.json`.
**Why:** Expo push tokens cannot be issued without it. Until it exists the
in-app control refuses to enable notifications rather than pretending it
worked — the backend neither reads it nor needs it to start.
**Secret?** No.
**Can the app launch without it?** Yes. Native push simply stays unavailable.
**Optional feature only?** Yes.

### 15. A physical iOS or Android device, and a test Google account

**What:** One real device for push, and a throwaway Google account with a few
calendar events if you enable that integration.
**Why:** Push tokens and the OAuth consent screen cannot be exercised in CI.
Every automated check that can be run has been; these two cannot.
**Secret?** No.
**Can the app launch without them?** Yes — but those two paths will be unproven.

---

## Summary

| Group | Items | Blocks launch? |
| --- | --- | --- |
| A. Required before production | 9 | Yes |
| B. Google Calendar (optional) | 3 | No |
| C. Live environment (optional) | 1 | No |
| D. Real-device verification | 2 | No |

Once group A is supplied, the deployment sequence is in the README: configure,
run the readiness check, migrate, deploy, schedule the worker, smoke test.
