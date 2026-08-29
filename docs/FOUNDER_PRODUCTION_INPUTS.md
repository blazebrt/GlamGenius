# Founder production inputs

Engineering has completed the code-only readiness work. Supply the following
once production deployment is being prepared; do not put secrets in Git.

## Required before production

### Supabase project configuration

What: Project URL, anon key, service-role key, JWKS endpoint, JWT issuer, and the private media bucket.

Why: Authentication, storage, and server-side administration depend on the selected Supabase project.

Where it will be configured: backend deployment environment (`SUPABASE_*`) and frontend build environment (`EXPO_PUBLIC_SUPABASE_*`).

Secret? yes — except the project URL and anon key.

Can app launch without it? no.

Required only for optional feature? no.

### Production PostgreSQL URL

What: Production database connection URL.

Why: The API and migrations need the production database.

Where it will be configured: backend deployment environment (`POSTGRES_URL`).

Secret? yes.

Can app launch without it? no.

Required only for optional feature? no.

### Gemini API key

What: Gemini API key.

Why: The approved AI gateway requires it in production.

Where it will be configured: backend deployment environment (`GEMINI_API_KEY`).

Secret? yes.

Can app launch without it? no.

Required only for optional feature? no.

### Sentry backend DSN

What: Backend Sentry DSN.

Why: Production validation requires privacy-scrubbed error monitoring.

Where it will be configured: backend deployment environment (`SENTRY_BACKEND_DSN`).

Secret? yes.

Can app launch without it? no.

Required only for optional feature? no.

### Legal and support URLs

What: Real Privacy Policy and Support URLs.

Why: Safe placeholder URLs are deliberately rejected outside development.

Where it will be configured: backend deployment environment (`PRIVACY_POLICY_URL`, `SUPPORT_URL`).

Secret? no.

Can app launch without it? no.

Required only for optional feature? no.

### Production frontend origin

What: The deployed frontend origin or origins.

Why: CORS must not use development localhost defaults in production.

Where it will be configured: backend deployment environment (`ALLOWED_ORIGINS`).

Secret? no.

Can app launch without it? no.

Required only for optional feature? no.

### Hosting and notification scheduler decision

What: Hosting platform and the mechanism that runs `python -m app.workers.notifications` once per hour.

Why: The application intentionally does not own a scheduler service.

Where it will be configured: chosen hosting-provider scheduler, outside this repository.

Secret? no.

Can app launch without it? yes — but proactive push reminders will not run.

Required only for optional feature? yes — native push.

## Required only if enabling Google Calendar

### Google OAuth registration

What: OAuth client ID, OAuth client secret, and registered server redirect URI.

Why: Calendar access is read-only and remains disabled until complete OAuth and Supabase Vault configuration exists.

Where it will be configured: backend deployment environment (`GOOGLE_CALENDAR_CLIENT_ID`, `GOOGLE_CALENDAR_CLIENT_SECRET`, `GOOGLE_CALENDAR_REDIRECT_URI`) with `GOOGLE_CALENDAR_CREDENTIAL_STORE=supabase_vault`.

Secret? yes — the client secret.

Can app launch without it? yes.

Required only for optional feature? yes.

## Required only if enabling live Open-Meteo commercial mode

### Open-Meteo commercial credential

What: Commercial Open-Meteo API credential.

Why: Commercial live weather and air-quality context requires it; evaluation mode is not allowed in staging or production.

Where it will be configured: backend deployment environment (`LIVE_ENVIRONMENT_PROVIDER=open_meteo`, `OPEN_METEO_MODE=commercial`, `OPEN_METEO_API_KEY`).

Secret? yes.

Can app launch without it? yes, while live environment context is disabled.

Required only for optional feature? yes.

## Real-device verification

### Test access

What: A test Google account if Calendar is enabled, plus a physical iOS or Android device if native push is being verified.

Why: OAuth and Expo push require real-provider/device smoke testing after deployment.

Where it will be configured: staging test process, not repository configuration.

Secret? no.

Can app launch without it? yes.

Required only for optional feature? yes.
