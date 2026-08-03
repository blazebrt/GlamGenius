# ADR 0002 — Feature flags via V2_FEATURES + Postgres override

**Status:** Accepted. **Date:** 2026-08-03. **Deciders:** @blazebrt.

## Context

The V2 rewrite ships domain-by-domain. Every domain needs a way to
be turned off in a running deployment without a code push.

## Decision

Boot-time defaults come from the `V2_FEATURES` environment variable
(comma-separated flag keys — see `backend/app/config.py`). A
Postgres-backed table (`v2_features`) can override the boot default
at runtime. Reads are cached with a short TTL.

## Consequences

- Fresh deployments start with the flags in `V2_FEATURES`. An empty
  value starts closed.
- An operator can turn a domain off in production without a code
  push by writing to the override table.
- The frontend reads the resolved flag set from `/api/v2/config`,
  so a paid action never shows up when its flag is off.

## Alternatives considered

- **Env vars only**: rejected. Every flag flip needs a deploy.
- **A third-party feature-flag SaaS**: rejected for the beta. Extra
  vendor, extra credential, extra failure mode.

## Related

- `backend/app/config.py::V2_FEATURES`
- `backend/app/api/v2/config.py`
