# Backup / restore qualification — current evidence

**CURRENT status authority; NOT a successful recovery qualification.**

Starting main: `83d9f758a1c59be591958c437cff116e436696c6`, tree
`4971fe9d830ddf594cbb5cba5b2ca7730c278e30`. Audit Lane 6 addresses F14/F15
only. **F15 OPEN — real backup/restore qualification incomplete.** F01–F13
remain closed. This document does not authorize deployment, Phase B, paid
resources, a hosted project/branch, Store A activation or production writes.

The [old drill draft](../production/BACKUP_AND_RESTORE_DRILL.md) is preserved
unchanged as HISTORICAL / UNVERIFIED provenance. The fake-success
`scripts/simulate_backup_restore.sh` has been removed. It created no backup
and its historical success output was not recovery evidence. No replacement
simulator or pretend restore result is provided.

## Read-only provider preflight — 2026-10-08

Connected provider metadata confirms the organization is Free (`free`),
Store B is active on provider PostgreSQL release `17.6.1.166` (SQL server
version `17.6`), and physically separate hosted Store A is INACTIVE.
Neither project was modified or activated.

An aggregate-only Store B query at `2026-10-08T06:42:46.152860Z` returned:

| Aggregate | Count |
| --- | ---: |
| Auth users | 0 |
| Storage buckets | 1 |
| Storage objects | 0 |
| Vault secret rows | 2 |
| External integrations | 0 |

An earlier same-day aggregate query also found zero Vault-backed integration
references. These are point-in-time observations, NOT dump/restore parity,
application constants or proof of recovery. No emails, object paths, secret
plaintext or identifying application rows were queried for this evidence.

## F14 — Vault deletion contract

Read-only catalog inspection confirms extension `supabase_vault 0.3.1`.
`vault.create_secret(text, text, text, uuid)` returns `uuid`;
`vault.update_secret(uuid, text, text, text, uuid)` returns `void`.
The catalog contains **zero** functions named `vault.delete_secret`.

The production credential adapter now uses only:

```sql
DELETE FROM vault.secrets WHERE id = CAST(:id AS uuid)
```

The opaque `supabase-vault:` prefix, `_id()` rejection, bound parameter and UUID
cast remain unchanged. The approved correction adds `session.begin_nested()`
in `SupabaseVaultCredentialStore.delete()` around ONLY that exact DELETE,
using the existing `AsyncSession`. The store does not commit, open another
session, roll back the outer transaction or swallow the database exception.
No RPC, grant,
SECURITY DEFINER function or migration was added. The decrypted view is not
a deletion target. Repeating deletion of an absent valid UUID is harmless.

Production was NOT used for a deletion smoke test. A non-mutating `EXPLAIN
DELETE` with the all-zero dummy UUID returned `Delete on secrets` backed by
`Index Scan using secrets_pkey` and the exact UUID `Index Cond`. `ANALYZE`
was not used; no row was deleted.

`test_vault_deletion_authority.py` proves exact bound SQL/prefix rejection,
unrelated-row preservation, malformed-UUID rejection and idempotent replay.
Its PostgreSQL table has just a UUID primary key. The fixture refuses a
non-loopback host, a database not named as a test database, or an existing
Vault schema. It commits its synthetic setup to permit real outer-transaction
commit/fresh-observer proofs, then removes only its own fixture tables/schema.
It is a SQL-contract fixture, **not Supabase Vault encryption or decryption**.
The local runtime is PostgreSQL/psql `16.15`; no real local Vault smoke was
possible because Docker's Linux engine is unavailable.

Disconnect tests use explicit asyncio events to prove local events are
revoked and credential authority stays pending throughout an awaited delete.
An adapter exception keeps `credential_ref` and `revocation_pending`; a
successful delete precedes clearing that reference and declaring `revoked`.
The account-deletion coverage retains the adapter-failure test and adds a
real PostgreSQL FK-blocked DELETE through the actual privacy worker. It
proves a committable `failed_retryable` job at `integrations_deleting`, retained
account/credential authority, locally revoked imported events, and no Auth
erasure or false completion. Removing only the synthetic FK obstruction lets
the normal retry complete. Google revocation/Auth calls are stubbed; the
Vault DELETE and transaction recovery are real PostgreSQL statements.
No production token is used.

Executed F14 mutants (all killed, then restored):

| Mutant | Observed result |
| --- | --- |
| Revert to nonexistent `vault.delete_secret` | 2 tests fail: exact SQL assertion and PostgreSQL undefined function. |
| Remove UUID predicate | 2 tests fail: exact SQL assertion and unrelated synthetic row preservation. |
| Clear reference in disconnect's cleanup exception handler | 2 tests fail: disconnect and real account-deletion retained-reference assertions. |
| Remove the DELETE SAVEPOINT | 1 retained PostgreSQL regression fails with `InFailedSQLTransactionError` on the caller's next SQL statement. |

These four mutants were rerun against the corrected test coverage, then fully
restored. No move-pending-inside-SAVEPOINT mutant, F15 harness, Storage or
Store A importer mutant is claimed.

### Retained failure and approved transaction correction

After the 162-case targeted suite passed, the stronger real PostgreSQL test
`test_postgres_delete_error_preserves_committable_pending_disconnect` exposed
a gap not covered by adapter-level failure injection. A LOCAL synthetic
foreign-key restriction causes the exact DELETE to fail. Disconnect returns
`revocation_pending`, but the caller's next SQL statement raises
`InFailedSQLTransactionError: current transaction is aborted, commands ignored
until end of transaction block`. Its in-memory pending status was not proof
of committable revocation authority. That failure was reported before making
the transaction change; the user then explicitly approved the narrow
credential-store SAVEPOINT.

The retained test now observes PostgreSQL SQLSTATE `23503` from the actual
exact DELETE, `ROLLBACK TO SAVEPOINT`, successful subsequent SQL on the SAME
session and unchanged outer transaction identity. The caller commits that
transaction; a fresh session confirms persisted `revocation_pending`, the
original credential reference, NULL `revoked_at`, and revoked imported events.
The test was strengthened, not removed, skipped or weakened.

Both present-UUID and already-absent-UUID success tests observe normal
SAVEPOINT release, a usable unchanged outer transaction, commit, and fresh
session confirmation of `revoked`, cleared reference/cursor/error and revoked
events. The unrelated synthetic UUID survives.

SQL tracing also proves the pending integration/event UPDATEs precede provider
revocation, with no SAVEPOINT around that provider call. These states remain
outside the DELETE savepoint. This follows the documented
[SQLAlchemy SAVEPOINT model](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#using-savepoint),
including its pre-SAVEPOINT flush. The disconnect locking/ordering and
fail-closed privacy rule were not changed.

Local checks: the retained PostgreSQL regression passed first; the
complete Vault file passed **16 tests**, and the expanded Calendar/planning/
privacy/account-deletion/ODbL/recovery-status set passed **298 tests**
(`2 warnings`). Ruff, compileall, changed-file Gitleaks and `git diff --check`
passed. Alembic still has one head, `o3p4q5r6s7`. After all mutants were
restored, the Vault/status/adapter-failure subset passed **19 tests**
(`2 warnings`). The separate `INVITE_REQUIRED=true` reservation/bypass
follow-up passed **17 tests** (`2 warnings`). Final isolated Linux
qualification passed **8158 tests, 1 skipped, 140 warnings** in `1613.55s`;
its invite-required follow-up passed **17 tests, 2 warnings** in `7.08s`.
The only skip was the optional PyYAML comparison in
`test_production_runtime_foundation`; PyYAML is absent from this runtime.
No PostgreSQL regression was skipped. The final normal-durability rerun
passed the complete Vault file again (**16 tests, 2 warnings**, `21.67s`)
and the invite follow-up again (**17 tests, 2 warnings**, `60.56s`).

### Current publication and F14 review authority

The implementation was published in [draft PR #221](https://github.com/blazebrt/GlamGenius/pull/221).
Publication commit: `587e8f26223f69be29901e843aaae452a7dba43d`; publication
tree: `970338b02074784c8dfed1b35c708df6c145d492`; exact parent/base:
`83d9f758a1c59be591958c437cff116e436696c6`. The PR remains
**DRAFT / OPEN / UNMERGED**. The recovered local Git-fixture incident below
is historical qualification evidence, not an unpublished delivery state.

[Exact-head CI #906 / run 37763125254](https://github.com/blazebrt/GlamGenius/actions/runs/37763125254)
completed **SUCCESS** for that publication commit/tree. Canonical backend:
**8158 passed, 1 skipped, 140 warnings**; invite-required follow-up:
**17 passed, 2 warnings**. The PR gate passed, and Gitleaks reported
**no leaks found**. These results qualify the implementation publication
head; any later docs-only correction requires its own scope-selected
exact-head CI and must not be described as a new backend-suite run.

Independent ChatGPT exact-tree review accepted the F14 transaction
implementation. The only review correction requested was this stale-status
documentation update; no F14 application code or tests are changed by it.
**F14 implementation independently accepted — formal closure pending merge
and post-merge push qualification.** F14 is not declared closed: formal
closure requires the approved tree to merge and the subsequent full push CI
to succeed. This PR is not merge-authorized, and F15 remains OPEN.

### Qualification environment failures and final isolated Linux result

The initial full run encountered the local default `/data/media`, which maps
to unwritable `C:\data` on this desktop. Setting `MEDIA_LOCAL_ROOT` to owned
temporary test storage (as CI does) resolved that failure: the affected Care/
history/community batch group passed **13 tests** (`2 warnings`). This was an
environment-only change; no repository configuration was altered.

The restarted full run then reported additional failures. It was interrupted
to reproduce the first remaining one:
`test_the_real_deletion_lifecycle_removes_a_reporter_from_the_public_count`
failed at the retained-report assertion (`1 failed, 2 warnings`). The deletion
worker recorded `StorageMisconfigured` at storage cleanup. This is not a
Vault DELETE failure: that test creates no Google integration.

The actual local adapter's `list_prefix()` returns
`str(p.relative_to(self.root))`, which uses backslashes on Windows, while
`_path_for()` rejects every backslash. A read-only probe of the owned synthetic
fixture listed three keys, confirmed backslashes, and reproduced
`StorageMisconfigured: Invalid storage key` before any deletion. Linux CI
uses POSIX paths; hosted production refuses this development/test adapter.
No hosted storage state was touched.

This unrelated adapter was NOT changed under the narrow Vault approval.
Neither partial Windows run supplies a passing total, and other observed
Windows failures were not all independently classified. The complete Linux
run above supplies the final passing functional regression result.

The existing Ubuntu environment initially had no compatible Python/test
runtime or PostgreSQL. Isolated portable Python `3.11.17`, PostgreSQL `16.15`
and the unchanged repository requirements were prepared locally; no system
package or repository dependency file was changed. The first Linux run was
interrupted after a heartbeat-age failure: this new test database used
`Asia/Calcutta`, so a heartbeat 120 seconds old was reported as `-19679s`.
Changing ONLY the disposable test database to UTC made the unchanged test
pass (**1 test, 2 warnings**). Its non-UTC partial run is not passing evidence.

The mounted Windows checkout also carried CRLF shell scripts, making Bash
reject `set -euo pipefail`. Seven CI-scope tests failed for that reason.
A temporary Git-canonical LF source export, with the existing uncommitted
changes overlaid, made those unchanged tests pass (**7 tests, 2 warnings**).
No original source or CI file was edited to repair this checkout condition.

The initial source-export harness incorrectly inherited `GIT_DIR` and
`GIT_WORK_TREE` into tests that create throwaway Git repositories. An audit
fixture therefore created unintended local `seed` commit
`732c6b044f6de039839d923a85260c3c0e311315`, whose parent was the required
`83d9f758a1c59be591958c437cff116e436696c6`. Nothing was pushed, and original
working files were unchanged. The branch reference was compare-and-swap
restored to the required HEAD and the prior unstaged index restored WITHOUT
updating/resetting working files. The temporary commit remains recoverable
in local Git history/reflog, and an index recovery copy was kept outside Git.
The final original HEAD/tree and eight uncommitted paths were verified.

Final qualification used a private source copy with INDEPENDENT Git refs and
index, no Git environment overrides, and read-only borrowed object storage.
All **1142** candidate files matched the original after checkout-only CRLF/LF
normalization. Both canonical manifests had SHA-256
`64e1b066d3d6a27e7d72562a5978905b5f12311643c37e2d9211f30a1e19febc`.
The audit/scope isolation preflight passed **25 tests, 2 warnings**, with both
qualification/original HEADs unchanged. The complete final suite and invite
follow-up then passed with the counts recorded above. The qualification copy
never replaced the original implementation branch.

For the broad functional suite ONLY, the disposable PostgreSQL server used
`fsync=off`, `synchronous_commit=off`, and `full_page_writes=off` to avoid slow
local test-disk writes. No transaction isolation or assertion was changed.
These are NOT production or backup settings and prove no crash durability.
The mandatory Vault SQL/outer-commit proofs passed first with normal settings
and were repeated after the full suite with all three settings ON, UTC, and
zero pre-existing Vault schemas. Ruff, compileall and the single Alembic head
were rechecked. These tests remain synthetic SQL-contract qualification,
not a real Supabase Vault encryption test or any F15 recovery evidence.

## F15 — blocked at the secure-execution preflight

This desktop is founder-local, but a usable production Store B database URL/
password is **not configured** in the checked process settings or local
project environment files. Read-only provider SQL access does not supply a
credential to a local logical-export tool, and must not be used to stream
private production rows through chat as an alternative.

| Capability | Observed state |
| --- | --- |
| Supabase CLI/version/`db dump --help` | Not available; no CLI dump executed. |
| psql | Not on Windows PATH; Windows and isolated Linux test binaries are `16.15`, not a Supabase PG17/Vault restore target. |
| Docker client | `29.1.3`, build `f52814d`. |
| Docker Linux engine / local Supabase PG17/Vault | Unavailable: `dockerDesktopLinuxEngine` pipe not found. |
| Production DB credentials | Not available in checked secure local configuration. |
| Roles/schema/data dumps | NOT RUN; no files, sizes or SHA-256 values to report. |
| Isolated Store B restore / verification | NOT RUN; no source/restored manifest parity. |
| Backup/restore/verification/total duration | NOT MEASURED; not zero-second successful operations. |
| Local synthetic Auth / Storage recovery | NOT RUN. |
| Store A local export/import recovery | NOT RUN; no import path was added in this blocked attempt. |

Therefore F15 work stops at preflight. There is no qualified recovery harness
or real production-source restore to claim. Removing the fake simulator,
passing CI migrations or testing synthetic UUID deletion does not close F15.
**No guaranteed production RPO is currently claimed.** No measured RTO or
contractual recovery SLA is claimed either.

## Boundaries for the still-required real drill

Use [current official CLI backup/restore guidance](https://supabase.com/docs/guides/platform/migrating-within-supabase/backup-restore)
and [platform-to-local/self-hosted restore guidance](https://supabase.com/docs/guides/self-hosting/restore-from-platform),
after checking installed CLI help. Separate roles, schema and data artifacts
are required; a single default schema dump is not a complete backup. Final
restore must be isolated and fail on every SQL error, with source/restored
manifest verification, measured timestamps/durations and artifact sizes/
hashes. Use a supported Supabase PG17 environment; this local PG16 SQL test
database is not that target. The current changelog, including the
[PG17 self-hosting change](https://supabase.com/changelog/46080-self-hosted-supabase-upgrading-from-pg-15-to-17-breaking-change)
and [minor-release compatibility notice](https://supabase.com/changelog/postgres-15-19-17-11-breaking-changes),
was checked; no provider upgrade or configuration change was performed.

- **Auth:** logical user rows and JWT/API/OAuth/SMTP authority are different
  boundaries. No row recovery or existing-session validity was proved here.
- **Storage:** the zero-object preflight is not an executed byte-transfer
  proof. Database metadata does not back up object bytes. Re-measure for the
  real drill and qualify a separate synthetic LOCAL byte recovery path; if
  actual objects exist, their protected local recovery set is also required.
- **Vault:** the two encrypted rows were counted only. No secret was read,
  exported or decrypted. A database restore alone cannot prove decryptability:
  [Vault's key is outside ordinary database data](https://supabase.com/docs/guides/database/vault#encryption-key-location).
  Until independently verified safe, affected Calendar credentials require
  reconnection, not a claim that old opaque references are usable. Neither
  key extraction nor production credential mutation is authorized.
- **Store A:** hosted Store A stayed inactive and untouched. Its future LOCAL
  ODbL cache export/import proof must remain physically separate, enforce
  `OFF_FIELDS`, verify manifest hashes, preserve attribution and restore
  `fetched_at` as NULL/unknown. Never load Store B into Store A or acquire a
  Store B session from its importer. The existing ODbL wall is unchanged.
- **Private artifacts:** no SQL dumps or Storage bytes were created here.
  Future dumps belong only in protected temporary operator storage, never Git,
  public/shared CI artifacts, chat or shared buckets. Never print credentials.

F14's retained transaction correction passed independent implementation
review and publication-head CI #906 as recorded above. Formal F14 closure
still requires merge and successful post-merge full push qualification; the
docs-only status correction awaits final re-review. F15 remains OPEN, and
Lane 6 is not complete.
**DO NOT MERGE — awaiting independent ChatGPT final F14 re-review.**
