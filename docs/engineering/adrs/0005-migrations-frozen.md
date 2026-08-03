# ADR 0005 — Migrations 0001–0008 frozen; corrections are new migrations

**Status:** Accepted. **Date:** 2026-08-03. **Deciders:** @blazebrt.

## Context

Migrations 0001–0008 have been deployed to at least one environment
that carries real user data. Editing a deployed migration in place
would leave live databases at a schema state that no code in the
repo matches.

## Decision

- Migrations 0001–0008 are frozen. They are not edited in place,
  renamed, or deleted.
- A correction to any of them is a new numbered migration
  (`backend/migrations/versions/0009_*.py` and onwards).
- CI enforces this via `alembic-round-trip` (upgrade → downgrade →
  re-upgrade) in the `Alembic round-trip` job. Every PR to `main`
  runs that job.

## Consequences

- The migration history is append-only from the frozen point
  forward.
- Even a "trivial" typo fix in a deployed migration is a new file.
- Reviewers walk `docs/engineering/CHECKLIST_MIGRATION.md` on every
  migration-touching PR.

## Alternatives considered

- **Amend in place with a data-fix script**: rejected. Amending a
  deployed migration silently is exactly the failure mode this ADR
  prevents.
- **Squash regularly**: rejected. Squashing forces re-runs on live
  databases that have already advanced past the squashed set.

## Related

- `backend/migrations/versions/`
- `docs/engineering/CHECKLIST_MIGRATION.md`
