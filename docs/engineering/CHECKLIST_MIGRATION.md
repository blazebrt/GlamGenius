# Migration review checklist

Use this checklist when the PR touches `backend/migrations/**` or adds
any script that alters production data. Migrations are the class of
change with the highest cost of getting it wrong; the review bar
matches.

## 1. Migration files 0001–0008 are frozen

- [ ] No committed migration file numbered 0001 through 0008 is edited.
- [ ] Any correction to those migrations is a new numbered migration,
      not an in-place rewrite.
- [ ] The PR does not delete or rename a committed migration file.
- [ ] `alembic history` on the head commit still shows a linear chain
      from 0001 to the new head, with no branches.

## 2. New migration file

- [ ] Filename follows `NNNN_short_description.py` (Alembic convention).
- [ ] `down_revision` points at the actual current head, not a stale
      one.
- [ ] `revision` is a new hash, not a copy-paste of another migration's.
- [ ] The migration is self-contained: no relative import of app code
      that will be gone tomorrow. If a model is needed, redefine the
      minimum table shape locally in the migration.

## 3. Upgrade path

- [ ] `alembic upgrade head` on an empty database succeeds. CI's
      `backend-tests` job proves this.
- [ ] `alembic check` reports no drift between the head migration and
      the SQLAlchemy metadata.
- [ ] The migration is idempotent enough to be re-run after a partial
      failure (or the PR describes the manual step required).
- [ ] Long-running operations (large table rewrites, index creation on
      hot tables) use `CREATE INDEX CONCURRENTLY` / `ALTER TABLE ...
      SET STATISTICS` / batched updates rather than blocking the table.
- [ ] The migration does not require an application restart between
      steps (or the PR describes the required deployment order).

## 4. Downgrade path

- [ ] The `downgrade()` function is present and reversible for the
      structural change.
- [ ] For destructive migrations (dropping a column, dropping a
      table), the downgrade documents that data is not recoverable
      once upgrade runs — and the PR explains the retention plan.
- [ ] CI's `alembic-round-trip` job (upgrade → downgrade one →
      re-upgrade) is green.

## 5. Backfill

- [ ] Any backfill is idempotent: running it twice produces the same
      final state.
- [ ] Any backfill has a dry-run mode that reports the number of rows
      that would change without changing them.
- [ ] The dry-run output is pasted in the PR description before the
      migration runs against a real database.
- [ ] The backfill batches its work if the table is large enough that
      a single transaction would block reads.

## 6. Rollback plan

- [ ] The PR's "Rollback plan" section names the exact command to
      undo the change (`alembic downgrade <revision>` and, if
      relevant, a data-restore step).
- [ ] The rollback is safe to run without the application code that
      the migration originally shipped with.
- [ ] If a rollback would lose committed customer data, the PR says
      so in bold.

## 7. Data safety

- [ ] The migration does not silently truncate free-text fields.
- [ ] The migration does not drop a column that any deployed version
      of the application still reads. Two-phase rollout (add column →
      dual-write → migrate → drop column) is used when needed.
- [ ] The migration does not add a `NOT NULL` column without a
      default, on a non-empty table.
- [ ] The migration does not add a unique constraint on a column that
      may already contain duplicates in production (or the PR includes
      the dedup step).

## 8. Payment tables

- [ ] The diff does not touch `orders`, `subscriptions`, `refunds`,
      `payment_events`, `billing_events` or the equivalent Postgres
      billing schema. This is the non-payment stabilisation phase
      rule; a "small clean-up" of a payment table is out of scope.

## 9. Evidence

- [ ] `pytest -q tests/test_v1_regression.py
      tests/test_config_flags_and_billing.py` passes.
- [ ] `alembic upgrade head && alembic check && alembic downgrade -1
      && alembic upgrade head` on a local Postgres passes and the
      output is pasted in the PR.

## 10. Sign-off

- [ ] Independent reviewer per [`REVIEW_POLICY.md`](REVIEW_POLICY.md).
- [ ] Owner approval per CODEOWNERS (`backend/migrations/**` matches).
