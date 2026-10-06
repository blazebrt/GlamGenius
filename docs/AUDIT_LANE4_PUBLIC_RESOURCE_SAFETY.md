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
4. Aggregate/admit against PostgreSQL retained rows, no reservation/flush.
5. Canonical validated bytes/MIME, server UUID key, object write.
6. Row (including exact bytes), flush within existing savepoint.
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

Storage failure leaves no report; flush failure compensates. Raised commit
retains objects for COMMITTED/UNKNOWN; only proven NOT_COMMITTED permits delete.
Privacy export includes nullable photo bytes, never the object path. Existing
account prefix and safe legacy erasure remain unchanged.

## Scope

F04 only. F01/F02/F03/F05/F06/F07/F08/F09/F13 stay closed. Other open findings
F10/F11/F12/F14/F15 remain outside this lane. No frontend, deployment, production
secret, paid resource, entitlement, roadmap or AI behavior changes.

## Executed adversarial evidence

`python scripts/run_lane4_mutations.py` executes opt-in in-process mutants on a
disposable PostgreSQL database. Final complete run: **10/10 proven kills**:
quota removed; admission after storage; legacy unknown size zero; replay charged;
serialization removed (second independent writer reaches storage before first
commit); declared MIME trusted; magic-only file accepted; unbounded read;
commit exception guess-deletes possibly durable evidence; rejection still stores.
Kills are actual regression assertions, not import/setup failures. Normal pytest
does not load the mutant plugin. No production code is changed by this runner.

Stricter validation required repairing old test-only fake JPEG/WebP/PNG files
and PNG-plus-trailing-garbage uniqueness fixtures. Complete JPEG/WebP/PNG bytes
and a CRC-correct PNG ancillary metadata chunk preserve all original assertions
and byte-distinct-photo semantics. No test is weakened or skipped.

## Local qualification and environment boundaries

Expanded PostgreSQL-backed F04, report-evidence, media, storage, body-limit,
device, deletion and privacy regressions: **248 passed**. Ruff, compileall and
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
