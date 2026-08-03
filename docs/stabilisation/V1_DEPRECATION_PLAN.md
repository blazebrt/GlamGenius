# V1 deprecation plan (Fix 11, WP4)

## Summary

V1 (MongoDB-backed) is the original API. V2 (Postgres-backed) is
the rewrite. Both are in production today. This document is the
plan for retiring V1 without dropping any user's data.

## What still runs on V1

Live V1 endpoints (see `backend/routes/`):

- `/api/auth/*` — invite-gated register/login, JWT issuance.
- `/api/users/*` — user profile read/write.
- `/api/scan/*` — image analysis + history + trends.
- `/api/quiz/*` — style-quiz questions and submissions.
- `/api/plans/style` — style plan generation.
- `/api/recommendations/*` — advice generation and history.
- `/api/services`, `/api/salon-ideas` — static catalogue.
- `/api/subscription/*` — billing (behind `SUBSCRIPTIONS_AVAILABLE=false`).
- `/api/admin/invites` — admin-secret gated invite management.

Data still on Mongo:

- `db.users` — one document per registered user.
- `db.scans` — one document per completed scan.
- `db.invites` — invite codes and their usage.
- `db.quiz_results` — quiz submissions.
- `db.recommendations` — advice history.

## Phased retirement

The retirement is domain-by-domain, not big-bang. Each phase is a
work-package-sized PR of its own, after Work Package 6 lands.

### Phase A — read-parity

For every V1 endpoint, ship an equivalent V2 endpoint on Postgres.
Both endpoints stay live; the frontend can call either. This work
is largely done for profile, media, consent, inventory, and
routines (`/api/v2/*` covers them). Remaining domains: quiz,
recommendations history, scan history.

**Exit criteria:** every V1 read is also served by a V2 read
against the same underlying account.

### Phase B — dual-write

For a bounded window (weeks, not months), every V1 write also
lands the equivalent V2 record. The V1 read remains authoritative.
This phase catches translation defects on live traffic before we
switch reads.

**Exit criteria:** no V1 write in the window produced a V2 record
that could not be read back with equivalent fields.

### Phase C — cut reads to V2

Flip the frontend to read from V2 endpoints for the migrated
domains. V1 writes still land in Mongo but no user sees them —
they are a safety net.

**Exit criteria:** at least one full billing month with V2 as the
read side and zero incidents caused by the switch.

### Phase D — one-way migration + freeze V1 writes

Backfill every remaining Mongo record to Postgres via a scripted,
idempotent migration (mirroring the Fix 12 pattern). Freeze V1
writes on the same commit.

**Exit criteria:** V1 read/write is disabled at the router; every
domain answers on V2 only.

### Phase E — remove V1 code

Delete `backend/routes/`, `backend/database.py` (Mongo client),
`backend/models.py` (Mongo models). Update `docker-compose.yml` to
drop the Mongo service in the dev stack.

**Exit criteria:** the repository does not import `motor` or
`pymongo` anywhere.

## Constraints during deprecation

- Migrations 0001–0008 remain frozen (ADR 0005).
- No user data is destroyed. The Mongo instance remains online
  after Phase E for at least one billing cycle in cold storage.
- Payment mechanics are unchanged until the phase-7 billing
  work-package starts (see the top-level plan).
- Any consent revocation in either store must be honoured in both
  until Phase E; the V2 privacy scrubber
  (`backend/app/api/v2/privacy.py`) is the single authoritative
  deletion path.

## Order relative to the stabilisation phase

- **This branch (WP4):** publish the plan (this document). No code
  change to V1 routes.
- **After WP6 closes:** open a new series of PRs (`v1-retire/N-*`)
  that walks Phases A → E one at a time.

## Cross-references

- [ADR 0001 — dual datastore](../engineering/adrs/0001-dual-datastore.md)
- [ADR 0005 — migrations frozen](../engineering/adrs/0005-migrations-frozen.md)
- `docs/engineering/CHECKLIST_MIGRATION.md`
