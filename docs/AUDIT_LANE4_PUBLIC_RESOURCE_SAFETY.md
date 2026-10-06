# Audit Lane 4 — F04 public label-report resource safety

## Verified Phase A, before implementation

Baseline main `30d6c0ac7ee5ed35c4eed77f563ef5fa34ae6121`, tree
`dabb702b95e0925c3dc9d41522495b80dc75357f`, sole parent
`a9374fbf9ce67ef636efde258129e9d4002cc2db`; GitHub signature valid.
Push CI #894 (`37357288242`) succeeded.

1. Confirmed: fresh client IDs have no aggregate row/byte ceiling in
   `file_label_error_report`; the unique identity only bounds a single report.
2. Confirmed: registration has a limiter, label-error has no dedicated limiter.
3. Confirmed: device-only reports have `account_id=NULL`, so an account limit
   alone cannot protect this public route.
4. Confirmed: route calls one-shot `await photo.read()` before checking 6 MiB.
5. Confirmed: declared part MIME reaches `storage.put`, defaulting to JPEG.
6. Confirmed: report service bypasses shared `validate_upload`. That validator
   accepts magic-only truncated files, requiring a minimal structural hardening.
7. Disproved retry-object gap: identity advisory lock precedes lookup/storage;
   `uq_label_report_device_client_id` remains the final duplicate invariant.
8. Stronger than old audit: server UUID namespaces, lifecycle account hold,
   ordinary failure compensation, three-way commit reconciliation, prefix purge
   and explicit legacy-photo erasure already exist. Preserve them.

Global body cap remains 16 MiB, including streamed bodies. Report cap remains
6 MiB, separate from ordinary media's 8 MiB default. No new infrastructure.

## New beta policies

Named constants: `app/domains/product/report_policy.py`.
Retained lifetime device quota: 100 rows / 60 MiB photos. Authenticated account:
200 rows / 120 MiB, across all its devices. Both apply; resolved rows count.
These allow substantial beta correction work while bounding a single identity
to tens of full-sized photos. Quotas do not reset on process restart. No billing
or entitlement changes. Anonymous rows remain anonymous, regardless of claim.
Existing legacy photo with unknown exact size costs the full 6 MiB; absent
photos cost zero. New exact bytes are stored in nullable `photo_byte_size`.

Additional process-local abuse ceiling per hour: IP 60, device 20, account 30;
one shared limiter capped at 4096 keys, expiring/throttled sweep, full table
fails closed. Trusted `client_ip(request)` only. Timed rejection has 429 with
reason, Retry-After and retry metadata; durable quota rejection has 429 with
reason and `retryable=false` (there is no invented reset time). No flood logs.

## Lock order and storage

1. Report idempotency identity advisory lock; replay lookup, return if present.
2. Authenticated account lifecycle FOR SHARE hold (anonymous takes none).
3. Quota advisory identities sorted lexically: account, then device.
4. Reconcile any existing uncertain identity; aggregate/admit against reports
   AND durable upload resources, without double-charging a paired report.
5. Commit the resource in a separate transaction BEFORE upload, while the
   caller retains identity/account/quota locks. No early caller/device flush.
6. Canonical validated bytes/MIME, server UUID key, object write; acknowledge
   terminal completion durably. File the report and retire its resource in
   the same caller transaction/savepoint.
7. Commit releases all locks.

No account acquisition follows a quota/device lock; pending device last_seen
is not flushed early. Deletion takes no report/quota locks. Its account-first
device cascade therefore cannot close a cycle. Replays never charge durable
quota or write an object, although request rate limits still apply.

Photo reads use 64 KiB chunks, shrinking the final read to at most ceiling+1.
Shared validation checks actual MIME and complete bounded containers: PNG
chunk bounds/CRC/IHDR/payload/IEND, JPEG segment/SOF/SOS/entropy/EOI, WebP RIFF
length/chunks/image payload/dimensions. This is structural validation, not full
pixel decoding; no imaging dependency, decompression or dimension allocation.
Animated WebP is refused as a label photo. New extensions match canonical MIME;
existing JPEG and legacy keys remain readable/deletable by their stored keys.

The first correction compensated synchronous write-then-raise, but independent
review exposed a different lifecycle: `wait_for(to_thread(upload))` can return
while the worker later creates the object after an immediate no-op delete.
Two event-coordinated timeout/cancellation regressions were actually red on
`ac6c88b2b50f0f0f6a258dd4c73d2b64ccf50025`: late objects had no durable authority.

The follow-up uses **Model B: durable uncertain-write authority**. The new
`label_report_resources` row is committed BEFORE storage dispatch. It names
the server UUID key, exact validated bytes, device/account and retry identity;
it is NOT a successfully filed report. Request rollback, cancellation, DB
acknowledgement loss or process restart cannot remove that quota charge.
Reservation commit failure stops dispatch even if only its acknowledgement
was lost. Anonymous resources remain device-scoped and durably discoverable.

The pinned supabase/storage3 2.31.0 source uses HTTPX 0.28.1. It supplies an
`AsyncStorageClient` with a caller-owned `AsyncClient`. Label-report uploads
now use that native async SDK, HTTPX connect/read/write/pool deadlines AND a
20-second total deadline; no detached upload thread, no transport retries,
`upsert=false`. Cancellation closes the local transport and is re-raised.
Other media operations retain their approved adapter behavior. Closing a
connection is NOT proof that the provider did not commit remotely.

The published [Supabase uploader implementation](https://github.com/supabase/storage/blob/master/src/storage/uploader.ts)
awaits backend upload completion before publishing the object metadata in its
completion transaction. Exact-key visible metadata therefore proves completion
of this single standard upload, not merely dispatch. The adapter issues one
immutable standard upload with no automatic retry. This is source-contract
verification, not a claim of live-provider/production smoke testing.

Reconciliation uses exact-key SDK `info` (never paginated listing), then exact
delete and fresh exact absence. Unknown + absent retains the resource: the
provider may still finish. Once the single immutable upload is visibly
complete, or a terminal non-write is explicitly proven, deletion plus absence
can retire authority. Unavailable/stuck storage cannot release it. There is no
fixed delay/TTL, inferred non-write, untracked in-memory cleanup task or daemon.
A repeated unknown identity receives the existing retryable failure without
uploading another key. An unknown-absent resource may therefore remain charged
and block account erasure until there is real terminal-provider evidence; this
is deliberate fail-closed behavior, not a claim of immediate cleanup.

Account deletion reconciles these records BEFORE both prefix purges/cascade.
Otherwise a prefix delete could erase the visible completion proof first.
Unknown-absent or unavailable resources keep the account/Auth deletion retryable;
late visible objects are deleted/proven absent before database/Auth removal.
The new table is OPERATIONAL (no customer export of storage/retry bookkeeping),
RLS enabled, with public/anon/authenticated access revoked. Existing report
export remains unchanged. Storage failure leaves no filed report; flush failure
compensates without dropping durable authority prematurely. Raised DB commit
retains objects for COMMITTED/UNKNOWN; only proven NOT_COMMITTED permits delete.
Privacy export includes nullable photo bytes, never the object path. Existing
account prefix and safe legacy erasure remain intact.

The only follow-up migration is linear `n2o3p4q5r6 -> o3p4q5r6s7`. Downgrade
takes an exclusive table lock and REFUSES populated resource authority rather
than silently discarding it. The PostgreSQL regression proves refusal preserves
the exact key/bytes, then verifies empty downgrade/re-upgrade.

## Scope

F04 only. F01/F02/F03/F05/F06/F07/F08/F09/F13 stay closed. Other open findings
F10/F11/F12/F14/F15 remain outside this lane. No frontend, deployment, production
secret, paid resource, entitlement, roadmap or AI behavior changes.

## Executed adversarial evidence

`python scripts/run_lane4_mutations.py` executes opt-in in-process mutants on a
disposable PostgreSQL database. Before independent-review correction, **11/11
proven kills**:
quota removed; admission after storage; legacy unknown size zero; replay charged;
serialization removed (second independent writer reaches storage before first
commit); declared MIME trusted; magic-only file accepted; unbounded read;
commit exception guess-deletes possibly durable evidence; rejection still stores;
upload acknowledgement failure compensation removed.
Kills are actual regression assertions, not import/setup failures. Normal pytest
does not load the mutant plugin. No production code is changed by this runner.

The correction retains those 11 and adds four actual mutant targets: timeout
delete treated as final while a write remains alive; cancellation without
pre-write durable authority; uncertain retry allocating fresh keys; and omission
of uncertain bytes from quota accounting. The serialization regression now
pauses BEFORE the durable reservation, not just at storage, so a committed
reservation cannot hide a removed quota lock. UNKNOWN DB commit also asserts
that storage compensation is not even attempted, independently of the new
cleanup helper's additional protection for filed evidence.

Stricter validation required repairing old test-only fake JPEG/WebP/PNG files
and PNG-plus-trailing-garbage uniqueness fixtures. Complete JPEG/WebP/PNG bytes
and a CRC-correct PNG ancillary metadata chunk preserve all original assertions
and byte-distinct-photo semantics. No test is weakened or skipped.

## Local qualification before independent-review correction

Expanded PostgreSQL-backed F04, report-evidence, media, storage, body-limit,
device, deletion and privacy regressions: **248 passed**. After the new upload
acknowledgement regression proved its orphan path and the narrow correction,
the latest dedicated F04 (30) plus report-integrity (32) suite: **62 passed**,
including failed-cleanup warning/error preservation. Ruff, compileall and
diff whitespace checks passed. One Alembic head: `n2o3p4q5r6`, directly after
`m1n2o3p4q5`. Empty-database upgrade, schema drift check, populated downgrade /
upgrade and repeated reference seed passed. Legacy rows survive with unknown
sizes, which continue to incur the conservative quota cost.

All local databases/media folders are disposable and task-scoped. PostgreSQL
uses UTC to match canonical CI. Local pytest uses an isolated temporary folder
to avoid an unrelated Windows user/sandbox permission mismatch. The existing
Windows local-storage adapter emits backslash keys during prefix listing and
then refuses them during deletion; the existing community deletion regression
exposes this platform limitation. No unrelated adapter behavior was changed.
Exact-head Linux GitHub CI is the final full-suite qualification authority;
local attempts are not presented as a passing canonical full suite.

## Independent-review correction qualification

Old head/tree: `ac6c88b2b50f0f0f6a258dd4c73d2b64ccf50025` /
`3a5de8fc330e821e0a25faee3e1438e7e4edc8cf` (CI #896 was green).
New deterministic timeout/cancellation tests: **2 failed before correction**,
late objects existed with no durable resource. Latest corrected dedicated
upload/F04/report-integrity/storage suite: **89 passed, 2 warnings** (including
14 new uncertain-upload/adapter/migration tests). Expanded F04/media/body/deletion/
privacy suite: **213 passed, 3 warnings**. Invite-required: **17 passed, 2 warnings**.
Full actual mutation runner: **15/15 proven kills**, including all prior 11 and
the four new lifecycle/resource mutants above. Ruff, compileall and whitespace
checks passed; new one-head schema drift and populated downgrade-refusal/empty
round-trip checks passed. An additional disposable empty database completed
upgrade -> downgrade base -> upgrade -> drift check, followed by two successful
reference-data seed runs.

The full Windows suite was started and interrupted after reproducing the known
local-storage deletion limitation, rather than described as a full passing run.
The existing community deletion test separately confirms that same platform
failure. Canonical Linux CI on the NEW exact head must run the full suite and
invite follow-up; CI #896 cannot qualify the correction. No tests were skipped
or weakened to compensate for the Windows adapter limitation.
