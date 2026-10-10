These scripts and mode flags were captured from the actual Docker create
requests emitted by installed Supabase CLI 2.120.0 against a local synthetic
Docker capture server. No production credential or connection was used.

Incoming commands must match these scripts byte-for-byte. The proxy discards
incoming command text and constructs the final command from these trusted
files, replacing only the fixed PGPASSWORD export with an environment check.
Only the approved mode flags and operator-pinned connection target are used.
Unknown flags, environment variables, command modifications and entrypoints
fail closed. Updating the CLI requires new reviewed templates and qualification.

Upstream authority:
https://github.com/supabase/cli/blob/v2.120.0/apps/cli/src/command-internal/pg-dump.scripts.ts
https://github.com/supabase/cli/blob/v2.120.0/apps/cli/src/command-internal/pg-dump.run.ts
https://github.com/supabase/cli/blob/v2.120.0/apps/cli/src/command-internal/pg-dump.env.ts

The upstream MIT copyright and permission notice is included in LICENSE.
