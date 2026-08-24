# VC-05 — Optional Google Calendar

GlamGenius can optionally read one user's Google **primary** calendar using
the read-only `https://www.googleapis.com/auth/calendar.events.readonly` scope.
Manual events remain fully supported when Google is disabled or disconnected.

The server performs the OAuth web flow, keeps only the refresh credential in
Supabase Vault, and stores the resulting opaque Vault reference in
`external_integrations.credential_ref`. Access tokens, authorization codes,
client secrets, raw OAuth state, and sync cursors are never returned to the
client or included in a privacy export. Google events are normalized into the
existing canonical `CalendarEvent` rows used by Upcoming Events, Today,
weekly planning, and Event Ready.

Configuration is disabled by default. To enable staging/production, configure
the fixed Google Console callback URI, fixed app return URI, client ID and
secret, and set `GOOGLE_CALENDAR_CREDENTIAL_STORE=supabase_vault`. Development
and CI should keep Google disabled and inject an in-memory credential store in
provider tests.

Sync is explicit: the first sync covers today through the existing 90-day
upcoming horizon, and later syncs use Google's incremental `syncToken`. A 410
response clears the cursor and performs exactly one bounded full rebuild; only
events inside that requested interval may be reconciled by absence. A malformed
provider item fails the sync before its replacement cursor is stored, so it can
be retried. Provider cancellations always revoke an imported event, while an
explicit user status correction remains authoritative for live events.

Disconnect treats only HTTP 200 (or Google's documented `invalid_token`
already-revoked response) as successful revocation. Other 400 responses,
transient failures and network errors leave the integration in
`revocation_pending`; the Vault credential is retained for retry. A terminal
refresh-token `invalid_grant` moves the integration to `reconnect_required`
without logging or persisting the token. Vault replacement updates the
existing secret UUID in place, while first-time secrets are unnamed and
collision-safe across accounts. Account deletion runs the same revocation and
Vault cleanup before removing local integration rows, and retries without
advancing its stage when cleanup is unresolved.
