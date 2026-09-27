# Lane F — bounded correctness cleanup

The last repository-stability lane before Step 15 is requalified. It closes
independently reproduced defects and adds no product. Nothing here restores a
retired surface, changes a user-facing string beyond one validation sentence,
or changes the privacy export's shape (it stays at schema `1.5`).

One Alembic revision, `lf1a2b3c4d` (revises `k9l0m1n2o3`), carries the only two
facts the schema could not already state.

## 1. Erased scan history belongs to nobody

**The defect.** Account erasure keeps a scan row, because a confirmed capture is
the provenance of a shared `product_label_snapshots` row, and severs it from the
person: `label_facts` is withdrawn and `account_id` becomes `NULL`. A genuinely
anonymous scan also has `account_id` `NULL`. `attach_scans_to_account` moved
every accountless row on a device to whoever claimed it, so the next person to
sign in on the same phone was given the deleted person's history.

**The authority.** `scan_events.account_attachment_allowed`:

| State | `account_id` | `account_attachment_allowed` |
| --- | --- | --- |
| New signed-out scan | `NULL` | `true` — it may follow its phone into an account |
| Scan recorded for an account | the account | `false` from the start |
| Attached by a device claim | the claimant | set `false` in the same statement |
| Left by account erasure | `NULL` | `false`, set by erasure before the account goes |

- `attach_scans_to_account` moves only rows that are accountless **and** attachable.
- A check constraint, `ck_scan_events_owned_not_attachable`, makes an owned row
  non-attachable by construction. So a row an account leaves behind — through
  the erasure path or the `ON DELETE SET NULL` cascade alone — can never be
  attached, even by a path that forgets to say so. Erasure still sets the flag
  explicitly, as a second line.
- The flag never goes back to `true`.

**Backfill: privacy wins.** Nothing stored can prove that an accountless row
from before this revision was never owned: a device's claim is not a history of
claims, and a scan made signed in on an unclaimed phone looks exactly like a
signed-out one once its account is gone. So every existing row, owned or not,
is written `false` by the column default. The cost is that anonymous scans made
before the revision and never claimed will not follow their phone into an
account signed up afterwards. The alternative is attaching a stranger's erased
history.

**Lane C is kept, and made stricter.** Nothing that
`app/domains/product/withdrawn_confirmation.py` reads is removed: the source's
`device_id` and `ai_run_id`, the snapshot's device, the retained run ledger. The
proof now also requires `account_attachment_allowed` to be `false` — a withdrawn
confirmation always is, so an accountless capture still open to a claim is not
one. No erased fact is restored and no snapshot is repointed.

**Export.** The flag is internal control state. The scans export withholds it
(`EXPORT_COVERAGE["scan_events"].withheld`), so the export shape and its schema
version are unchanged.

## 2. Allowance is reserved before it is spent

**The defect.** The beta limiter checked usage, called the provider, then
recorded usage after success. Every request racing for the last unit read "one
left" and all of them paid the provider. Reproduced on the base: six parallel
calls against a budget of one reached the provider six times, for the hourly AI
budget and for the monthly scan allowance alike.

**The authority.** `beta_usage_reservations` (operational, cascades with the
account, holds no prompt, output or customer text) and three functions in
`app.domains.beta_access.service`:

- `reserve_usage` takes a transaction-scoped advisory lock keyed by
  `account + feature + period`, checks
  `successful usage + live reservations + this one ≤ limit`, and inserts the
  reservation — one step, so exactly one contender gets the last unit. The
  caller commits **before** any provider call; no lock or transaction is held
  across it. A database failure raises here, so the budget fails closed.
- `settle_usage` — success: one `beta_usage_events` row in the period the unit
  was reserved from, and the reservation deleted, in one transaction. Keeps the
  existing idempotency key.
- `release_usage` — a known failure: the reservation is deleted and nothing is
  counted. A failed run still never consumes an allowance.

A process that dies between reserving and settling leaves a reservation that
stops counting at `expires_at` (`RESERVATION_TTL`, ten minutes — longer than
the provider timeout plus the database work either side). Every count ignores
an expired reservation, so no scheduler is needed; expired rows for the same
budget are tidied opportunistically under the same lock.

**Where it is used.** Every tracked limit that is live:

| Limit | Call site |
| --- | --- |
| Hourly AI requests (`ai.request`) | `ai_gateway.gateway.run_structured` — every model call |
| Monthly photo checks (`scan.analyse`) | `POST /api/v2/scan/analyse` |

`style.recommendations` and `shopping.evaluate` are declared in
`beta_access` but have no call site. The recommendation entitlements
(`style_occasion`, `shopping_evaluation`) are consumed only by
`recommendation.orchestrator.style_for_occasion` and `evaluate_purchase`,
which no mounted route calls: the retired Style surface. None of them is a
live check-then-record path, so none was changed.

## 3. "Today" is the customer's date

**The defect.** Account-facing helpers fell back to the server's
`date.today()`. On a UTC server that is the customer's yesterday from 00:00 to
05:30 in India, and the Care shelf (server date) and Care decisions (India
date) disagreed about the same product on the same page.

**The authority.** `app.domains.planning.context.account_today` — the account's
own timezone (`resolve_timezone_for`, India by default), or an explicit one when
given. It is resolved once at an account-facing boundary — a route, or a
service entry point such as `routines.manager.build_queue` — and passed down.
The helpers that compare against today now *require* the date: inventory
serialisation, low use, expiry, value to recover and the summary; the Care
shelf context, expiry findings and slot ranking; perfume ranking; the supplement
summary and detail; the progress metrics. None of them reads the server's date.
A usage logged without a date is logged on the customer's date. The goal check
"target before start" stays in the schema for a stated start, and moves to
`progress.service.create_goal` when the start is the customer's today.

A static guard (`test_f_d_static_guard_no_repaired_path_reads_the_server_date`)
fails if `date.today` or `datetime.today` reappears in a repaired module. Pure
calendar helpers elsewhere are not banned.

## 4. The smaller defects

- **Legacy inventory rows.** `inventory.service.details_for_many` gives details
  only to categories with current detail authority (`has_detail_authority`) and
  never raw-indexes `DETAIL_MODELS`. The Care shelf and the style/Today context
  read only the governed taxonomy. Wardrobe, shoe and accessory rows stay as
  history for the export and never enter the current product.
- **Duplicate pairs.** Archiving an item retires every pending pair naming it
  (`resolution = item_archived`); merging retires the archived side's other
  pairs. A pair that no longer resolves to two of the account's current items —
  historical, or a malformed cross-account row — is retired
  (`item_unavailable`) and never shown, and the valid pairs are still returned.
- **Supplement `raw_name: null`** is refused at request validation (422); an
  omitted name keeps the recorded one.
- **Scheduler credential.** Compared with `hmac.compare_digest` over UTF-8
  bytes, so a Unicode value is the same 401 as any other wrong value.
- **Deleted photos.** Deleting a media asset removes the links from the
  account's own inventory items in the same transaction. Nobody else's link is
  touched, and no item or byte is recreated.
- **Deferred-purchase notice.** Skips only `NotFoundError` and
  `ValidationFailedError` — a removed or untrusted candidate. Anything else is
  logged as `deferred_purchase_relevance_failed` with its type only, and raised,
  so the worker records the account's cycle as failed.
- **Invite reservations.** Account deletion sets `supabase_user_id` to `NULL`
  and `email_normalised` to the constant `erased` on the consumed reservations
  the account used, before the account row goes. Invite, status and times stay.
- **Weather.** `planning/weather.py` had no production caller; it, its tests and
  the manual `live-weather.yml` probe that exercised it are removed. The live
  path is `planning/providers/open_meteo.py`, unchanged.

## Rollback

`git revert` the merge, then `alembic downgrade k9l0m1n2o3`. The downgrade drops
the reservation table and the attachment column. It cannot keep what the column
knew: after it, the pre-Lane-F claim code once more treats erased history as
anonymous. Re-upgrading writes every row `false` again, which is privacy-safe.
