# Phase 3 Report — Complete Appearance Inventory

**Baseline:** `7c9e5e7` (`main`, merged Phase 2)
**Branch:** `v2/phase-3-complete-inventory`
**Date:** 2026-08-01

## Plain-English summary

Phase 3 adds one calm place to catalogue everything appearance-related a user owns:
wardrobe, shoes, accessories, beauty products, hair products, perfumes and supplements.
Users can add an item manually or create a reviewable draft from an owned product photo or
screenshot. A photo result never becomes a verified fact until the user confirms it.

The inventory explains what needs attention without judgement. It identifies duplicate
candidates, deterministic expiry dates, Low-Use Products and an explicitly estimated Value
to Recover. Missing prices remain missing. Supplement records are inventory-only and never
generate dosage advice.

## Architecture decisions

- The inventory is a module inside the existing FastAPI modular monolith.
- PostgreSQL owns structured items, category details, provenance and immutable events.
- The existing media abstraction owns item images and enforces account ownership.
- The Phase 1 AI gateway is the only model entry point; it supplies model, prompt and schema
  provenance and rejects invalid output.
- Manual entries are confirmed user facts. AI extraction creates a `draft` item and draft
  attributes, regardless of confidence.
- Searchable category details are projected into controlled inventory attributes while the
  typed category table remains authoritative.
- `client_mutation_id` makes an offline create safe to retry. `expected_version` returns a
  409 conflict when another device changed the item first.
- Shelf photos, wardrobe photos and short videos have interfaces but remain behind the
  disabled `v2_inventory_batch` flag. No batch-accuracy claim is made.
- Today and weekly-planner logic was not started.

## Database changes

Migration `0003_complete_inventory` adds 19 tables:

1. `inventory_categories`
2. `inventory_items`
3. `inventory_item_images`
4. `inventory_attributes`
5. `wardrobe_item_details`
6. `shoe_item_details`
7. `accessory_item_details`
8. `beauty_product_details`
9. `hair_product_details`
10. `perfume_details`
11. `supplement_details`
12. `item_usage_events`
13. `item_condition_events`
14. `item_expiry_events`
15. `item_relationships`
16. `duplicate_candidates`
17. `inventory_import_jobs`
18. `inventory_value_events`
19. `inventory_events`

The migration seeds only the seven controlled category rows. Item ownership uses the V2
account id, and inventory/media/AI foreign keys preserve the existing privacy boundaries.

## API changes

- `POST /api/v2/inventory/extract`
- `POST/GET /api/v2/inventory/items`
- `GET/PATCH/DELETE /api/v2/inventory/items/{id}`
- `POST /api/v2/inventory/items/{id}/confirm`
- `POST /api/v2/inventory/items/{id}/usage`
- `POST /api/v2/inventory/items/{id}/condition`
- `GET /api/v2/inventory/search`
- `GET /api/v2/inventory/duplicates`
- `POST /api/v2/inventory/duplicates/{id}/resolve`
- `GET /api/v2/inventory/expiring`
- `GET /api/v2/inventory/low-use`
- `GET /api/v2/inventory/value-to-recover`
- `GET /api/v2/inventory/summary`

List and search routes support pagination, sorting and filters for category, brand, colour,
ingredient, occasion, season, condition, expiry state, usage level and verification state.
The privacy export now includes active and archived inventory with item history.

## UI changes

- Inventory is a primary tab destination; the old History screen remains available as a
  route but is no longer a primary tab.
- The overview shows seven category counts, three current attention signals and an estimated
  Value to Recover rather than a crowded dashboard.
- A guided sprint suggests common first items and explicitly says the full catalogue is not
  required before receiving value.
- Add Item supports manual entry, one-item camera capture and screenshot import.
- Extracted drafts have visible confidence plus accessible Correct and Confirm actions.
- Search has category, brand, colour, ingredient, occasion and usage filters.
- Item Details supports correction, expiry editing, usage logging, condition changes,
  history and removal from active inventory.
- Dedicated views cover Low-Use Products, Products Expiring Soon, Duplicate Candidates and
  Value to Recover.
- Supplement screens show a neutral inventory-only safety disclaimer.

## Main files changed

- `backend/app/domains/inventory/` — taxonomy, models, schemas, extraction and services
- `backend/app/api/v2/inventory.py` — secured inventory API
- `backend/migrations/versions/0003_complete_inventory.py` — Phase 3 schema
- `backend/tests/test_inventory.py` — inventory acceptance contracts
- `frontend/app/(tabs)/inventory.tsx` — overview, guided sprint, search and filters
- `frontend/app/inventory-add.tsx` — manual/photo/screenshot capture
- `frontend/app/inventory-item.tsx` — draft review, details and events
- `frontend/app/inventory-insights.tsx` — duplicates, expiry, low-use and value
- `frontend/src/components/inventory/InventoryPieces.tsx` — reusable accessible UI
- `frontend/src/services/apiV2.ts` — typed inventory client
- `frontend/src/__tests__/inventory.test.tsx` — UI contracts
- `README.md`, `env.example`, privacy export and feature-flag registry

## Deterministic metric definitions

### Low-Use Products

An active item is low-use when it is at least 30 days old, has at most two logged uses, and
has not been used in the last 30 days. A newly added item is not immediately labelled
low-use.

### Products Expiring Soon

The effective expiry is the earlier of the entered expiry date and opened date plus the
entered period-after-opening months. Calendar-month arithmetic is used. The standard search
window is 90 days and exposes expired, expiring-soon, current and missing-date states.

### Value to Recover

Formula version `v1` uses only entered purchase price, entered remaining percentage or a
usage-based remaining estimate, condition, time since last use and expiry proximity. Each
item returns visible inputs, missing inputs and an explanation. The result is always labelled
an estimate and is never described as exact. Items without prices have no item estimate and
are excluded from the total.

## Tests run and actual results

### Backend

```text
alembic upgrade head
alembic check
118 passed, 229 warnings in 28.91s
```

Fourteen Phase 3 API tests cover authentication, all seven categories, draft extraction,
confirmation, correction, item and media ownership, duplicates, expiry and
period-after-opening, immutable expiry events, low-use, usage/condition history, Value to
Recover, missing prices, search, pagination, filters, offline retry, sync conflicts, 250-item
pagination, controlled attributes, failed-import history, supplement language, batch gating
and privacy export. Migration tests bring the focused Phase 3/database total to 19. Existing warnings are framework deprecations and the
intentionally short test JWT key.

### Frontend

```text
TypeScript: passed
Lint: 0 errors, 3 pre-existing hook warnings
Test suites: 6 passed, 6 total
Tests: 56 passed, 56 total
Expo web export: passed, 28 static routes
```

Seven Phase 3 component tests cover all groups, accessible category controls, guided sprint,
draft correction/confirmation, draft labels, estimate language and recovery actions.

### Runtime smoke

- A fresh database applied migrations `0001`, `0002` and `0003` in order.
- PostgreSQL reported 40 public tables.
- V2 health returned healthy with PostgreSQL up.
- `v2_inventory` resolved to enabled; `v2_inventory_batch` resolved to disabled.
- Unauthenticated `GET /api/v2/inventory/summary` returned 401.
- The worker started after the migrated backend became healthy.

## Known limitations

- Multi-item shelf, wardrobe and video detection is deliberately disabled until its quality
  is proven. The current UI supports accurate individual-item capture first.
- Local development uses filesystem media; production needs the existing S3 adapter
  configured with owner-managed credentials.
- The repository-wide live `backend_test.py` is not green in this environment. Its latest
  run completed 9/18: Google accepted the configured credential and one request succeeded,
  then returned HTTP 429 quota errors, while one legacy assertion still expects the removed
  unversioned `wellness_scores` object. This is not represented as passing and no test was
  weakened. Phase 3's provider-independent extraction contract is covered by the AI gateway
  fake in the 118-test backend suite.

## Security and privacy

- Every item, media attachment, duplicate and import lookup derives ownership from the bearer
  token. Missing and not-owned ids both return 404.
- The API never accepts a user id from the request body.
- Inventory photos use the validated media service; internal storage keys are never exposed.
- AI extraction reads an owned media asset and stores only schema-validated structured facts
  plus provenance. It does not silently confirm them.
- Inventory is included in the user's privacy export, including archived items and history.
- Secrets remain environment-only. No Gemini credential is committed.

## Cost considerations

- Manual entry, search, filters, duplicates, expiry, low-use and Value to Recover use no AI.
- Only explicit photo/screenshot extraction calls Gemini and is recorded in the AI ledger.
- Batch capture is disabled, preventing expensive low-confidence multi-item calls.
- Pagination is capped at 100 items per response and the large-inventory test uses 250 rows.

## Rollback

Remove `v2_inventory` from `V2_FEATURES` to hide Phase 3 routes without deleting data. Keep
`v2_inventory_batch` off. To remove only the Phase 3 schema after taking a backup:

```bash
docker compose exec backend alembic downgrade 0002_appearance_digital_twin
```

MongoDB, V1 routes and the Phase 2 digital twin remain unchanged.

## Simple product-owner verification

1. Open the Inventory tab and confirm all seven categories are present.
2. Add one wardrobe item manually and reopen it from Recently Added.
3. Add an item from a product screenshot and confirm it appears as Review, not verified.
4. Correct one suggested value, then confirm the draft.
5. Add the same named/brand item twice and review Duplicate Candidates.
6. Add an expiry date to a beauty item and check Products Expiring Soon.
7. Add a purchase price and open Value to Recover; confirm it says Estimate and explains the
   inputs. Remove the price and confirm no amount is invented.
8. Open a supplement item and confirm the inventory-only safety language contains no dosage
   advice.

## Acceptance checklist

| Criterion | Status | Evidence |
|---|---|---|
| All seven inventory groups work | ✅ | Typed tables, APIs, overview and category test |
| Individual item can be added and corrected | ✅ | Manual create and optimistic patch workflow |
| AI extraction stays draft until confirmed | ✅ | Draft-only service/API and test |
| Search across supported categories | ✅ | Indexed item query plus searchable attribute projection |
| Duplicate candidates review and resolution | ✅ | Pending candidates, keep/merge/not-duplicate actions |
| Deterministic expiry | ✅ | Explicit expiry and calendar-month PAO tests |
| Value to Recover is transparent | ✅ | Versioned formula, visible/missing inputs and Estimate labels |
| No supplement dosage advice | ✅ | Restricted schema, prompt, disclaimer and tests |
| Ten common items can be added reasonably | ✅ | Guided sprint, optional details and retry-safe manual entry |
| Relevant Phase 3 tests pass | ✅ | 118 backend and 56 frontend tests plus production export |
| Repository-wide live provider suite | ⚠️ | External 429 quota and stale legacy assertion; not claimed as passed |
| Report exists | ✅ | This file |
| Phase committed | ⏳ | Filled below after commit |

## Commit

```text
PENDING  feat(v2): build complete appearance inventory system
```

Phase 4 was not started.
