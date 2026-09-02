# FSSAI official Food Recall records

V1 imports only a manually downloaded public FoSCoS **Food Recall → Export to excel** artifact. The source is one XLSX workbook with one `data` sheet, the thirteen FoSCoS headers in their published order, and `DD-MM-YYYY` text dates. `NA` termination dates mean absent; malformed nonblank dates fail the import.

The operator supplies the real download/review time with `--source-checked-at`; database `created_at` is only import time. The time is never inferred from a filename, and a naive or future timestamp is refused. There is no scraping, CAPTCHA bypass, private API, or automatic live-source claim.

The importer stores SHA-256 of the original XLSX bytes, source format, source check time, row count, parser version, and source-fetch lineage. A changed record revision points to the import that produced it. An unchanged re-observation advances `last_seen_at` and `last_seen_fetch_id` without manufacturing a revision.

## What a workbook must be to be accepted

The artifact is validated as a file before it is read as a spreadsheet: the four-byte ZIP local file header `50 4B 03 04`, a readable ZIP directory, and no macro part (`xl/vbaProject.bin`) anywhere in the package — a `.xlsm` renamed `.xlsx` is still macro-enabled, and openpyxl only reports `vba_archive` when it was loaded with `keep_vba`, so the package itself is inspected.

`License / Registration No.` and `Batch / Lot No.` must be genuine text cells. A numeric cell reaching `str(value)` turns `0789` into `789` and a licence into `10012345678901.0`, so the import is refused rather than the identifier quietly rewritten. `Recall Id` follows the real observed source type: an integral numeric cell becomes its integer form.

A workbook of headers with no recall rows is refused as `empty_official_export`. Accepting it would create a successful `OfficialSourceFetch`, advance `last_successful_check_at`, and make a stale copy of the register look current. A workbook carrying the same `Recall Id` twice is refused whole as `duplicate_official_record_id` — last-row-wins would pick a winner the source never named, and two revisions from one source check would invent a history.

Every failure is a closed, reviewable key. Parser, ZIP and XML exception text never reaches `OfficialSourceFetch.error_code`:

`unsupported_official_export`, `invalid_official_export`, `unexpected_official_export_schema`, `macro_enabled_official_export`, `empty_official_export`, `missing_official_record_id`, `duplicate_official_record_id`, `malformed_official_identifier`, `malformed_official_date`, `out_of_order_source_check`, `duplicate_source_check`, `conflicting_source_check`, `unhandled_official_source_error`.

A rejected artifact stays auditable: where the original bytes were readable, the failed ledger row keeps `source_checked_at`, `original_filename`, `source_format` and `source_file_sha256`. A failed row never advances `last_successful_check_at`.

## Source time only ever moves forward

Before any canonical mutation, a successful import is compared against the newest accepted `source_checked_at` for `fssai_foscos` / `food_recall`:

- **Older** — refused as `out_of_order_source_check`. Replaying an earlier download would overwrite canonical status and reason with content the register has already superseded, bump `latest_revision` with stale text, and pull `last_seen_at` backwards while `last_successful_check_at` still reported the newer check.
- **Equal source time, identical `source_file_sha256`** — refused as `duplicate_source_check`. The same artifact re-imported is idempotent: nothing semantic changes, and no second successful fetch is written.
- **Equal source time, different `source_file_sha256`** — refused as `conflicting_source_check`. Two artifacts claiming the same instant disagree about the register, and V1 picks no winner between them.

Refused imports leave `OfficialRecord`, `OfficialRecordRevision`, `last_seen_at`, `last_seen_fetch_id`, `latest_revision`, canonical status/reason and successful freshness exactly as the last accepted check left them. A failure ledger row may be retained.

That comparison is only true if it cannot be raced. Two concurrent imports could otherwise read the same "latest successful check", both pass the guard, and both mutate. So an import takes a transaction-scoped PostgreSQL advisory lock — `pg_advisory_xact_lock(hashtextextended('official_source:fssai_foscos:food_recall', 0))` — after the workbook is validated and **before** the latest-check read, and holds it until commit or rollback. It is a database lock rather than a Python one because a second worker, a second operator, or a cron run on another host would not see a process-local lock.

## A record we keep after it leaves the export

A record is never deleted because a later export omits it: absence from one download proves nothing about withdrawal, correction or resolution. That makes per-record observation provenance part of the public contract, alongside the source-wide `last_successful_check_at`:

- `source_last_seen_at` — `OfficialRecord.last_seen_at`, when this record itself was last observed in an accepted export.
- `seen_in_latest_successful_check` — whether `OfficialRecord.last_seen_fetch_id` is the latest successful `OfficialSourceFetch`.

"This record was last observed on 1 August" and "we last checked the register on 4 August" are different sentences, and the screen says whichever is true. No wording derives meaning from absence: the words *withdrawn*, *removed*, *no longer recalled*, *resolved*, *cleared* and *safe* are never emitted unless the official record itself states such a status.

## Matching a pack to a record

Pack matching requires a valid 14-digit FSSAI licence and a meaningful exact batch/lot on **both** sides. Matching runs only against a confirmed Store B label snapshot; Open Food Facts cannot supply a licence or a batch and therefore cannot manufacture a match.

A licence is its fourteen printed digits, optionally grouped with spaces as labels print them. It is never salvaged by deleting characters: `FSSAI 10012345678901`, `10012345678901X` and `10012-345678901` are not licences, because collapsing them onto one identifier would invent an exactness the source never stated. A run of one repeated digit is a placeholder, not a licence.

One licence and lot can name several rows in the public register, so the candidate set is resolved together rather than row by row:

1. Rows whose normalised licence and batch do not match exactly are not candidates.
2. Rows whose brand or product conflicts with the pack are dropped.
3. One survivor is the answer.
4. Several survivors require the pack to **positively** corroborate an identity — missing text on either side corroborates nothing — and every corroborated row must name that same identity. Otherwise the answer is no match: not the first row, not the newest, not all of them.

Several rows are returned only when they deterministically resolve to the same corroborated pack identity.

Placeholder batches are not matchable, compared case-insensitively after whitespace normalisation: `NA`, `N/A`, `nil`, `none`, `not applicable`, `not available`, `other`, `others`, `-`, `.`, `no`, `loose`, `loose sample`, `sold as loose`, and zero-only strings such as `0`, `00`, `000`. Short real lots stay matchable (`C`, `1`, `0789/55`), and separators are never stripped, so `B-123` and `B 123` remain different identifiers.

Brand and product use a separate `normalise_identity_text` — NFKC, whitespace collapse, casefold — and deliberately do **not** inherit the batch placeholder vocabulary or the zero-only rule: a brand may legitimately be called `Other`. Licence and batch establish eligibility; brand and product only block a real conflict. Missing identity on either side is missing information, not disagreement, and identity alone never establishes a match. There is no fuzzy or AI matching.

Absence of a match makes no safety claim. Official records remain separate from Product Result grade, decision, community reporting, complaint handoff, and Open Food Facts attribution.
