# FSSAI official Food Recall records

V1 imports only a manually downloaded public FoSCoS **Food Recall → Export to excel** artifact. The source is one XLSX workbook with one `data` sheet, the thirteen FoSCoS headers in their published order, and `DD-MM-YYYY` text dates. `NA` termination dates mean absent; malformed nonblank dates fail the import.

The operator supplies the real download/review time with `--source-checked-at`; database `created_at` is only import time. There is no scraping, CAPTCHA bypass, private API, or automatic live-source claim.

The importer stores SHA-256 of the original XLSX bytes, source format, source check time, row count, parser version, and source-fetch lineage. A changed record revision points to the import that produced it. An unchanged re-observation advances `last_seen_fetch_id` without manufacturing a revision.

Pack matching requires a valid 14-digit FSSAI licence, a meaningful exact batch/lot, and no brand/product identity conflict. Placeholder batches such as `NA`, `nil`, `other`, and zero-only strings are not matchable. Absence of a match makes no safety claim. Official records remain separate from Product Result grade, decision, community reporting, complaint handoff, and Open Food Facts attribution.
