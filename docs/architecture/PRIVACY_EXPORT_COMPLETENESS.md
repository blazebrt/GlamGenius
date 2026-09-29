# Privacy export completeness (Lane E)

`GET /api/v2/privacy/export` hands a person a copy of everything GlamGenius
holds about them. The privacy registry (`backend/app/domains/privacy/__init__.py`,
`REGISTRY`) classifies every table, and `INCLUDED` means **this table belongs
in the account holder's export**.

Until Lane E that promise was not kept.

| Before (base `2651f3f0`) | Now |
| --- | --- |
| 111 tables classified `INCLUDED`; **33** of them were read by no export handler. | All 111 are exported, each through one entry in `EXPORT_COVERAGE`. |
| Every collection was cut at 20 000 rows (`_MAX_ROWS`), with nothing in the file saying so. | No row limit anywhere. A regression proves 20 050 rows all arrive. |
| A failed domain became `{"error": "domain_export_failed"}` inside a payload that `build_export` returned as though it were complete. | Any failed domain, or any covered table that cannot be shown to be in the file, raises `PrivacyExportIncomplete`; the route answers `503 PRIVACY_EXPORT_INCOMPLETE` and records no successful export. |
| A label-error report exported its `photo_key` — an internal object-storage path. | The report says `photo_attached: true/false`; the key never leaves. |

The 33 tables that were missing, found mechanically by comparing the registry
with every full-row `select(Model)` in the exporter:

- **Inventory:** `wardrobe_item_details`, `shoe_item_details`,
  `accessory_item_details`, `beauty_product_details`, `hair_product_details`,
  `perfume_details`, `inventory_item_images`, `inventory_value_events`,
  `item_condition_events`, `item_expiry_events`, `item_usage_events`,
  `item_relationships`, `duplicate_candidates`, `laundry_state_events`
- **Recommendation and shopping:** `recommendation_inputs`,
  `recommendation_entitlements`, `look_items`, `outfit_schedule`,
  `compatibility_edges`, `purchase_evaluation_factors`
- **Planning:** `daily_plan_actions`, `daily_plan_inputs`, `weekly_plan_days`,
  `air_quality_snapshots`, `plan_recalculation_events`
- **Progress and memory:** `goal_updates`, `progress_snapshots`,
  `comparison_sessions`, `score_explanations`, `streaks`,
  `memory_category_preferences`
- **Other:** `app_events`, `fssai_complaint_handoffs`

None was reclassified. Several belong to product surfaces that are no longer
offered (wardrobe, shoes, accessories, looks). That does not change whose
rows they are, so they are exported; no retired screen was restored.

## The contract: `EXPORT_COVERAGE`

`backend/app/domains/privacy/coverage.py` has exactly one entry per `INCLUDED`
table. Each entry says:

- **domain and paths** — where the rows are in the file, e.g.
  `inventory.beauty_product_details` or
  `shopping.subjects[*].decisions` + `shopping.unattributed_decisions`.
  `registry_summary.export_locations` publishes the same map in every export.
- **scope** — how rows are tied to the account **in SQL**:
  - `account_id`: `WHERE <table>.account_id = :account`;
  - `parent`: `WHERE <fk> IN (SELECT id FROM <parent> WHERE <parent owned by :account>)`,
    for child rows that carry no `account_id` (item details, look items,
    run inputs, plan actions, evaluation factors, …);
  - `account_row`: the account itself.

  Ownership is never inferred from an id's existence, never taken from the
  client, and never decided by fetching a whole table and filtering in Python.
- **layout** — a flat, deterministic list; or grouped by the household member
  each row describes (Steps 11B–11E), with unattributable rows kept separately
  and foreign subject ids stripped. None of the 33 repaired tables carries a
  `household_subject_id`, and none of their parents does, so they are
  account-level lists and the household grouping is unchanged.
- **handler** — `contract` rows are exported straight from the entry by the
  generic exporter (`_contract_collections`); `domain` rows by the existing
  hand-written handlers, whose structure is subject-aware or deliberately
  reduced.
- **withheld** — internal columns that must never appear: storage paths,
  provider bookkeeping, credentials, cursors, retry keys.
- **references** — for contract rows, every foreign key to another
  account-owned table. The exporter keeps the id only when that row is this
  account's; otherwise it writes `null`, marks the row
  `"invariant": "reference_ownership_invalid"` with the column names, and
  still exports the row. A broken invariant never puts another person's id in
  somebody's file.

### It is load-bearing

`build_export` holds every export to the contract before returning it:

1. `set(EXPORT_COVERAGE) == included_tables()` — otherwise the export is
   refused (`<table>:no_export_contract` / `<table>:not_included` in the log);
2. every covered table was read by its declared domain (the exporter records
   each full-row read) — otherwise `<table>:not_read`;
3. every declared path is present in the output — otherwise
   `<table>:path_missing`.

`tests/test_privacy_export_completeness.py` holds the same equality in CI,
checks every entry's scope against the real foreign keys, and seeds a real row
for every contract table for two accounts: the owner's row is in the owner's
file with every value, the other account's row is not, and nothing of the
other account appears anywhere in the serialised file.

### Adding a table

1. Classify it in `REGISTRY`.
2. If `INCLUDED`, add its `EXPORT_COVERAGE` entry. A plain account-owned or
   parent-owned list is one line with `_contract(...)` or `_child(...)`,
   declaring any references to other account-owned tables.
3. Add a real row for it to `_seed_graph` in the completeness suite (the
   suite refuses to run until every contract table has one).

Skipping step 2 fails CI, and would also make every export refuse to return.

## Complete, or not at all

- **No row ceiling.** Each collection is one SQL statement returning every
  owned row. Child rows are selected through a subquery, never a bound list
  of parent ids, so no account is too large for the driver's parameter limit.
  There is no internal batching.
- **Deterministic order.** Oldest first by `created_at`, with the primary key
  as the tie-break (`created_at` is the database's `now()`, shared by every
  row of one transaction). Existing orderings (consents, scans, audit events,
  household position) are unchanged. Order never limits anything.
- **Failure.** Every domain is still tried after one fails, so the log names
  every failed domain (domain and exception type only, never the exception's
  text). The shared read-only transaction is rolled back after each failure
  so the next domain really runs. Then `PrivacyExportIncomplete` is raised:
  - HTTP `503`, `{"detail": {"code": "PRIVACY_EXPORT_INCOMPLETE", "message":
    "Your data export could not be completed, so nothing was sent. Please try
    again later.", "retryable": true, "request_id": …}}`;
  - no domain name, table name, SQL, value or stack trace in the response;
  - no `privacy_exported` audit event — it is written only after a complete
    payload exists.

  On the base, `build_export` returned the partial payload. The route itself
  then crashed with an unhandled 500 — the rollback had expired the caller's
  account row, which the audit call then touched — so no success audit was
  written in that path either. Neither outcome was a truthful answer.
- **Retry** is safe: building an export only reads. A successful retry writes
  exactly one `privacy_exported` audit event.
- **Pure read.** The completeness suite digests every table before and after:
  a failed export changes nothing, and a successful one changes nothing but
  that single audit row.

## What never leaves

- Media: `storage_key` and `storage_backend` (the existing
  `media.service.to_public_dict`).
- Label-error reports: `photo_key`, replaced by `photo_attached`.
- Calendar integrations: `credential_ref`, `sync_cursor`.
- Notification deliveries: status metadata only, as before.
- Product watches: anchor ids and the notice cursor, as before.
- Supplement label components: pipeline bookkeeping, as before.
- `scan_devices` (token hashes) and `notification_devices` (Expo push tokens)
  stay `SECRET_EXCLUDED`.

The suite plants a sentinel in each of these and checks that none appears
anywhere in the serialised file, and that no forbidden key name appears
anywhere in it.

This is a JSON export of database rows. It carries no photo bytes, and Lane E
adds no archive or file-bundle download.

## Schema version

`EXPORT_SCHEMA_VERSION` is `1.5`. The additions are new collections, the
`photo_attached` field replacing `photo_key`, and `registry_summary.exported_tables`
and `export_locations`, both derived from `EXPORT_COVERAGE`. Nothing
previously exported moved.

Lane E set `1.5`. Step 15 later set `1.6` for its `growth` domain
(`consumer_referral_invites` at `growth.referral.issued_codes`, the 112th
`INCLUDED` table) and withheld `app_events.client_event_id`; `app_events` stays
at `ai_and_ops.app_events`. See `CONSUMER_GROWTH.md` §6.

## Known limits

- Hand-written domain handlers that existed before Lane E still export their
  own foreign-key columns as stored. The reference-ownership check covers the
  33 contract tables; extending it to the older handlers is a separate change.
- `media.service.to_public_dict` has never exported `original_filename` or
  `sha256`, and notification deliveries have never exported their title or
  body. Lane E keeps those serialisers as they were.
