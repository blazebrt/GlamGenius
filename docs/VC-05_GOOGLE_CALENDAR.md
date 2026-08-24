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
response clears the cursor and performs one bounded full rebuild. Disconnect
revokes the grant, deletes the Vault secret, deactivates imported events, and
never removes manual events. Account deletion runs the same cleanup before the
database account is removed.
