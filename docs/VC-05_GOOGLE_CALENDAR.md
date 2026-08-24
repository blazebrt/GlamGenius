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
be retried, and a final page without a usable `nextSyncToken` is also rejected.
The bounded reconciliation considers every non-revoked imported row, including
locally dismissed rows: an absent row is revoked, while a present row retains
its dismissal and field overrides. Provider cancellations always revoke an
imported event, while an explicit user status correction remains authoritative
for live events.

A one-time OAuth nonce is stored only as a hash, and is spent on first use.
Starting a new authorization clears that account's own consumed and expired
nonces, so the table cannot grow without bound and no separate sweep job is
needed. A nonce that is still live is never pruned, and one account's
authorization never touches another account's rows.

A correction sent as an explicit null is not a correction: it claims no field
and leaves later provider syncs free to update it. Only a field the user
actually set becomes authoritative over Google.

Disconnect treats only HTTP 200 as confirmed successful revocation. Provider
errors, transient failures and network errors leave the integration in
`revocation_pending`; importantly, the integration is marked pending and all
imported events are revoked locally before any remote or Vault work begins, so
planning stops immediately even if cleanup later fails. There is no background
retry: the customer-facing copy says so plainly and offers a Retry control, and
account deletion runs the same cleanup. The Vault credential is
retained for retry until remote revoke and Vault deletion both succeed. The
generic calendar DELETE is manual-only; Google uses its dedicated secure
disconnect route. A terminal refresh-token `invalid_grant` moves the
integration to `reconnect_required` without logging or persisting the token,
and only that recovery path requests explicit Google consent. Vault replacement
updates the existing secret UUID in place, while first-time secrets are
unnamed and collision-safe across accounts. The Google identity index is
partial, so valid legacy/manual duplicate external IDs remain intact. Account
deletion runs the same revocation and Vault cleanup before removing local
integration rows, and retries without advancing its stage when cleanup is
unresolved.
