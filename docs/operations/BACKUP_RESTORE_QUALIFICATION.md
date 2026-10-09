# Backup / restore qualification — current evidence

**Authority: F14 historical implementation/evidence and the dated, executed
F15 live-source recovery evidence below. F01–F14 are CLOSED.
F15 recovery qualification is complete. Formal finding closure is determined by
repository exact-tree merge/post-merge CI authority and is recorded by the audit
closure process, not inferred from this document alone.**

Starting main: `83d9f758a1c59be591958c437cff116e436696c6`, tree
`4971fe9d830ddf594cbb5cba5b2ca7730c278e30`. Audit Lane 6 addresses F14/F15
only. The October 8 F15 evidence supersedes the initial tooling blocker.
F01–F14 remain closed. This document does not authorize deployment, Phase B, paid
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

The repository production credential adapter uses only:

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

### Durable F14 implementation qualification

**F14 implementation qualification is complete. Formal finding closure is
determined by repository merge/post-merge CI authority and is recorded by the
audit closure process, not inferred from this document alone.**

Independent exact-tree review accepted the transaction behavior, PostgreSQL
SAVEPOINT semantics, retained credential authority and privacy retry tests
described above. The immutable publication commit/tree and qualification
runs are recorded in the dated checkpoint below. Formal closure uses Git
merge-tree integrity and successful post-merge full push CI, not mutable
PR-state prose in this document. Neither implementation qualification nor
finding closure supplies production backup/restore evidence for F15.

### Publication/review checkpoint — 2026-10-08

This dated checkpoint records historical publication/review evidence, not
live PR state or a current formal-closure decision.

At this checkpoint, [PR #221](https://github.com/blazebrt/GlamGenius/pull/221)
was **DRAFT / OPEN / UNMERGED**. Its implementation publication commit was
`587e8f26223f69be29901e843aaae452a7dba43d`, tree
`970338b02074784c8dfed1b35c708df6c145d492`, with exact parent/base
`83d9f758a1c59be591958c437cff116e436696c6`. The prior stale-status docs
correction head was `d47fa8edf816cdab62bc61aae48de2310d44cbb7`, tree
`d6372d63025b886d35ccdbd0259100c30ab9bb18`, with the publication commit
as its exact parent. The recovered local Git-fixture incident below was
historical qualification evidence, not an unpublished delivery state.

[CI #906 / run 37763125254](https://github.com/blazebrt/GlamGenius/actions/runs/37763125254)
qualified the implementation publication head, and
[CI #907 / run 37767788110](https://github.com/blazebrt/GlamGenius/actions/runs/37767788110)
qualified the docs-corrected head. Both completed **SUCCESS** with canonical
backend **8158 passed, 1 skipped, 140 warnings** and invite-required
follow-up **17 passed, 2 warnings**. Both PR gates passed, and Gitleaks
reported **no leaks found**. These immutable run results are not a claim
that CI ran on any later head.

Independent ChatGPT review had accepted the F14 implementation and the
prior stale-status documentation correction. F15 remained OPEN, with no
production-source backup/restore qualification established at that
checkpoint.

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

## F15 — real live-source recovery, 2026-10-08 UTC

**F15 recovery qualification is complete. Formal finding closure is determined
by repository exact-tree merge/post-merge CI authority and is recorded by the
audit closure process, not inferred from this document alone.**

Ordinary live database parity and the required local recovery/implementation
checks passed. Qualification does not authorize merge or deployment.

Machine-readable evidence: [live drill](evidence/F15-2026-10-08.json),
[local boundaries](evidence/F15-local-boundaries.json), [validation](evidence/F15-validation.json)
and [eight executed mutants](evidence/F15-mutants.json). These contain aggregate
counts, schema names and hashes only. No SQL dump, private row, object bytes,
credential, Vault plaintext or hosted encryption key is committed.

### Source authority and development authority

Live production is intentionally behind repository main because Render auto-deploy is OFF.

| Authority | Exact state |
| --- | --- |
| Live Render service | `glamgenius-api-kugi`, `srv-dair328ae00c73fkkht0`; auto-deploy OFF |
| Live application | `27f1df4a08f17321cba21b95a00a65b37ea5ce5a` |
| Live Store B | `thuyrlepavzdgkvdzuos`, PostgreSQL 17.6, Alembic `d0e1f2g3h4` |
| Restored disposable local target | Supabase PostgreSQL 17.11; Alembic `d0e1f2g3h4` |
| Repository branch base | `f1a2db6dad3f6824a3d24f7f6a6ebcaaf8a1fc4f`, tree `7b2a13340a56690e6da5e50f584eeadaffd9c760` |
| Current development migration head | `o3p4q5r6s7`; one head; no migration added by F15 |

All 147 live public base tables were restored. `label_report_resources` and
`off_data` were absent in the source and restored copy, as expected for that
deployed release. Later undeployed tables are not restoration requirements.
The recovery branch was created only after the actual backup, restore and
parity passed. Production and repository main were never reset or migrated.

### Actual artifacts and observations

Supabase CLI **2.120.0** exported separate roles, schema and data-only COPY
artifacts. The data command excluded `storage.buckets_vectors` and
`storage.vector_indexes` using the installed help syntax. Official guidance:
[platform backup/restore](https://supabase.com/docs/guides/platform/migrating-within-supabase/backup-restore)
and [restore to local/self-hosted](https://supabase.com/docs/guides/self-hosting/restore-from-platform).

| Actual artifact | Bytes | SHA-256 | Seconds | Exit |
| --- | ---: | --- | ---: | ---: |
| roles.sql | 370 | `168a95a9c745af5ed4679751f90419ac9dc434240a213b03e32a06d5664c2308` | 12.17 | 0 |
| schema.sql | 247979 | `88ac9b099e1deca8aa9298f213480e49a0d8dbf1fe7b40bfd3e55968546fd07f` | 35.44 | 0 |
| data.sql | 171730 | `d0603b1bb1b9c9710523d788bc0f063b8007fa720a7e496c682430a2bc389ea1` | 63.64 | 0 |

Backup ran `16:49:10.9800215Z`–`16:51:02.3814011Z`: **111.40 s**.
Restore ran `16:51:05.9649143Z`–`16:51:07.8825134Z`: **1.91 s**.
The successful final verification took **0.734 s**. Total elapsed from backup
start through successful verification was **596.54 s**, including constraint
diagnosis and re-verification; it is not the sum of the successful phase
times. Full UTC timestamps are in the evidence.
No guaranteed production RPO is currently claimed.
These observations are not guaranteed RTO or an SLA.

The source used its confirmed IPv4 Session pooler. Client certificate chain
and hostname validation used `sslmode=verify-full` with the official public
CA; no Windows trust-store change or paid IPv4 add-on was required.
`pg_stat_ssl` behind the pooler measures the internal pooler/database hop;
same-session client connection information separately established client TLS.
Source transactions and the dump container enforced read-only defaults.

CLI 2.120.0's container environment did not propagate the URL's SSL options.
A **local-only** image derived from the pinned Supabase PG17.11 image added
only the public CA and verified-TLS/read-only environment defaults. It held
no credential, production row or dump, and was never pushed. Image source is
[`Dockerfile.dump-tls`](../../scripts/f15/Dockerfile.dump-tls). The dump tag
was used only for clients; the empty local server used the original image.

The temporary dump directory had inheritance disabled and access only for
the current Windows user. Password entry was non-echoing and local/process
only. The password process was terminated and verified gone. All three SQL
artifacts, the production-copy container and its exact volume were removed
after parity; there is no approved retained production copy. Deletion does
not claim forensic erasure of SSD/controller backups.

### Restore and parity

The local restore used PG17 tooling, `--single-transaction`,
`ON_ERROR_STOP=1`, roles → schema → `SET session_replication_role = replica`
→ data → origin, and returned exit 0 with no SQL error. No hosted destination
was used. Source manifests before/after were unchanged.

All table names, every public table row count, 393 index definitions and
validity flags, seven extension names/versions/schemas, Auth/Storage metadata
aggregates and eight sorted-primary-ID SHA-256 values matched. A PK digest
proves identity parity for the selected tables; it does not hash private
column values or claim full row-content parity.

All 509 constraint identities/types/validation flags matched. Of their raw
definition hashes, 466 were identical and 43 CHECK definitions differed due
to PG17.6/17.11 catalog formatting. Each **original** CHECK expression from
the integrity-checked schema artifact was reparsed on the restored target as
a temporary NOT VALID probe inside an explicit transaction. All 43 resulting
canonical definitions matched the actual restored definition hashes. The
probe transaction rolled back; the catalog was rechecked unchanged. A hash
mismatch was not ignored or globally normalized away.

### Recovery boundaries

| Boundary | Actual observation and recovery contract |
| --- | --- |
| Ordinary Store B | Actual dump → isolated restore → aggregate/schema/index/constraint/selected-ID parity passed at the live `d0e1f2g3h4` authority. |
| Auth | Source/restored users 0. Database rows do not qualify JWT signing secrets, API keys, OAuth/SMTP settings or existing access-token/session validity. No production user was created. |
| Vault | Source 2, restored 0. Dump COPY/schema inventory contained no Vault data. External integrations 0 at dump time. No secret or hosted root key was retrieved; no Vault write/delete occurred. |
| Production Storage bytes | actual production object-byte recovery: N/A — source contained zero objects |
| Synthetic LOCAL Storage | Actual object → separate local byte backup → deletion → restore → size/SHA-256 and exact byte equality passed through the local storage adapter. This is a local byte-transfer proof, not hosted API/configuration recovery. |
| Store A | Hosted `yvbeipihxptwttsteatp` remained INACTIVE/untouched. A distinct local database with synthetic OFF fixtures passed export → actual table destruction → recreate → import → exact JSONL/checksum parity. |

Vault credential recovery is not established by the ordinary logical database backup. External integrations requiring Vault credentials must reconnect after disaster recovery.

The local Store A importer accepts canonical export artifacts only, verifies
checksum/count/license/attribution, bounds data at 64 MiB/100,000 records,
rejects unknown/proprietary fields and supplied `fetched_at`, and refuses
hosted targets, Store B aliases and a cached engine bound to another target.
It validates the entire artifact before acquiring its guarded Store A
session, locks an empty target, and imports atomically. Restored `fetched_at`
is NULL/unknown, not the recovery time. No Store B session is acquired.

### Exact execution provenance and reusable operator

The October 8 qualification was a multi-stage executed recovery proof. The
historical live operator performed the read-only Store B dump and isolated
restore but failed closed on raw PG17.6/17.11 CHECK-definition differences and
did not remove private SQL/local-copy artifacts itself. A separately executed
CHECK-reparse verification established canonical equality for all 43 differing
CHECK definitions and rolled its probes back. A separately executed cleanup
removed the protected SQL workspace and local production-copy container/volume.
The later reusable operator incorporates both verification and cleanup but was
not rerun against production.

Historical operator performed credential/process cleanup but did not perform SQL workspace or local production-copy container/volume cleanup. Those artifacts were removed by a separately executed post-drill cleanup step.

The [provenance manifest](evidence/F15-provenance.json) binds exact SHA-256 and
byte counts for the [executed historical operator](evidence/F15-executed-live-operator-2026-10-08.ps1.txt),
[executed CHECK helper](evidence/F15-executed-constraint-reparse-2026-10-08.py.txt),
[original CHECK result](evidence/F15-constraint-reparse-verification.json),
[separate cleanup record](evidence/F15-private-artifact-cleanup.json),
the public source TLS CA and the original privacy-safe evidence exporter.
The source-manifest SQL and TLS Dockerfile are byte-for-byte identical to their
existing committed files; their paths and hashes are bound without duplicate
historical copies. The historical CHECK helper differs from the later reusable
helper and is preserved independently. Snapshot bytes are protected from Git
newline conversion. These historical `.txt` files are review evidence; do not
execute them as the current operator.

The [current local absence check](evidence/F15-current-local-absence-2026-10-09.json)
verified the exact recorded SQL workspace, container/volume names, dump-image
clients and credential-process PIDs without contacting production. After the
original cleanup, an empty PG17 target had been recreated under the same name.
That replacement was verified to contain zero public tables, Auth users,
Storage buckets/objects and Vault rows, then removed with its task-owned volume
so those names are currently absent. Unrelated local resources were untouched.
This is current cleanup-state verification, not a second production drill.

The accepted parity proof still contains 509 constraints: 466 unchanged raw
definitions and the separately verified 43 canonical CHECK definitions. The
original operator's refusal is part of this evidence chain; it is not reported
as automatic canonical-parity success or private-artifact cleanup.

No guaranteed production RTO is currently claimed. These executed observations
and provenance records do not authorize migration, deployment or finding closure.

### Reviewable operator and fail-closed tests

[`Invoke-StoreB-Drill.ps1`](../../scripts/f15/Invoke-StoreB-Drill.ps1) is the
parameterized Windows operator derived from the executed local script. It
incorporates the previously separate CHECK verification and implements live-run
private-artifact cleanup in `finally`. Its PG-minor constraint handling is the
reviewed form of the separately executed verification helper. The refactor was
syntax-checked and self-tested locally only; it did not execute the recorded
production dump/restore, and the live backup was not repeated after credential
destruction.
[`safe-source-manifest.sql`](../../scripts/f15/safe-source-manifest.sql)
queries aggregates/IDs-as-digests only, under a read-only transaction.
[`verify-constraint-parity.py`](../../scripts/f15/verify-constraint-parity.py)
reparses original CHECK expressions locally, fails every other mismatch and
rolls back. Never dot-source the operator or launch it in a persistent shell:
it exits the process to destroy managed credential strings.

Prerequisites: an empty task-owned `supabase_db_glamgenius-f15-*` server at
17.11, local Docker context `desktop-linux`, CLI 2.120.0 with telemetry
disabled, the official CA at its verified hash, and the TLS client image.
Create/init the Supabase scratch directory outside Git, set database major
17, disable seed, and start the **original** PG17 image. Then pin
`supabase/.temp/postgres-version` to `17.11.0.004-f15-tls-20261008` for CLI
dump clients. Never use that client image as the local server. Supply the
operator's six explicit local paths and use `-SelfTest` before authorizing a
new live run. The task's confirmed non-secret pooler hostname is its default.
Do not pass a password as an argument. A new live run prompts locally only
when all prerequisites are ready. Run one operator at a time. Cleanup removes its dedicated dump clients, SQL
artifacts and local production copy; return the scratch pin to the original image before
creating another empty target.

[`app.operations.recovery`](../../backend/app/operations/recovery.py) provides
the reusable fail-closed stage orchestration. It calls the real adapter
operations, requires every nonempty artifact and successful native exit,
requires verification, checks unchanged source/artifact manifests, and
refuses omitted production byte proof for a nonzero object count. It keeps
Vault/Auth authority outside ordinary parity. Its synthetic PostgreSQL test
executes a real COPY restore and proves an intentional SQL error rolls the
transaction back. It deliberately rejects raw CHECK differences; the Windows
operator uses the independently executed reparse path for a PG-minor change.

[`qualify_f15_mutants.py`](../../scripts/qualify_f15_mutants.py) actually
changed code, executed the detecting tests and restored original bytes.
All eight were killed: zero dump, failed dump, failed restore, skipped
verification, manifest mismatch, accepted proprietary field, fabricated
fetch timestamp, and skipped required Storage proof for a nonzero source.
These synthetic harness tests are not described as a second live drill.

### Future deployment gate — not executed by F15

Before deploying current main, separately authorize a fresh verified backup,
review the exact 16-migration chain below, qualify upgrade on a production-like
restored copy and downgrade/refusal behavior, then explicitly authorize the
production migration, matching application deployment and readiness checks.
F15 did none of these future deployment steps.

`d0e1f2g3h4` → `a9b0c1d2e3` → `b0c1d2e3f4` → `c1d2e3f4g5` →
`d2e3f4g5h6` → `e3f4g5h6i7` → `f4g5h6i7j8` → `g5h6i7j8k9` →
`h6i7j8k9l0` → `i7j8k9l0m1` → `j8k9l0m1n2` → `k9l0m1n2o3` →
`lf1a2b3c4d` → `l0m1n2o3p4` → `m1n2o3p4q5` → `n2o3p4q5r6` →
`o3p4q5r6s7`.

### Regression and publication evidence

Focused recovery/Store A/ODbL/authority qualification passed **77 tests**
(2 existing warnings). Full backend passed **8198 tests,
1 skipped**, and invite-required passed **17 tests**.
All **8/8 actual mutants** were killed and original source bytes restored.
Ruff, compileall, standalone helper syntax, Windows credential-free self-test,
diff check and the current repository one-head/schema check passed. Gitleaks
scanned every proposed changed file without added exclusions and found no leak.
Whole-directory scanning also reported two unchanged public reference fixture
identifiers already described by the repository's historical Git ignores;
neither file nor ignore policy was changed. The commit-range Git scan is
recorded in the publication report, matching PR scope.

The full synthetic functional suite used a disposable PG16 server on tmpfs
with fsync/synchronous_commit/full_page_writes OFF and UTC. These settings do
not qualify crash durability and were never used for the real PG17 restore.
The earlier partial full run was interrupted after 1820 passing tests to
qualify the final immutable source snapshot; it is not a complete-suite result.
Current complete results are in the validation evidence linked above.

Exact-head CI run/status and the review/comment/thread snapshot belong to
the publication report. No result
from an earlier F14 SHA is treated as CI evidence for the F15 head.
