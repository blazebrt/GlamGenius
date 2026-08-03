# ADR 0001 — MongoDB (V1) alongside Postgres (V2)

**Status:** Accepted. **Date:** 2026-08-03. **Deciders:** @blazebrt.

## Context

The application originally shipped on MongoDB (V1). The V2 rewrite,
started in a previous phase, uses PostgreSQL as the authoritative
store for stronger transactional guarantees, real foreign keys, and
Alembic-managed migrations. Both stores are in production today.

## Decision

Run both stores concurrently during the deprecation window. V1
routes under `/api/*` keep reading and writing MongoDB; V2 routes
under `/api/v2/*` read and write Postgres. No dual-write is
attempted — each store is authoritative for the domain it owns.

## Consequences

- Users have some data in Mongo (scans, quiz, invites) and some in
  Postgres (V2 profile, inventory, media, entitlements). The
  frontend chooses per-domain which endpoint to call.
- Any cross-domain read that spans both stores does so at the API
  layer; the databases do not join.
- V1 will be retired per `docs/stabilisation/V1_DEPRECATION_PLAN.md`
  after V2 covers all in-app surfaces.

## Alternatives considered

- **Dual-write**: rejected. Two write paths that must agree is a
  reliability tax and a debugging tax. Better to migrate a domain
  fully than to sync it forever.
- **Big-bang cutover**: rejected. The V2 rewrite is not finished for
  every V1 domain; a cutover would drop history.

## Related

- `docs/stabilisation/V1_DEPRECATION_PLAN.md`
- `backend/app/config.py::POSTGRES_URL`, `backend/database.py`
