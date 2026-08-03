# Historical image-prefix cleanup (Fix 12)

Work Package 2 removes the V1 storage pattern
`image_base64=(request.image_base64[:80] + "...")` from
[`backend/routes/scan.py`](../../backend/routes/scan.py) and replaces
it with `image_base64=None`. That fixes new writes; historical records
still carry the fragment until the cleanup script runs.

This document describes the cleanup script, its guarantees, and what
the operator does with it.

## What the script does

Path: [`backend/scripts/cleanup_v1_scan_image_prefixes.py`](../../backend/scripts/cleanup_v1_scan_image_prefixes.py)

It scans `db.scans` for any document with a non-null `image_base64`
field and `$unset`s that field. It sets a
`image_base64_cleaned_at` timestamp on each cleaned record so a
subsequent audit can distinguish "always clean" from "cleaned by this
script".

The script:

* is **idempotent** — running it twice reports zero updates the
  second time;
* is **dry-run by default** — the `--apply` flag is required for any
  write;
* prints only counts, never a payload, never a prefix, never a
  record identifier that would leak association;
* batches its writes (default 500 per batch) so no single operation
  blocks the collection on a large deployment;
* uses only `MONGO_URL` and `DB_NAME` from the environment — no new
  configuration key;
* emits a final `{"cleanup": ...}` JSON line so CI can parse the
  result deterministically.

## Usage

Dry-run first, always:

```bash
cd /app/backend
python -m scripts.cleanup_v1_scan_image_prefixes
```

Expected output on a fresh V1 deployment (before the fix landed):

```
[image-prefix-cleanup] mode=DRY-RUN batch=500
[image-prefix-cleanup] total=12345 already_clean=0 to_clean=12345 updated=0
{"cleanup": "v1_scan_image_prefix", "scans_total": 12345,
 "scans_already_clean": 0, "scans_to_clean": 12345,
 "scans_updated": 0, "mode": "DRY-RUN"}
```

Then apply:

```bash
python -m scripts.cleanup_v1_scan_image_prefixes --apply
```

Expected output on the same deployment:

```
[image-prefix-cleanup] mode=APPLY batch=500
[image-prefix-cleanup] total=12345 already_clean=0 to_clean=12345 updated=12345
{"cleanup": "v1_scan_image_prefix", "scans_total": 12345,
 "scans_already_clean": 0, "scans_to_clean": 12345,
 "scans_updated": 12345, "mode": "APPLY"}
```

Re-run of `--apply` on the same collection is a no-op:

```
[image-prefix-cleanup] total=12345 already_clean=12345 to_clean=0 updated=0
```

## Guarantees

| Guarantee | Enforced by |
|---|---|
| No content is logged | The script never reads or prints the removed value. It projects only `_id` during the batched sweep. |
| Idempotent | The filter `{"image_base64": {"$ne": null, "$exists": true}}` matches only records still containing a fragment. `$unset` on a missing field is a Mongo no-op. |
| Dry-run default | `--apply` is required for any write. |
| Bounded blast radius | Only `db.scans` is touched; only `image_base64` is unset; only records containing that field are matched. |
| Auditable | Every cleaned record gets a `image_base64_cleaned_at` timestamp. |

## Rollback limits

The `image_base64` prefix that this script removes held at most 83
characters (80 base64 payload characters + `"..."`). It is a slice of
the user's photo bytes.

**The slice cannot be reconstructed from other tables.** It is the
only place that prefix ever lived. Once the script runs with
`--apply`, the prefix is gone.

If a rollback is required (for example a customer-support case that
depends on the prefix), the operator restores from a MongoDB backup
taken **before** the cleanup. The script itself provides no undo.

The prefix is not a functional requirement anywhere in the app:

- Scan history and trends read `analysis` and `wellness_scores`,
  not `image_base64`.
- The AI provenance is preserved in
  `analysis.meta.provenance.ai_run_id`; that is the durable reference
  used by the entitlements ledger.
- No frontend surface reads `image_base64` from a scan record. A grep
  confirmed this before the cleanup was designed.

## Order of operations

1. Deploy the code change so **new** writes no longer store a prefix.
2. Run the cleanup dry-run (`python -m scripts.cleanup_v1_scan_image_prefixes`)
   and record the counts in the runbook.
3. Take a MongoDB backup.
4. Run the cleanup with `--apply`.
5. Run the dry-run again and confirm `to_clean=0`. Attach the JSON
   summary line to the deploy notes.

If steps 3 and 4 must happen in a maintenance window, the window is
tiny — the `$unset` is O(batch) and the total count of scans is
knowable from step 2.

## What still ships to the AI provider

Fix 12 does not change the AI call path. The image bytes still travel
to the provider for analysis (subject to the analysis-consent gate).
What changes is the **persistence** of a slice on the scan record.

## Verification query

After running the cleanup, this Mongo query should return zero
documents:

```javascript
db.scans.count({ image_base64: { $ne: null, $exists: true } })
```

And this one should equal `scans_updated` from the JSON summary:

```javascript
db.scans.count({ image_base64_cleaned_at: { $exists: true } })
```

## Payment mechanics

The script does not touch any collection under `orders`,
`subscriptions`, `refunds`, `payment_events`, or `billing_events`.
`SUBSCRIPTIONS_AVAILABLE=false` is unrelated to this script's
behaviour.

## Cross-references

- [`backend/routes/scan.py`](../../backend/routes/scan.py) — new writes
  now store `image_base64=None`.
- [`backend/tests/test_v1_regression.py`](../../backend/tests/test_v1_regression.py)
  — the old regression that asserted the 83-character rule has been
  inverted to `test_new_scans_store_no_image_fragment`.
- [`docs/engineering/CHECKLIST_PRIVACY.md`](../engineering/CHECKLIST_PRIVACY.md)
  — item §1 "No new field stores a base64 fragment".
